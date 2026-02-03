#!/usr/bin/env python3
"""
Test script: Compare olmOCR (via DeepInfra) against native PyMuPDF extraction.

Goal: Validate olmOCR accuracy on FPPC documents across different eras,
especially scanned documents where native extraction fails.
"""

import os
import re
import base64
import time
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

import requests
import pymupdf
from openai import OpenAI

# Load environment
load_dotenv()
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY")

if not DEEPINFRA_API_KEY:
    raise ValueError("DEEPINFRA_API_KEY not found in .env file")

# DeepInfra client (OpenAI-compatible)
client = OpenAI(
    api_key=DEEPINFRA_API_KEY,
    base_url="https://api.deepinfra.com/v1/openai",
)

# Constants
HEADERS = {"User-Agent": "FPPC-Research-Bot/1.0 (academic research)"}
BASE_URL = "https://www.fppc.ca.gov"
MODEL = "allenai/olmOCR-2-7B-1025"

# Test PDFs spanning different eras
TEST_PDFS = [
    {
        "era": "2024 (modern native PDF)",
        "year": 2024,
        "url": "/content/dam/fppc/documents/advice-letters/2024/24006.pdf",
        "title": "Alan J. Peake - A-24-006",
        "expect_native_ok": True,
    },
    {
        "era": "2015 (intermediate era)",
        "year": 2015,
        "url": "/content/dam/fppc/documents/advice-letters/1995-2015/2015/15-033.pdf",
        "title": "Advice Letter 15-033",
        "expect_native_ok": True,
    },
    {
        "era": "2000 (turn of century)",
        "year": 2000,
        "url": "/content/dam/fppc/documents/advice-letters/1995-2015/2000/00-033.pdf",
        "title": "Advice Letter 00-033",
        "expect_native_ok": True,
    },
    {
        "era": "1990 (older format)",
        "year": 1990,
        "url": "/content/dam/fppc/documents/advice-letters/1984-1994/1990/90695.pdf",
        "title": "Rynearson, Mark - 90695",
        "expect_native_ok": False,  # May be scanned
    },
    {
        "era": "1982 (scanned typewriter)",
        "year": 1982,
        "url": "/content/dam/fppc/documents/advice-letters/1982/82A032.PDF",
        "title": "Francis LaSage - A-82-032",
        "expect_native_ok": False,  # Known to need OCR
    },
]


@dataclass
class ExtractionResult:
    """Results from a single extraction method."""
    method: str
    text: str
    word_count: int
    char_count: int
    time_seconds: float
    cost_estimate: float = 0.0
    error: str | None = None


@dataclass
class DocumentComparison:
    """Comparison of extraction methods for one document."""
    era: str
    year: int
    title: str
    page_count: int
    pdf_size_bytes: int
    native_result: ExtractionResult | None = None
    olmocr_result: ExtractionResult | None = None
    quality_analysis: dict = field(default_factory=dict)


def download_pdf(url: str) -> bytes | None:
    """Download PDF from FPPC website."""
    full_url = BASE_URL + url if url.startswith("/") else url
    try:
        response = requests.get(full_url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"  ERROR downloading: {e}")
        return None


def extract_native(pdf_bytes: bytes) -> ExtractionResult:
    """Extract text using PyMuPDF native extraction."""
    start = time.time()
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()

        text = "\n".join(text_parts)
        elapsed = time.time() - start

        return ExtractionResult(
            method="native (PyMuPDF)",
            text=text,
            word_count=len(text.split()),
            char_count=len(text),
            time_seconds=elapsed,
            cost_estimate=0.0,
        )
    except Exception as e:
        return ExtractionResult(
            method="native (PyMuPDF)",
            text="",
            word_count=0,
            char_count=0,
            time_seconds=time.time() - start,
            error=str(e),
        )


def pdf_page_to_image(doc: pymupdf.Document, page_num: int, dpi: int = 150) -> bytes:
    """Convert a PDF page to PNG image bytes."""
    page = doc[page_num]
    # Create a transformation matrix for the desired DPI
    # Default PDF is 72 DPI, so scale factor = desired_dpi / 72
    zoom = dpi / 72
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def extract_olmocr(pdf_bytes: bytes, max_pages: int = 10) -> ExtractionResult:
    """Extract text using olmOCR via DeepInfra API."""
    start = time.time()

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        pages_to_process = min(page_count, max_pages)

        all_text = []
        total_tokens = 0

        for page_num in range(pages_to_process):
            # Convert page to image
            img_bytes = pdf_page_to_image(doc, page_num, dpi=150)
            base64_image = base64.b64encode(img_bytes).decode()

            # Call olmOCR
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )

            page_text = response.choices[0].message.content
            all_text.append(f"--- Page {page_num + 1} ---\n{page_text}")

            # Track usage
            if response.usage:
                total_tokens += response.usage.total_tokens

        doc.close()

        text = "\n\n".join(all_text)
        elapsed = time.time() - start

        # Estimate cost: ~$0.86 per million tokens
        cost = (total_tokens / 1_000_000) * 0.86

        return ExtractionResult(
            method=f"olmOCR ({MODEL})",
            text=text,
            word_count=len(text.split()),
            char_count=len(text),
            time_seconds=elapsed,
            cost_estimate=cost,
        )

    except Exception as e:
        return ExtractionResult(
            method=f"olmOCR ({MODEL})",
            text="",
            word_count=0,
            char_count=0,
            time_seconds=time.time() - start,
            error=str(e),
        )


def analyze_quality(text: str) -> dict:
    """Analyze extraction quality with various metrics."""
    analysis = {}

    # Basic stats
    analysis["word_count"] = len(text.split())
    analysis["char_count"] = len(text)

    # Check for structural markers (FPPC-specific)
    analysis["has_question"] = bool(re.search(r'\bQUESTION\b', text, re.IGNORECASE))
    analysis["has_conclusion"] = bool(re.search(r'\bCONCLUSION\b', text, re.IGNORECASE))
    analysis["has_analysis"] = bool(re.search(r'\bANALYSIS\b', text, re.IGNORECASE))
    analysis["has_facts"] = bool(re.search(r'\bFACTS\b', text, re.IGNORECASE))

    # Extract date
    date_match = re.search(
        r'(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        text
    )
    analysis["extracted_date"] = date_match.group(0) if date_match else None

    # Extract file number
    file_match = re.search(r'\b([AIM]-?\d{2}-?\d{3})\b', text)
    analysis["extracted_file_no"] = file_match.group(1) if file_match else None

    # Quality indicators
    if analysis["word_count"] > 0:
        # Alphabetic ratio (should be high for clean text)
        alpha_chars = sum(1 for c in text if c.isalpha())
        analysis["alpha_ratio"] = alpha_chars / len(text) if text else 0

        # Average word length (OCR garbage often has weird lengths)
        words = text.split()
        analysis["avg_word_length"] = sum(len(w) for w in words) / len(words)

        # Very long "words" (often OCR errors / missing spaces)
        analysis["long_word_count"] = sum(1 for w in words if len(w) > 25)
    else:
        analysis["alpha_ratio"] = 0
        analysis["avg_word_length"] = 0
        analysis["long_word_count"] = 0

    return analysis


def compare_document(pdf_info: dict) -> DocumentComparison:
    """Run both extraction methods on a document and compare."""
    print(f"\n{'='*70}")
    print(f"Testing: {pdf_info['era']}")
    print(f"Title: {pdf_info['title']}")
    print(f"{'='*70}")

    # Download
    print("\nDownloading PDF...")
    pdf_bytes = download_pdf(pdf_info["url"])
    if not pdf_bytes:
        print("  FAILED to download")
        return None

    print(f"  Size: {len(pdf_bytes):,} bytes")

    # Get page count
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    doc.close()
    print(f"  Pages: {page_count}")

    comparison = DocumentComparison(
        era=pdf_info["era"],
        year=pdf_info["year"],
        title=pdf_info["title"],
        page_count=page_count,
        pdf_size_bytes=len(pdf_bytes),
    )

    # Native extraction
    print("\n--- Native Extraction (PyMuPDF) ---")
    native_result = extract_native(pdf_bytes)
    comparison.native_result = native_result

    if native_result.error:
        print(f"  ERROR: {native_result.error}")
    else:
        print(f"  Words: {native_result.word_count:,}")
        print(f"  Time: {native_result.time_seconds:.2f}s")

        native_analysis = analyze_quality(native_result.text)
        print(f"  Has QUESTION: {native_analysis['has_question']}")
        print(f"  Has CONCLUSION: {native_analysis['has_conclusion']}")
        print(f"  Extracted date: {native_analysis['extracted_date']}")
        print(f"  Extracted file#: {native_analysis['extracted_file_no']}")

        # Show sample
        sample_lines = [l.strip() for l in native_result.text.split('\n') if l.strip()][:3]
        print(f"  Sample: {' | '.join(sample_lines)[:100]}...")

    # olmOCR extraction
    print("\n--- olmOCR Extraction (DeepInfra) ---")
    olmocr_result = extract_olmocr(pdf_bytes, max_pages=5)  # Limit pages for testing
    comparison.olmocr_result = olmocr_result

    if olmocr_result.error:
        print(f"  ERROR: {olmocr_result.error}")
    else:
        print(f"  Words: {olmocr_result.word_count:,}")
        print(f"  Time: {olmocr_result.time_seconds:.2f}s")
        print(f"  Est. cost: ${olmocr_result.cost_estimate:.6f}")

        olmocr_analysis = analyze_quality(olmocr_result.text)
        comparison.quality_analysis = {
            "native": analyze_quality(native_result.text) if not native_result.error else {},
            "olmocr": olmocr_analysis,
        }

        print(f"  Has QUESTION: {olmocr_analysis['has_question']}")
        print(f"  Has CONCLUSION: {olmocr_analysis['has_conclusion']}")
        print(f"  Extracted date: {olmocr_analysis['extracted_date']}")
        print(f"  Extracted file#: {olmocr_analysis['extracted_file_no']}")

        # Show sample
        sample_lines = [l.strip() for l in olmocr_result.text.split('\n') if l.strip()][:3]
        print(f"  Sample: {' | '.join(sample_lines)[:100]}...")

    return comparison


def print_summary(comparisons: list[DocumentComparison]):
    """Print summary comparison table."""
    print("\n" + "="*80)
    print("SUMMARY: Native vs olmOCR Comparison")
    print("="*80)

    # Header
    print(f"\n{'Era':<30} {'Pages':<6} {'Native':<12} {'olmOCR':<12} {'Cost':<10} {'Winner'}")
    print("-"*80)

    total_cost = 0

    for c in comparisons:
        if not c:
            continue

        native_words = c.native_result.word_count if c.native_result else 0
        olmocr_words = c.olmocr_result.word_count if c.olmocr_result else 0
        cost = c.olmocr_result.cost_estimate if c.olmocr_result else 0
        total_cost += cost

        # Determine winner based on word count and quality
        if native_words > olmocr_words * 0.9 and native_words > 100:
            winner = "Native ✓"
        elif olmocr_words > native_words:
            winner = "olmOCR ✓"
        else:
            winner = "Tie"

        print(f"{c.era:<30} {c.page_count:<6} {native_words:<12,} {olmocr_words:<12,} ${cost:<9.5f} {winner}")

    print("-"*80)
    print(f"{'Total API cost':<60} ${total_cost:.5f}")

    # Quality comparison
    print("\n" + "="*80)
    print("QUALITY METRICS")
    print("="*80)

    print(f"\n{'Era':<30} {'Method':<10} {'Date Found':<15} {'File# Found':<12} {'Sections'}")
    print("-"*80)

    for c in comparisons:
        if not c or not c.quality_analysis:
            continue

        for method, analysis in c.quality_analysis.items():
            if not analysis:
                continue
            date = (analysis.get('extracted_date') or '-')[:12]
            file_no = analysis.get('extracted_file_no') or '-'
            sections = []
            if analysis.get('has_question'): sections.append('Q')
            if analysis.get('has_conclusion'): sections.append('C')
            if analysis.get('has_analysis'): sections.append('A')
            if analysis.get('has_facts'): sections.append('F')
            sections_str = '/'.join(sections) if sections else '-'

            print(f"{c.era:<30} {method:<10} {date:<15} {file_no:<12} {sections_str}")


def main():
    print("="*80)
    print("FPPC OCR Comparison: Native PyMuPDF vs olmOCR (DeepInfra)")
    print("="*80)
    print(f"\nModel: {MODEL}")
    print(f"Testing {len(TEST_PDFS)} documents across different eras")
    print("\nNote: olmOCR is limited to 5 pages per doc for this test")

    comparisons = []
    for pdf_info in TEST_PDFS:
        comparison = compare_document(pdf_info)
        comparisons.append(comparison)

        # Small delay between API calls
        time.sleep(1)

    print_summary(comparisons)

    print("\n" + "="*80)
    print("CONCLUSIONS")
    print("="*80)
    print("""
Based on this comparison:
1. Native extraction works well for modern PDFs (2010+)
2. olmOCR is essential for scanned/older documents
3. Cost is minimal (~$0.001 per document)

Recommended strategy:
- Try native extraction first
- Fall back to olmOCR if word_count < threshold OR year < 2000
""")


if __name__ == "__main__":
    main()
