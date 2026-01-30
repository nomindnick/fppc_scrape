#!/usr/bin/env python3
"""
Test script: Compare response times of different FPPC search endpoints.

Goal: Determine which endpoint is fastest/most reliable for scraping.

Endpoints to test:
1. Legacy: /advice/advice-opinion-search.html
2. Law advice: /the-law/opinions-and-advice-letters/law-advice-search.html
3. Transparency: /transparency/form-700-filed-by-public-officials/advice-letter-search.html
"""

import requests
import time
from dataclasses import dataclass

# Be polite
HEADERS = {
    "User-Agent": "FPPC-Research-Bot/1.0 (academic research; contact: your-email@example.com)"
}
TIMEOUT = 120  # seconds - these pages can be slow


@dataclass
class EndpointResult:
    name: str
    url: str
    status_code: int | None
    response_time: float | None
    result_count: str | None
    error: str | None


def test_endpoint(name: str, url: str) -> EndpointResult:
    """Test a single endpoint and return timing + basic info."""
    print(f"\nTesting: {name}")
    print(f"URL: {url}")

    start = time.time()
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        elapsed = time.time() - start

        # Try to find result count in page (look for common patterns)
        result_count = None
        text = response.text

        # Pattern: "X results" or "Page X of Y"
        import re

        # Look for total results
        match = re.search(r'(\d{1,3}(?:,\d{3})*)\s*results?', text, re.IGNORECASE)
        if match:
            result_count = match.group(1)

        # Look for "Page X of Y" pattern
        page_match = re.search(r'Page\s+\d+\s+of\s+(\d{1,3}(?:,\d{3})*)', text, re.IGNORECASE)
        if page_match:
            pages = page_match.group(1)
            result_count = f"{result_count or '?'} (pages: {pages})"

        print(f"  Status: {response.status_code}")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Results found: {result_count or 'unknown'}")
        print(f"  Content length: {len(response.text):,} chars")

        return EndpointResult(
            name=name,
            url=url,
            status_code=response.status_code,
            response_time=elapsed,
            result_count=result_count,
            error=None
        )

    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        print(f"  TIMEOUT after {elapsed:.2f}s")
        return EndpointResult(
            name=name, url=url, status_code=None,
            response_time=elapsed, result_count=None,
            error="Timeout"
        )
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ERROR: {e}")
        return EndpointResult(
            name=name, url=url, status_code=None,
            response_time=elapsed, result_count=None,
            error=str(e)
        )


def main():
    print("=" * 60)
    print("FPPC Endpoint Speed Test")
    print("=" * 60)

    # Define endpoints to test (all with empty search to get full results)
    endpoints = [
        (
            "Legacy Search",
            "https://fppc.ca.gov/advice/advice-opinion-search.html?SearchTerm=&tag1=na&tagCount=1"
        ),
        (
            "Law Advice Search",
            "https://www.fppc.ca.gov/the-law/opinions-and-advice-letters/law-advice-search.html?SearchTerm=&tag1=na&tagCount=1"
        ),
        (
            "Transparency Portal",
            "https://fppc.ca.gov/transparency/form-700-filed-by-public-officials/advice-letter-search.html?SearchTerm=&tag1=na&tagCount=1"
        ),
    ]

    results = []

    for name, url in endpoints:
        result = test_endpoint(name, url)
        results.append(result)
        # Be polite between requests
        print("  (waiting 3s before next request...)")
        time.sleep(3)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Endpoint':<25} {'Time (s)':<12} {'Status':<10} {'Results'}")
    print("-" * 60)

    for r in results:
        time_str = f"{r.response_time:.2f}" if r.response_time else "N/A"
        status_str = str(r.status_code) if r.status_code else r.error or "N/A"
        results_str = r.result_count or "unknown"
        print(f"{r.name:<25} {time_str:<12} {status_str:<10} {results_str}")

    # Recommendation
    print("\n" + "-" * 60)
    successful = [r for r in results if r.status_code == 200 and r.response_time]
    if successful:
        fastest = min(successful, key=lambda r: r.response_time)
        print(f"Fastest successful endpoint: {fastest.name} ({fastest.response_time:.2f}s)")
    else:
        print("No successful responses!")

    print("\nNext steps:")
    print("- Update notes/LEARNINGS.md with these results")
    print("- If endpoints are similar, pick one and test pagination + year filter")


if __name__ == "__main__":
    main()
