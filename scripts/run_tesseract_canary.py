#!/usr/bin/env python3
"""
Tesseract canary scan: detect olmOCR hallucinations by comparison.

Tesseract produces noisy but honest text — it never fabricates content.
By comparing Tesseract output against olmOCR output page-by-page, we can
flag documents where olmOCR diverged from reality (hallucinated).

This computes a canary_score (0.0–1.0) per document:
  - 1.0 = Tesseract and olmOCR agree perfectly
  - 0.7+ = minor differences (expected — olmOCR is cleaner)
  - 0.5–0.7 = significant divergence, may indicate partial hallucination
  - <0.5 = strong divergence, likely hallucination or description-mode

Also detects description-mode markers ("The image is a scanned document...").

Usage:
    python scripts/run_tesseract_canary.py --dry-run         # Preview olmOCR doc count
    python scripts/run_tesseract_canary.py                   # Full scan
    python scripts/run_tesseract_canary.py --limit 50        # Test on 50 docs
    python scripts/run_tesseract_canary.py --workers 4       # Parallel Tesseract
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.config import DATA_DIR, RAW_PDFS_DIR
from scraper.db import get_connection, update_fidelity

DB_PATH = DATA_DIR / "documents.db"
REPORT_DIR = DATA_DIR / "qa_reports"
REPORT_PATH = REPORT_DIR / "canary_scan.json"

# Description-mode markers — olmOCR describes the image instead of transcribing
DESCRIPTION_MODE_PATTERNS = [
    re.compile(r"The image (?:is|shows|contains|appears|displays|presents)", re.IGNORECASE),
    re.compile(r"This (?:is a|appears to be a) scanned", re.IGNORECASE),
    re.compile(r"The document (?:is|appears|shows|contains)", re.IGNORECASE),
    re.compile(r"(?:scanned|photographed) (?:image|copy|document) of", re.IGNORECASE),
    re.compile(r"The (?:text|content) (?:of the|in the) (?:image|document)", re.IGNORECASE),
    re.compile(r"This image (?:is|shows|contains)", re.IGNORECASE),
]


@dataclass
class CanaryResult:
    """Result of canary comparison for a single document."""
    doc_id: int
    letter_id: str
    year: int
    canary_score: float          # 0.0-1.0 average page similarity
    page_scores: list[float]     # per-page similarity scores
    is_description_mode: bool    # detected description-mode markers
    description_pages: list[int] # which pages have description markers
    tesseract_words: int         # total words from Tesseract
    olmocr_words: int            # total words from olmOCR
    risk_tier: str               # critical, high, medium, low
    error: str | None = None


def detect_description_mode(text: str) -> bool:
    """Check if text contains description-mode markers."""
    # Check first 500 chars of each "page" (olmOCR sometimes describes mid-doc)
    for pattern in DESCRIPTION_MODE_PATTERNS:
        if pattern.search(text[:500]):
            return True
    return False


def normalize_for_comparison(text: str) -> str:
    """
    Normalize text for fair comparison between Tesseract and olmOCR.

    Both OCR engines produce different formatting (whitespace, line breaks,
    headers) but should agree on the actual words. Normalization strips
    formatting differences to focus on content agreement.
    """
    # Lowercase
    text = text.lower()
    # Remove common OCR artifacts and formatting
    text = re.sub(r'[^\w\s]', ' ', text)  # Strip punctuation
    text = re.sub(r'\s+', ' ', text)       # Collapse whitespace
    return text.strip()


def run_tesseract_on_page(pdf_path: str, page_num: int) -> str:
    """
    Render a single PDF page and run Tesseract on it.

    Args:
        pdf_path: Path to PDF file
        page_num: 0-indexed page number

    Returns:
        Extracted text from Tesseract
    """
    import fitz

    doc = fitz.open(pdf_path)
    try:
        if page_num >= len(doc):
            return ""
        page = doc[page_num]
        # 300 DPI for good OCR quality (higher than olmOCR's 150 DPI)
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
    finally:
        doc.close()

    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--dpi", "300", "-l", "eng"],
        input=img_bytes,
        capture_output=True,
        timeout=60,
    )
    return result.stdout.decode("utf-8", errors="replace")


def load_olmocr_text(json_path: str) -> str:
    """Load the full olmOCR text from a document's JSON file."""
    with open(json_path) as f:
        data = json.load(f)
    # full_text is the canonical store; markdown may have extra formatting
    return data.get("content", {}).get("full_text", "")


def compare_texts(tesseract_text: str, olmocr_text: str) -> float:
    """
    Compare full Tesseract and olmOCR text using word-level SequenceMatcher.

    Works on normalized word sequences rather than character-level comparison.
    This is more robust against formatting differences (whitespace, line breaks)
    between the two OCR engines while still detecting content divergence.

    Returns similarity ratio 0.0-1.0.
    """
    t_norm = normalize_for_comparison(tesseract_text)
    o_norm = normalize_for_comparison(olmocr_text)

    if not t_norm and not o_norm:
        return 1.0  # Both empty — agreement
    if not t_norm or not o_norm:
        return 0.0  # One empty, one not — total disagreement

    # Word-level comparison: split into words, compare sequences.
    # More robust than char-level because OCR engines differ on spacing/punctuation
    # but agree on actual words. Also much faster for long docs.
    t_words = t_norm.split()
    o_words = o_norm.split()

    return SequenceMatcher(None, t_words, o_words).ratio()


def process_single_doc(args: tuple) -> CanaryResult:
    """
    Process a single document: run Tesseract on all pages, compare full text
    against olmOCR output.

    Args:
        Tuple of (doc_id, letter_id, year, pdf_path, json_path, page_count)
    """
    doc_id, letter_id, year, pdf_path, json_path, page_count = args

    try:
        # Load olmOCR output as a single string
        full_json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            json_path,
        )
        olmocr_text = load_olmocr_text(full_json_path)

        if not olmocr_text:
            return CanaryResult(
                doc_id=doc_id, letter_id=letter_id, year=year,
                canary_score=0.0, page_scores=[], is_description_mode=False,
                description_pages=[], tesseract_words=0, olmocr_words=0,
                risk_tier="high", error="no olmOCR text found",
            )

        # Check for description-mode markers in the olmOCR text
        # Check at the start and after each double-newline (page boundaries)
        is_description_mode = detect_description_mode(olmocr_text)
        description_pages = []
        chunks = olmocr_text.split("\n\n")
        for i, chunk in enumerate(chunks):
            if detect_description_mode(chunk):
                description_pages.append(i)

        # Run Tesseract on each page, collecting per-page and full text
        pages_to_check = min(page_count or 1, 20)
        page_scores = []
        tess_page_texts = []

        for page_num in range(pages_to_check):
            tess_text = run_tesseract_on_page(pdf_path, page_num)
            tess_page_texts.append(tess_text)

        # Full document comparison: concatenate all Tesseract pages, compare
        # against the full olmOCR text
        full_tess_text = "\n\n".join(tess_page_texts)
        total_tess_words = len(full_tess_text.split())
        total_olm_words = len(olmocr_text.split())

        canary_score = compare_texts(full_tess_text, olmocr_text)

        # Also compute per-page scores for diagnostic purposes:
        # Compare each Tesseract page against a proportional slice of olmOCR text
        if pages_to_check > 1 and total_olm_words > 0:
            olm_words = olmocr_text.split()
            words_per_page = total_olm_words // pages_to_check
            for i, tess_page in enumerate(tess_page_texts):
                start = i * words_per_page
                end = start + words_per_page if i < pages_to_check - 1 else total_olm_words
                olm_slice = " ".join(olm_words[start:end])
                score = compare_texts(tess_page, olm_slice)
                page_scores.append(round(score, 4))
        else:
            page_scores = [round(canary_score, 4)]

        # Classify risk tier
        if is_description_mode:
            risk_tier = "critical"
        elif canary_score < 0.30:
            risk_tier = "critical"
        elif canary_score < 0.50:
            risk_tier = "high"
        elif canary_score < 0.70:
            risk_tier = "medium"
        else:
            risk_tier = "low"

        return CanaryResult(
            doc_id=doc_id,
            letter_id=letter_id,
            year=year,
            canary_score=round(canary_score, 4),
            page_scores=page_scores,
            is_description_mode=is_description_mode,
            description_pages=description_pages,
            tesseract_words=total_tess_words,
            olmocr_words=total_olm_words,
            risk_tier=risk_tier,
        )

    except Exception as e:
        return CanaryResult(
            doc_id=doc_id, letter_id=letter_id, year=year,
            canary_score=0.0, page_scores=[], is_description_mode=False,
            description_pages=[], tesseract_words=0, olmocr_words=0,
            risk_tier="high", error=str(e),
        )


def get_olmocr_documents() -> list[dict]:
    """Fetch all olmOCR-extracted documents from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, letter_id, year_tag, pdf_url, json_path, page_count
        FROM documents
        WHERE extraction_status = 'extracted'
        AND extraction_method = 'olmocr'
        AND json_path IS NOT NULL
        ORDER BY year_tag, id
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def resolve_pdf_path(doc: dict) -> str | None:
    """Resolve the local PDF path for a document."""
    year = doc.get("year_tag")
    pdf_url = doc.get("pdf_url", "")
    filename = pdf_url.rstrip("/").split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    year_dir = RAW_PDFS_DIR / str(year)
    pdf_path = year_dir / filename

    if pdf_path.exists():
        return str(pdf_path)

    # Case-insensitive fallback
    if year_dir.is_dir():
        target_stem = Path(filename).stem.lower()
        for candidate in year_dir.iterdir():
            if candidate.stem.lower() == target_stem and candidate.suffix.lower() == ".pdf":
                return str(candidate)

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Tesseract canary scan for olmOCR hallucination detection"
    )
    parser.add_argument("--limit", type=int, help="Max documents to process")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers (default: 1, Tesseract is CPU-bound)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show candidate count without processing")
    parser.add_argument("--update-db", action="store_true",
                        help="Update fidelity columns in database with results")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from existing canary_scan.json")
    args = parser.parse_args()

    docs = get_olmocr_documents()
    print(f"Found {len(docs)} olmOCR documents to scan")

    if args.dry_run:
        by_year = {}
        for d in docs:
            by_year[d["year_tag"]] = by_year.get(d["year_tag"], 0) + 1
        print("\nBy year:")
        for year in sorted(by_year):
            print(f"  {year}: {by_year[year]}")
        total_pages = sum(d.get("page_count", 1) or 1 for d in docs)
        print(f"\nTotal pages to OCR: {total_pages}")
        print(f"Estimated time at ~2s/page: {total_pages * 2 / 3600:.1f} hours")
        return

    # Load existing results if resuming
    completed_ids = set()
    existing_results = []
    if args.resume and REPORT_PATH.exists():
        with open(REPORT_PATH) as f:
            data = json.load(f)
        existing_results = data.get("results", [])
        completed_ids = {r["doc_id"] for r in existing_results}
        print(f"Resuming: {len(completed_ids)} docs already scanned")

    # Build work items
    work_items = []
    skipped_no_pdf = 0
    for doc in docs:
        if doc["id"] in completed_ids:
            continue

        pdf_path = resolve_pdf_path(doc)
        if not pdf_path:
            skipped_no_pdf += 1
            continue

        work_items.append((
            doc["id"],
            doc.get("letter_id") or f"doc#{doc['id']}",
            doc["year_tag"],
            pdf_path,
            doc["json_path"],
            doc.get("page_count") or 1,
        ))

    if args.limit:
        work_items = work_items[:args.limit]

    if skipped_no_pdf:
        print(f"Skipped {skipped_no_pdf} docs with missing PDFs")
    print(f"Processing {len(work_items)} documents...")

    # Process documents
    results = list(existing_results)
    start_time = time.time()
    processed = 0
    errors = 0

    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_single_doc, item): item for item in work_items}
            for future in as_completed(futures):
                result = future.result()
                results.append(asdict(result))
                processed += 1
                if result.error:
                    errors += 1

                if processed % 50 == 0:
                    elapsed = time.time() - start_time
                    rate = processed / elapsed if elapsed > 0 else 0
                    remaining = (len(work_items) - processed) / rate if rate > 0 else 0
                    print(f"  [{processed}/{len(work_items)}] "
                          f"{rate:.1f} docs/min, ~{remaining / 60:.1f} hrs remaining")

                # Periodic checkpoint save
                if processed % 200 == 0:
                    _save_report(results, start_time, processed, errors)
    else:
        for item in work_items:
            result = process_single_doc(item)
            results.append(asdict(result))
            processed += 1
            if result.error:
                errors += 1

            if processed % 10 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (len(work_items) - processed) / rate if rate > 0 else 0
                tier = result.risk_tier
                print(f"  [{processed}/{len(work_items)}] "
                      f"{result.letter_id}: canary={result.canary_score:.3f} "
                      f"({tier}) | {rate:.1f} docs/min, "
                      f"~{remaining / 60:.1f} hrs remaining")

            # Periodic checkpoint save
            if processed % 100 == 0:
                _save_report(results, start_time, processed, errors)

    # Final save
    _save_report(results, start_time, processed, errors)

    # Update database if requested
    if args.update_db:
        print("\nUpdating database fidelity columns...")
        updated = 0
        for r in results:
            if r.get("error"):
                continue
            update_fidelity(
                doc_id=r["doc_id"],
                score=r["canary_score"],
                method="tesseract_canary",
                risk=r["risk_tier"],
            )
            updated += 1
        print(f"Updated {updated} documents in database")

    # Print summary
    _print_summary(results)


def _save_report(results: list[dict], start_time: float, processed: int, errors: int):
    """Save checkpoint report to JSON."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Compute tier distribution
    tiers = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    desc_mode_count = 0
    for r in results:
        tier = r.get("risk_tier", "high")
        tiers[tier] = tiers.get(tier, 0) + 1
        if r.get("is_description_mode"):
            desc_mode_count += 1

    report = {
        "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_scanned": len(results),
        "errors": errors,
        "elapsed_seconds": round(time.time() - start_time, 1),
        "tier_distribution": tiers,
        "description_mode_count": desc_mode_count,
        "results": results,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)


def _print_summary(results: list[dict]):
    """Print summary of canary scan results."""
    tiers = {"critical": [], "high": [], "medium": [], "low": []}
    desc_mode = []
    error_count = 0

    for r in results:
        if r.get("error"):
            error_count += 1
            continue
        tier = r.get("risk_tier", "high")
        tiers.setdefault(tier, []).append(r)
        if r.get("is_description_mode"):
            desc_mode.append(r)

    print(f"\n{'=' * 60}")
    print("TESSERACT CANARY SCAN RESULTS")
    print(f"{'=' * 60}")
    print(f"Total scanned: {len(results)}")
    print(f"Errors: {error_count}")
    print(f"\nRisk Tier Distribution:")
    print(f"  Critical (description-mode or canary < 0.30): {len(tiers['critical'])}")
    print(f"  High     (canary < 0.50):                     {len(tiers['high'])}")
    print(f"  Medium   (canary 0.50-0.70):                  {len(tiers['medium'])}")
    print(f"  Low      (canary > 0.70):                     {len(tiers['low'])}")
    print(f"\nDescription-mode detected: {len(desc_mode)}")

    # Show worst docs
    all_scored = [r for r in results if not r.get("error")]
    all_scored.sort(key=lambda r: r["canary_score"])
    if all_scored:
        print(f"\nWorst 10 documents:")
        for r in all_scored[:10]:
            dm = " [DESC-MODE]" if r.get("is_description_mode") else ""
            print(f"  {r['letter_id']:20s} canary={r['canary_score']:.3f} "
                  f"year={r['year']} tess_words={r['tesseract_words']} "
                  f"olm_words={r['olmocr_words']}{dm}")

    print(f"\nReport saved to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
