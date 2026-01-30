#!/usr/bin/env python3
"""
Test script: Download and extract text from PDFs across different eras.

Goal: Assess text extraction quality and identify OCR issues.
"""

import requests
import pymupdf
import tempfile
import re
from dataclasses import dataclass
from pathlib import Path

HEADERS = {
    "User-Agent": "FPPC-Research-Bot/1.0 (academic research)"
}
TIMEOUT = 60
BASE_URL = "https://www.fppc.ca.gov"


@dataclass
class PDFResult:
    era: str
    year: int
    url: str
    title: str
    page_count: int
    char_count: int
    word_count: int
    sample_text: str
    has_question_section: bool
    has_conclusion_section: bool
    extracted_date: str | None
    extracted_file_no: str | None
    quality_notes: list[str]


# Sample PDFs from different eras (from our test results)
TEST_PDFS = [
    {
        "era": "2024 (modern)",
        "year": 2024,
        "url": "/content/dam/fppc/documents/advice-letters/2024/24006.pdf",
        "title": "Alan J. Peake - A-24-006 - January 23, 2024 - Bakersfield",
    },
    {
        "era": "2015 (sparse metadata era)",
        "year": 2015,
        "url": "/content/dam/fppc/documents/advice-letters/1995-2015/2015/15-033.pdf",
        "title": "Year: 2015 Advice Letter # 15-033",
    },
    {
        "era": "2000 (sparse metadata era)",
        "year": 2000,
        "url": "/content/dam/fppc/documents/advice-letters/1995-2015/2000/00-033.pdf",
        "title": "Year: 2000 Advice Letter # 00-033",
    },
    {
        "era": "1990 (partial metadata)",
        "year": 1990,
        "url": "/content/dam/fppc/documents/advice-letters/1984-1994/1990/90695.pdf",
        "title": "Rynearson, Mark Year: 1990 Advice Letter # 90695",
    },
    {
        "era": "1982 (old but rich metadata)",
        "year": 1982,
        "url": "/content/dam/fppc/documents/advice-letters/1982/82A032.PDF",
        "title": "Mr. Francis LaSage - A-82-032 - February 22, 1982 - Escalon",
    },
]


def download_pdf(url: str) -> bytes | None:
    """Download PDF and return bytes."""
    full_url = BASE_URL + url if url.startswith("/") else url
    print(f"  Downloading: {full_url}")

    try:
        response = requests.get(full_url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"  ERROR downloading: {e}")
        return None


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Extract text from PDF bytes. Returns (text, page_count)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []

    for page in doc:
        text_parts.append(page.get_text())

    doc.close()
    return "\n".join(text_parts), len(text_parts)


def analyze_text(text: str, era: str, year: int) -> dict:
    """Analyze extracted text for quality indicators."""
    quality_notes = []

    # Basic stats
    char_count = len(text)
    word_count = len(text.split())

    # Check for common sections
    has_question = bool(re.search(r'\bQUESTION\b', text, re.IGNORECASE))
    has_conclusion = bool(re.search(r'\bCONCLUSION\b', text, re.IGNORECASE))

    # Try to extract date
    date_match = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        text
    )
    extracted_date = date_match.group(0) if date_match else None

    # Try to extract file number
    file_no_match = re.search(r'(?:Our\s+)?File\s+No\.?\s*:?\s*([A-Z]?-?\d{2}-?\d{3})', text, re.IGNORECASE)
    if not file_no_match:
        file_no_match = re.search(r'\b([AIM]-\d{2}-\d{3})\b', text)
    extracted_file_no = file_no_match.group(1) if file_no_match else None

    # Quality checks
    if char_count < 500:
        quality_notes.append("Very short document")
    if word_count < 100:
        quality_notes.append("Very few words extracted")

    # Check for OCR garbage (lots of non-ASCII, weird characters)
    non_ascii = len([c for c in text if ord(c) > 127])
    if non_ascii > char_count * 0.05:
        quality_notes.append(f"High non-ASCII ratio ({non_ascii}/{char_count})")

    # Check for common OCR errors
    if re.search(r'[Il1]{3,}', text):  # Confused I/l/1
        quality_notes.append("Possible OCR confusion (I/l/1)")
    if re.search(r'[O0]{3,}', text):  # Confused O/0
        quality_notes.append("Possible OCR confusion (O/0)")

    # Check for missing spaces (OCR issue)
    long_words = [w for w in text.split() if len(w) > 30]
    if long_words:
        quality_notes.append(f"Possible missing spaces ({len(long_words)} very long 'words')")

    if not quality_notes:
        quality_notes.append("Text extraction looks clean")

    return {
        "char_count": char_count,
        "word_count": word_count,
        "has_question": has_question,
        "has_conclusion": has_conclusion,
        "extracted_date": extracted_date,
        "extracted_file_no": extracted_file_no,
        "quality_notes": quality_notes,
    }


def get_sample_text(text: str, max_chars: int = 500) -> str:
    """Get a clean sample of the text."""
    # Try to get the first meaningful paragraph
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    sample = ""
    for line in lines:
        if len(line) > 20:  # Skip very short lines
            sample += line + " "
            if len(sample) > max_chars:
                break
    return sample[:max_chars].strip() + "..." if len(sample) > max_chars else sample.strip()


def test_pdf(pdf_info: dict) -> PDFResult | None:
    """Test extraction for a single PDF."""
    print(f"\n{'=' * 60}")
    print(f"Era: {pdf_info['era']}")
    print(f"Title: {pdf_info['title']}")

    pdf_bytes = download_pdf(pdf_info["url"])
    if not pdf_bytes:
        return None

    print(f"  Downloaded: {len(pdf_bytes):,} bytes")

    text, page_count = extract_text(pdf_bytes)
    print(f"  Pages: {page_count}")
    print(f"  Extracted: {len(text):,} chars, {len(text.split()):,} words")

    analysis = analyze_text(text, pdf_info["era"], pdf_info["year"])

    print(f"  Has QUESTION section: {analysis['has_question']}")
    print(f"  Has CONCLUSION section: {analysis['has_conclusion']}")
    print(f"  Extracted date: {analysis['extracted_date']}")
    print(f"  Extracted file no: {analysis['extracted_file_no']}")
    print(f"  Quality notes: {', '.join(analysis['quality_notes'])}")

    sample = get_sample_text(text)
    print(f"\n  Sample text:\n  {'-' * 50}")
    # Indent sample for readability
    for line in sample.split('\n')[:5]:
        print(f"    {line[:80]}")

    return PDFResult(
        era=pdf_info["era"],
        year=pdf_info["year"],
        url=pdf_info["url"],
        title=pdf_info["title"],
        page_count=page_count,
        char_count=analysis["char_count"],
        word_count=analysis["word_count"],
        sample_text=sample,
        has_question_section=analysis["has_question"],
        has_conclusion_section=analysis["has_conclusion"],
        extracted_date=analysis["extracted_date"],
        extracted_file_no=analysis["extracted_file_no"],
        quality_notes=analysis["quality_notes"],
    )


def main():
    print("FPPC PDF Extraction Test")
    print("=" * 60)

    results = []
    for pdf_info in TEST_PDFS:
        result = test_pdf(pdf_info)
        if result:
            results.append(result)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(f"\n{'Era':<30} {'Pages':<6} {'Words':<8} {'Q?':<4} {'C?':<4} {'Date Found':<15} {'File# Found'}")
    print("-" * 90)
    for r in results:
        q = "Yes" if r.has_question_section else "No"
        c = "Yes" if r.has_conclusion_section else "No"
        date = r.extracted_date[:15] if r.extracted_date else "No"
        file_no = r.extracted_file_no or "No"
        print(f"{r.era:<30} {r.page_count:<6} {r.word_count:<8} {q:<4} {c:<4} {date:<15} {file_no}")

    print("\nQuality Notes by Era:")
    for r in results:
        print(f"  {r.era}: {', '.join(r.quality_notes)}")

    print("\n" + "=" * 60)
    print("CONCLUSIONS")
    print("=" * 60)
    print("- Check if text extraction is usable across all eras")
    print("- Note which eras have structured sections (QUESTION/CONCLUSION)")
    print("- Identify any OCR problems in older documents")


if __name__ == "__main__":
    main()
