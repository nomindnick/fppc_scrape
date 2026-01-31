"""Main crawling logic for FPPC advice letters."""

import argparse
import json
import time
from datetime import datetime

import requests

from .config import (
    BASE_URL,
    CHECKPOINT_PATH,
    DELAY_SECONDS,
    END_YEAR,
    HEADERS,
    MAX_RETRIES,
    PAGE_PARAM,
    RETRY_BACKOFF,
    START_YEAR,
    TIMEOUT,
    YEAR_FILTER,
)
from .db import get_stats, get_year_count, init_db, insert_document
from .parser import (
    extract_year_from_tags,
    extract_year_from_url,
    get_page_count,
    get_result_count,
    parse_results,
    parse_title_metadata,
)


def fetch_page(url: str) -> str | None:
    """
    Fetch a page with retry logic.

    Returns HTML content or None if all retries fail.
    """
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            wait_time = RETRY_BACKOFF ** attempt
            print(f"  Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

    return None


def build_year_url(year: int, page: int = 1) -> str:
    """Build URL for a specific year and page."""
    url = BASE_URL + YEAR_FILTER.format(year=year)
    if page > 1:
        url += PAGE_PARAM.format(page=page)
    return url


def load_checkpoint() -> tuple[int | None, int]:
    """
    Load checkpoint from file.

    Returns (last_completed_year, last_completed_page).
    Returns (None, 0) if no checkpoint exists.
    """
    if not CHECKPOINT_PATH.exists():
        return None, 0

    try:
        with open(CHECKPOINT_PATH) as f:
            data = json.load(f)
            return data.get("last_completed_year"), data.get("last_completed_page", 0)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Could not load checkpoint: {e}")
        return None, 0


def save_checkpoint(year: int, page: int) -> None:
    """Save checkpoint to file."""
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_completed_year": year,
        "last_completed_page": page,
        "timestamp": datetime.now().isoformat(),
    }
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(data, f, indent=2)


def clear_checkpoint() -> None:
    """Remove checkpoint file."""
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint cleared.")


def crawl_year(year: int, start_page: int = 1) -> int:
    """
    Crawl all pages for a given year.

    Returns total documents found.
    """
    documents_found = 0
    documents_inserted = 0
    page = start_page

    # Fetch first page to get total count
    url = build_year_url(year, page)
    print(f"\nCrawling year {year}, page {page}...")
    print(f"  URL: {url}")

    html = fetch_page(url)
    if html is None:
        print(f"  ERROR: Failed to fetch page 1 for year {year}")
        return 0

    total_results = get_result_count(html)
    total_pages = get_page_count(html)

    if total_results is None or total_results == 0:
        print(f"  No results for year {year}")
        return 0

    print(f"  Total results: {total_results}, Pages: {total_pages}")

    # Process pages
    while True:
        if page > start_page:
            # Fetch the page (we already have page 1 if start_page == 1)
            url = build_year_url(year, page)
            print(f"\nCrawling year {year}, page {page}/{total_pages}...")

            html = fetch_page(url)
            if html is None:
                print(f"  ERROR: Failed to fetch page {page}, skipping...")
                page += 1
                if page > (total_pages or 1):
                    break
                time.sleep(DELAY_SECONDS)
                continue

        # Parse results
        results = parse_results(html)
        if not results:
            print(f"  No results found on page {page}")
            if page >= (total_pages or 1):
                break
            page += 1
            time.sleep(DELAY_SECONDS)
            continue

        print(f"  Found {len(results)} results on page")
        documents_found += len(results)

        # Process each result
        for result in results:
            # Determine year from tags or URL
            result_year = extract_year_from_tags(result.tags)
            if result_year is None:
                result_year = extract_year_from_url(result.pdf_url)
            if result_year is None:
                result_year = year  # Fall back to filter year

            # Parse title metadata
            title_meta = parse_title_metadata(result.title, result_year)

            # Build document record
            doc = {
                "pdf_url": result.pdf_url,
                "title_text": result.title,
                "year_tag": result_year,
                "tags": result.tags,
                "source_page_url": url,
                "requestor_name": title_meta["requestor_name"],
                "letter_id": title_meta["letter_id"],
                "letter_date": title_meta["letter_date"],
                "city": title_meta["city"],
            }

            # Insert into database
            if insert_document(doc):
                documents_inserted += 1

        # Save checkpoint after each page
        save_checkpoint(year, page)

        # Check if we're done
        if page >= (total_pages or 1):
            break

        page += 1
        print(f"  Sleeping {DELAY_SECONDS}s...")
        time.sleep(DELAY_SECONDS)

    print(f"\nYear {year} complete: {documents_found} found, {documents_inserted} new")
    return documents_found


def crawl_all(start_year: int | None = None, start_page: int = 1) -> None:
    """
    Main entry point - crawl all years with checkpoint support.

    If start_year is None, attempts to resume from checkpoint.
    """
    # Load checkpoint if not specified
    if start_year is None:
        checkpoint_year, checkpoint_page = load_checkpoint()
        if checkpoint_year is not None:
            # Resume from next year after completed
            start_year = checkpoint_year + 1
            start_page = 1
            print(f"Resuming from year {start_year} (checkpoint: year {checkpoint_year} complete)")
        else:
            start_year = START_YEAR
            start_page = 1

    total_documents = 0
    start_time = datetime.now()

    print(f"Starting crawl from year {start_year} to {END_YEAR}")
    print(f"Start time: {start_time.isoformat()}")
    print("=" * 60)

    for year in range(start_year, END_YEAR + 1):
        year_start = datetime.now()

        # Use start_page only for first year if resuming mid-year
        page_start = start_page if year == start_year else 1

        docs = crawl_year(year, page_start)
        total_documents += docs

        # Clear start_page after first year
        start_page = 1

        year_elapsed = datetime.now() - year_start
        print(f"Year {year} took {year_elapsed}")

        # Small delay between years
        if year < END_YEAR:
            time.sleep(DELAY_SECONDS)

    elapsed = datetime.now() - start_time
    print("\n" + "=" * 60)
    print(f"Crawl complete!")
    print(f"Total documents: {total_documents}")
    print(f"Total time: {elapsed}")


def print_stats() -> None:
    """Print database statistics."""
    stats = get_stats()

    print("\n" + "=" * 60)
    print("FPPC Document Registry Statistics")
    print("=" * 60)

    print(f"\nTotal documents: {stats['total']}")

    print("\nBy download status:")
    for status, count in stats["by_download_status"].items():
        print(f"  {status}: {count}")

    print("\nBy extraction status:")
    for status, count in stats["by_extraction_status"].items():
        print(f"  {status}: {count}")

    print("\nBy year:")
    for year, count in sorted(stats["by_year"].items(), reverse=True):
        print(f"  {year}: {count}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="FPPC Advice Letter Crawler - Build document registry"
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize the database (creates tables)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Crawl a specific year only",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Crawl all years (resumes from checkpoint if available)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics",
    )
    parser.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Clear the checkpoint file",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        help="Override start year for --all (ignores checkpoint)",
    )

    args = parser.parse_args()

    if args.init:
        init_db()
        return

    if args.clear_checkpoint:
        clear_checkpoint()
        return

    if args.stats:
        print_stats()
        return

    if args.year:
        init_db()  # Ensure DB exists
        crawl_year(args.year)
        return

    if args.all:
        init_db()  # Ensure DB exists
        crawl_all(start_year=args.start_year)
        return

    # No arguments - show help
    parser.print_help()


if __name__ == "__main__":
    main()
