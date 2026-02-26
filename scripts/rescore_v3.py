#!/usr/bin/env python3
"""
Rescore all documents in the database with v3 quality scoring.

The extraction_quality column currently holds a mix of:
- v1 scores (capped at 0.80) for ~11K native-extracted docs
- v3 scores (full 0.0-1.0 range) for ~3K olmocr-reextracted docs

This script reads each document's JSON file, runs compute_quality_score(),
and updates extraction_quality in the database. No API calls, no cost --
purely local computation.

Usage:
    python scripts/rescore_v3.py             # Full run
    python scripts/rescore_v3.py --dry-run   # Preview without DB writes
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.quality import compute_quality_score


def main():
    parser = argparse.ArgumentParser(description="Rescore all documents with v3 quality scoring")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--only-stale", action="store_true",
                        help="Only rescore docs with v1 scores (extraction_quality <= 0.80)")
    args = parser.parse_args()

    db_path = Path("data/documents.db")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Fetch all documents
    if args.only_stale:
        cur.execute("""
            SELECT id, json_path, page_count, extraction_quality, extraction_method
            FROM documents
            WHERE extraction_quality <= 0.80 OR extraction_quality IS NULL
            ORDER BY id
        """)
    else:
        cur.execute("""
            SELECT id, json_path, page_count, extraction_quality, extraction_method
            FROM documents
            ORDER BY id
        """)

    rows = cur.fetchall()
    total = len(rows)
    print(f"Documents to rescore: {total}")
    if args.dry_run:
        print("(DRY RUN — no database writes)")
    print()

    updated = 0
    unchanged = 0
    errors = 0
    score_changes = []  # Track (old, new) for summary

    start = time.time()

    for i, row in enumerate(rows):
        doc_id = row["id"]
        json_path = row["json_path"]
        page_count = row["page_count"] or 1
        old_score = row["extraction_quality"]

        # Read the JSON file to get full_text
        try:
            with open(json_path) as f:
                data = json.load(f)
            text = data.get("content", {}).get("full_text", "")
            if not text:
                text = ""

            # Also get page_count from JSON if DB value is missing
            if not row["page_count"]:
                page_count = data.get("extraction", {}).get("page_count", 1) or 1
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            errors += 1
            if errors <= 10:
                print(f"  ERROR doc {doc_id}: {e}")
            continue

        # Compute v3 score
        metrics = compute_quality_score(text, page_count)
        new_score = round(metrics.final_score, 4)

        if old_score is not None and abs(new_score - old_score) < 0.0001:
            unchanged += 1
        else:
            score_changes.append((doc_id, old_score, new_score))
            updated += 1
            if not args.dry_run:
                cur.execute(
                    "UPDATE documents SET extraction_quality = ? WHERE id = ?",
                    (new_score, doc_id),
                )

        # Progress every 2000 docs
        if (i + 1) % 2000 == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed
            remaining = (total - i - 1) / rate
            print(f"  [{i+1}/{total}] {rate:.0f} docs/sec, ~{remaining:.0f}s remaining "
                  f"({updated} changed, {unchanged} unchanged, {errors} errors)")

    if not args.dry_run:
        conn.commit()

    elapsed = time.time() - start

    # Summary
    print()
    print(f"{'DRY RUN ' if args.dry_run else ''}Rescore complete in {elapsed:.1f}s")
    print(f"  Total:     {total}")
    print(f"  Updated:   {updated}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Errors:    {errors}")

    # Distribution of new scores
    if not args.dry_run and updated > 0:
        cur.execute("""
            SELECT
                CASE
                    WHEN extraction_quality < 0.50 THEN '< 0.50'
                    WHEN extraction_quality < 0.70 THEN '0.50-0.69'
                    WHEN extraction_quality < 0.80 THEN '0.70-0.79'
                    WHEN extraction_quality < 0.90 THEN '0.80-0.89'
                    ELSE '0.90+'
                END as bucket,
                COUNT(*) as cnt
            FROM documents
            GROUP BY bucket
            ORDER BY bucket
        """)
        print("\nNew quality distribution:")
        for row in cur.fetchall():
            print(f"  {row['bucket']:>12}: {row['cnt']:>6}")

        cur.execute("SELECT AVG(extraction_quality) FROM documents WHERE extraction_quality IS NOT NULL")
        avg = cur.fetchone()[0]
        print(f"\n  Average score: {avg:.3f}")

        cur.execute("SELECT COUNT(*) FROM documents WHERE extraction_quality >= 0.80")
        usable = cur.fetchone()[0]
        print(f"  Usable (>=0.80): {usable}/{total} ({100*usable/total:.1f}%)")
    elif args.dry_run and score_changes:
        # Show score change distribution in dry run
        increases = [n - (o or 0) for _, o, n in score_changes if o is not None]
        decreases = [n - o for _, o, n in score_changes if o is not None and n < o]
        if increases:
            avg_change = sum(increases) / len(increases)
            print(f"\n  Average score change: {avg_change:+.3f}")
        if decreases:
            print(f"  Decreases: {len(decreases)}")

        # Show a few example changes
        print("\n  Sample changes (first 10):")
        for doc_id, old, new in score_changes[:10]:
            old_str = f"{old:.3f}" if old is not None else "None"
            print(f"    doc {doc_id}: {old_str} -> {new:.3f}")

    conn.close()


if __name__ == "__main__":
    main()
