#!/usr/bin/env python3
"""
Fix critical-risk documents: description-mode and severe hallucinations.

Reads the canary scan report to find critical-risk documents, then:
1. Re-extracts each via olmOCR (description-mode is often transient)
2. If still bad after retry: extracts via Tesseract as honest fallback
3. Updates JSON files and database

Requires: DEEPINFRA_API_KEY (for olmOCR retry)

Usage:
    python scripts/fix_critical_fidelity.py --dry-run         # Preview critical docs
    python scripts/fix_critical_fidelity.py                   # Fix all critical docs
    python scripts/fix_critical_fidelity.py --limit 10        # Fix 10 docs
    python scripts/fix_critical_fidelity.py --tesseract-only  # Skip olmOCR retry, use Tesseract
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from scraper.config import DATA_DIR, RAW_PDFS_DIR
from scraper.db import get_connection, update_extraction_status, update_fidelity
from scraper.extractor import Extractor, EXTRACTED_DIR
from scraper.quality import compute_quality_score

DB_PATH = DATA_DIR / "documents.db"
CANARY_REPORT = DATA_DIR / "qa_reports" / "canary_scan.json"

# Description-mode detection (same patterns as canary scan)
DESCRIPTION_MODE_PATTERNS = [
    re.compile(r"The image (?:is|shows|contains|appears|displays|presents)", re.IGNORECASE),
    re.compile(r"This (?:is a|appears to be a) scanned", re.IGNORECASE),
    re.compile(r"The document (?:is|appears|shows|contains)", re.IGNORECASE),
    re.compile(r"(?:scanned|photographed) (?:image|copy|document) of", re.IGNORECASE),
    re.compile(r"The (?:text|content) (?:of the|in the) (?:image|document)", re.IGNORECASE),
    re.compile(r"This image (?:is|shows|contains)", re.IGNORECASE),
]


def has_description_mode(text: str) -> bool:
    """Check if text contains description-mode markers."""
    for pattern in DESCRIPTION_MODE_PATTERNS:
        if pattern.search(text[:1000]):
            return True
    return False


def extract_with_tesseract(pdf_path: str, max_pages: int = 20) -> str:
    """
    Extract text from PDF using Tesseract (honest fallback).

    Tesseract never fabricates — it produces noisy text for hard-to-read
    pages, but every word it outputs came from the actual document.
    """
    import fitz

    texts = []
    doc = fitz.open(pdf_path)
    pages = min(len(doc), max_pages)

    for page_num in range(pages):
        page = doc[page_num]
        mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--dpi", "300", "-l", "eng"],
            input=img_bytes,
            capture_output=True,
            timeout=60,
        )
        texts.append(result.stdout.decode("utf-8", errors="replace"))

    doc.close()
    return "\n\n".join(texts)


def resolve_pdf_path(doc_row: dict) -> str | None:
    """Resolve local PDF path from DB row."""
    year = doc_row.get("year_tag")
    pdf_url = doc_row.get("pdf_url", "")
    filename = pdf_url.rstrip("/").split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    year_dir = RAW_PDFS_DIR / str(year)
    pdf_path = year_dir / filename

    if pdf_path.exists():
        return str(pdf_path)

    if year_dir.is_dir():
        target_stem = Path(filename).stem.lower()
        for candidate in year_dir.iterdir():
            if candidate.stem.lower() == target_stem and candidate.suffix.lower() == ".pdf":
                return str(candidate)
    return None


def get_critical_docs() -> list[dict]:
    """Load critical-risk docs from the canary scan report."""
    if not CANARY_REPORT.exists():
        print(f"Error: canary scan report not found at {CANARY_REPORT}")
        print("Run: python scripts/run_tesseract_canary.py first")
        sys.exit(1)

    with open(CANARY_REPORT) as f:
        data = json.load(f)

    critical = [
        r for r in data["results"]
        if r.get("risk_tier") == "critical" and not r.get("error")
    ]
    return critical


def main():
    parser = argparse.ArgumentParser(
        description="Fix critical-risk fidelity documents"
    )
    parser.add_argument("--limit", type=int, help="Max documents to fix")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--tesseract-only", action="store_true",
                        help="Skip olmOCR retry, use Tesseract directly")
    args = parser.parse_args()

    critical = get_critical_docs()
    print(f"Found {len(critical)} critical-risk documents")

    if not critical:
        print("Nothing to fix!")
        return

    # Show summary
    desc_mode = [d for d in critical if d.get("is_description_mode")]
    low_canary = [d for d in critical if not d.get("is_description_mode")]
    print(f"  Description-mode: {len(desc_mode)}")
    print(f"  Low canary (<0.30): {len(low_canary)}")

    if args.dry_run:
        print(f"\nCritical documents:")
        for d in critical[:30]:
            dm = " [DESC-MODE]" if d.get("is_description_mode") else ""
            print(f"  {d['letter_id']:20s} canary={d['canary_score']:.3f} "
                  f"year={d['year']}{dm}")
        if len(critical) > 30:
            print(f"  ... and {len(critical) - 30} more")
        return

    # Check API key if doing olmOCR retry
    if not args.tesseract_only and not os.environ.get("DEEPINFRA_API_KEY"):
        print("Warning: DEEPINFRA_API_KEY not set, using Tesseract-only mode")
        args.tesseract_only = True

    # Backup DB
    backup_path = DB_PATH.with_suffix(f".db.bak-critical-{datetime.now():%Y%m%d}")
    if not backup_path.exists():
        print(f"Backing up database to {backup_path.name}...")
        shutil.copy2(DB_PATH, backup_path)

    to_fix = critical[:args.limit] if args.limit else critical

    # Initialize extractor for olmOCR retry
    extractor = None
    if not args.tesseract_only:
        extractor = Extractor(skip_olmocr=False, force_olmocr=True, verbose=False)

    conn = get_connection()
    cursor = conn.cursor()

    fixed_olmocr = 0
    fixed_tesseract = 0
    still_bad = 0
    errors = 0

    for i, doc_info in enumerate(to_fix, 1):
        doc_id = doc_info["doc_id"]
        letter_id = doc_info["letter_id"]
        old_canary = doc_info["canary_score"]

        print(f"[{i}/{len(to_fix)}] {letter_id} (canary={old_canary:.3f})", end="")

        # Fetch full DB row
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            print(" — DB row not found, skipping")
            errors += 1
            continue

        doc_row = dict(row)
        pdf_path = resolve_pdf_path(doc_row)
        if not pdf_path:
            print(" — PDF not found, skipping")
            errors += 1
            continue

        fixed = False

        # Strategy 1: olmOCR retry (if not tesseract-only)
        if not args.tesseract_only and extractor:
            # Reset to pending for re-extraction
            cursor.execute(
                "UPDATE documents SET extraction_status = 'pending' WHERE id = ?",
                (doc_id,),
            )
            conn.commit()

            try:
                doc = extractor.process_document(doc_row)
                if doc:
                    new_text = doc.content.full_text
                    if not has_description_mode(new_text) and doc.extraction.quality_score > 0.5:
                        # olmOCR retry succeeded
                        json_path = extractor.save_document(doc)
                        update_extraction_status(
                            doc_id=doc_id,
                            status="extracted",
                            method=doc.extraction.method,
                            quality=doc.extraction.quality_score,
                            section_confidence=doc.sections.extraction_confidence,
                            json_path=str(json_path.relative_to(DATA_DIR.parent)),
                            needs_llm=(
                                doc.sections.extraction_confidence < 0.5
                                or not doc.sections.has_standard_format
                            ),
                        )
                        update_fidelity(doc_id, 0.8, "olmocr_retry", "medium")
                        fixed_olmocr += 1
                        fixed = True
                        print(f" — FIXED via olmOCR retry (q={doc.extraction.quality_score:.3f})")
            except Exception as e:
                print(f" — olmOCR retry error: {e}", end="")

            if not fixed:
                # Restore status
                cursor.execute(
                    "UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                    (doc_id,),
                )
                conn.commit()

        # Strategy 2: Tesseract fallback
        if not fixed:
            try:
                tess_text = extract_with_tesseract(pdf_path)
                page_count = doc_row.get("page_count") or 1
                metrics = compute_quality_score(tess_text, page_count)

                if metrics.final_score > 0.3 and len(tess_text.split()) > 20:
                    # Tesseract produced usable text — save it
                    # Load existing JSON, replace text content
                    json_path_str = doc_row.get("json_path")
                    if json_path_str:
                        full_json_path = os.path.join(
                            str(DATA_DIR.parent), json_path_str
                        )
                        if os.path.exists(full_json_path):
                            with open(full_json_path) as f:
                                doc_json = json.load(f)

                            doc_json["content"]["full_text"] = tess_text
                            doc_json["content"]["full_text_markdown"] = None
                            doc_json["extraction"]["method"] = "tesseract_fallback"
                            doc_json["extraction"]["quality_score"] = metrics.final_score
                            doc_json["extraction"]["word_count"] = len(tess_text.split())
                            doc_json["extraction"]["char_count"] = len(tess_text)

                            with open(full_json_path, "w") as f:
                                json.dump(doc_json, f, indent=2, ensure_ascii=False)

                            update_extraction_status(
                                doc_id=doc_id,
                                status="extracted",
                                method="tesseract_fallback",
                                quality=metrics.final_score,
                            )
                            update_fidelity(doc_id, 0.9, "tesseract_fallback", "low")
                            fixed_tesseract += 1
                            fixed = True
                            print(f" — FIXED via Tesseract (q={metrics.final_score:.3f})")

                if not fixed:
                    update_fidelity(doc_id, old_canary, "tesseract_canary", "critical")
                    still_bad += 1
                    print(f" — STILL BAD (Tesseract q={metrics.final_score:.3f})")

            except Exception as e:
                update_fidelity(doc_id, old_canary, "tesseract_canary", "critical")
                errors += 1
                print(f" — Tesseract error: {e}")

    conn.close()

    print(f"\n{'=' * 60}")
    print("CRITICAL FIDELITY FIX SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total processed:  {len(to_fix)}")
    print(f"  Fixed via olmOCR:  {fixed_olmocr}")
    print(f"  Fixed via Tesseract: {fixed_tesseract}")
    print(f"  Still critical:   {still_bad}")
    print(f"  Errors:           {errors}")


if __name__ == "__main__":
    main()
