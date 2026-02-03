# Phase 3 Implementation Plan: Text Extraction & Structuring

## Overview

This document provides the complete implementation plan for Phase 3 of the FPPC scraping project. The goal is to extract text from all 14,096 downloaded PDFs and structure them into searchable JSON files that support downstream search, RAG, and analysis use cases.

### Current State
- **Phase 1 (Complete)**: 14,132 documents indexed in SQLite
- **Phase 2 (Complete)**: 14,096 unique PDFs downloaded to `raw_pdfs/{year}/`
- **Database**: `data/documents.db` with full metadata

### Phase 3 Goals
1. Extract text from all PDFs (native extraction + olmOCR fallback)
2. Parse document sections (QUESTION, CONCLUSION, FACTS, ANALYSIS)
3. Extract legal citations (Government Code, Regulations, prior opinions)
4. Output structured JSON files ready for search/embedding

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
    letter_type: Literal["formal", "informal", "opinion"] | None

@dataclass
class Sections:
    """Structured sections from the document."""
    question: str | None              # The QUESTION section
    conclusion: str | None            # The CONCLUSION/SHORT ANSWER
    facts: str | None                 # The FACTS section
    analysis: str | None              # The ANALYSIS section
    has_standard_format: bool         # True if Q/C sections were found
    parsing_notes: str | None         # Any issues encountered

@dataclass
class Citations:
    """Legal citations found in the document."""
    government_code: list[str]        # ["87100", "87103(a)", "87200"]
    regulations: list[str]            # ["18700", "18702.1", "18730"]
    prior_opinions: list[str]         # ["A-23-001", "I-22-015"]
    external: list[str]               # Other citations

@dataclass
class Classification:
    """Topic classification (populated in Phase 4)."""
    topic_primary: Literal["conflicts_of_interest", "campaign_finance", "lobbying", "other"] | None
    topic_secondary: str | None
    topic_tags: list[str]             # Granular tags
    confidence: float | None          # Classification confidence
    classified_at: str | None
    classification_method: str | None # "heuristic", "llm:claude-3-sonnet", etc.

@dataclass
class Generated:
    """LLM-generated content (populated in Phase 4)."""
    question_synthetic: str | None    # For docs without clear Q section
    answer_synthetic: str | None      # For docs without clear A section
    summary: str | None               # Brief summary of the opinion
    generated_at: str | None
    generation_model: str | None

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
    generated: Generated
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
    "letter_type": "formal"
  },

  "sections": {
    "question": "Whether a City Council Member who is employed by a company that provides water well services must recuse himself from decisions affecting water infrastructure.",
    "conclusion": "Yes, the Council Member must recuse himself from decisions that would have a reasonably foreseeable material financial effect on his employer.",
    "facts": "You state that you are a member of the Bakersfield City Council...",
    "analysis": "Government Code Section 87100 prohibits a public official from...",
    "has_standard_format": true,
    "parsing_notes": null
  },

  "citations": {
    "government_code": ["87100", "87103", "87103(a)", "82030"],
    "regulations": ["18700", "18701", "18702.1", "18702.2"],
    "prior_opinions": ["A-23-142", "A-22-088"],
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

  "generated": {
    "question_synthetic": null,
    "answer_synthetic": null,
    "summary": null,
    "generated_at": null,
    "generation_model": null
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
    ├── db.py                        # Existing DB operations
    ├── crawler.py                   # Existing crawler
    ├── downloader.py                # Existing downloader
    ├── schema.py                    # NEW: Pydantic/dataclass models
    ├── extractor.py                 # NEW: Core extraction logic
    ├── section_parser.py            # NEW: Section extraction
    ├── citation_extractor.py        # NEW: Citation extraction
    └── quality.py                   # NEW: Quality scoring
```

---

## Phase 3A: Core Extraction

### Decision Logic

```python
def should_use_olmocr(doc_record, native_result) -> bool:
    """Decide whether to use olmOCR for a document."""

    # Always use olmOCR for very old documents (likely scanned)
    if doc_record.year_tag < 1990:
        return True

    # Use olmOCR if native extraction failed
    if native_result.word_count < 50:
        return True

    # Use olmOCR if quality indicators are poor
    words_per_page = native_result.word_count / native_result.page_count
    if words_per_page < 80:  # Too sparse, likely scan
        return True

    # Use olmOCR if alpha ratio is low (OCR garbage)
    if native_result.alpha_ratio < 0.7:
        return True

    # Suspicious: 1990-2005 era with no extracted date
    if 1990 <= doc_record.year_tag <= 2005:
        if not native_result.extracted_date:
            return True

    return False
```

### Extraction Module

```python
# scraper/extractor.py

"""
Core extraction module for FPPC documents.

Usage:
    python -m scraper.extractor --extract-all
    python -m scraper.extractor --extract-year 2024
    python -m scraper.extractor --extract-pending
    python -m scraper.extractor --stats
"""

import os
import json
import base64
import time
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

import pymupdf
from openai import OpenAI
from dotenv import load_dotenv

from .config import RAW_PDFS_DIR, DATA_DIR, HEADERS
from .db import get_connection
from .schema import FPPCDocument, ExtractionInfo, Content, ...
from .section_parser import parse_sections
from .citation_extractor import extract_citations
from .quality import compute_quality_score

load_dotenv()

DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY")
OLMOCR_MODEL = "allenai/olmOCR-2-7B-1025"
EXTRACTED_DIR = DATA_DIR / "extracted"


class Extractor:
    def __init__(self):
        self.client = None
        if DEEPINFRA_API_KEY:
            self.client = OpenAI(
                api_key=DEEPINFRA_API_KEY,
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

    def extract_olmocr(self, pdf_path: Path, max_pages: int = 50) -> dict:
        """Extract text using olmOCR via DeepInfra API."""
        if not self.client:
            raise ValueError("DeepInfra API key not configured")

        doc = pymupdf.open(pdf_path)
        pages_to_process = min(len(doc), max_pages)

        all_text = []
        all_markdown = []
        total_tokens = 0

        for page_num in range(pages_to_process):
            # Convert page to image
            page = doc[page_num]
            zoom = 150 / 72  # 150 DPI
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            base64_image = base64.b64encode(img_bytes).decode()

            # Call olmOCR
            response = self.client.chat.completions.create(
                model=OLMOCR_MODEL,
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

            # Strip markdown for plain text version
            plain_text = self._markdown_to_plain(page_text)
            all_text.append(plain_text)

            if response.usage:
                total_tokens += response.usage.total_tokens

        doc.close()

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
        """Convert markdown to plain text (simple version)."""
        import re
        text = md
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # Italic
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)  # Headers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)  # Lists
        return text

    def process_document(self, doc_record) -> FPPCDocument:
        """Process a single document through the full pipeline."""

        # Find the PDF file
        pdf_path = self._find_pdf(doc_record)
        if not pdf_path:
            raise FileNotFoundError(f"PDF not found for {doc_record.id}")

        # Step 1: Try native extraction
        native_result = self.extract_native(pdf_path)

        # Step 2: Decide if we need olmOCR
        use_olmocr = should_use_olmocr(doc_record, native_result)

        # Step 3: Run olmOCR if needed
        olmocr_result = None
        if use_olmocr and self.client:
            olmocr_result = self.extract_olmocr(pdf_path)

        # Step 4: Choose best result
        if olmocr_result and olmocr_result["word_count"] > native_result["word_count"]:
            primary_text = olmocr_result["text"]
            markdown_text = olmocr_result["markdown"]
            method = "olmocr"
            word_count = olmocr_result["word_count"]
            olmocr_cost = olmocr_result["cost"]
        else:
            primary_text = native_result["text"]
            markdown_text = None
            method = "native" if not use_olmocr else "native+olmocr"
            word_count = native_result["word_count"]
            olmocr_cost = olmocr_result["cost"] if olmocr_result else None

        # Step 5: Parse sections
        sections = parse_sections(primary_text)

        # Step 6: Extract citations
        citations = extract_citations(primary_text)

        # Step 7: Compute quality score
        quality_score = compute_quality_score(primary_text, native_result["page_count"])

        # Step 8: Parse metadata from text
        parsed_meta = self._parse_metadata(primary_text, doc_record)

        # Step 9: Heuristic classification (based on citations)
        classification = self._classify_by_citations(citations)

        # Build the document
        return FPPCDocument(
            id=doc_record.letter_id or self._generate_id(doc_record),
            year=doc_record.year_tag,
            pdf_url=doc_record.pdf_url,
            pdf_sha256=doc_record.pdf_sha256,
            local_pdf_path=str(pdf_path.relative_to(Path.cwd())),
            source_metadata=SourceMetadata(...),
            extraction=ExtractionInfo(
                method=method,
                extracted_at=datetime.now().isoformat(),
                page_count=native_result["page_count"],
                word_count=word_count,
                char_count=len(primary_text),
                quality_score=quality_score,
                olmocr_cost=olmocr_cost,
                native_word_count=native_result["word_count"],
            ),
            content=Content(
                full_text=primary_text,
                full_text_markdown=markdown_text,
            ),
            parsed=parsed_meta,
            sections=sections,
            citations=citations,
            classification=classification,
            generated=Generated(...),  # All null for now
        )

    def _find_pdf(self, doc_record) -> Path | None:
        """Find the local PDF file for a document record."""
        # PDFs are stored as raw_pdfs/{year}/{filename}
        year = doc_record.year_tag
        # Extract filename from URL
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

    def save_document(self, doc: FPPCDocument):
        """Save extracted document to JSON file."""
        year_dir = EXTRACTED_DIR / str(doc.year)
        year_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{doc.id}.json"
        filepath = year_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(doc), f, indent=2, ensure_ascii=False)

        return filepath
```

### CLI Interface

```python
# At the bottom of scraper/extractor.py

def main():
    import argparse

    parser = argparse.ArgumentParser(description="FPPC Document Extractor")
    parser.add_argument("--extract-all", action="store_true",
                        help="Extract all pending documents")
    parser.add_argument("--extract-year", type=int,
                        help="Extract documents from specific year")
    parser.add_argument("--extract-one", type=int,
                        help="Extract single document by DB id")
    parser.add_argument("--stats", action="store_true",
                        help="Show extraction statistics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be extracted without doing it")
    parser.add_argument("--force-olmocr", action="store_true",
                        help="Force olmOCR even if native succeeds")
    parser.add_argument("--skip-olmocr", action="store_true",
                        help="Skip olmOCR entirely (native only)")

    args = parser.parse_args()

    extractor = Extractor()

    if args.stats:
        show_extraction_stats()
    elif args.extract_all:
        extract_all(extractor, dry_run=args.dry_run)
    elif args.extract_year:
        extract_year(extractor, args.extract_year, dry_run=args.dry_run)
    elif args.extract_one:
        extract_one(extractor, args.extract_one)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

---

## Phase 3B: Section Parser

### Section Patterns

FPPC advice letters follow several formats across different eras:

**Modern Format (2010+)**:
```
QUESTION
[question text]

CONCLUSION
[conclusion text]

FACTS
[facts text]

ANALYSIS
[analysis text]
```

**Older Format (1990s-2000s)**:
```
QUESTION PRESENTED
[question text]

SHORT ANSWER
[answer text]

DISCUSSION
[discussion text]
```

**Very Old Format (1975-1989)**:
Often less structured, may just be a letter with paragraphs.

### Implementation

```python
# scraper/section_parser.py

"""
Parse structured sections from FPPC advice letters.
"""

import re
from dataclasses import dataclass

@dataclass
class Sections:
    question: str | None
    conclusion: str | None
    facts: str | None
    analysis: str | None
    has_standard_format: bool
    parsing_notes: str | None


# Section header patterns (order matters - more specific first)
SECTION_PATTERNS = [
    # Modern format
    (r'(?:^|\n)\s*QUESTION[S]?\s*(?:\n|:)', 'question'),
    (r'(?:^|\n)\s*CONCLUSION[S]?\s*(?:\n|:)', 'conclusion'),
    (r'(?:^|\n)\s*SHORT\s+ANSWER[S]?\s*(?:\n|:)', 'conclusion'),
    (r'(?:^|\n)\s*FACT[S]?\s*(?:\n|:)', 'facts'),
    (r'(?:^|\n)\s*ANALYSIS\s*(?:\n|:)', 'analysis'),
    (r'(?:^|\n)\s*DISCUSSION\s*(?:\n|:)', 'analysis'),

    # Older format variants
    (r'(?:^|\n)\s*QUESTION\s+PRESENTED\s*(?:\n|:)', 'question'),
    (r'(?:^|\n)\s*ISSUES?\s+PRESENTED\s*(?:\n|:)', 'question'),
    (r'(?:^|\n)\s*SUMMARY\s*(?:\n|:)', 'conclusion'),
]


def parse_sections(text: str) -> Sections:
    """
    Extract structured sections from document text.

    Returns a Sections object with extracted content for each section,
    or None if the section wasn't found.
    """

    # Find all section headers and their positions
    section_positions = []

    for pattern, section_type in SECTION_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            section_positions.append({
                'type': section_type,
                'start': match.end(),  # Start of content (after header)
                'header_start': match.start(),
                'header': match.group().strip(),
            })

    # Sort by position in document
    section_positions.sort(key=lambda x: x['start'])

    # Remove duplicates (keep first occurrence of each type)
    seen_types = set()
    unique_sections = []
    for sec in section_positions:
        if sec['type'] not in seen_types:
            seen_types.add(sec['type'])
            unique_sections.append(sec)

    # Extract content for each section
    extracted = {'question': None, 'conclusion': None, 'facts': None, 'analysis': None}

    for i, sec in enumerate(unique_sections):
        # Find end of this section (start of next section, or end of document)
        if i + 1 < len(unique_sections):
            end_pos = unique_sections[i + 1]['header_start']
        else:
            # For last section, try to find a reasonable end
            # (signature, page break, etc.)
            end_pos = _find_section_end(text, sec['start'])

        content = text[sec['start']:end_pos].strip()

        # Clean up the content
        content = _clean_section_content(content)

        if content and len(content) > 20:  # Minimum viable content
            extracted[sec['type']] = content

    # Determine if document has standard format
    has_standard = bool(extracted['question'] or extracted['conclusion'])

    # Build notes about parsing
    notes = None
    if not has_standard:
        notes = "No standard Q/C sections found; may need LLM extraction"
    elif extracted['question'] and not extracted['conclusion']:
        notes = "Question found but no conclusion"

    return Sections(
        question=extracted['question'],
        conclusion=extracted['conclusion'],
        facts=extracted['facts'],
        analysis=extracted['analysis'],
        has_standard_format=has_standard,
        parsing_notes=notes,
    )


def _find_section_end(text: str, start: int) -> int:
    """Find a reasonable end point for the last section."""

    # Look for common ending patterns
    end_patterns = [
        r'\n\s*Sincerely,',
        r'\n\s*Very truly yours,',
        r'\n\s*Respectfully,',
        r'\n\s*[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+\s*\n',  # Name signature
        r'\n\s*\*\s*\*\s*\*',  # Asterisk separator
    ]

    search_text = text[start:]
    min_end = len(text)

    for pattern in end_patterns:
        match = re.search(pattern, search_text)
        if match:
            end = start + match.start()
            min_end = min(min_end, end)

    return min_end


def _clean_section_content(content: str) -> str:
    """Clean up extracted section content."""

    # Remove excessive whitespace
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r'[ \t]+', ' ', content)

    # Remove page numbers and headers that might have been captured
    content = re.sub(r'\n\s*-?\s*\d+\s*-?\s*\n', '\n', content)  # Page numbers
    content = re.sub(r'\n\s*Page\s+\d+\s*\n', '\n', content, flags=re.IGNORECASE)

    return content.strip()
```

---

## Phase 3C: Citation Extractor

### Citation Patterns

FPPC documents cite:
1. **Government Code** sections (e.g., "Section 87100", "Government Code section 87103(a)")
2. **FPPC Regulations** (e.g., "Regulation 18700", "2 Cal. Code Regs. § 18702.1")
3. **Prior Advice Letters** (e.g., "Advice Letter A-23-001", "In re Doe, I-22-015")

### Implementation

```python
# scraper/citation_extractor.py

"""
Extract legal citations from FPPC advice letters.
"""

import re
from dataclasses import dataclass

@dataclass
class Citations:
    government_code: list[str]
    regulations: list[str]
    prior_opinions: list[str]
    external: list[str]


def extract_citations(text: str) -> Citations:
    """Extract all legal citations from document text."""

    gov_code = extract_government_code(text)
    regulations = extract_regulations(text)
    prior_opinions = extract_prior_opinions(text)
    external = extract_external_citations(text)

    return Citations(
        government_code=sorted(set(gov_code)),
        regulations=sorted(set(regulations)),
        prior_opinions=sorted(set(prior_opinions)),
        external=sorted(set(external)),
    )


def extract_government_code(text: str) -> list[str]:
    """Extract Government Code section citations."""

    patterns = [
        # "Section 87100", "Sections 87100 and 87103"
        r'[Ss]ections?\s+(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # "Government Code section 87100"
        r'Government\s+Code\s+[Ss]ections?\s+(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # "Gov. Code § 87100", "Gov. Code, § 87100"
        r'Gov(?:\.|ernment)\s+Code,?\s*§+\s*(\d{5}(?:\([a-z]\))?(?:\(\d+\))?)',

        # Just "§ 87100" in context (be careful, could be regulations)
        r'(?:Code\s+)?§+\s*(\d{5}(?:\([a-z]\))?)',
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        citations.extend(matches)

    # Filter to valid FPPC-related code sections
    # Political Reform Act is primarily sections 81000-91014
    valid_citations = []
    for cite in citations:
        # Extract the base number
        base_num = int(re.match(r'(\d+)', cite).group(1))
        if 81000 <= base_num <= 92000:  # Political Reform Act range
            valid_citations.append(cite)

    return valid_citations


def extract_regulations(text: str) -> list[str]:
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
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        citations.extend(matches)

    # FPPC regulations are in the 18000 range
    valid_citations = []
    for cite in citations:
        base_num = int(re.match(r'(\d+)', cite).group(1))
        if 18000 <= base_num <= 19000:
            valid_citations.append(cite)

    return valid_citations


def extract_prior_opinions(text: str) -> list[str]:
    """Extract references to prior FPPC advice letters and opinions."""

    patterns = [
        # "A-24-006", "I-23-177", "A-00-033"
        r'\b([AIM]-\d{2}-\d{3})\b',

        # "Advice Letter No. 24006"
        r'[Aa]dvice\s+[Ll]etter\s+(?:No\.?\s*)?(\d{5})',

        # "In re Smith, A-22-001"
        r'In\s+re\s+\w+,?\s+([AIM]-\d{2}-\d{3})',

        # Older format: "Opinion No. 82-032"
        r'[Oo]pinion\s+(?:No\.?\s*)?(\d{2}-\d{3})',
    ]

    citations = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        citations.extend(matches)

    return citations


def extract_external_citations(text: str) -> list[str]:
    """Extract other legal citations (cases, etc.)."""

    citations = []

    # California case citations: "123 Cal.App.4th 456"
    case_pattern = r'\d+\s+Cal\.?\s*(?:App\.?\s*)?(?:2d|3d|4th|5th)?\s+\d+'
    cases = re.findall(case_pattern, text)
    citations.extend(cases)

    # Federal case citations
    federal_pattern = r'\d+\s+(?:U\.S\.|F\.2d|F\.3d|F\.Supp\.?)\s+\d+'
    federal = re.findall(federal_pattern, text)
    citations.extend(federal)

    return citations
```

---

## Quality Scoring

```python
# scraper/quality.py

"""
Compute quality scores for extracted text.
"""

import re


def compute_quality_score(text: str, page_count: int) -> float:
    """
    Compute a quality score from 0.0 to 1.0 for extracted text.

    Factors:
    - Word count per page (too low = OCR failure)
    - Alphabetic character ratio (too low = garbled)
    - Presence of expected patterns (dates, sections)
    - Absence of OCR artifacts
    """

    if not text or page_count == 0:
        return 0.0

    scores = []

    # 1. Words per page (expect 200-500 for legal docs)
    word_count = len(text.split())
    words_per_page = word_count / page_count

    if words_per_page >= 200:
        scores.append(1.0)
    elif words_per_page >= 100:
        scores.append(0.7)
    elif words_per_page >= 50:
        scores.append(0.4)
    else:
        scores.append(0.1)

    # 2. Alphabetic ratio
    alpha_chars = sum(1 for c in text if c.isalpha())
    alpha_ratio = alpha_chars / len(text) if text else 0

    if alpha_ratio >= 0.7:
        scores.append(1.0)
    elif alpha_ratio >= 0.5:
        scores.append(0.6)
    else:
        scores.append(0.2)

    # 3. Expected patterns present
    has_date = bool(re.search(
        r'(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        text
    ))
    has_fppc = bool(re.search(r'FPPC|Fair Political|Political Practices', text, re.I))
    has_section = bool(re.search(r'\b(QUESTION|CONCLUSION|ANALYSIS)\b', text, re.I))

    pattern_score = (int(has_date) + int(has_fppc) + int(has_section)) / 3
    scores.append(pattern_score)

    # 4. OCR artifact detection (penalty)
    # Look for garbled text indicators
    long_words = sum(1 for w in text.split() if len(w) > 25)
    garbage_ratio = long_words / word_count if word_count else 0

    if garbage_ratio < 0.01:
        scores.append(1.0)
    elif garbage_ratio < 0.05:
        scores.append(0.7)
    else:
        scores.append(0.3)

    # Weighted average
    weights = [0.3, 0.25, 0.25, 0.2]  # words, alpha, patterns, artifacts
    final_score = sum(s * w for s, w in zip(scores, weights))

    return round(final_score, 3)
```

---

## Database Updates

Add new columns to track extraction status:

```python
# Add to scraper/db.py

def add_extraction_columns():
    """Add extraction tracking columns to documents table."""
    conn = get_connection()
    cursor = conn.cursor()

    # These may already exist, so use IF NOT EXISTS logic
    new_columns = [
        ("extraction_status", "TEXT DEFAULT 'pending'"),
        ("extracted_at", "TEXT"),
        ("extraction_method", "TEXT"),
        ("extraction_quality", "REAL"),
        ("json_path", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
        except:
            pass  # Column already exists

    conn.commit()
    conn.close()


def get_pending_extractions(year: int = None) -> list:
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

    cursor.execute(sql)
    results = cursor.fetchall()
    conn.close()
    return results


def update_extraction_status(doc_id: int, status: str, method: str = None,
                            quality: float = None, json_path: str = None):
    """Update extraction status for a document."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE documents
        SET extraction_status = ?,
            extracted_at = datetime('now'),
            extraction_method = COALESCE(?, extraction_method),
            extraction_quality = COALESCE(?, extraction_quality),
            json_path = COALESCE(?, json_path)
        WHERE id = ?
    """, (status, method, quality, json_path, doc_id))

    conn.commit()
    conn.close()
```

---

## Heuristic Classification

Simple topic classification based on cited code sections:

```python
# scraper/classifier.py

"""
Heuristic topic classification based on citations.
"""

from dataclasses import dataclass


# Government Code section ranges by topic
CONFLICTS_OF_INTEREST_SECTIONS = {
    # Conflicts of Interest (87100-87500 range)
    range(87100, 87600),
    # Economic Interest Disclosure (87200-87210)
    range(87200, 87220),
}

CAMPAIGN_FINANCE_SECTIONS = {
    # Campaign Reporting (84100-84511)
    range(84100, 84600),
    # Contributions/Expenditures (85100-85704)
    range(85100, 85800),
}

LOBBYING_SECTIONS = {
    # Lobbyist Registration (86100-86300)
    range(86100, 86400),
}


def classify_by_citations(citations) -> dict:
    """
    Classify document topic based on Government Code citations.

    Returns classification dict with topic_primary, confidence, and method.
    """

    if not citations.government_code:
        return {
            "topic_primary": None,
            "confidence": None,
            "classification_method": "heuristic:no_citations",
        }

    coi_count = 0
    cf_count = 0
    lobby_count = 0

    for cite in citations.government_code:
        # Extract base section number
        try:
            base_num = int(cite.split('(')[0])
        except ValueError:
            continue

        for range_set in CONFLICTS_OF_INTEREST_SECTIONS:
            if base_num in range_set:
                coi_count += 1
                break

        for range_set in CAMPAIGN_FINANCE_SECTIONS:
            if base_num in range_set:
                cf_count += 1
                break

        for range_set in LOBBYING_SECTIONS:
            if base_num in range_set:
                lobby_count += 1
                break

    total = coi_count + cf_count + lobby_count

    if total == 0:
        return {
            "topic_primary": "other",
            "confidence": 0.5,
            "classification_method": "heuristic:unknown_sections",
        }

    # Determine winner
    if coi_count > cf_count and coi_count > lobby_count:
        topic = "conflicts_of_interest"
        confidence = coi_count / total
    elif cf_count > coi_count and cf_count > lobby_count:
        topic = "campaign_finance"
        confidence = cf_count / total
    elif lobby_count > coi_count and lobby_count > cf_count:
        topic = "lobbying"
        confidence = lobby_count / total
    else:
        # Tie - default to conflicts_of_interest (more common)
        topic = "conflicts_of_interest" if coi_count >= cf_count else "campaign_finance"
        confidence = 0.5

    return {
        "topic_primary": topic,
        "confidence": round(confidence, 2),
        "classification_method": "heuristic:citation_based",
    }
```

---

## Estimated Costs & Time

### Phase 3A: Core Extraction

| Component | Documents | Time | Cost |
|-----------|-----------|------|------|
| Native extraction (all) | 14,096 | ~2 hours | Free |
| olmOCR (pre-1995) | ~3,000 | ~8 hours | ~$2.50 |
| olmOCR (failures) | ~1,000 | ~3 hours | ~$1.00 |
| **Total** | 14,096 | ~13 hours | **~$3.50** |

### Phase 3B & 3C: Parsing & Citations

| Component | Documents | Time | Cost |
|-----------|-----------|------|------|
| Section parsing | 14,096 | ~10 min | Free |
| Citation extraction | 14,096 | ~10 min | Free |
| Quality scoring | 14,096 | ~5 min | Free |

---

## CLI Commands Summary

```bash
# Initialize extraction columns
python -m scraper.extractor --init

# Extract all pending documents
python -m scraper.extractor --extract-all

# Extract specific year
python -m scraper.extractor --extract-year 2024

# Extract single document (for testing)
python -m scraper.extractor --extract-one 12345

# Show statistics
python -m scraper.extractor --stats

# Dry run (show what would be done)
python -m scraper.extractor --extract-all --dry-run

# Force olmOCR on everything (expensive!)
python -m scraper.extractor --extract-year 1990 --force-olmocr

# Skip olmOCR entirely (native only, fast but lower quality for old docs)
python -m scraper.extractor --extract-all --skip-olmocr
```

---

## Testing Strategy

### Unit Tests

```python
# tests/test_section_parser.py

def test_modern_format():
    text = """
    QUESTION

    Whether the official may vote on the contract.

    CONCLUSION

    No, the official may not vote.

    FACTS

    The requestor is a city council member.

    ANALYSIS

    Government Code Section 87100 provides...
    """

    result = parse_sections(text)
    assert result.has_standard_format
    assert "vote on the contract" in result.question
    assert "may not vote" in result.conclusion


def test_old_format():
    text = """
    QUESTION PRESENTED

    May an official participate in a decision?

    SHORT ANSWER

    The official should not participate.
    """

    result = parse_sections(text)
    assert result.has_standard_format
    assert "participate" in result.question
```

### Integration Tests

```bash
# Test extraction on sample documents
python -m scraper.extractor --extract-one 1      # 1975 doc
python -m scraper.extractor --extract-one 5000   # 1990s doc
python -m scraper.extractor --extract-one 14000  # 2024 doc

# Verify output
cat data/extracted/2024/A-24-006.json | jq '.sections'
```

---

## Next Steps After Phase 3

### Phase 4: LLM Enrichment (Future)

For documents where:
- `sections.has_standard_format == false`
- `classification.topic_primary is null`
- We want granular topic tags

Run through Claude/GPT to:
1. Generate synthetic Q&A
2. Classify topics with high confidence
3. Generate searchable summaries
4. Extract granular topic tags

### Phase 5: Search Infrastructure (Future)

1. Load all JSON into search index (Meilisearch recommended)
2. Generate embeddings for `sections.question` + `sections.conclusion`
3. Generate chunk embeddings for `content.full_text`
4. Build faceted search with `classification.topic_primary` filter

---

## Dependencies

Add to `requirements.txt`:

```
# Existing
requests
pymupdf

# New for Phase 3
openai          # DeepInfra client (OpenAI-compatible)
python-dotenv   # Environment variable loading
pydantic>=2.0   # Schema validation (optional but recommended)
```

---

## Implementation Order

1. **Create `scraper/schema.py`** - Define all dataclasses
2. **Create `scraper/quality.py`** - Quality scoring
3. **Create `scraper/section_parser.py`** - Section extraction
4. **Create `scraper/citation_extractor.py`** - Citation extraction
5. **Create `scraper/classifier.py`** - Heuristic classification
6. **Update `scraper/db.py`** - Add extraction tracking columns
7. **Create `scraper/extractor.py`** - Main extraction logic + CLI
8. **Test on samples** - One doc from each era
9. **Run full extraction** - All 14,096 documents
