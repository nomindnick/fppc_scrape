"""HTML parsing functions for FPPC search results."""

import re
from dataclasses import dataclass


@dataclass
class SearchResult:
    """A single search result from the FPPC site."""

    title: str
    pdf_url: str
    tags: str


def parse_results(html: str) -> list[SearchResult]:
    """
    Extract search results from HTML.

    Returns a list of SearchResult objects.
    """
    results = []

    # Find all hit divs using regex
    hits = re.findall(
        r'<div class="hit">\s*<a href="([^"]+)">([^<]+)</a>.*?'
        r'<div class="hit-tags">Filed under:\s*([^<]*)</div>',
        html,
        re.DOTALL,
    )

    for pdf_url, title, tags in hits:
        results.append(
            SearchResult(
                title=title.strip(),
                pdf_url=pdf_url.strip(),
                tags=tags.strip(),
            )
        )

    return results


def get_result_count(html: str) -> int | None:
    """Extract total result count from HTML."""
    match = re.search(r"(\d{1,6})\s*results?", html, re.IGNORECASE)
    return int(match.group(1)) if match else None


def get_page_count(html: str) -> int | None:
    """Extract total page count from HTML."""
    match = re.search(r"Page\s+\d+\s+of\s+(\d+)", html, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_title_metadata(title: str, year: int | None = None) -> dict:
    """
    Parse metadata from the title text.

    Different eras have different title formats:
    - Modern (2020+): "Name - A-24-006 - January 23, 2024 - City"
    - 1995-2019: "Year: 2015 Advice Letter # 15001"
    - 1984-1994: "Name, Description Year: 1990 Advice Letter # 90024"

    Returns a dict with keys: requestor_name, letter_id, letter_date, city
    """
    result = {
        "requestor_name": None,
        "letter_id": None,
        "letter_date": None,
        "city": None,
    }

    # Try modern format first (2020+)
    # Pattern: "Name - A-24-006 - January 23, 2024 - City"
    modern_match = re.match(
        r"^(.+?)\s*-\s*([AI]-\d{2}-\d{3})\s*-\s*(.+?)\s*-\s*(.+)$",
        title,
    )
    if modern_match:
        result["requestor_name"] = modern_match.group(1).strip()
        result["letter_id"] = modern_match.group(2).strip()
        result["letter_date"] = modern_match.group(3).strip()
        result["city"] = modern_match.group(4).strip()
        return result

    # Try 1984-1994 format: "Name, Description Year: YYYY Advice Letter # NNNNN"
    old_with_name = re.match(
        r"^(.+?),\s*(.+?)\s+Year:\s*(\d{4})\s*Advice Letter\s*#\s*(\S+)",
        title,
        re.IGNORECASE,
    )
    if old_with_name:
        result["requestor_name"] = old_with_name.group(1).strip()
        result["letter_id"] = old_with_name.group(4).strip()
        return result

    # Try 1995-2019 format: "Year: YYYY Advice Letter # NNNNN"
    year_only = re.match(
        r"Year:\s*(\d{4})\s*Advice Letter\s*#\s*(\S+)",
        title,
        re.IGNORECASE,
    )
    if year_only:
        result["letter_id"] = year_only.group(2).strip()
        return result

    # Very old format might just have letter number
    letter_num_only = re.search(r"Advice Letter\s*#?\s*(\S+)", title, re.IGNORECASE)
    if letter_num_only:
        result["letter_id"] = letter_num_only.group(1).strip()
        return result

    return result


def extract_year_from_tags(tags: str) -> int | None:
    """Extract year from the "Filed under" tags."""
    # Tags look like "Advice Letter, 2024" or similar
    match = re.search(r"\b(19\d{2}|20\d{2})\b", tags)
    return int(match.group(1)) if match else None


def extract_year_from_url(pdf_url: str) -> int | None:
    """Extract year from PDF URL patterns."""
    # Try various patterns
    # 2016+: /advice-letters/2024/24006.pdf
    # 1995-2015: /advice-letters/1995-2015/2000/00-123.pdf
    # 1984-1994: /advice-letters/1984-1994/1990/90001.pdf

    match = re.search(r"/advice-letters/(?:\d{4}-\d{4}/)?(\d{4})/", pdf_url)
    if match:
        return int(match.group(1))

    return None
