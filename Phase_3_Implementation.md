# Phase 3 Implementation Plan: Text Extraction & Structuring

## Overview

This document provides the complete implementation plan for Phase 3 of the FPPC scraping project. The goal is to extract text from all 14,096 downloaded PDFs and structure them into searchable JSON files that support downstream search, RAG, and analysis use cases.

### Current State
- **Phase 1 (Complete)**: 14,132 documents indexed in SQLite
- **Phase 2 (Complete)**: 14,096 unique PDFs downloaded to `raw_pdfs/{year}/`
- **Database**: `data/documents.db` with full metadata

### Phase 3 Goals
1. Extract text from all PDFs (native extraction + olmOCR fallback)
2. Classify document types (filter non-opinion documents)
3. Parse document sections with validation (QUESTION, CONCLUSION, FACTS, ANALYSIS)
4. Use LLM to generate synthetic Q&A for documents without standard structure
5. Extract legal citations (Government Code, Regulations, prior opinions)
6. Output structured JSON files ready for search/embedding

### Key Design Decisions
- **Two-phase approach**: Conservative extraction first, LLM enhancement second
- **Validation over regex**: Section extraction includes confidence scoring
- **Synthetic Q&A**: All documents get searchable Q&A content (extracted or LLM-generated)
- **Era-aware processing**: Different strategies for pre-1988 vs modern documents

---

## Implementation Tasks

The implementation is divided into discrete, sequential tasks:

| Task | Module | Description | Dependencies | Status |
|------|--------|-------------|--------------|--------|
| **3.1** | `schema.py` | Define all dataclasses | None | ✓ Complete |
| **3.2** | `quality.py` | Text quality scoring | None | ✓ Complete |
| **3.3** | `section_parser.py` | Regex section extraction with validation | None | ✓ Complete |
| **3.4** | `citation_extractor.py` | Legal citation extraction | None | ✓ Complete |
| **3.5** | `classifier.py` | Heuristic topic classification | 3.4 | ✓ Complete |
| **3.6** | `db.py` additions | Add extraction tracking columns | None | |
| **3.7** | `extractor.py` | Core extraction pipeline (Phase 3A) | 3.1-3.6 | |
| **3.8** | Review & calibrate | Manual review of 50-doc sample | 3.7 | |
| **3.9** | `llm_extractor.py` | LLM-based section extraction | 3.8 | |
| **3.10** | Full extraction run | Process all 14,096 documents | 3.9 | |
| **3.11** | Post-processing | Build citation graph, compute `cited_by` | 3.10 | |

---

## JSON Schema

### Complete Schema Definition

```python
# scraper/schema.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

@dataclass
class SourceMetadata:
    """Metadata from the original crawl."""
    title_raw: str                    # Raw title from search results
    tags: list[str]                   # Tags from "Filed under"
    scraped_at: str                   # When we found this document
    source_page_url: str | None       # Which search page we found it on

@dataclass
class ExtractionInfo:
    """Information about the extraction process."""
    method: Literal["native", "olmocr", "native+olmocr"]
    extracted_at: str                 # ISO timestamp
    page_count: int
    word_count: int
    char_count: int
    quality_score: float              # 0-1, based on heuristics
    olmocr_cost: float | None         # Cost in USD if olmOCR was used
    native_word_count: int | None     # Word count from native (for comparison)

@dataclass
class Content:
    """The actual extracted text content."""
    full_text: str                    # Plain text (always present)
    full_text_markdown: str | None    # Markdown from olmOCR (when used)

@dataclass
class ParsedMetadata:
    """Metadata parsed from the document content."""
    date: str | None                  # ISO date: "2024-01-23"
    date_raw: str | None              # As written: "January 23, 2024"
    requestor_name: str | None
    requestor_title: str | None       # e.g., "City Attorney"
    requestor_city: str | None
    document_type: Literal["advice_letter", "opinion", "informal_advice",
                           "correspondence", "other", "unknown"]

@dataclass
class Sections:
    """Structured sections from the document."""
    # Extracted content (null if not found via regex)
    question: str | None              # The QUESTION section
    conclusion: str | None            # The CONCLUSION/SHORT ANSWER
    facts: str | None                 # The FACTS section
    analysis: str | None              # The ANALYSIS section

    # Synthetic content (LLM-generated if extraction failed)
    question_synthetic: str | None    # Generated question for docs without Q section
    conclusion_synthetic: str | None  # Generated conclusion for docs without C section

    # Extraction metadata
    extraction_method: Literal["regex", "regex_validated", "llm", "none"]
    extraction_confidence: float      # 0.0-1.0
    has_standard_format: bool         # True if Q/C sections were found via regex
    parsing_notes: str | None         # Any issues encountered

@dataclass
class Citations:
    """Legal citations found in the document."""
    government_code: list[str]        # ["87100", "87103(a)", "87200"]
    regulations: list[str]            # ["18700", "18702.1", "18730"]
    prior_opinions: list[str]         # ["A-23-001", "I-22-015"]
    cited_by: list[str]               # Opinions that cite THIS document (populated in post-processing)
    external: list[str]               # Court cases and other citations

@dataclass
class Classification:
    """Topic classification."""
    topic_primary: Literal["conflicts_of_interest", "campaign_finance",
                           "lobbying", "other"] | None
    topic_secondary: str | None
    topic_tags: list[str]             # Granular tags
    confidence: float | None          # Classification confidence
    classified_at: str | None
    classification_method: str | None # "heuristic:citation_based", "llm:claude-haiku", etc.

@dataclass
class EmbeddingContent:
    """Pre-computed content optimized for embedding generation."""
    qa_text: str                      # question + conclusion (extracted or synthetic)
    qa_source: Literal["extracted", "synthetic", "mixed"]
    first_500_words: str              # Fallback for docs with no structure
    summary: str | None               # LLM-generated summary (optional)

@dataclass
class FPPCDocument:
    """Complete structured document."""
    # Identity
    id: str                           # Letter ID: "A-24-006"
    year: int
    pdf_url: str
    pdf_sha256: str
    local_pdf_path: str               # Relative path: "raw_pdfs/2024/24006.pdf"

    # Nested structures
    source_metadata: SourceMetadata
    extraction: ExtractionInfo
    content: Content
    parsed: ParsedMetadata
    sections: Sections
    citations: Citations
    classification: Classification
    embedding: EmbeddingContent
```

### Example JSON Output

```json
{
  "id": "A-24-006",
  "year": 2024,
  "pdf_url": "https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2024/24006.pdf",
  "pdf_sha256": "a1b2c3d4...",
  "local_pdf_path": "raw_pdfs/2024/24006.pdf",

  "source_metadata": {
    "title_raw": "Alan J. Peake - A-24-006 - January 23, 2024 - Bakersfield",
    "tags": ["Advice Letter", "2024"],
    "scraped_at": "2025-01-15T10:30:00Z",
    "source_page_url": "https://fppc.ca.gov/advice/advice-opinion-search.html?page=1&..."
  },

  "extraction": {
    "method": "native",
    "extracted_at": "2025-01-20T14:22:00Z",
    "page_count": 4,
    "word_count": 1713,
    "char_count": 10842,
    "quality_score": 0.95,
    "olmocr_cost": null,
    "native_word_count": 1713
  },

  "content": {
    "full_text": "STATE OF CALIFORNIA\nFAIR POLITICAL PRACTICES COMMISSION\n...",
    "full_text_markdown": null
  },

  "parsed": {
    "date": "2024-01-23",
    "date_raw": "January 23, 2024",
    "requestor_name": "Alan J. Peake",
    "requestor_title": null,
    "requestor_city": "Bakersfield",
    "document_type": "advice_letter"
  },

  "sections": {
    "question": "Whether a City Council Member who is employed by a company...",
    "conclusion": "Yes, the Council Member must recuse himself from decisions...",
    "facts": "You state that you are a member of the Bakersfield City Council...",
    "analysis": "Government Code Section 87100 prohibits a public official from...",
    "question_synthetic": null,
    "conclusion_synthetic": null,
    "extraction_method": "regex_validated",
    "extraction_confidence": 0.95,
    "has_standard_format": true,
    "parsing_notes": null
  },

  "citations": {
    "government_code": ["87100", "87103", "87103(a)", "82030"],
    "regulations": ["18700", "18701", "18702.1", "18702.2"],
    "prior_opinions": ["A-23-142", "A-22-088"],
    "cited_by": ["A-24-089", "A-25-012"],
    "external": []
  },

  "classification": {
    "topic_primary": "conflicts_of_interest",
    "topic_secondary": null,
    "topic_tags": ["voting recusal", "financial interest", "employer interest"],
    "confidence": 0.95,
    "classified_at": "2025-01-21T09:00:00Z",
    "classification_method": "heuristic:citation_based"
  },

  "embedding": {
    "qa_text": "QUESTION: Whether a City Council Member who is employed by a company that provides water well services must recuse himself from decisions affecting water infrastructure.\n\nCONCLUSION: Yes, the Council Member must recuse himself from decisions that would have a reasonably foreseeable material financial effect on his employer.",
    "qa_source": "extracted",
    "first_500_words": "STATE OF CALIFORNIA FAIR POLITICAL PRACTICES COMMISSION...",
    "summary": null
  }
}
```

---

## File Organization

```
fppc_scrape/
├── data/
│   ├── documents.db                 # SQLite registry (existing)
│   └── extracted/
│       ├── 2024/
│       │   ├── A-24-006.json
│       │   ├── A-24-007.json
│       │   └── ...
│       ├── 2023/
│       ├── ...
│       └── 1975/
├── raw_pdfs/                        # Downloaded PDFs (existing)
│   ├── 2024/
│   │   ├── 24006.pdf
│   │   └── ...
│   └── ...
└── scraper/
    ├── __init__.py
    ├── config.py                    # Existing config
    ├── db.py                        # Existing + new extraction columns
    ├── crawler.py                   # Existing crawler
    ├── downloader.py                # Existing downloader
    ├── schema.py                    # Task 3.1: Pydantic/dataclass models
    ├── quality.py                   # Task 3.2: Quality scoring
    ├── section_parser.py            # Task 3.3: Section extraction
    ├── citation_extractor.py        # Task 3.4: Citation extraction
    ├── classifier.py                # Task 3.5: Topic classification
    ├── extractor.py                 # Task 3.7: Core extraction pipeline
    └── llm_extractor.py             # Task 3.9: LLM-based extraction
```

---

## Task 3.1: Schema Module

**File**: `scraper/schema.py`

Defines all dataclasses as shown above. Uses Python dataclasses (not Pydantic) for simplicity, with a `to_dict()` helper for JSON serialization.

```python
# scraper/schema.py

"""
Data models for FPPC document extraction.

Usage:
    from scraper.schema import FPPCDocument, Sections, Citations
"""

from dataclasses import dataclass, field, asdict
from typing import Literal
import json

# [All dataclass definitions from above]

def to_json(doc: FPPCDocument, indent: int = 2) -> str:
    """Serialize an FPPCDocument to JSON."""
    return json.dumps(asdict(doc), indent=indent, ensure_ascii=False)

def from_json(json_str: str) -> FPPCDocument:
    """Deserialize JSON to an FPPCDocument."""
    data = json.loads(json_str)
    # Reconstruct nested dataclasses
    # [Implementation details]
```

---

## Task 3.2: Quality Scoring

**File**: `scraper/quality.py`

```python
# scraper/quality.py

"""
Compute quality scores for extracted text.

Quality score is 0.0-1.0, based on:
- Words per page (expect 200-500 for legal docs)
- Alphabetic character ratio
- Presence of expected patterns (dates, "FPPC", section headers)
- Absence of OCR artifacts (garbled text)
"""

import re
from dataclasses import dataclass

@dataclass
class QualityMetrics:
    """Detailed quality metrics for debugging."""
    words_per_page: float
    alpha_ratio: float
    has_date: bool
    has_fppc_mention: bool
    has_section_headers: bool
    garbage_ratio: float
    final_score: float


def compute_quality_score(text: str, page_count: int) -> QualityMetrics:
    """
    Compute quality metrics for extracted text.

    Returns QualityMetrics with individual scores and final weighted score.
    """
    if not text or page_count == 0:
        return QualityMetrics(0, 0, False, False, False, 1.0, 0.0)

    word_count = len(text.split())
    words_per_page = word_count / page_count

    # Alpha ratio
    alpha_chars = sum(1 for c in text if c.isalpha())
    alpha_ratio = alpha_chars / len(text) if text else 0

    # Expected patterns
    has_date = bool(re.search(
        r'(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        text
    ))
    has_fppc = bool(re.search(r'FPPC|Fair Political|Political Practices', text, re.I))
    has_section = bool(re.search(r'\b(QUESTION|CONCLUSION|ANALYSIS|FACTS)\b', text, re.I))

    # OCR artifact detection
    long_words = sum(1 for w in text.split() if len(w) > 25)
    garbage_ratio = long_words / word_count if word_count else 0

    # Compute component scores
    scores = []

    # Words per page score
    if words_per_page >= 200:
        scores.append(1.0)
    elif words_per_page >= 100:
        scores.append(0.7)
    elif words_per_page >= 50:
        scores.append(0.4)
    else:
        scores.append(0.1)

    # Alpha ratio score
    if alpha_ratio >= 0.7:
        scores.append(1.0)
    elif alpha_ratio >= 0.5:
        scores.append(0.6)
    else:
        scores.append(0.2)

    # Pattern score
    pattern_score = (int(has_date) + int(has_fppc) + int(has_section)) / 3
    scores.append(pattern_score)

    # Artifact penalty
    if garbage_ratio < 0.01:
        scores.append(1.0)
    elif garbage_ratio < 0.05:
        scores.append(0.7)
    else:
        scores.append(0.3)

    # Weighted average
    weights = [0.3, 0.25, 0.25, 0.2]
    final_score = sum(s * w for s, w in zip(scores, weights))

    return QualityMetrics(
        words_per_page=round(words_per_page, 1),
        alpha_ratio=round(alpha_ratio, 3),
        has_date=has_date,
        has_fppc_mention=has_fppc,
        has_section_headers=has_section,
        garbage_ratio=round(garbage_ratio, 4),
        final_score=round(final_score, 3)
    )


def should_use_olmocr(year: int, quality: QualityMetrics) -> bool:
    """Decide whether to use olmOCR for a document."""

    # Always use olmOCR for very old documents (likely scanned)
    if year < 1990:
        return True

    # Use olmOCR if quality is poor
    if quality.final_score < 0.5:
        return True

    # Use olmOCR if text is too sparse
    if quality.words_per_page < 80:
        return True

    # Use olmOCR if alpha ratio is low (OCR garbage)
    if quality.alpha_ratio < 0.6:
        return True

    return False
```

---

## Task 3.3: Section Parser

**File**: `scraper/section_parser.py`

This module uses **validated regex extraction** with confidence scoring.

```python
# scraper/section_parser.py

"""
Parse structured sections from FPPC advice letters.

Uses regex with validation to avoid false positives from common words
like "question" and "conclusion" appearing in body text.

Validation rules:
1. QUESTION must appear before CONCLUSION in document
2. Section headers must be near start of line (not mid-paragraph)
3. Content between headers must be substantial (50+ words)
4. Sections should appear in expected order
"""

import re
from dataclasses import dataclass
from typing import Literal

@dataclass
class SectionMatch:
    """A potential section match with position info."""
    section_type: str
    header_text: str
    header_start: int
    content_start: int
    content: str | None = None

@dataclass
class SectionResult:
    """Result of section parsing."""
    question: str | None
    conclusion: str | None
    facts: str | None
    analysis: str | None
    extraction_method: Literal["regex", "regex_validated", "none"]
    extraction_confidence: float
    has_standard_format: bool
    parsing_notes: str | None


# Section patterns ordered by specificity (most specific first)
# Each pattern is (regex, section_type, era_hint)
SECTION_PATTERNS = [
    # Modern format (strict - requires newline or colon after)
    (r'(?:^|\n)\s{0,4}QUESTIONS?\s*(?:\n|:)', 'question', 'modern'),
    (r'(?:^|\n)\s{0,4}CONCLUSIONS?\s*(?:\n|:)', 'conclusion', 'modern'),
    (r'(?:^|\n)\s{0,4}FACTS?\s*(?:\n|:)', 'facts', 'modern'),
    (r'(?:^|\n)\s{0,4}ANALYSIS\s*(?:\n|:)', 'analysis', 'modern'),

    # Older format variants
    (r'(?:^|\n)\s{0,4}QUESTIONS?\s+PRESENTED\s*(?:\n|:)', 'question', 'old'),
    (r'(?:^|\n)\s{0,4}ISSUES?\s+PRESENTED\s*(?:\n|:)', 'question', 'old'),
    (r'(?:^|\n)\s{0,4}SHORT\s+ANSWERS?\s*(?:\n|:)', 'conclusion', 'old'),
    (r'(?:^|\n)\s{0,4}ANSWERS?\s*\d*\s*(?:\n|:)', 'conclusion', 'old'),
    (r'(?:^|\n)\s{0,4}DISCUSSION\s*(?:\n|:)', 'analysis', 'old'),
    (r'(?:^|\n)\s{0,4}SUMMARY\s*(?:\n|:)', 'conclusion', 'old'),

    # Numbered format (QUESTION 1, ANSWER 1, etc.)
    (r'(?:^|\n)\s{0,4}QUESTION\s+\d+\s*[:\n]', 'question', 'numbered'),
    (r'(?:^|\n)\s{0,4}ANSWER\s+\d+\s*[:\n]', 'conclusion', 'numbered'),
]


def parse_sections(text: str, year: int = None) -> SectionResult:
    """
    Extract structured sections from document text with validation.

    Returns SectionResult with extracted content and confidence metrics.
    """
    if not text or len(text) < 100:
        return SectionResult(
            question=None, conclusion=None, facts=None, analysis=None,
            extraction_method="none", extraction_confidence=0.0,
            has_standard_format=False, parsing_notes="Text too short"
        )

    # Find all potential section matches
    matches = _find_section_matches(text)

    if not matches:
        return SectionResult(
            question=None, conclusion=None, facts=None, analysis=None,
            extraction_method="none", extraction_confidence=0.0,
            has_standard_format=False, parsing_notes="No section headers found"
        )

    # Validate and extract content
    validated = _validate_and_extract(text, matches)

    # Compute confidence
    confidence = _compute_confidence(validated, year)

    # Determine if we have standard format
    has_standard = bool(validated.get('question') or validated.get('conclusion'))

    # Build notes
    notes = _build_parsing_notes(validated, matches)

    return SectionResult(
        question=validated.get('question'),
        conclusion=validated.get('conclusion'),
        facts=validated.get('facts'),
        analysis=validated.get('analysis'),
        extraction_method="regex_validated" if confidence >= 0.7 else "regex",
        extraction_confidence=confidence,
        has_standard_format=has_standard,
        parsing_notes=notes
    )


def _find_section_matches(text: str) -> list[SectionMatch]:
    """Find all potential section header matches."""
    matches = []

    for pattern, section_type, era in SECTION_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            matches.append(SectionMatch(
                section_type=section_type,
                header_text=match.group().strip(),
                header_start=match.start(),
                content_start=match.end(),
            ))

    # Sort by position
    matches.sort(key=lambda m: m.content_start)
    return matches


def _validate_and_extract(text: str, matches: list[SectionMatch]) -> dict:
    """Validate matches and extract content."""

    # Keep only first occurrence of each section type
    seen = set()
    unique_matches = []
    for m in matches:
        if m.section_type not in seen:
            seen.add(m.section_type)
            unique_matches.append(m)

    # Validate order: question should come before conclusion
    q_pos = next((m.content_start for m in unique_matches if m.section_type == 'question'), None)
    c_pos = next((m.content_start for m in unique_matches if m.section_type == 'conclusion'), None)

    if q_pos and c_pos and q_pos > c_pos:
        # Question appears after conclusion - suspicious, might be false positive
        # Keep conclusion but mark question as potentially invalid
        pass  # For now, we still extract but note it

    # Extract content for each section
    result = {}
    for i, match in enumerate(unique_matches):
        # Find end of section (next section start or document end markers)
        if i + 1 < len(unique_matches):
            end_pos = unique_matches[i + 1].header_start
        else:
            end_pos = _find_section_end(text, match.content_start)

        content = text[match.content_start:end_pos].strip()
        content = _clean_section_content(content)

        # Validate content length
        if content and len(content.split()) >= 10:  # At least 10 words
            result[match.section_type] = content

    return result


def _find_section_end(text: str, start: int) -> int:
    """Find end of the last section."""
    end_patterns = [
        r'\n\s*Sincerely,',
        r'\n\s*Very truly yours,',
        r'\n\s*Respectfully,',
        r'\n\s*Dave Bainbridge',  # Common FPPC signatory
        r'\n\s*[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+\s*\n.*?General Counsel',
        r'\n\s*\*\s*\*\s*\*',
    ]

    search_text = text[start:]
    min_end = len(text)

    for pattern in end_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            min_end = min(min_end, start + match.start())

    return min_end


def _clean_section_content(content: str) -> str:
    """Clean extracted section content."""
    # Remove excessive whitespace
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r'[ \t]+', ' ', content)

    # Remove page numbers
    content = re.sub(r'\n\s*-?\s*\d+\s*-?\s*\n', '\n', content)
    content = re.sub(r'\n\s*Page\s+\d+\s*\n', '\n', content, flags=re.IGNORECASE)

    # Remove header/footer artifacts
    content = re.sub(r'\nFile No\.\s+[A-Z]-\d+-\d+\s*\n', '\n', content)
    content = re.sub(r'\nPage No\.\s+\d+\s*\n', '\n', content)

    return content.strip()


def _compute_confidence(extracted: dict, year: int = None) -> float:
    """Compute extraction confidence score."""
    confidence = 0.0

    # Base confidence from what we found
    if extracted.get('question') and extracted.get('conclusion'):
        confidence = 0.9
    elif extracted.get('question') or extracted.get('conclusion'):
        confidence = 0.6
    elif extracted.get('analysis') or extracted.get('facts'):
        confidence = 0.4

    # Era adjustment
    if year:
        if year >= 2000:
            confidence = min(confidence + 0.05, 1.0)  # Modern docs more reliable
        elif year < 1985:
            confidence = max(confidence - 0.2, 0.0)  # Old docs less reliable

    return round(confidence, 2)


def _build_parsing_notes(extracted: dict, matches: list[SectionMatch]) -> str | None:
    """Build notes about parsing results."""
    notes = []

    if not extracted:
        notes.append("No valid sections extracted")
    elif not extracted.get('question') and not extracted.get('conclusion'):
        notes.append("No Q/C sections found; may need LLM extraction")
    elif extracted.get('question') and not extracted.get('conclusion'):
        notes.append("Question found but no conclusion")

    # Check for multiple matches of same type (might indicate false positives)
    type_counts = {}
    for m in matches:
        type_counts[m.section_type] = type_counts.get(m.section_type, 0) + 1

    duplicates = [t for t, c in type_counts.items() if c > 1]
    if duplicates:
        notes.append(f"Multiple matches for: {', '.join(duplicates)}")

    return "; ".join(notes) if notes else None
```

---

## Task 3.4: Citation Extractor

**File**: `scraper/citation_extractor.py`

```python
# scraper/citation_extractor.py

"""
Extract legal citations from FPPC advice letters.

Citation types:
1. Government Code sections (Political Reform Act: §§ 81000-91014)
2. FPPC Regulations (Title 2, CCR §§ 18000-18999)
3. Prior FPPC advice letters and opinions
4. External citations (court cases)
"""

import re
from dataclasses import dataclass

@dataclass
class CitationResult:
    """Extracted citations with metadata."""
    government_code: list[str]
    regulations: list[str]
    prior_opinions: list[str]
    external: list[str]
    extraction_notes: str | None = None


def extract_citations(text: str) -> CitationResult:
    """Extract all legal citations from document text."""

    gov_code = _extract_government_code(text)
    regulations = _extract_regulations(text)
    prior_opinions = _extract_prior_opinions(text)
    external = _extract_external_citations(text)

    return CitationResult(
        government_code=sorted(set(gov_code)),
        regulations=sorted(set(regulations)),
        prior_opinions=sorted(set(prior_opinions)),
        external=sorted(set(external)),
    )


def _extract_government_code(text: str) -> list[str]:
    """Extract Government Code section citations."""

    patterns = [
        # "Section 87100", "Sections 87100 and 87103"
        r'[Ss]ections?\s+(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # "Government Code section 87100"
        r'Government\s+Code\s+[Ss]ections?\s+(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # "Gov. Code § 87100", "Gov. Code, § 87100"
        r'Gov(?:\.|ernment)\s+Code,?\s*§+\s*(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # "§ 87100" after "Government Code" context
        r'§+\s*(\d{5}(?:\([a-z]\))?)',
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        citations.extend(matches)

    # Filter to valid Political Reform Act sections (81000-91014)
    valid = []
    for cite in citations:
        try:
            base_num = int(re.match(r'(\d+)', cite).group(1))
            if 81000 <= base_num <= 92000:
                valid.append(cite)
        except (ValueError, AttributeError):
            continue

    return valid


def _extract_regulations(text: str) -> list[str]:
    """Extract FPPC Regulation citations."""

    patterns = [
        # "Regulation 18700"
        r'[Rr]egulations?\s+(\d{5}(?:\.\d+)?)',

        # "2 Cal. Code Regs. § 18700"
        r'2\s+Cal\.?\s+Code\s+(?:of\s+)?Regs?\.?\s*§?\s*(\d{5}(?:\.\d+)?)',

        # "FPPC Regulation 18700"
        r'FPPC\s+[Rr]egulations?\s+(\d{5}(?:\.\d+)?)',

        # "Cal. Code Regs., tit. 2, § 18700"
        r'tit\.?\s*2,?\s*§?\s*(\d{5}(?:\.\d+)?)',

        # "Section 18700" in regulation context
        r'[Ss]ection\s+(\d{5}(?:\.\d+)?)\s+of\s+(?:the\s+)?(?:FPPC\s+)?[Rr]egulations?',
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        citations.extend(matches)

    # FPPC regulations are in 18000-18999 range
    valid = []
    for cite in citations:
        try:
            base_num = int(re.match(r'(\d+)', cite).group(1))
            if 18000 <= base_num <= 19000:
                valid.append(cite)
        except (ValueError, AttributeError):
            continue

    return valid


def _extract_prior_opinions(text: str) -> list[str]:
    """Extract references to prior FPPC advice letters and opinions."""

    patterns = [
        # Modern format: "A-24-006", "I-23-177", "M-00-033"
        r'\b([AIM]-\d{2}-\d{3})\b',

        # With "No." prefix: "No. A-24-006"
        r'No\.?\s*([AIM]-\d{2}-\d{3})',

        # Older format: "Advice Letter No. 24006"
        r'[Aa]dvice\s+[Ll]etter\s+(?:No\.?\s*)?(\d{5})',

        # "In re Smith, A-22-001"
        r'In\s+re\s+\w+,?\s+([AIM]-\d{2}-\d{3})',

        # Older opinion format: "Opinion No. 82-032"
        r'[Oo]pinion\s+(?:No\.?\s*)?(\d{2}-\d{3})',

        # File number format: "Our File No. A-24-006"
        r'(?:Our\s+)?File\s+No\.?\s*([AIM]-\d{2}-\d{3})',
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        citations.extend(matches)

    return citations


def _extract_external_citations(text: str) -> list[str]:
    """Extract court case and other external citations."""

    citations = []

    # California case citations: "123 Cal.App.4th 456"
    ca_pattern = r'\d+\s+Cal\.?\s*(?:App\.?\s*)?(?:2d|3d|4th|5th)?\s+\d+'
    citations.extend(re.findall(ca_pattern, text))

    # Federal case citations
    fed_pattern = r'\d+\s+(?:U\.S\.|F\.2d|F\.3d|F\.\s*Supp\.?)\s+\d+'
    citations.extend(re.findall(fed_pattern, text))

    # FPPC formal opinions: "In re Doe (1975) 1 FPPC Ops. 71"
    fppc_ops = r'In\s+re\s+\w+\s*\(\d{4}\)\s*\d+\s+FPPC\s+Ops\.?\s+\d+'
    citations.extend(re.findall(fppc_ops, text))

    return citations
```

---

## Task 3.5: Topic Classifier

**File**: `scraper/classifier.py`

```python
# scraper/classifier.py

"""
Heuristic topic classification based on cited code sections.

Government Code section ranges by topic:
- Conflicts of Interest: §§ 87100-87500
- Campaign Finance: §§ 84100-85800
- Lobbying: §§ 86100-86400
"""

from dataclasses import dataclass
from typing import Literal

TopicType = Literal["conflicts_of_interest", "campaign_finance", "lobbying", "other"]

@dataclass
class ClassificationResult:
    """Topic classification result."""
    topic_primary: TopicType | None
    confidence: float
    method: str
    section_counts: dict[str, int]


# Section ranges by topic
TOPIC_RANGES = {
    "conflicts_of_interest": [
        range(87100, 87600),  # General conflicts
        range(87200, 87220),  # Economic disclosure
        range(87300, 87315),  # Designated employees
    ],
    "campaign_finance": [
        range(84100, 84600),  # Campaign reporting
        range(85100, 85800),  # Contributions/expenditures
        range(89500, 89600),  # Mass mailing
    ],
    "lobbying": [
        range(86100, 86400),  # Lobbyist registration
    ],
}


def classify_by_citations(government_code_citations: list[str]) -> ClassificationResult:
    """
    Classify document topic based on Government Code citations.
    """
    if not government_code_citations:
        return ClassificationResult(
            topic_primary=None,
            confidence=0.0,
            method="heuristic:no_citations",
            section_counts={}
        )

    # Count citations by topic
    counts = {"conflicts_of_interest": 0, "campaign_finance": 0, "lobbying": 0}

    for cite in government_code_citations:
        try:
            base_num = int(cite.split('(')[0])
        except ValueError:
            continue

        for topic, ranges in TOPIC_RANGES.items():
            for r in ranges:
                if base_num in r:
                    counts[topic] += 1
                    break

    total = sum(counts.values())

    if total == 0:
        return ClassificationResult(
            topic_primary="other",
            confidence=0.5,
            method="heuristic:unknown_sections",
            section_counts=counts
        )

    # Find winner
    max_topic = max(counts, key=counts.get)
    max_count = counts[max_topic]

    if max_count == 0:
        topic = "other"
        confidence = 0.5
    else:
        topic = max_topic
        confidence = max_count / total

    return ClassificationResult(
        topic_primary=topic,
        confidence=round(confidence, 2),
        method="heuristic:citation_based",
        section_counts=counts
    )
```

---

## Task 3.6: Database Updates

**File**: Update `scraper/db.py`

```python
# Add to scraper/db.py

def add_extraction_columns():
    """Add extraction tracking columns to documents table."""
    conn = get_connection()
    cursor = conn.cursor()

    new_columns = [
        ("extraction_status", "TEXT DEFAULT 'pending'"),
        ("extracted_at", "TEXT"),
        ("extraction_method", "TEXT"),
        ("extraction_quality", "REAL"),
        ("section_confidence", "REAL"),
        ("json_path", "TEXT"),
        ("needs_llm_extraction", "INTEGER DEFAULT 0"),
        ("llm_extracted_at", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass  # Column already exists

    conn.commit()
    conn.close()


def get_pending_extractions(year: int = None, limit: int = None) -> list:
    """Get documents that need extraction."""
    conn = get_connection()
    cursor = conn.cursor()

    sql = """
        SELECT * FROM documents
        WHERE download_status = 'downloaded'
        AND (extraction_status = 'pending' OR extraction_status IS NULL)
    """
    if year:
        sql += f" AND year_tag = {year}"
    sql += " ORDER BY year_tag DESC, id"
    if limit:
        sql += f" LIMIT {limit}"

    cursor.execute(sql)
    results = cursor.fetchall()
    conn.close()
    return results


def get_documents_needing_llm(limit: int = None) -> list:
    """Get documents flagged for LLM extraction."""
    conn = get_connection()
    cursor = conn.cursor()

    sql = """
        SELECT * FROM documents
        WHERE extraction_status = 'extracted'
        AND needs_llm_extraction = 1
        AND llm_extracted_at IS NULL
        ORDER BY year_tag DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    cursor.execute(sql)
    results = cursor.fetchall()
    conn.close()
    return results


def update_extraction_status(doc_id: int, status: str, method: str = None,
                            quality: float = None, section_confidence: float = None,
                            json_path: str = None, needs_llm: bool = False):
    """Update extraction status for a document."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE documents
        SET extraction_status = ?,
            extracted_at = datetime('now'),
            extraction_method = COALESCE(?, extraction_method),
            extraction_quality = COALESCE(?, extraction_quality),
            section_confidence = COALESCE(?, section_confidence),
            json_path = COALESCE(?, json_path),
            needs_llm_extraction = ?
        WHERE id = ?
    """, (status, method, quality, section_confidence, json_path,
          1 if needs_llm else 0, doc_id))

    conn.commit()
    conn.close()
```

---

## Task 3.7: Core Extractor (Phase 3A)

**File**: `scraper/extractor.py`

```python
# scraper/extractor.py

"""
Core extraction pipeline for FPPC documents.

Phase 3A: Conservative extraction using native text + regex parsing.
Does NOT use LLM - that's Phase 3B (llm_extractor.py).

Usage:
    python -m scraper.extractor --extract-all
    python -m scraper.extractor --extract-year 2024
    python -m scraper.extractor --extract-sample 50
    python -m scraper.extractor --stats
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

import pymupdf
from dotenv import load_dotenv

from .config import RAW_PDFS_DIR, DATA_DIR
from .db import (get_connection, get_pending_extractions,
                 update_extraction_status, add_extraction_columns)
from .schema import (FPPCDocument, SourceMetadata, ExtractionInfo, Content,
                     ParsedMetadata, Sections, Citations, Classification,
                     EmbeddingContent)
from .quality import compute_quality_score, should_use_olmocr
from .section_parser import parse_sections
from .citation_extractor import extract_citations
from .classifier import classify_by_citations

load_dotenv()

EXTRACTED_DIR = DATA_DIR / "extracted"


class Extractor:
    """Core extraction pipeline."""

    def __init__(self, use_olmocr: bool = True):
        self.use_olmocr = use_olmocr
        self.olmocr_client = None

        if use_olmocr:
            api_key = os.getenv("DEEPINFRA_API_KEY")
            if api_key:
                from openai import OpenAI
                self.olmocr_client = OpenAI(
                    api_key=api_key,
                    base_url="https://api.deepinfra.com/v1/openai",
                )

    def extract_native(self, pdf_path: Path) -> dict:
        """Extract text using PyMuPDF native extraction."""
        doc = pymupdf.open(pdf_path)
        text_parts = []

        for page in doc:
            text_parts.append(page.get_text())

        text = "\n".join(text_parts)
        page_count = len(doc)
        doc.close()

        return {
            "text": text,
            "page_count": page_count,
            "word_count": len(text.split()),
            "char_count": len(text),
        }

    def extract_olmocr(self, pdf_path: Path, max_pages: int = 50) -> dict | None:
        """Extract text using olmOCR via DeepInfra API."""
        if not self.olmocr_client:
            return None

        import base64

        doc = pymupdf.open(pdf_path)
        pages_to_process = min(len(doc), max_pages)

        all_text = []
        all_markdown = []
        total_tokens = 0

        for page_num in range(pages_to_process):
            page = doc[page_num]
            zoom = 150 / 72  # 150 DPI
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            base64_image = base64.b64encode(img_bytes).decode()

            try:
                response = self.olmocr_client.chat.completions.create(
                    model="allenai/olmOCR-2-7B-1025",
                    max_tokens=4096,
                    messages=[{
                        "role": "user",
                        "content": [{
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                        }]
                    }]
                )

                page_text = response.choices[0].message.content
                all_markdown.append(f"<!-- Page {page_num + 1} -->\n{page_text}")
                all_text.append(self._markdown_to_plain(page_text))

                if response.usage:
                    total_tokens += response.usage.total_tokens

            except Exception as e:
                print(f"  olmOCR error on page {page_num + 1}: {e}")
                continue

        doc.close()

        if not all_text:
            return None

        text = "\n\n".join(all_text)
        markdown = "\n\n".join(all_markdown)
        cost = (total_tokens / 1_000_000) * 0.86

        return {
            "text": text,
            "markdown": markdown,
            "page_count": pages_to_process,
            "word_count": len(text.split()),
            "char_count": len(text),
            "cost": cost,
            "tokens": total_tokens,
        }

    def _markdown_to_plain(self, md: str) -> str:
        """Convert markdown to plain text."""
        import re
        text = md
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        return text

    def process_document(self, doc_record) -> FPPCDocument:
        """Process a single document through the extraction pipeline."""

        # Find PDF file
        pdf_path = self._find_pdf(doc_record)
        if not pdf_path:
            raise FileNotFoundError(f"PDF not found for {doc_record}")

        # Compute SHA256
        with open(pdf_path, 'rb') as f:
            pdf_sha256 = hashlib.sha256(f.read()).hexdigest()

        # Step 1: Native extraction
        native_result = self.extract_native(pdf_path)

        # Step 2: Quality assessment
        quality = compute_quality_score(
            native_result["text"],
            native_result["page_count"]
        )

        # Step 3: olmOCR if needed
        olmocr_result = None
        if self.use_olmocr and should_use_olmocr(doc_record.year_tag, quality):
            olmocr_result = self.extract_olmocr(pdf_path)

        # Step 4: Choose best result
        if olmocr_result and olmocr_result["word_count"] > native_result["word_count"]:
            primary_text = olmocr_result["text"]
            markdown_text = olmocr_result["markdown"]
            method = "olmocr"
            olmocr_cost = olmocr_result["cost"]
        else:
            primary_text = native_result["text"]
            markdown_text = None
            method = "native"
            olmocr_cost = olmocr_result["cost"] if olmocr_result else None

        # Step 5: Parse sections
        sections_result = parse_sections(primary_text, doc_record.year_tag)

        # Step 6: Extract citations
        citations_result = extract_citations(primary_text)

        # Step 7: Classify by citations
        classification_result = classify_by_citations(citations_result.government_code)

        # Step 8: Parse metadata from text
        parsed_meta = self._parse_metadata(primary_text, doc_record)

        # Step 9: Build embedding content
        embedding = self._build_embedding_content(sections_result, primary_text)

        # Step 10: Build document
        return FPPCDocument(
            id=doc_record.letter_id or self._generate_id(doc_record),
            year=doc_record.year_tag,
            pdf_url=doc_record.pdf_url,
            pdf_sha256=pdf_sha256,
            local_pdf_path=str(pdf_path.relative_to(Path.cwd())),
            source_metadata=SourceMetadata(
                title_raw=doc_record.title or "",
                tags=doc_record.tags.split(",") if doc_record.tags else [],
                scraped_at=doc_record.scraped_at or "",
                source_page_url=None,
            ),
            extraction=ExtractionInfo(
                method=method,
                extracted_at=datetime.now().isoformat(),
                page_count=native_result["page_count"],
                word_count=len(primary_text.split()),
                char_count=len(primary_text),
                quality_score=quality.final_score,
                olmocr_cost=olmocr_cost,
                native_word_count=native_result["word_count"],
            ),
            content=Content(
                full_text=primary_text,
                full_text_markdown=markdown_text,
            ),
            parsed=parsed_meta,
            sections=Sections(
                question=sections_result.question,
                conclusion=sections_result.conclusion,
                facts=sections_result.facts,
                analysis=sections_result.analysis,
                question_synthetic=None,  # Populated in Phase 3B
                conclusion_synthetic=None,
                extraction_method=sections_result.extraction_method,
                extraction_confidence=sections_result.extraction_confidence,
                has_standard_format=sections_result.has_standard_format,
                parsing_notes=sections_result.parsing_notes,
            ),
            citations=Citations(
                government_code=citations_result.government_code,
                regulations=citations_result.regulations,
                prior_opinions=citations_result.prior_opinions,
                cited_by=[],  # Populated in post-processing
                external=citations_result.external,
            ),
            classification=Classification(
                topic_primary=classification_result.topic_primary,
                topic_secondary=None,
                topic_tags=[],
                confidence=classification_result.confidence,
                classified_at=datetime.now().isoformat(),
                classification_method=classification_result.method,
            ),
            embedding=embedding,
        )

    def _find_pdf(self, doc_record) -> Path | None:
        """Find the local PDF file for a document record."""
        year = doc_record.year_tag
        filename = doc_record.pdf_url.split("/")[-1]

        path = RAW_PDFS_DIR / str(year) / filename
        if path.exists():
            return path

        # Try case-insensitive search
        year_dir = RAW_PDFS_DIR / str(year)
        if year_dir.exists():
            for f in year_dir.iterdir():
                if f.name.lower() == filename.lower():
                    return f

        return None

    def _generate_id(self, doc_record) -> str:
        """Generate an ID if letter_id is missing."""
        return f"UNK-{doc_record.year_tag}-{doc_record.id}"

    def _parse_metadata(self, text: str, doc_record) -> ParsedMetadata:
        """Parse metadata from document text."""
        import re

        # Extract date
        date_match = re.search(
            r'(January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
            text
        )
        date_raw = date_match.group(0) if date_match else None
        date_iso = None
        if date_match:
            try:
                from datetime import datetime as dt
                parsed = dt.strptime(date_raw.replace(",", ""), "%B %d %Y")
                date_iso = parsed.strftime("%Y-%m-%d")
            except:
                pass

        # Determine document type
        doc_type = "advice_letter"  # Default
        if re.search(r'informal\s+advice', text, re.I):
            doc_type = "informal_advice"
        elif re.search(r'\bopinion\b', text[:500], re.I):
            doc_type = "opinion"
        elif not re.search(r'FPPC|Fair Political|Political Reform', text, re.I):
            doc_type = "unknown"

        return ParsedMetadata(
            date=date_iso,
            date_raw=date_raw,
            requestor_name=None,  # Could parse from salutation
            requestor_title=None,
            requestor_city=None,
            document_type=doc_type,
        )

    def _build_embedding_content(self, sections, full_text: str) -> EmbeddingContent:
        """Build content optimized for embedding generation."""

        # Q+A text (prefer extracted, fall back to synthetic later)
        qa_parts = []
        qa_source = "extracted"

        if sections.question:
            qa_parts.append(f"QUESTION: {sections.question}")
        if sections.conclusion:
            qa_parts.append(f"CONCLUSION: {sections.conclusion}")

        if not qa_parts:
            qa_source = "synthetic"  # Will be filled by LLM later

        qa_text = "\n\n".join(qa_parts) if qa_parts else ""

        # First 500 words
        words = full_text.split()
        first_500 = " ".join(words[:500])

        return EmbeddingContent(
            qa_text=qa_text,
            qa_source=qa_source,
            first_500_words=first_500,
            summary=None,
        )

    def save_document(self, doc: FPPCDocument) -> Path:
        """Save extracted document to JSON file."""
        year_dir = EXTRACTED_DIR / str(doc.year)
        year_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize ID for filename
        safe_id = doc.id.replace("/", "-").replace("\\", "-")
        filename = f"{safe_id}.json"
        filepath = year_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(doc), f, indent=2, ensure_ascii=False)

        return filepath


def extract_sample(n: int = 50, output_dir: str = None):
    """
    Extract a representative sample for review.

    Samples across eras:
    - 10 docs from 1975-1985
    - 10 docs from 1986-1995
    - 10 docs from 1996-2005
    - 10 docs from 2006-2015
    - 10 docs from 2016-2025
    """
    from collections import defaultdict

    conn = get_connection()
    cursor = conn.cursor()

    # Get documents by era
    eras = [
        (1975, 1985),
        (1986, 1995),
        (1996, 2005),
        (2006, 2015),
        (2016, 2025),
    ]

    samples = []
    per_era = n // len(eras)

    for start, end in eras:
        cursor.execute("""
            SELECT * FROM documents
            WHERE download_status = 'downloaded'
            AND year_tag >= ? AND year_tag <= ?
            ORDER BY RANDOM()
            LIMIT ?
        """, (start, end, per_era))
        samples.extend(cursor.fetchall())

    conn.close()

    print(f"Extracting {len(samples)} sample documents...")

    extractor = Extractor(use_olmocr=False)  # Skip olmOCR for quick sample
    results = []

    for i, doc_record in enumerate(samples):
        print(f"  [{i+1}/{len(samples)}] {doc_record.year_tag}: {doc_record.pdf_url.split('/')[-1]}")
        try:
            doc = extractor.process_document(doc_record)
            filepath = extractor.save_document(doc)
            results.append({
                "id": doc.id,
                "year": doc.year,
                "quality": doc.extraction.quality_score,
                "section_confidence": doc.sections.extraction_confidence,
                "has_question": bool(doc.sections.question),
                "has_conclusion": bool(doc.sections.conclusion),
                "filepath": str(filepath),
            })
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({
                "id": doc_record.letter_id,
                "year": doc_record.year_tag,
                "error": str(e),
            })

    # Save summary
    summary_path = EXTRACTED_DIR / "sample_summary.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary
    success = [r for r in results if "error" not in r]
    has_q = sum(1 for r in success if r.get("has_question"))
    has_c = sum(1 for r in success if r.get("has_conclusion"))
    avg_quality = sum(r.get("quality", 0) for r in success) / len(success) if success else 0
    avg_confidence = sum(r.get("section_confidence", 0) for r in success) / len(success) if success else 0

    print(f"\n{'='*50}")
    print(f"Sample Extraction Summary")
    print(f"{'='*50}")
    print(f"Total: {len(samples)}, Success: {len(success)}, Errors: {len(results) - len(success)}")
    print(f"Has QUESTION: {has_q}/{len(success)} ({100*has_q/len(success):.1f}%)")
    print(f"Has CONCLUSION: {has_c}/{len(success)} ({100*has_c/len(success):.1f}%)")
    print(f"Avg quality score: {avg_quality:.2f}")
    print(f"Avg section confidence: {avg_confidence:.2f}")
    print(f"\nReview files in: {EXTRACTED_DIR}")
    print(f"Summary saved to: {summary_path}")


def extract_all(dry_run: bool = False, skip_olmocr: bool = False):
    """Extract all pending documents."""
    add_extraction_columns()

    docs = get_pending_extractions()
    print(f"Found {len(docs)} documents to extract")

    if dry_run:
        print("Dry run - not extracting")
        return

    extractor = Extractor(use_olmocr=not skip_olmocr)

    for i, doc_record in enumerate(docs):
        print(f"[{i+1}/{len(docs)}] {doc_record.year_tag}: {doc_record.pdf_url.split('/')[-1]}")

        try:
            doc = extractor.process_document(doc_record)
            filepath = extractor.save_document(doc)

            # Flag for LLM extraction if needed
            needs_llm = (
                doc.sections.extraction_confidence < 0.7 or
                not doc.sections.has_standard_format
            )

            update_extraction_status(
                doc_id=doc_record.id,
                status="extracted",
                method=doc.extraction.method,
                quality=doc.extraction.quality_score,
                section_confidence=doc.sections.extraction_confidence,
                json_path=str(filepath),
                needs_llm=needs_llm,
            )

            print(f"  ✓ quality={doc.extraction.quality_score:.2f}, "
                  f"sections={doc.sections.extraction_confidence:.2f}, "
                  f"llm_needed={needs_llm}")

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            update_extraction_status(doc_record.id, "error")


def show_stats():
    """Show extraction statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            extraction_status,
            COUNT(*) as count,
            AVG(extraction_quality) as avg_quality,
            AVG(section_confidence) as avg_confidence,
            SUM(needs_llm_extraction) as needs_llm
        FROM documents
        WHERE download_status = 'downloaded'
        GROUP BY extraction_status
    """)

    print("\nExtraction Status:")
    print("-" * 60)
    for row in cursor.fetchall():
        print(f"  {row[0] or 'pending':12} {row[1]:>6} docs  "
              f"quality={row[2] or 0:.2f}  confidence={row[3] or 0:.2f}  "
              f"needs_llm={row[4] or 0}")

    cursor.execute("""
        SELECT year_tag, COUNT(*), AVG(extraction_quality), AVG(section_confidence)
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY year_tag
        ORDER BY year_tag DESC
        LIMIT 10
    """)

    print("\nBy Year (recent):")
    print("-" * 60)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]:>4} docs  quality={row[2]:.2f}  confidence={row[3]:.2f}")

    conn.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FPPC Document Extractor (Phase 3A)")
    parser.add_argument("--extract-all", action="store_true",
                        help="Extract all pending documents")
    parser.add_argument("--extract-year", type=int,
                        help="Extract documents from specific year")
    parser.add_argument("--extract-sample", type=int, default=50,
                        help="Extract N sample documents for review")
    parser.add_argument("--stats", action="store_true",
                        help="Show extraction statistics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be extracted")
    parser.add_argument("--skip-olmocr", action="store_true",
                        help="Skip olmOCR (native only)")
    parser.add_argument("--init", action="store_true",
                        help="Initialize extraction columns in DB")

    args = parser.parse_args()

    if args.init:
        add_extraction_columns()
        print("Extraction columns initialized")
    elif args.stats:
        show_stats()
    elif args.extract_sample:
        extract_sample(args.extract_sample)
    elif args.extract_all:
        extract_all(dry_run=args.dry_run, skip_olmocr=args.skip_olmocr)
    elif args.extract_year:
        # TODO: implement year-specific extraction
        print(f"Would extract year {args.extract_year}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

---

## Task 3.9: LLM Extractor (Phase 3B)

**File**: `scraper/llm_extractor.py`

```python
# scraper/llm_extractor.py

"""
LLM-based section extraction for documents without standard format.

Uses Claude Haiku to:
1. Extract question/conclusion from unstructured text
2. Generate synthetic Q/A for documents without clear sections
3. Optionally generate summaries

Usage:
    python -m scraper.llm_extractor --process-pending
    python -m scraper.llm_extractor --process-one 12345
    python -m scraper.llm_extractor --estimate-cost
"""

import os
import json
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

import anthropic
from dotenv import load_dotenv

from .config import DATA_DIR
from .db import get_connection, get_documents_needing_llm

load_dotenv()

EXTRACTED_DIR = DATA_DIR / "extracted"

# Claude Haiku pricing (as of 2024)
HAIKU_INPUT_COST = 0.25 / 1_000_000   # $0.25 per million input tokens
HAIKU_OUTPUT_COST = 1.25 / 1_000_000  # $1.25 per million output tokens


EXTRACTION_PROMPT = """You are analyzing an FPPC (California Fair Political Practices Commission) advice letter. Your task is to extract or synthesize the legal question and conclusion.

<document>
{text}
</document>

Instructions:
1. Look for explicit QUESTION/CONCLUSION sections. If found, extract them verbatim.
2. If no explicit sections exist, synthesize them from the document content:
   - question_synthetic: A clear, one-sentence statement of the legal question addressed
   - conclusion_synthetic: A clear summary of the FPPC's answer/position

Return ONLY valid JSON (no markdown, no explanation):
{{
  "question_extracted": "..." or null,
  "conclusion_extracted": "..." or null,
  "question_synthetic": "...",
  "conclusion_synthetic": "...",
  "document_type": "advice_letter" | "correspondence" | "other",
  "confidence": 0.0-1.0,
  "notes": "..." or null
}}"""


class LLMExtractor:
    """LLM-based extraction using Claude Haiku."""

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def extract_sections(self, text: str, max_chars: int = 15000) -> dict:
        """Extract/synthesize sections using Claude Haiku."""

        # Truncate if needed (save tokens)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[... truncated ...]"

        prompt = EXTRACTION_PROMPT.format(text=text)

        response = self.client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        # Track token usage
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        # Parse response
        try:
            result = json.loads(response.content[0].text)
            return result
        except json.JSONDecodeError:
            return {
                "error": "Failed to parse LLM response",
                "raw_response": response.content[0].text[:500]
            }

    def get_cost(self) -> float:
        """Get total cost so far."""
        return (self.total_input_tokens * HAIKU_INPUT_COST +
                self.total_output_tokens * HAIKU_OUTPUT_COST)

    def process_document(self, json_path: Path) -> dict:
        """Process a single document JSON file."""

        with open(json_path) as f:
            doc = json.load(f)

        # Extract using LLM
        result = self.extract_sections(doc["content"]["full_text"])

        if "error" in result:
            return {"error": result["error"], "path": str(json_path)}

        # Update document
        doc["sections"]["question_synthetic"] = result.get("question_synthetic")
        doc["sections"]["conclusion_synthetic"] = result.get("conclusion_synthetic")

        # If LLM found explicit sections we missed, use them
        if result.get("question_extracted") and not doc["sections"]["question"]:
            doc["sections"]["question"] = result["question_extracted"]
        if result.get("conclusion_extracted") and not doc["sections"]["conclusion"]:
            doc["sections"]["conclusion"] = result["conclusion_extracted"]

        # Update embedding content
        qa_parts = []
        if doc["sections"]["question"] or doc["sections"]["question_synthetic"]:
            q = doc["sections"]["question"] or doc["sections"]["question_synthetic"]
            qa_parts.append(f"QUESTION: {q}")
        if doc["sections"]["conclusion"] or doc["sections"]["conclusion_synthetic"]:
            c = doc["sections"]["conclusion"] or doc["sections"]["conclusion_synthetic"]
            qa_parts.append(f"CONCLUSION: {c}")

        doc["embedding"]["qa_text"] = "\n\n".join(qa_parts)
        doc["embedding"]["qa_source"] = "mixed" if doc["sections"]["question"] else "synthetic"

        # Update document type if LLM detected non-opinion
        if result.get("document_type") == "correspondence":
            doc["parsed"]["document_type"] = "correspondence"

        # Save updated document
        with open(json_path, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)

        return {
            "id": doc["id"],
            "path": str(json_path),
            "has_synthetic_q": bool(result.get("question_synthetic")),
            "has_synthetic_c": bool(result.get("conclusion_synthetic")),
            "document_type": result.get("document_type"),
            "confidence": result.get("confidence"),
        }


def estimate_cost():
    """Estimate cost for processing all documents needing LLM."""
    docs = get_documents_needing_llm()
    print(f"Documents needing LLM extraction: {len(docs)}")

    # Estimate average tokens per document
    avg_input_tokens = 3000   # ~15KB of text
    avg_output_tokens = 300   # JSON response

    total_input = len(docs) * avg_input_tokens
    total_output = len(docs) * avg_output_tokens

    cost = total_input * HAIKU_INPUT_COST + total_output * HAIKU_OUTPUT_COST

    print(f"Estimated tokens: {total_input:,} input, {total_output:,} output")
    print(f"Estimated cost: ${cost:.2f}")


def process_pending(limit: int = None, dry_run: bool = False):
    """Process documents flagged for LLM extraction."""
    docs = get_documents_needing_llm(limit=limit)
    print(f"Found {len(docs)} documents needing LLM extraction")

    if dry_run:
        print("Dry run - not processing")
        return

    extractor = LLMExtractor()
    results = []

    for i, doc_record in enumerate(docs):
        json_path = Path(doc_record.json_path) if doc_record.json_path else None

        if not json_path or not json_path.exists():
            print(f"  [{i+1}/{len(docs)}] SKIP: No JSON file for {doc_record.letter_id}")
            continue

        print(f"  [{i+1}/{len(docs)}] Processing {doc_record.letter_id}...")

        try:
            result = extractor.process_document(json_path)
            results.append(result)

            # Update DB
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE documents
                SET llm_extracted_at = datetime('now'),
                    needs_llm_extraction = 0
                WHERE id = ?
            """, (doc_record.id,))
            conn.commit()
            conn.close()

            print(f"    ✓ synthetic_q={result.get('has_synthetic_q')}, "
                  f"type={result.get('document_type')}")

        except Exception as e:
            print(f"    ✗ ERROR: {e}")
            results.append({"id": doc_record.letter_id, "error": str(e)})

        # Print cost update every 100 docs
        if (i + 1) % 100 == 0:
            print(f"    [Cost so far: ${extractor.get_cost():.2f}]")

    print(f"\nCompleted. Total cost: ${extractor.get_cost():.2f}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="LLM-based extraction (Phase 3B)")
    parser.add_argument("--process-pending", action="store_true",
                        help="Process all documents flagged for LLM")
    parser.add_argument("--process-one", type=int,
                        help="Process single document by DB id")
    parser.add_argument("--limit", type=int,
                        help="Limit number of documents to process")
    parser.add_argument("--estimate-cost", action="store_true",
                        help="Estimate cost without processing")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed")

    args = parser.parse_args()

    if args.estimate_cost:
        estimate_cost()
    elif args.process_pending:
        process_pending(limit=args.limit, dry_run=args.dry_run)
    elif args.process_one:
        # TODO: implement single document processing
        print(f"Would process document {args.process_one}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

---

## Task 3.11: Post-Processing (Citation Graph)

**File**: `scraper/postprocess.py`

```python
# scraper/postprocess.py

"""
Post-processing to build citation graph (cited_by relationships).

Usage:
    python -m scraper.postprocess --build-citation-graph
"""

import json
from pathlib import Path
from collections import defaultdict

from .config import DATA_DIR

EXTRACTED_DIR = DATA_DIR / "extracted"


def build_citation_graph():
    """
    Build cited_by relationships across all documents.

    For each document, find all other documents that cite it
    and populate the cited_by field.
    """

    # Phase 1: Build index of all documents and their citations
    print("Phase 1: Scanning documents...")

    doc_index = {}  # id -> filepath
    cites_index = defaultdict(list)  # cited_id -> [citing_ids]

    for year_dir in EXTRACTED_DIR.iterdir():
        if not year_dir.is_dir():
            continue

        for json_file in year_dir.glob("*.json"):
            with open(json_file) as f:
                doc = json.load(f)

            doc_id = doc["id"]
            doc_index[doc_id] = json_file

            # Record citations
            for cited_id in doc["citations"]["prior_opinions"]:
                cites_index[cited_id].append(doc_id)

    print(f"  Indexed {len(doc_index)} documents")
    print(f"  Found {len(cites_index)} cited documents")

    # Phase 2: Update documents with cited_by
    print("Phase 2: Updating cited_by relationships...")

    updated = 0
    for doc_id, citing_ids in cites_index.items():
        if doc_id not in doc_index:
            continue  # Cited document not in our corpus

        filepath = doc_index[doc_id]
        with open(filepath) as f:
            doc = json.load(f)

        # Update cited_by
        doc["citations"]["cited_by"] = sorted(set(citing_ids))

        with open(filepath, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)

        updated += 1

    print(f"  Updated {updated} documents with cited_by data")

    # Stats
    cited_counts = [len(v) for v in cites_index.values()]
    if cited_counts:
        print(f"\nCitation statistics:")
        print(f"  Most cited: {max(cited_counts)} times")
        print(f"  Average citations: {sum(cited_counts)/len(cited_counts):.1f}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Post-processing")
    parser.add_argument("--build-citation-graph", action="store_true",
                        help="Build cited_by relationships")

    args = parser.parse_args()

    if args.build_citation_graph:
        build_citation_graph()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

---

## Estimated Costs

| Component | Documents | Tokens | Cost |
|-----------|-----------|--------|------|
| Native extraction | 14,096 | N/A | Free |
| olmOCR (pre-1990 + failures) | ~4,000 | ~20M | ~$17 |
| Claude Haiku (low-confidence) | ~5,000 | ~16.5M | ~$8 |
| **Total** | 14,096 | ~36.5M | **~$25** |

With your $100 budget ceiling, there's ample room for:
- Processing retries
- Additional LLM calls for edge cases
- Quality improvements

---

## CLI Commands Summary

```bash
# Task 3.6: Initialize database columns
python -m scraper.extractor --init

# Task 3.8: Extract review sample (50 docs across eras)
python -m scraper.extractor --extract-sample 50

# Review sample results
ls data/extracted/
cat data/extracted/sample_summary.json | jq

# Task 3.7: Full extraction (Phase 3A) - native + regex
python -m scraper.extractor --extract-all

# Task 3.7 alternate: Skip olmOCR for speed
python -m scraper.extractor --extract-all --skip-olmocr

# Check progress
python -m scraper.extractor --stats

# Task 3.9: Estimate LLM costs
python -m scraper.llm_extractor --estimate-cost

# Task 3.9: Run LLM extraction (Phase 3B)
python -m scraper.llm_extractor --process-pending

# Task 3.9 alternate: Process limited batch
python -m scraper.llm_extractor --process-pending --limit 100

# Task 3.11: Build citation graph
python -m scraper.postprocess --build-citation-graph
```

---

## Testing Strategy

### Unit Tests

```python
# tests/test_section_parser.py

def test_modern_format():
    """Test extraction from modern FPPC format."""
    text = """
    QUESTION

    Whether the official may vote on the contract.

    CONCLUSION

    No, the official may not vote.

    FACTS

    The requestor is a city council member.
    """

    result = parse_sections(text, year=2024)
    assert result.has_standard_format
    assert result.extraction_confidence >= 0.8
    assert "vote on the contract" in result.question
    assert "may not vote" in result.conclusion


def test_no_false_positive():
    """Test that mid-paragraph 'question' doesn't trigger extraction."""
    text = """
    Dear Mr. Smith:

    This letter addresses your inquiry. The central question
    is whether the official may participate. We conclude that
    the answer depends on the facts.

    ANALYSIS

    Government Code Section 87100...
    """

    result = parse_sections(text, year=2020)
    # Should NOT extract the mid-paragraph "question"
    assert result.question is None or result.extraction_confidence < 0.5
```

### Integration Test Workflow

```bash
# 1. Extract small sample
python -m scraper.extractor --extract-sample 10

# 2. Manually review JSON files
cat data/extracted/2024/*.json | jq '.sections'

# 3. Check extraction quality
python -m scraper.extractor --stats

# 4. If quality looks good, run full extraction
python -m scraper.extractor --extract-all
```

---

## Next Steps After Phase 3

### Phase 4: Search Infrastructure
1. Load JSON into Meilisearch or similar
2. Create faceted search by topic, year, citations
3. Generate embeddings for `embedding.qa_text`
4. Build search API

### Phase 5: Advanced Features
1. MCP server for Claude.ai integration
2. RAG pipeline for question answering
3. Citation network visualization
4. Topic trend analysis over time
