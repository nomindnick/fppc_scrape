#!/usr/bin/env python3
"""
Test script: Test year filtering and examine recent vs old letter formats.

Goal: Confirm year filter URL pattern and compare metadata quality by era.
"""

import requests
import re
from dataclasses import dataclass
import time

HEADERS = {
    "User-Agent": "FPPC-Research-Bot/1.0 (academic research)"
}
TIMEOUT = 120
BASE_URL = "https://fppc.ca.gov/advice/advice-opinion-search.html"


@dataclass
class SearchResult:
    title: str
    pdf_url: str
    tags: str


def parse_results(html: str) -> list[SearchResult]:
    """Extract search results from HTML."""
    results = []

    # Find all hit divs
    hits = re.findall(
        r'<div class="hit">\s*<a href="([^"]+)">([^<]+)</a>.*?'
        r'<div class="hit-tags">Filed under:\s*([^<]*)</div>',
        html,
        re.DOTALL
    )

    for pdf_url, title, tags in hits:
        results.append(SearchResult(
            title=title.strip(),
            pdf_url=pdf_url.strip(),
            tags=tags.strip()
        ))

    return results


def get_result_count(html: str) -> tuple[int | None, int | None]:
    """Extract total results and page count."""
    total_match = re.search(r'(\d{1,6})\s*results?', html, re.IGNORECASE)
    page_match = re.search(r'Page\s+\d+\s+of\s+(\d+)', html, re.IGNORECASE)

    total = int(total_match.group(1)) if total_match else None
    pages = int(page_match.group(1)) if page_match else None

    return total, pages


def test_year(year: int):
    """Test fetching results for a specific year."""
    # Try the suspected year filter format
    year_tag = f"/etc/tags/fppc/year/{year}"
    url = f"{BASE_URL}?SearchTerm=&tag1={year_tag}&tagCount=1"

    print(f"\n{'=' * 60}")
    print(f"Testing year: {year}")
    print(f"URL: {url}")

    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    html = response.text

    total, pages = get_result_count(html)
    results = parse_results(html)

    print(f"Total results: {total}")
    print(f"Pages: {pages}")
    print(f"Results on this page: {len(results)}")

    if results:
        print(f"\nSample results:")
        for r in results[:3]:
            print(f"  Title: {r.title}")
            print(f"  PDF: {r.pdf_url}")
            print(f"  Tags: {r.tags}")
            print()

    return results


def main():
    print("FPPC Year Filter Test")
    print("=" * 60)

    # Test a few different years to see metadata quality differences
    test_years = [2024, 2015, 2000, 1990, 1982]

    for year in test_years:
        test_year(year)
        print("(waiting 3s...)")
        time.sleep(3)

    print("\n" + "=" * 60)
    print("CONCLUSIONS")
    print("=" * 60)
    print("- Check if year filter worked (results should be year-specific)")
    print("- Compare metadata richness across years")
    print("- Note the PDF URL patterns for each era")


if __name__ == "__main__":
    main()
