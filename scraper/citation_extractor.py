"""
Extract legal citations from FPPC advice letters.

This module extracts four types of legal citations that are critical for
downstream classification and citation graph building:

1. Government Code sections (Political Reform Act: §§ 81000-91014)
2. FPPC Regulations (Title 2, CCR §§ 18000-18999)
3. Prior FPPC advice letters and opinions
4. External citations (court cases)

Usage:
    from scraper.citation_extractor import extract_citations

    result = extract_citations(document_text)
    print(result.government_code)  # ['87100', '87103(a)']
    print(result.regulations)      # ['18700', '18702.1']
    print(result.prior_opinions)   # ['A-24-006', 'I-23-177']
    print(result.external)         # ['123 Cal.App.4th 456']
"""

import re
from dataclasses import dataclass


# =============================================================================
# Result Dataclass
# =============================================================================


@dataclass
class CitationResult:
    """
    Extracted citations with metadata.

    All lists are deduplicated and sorted alphabetically.
    Empty lists are returned for citation types not found (never None).
    """

    government_code: list[str]  # ["87100", "87103(a)", "87200"]
    regulations: list[str]  # ["18700", "18702.1", "18730"]
    prior_opinions: list[str]  # ["A-23-001", "I-22-015", "M-00-033"]
    external: list[str]  # Court cases and other external citations
    extraction_notes: str | None = None  # Any issues encountered


# =============================================================================
# Regex Patterns
# =============================================================================

# Government Code patterns (Political Reform Act: §§ 81000-92000)
# These capture the section number and optional subsection
GOVERNMENT_CODE_PATTERNS = [
    # "Government Code section 87100", "Government Code Section 1090"
    r'Government\s+Code\s+[Ss]ections?\s+(\d{4,5}(?:\s*\([a-z]\))?(?:\s*\(\d+\))?)',
    # "Gov. Code § 87100", "Gov. Code, § 1090", "Gov. Code §§ 87100"
    r'Gov(?:\.|ernment)\s+Code,?\s*§+\s*(\d{4,5}(?:\s*\([a-z]\))?(?:\s*\(\d+\))?)',
    # "Section 87100" or "Section 1090" (standalone)
    r'[Ss]ections?\s+(\d{4,5}(?:\s*\([a-z]\))?(?:\s*\(\d+\))?)',
    # "§ 87100" or "§ 1090" (context-dependent)
    r'§+\s*(\d{4,5}(?:\s*\([a-z]\))?(?:\s*\(\d+\))?)',
]

# FPPC Regulation patterns (Title 2, CCR §§ 18000-18999)
REGULATION_PATTERNS = [
    # "Regulation 18700", "Regulations 18700 and 18702.1"
    r'[Rr]egulations?\s+(\d{5}(?:\.\d+)?)',
    # "2 Cal. Code Regs. § 18700", "2 Cal. Code of Regs. § 18700"
    r'2\s+Cal\.?\s+Code\s+(?:of\s+)?Regs?\.?\s*§?\s*(\d{5}(?:\.\d+)?)',
    # "FPPC Regulation 18700"
    r'FPPC\s+[Rr]egulations?\s+(\d{5}(?:\.\d+)?)',
    # "Cal. Code Regs., tit. 2, § 18700"
    r'tit\.?\s*2,?\s*§?\s*(\d{5}(?:\.\d+)?)',
    # "Title 2, section 18700"
    r'Title\s+2,?\s+[Ss]ections?\s+(\d{5}(?:\.\d+)?)',
]

# Prior FPPC opinion patterns
PRIOR_OPINION_PATTERNS = [
    # Modern format: "A-24-006", "I-23-177", "M-00-033" (3 or 4 digit number)
    r'\b([AIM]-\d{2}-\d{3,4})\b',
    # With "No." prefix: "No. A-24-006", "No.A-24-006"
    r'No\.?\s*([AIM]-\d{2}-\d{3,4})',
    # OCR misread prefixes: "4-22-078" (A→4), "1-23-177" (I→1)
    r'\b([41]-\d{2}-\d{3,4})\b',
    # "Advice Letter No. 24006", "Advice Letter 24006"
    r'[Aa]dvice\s+[Ll]etter\s+(?:No\.?\s*)?(\d{5,6})',
    # "Advice Letter, No. A-22-078" or "Memorandum, No. M-00-033"
    r'(?:Advice\s+Letter|Memorandum),?\s*No\.?\s*([AIM41]?-?\d{2}-?\d{3,4})',
    # "In re Smith, A-22-001"
    r'In\s+re\s+\w+,?\s+([AIM]-\d{2}-\d{3,4})',
    # "Opinion No. 82-032", "Opinion 82-032"
    r'[Oo]pinion\s+(?:No\.?\s*)?(\d{2}-\d{3,4})',
    # "Our File No. A-24-006", "File No. A-24-006"
    r'(?:Our\s+)?File\s+No\.?\s*([AIM]-\d{2}-\d{3,4})',
]

# External citation patterns (court cases, etc.)
EXTERNAL_PATTERNS = [
    # California cases: "123 Cal.App.4th 456", "123 Cal. App. 4th 456"
    r'\d+\s+Cal\.?\s*(?:App\.?\s*)?(?:2d|3d|4th|5th)?\s+\d+',
    # California Supreme Court: "123 Cal.2d 456", "123 Cal. 3d 456"
    r'\d+\s+Cal\.?\s*(?:2d|3d|4th|5th)\s+\d+',
    # Federal cases: "123 U.S. 456"
    r'\d+\s+U\.S\.\s+\d+',
    # Federal circuit: "123 F.2d 456", "123 F.3d 456"
    r'\d+\s+F\.(?:2d|3d)\s+\d+',
    # Federal supplement: "123 F. Supp. 456", "123 F.Supp.2d 456"
    r'\d+\s+F\.\s*Supp\.?(?:\s*2d)?\s+\d+',
    # FPPC formal opinions: "In re Doe (1975) 1 FPPC Ops. 71"
    r'In\s+re\s+\w+\s*\(\d{4}\)\s*\d+\s+FPPC\s+Ops\.?\s+\d+',
    # California Reporter: "123 Cal.Rptr. 456", "123 Cal.Rptr.2d 456"
    r'\d+\s+Cal\.?\s*Rptr\.?(?:\s*2d|3d)?\s+\d+',
]


# =============================================================================
# Validation Functions
# =============================================================================


def _is_valid_gov_code(section: str) -> bool:
    """
    Validate Government Code section is in Political Reform Act range.

    The Political Reform Act spans Government Code sections 81000-91014.
    We use a slightly wider range (81000-92000) to catch edge cases.

    Args:
        section: The section number (may include subsection like "87103(a)")

    Returns:
        True if the base section number is in valid range
    """
    match = re.match(r'(\d+)', section)
    if not match:
        return False
    base_num = int(match.group(1))
    # Political Reform Act (81000-92000) + Section 1090 conflicts statutes
    return 81000 <= base_num <= 92000 or 1090 <= base_num <= 1097


def _is_valid_regulation(section: str) -> bool:
    """
    Validate regulation section is in FPPC range.

    FPPC regulations are in Title 2, California Code of Regulations,
    sections 18000-18999 (approximately).

    Args:
        section: The section number (may include decimals like "18702.1")

    Returns:
        True if the base section number is in valid range
    """
    match = re.match(r'(\d+)', section)
    if not match:
        return False
    base_num = int(match.group(1))
    return 18000 <= base_num <= 19000


# =============================================================================
# Normalization Functions
# =============================================================================


def _normalize_citation(citation: str) -> str:
    """
    Clean up formatting variations in citations.

    Standardizes whitespace and formatting for consistent output.

    Args:
        citation: Raw citation string

    Returns:
        Normalized citation string
    """
    # Remove extra whitespace
    citation = re.sub(r'\s+', ' ', citation.strip())

    # Normalize spacing around parentheses in subsections
    # "87103 (a)" -> "87103(a)"
    citation = re.sub(r'\s+\(', '(', citation)
    citation = re.sub(r'\)\s+', ')', citation)

    return citation


def _normalize_prior_opinion(opinion_id: str) -> str:
    """
    Normalize prior opinion identifiers to standard format.

    Converts various formats to the standard "X-YY-NNN" format where possible.

    Args:
        opinion_id: Raw opinion identifier

    Returns:
        Normalized opinion ID
    """
    opinion_id = opinion_id.strip().upper()

    # OCR misread prefix mapping: 4→A, 1→I
    ocr_prefix_map = {"4": "A", "1": "I"}

    # Already in standard format (A-24-006 or A-24-0067)
    if re.match(r'^[AIM]-\d{2}-\d{3,4}$', opinion_id):
        return opinion_id

    # OCR misread prefix: "4-22-078" → "A-22-078", "1-23-177" → "I-23-177"
    m = re.match(r'^([41])-(\d{2})-(\d{3,4})$', opinion_id)
    if m:
        prefix = ocr_prefix_map.get(m.group(1), "A")
        return f"{prefix}-{m.group(2)}-{m.group(3)}"

    # Missing dashes with prefix: "A22078" → "A-22-078"
    m = re.match(r'^([AIM41])(\d{2})(\d{3,4})$', opinion_id)
    if m:
        prefix = ocr_prefix_map.get(m.group(1), m.group(1))
        return f"{prefix}-{m.group(2)}-{m.group(3)}"

    # Old numeric format (24006 or 240067) - try to convert
    if re.match(r'^\d{5,6}$', opinion_id):
        # Format: YYNNN -> A-YY-NNN (assume A for advice letter)
        return f"A-{opinion_id[:2]}-{opinion_id[2:]}"

    # Two-digit year with three/four-digit number (82-032)
    if re.match(r'^\d{2}-\d{3,4}$', opinion_id):
        return f"A-{opinion_id}"

    return opinion_id


# =============================================================================
# Extraction Functions
# =============================================================================


# Patterns indicating a Section 1090 disclaimer (false positive context)
_1090_DISCLAIMER_PATTERNS = [
    r'not\s+(?:under|providing\s+advice\s+under)\s+(?:other\s+)?(?:.*?\s+)?Section\s+1090',
    r'not\s+under\s+(?:.*?\s+)?Section\s+1090',
    r'we\s+are\s+not\s+(?:providing\s+)?(?:advice\s+)?(?:under\s+)?(?:.*?\s+)?1090',
]


def _is_1090_disclaimer(text: str) -> bool:
    """Check if a text contains Section 1090 only in disclaimer context."""
    for pattern in _1090_DISCLAIMER_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_government_code(text: str) -> list[str]:
    """
    Extract Government Code section citations.

    Args:
        text: Document text to search

    Returns:
        List of validated, deduplicated, sorted section citations
    """
    citations = set()
    has_1090_substantive = False

    for pattern in GOVERNMENT_CODE_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            section = _normalize_citation(match.group(1))
            if _is_valid_gov_code(section):
                # For Section 1090 citations, check context to avoid false positives
                base_match = re.match(r'(\d+)', section)
                if base_match and 1090 <= int(base_match.group(1)) <= 1097:
                    # Check if this specific mention is substantive (not disclaimer)
                    start = max(0, match.start() - 100)
                    context = text[start:match.end()]
                    if not _is_1090_disclaimer(context):
                        has_1090_substantive = True
                    citations.add(section)
                else:
                    citations.add(section)

    # Remove 1090-range citations if ALL mentions were in disclaimer context
    if not has_1090_substantive:
        citations = {c for c in citations
                     if not (re.match(r'(\d+)', c) and 1090 <= int(re.match(r'(\d+)', c).group(1)) <= 1097)}

    return sorted(citations)


def _extract_regulations(text: str) -> list[str]:
    """
    Extract FPPC regulation citations.

    Args:
        text: Document text to search

    Returns:
        List of validated, deduplicated, sorted regulation citations
    """
    citations = set()

    for pattern in REGULATION_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            section = _normalize_citation(match.group(1))
            if _is_valid_regulation(section):
                citations.add(section)

    return sorted(citations)


def _extract_prior_opinions(text: str) -> list[str]:
    """
    Extract prior FPPC opinion citations.

    Args:
        text: Document text to search

    Returns:
        List of deduplicated, sorted, normalized opinion identifiers
    """
    citations = set()

    for pattern in PRIOR_OPINION_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            opinion_id = _normalize_prior_opinion(match.group(1))
            citations.add(opinion_id)

    return sorted(citations)


def _extract_external_citations(text: str) -> list[str]:
    """
    Extract external legal citations (court cases, etc.).

    Args:
        text: Document text to search

    Returns:
        List of deduplicated, sorted external citations
    """
    citations = set()

    for pattern in EXTERNAL_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            citation = _normalize_citation(match.group(0))
            citations.add(citation)

    return sorted(citations)


# =============================================================================
# Main Extraction Function
# =============================================================================


def extract_citations(text: str) -> CitationResult:
    """
    Extract all legal citations from document text.

    Searches for four types of citations:
    1. Government Code sections (Political Reform Act)
    2. FPPC Regulations (Title 2 CCR)
    3. Prior FPPC advice letters and opinions
    4. External citations (court cases)

    Args:
        text: The full document text to search

    Returns:
        CitationResult containing deduplicated, sorted lists of citations.
        Empty lists are returned for citation types not found.

    Example:
        >>> result = extract_citations('''
        ...     Government Code Section 87100 prohibits officials from making
        ...     decisions affecting their financial interests. See also
        ...     Gov. Code § 87103(a). Regulation 18700 provides guidance.
        ...     See A-24-006 and I-23-177 for prior analysis.
        ... ''')
        >>> result.government_code
        ['87100', '87103(a)']
        >>> result.regulations
        ['18700']
        >>> result.prior_opinions
        ['A-24-006', 'I-23-177']
    """
    if not text or not text.strip():
        return CitationResult(
            government_code=[],
            regulations=[],
            prior_opinions=[],
            external=[],
            extraction_notes="Empty or whitespace-only input",
        )

    # Extract all citation types
    gov_code = _extract_government_code(text)
    regulations = _extract_regulations(text)
    prior_opinions = _extract_prior_opinions(text)
    external = _extract_external_citations(text)

    # Build extraction notes if any issues
    notes = None
    total_count = len(gov_code) + len(regulations) + len(prior_opinions) + len(external)
    if total_count == 0:
        notes = "No citations found"

    return CitationResult(
        government_code=gov_code,
        regulations=regulations,
        prior_opinions=prior_opinions,
        external=external,
        extraction_notes=notes,
    )
