#!/usr/bin/env python3
"""
Re-extract low-density documents using olmOCR via DeepInfra.

Targets ~46 native-only documents with <50 words/page, typically 1976-1983 era
scanned PDFs where PyMuPDF native extraction failed. Estimated cost: ~$0.50-1.00.

Requires: source .env (for DEEPINFRA_API_KEY) before running.

Usage:
    python scripts/fix_low_density.py --dry-run     # Preview what would be re-extracted
    python scripts/fix_low_density.py               # Apply re-extraction
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from scraper.db import get_connection, update_extraction_status
from scraper.extractor import Extractor, EXTRACTED_DIR
from scraper.config import DATA_DIR

VALIDATION_JSON = "data/qa_reports/corpus_validation.json"


def get_deleted_ids() -> set[str]:
    """Get IDs of documents deleted in Task 1 (deduplication)."""
    # Check which JSON files no longer exist
    deleted = set()
    conn = get_connection()
    cursor = conn.cursor()
    # Documents with no JSON on disk are deleted
    cursor.execute("SELECT letter_id FROM documents WHERE extraction_status = 'extracted'")
    for row in cursor.fetchall():
        lid = row["letter_id"]
        if lid:
            safe_id = re.sub(r'[^\w\-.]', '_', lid)
            # Check all year dirs
            found = False
            extracted_dir = str(EXTRACTED_DIR)
            if os.path.isdir(extracted_dir):
                for year_dir in os.listdir(extracted_dir):
                    json_path = os.path.join(extracted_dir, year_dir, f"{safe_id}.json")
                    if os.path.exists(json_path):
                        found = True
                        break
    conn.close()
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Re-extract low-density docs with olmOCR")
    parser.add_argument("--dry-run", action="store_true", help="Preview without re-extracting")
    args = parser.parse_args()

    # Check for API key
    if not args.dry_run and not os.environ.get("DEEPINFRA_API_KEY"):
        print("Error: DEEPINFRA_API_KEY not set. Run: source .env")
        sys.exit(1)

    # Load low-density doc list from validation report
    if not os.path.exists(VALIDATION_JSON):
        print(f"Error: {VALIDATION_JSON} not found. Run qa_corpus_validate.py --json first.")
        sys.exit(1)

    with open(VALIDATION_JSON) as f:
        data = json.load(f)

    low_density = data["checks"]["4_word_page_outliers"]["low_details"]
    print(f"Found {len(low_density)} low-density documents total")

    # Filter to native-only (skip docs already using olmOCR)
    native_only = [d for d in low_density if d["method"] == "native"]
    print(f"  Native-only: {len(native_only)}")

    # Filter out docs deleted in Task 1 (check if JSON exists)
    to_process = []
    for d in native_only:
        doc_id = d["doc_id"]
        safe_id = re.sub(r'[^\w\-.]', '_', doc_id)
        found = False
        extracted_dir = str(EXTRACTED_DIR)
        for year_dir in os.listdir(extracted_dir):
            json_path = os.path.join(extracted_dir, year_dir, f"{safe_id}.json")
            if os.path.exists(json_path):
                found = True
                break
        if found:
            to_process.append(d)
        else:
            print(f"  Skipping {doc_id} — deleted in dedup")

    print(f"  After filtering: {len(to_process)} docs to re-extract\n")

    if args.dry_run:
        print("DRY RUN — would re-extract these documents:\n")
        total_pages = 0
        for d in to_process:
            print(f"  {d['doc_id']:20s}  words={d['word_count']:5d}  pages={d['page_count']:2d}  "
                  f"ratio={d['ratio']:5.1f} w/p")
            total_pages += d["page_count"]
        est_cost = total_pages * 0.002  # ~$0.002 per page for olmOCR
        print(f"\nTotal pages: {total_pages}")
        print(f"Estimated cost: ~${est_cost:.2f}")
        return

    # Look up DB rows for these documents
    conn = get_connection()
    cursor = conn.cursor()

    extractor = Extractor(skip_olmocr=False, verbose=True)

    success = 0
    errors = 0
    improved = 0

    for d in to_process:
        doc_id_str = d["doc_id"]

        # Find DB row by letter_id
        cursor.execute("SELECT * FROM documents WHERE letter_id = ?", (doc_id_str,))
        row = cursor.fetchone()

        if not row:
            # Try json_path match
            safe_id = re.sub(r'[^\w\-.]', '_', doc_id_str)
            cursor.execute("SELECT * FROM documents WHERE json_path LIKE ?",
                           (f"%{safe_id}.json",))
            row = cursor.fetchone()

        if not row:
            print(f"  DB row not found for {doc_id_str}, skipping")
            errors += 1
            continue

        doc_row = dict(row)
        old_quality = doc_row.get("extraction_quality", 0)

        # Reset extraction status to pending so the extractor will process it
        cursor.execute("UPDATE documents SET extraction_status = 'pending' WHERE id = ?",
                        (doc_row["id"],))
        conn.commit()

        # Re-extract with olmOCR enabled
        try:
            doc = extractor.process_document(doc_row)
            if doc:
                json_path = extractor.save_document(doc)
                update_extraction_status(
                    doc_id=doc_row["id"],
                    status="extracted",
                    method=doc.extraction.method,
                    quality=doc.extraction.quality_score,
                    section_confidence=doc.sections.extraction_confidence,
                    json_path=str(json_path.relative_to(DATA_DIR.parent)),
                    needs_llm=doc.sections.extraction_confidence < 0.5 or not doc.sections.has_standard_format,
                )
                new_quality = doc.extraction.quality_score
                new_words = doc.extraction.word_count
                word_ratio = new_words / d["page_count"] if d["page_count"] else 0

                if new_quality > old_quality:
                    improved += 1

                print(f"  {doc_id_str}: quality {old_quality:.2f} → {new_quality:.2f}, "
                      f"words {d['word_count']} → {new_words} "
                      f"({word_ratio:.0f} w/p), method={doc.extraction.method}")
                success += 1
            else:
                # Reset to extracted so it's not stuck as pending
                cursor.execute("UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                                (doc_row["id"],))
                conn.commit()
                errors += 1
        except Exception as e:
            print(f"  Error re-extracting {doc_id_str}: {e}")
            cursor.execute("UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                            (doc_row["id"],))
            conn.commit()
            errors += 1

    conn.close()

    print(f"\n{'=' * 60}")
    print(f"RE-EXTRACTION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Processed: {len(to_process)}")
    print(f"  Success: {success}")
    print(f"  Improved quality: {improved}")
    print(f"  Errors: {errors}")
    print(f"  olmOCR cost: ${extractor.stats['total_olmocr_cost']:.4f}")


if __name__ == "__main__":
    main()
