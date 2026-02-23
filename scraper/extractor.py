"""
Core text extraction pipeline for FPPC advice letters.

This module orchestrates the full extraction pipeline for processing PDFs into
structured JSON documents. It coordinates all Phase 3A components:

1. Native PDF extraction via PyMuPDF
2. olmOCR fallback via DeepInfra API for scanned/low-quality documents
3. Quality assessment and OCR decision logic
4. Section parsing (QUESTION, CONCLUSION, FACTS, ANALYSIS)
5. Citation extraction (Gov Code, regulations, prior opinions)
6. Topic classification based on citations
7. Embedding content preparation

Usage:
    # Extract a sample for review
    python -m scraper.extractor --extract-sample 50

    # Full extraction (with olmOCR fallback)
    python -m scraper.extractor --extract-all

    # Skip olmOCR (faster, cheaper)
    python -m scraper.extractor --extract-all --skip-olmocr

    # Show extraction statistics
    python -m scraper.extractor --stats
"""

import argparse
import base64
import hashlib
import io
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from .citation_extractor import extract_citations
from .classifier import classify_by_citations
from .config import DATA_DIR, RAW_PDFS_DIR
from .db import (
    add_extraction_columns,
    get_connection,
    get_pending_extractions,
    update_extraction_status,
)
from .quality import QualityMetrics, compute_quality_score, should_use_olmocr
from .schema import (
    Citations,
    Classification,
    Content,
    EmbeddingContent,
    ExtractionInfo,
    FPPCDocument,
    ParsedMetadata,
    Sections,
    SourceMetadata,
    to_json,
)
from .section_parser import clean_section_content, parse_sections

# =============================================================================
# Constants
# =============================================================================

EXTRACTED_DIR = DATA_DIR / "extracted"

# olmOCR via DeepInfra
DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY")
OLMOCR_MODEL = "allenai/olmOCR-2-7B-1025"
OLMOCR_MAX_PAGES = 20  # Limit pages per document to control cost
OLMOCR_COST_PER_MILLION_TOKENS = 0.86  # Approximate cost

# Month name regex — includes standard spellings + common OCR variants
_MONTH_NAMES = (
    r'(?:January|February|March|April|May|June|July|August|September|October|November|December'
    r'|Ianuary|Lanuary|Januarv|Februarv|Febniary|Iarch|Inarch|Aprii|Apnl|Mav'
    r'|Iune|Lune|Iuly|Luly|Idy|htly|Julv|Jidy|Juiy'
    r'|Augusl|Augusi|Septeinber|Septernber|Octoher'
    r'|Noveinber|Novernber|Deceinber|Decernber)'
)

# Date parsing patterns
DATE_PATTERNS = [
    # "January 23, 2024", "May26, 1998", "June 27,2002", "Iuly 23, L99L"
    (rf'({_MONTH_NAMES})\s*(\d{{1,2}})[,.\s]\s*(\d{{4}}|[Ll0-9O]{{4}})',
     lambda m: f"{_fix_ocr_year(m.group(3))}-{_month_to_num(m.group(1))}-{int(m.group(2)):02d}"),
    # "January 23, 2024" with comma separator (strict, to avoid false positives)
    (rf'({_MONTH_NAMES})\s*(\d{{1,2}}),?\s*(\d{{4}})',
     lambda m: f"{m.group(3)}-{_month_to_num(m.group(1))}-{int(m.group(2)):02d}"),
    # "01/23/2024" or "1/23/24"
    (r'(\d{1,2})/(\d{1,2})/(\d{2,4})',
     lambda m: f"{_expand_year(m.group(3))}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"),
]

MONTH_MAP = {
    'january': '01', 'february': '02', 'march': '03', 'april': '04',
    'may': '05', 'june': '06', 'july': '07', 'august': '08',
    'september': '09', 'october': '10', 'november': '11', 'december': '12',
}

# OCR misspellings of month names mapped to canonical forms
OCR_MONTH_MAP = {
    'iuly': 'july', 'idy': 'july', 'htly': 'july', 'julv': 'july',
    'luly': 'july', 'jidy': 'july', 'juiy': 'july',
    'iune': 'june', 'lune': 'june',
    'ianuary': 'january', 'lanuary': 'january', 'januarv': 'january',
    'februarv': 'february', 'febniary': 'february',
    'iarch': 'march', 'inarch': 'march',
    'aprii': 'april', 'apnl': 'april',
    'mav': 'may',
    'augusl': 'august', 'augusi': 'august',
    'septeinber': 'september', 'septernber': 'september',
    'octoher': 'october', 'octoher': 'october',
    'noveinber': 'november', 'novernber': 'november',
    'deceinber': 'december', 'decernber': 'december',
}


def _month_to_num(month: str) -> str:
    """Convert month name (including OCR variants) to two-digit number."""
    lower = month.lower()
    # Try direct match first
    if lower in MONTH_MAP:
        return MONTH_MAP[lower]
    # Try OCR correction
    canonical = OCR_MONTH_MAP.get(lower)
    if canonical:
        return MONTH_MAP[canonical]
    return '01'


def _fix_ocr_year(year_str: str) -> str:
    """Fix common OCR garbles in year strings: L→1, O→0, strip spaces/hyphens."""
    fixed = year_str.replace('L', '1').replace('l', '1').replace('O', '0').replace('o', '0')
    fixed = fixed.replace(' ', '').replace('-', '')
    return fixed


def _expand_year(year_str: str) -> str:
    """Expand 2-digit year to 4-digit."""
    if len(year_str) == 4:
        return year_str
    year = int(year_str)
    # 00-25 -> 2000-2025, 26-99 -> 1926-1999
    if year <= 25:
        return f"20{year:02d}"
    return f"19{year:02d}"


def _extract_letter_id_from_text(text: str) -> str | None:
    """
    Try to extract a letter ID from the document text header.

    Looks for "Our File No." / "File No." patterns in the first 3000 chars.

    Returns:
        Normalized letter ID like "A-22-078", or None if not found.
    """
    header = text[:3000]
    match = re.search(
        r'(?:Our\s+)?File\s+No\.?\s*([AIM]?)\s*-?\s*(\d{2})\s*-?\s*(\d{3,4})',
        header, re.IGNORECASE,
    )
    if match:
        prefix = match.group(1).upper() or "A"
        year_part = match.group(2)
        num_part = match.group(3)
        return f"{prefix}-{year_part}-{num_part}"
    return None


def _build_self_id_variants(letter_id: str) -> set[str]:
    """
    Build a set of variant forms for a letter ID to detect self-citations.

    E.g. "A-22-078" → {"A-22-078", "22-078", "22078", "A22078"}
         "90-753"   → {"90-753", "90753", "A-90-753", "I-90-753", ...}
         "84263"    → {"84263", "84-263", "A-84-263", "I-84-263", ...}
         "83A195"   → {"83A195", "A-83-195", ...}
    """
    variants = {letter_id.upper()}
    lid = letter_id.upper()

    # Case 1: Already has prefix — "A-22-078", "I-91-495"
    m = re.match(r'^([AIM])-(\d{2})-(\d{3,4})$', lid)
    if m:
        prefix, yy, nnn = m.group(1), m.group(2), m.group(3)
        bare = f"{yy}-{nnn}"
        variants.update({
            bare,                           # "22-078"
            bare.replace("-", ""),           # "22078"
            f"{prefix}{yy}{nnn}",           # "A22078"
            lid.replace("-", ""),            # "A22078" (same)
        })
        return variants

    # Case 2: "YY-NNN" bare format — "90-753", "07-164"
    m = re.match(r'^(\d{2})-(\d{3,4})$', lid)
    if m:
        yy, nnn = m.group(1), m.group(2)
        variants.update({
            f"{yy}{nnn}",                   # "90753"
            f"A-{yy}-{nnn}",               # "A-90-753"
            f"I-{yy}-{nnn}",               # "I-90-753"
            f"M-{yy}-{nnn}",               # "M-90-753"
        })
        return variants

    # Case 3: "YYNNN" compact format — "84263", "88460"
    m = re.match(r'^(\d{2})(\d{3,4})$', lid)
    if m:
        yy, nnn = m.group(1), m.group(2)
        variants.update({
            f"{yy}-{nnn}",                  # "84-263"
            f"A-{yy}-{nnn}",               # "A-84-263"
            f"I-{yy}-{nnn}",               # "I-84-263"
            f"M-{yy}-{nnn}",               # "M-84-263"
        })
        return variants

    # Case 4: "YYA###" old format — "83A195", "82A037"
    m = re.match(r'^(\d{2})([AIM])(\d{3,4})$', lid)
    if m:
        yy, prefix, nnn = m.group(1), m.group(2), m.group(3)
        variants.update({
            f"{prefix}-{yy}-{nnn}",         # "A-83-195"
            f"{yy}-{nnn}",                  # "83-195"
            f"{yy}{nnn}",                   # "83195"
        })
        return variants

    # Case 5: Complex IDs like "16-079-1090" or "78ADV-78-039"
    # Extract the core YY-NNN if possible, and add all prefix variants
    m = re.match(r'^(\d{2})-(\d{3,4})-', lid)
    if m:
        yy, nnn = m.group(1), m.group(2)
        variants.update({
            f"{yy}-{nnn}",                  # "16-079"
            f"A-{yy}-{nnn}",               # "A-16-079"
            f"I-{yy}-{nnn}",               # "I-16-079"
            f"M-{yy}-{nnn}",               # "M-16-079"
        })

    return variants


# =============================================================================
# Extractor Class
# =============================================================================


class Extractor:
    """
    Orchestrates the full text extraction pipeline.

    This class coordinates all Phase 3A components to process PDFs into
    structured JSON documents with sections, citations, and classifications.
    """

    def __init__(self, skip_olmocr: bool = False, force_olmocr: bool = False, verbose: bool = True):
        """
        Initialize the extractor.

        Args:
            skip_olmocr: If True, skip olmOCR even for poor quality extractions
            force_olmocr: If True, always attempt olmOCR regardless of quality heuristics
            verbose: If True, print progress information
        """
        self.skip_olmocr = skip_olmocr
        self.force_olmocr = force_olmocr
        self.verbose = verbose
        self._olmocr_client = None

        # Check for olmOCR availability
        if not skip_olmocr and DEEPINFRA_API_KEY:
            try:
                from openai import OpenAI
                self._olmocr_client = OpenAI(
                    api_key=DEEPINFRA_API_KEY,
                    base_url="https://api.deepinfra.com/v1/openai",
                )
                if self.verbose:
                    print("olmOCR enabled via DeepInfra API")
            except ImportError:
                if self.verbose:
                    print("Warning: openai package not installed, olmOCR disabled")
        elif not skip_olmocr and not DEEPINFRA_API_KEY:
            if self.verbose:
                print("Warning: DEEPINFRA_API_KEY not set, olmOCR disabled")

        # Statistics tracking
        self.stats = {
            "total": 0,
            "success": 0,
            "error": 0,
            "used_olmocr": 0,
            "total_olmocr_cost": 0.0,
            "flagged_for_llm": 0,
        }

    def _log(self, msg: str) -> None:
        """Print message if verbose mode is enabled."""
        if self.verbose:
            print(msg)

    def _get_pdf_path(self, doc_row: dict) -> Path | None:
        """
        Determine the local PDF path from document metadata.

        Args:
            doc_row: Database row as dict

        Returns:
            Path to the PDF file, or None if not found
        """
        year = doc_row.get("year_tag")
        pdf_url = doc_row.get("pdf_url", "")

        # Extract filename from URL
        filename = pdf_url.rstrip("/").split("/")[-1]
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        year_dir = RAW_PDFS_DIR / str(year)
        pdf_path = year_dir / filename

        if pdf_path.exists():
            return pdf_path

        # Case-insensitive fallback: scan directory for matching stem
        if year_dir.is_dir():
            target_stem = Path(filename).stem.lower()
            for candidate in year_dir.iterdir():
                if candidate.stem.lower() == target_stem and candidate.suffix.lower() == ".pdf":
                    return candidate

        return None

    def extract_native(self, pdf_path: Path) -> dict:
        """
        Extract text from PDF using PyMuPDF (native extraction).

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Dict with: text, page_count, word_count, char_count
        """
        text_parts = []
        page_count = 0

        with fitz.open(pdf_path) as doc:
            page_count = len(doc)
            for page in doc:
                text_parts.append(page.get_text())

        full_text = "\n\n".join(text_parts)
        word_count = len(full_text.split())
        char_count = len(full_text)

        return {
            "text": full_text,
            "page_count": page_count,
            "word_count": word_count,
            "char_count": char_count,
        }

    def extract_olmocr(self, pdf_path: Path, max_pages: int = OLMOCR_MAX_PAGES) -> dict | None:
        """
        Extract text from PDF using olmOCR via DeepInfra API.

        Converts each page to a 150 DPI PNG image, sends to olmOCR model,
        and concatenates the results.

        Args:
            pdf_path: Path to the PDF file
            max_pages: Maximum number of pages to process

        Returns:
            Dict with: text, markdown, cost (in USD), or None if failed
        """
        if not self._olmocr_client:
            return None

        try:
            all_text = []
            all_markdown = []
            total_tokens = 0

            with fitz.open(pdf_path) as doc:
                pages_to_process = min(len(doc), max_pages)

                for page_num in range(pages_to_process):
                    page = doc[page_num]

                    # Render page to PNG at 150 DPI
                    mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI
                    pix = page.get_pixmap(matrix=mat)
                    img_bytes = pix.tobytes("png")

                    # Base64 encode
                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                    # Send to olmOCR
                    response = self._olmocr_client.chat.completions.create(
                        model=OLMOCR_MODEL,
                        max_tokens=4096,
                        messages=[{
                            "role": "user",
                            "content": [{
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}"
                                }
                            }]
                        }]
                    )

                    page_text = response.choices[0].message.content or ""
                    all_text.append(page_text)
                    all_markdown.append(page_text)

                    # Track token usage
                    if hasattr(response, 'usage') and response.usage:
                        total_tokens += response.usage.total_tokens

                    # Small delay between pages to avoid rate limits
                    if page_num < pages_to_process - 1:
                        time.sleep(0.5)

            # Estimate cost
            cost = (total_tokens / 1_000_000) * OLMOCR_COST_PER_MILLION_TOKENS

            return {
                "text": "\n\n".join(all_text),
                "markdown": "\n\n".join(all_markdown),
                "cost": cost,
            }

        except Exception as e:
            self._log(f"  olmOCR error: {e}")
            return None

    def _parse_date_from_text(self, text: str) -> tuple[str | None, str | None]:
        """
        Extract date from document text.

        Args:
            text: Document text

        Returns:
            Tuple of (iso_date, raw_date_string) or (None, None)
        """
        # Look in first 2000 chars (header area)
        header = text[:2000]

        for pattern, formatter in DATE_PATTERNS:
            match = re.search(pattern, header, re.IGNORECASE)
            if match:
                try:
                    iso_date = formatter(match)
                    # Validate year is in reasonable range (1975-2026)
                    year_part = int(iso_date[:4])
                    if 1975 <= year_part <= 2026:
                        return iso_date, match.group(0)
                except (ValueError, IndexError):
                    continue

        return None, None

    def _parse_requestor_from_text(self, text: str) -> tuple[str | None, str | None, str | None]:
        """
        Extract requestor information from document text.

        Args:
            text: Document text

        Returns:
            Tuple of (name, title, city) - any may be None
        """
        # Look for "Dear Mr./Ms./Dr. NAME" patterns
        dear_pattern = r'Dear\s+(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
        match = re.search(dear_pattern, text[:3000])
        name = match.group(1) if match else None

        # Title patterns: "City Attorney", "County Counsel", etc.
        title_patterns = [
            r'(?:City|County|District)\s+(?:Attorney|Counsel)',
            r'(?:General|Chief)\s+Counsel',
            r'(?:Assistant|Deputy)\s+(?:City|County)\s+(?:Attorney|Manager)',
        ]
        title = None
        for pattern in title_patterns:
            if re.search(pattern, text[:3000], re.IGNORECASE):
                match = re.search(pattern, text[:3000], re.IGNORECASE)
                if match:
                    title = match.group(0)
                    break

        return name, title, None  # City is typically in DB metadata

    def _build_embedding_content(
        self,
        sections_result,
        full_text: str,
    ) -> tuple[str, str, str]:
        """
        Build content optimized for embedding generation.

        Note: In Phase 3A, we only have extracted content. Synthetic content
        (question_synthetic, conclusion_synthetic) will be added in Phase 3B
        for documents that need LLM extraction.

        Args:
            sections_result: SectionResult from section_parser
            full_text: Full document text

        Returns:
            Tuple of (qa_text, qa_source, first_500_words)
        """
        # First 500 words for fallback
        words = full_text.split()[:500]
        first_500 = " ".join(words)

        # Build QA text from extracted sections (apply boilerplate cleaning)
        parts = []
        qa_source = "extracted"

        if sections_result.question:
            parts.append(f"QUESTION: {clean_section_content(sections_result.question)}")

        if sections_result.conclusion:
            parts.append(f"CONCLUSION: {clean_section_content(sections_result.conclusion)}")

        # If no Q/C sections found, use first 500 words and mark as needing synthesis
        if not parts:
            qa_text = first_500
            qa_source = "extracted"  # Will become "synthetic" in Phase 3B
        else:
            qa_text = "\n\n".join(parts)

        return qa_text, qa_source, first_500

    def _determine_document_type(self, letter_id: str | None, text: str) -> str:
        """Determine document type from letter ID prefix or content."""
        upper_text = text.upper()

        # Check for withdrawal/decline letters first (overrides prefix-based detection)
        withdrawal_patterns = [
            r'WITHDRAW(?:N|AL|ING)\s+(?:YOUR|THE|THIS)\s+REQUEST',
            r'DECLINE\s+TO\s+(?:ISSUE|PROVIDE)',
            r'REQUEST\s+(?:HAS\s+BEEN|IS)\s+WITHDRAW',
            r'WITHDRAWAL\s+OF\s+(?:YOUR\s+)?REQUEST',
        ]
        for pattern in withdrawal_patterns:
            if re.search(pattern, upper_text[:5000]):
                return "correspondence"

        if letter_id:
            if letter_id.startswith("A-"):
                return "advice_letter"
            elif letter_id.startswith("I-"):
                return "informal_advice"
            elif letter_id.startswith("M-"):
                return "opinion"

        # Fallback to content analysis
        if "INFORMAL ASSISTANCE" in upper_text:
            return "informal_advice"
        if "FORMAL OPINION" in upper_text:
            return "opinion"

        return "advice_letter"  # Default

    def process_document(self, doc_row: dict) -> FPPCDocument | None:
        """
        Process a single document through the full extraction pipeline.

        Args:
            doc_row: Database row dict with document metadata

        Returns:
            FPPCDocument if successful, None if failed
        """
        doc_id = doc_row.get("id")
        letter_id = doc_row.get("letter_id") or None  # Treat empty string as None
        year = doc_row.get("year_tag", 0)

        self._log(f"Processing {letter_id or f'doc#{doc_id}'} ({year})...")

        # Step 1: Find PDF
        pdf_path = self._get_pdf_path(doc_row)
        if not pdf_path:
            self._log(f"  PDF not found for {letter_id}")
            return None

        # Step 2: Native extraction
        try:
            native_result = self.extract_native(pdf_path)
        except Exception as e:
            self._log(f"  Native extraction failed: {e}")
            return None

        text = native_result["text"]
        page_count = native_result["page_count"]
        native_word_count = native_result["word_count"]

        # Step 2b: Recover letter_id from text if missing
        if not letter_id and text:
            letter_id = _extract_letter_id_from_text(text)
            if letter_id:
                self._log(f"  Recovered letter_id from text: {letter_id}")

        # Final fallback: synthetic ID from year + DB primary key
        if not letter_id:
            letter_id = f"UNK-{year % 100:02d}-{doc_id:05d}"
            self._log(f"  Using synthetic ID: {letter_id}")

        # Step 3: Quality assessment
        metrics = compute_quality_score(text, page_count)

        # Step 4: olmOCR fallback if needed
        extraction_method = "native"
        olmocr_cost = None
        markdown_text = None

        if not self.skip_olmocr and (self.force_olmocr or should_use_olmocr(year, metrics)):
            self._log(f"  Quality score {metrics.final_score:.2f} - trying olmOCR...")
            olmocr_result = self.extract_olmocr(pdf_path)

            if olmocr_result:
                # Compare quality
                olmocr_metrics = compute_quality_score(
                    olmocr_result["text"],
                    page_count
                )

                if olmocr_metrics.final_score > metrics.final_score:
                    self._log(f"  olmOCR improved quality: {metrics.final_score:.2f} -> {olmocr_metrics.final_score:.2f}")
                    text = olmocr_result["text"]
                    markdown_text = olmocr_result["markdown"]
                    metrics = olmocr_metrics
                    extraction_method = "olmocr"
                else:
                    extraction_method = "native+olmocr"  # Tried but kept native

                olmocr_cost = olmocr_result["cost"]
                self.stats["used_olmocr"] += 1
                self.stats["total_olmocr_cost"] += olmocr_cost

        # Step 5: Parse sections
        sections_result = parse_sections(text, year)

        # Step 6: Extract citations
        citations_result = extract_citations(text)

        # Step 6b: Filter self-citations from prior opinions
        if letter_id:
            self_variants = _build_self_id_variants(letter_id)
            citations_result.prior_opinions = [
                op for op in citations_result.prior_opinions
                if op.upper() not in self_variants
            ]

        # Step 7: Classify topic
        classification_result = classify_by_citations(citations_result.government_code)

        # Step 8: Parse metadata from text
        date_iso, date_raw = self._parse_date_from_text(text)
        requestor_name, requestor_title, _ = self._parse_requestor_from_text(text)

        # Use DB metadata if text parsing failed
        if not date_raw and doc_row.get("letter_date"):
            date_raw = doc_row["letter_date"]
            # Try to parse DB date
            for pattern, formatter in DATE_PATTERNS:
                match = re.search(pattern, date_raw, re.IGNORECASE)
                if match:
                    try:
                        date_iso = formatter(match)
                        break
                    except (ValueError, IndexError):
                        continue

        if not requestor_name:
            requestor_name = doc_row.get("requestor_name")

        # Step 9: Build embedding content
        qa_text, qa_source, first_500 = self._build_embedding_content(sections_result, text)

        # Step 10: Determine if LLM extraction needed
        needs_llm = (
            sections_result.extraction_confidence < 0.5 or
            not sections_result.has_standard_format
        )
        if needs_llm:
            self.stats["flagged_for_llm"] += 1

        # Build the document
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        doc = FPPCDocument(
            id=letter_id,
            year=year,
            pdf_url=f"https://fppc.ca.gov{doc_row.get('pdf_url', '')}",
            pdf_sha256=doc_row.get("pdf_sha256", ""),
            local_pdf_path=str(pdf_path.relative_to(pdf_path.parent.parent.parent)),
            source_metadata=SourceMetadata(
                title_raw=doc_row.get("title_text", ""),
                tags=doc_row.get("tags", "").split(", ") if doc_row.get("tags") else [],
                scraped_at=doc_row.get("scraped_at", ""),
                source_page_url=doc_row.get("source_page_url"),
            ),
            extraction=ExtractionInfo(
                method=extraction_method,
                extracted_at=now,
                page_count=page_count,
                word_count=len(text.split()),
                char_count=len(text),
                quality_score=metrics.final_score,
                olmocr_cost=olmocr_cost,
                native_word_count=native_word_count,
            ),
            content=Content(
                full_text=text,
                full_text_markdown=markdown_text,
            ),
            parsed=ParsedMetadata(
                date=date_iso,
                date_raw=date_raw,
                requestor_name=requestor_name,
                requestor_title=requestor_title,
                requestor_city=doc_row.get("city"),
                document_type=self._determine_document_type(letter_id, text),
            ),
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
                classified_at=now,
                classification_method=classification_result.method,
            ),
            embedding=EmbeddingContent(
                qa_text=qa_text,
                qa_source=qa_source,
                first_500_words=first_500,
                summary=None,  # Populated in Phase 3B
            ),
        )

        return doc

    def save_document(self, doc: FPPCDocument) -> Path:
        """
        Save extracted document to JSON file.

        Args:
            doc: FPPCDocument to save

        Returns:
            Path to the saved JSON file
        """
        # Create year directory
        year_dir = EXTRACTED_DIR / str(doc.year)
        year_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize document ID for filesystem use
        safe_id = re.sub(r'[^\w\-.]', '_', doc.id) if doc.id else f"unknown_{doc.year}"
        json_path = year_dir / f"{safe_id}.json"
        json_content = to_json(doc)

        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_content)

        return json_path

    def extract_all(
        self,
        year: int | None = None,
        limit: int | None = None,
    ) -> dict:
        """
        Process all pending documents.

        Args:
            year: Optional year filter
            limit: Optional limit on documents to process

        Returns:
            Statistics dict
        """
        # Reset stats
        self.stats = {
            "total": 0,
            "success": 0,
            "error": 0,
            "used_olmocr": 0,
            "total_olmocr_cost": 0.0,
            "flagged_for_llm": 0,
        }

        # Get pending documents
        pending = get_pending_extractions(year=year, limit=limit)
        self._log(f"Found {len(pending)} documents to process")

        for i, doc_row in enumerate(pending, 1):
            self.stats["total"] += 1
            doc_id = doc_row["id"]

            try:
                # Process document
                doc = self.process_document(doc_row)

                if doc:
                    # Save JSON
                    json_path = self.save_document(doc)

                    # Update database
                    update_extraction_status(
                        doc_id=doc_id,
                        status="extracted",
                        method=doc.extraction.method,
                        quality=doc.extraction.quality_score,
                        section_confidence=doc.sections.extraction_confidence,
                        json_path=str(json_path.relative_to(DATA_DIR.parent)),
                        needs_llm=doc.sections.extraction_confidence < 0.5 or not doc.sections.has_standard_format,
                    )
                    self.stats["success"] += 1
                    self._log(f"  Saved: {json_path.name}")
                else:
                    update_extraction_status(doc_id=doc_id, status="error")
                    self.stats["error"] += 1

            except Exception as e:
                self._log(f"  Error processing {doc_row.get('letter_id', doc_id)}: {e}")
                update_extraction_status(doc_id=doc_id, status="error")
                self.stats["error"] += 1

            # Progress
            if i % 10 == 0:
                self._log(f"Progress: {i}/{len(pending)} ({self.stats['success']} success, {self.stats['error']} errors)")

        return self.stats

    def extract_sample(self, n: int = 50) -> dict:
        """
        Extract a sample of documents across eras for review.

        Selects documents proportionally from different era groups:
        - Modern (2010+)
        - 2000s
        - 1990s
        - Pre-1990

        Args:
            n: Total number of documents to sample

        Returns:
            Statistics dict
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Sample from each era
        eras = [
            (2010, 2026, int(n * 0.3)),  # 30% modern
            (2000, 2009, int(n * 0.25)),  # 25% 2000s
            (1990, 1999, int(n * 0.25)),  # 25% 1990s
            (1975, 1989, int(n * 0.2)),   # 20% pre-1990
        ]

        all_docs = []
        for start_year, end_year, sample_count in eras:
            cursor.execute("""
                SELECT * FROM documents
                WHERE download_status = 'downloaded'
                AND (extraction_status = 'pending' OR extraction_status IS NULL)
                AND year_tag >= ? AND year_tag <= ?
                ORDER BY RANDOM()
                LIMIT ?
            """, (start_year, end_year, sample_count))
            all_docs.extend([dict(row) for row in cursor.fetchall()])

        conn.close()

        self._log(f"Sampling {len(all_docs)} documents across eras")

        # Reset stats
        self.stats = {
            "total": 0,
            "success": 0,
            "error": 0,
            "used_olmocr": 0,
            "total_olmocr_cost": 0.0,
            "flagged_for_llm": 0,
        }

        for doc_row in all_docs:
            self.stats["total"] += 1
            doc_id = doc_row["id"]

            try:
                doc = self.process_document(doc_row)

                if doc:
                    json_path = self.save_document(doc)
                    update_extraction_status(
                        doc_id=doc_id,
                        status="extracted",
                        method=doc.extraction.method,
                        quality=doc.extraction.quality_score,
                        section_confidence=doc.sections.extraction_confidence,
                        json_path=str(json_path.relative_to(DATA_DIR.parent)),
                        needs_llm=doc.sections.extraction_confidence < 0.5 or not doc.sections.has_standard_format,
                    )
                    self.stats["success"] += 1
                else:
                    update_extraction_status(doc_id=doc_id, status="error")
                    self.stats["error"] += 1

            except Exception as e:
                self._log(f"  Error: {e}")
                update_extraction_status(doc_id=doc_id, status="error")
                self.stats["error"] += 1

        return self.stats


def get_extraction_stats() -> dict:
    """
    Get extraction statistics from the database.

    Returns:
        Dict with extraction counts and breakdowns
    """
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Overall counts
    cursor.execute("SELECT COUNT(*) FROM documents WHERE download_status = 'downloaded'")
    stats["total_downloaded"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM documents WHERE extraction_status = 'extracted'")
    stats["extracted"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM documents WHERE extraction_status = 'error'")
    stats["errors"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM documents
        WHERE download_status = 'downloaded'
        AND (extraction_status = 'pending' OR extraction_status IS NULL)
    """)
    stats["pending"] = cursor.fetchone()[0]

    # By method
    cursor.execute("""
        SELECT extraction_method, COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY extraction_method
    """)
    stats["by_method"] = {row["extraction_method"]: row["count"] for row in cursor.fetchall()}

    # Quality distribution
    cursor.execute("""
        SELECT
            CASE
                WHEN extraction_quality >= 0.8 THEN 'high (0.8+)'
                WHEN extraction_quality >= 0.5 THEN 'medium (0.5-0.8)'
                ELSE 'low (<0.5)'
            END as quality_band,
            COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY quality_band
    """)
    stats["by_quality"] = {row["quality_band"]: row["count"] for row in cursor.fetchall()}

    # Needing LLM extraction
    cursor.execute("SELECT COUNT(*) FROM documents WHERE needs_llm_extraction = 1")
    stats["needs_llm"] = cursor.fetchone()[0]

    # By year (extracted)
    cursor.execute("""
        SELECT year_tag, COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY year_tag
        ORDER BY year_tag DESC
        LIMIT 10
    """)
    stats["recent_years"] = {row["year_tag"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return stats


def print_stats() -> None:
    """Print extraction statistics."""
    stats = get_extraction_stats()

    print("\n" + "=" * 60)
    print("EXTRACTION STATISTICS")
    print("=" * 60)

    print(f"\nOverall Progress:")
    print(f"  Downloaded:  {stats['total_downloaded']:,}")
    print(f"  Extracted:   {stats['extracted']:,}")
    print(f"  Pending:     {stats['pending']:,}")
    print(f"  Errors:      {stats['errors']:,}")

    if stats.get("by_method"):
        print(f"\nBy Extraction Method:")
        for method, count in stats["by_method"].items():
            print(f"  {method or 'unknown'}: {count:,}")

    if stats.get("by_quality"):
        print(f"\nBy Quality Score:")
        for band, count in sorted(stats["by_quality"].items()):
            print(f"  {band}: {count:,}")

    print(f"\nPhase 3B (LLM extraction needed): {stats.get('needs_llm', 0):,}")

    if stats.get("recent_years"):
        print(f"\nRecent Years (extracted):")
        for year, count in list(stats["recent_years"].items())[:5]:
            print(f"  {year}: {count:,}")

    print()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Extract text and structure from FPPC PDFs"
    )
    parser.add_argument(
        "--extract-all",
        action="store_true",
        help="Extract all pending documents",
    )
    parser.add_argument(
        "--extract-sample",
        type=int,
        metavar="N",
        help="Extract sample of N documents across eras",
    )
    parser.add_argument(
        "--skip-olmocr",
        action="store_true",
        help="Skip olmOCR fallback (faster, cheaper)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Filter by year",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of documents to process",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show extraction statistics",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize extraction columns in database",
    )

    args = parser.parse_args()

    # Initialize if requested
    if args.init:
        add_extraction_columns()
        print("Extraction columns initialized")
        return

    # Show stats
    if args.stats:
        print_stats()
        return

    # Must specify an action
    if not args.extract_all and not args.extract_sample:
        parser.print_help()
        return

    # Create extractor
    extractor = Extractor(skip_olmocr=args.skip_olmocr)

    # Run extraction
    if args.extract_sample:
        stats = extractor.extract_sample(n=args.extract_sample)
    else:
        stats = extractor.extract_all(year=args.year, limit=args.limit)

    # Print summary
    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"Total processed: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Errors: {stats['error']}")
    print(f"Used olmOCR: {stats['used_olmocr']}")
    print(f"olmOCR cost: ${stats['total_olmocr_cost']:.4f}")
    print(f"Flagged for LLM: {stats['flagged_for_llm']}")


if __name__ == "__main__":
    main()
