"""PDF download module for FPPC advice letters."""

import hashlib
import time
from datetime import datetime
from pathlib import Path

import requests

from .config import (
    DOWNLOAD_DELAY,
    HEADERS,
    MAX_RETRIES,
    RAW_PDFS_DIR,
    RETRY_BACKOFF,
    TIMEOUT,
)
from .db import get_download_stats, get_pending_downloads, update_download_status

# Base domain for constructing full URLs from relative paths
FPPC_DOMAIN = "https://fppc.ca.gov"


def download_pdf(url: str, dest_path: Path) -> tuple[int, str] | None:
    """
    Download a PDF with retries.

    Streams the response to compute SHA256 while writing.

    Args:
        url: URL of the PDF to download
        dest_path: Local path to save the PDF

    Returns:
        Tuple of (size_bytes, sha256) on success, None on failure
    """
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            response.raise_for_status()

            # Ensure parent directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Stream to file while computing hash
            sha256 = hashlib.sha256()
            size = 0

            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    sha256.update(chunk)
                    size += len(chunk)

            return size, sha256.hexdigest()

        except requests.RequestException as e:
            wait_time = RETRY_BACKOFF**attempt
            print(f"    Download failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"    Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

            # Clean up partial file on failure
            if dest_path.exists():
                dest_path.unlink()

    return None


def get_pdf_path(pdf_url: str, year: int) -> Path:
    """
    Get the local path for a PDF based on its URL and year.

    Args:
        pdf_url: URL of the PDF
        year: Year tag for the document

    Returns:
        Path where the PDF should be saved
    """
    # Extract filename from URL (last segment)
    filename = pdf_url.rstrip("/").split("/")[-1]

    # Ensure .pdf extension
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    return RAW_PDFS_DIR / str(year) / filename


def download_pending(year: int | None = None, batch_size: int = 100) -> None:
    """
    Download all pending PDFs.

    Args:
        year: Optional year filter
        batch_size: Number of documents to query at a time
    """
    start_time = datetime.now()
    total_downloaded = 0
    total_failed = 0
    total_skipped = 0

    # Get initial count
    initial_pending = get_pending_downloads(year=year)
    total_pending = len(initial_pending)

    if total_pending == 0:
        year_str = f" for year {year}" if year else ""
        print(f"No pending downloads{year_str}.")
        return

    year_str = f" for year {year}" if year else ""
    print(f"Starting download of {total_pending} pending PDFs{year_str}")
    print(f"Start time: {start_time.isoformat()}")
    print("=" * 60)

    # Process in batches
    while True:
        docs = get_pending_downloads(year=year, limit=batch_size)
        if not docs:
            break

        for doc in docs:
            doc_id = doc["id"]
            pdf_url = doc["pdf_url"]
            doc_year = doc["year_tag"]

            # Build full URL if relative path
            if pdf_url.startswith("/"):
                full_url = FPPC_DOMAIN + pdf_url
            else:
                full_url = pdf_url

            # Determine destination path
            dest_path = get_pdf_path(pdf_url, doc_year)

            # Check if file already exists (maybe from a previous partial run)
            if dest_path.exists():
                print(f"[{total_downloaded + total_failed + total_skipped + 1}/{total_pending}] "
                      f"Skipped (exists): {dest_path.name}")
                # Update status based on existing file
                size = dest_path.stat().st_size
                with open(dest_path, "rb") as f:
                    sha256 = hashlib.sha256(f.read()).hexdigest()
                update_download_status(doc_id, "downloaded", size, sha256)
                total_skipped += 1
                continue

            print(f"[{total_downloaded + total_failed + total_skipped + 1}/{total_pending}] "
                  f"Downloading: {dest_path.name}")

            result = download_pdf(full_url, dest_path)

            if result is not None:
                size, sha256 = result
                update_download_status(doc_id, "downloaded", size, sha256)
                total_downloaded += 1
                print(f"    OK ({size:,} bytes)")
            else:
                update_download_status(doc_id, "failed")
                total_failed += 1
                print(f"    FAILED")

            # Polite delay between downloads
            time.sleep(DOWNLOAD_DELAY)

    elapsed = datetime.now() - start_time
    print("\n" + "=" * 60)
    print("Download complete!")
    print(f"  Downloaded: {total_downloaded}")
    print(f"  Skipped (existing): {total_skipped}")
    print(f"  Failed: {total_failed}")
    print(f"  Total time: {elapsed}")

    # Show total size
    stats = get_download_stats()
    total_mb = stats.get("total_size_bytes", 0) / (1024 * 1024)
    print(f"  Total downloaded size: {total_mb:.1f} MB")


def print_download_stats() -> None:
    """Print download statistics."""
    stats = get_download_stats()

    print("\n" + "=" * 60)
    print("PDF Download Statistics")
    print("=" * 60)

    pending = stats.get("pending", 0)
    downloaded = stats.get("downloaded", 0)
    failed = stats.get("failed", 0)
    total = pending + downloaded + failed

    print(f"\nTotal documents: {total}")
    print(f"  Pending: {pending}")
    print(f"  Downloaded: {downloaded}")
    print(f"  Failed: {failed}")

    if downloaded > 0:
        total_mb = stats.get("total_size_bytes", 0) / (1024 * 1024)
        avg_kb = (stats.get("total_size_bytes", 0) / downloaded) / 1024
        print(f"\nDownloaded size: {total_mb:.1f} MB")
        print(f"Average PDF size: {avg_kb:.1f} KB")

    if stats.get("pending_by_year"):
        print("\nPending by year:")
        for year, count in stats["pending_by_year"].items():
            print(f"  {year}: {count}")
