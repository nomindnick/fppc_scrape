#!/usr/bin/env python3
"""
Test script: Check if problematic PDFs are image-based (need OCR).

Goal: Identify which PDFs have text layers vs scanned images.
"""

import requests
import pymupdf

HEADERS = {"User-Agent": "FPPC-Research-Bot/1.0"}
BASE_URL = "https://www.fppc.ca.gov"

# The 1982 PDF that failed extraction
PDF_URL = "/content/dam/fppc/documents/advice-letters/1982/82A032.PDF"


def main():
    print("Checking PDF structure for: 82A032.PDF")
    print("=" * 60)

    # Download
    response = requests.get(BASE_URL + PDF_URL, headers=HEADERS, timeout=60)
    pdf_bytes = response.content
    print(f"Downloaded: {len(pdf_bytes):,} bytes")

    # Open with PyMuPDF
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    print(f"Pages: {len(doc)}")

    for page_num, page in enumerate(doc):
        print(f"\nPage {page_num + 1}:")

        # Get text
        text = page.get_text()
        print(f"  Text extracted: {len(text)} chars")
        if text.strip():
            print(f"  Text content: {repr(text[:200])}")

        # Get images
        images = page.get_images()
        print(f"  Images found: {len(images)}")

        for img_index, img in enumerate(images):
            xref = img[0]
            img_info = doc.extract_image(xref)
            print(f"    Image {img_index + 1}: {img_info['width']}x{img_info['height']}, {img_info['ext']}, {len(img_info['image']):,} bytes")

        # Get text blocks (more detail)
        blocks = page.get_text("blocks")
        print(f"  Text blocks: {len(blocks)}")

    doc.close()

    print("\n" + "=" * 60)
    print("CONCLUSION:")
    print("If images are found but no/little text, the PDF is scanned and needs OCR.")


if __name__ == "__main__":
    main()
