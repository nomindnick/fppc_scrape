#!/usr/bin/env python3
"""
Test script: Inspect the actual HTML structure of a search results page.

Goal: Understand how to parse results and verify result counts.
"""

import requests
import re
from pathlib import Path

HEADERS = {
    "User-Agent": "FPPC-Research-Bot/1.0 (academic research)"
}
TIMEOUT = 120

# Use the legacy endpoint (all seem equivalent)
URL = "https://fppc.ca.gov/advice/advice-opinion-search.html?SearchTerm=&tag1=na&tagCount=1"


def main():
    print("Fetching search results page...")
    response = requests.get(URL, headers=HEADERS, timeout=TIMEOUT)
    html = response.text

    # Save raw HTML for manual inspection
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    html_path = output_dir / "sample_search_page.html"
    html_path.write_text(html)
    print(f"Saved raw HTML to: {html_path}")

    print("\n" + "=" * 60)
    print("LOOKING FOR RESULT COUNT PATTERNS")
    print("=" * 60)

    # Look for various patterns that might indicate total results
    patterns = [
        (r'(\d{1,3}(?:,\d{3})*)\s*results?', "X results"),
        (r'of\s+(\d{1,3}(?:,\d{3})*)\s*results?', "of X results"),
        (r'total[:\s]+(\d{1,3}(?:,\d{3})*)', "total: X"),
        (r'showing.*?(\d{1,3}(?:,\d{3})*)', "showing X"),
        (r'Page\s+(\d+)\s+of\s+(\d+)', "Page X of Y"),
        (r'(\d{1,3}(?:,\d{3})*)\s*advice\s*letters?', "X advice letters"),
        (r'16,?\d{3}', "16xxx (looking for 16207)"),
    ]

    for pattern, description in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            print(f"\n{description}:")
            for m in matches[:5]:  # limit to first 5
                print(f"  {m}")

    print("\n" + "=" * 60)
    print("LOOKING FOR RESULT ITEM STRUCTURE")
    print("=" * 60)

    # Count PDF links
    pdf_links = re.findall(r'href="([^"]*\.pdf)"', html, re.IGNORECASE)
    print(f"\nPDF links found on this page: {len(pdf_links)}")
    if pdf_links:
        print("First 3 PDF links:")
        for link in pdf_links[:3]:
            print(f"  {link}")

    # Look for common result container patterns
    print("\n" + "=" * 60)
    print("EXAMINING PAGE STRUCTURE")
    print("=" * 60)

    # Look for "Filed under" which appears in results
    filed_under = re.findall(r'Filed under[:\s]*([^<]+)', html)
    print(f"\n'Filed under' occurrences: {len(filed_under)}")
    if filed_under:
        print("First 3:")
        for f in filed_under[:3]:
            print(f"  {f.strip()}")

    # Look for year mentions in results
    year_mentions = re.findall(r'20[12]\d', html)
    print(f"\nYear mentions (2010-2029): {len(year_mentions)}")

    # Look for result containers (common class names)
    for class_name in ['result', 'item', 'card', 'listing', 'entry']:
        count = len(re.findall(f'class="[^"]*{class_name}[^"]*"', html, re.IGNORECASE))
        if count:
            print(f"Elements with '{class_name}' in class: {count}")

    print("\n" + "=" * 60)
    print("SNIPPET AROUND 'results' TEXT")
    print("=" * 60)

    # Find context around "results" text
    for match in re.finditer(r'.{50}\d+\s*results?.{50}', html, re.IGNORECASE):
        print(f"\n{match.group()}")


if __name__ == "__main__":
    main()
