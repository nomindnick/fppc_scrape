#!/usr/bin/env python3
"""
Test script: OCR a scanned PDF using Tesseract.

Goal: Verify we can extract text from image-based PDFs.
"""

import requests
import pymupdf
import subprocess
import tempfile
from pathlib import Path

HEADERS = {"User-Agent": "FPPC-Research-Bot/1.0"}
BASE_URL = "https://www.fppc.ca.gov"

# The 1982 PDF that needs OCR
PDF_URL = "/content/dam/fppc/documents/advice-letters/1982/82A032.PDF"


def ocr_image(image_bytes: bytes, ext: str = "png") -> str:
    """Run Tesseract OCR on an image."""
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(image_bytes)
        temp_path = f.name

    try:
        result = subprocess.run(
            ["tesseract", temp_path, "stdout", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout
    finally:
        Path(temp_path).unlink(missing_ok=True)


def main():
    print("OCR Test: 82A032.PDF (1982 scanned document)")
    print("=" * 60)

    # Download
    response = requests.get(BASE_URL + PDF_URL, headers=HEADERS, timeout=60)
    pdf_bytes = response.content
    print(f"Downloaded: {len(pdf_bytes):,} bytes")

    # Open with PyMuPDF
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    print(f"Pages: {len(doc)}")

    full_text = []

    for page_num, page in enumerate(doc):
        print(f"\nProcessing Page {page_num + 1}...")

        # Get images from page
        images = page.get_images()
        if not images:
            print("  No images found, skipping")
            continue

        # Extract first (main) image
        xref = images[0][0]
        img_info = doc.extract_image(xref)
        img_bytes = img_info["image"]
        ext = img_info["ext"]

        print(f"  Image: {img_info['width']}x{img_info['height']} {ext}")
        print("  Running Tesseract OCR...")

        ocr_text = ocr_image(img_bytes, ext)
        word_count = len(ocr_text.split())
        print(f"  Extracted: {len(ocr_text)} chars, {word_count} words")

        full_text.append(ocr_text)

        # Show sample
        lines = [l for l in ocr_text.split('\n') if l.strip()][:8]
        print("  Sample lines:")
        for line in lines:
            print(f"    {line[:70]}")

    doc.close()

    # Combine and show full text quality
    combined = "\n\n".join(full_text)
    print("\n" + "=" * 60)
    print("FULL DOCUMENT OCR RESULT")
    print("=" * 60)
    print(f"Total: {len(combined)} chars, {len(combined.split())} words")

    # Try to find key fields
    import re

    date_match = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        combined
    )
    file_match = re.search(r'[Ff]ile\s*[Nn]o\.?\s*:?\s*([A-Z]?-?\d{2}-?\d{3})', combined)

    print(f"\nExtracted date: {date_match.group(0) if date_match else 'Not found'}")
    print(f"Extracted file no: {file_match.group(1) if file_match else 'Not found'}")

    # Check for QUESTION/CONCLUSION
    has_question = bool(re.search(r'\bQUESTION\b', combined, re.IGNORECASE))
    has_conclusion = bool(re.search(r'\bCONCLUSION\b', combined, re.IGNORECASE))
    print(f"Has QUESTION section: {has_question}")
    print(f"Has CONCLUSION section: {has_conclusion}")


if __name__ == "__main__":
    main()
