"""
Parse structured sections from FPPC advice letters.

FPPC advice letters typically follow a standard format with these sections:
- QUESTION(S): The legal question(s) being addressed
- CONCLUSION(S): The short answer(s) to the question(s)
- FACTS: Background facts as presented by the requestor
- ANALYSIS: Detailed legal analysis supporting the conclusion

This module extracts these sections using regex-based parsing with validation
and confidence scoring. The format has evolved over time:

- Modern (2000+): Clean headers on their own lines
- 1990s: Often "QUESTIONS PRESENTED", "SHORT ANSWER" variants
- 1980s: Various formats, some with "DISCUSSION" instead of "ANALYSIS"
- Pre-1980: Inconsistent formatting, often no clear section structure

Usage:
    from scraper.section_parser import parse_sections

    result = parse_sections(document_text, year=2024)
    if result.has_standard_format:
        print(f"Q: {result.question}")
        print(f"A: {result.conclusion}")
    print(f"Confidence: {result.extraction_confidence}")
"""

import re
from dataclasses import dataclass
from typing import Literal


# =============================================================================
# Constants
# =============================================================================

# Minimum words for a section to be considered valid
MIN_SECTION_WORDS = 10

# Section types we extract
SectionType = Literal["question", "conclusion", "facts", "analysis"]


# =============================================================================
# Regex Patterns
# =============================================================================

# Section header patterns - ordered by specificity (most specific first)
# Each tuple: (pattern, section_type, format_era)
SECTION_PATTERNS: list[tuple[str, SectionType, str]] = [
    # Modern format (strict) - header on own line
    (r'^[ \t]{0,4}QUESTIONS?\s*$', 'question', 'modern'),
    (r'^[ \t]{0,4}CONCLUSIONS?\s*$', 'conclusion', 'modern'),
    (r'^[ \t]{0,4}FACTS(?:\s+AS\s+PRESENTED(?:\s+BY\s+REQUESTER)?)?\s*$', 'facts', 'modern'),
    (r'^[ \t]{0,4}ANALYSIS\s*$', 'analysis', 'modern'),

    # Modern with colon
    (r'^[ \t]{0,4}QUESTIONS?\s*:', 'question', 'modern'),
    (r'^[ \t]{0,4}CONCLUSIONS?\s*:', 'conclusion', 'modern'),
    (r'^[ \t]{0,4}FACTS\s*:', 'facts', 'modern'),
    (r'^[ \t]{0,4}ANALYSIS\s*:', 'analysis', 'modern'),

    # Numbered format (only match first occurrence)
    (r'^[ \t]{0,4}QUESTIONS?\s+1\s*[:\.\n]', 'question', 'numbered'),
    (r'^[ \t]{0,4}(?:CONCLUSIONS?|ANSWERS?)\s+1\s*[:\.\n]', 'conclusion', 'numbered'),

    # Older format variants
    (r'^[ \t]{0,4}QUESTIONS?\s+PRESENTED\s*[:\n]?', 'question', 'old'),
    (r'^[ \t]{0,4}ISSUES?\s+PRESENTED\s*[:\n]?', 'question', 'old'),
    (r'^[ \t]{0,4}SHORT\s+ANSWERS?\s*[:\n]?', 'conclusion', 'old'),
    (r'^[ \t]{0,4}SUMMARY(?:\s+OF\s+CONCLUSIONS?)?\s*[:\n]?', 'conclusion', 'old'),
    (r'^[ \t]{0,4}DISCUSSION\s*[:\n]?', 'analysis', 'old'),
    (r'^[ \t]{0,4}BACKGROUND\s*[:\n]?', 'facts', 'old'),

    # Less common variants
    (r'^[ \t]{0,4}STATEMENT\s+OF\s+FACTS?\s*[:\n]?', 'facts', 'old'),
    (r'^[ \t]{0,4}FACTUAL\s+BACKGROUND\s*[:\n]?', 'facts', 'old'),
    (r'^[ \t]{0,4}LEGAL\s+ANALYSIS\s*[:\n]?', 'analysis', 'old'),
]

# Patterns that indicate end of document content (before signature)
DOCUMENT_END_PATTERNS = [
    r'\n[ \t]*Sincerely,',
    r'\n[ \t]*Very truly yours,',
    r'\n[ \t]*Respectfully,',
    r'\n[ \t]*Respectfully submitted,',
    r'\n[ \t]*General Counsel',
    r'\n[ \t]*Chief Counsel',
    r'\n[ \t]*\*\s*\*\s*\*[ \t]*\n',  # *** divider
    r'\n[ \t]*\* \* \*[ \t]*\n',
]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SectionMatch:
    """A potential section header match with position info."""

    section_type: SectionType
    header_text: str       # Matched text e.g. "QUESTION\n"
    header_start: int      # Position in text where header starts
    header_end: int        # Position where content begins (after header)
    format_era: str        # 'modern', 'numbered', or 'old'


@dataclass
class SectionResult:
    """
    Result of section parsing - maps to schema.Sections dataclass fields.

    This is the public return type from parse_sections(). All section fields
    are None if that section was not found.
    """

    question: str | None
    conclusion: str | None
    facts: str | None
    analysis: str | None
    extraction_method: Literal["regex", "regex_validated", "none"]
    extraction_confidence: float  # 0.0-1.0
    has_standard_format: bool
    parsing_notes: str | None


# =============================================================================
# Private Helper Functions
# =============================================================================


def _find_section_matches(text: str) -> list[SectionMatch]:
    """
    Find all section header matches in the text.

    Scans the text for all known section header patterns and returns
    them sorted by position. Only the first match for each section type
    is kept to handle documents with repeated headers.

    Args:
        text: The document text to search

    Returns:
        List of SectionMatch objects sorted by header_start position
    """
    matches: list[SectionMatch] = []
    seen_types: set[SectionType] = set()

    for pattern, section_type, format_era in SECTION_PATTERNS:
        # Skip if we already found this section type
        if section_type in seen_types:
            continue

        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            matches.append(SectionMatch(
                section_type=section_type,
                header_text=match.group(0),
                header_start=match.start(),
                header_end=match.end(),
                format_era=format_era,
            ))
            seen_types.add(section_type)

    # Sort by position in document
    matches.sort(key=lambda m: m.header_start)
    return matches


def _find_document_end(text: str) -> int | None:
    """
    Find the position where document content ends (before signature block).

    Args:
        text: The document text

    Returns:
        Position of document end marker, or None if not found
    """
    earliest_end = None

    for pattern in DOCUMENT_END_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if earliest_end is None or match.start() < earliest_end:
                earliest_end = match.start()

    return earliest_end


def _find_section_end(
    text: str,
    content_start: int,
    next_header_start: int | None,
    document_end: int | None
) -> int:
    """
    Find where a section's content ends.

    A section ends at the earliest of:
    1. The next section header
    2. The document end marker (signature block)
    3. The end of the text

    Args:
        text: The full document text
        content_start: Where this section's content begins
        next_header_start: Position of next section header (or None)
        document_end: Position of document end marker (or None)

    Returns:
        Position where section content ends
    """
    candidates = [len(text)]

    if next_header_start is not None:
        candidates.append(next_header_start)

    if document_end is not None and document_end > content_start:
        candidates.append(document_end)

    return min(candidates)


def _clean_section_content(content: str) -> str:
    """
    Clean up extracted section content.

    Removes:
    - Leading/trailing whitespace
    - Excessive blank lines (more than 2 consecutive)
    - Page break artifacts
    - Header remnants at the start

    Args:
        content: Raw section content

    Returns:
        Cleaned content string
    """
    if not content:
        return ""

    # Remove page break artifacts (form feed, page numbers)
    content = re.sub(r'\f', '\n', content)
    content = re.sub(r'\n[ \t]*-?\d+-[ \t]*\n', '\n', content)  # Page numbers like "-3-"
    content = re.sub(r'\n[ \t]*Page \d+ of \d+[ \t]*\n', '\n', content, flags=re.IGNORECASE)

    # Normalize line endings
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # Remove excessive blank lines (keep max 2)
    content = re.sub(r'\n{4,}', '\n\n\n', content)

    # Strip leading/trailing whitespace
    content = content.strip()

    return content


def _count_words(text: str) -> int:
    """Count words in text."""
    if not text:
        return 0
    return len(text.split())


def _validate_and_extract(
    text: str,
    matches: list[SectionMatch]
) -> tuple[dict[str, str], list[str]]:
    """
    Validate section order and extract content for each section.

    Validates:
    - QUESTION should appear before CONCLUSION
    - Each section has substantial content (≥10 words)

    Args:
        text: The full document text
        matches: List of SectionMatch objects (sorted by position)

    Returns:
        Tuple of (extracted_sections dict, validation_issues list)
    """
    extracted: dict[str, str] = {}
    issues: list[str] = []

    document_end = _find_document_end(text)

    # Check ordering: question should come before conclusion
    question_pos = None
    conclusion_pos = None
    for match in matches:
        if match.section_type == 'question':
            question_pos = match.header_start
        elif match.section_type == 'conclusion':
            conclusion_pos = match.header_start

    if question_pos is not None and conclusion_pos is not None:
        if conclusion_pos < question_pos:
            issues.append("CONCLUSION appears before QUESTION")

    # Extract content for each section
    for i, match in enumerate(matches):
        # Find where this section ends
        next_start = matches[i + 1].header_start if i + 1 < len(matches) else None
        section_end = _find_section_end(text, match.header_end, next_start, document_end)

        # Extract and clean content
        raw_content = text[match.header_end:section_end]
        content = _clean_section_content(raw_content)

        # Validate minimum content
        word_count = _count_words(content)
        if word_count < MIN_SECTION_WORDS:
            issues.append(f"{match.section_type.upper()} has only {word_count} words")
            continue

        extracted[match.section_type] = content

    return extracted, issues


def _compute_confidence(
    extracted: dict[str, str],
    issues: list[str],
    year: int | None
) -> float:
    """
    Calculate confidence score for the extraction.

    The score is based on:
    - What sections were found (Q+C is best)
    - Document era (modern docs more reliable)
    - Validation issues encountered

    Args:
        extracted: Dict of section_type -> content
        issues: List of validation issues
        year: Document year (for era adjustment)

    Returns:
        Confidence score from 0.0 to 1.0
    """
    # Base score from what was found
    has_question = 'question' in extracted
    has_conclusion = 'conclusion' in extracted
    has_analysis = 'analysis' in extracted
    has_facts = 'facts' in extracted

    if has_question and has_conclusion:
        base = 0.9
    elif has_question or has_conclusion:
        base = 0.6
    elif has_analysis or has_facts:
        base = 0.4
    else:
        base = 0.0

    # Bonus for having all four sections
    if has_question and has_conclusion and has_analysis and has_facts:
        base = min(base + 0.05, 1.0)

    # Era adjustment
    if year is not None:
        if year >= 2010:
            base = min(base + 0.05, 1.0)  # Very modern, reliable format
        elif year >= 2000:
            pass  # No adjustment for 2000s
        elif year >= 1985:
            base = max(base - 0.05, 0.0)  # Slightly less reliable
        else:
            base = max(base - 0.15, 0.0)  # Pre-1985, much less reliable

    # Penalty for validation issues
    base -= 0.1 * len(issues)

    return max(0.0, min(1.0, base))


def _build_parsing_notes(
    extracted: dict[str, str],
    matches: list[SectionMatch],
    issues: list[str]
) -> str | None:
    """
    Generate diagnostic notes about the parsing process.

    Args:
        extracted: Dict of section_type -> content
        matches: List of SectionMatch objects found
        issues: List of validation issues

    Returns:
        Parsing notes string, or None if nothing notable
    """
    notes: list[str] = []

    # Report what was found
    if matches:
        formats = set(m.format_era for m in matches)
        if len(formats) == 1:
            notes.append(f"Format: {list(formats)[0]}")
        else:
            notes.append(f"Mixed formats: {', '.join(sorted(formats))}")

    # Report sections found vs extracted
    found_types = {m.section_type for m in matches}
    extracted_types = set(extracted.keys())
    skipped = found_types - extracted_types
    if skipped:
        notes.append(f"Skipped (too short): {', '.join(sorted(skipped))}")

    # Report validation issues
    if issues:
        notes.extend(issues)

    if not notes:
        return None

    return "; ".join(notes)


# =============================================================================
# Public API
# =============================================================================


def parse_sections(text: str, year: int | None = None) -> SectionResult:
    """
    Parse QUESTION, CONCLUSION, FACTS, and ANALYSIS sections from document text.

    This is the main entry point for section extraction. It searches for known
    section header patterns, validates the structure, and extracts content.

    Args:
        text: The full document text (plain text, not PDF)
        year: Document year (optional, used for confidence scoring)

    Returns:
        SectionResult with extracted sections and metadata

    Example:
        >>> result = parse_sections('''
        ...     QUESTION
        ...
        ...     May a city council member vote on a contract
        ...     with a company in which they own stock?
        ...
        ...     CONCLUSION
        ...
        ...     No. Under Government Code Section 87100...
        ... ''', year=2024)
        >>> result.has_standard_format
        True
        >>> result.question
        'May a city council member vote on a contract\\nwith a company in which they own stock?'
    """
    # Handle empty input
    if not text or not text.strip():
        return SectionResult(
            question=None,
            conclusion=None,
            facts=None,
            analysis=None,
            extraction_method="none",
            extraction_confidence=0.0,
            has_standard_format=False,
            parsing_notes="Empty or whitespace-only input",
        )

    # Find all section headers
    matches = _find_section_matches(text)

    # No sections found
    if not matches:
        return SectionResult(
            question=None,
            conclusion=None,
            facts=None,
            analysis=None,
            extraction_method="none",
            extraction_confidence=0.0,
            has_standard_format=False,
            parsing_notes="No section headers found",
        )

    # Validate structure and extract content
    extracted, issues = _validate_and_extract(text, matches)

    # No valid sections extracted (all too short or invalid)
    if not extracted:
        return SectionResult(
            question=None,
            conclusion=None,
            facts=None,
            analysis=None,
            extraction_method="none",
            extraction_confidence=0.0,
            has_standard_format=False,
            parsing_notes=_build_parsing_notes(extracted, matches, issues),
        )

    # Compute confidence and determine extraction method
    confidence = _compute_confidence(extracted, issues, year)
    has_standard = 'question' in extracted or 'conclusion' in extracted

    # Determine extraction method based on validation
    if issues:
        method: Literal["regex", "regex_validated", "none"] = "regex"
    else:
        method = "regex_validated"

    return SectionResult(
        question=extracted.get('question'),
        conclusion=extracted.get('conclusion'),
        facts=extracted.get('facts'),
        analysis=extracted.get('analysis'),
        extraction_method=method,
        extraction_confidence=confidence,
        has_standard_format=has_standard,
        parsing_notes=_build_parsing_notes(extracted, matches, issues),
    )
