#!/usr/bin/env python3
"""
Re-extract low-quality documents using olmOCR via DeepInfra.

Targets ~2,897 documents scoring below 0.80 on v3 quality scoring — mostly
1990s-era PDFs with font encoding corruption and 1970s scans. The DB still
stores v1 scores (capped at 0.80), so this script rescores everything with v3
to find true candidates.

Estimated cost for all candidates: ~$2.84 (negligible).

Requires: source .env (for DEEPINFRA_API_KEY) before running.

Usage:
    python scripts/reocr_corpus.py --dry-run              # Preview candidates + cost estimate
    python scripts/reocr_corpus.py --limit 10              # Process 10 worst documents
    python scripts/reocr_corpus.py --skip-already-ocr      # Full run, skip already-OCR'd docs
    python scripts/reocr_corpus.py --max-cost 1.00         # Stop after $1.00 spent
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from scraper.db import get_connection, update_extraction_status
from scraper.extractor import Extractor, EXTRACTED_DIR
from scraper.config import DATA_DIR
from scraper.quality import compute_quality_score

DB_PATH = DATA_DIR / "documents.db"


def discover_candidates(threshold: float, skip_already_ocr: bool) -> list[dict]:
    """
    Rescore all extracted documents with v3 scoring and return those below threshold.

    Returns list of dicts sorted by v3_score ascending (worst first):
        [{db_id, letter_id, year, v3_score, old_db_score, extraction_method,
          page_count, json_path}]
    """
    conn = get_connection()
    conn.row_factory = None  # Use tuples for speed
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, letter_id, year_tag, extraction_method, extraction_quality, "
        "page_count, json_path "
        "FROM documents WHERE extraction_status = 'extracted' AND json_path IS NOT NULL"
    )
    rows = cursor.fetchall()
    conn.close()

    print(f"Rescoring {len(rows)} extracted documents with v3 quality scoring...")

    all_scores = []
    candidates = []
    errors = 0

    for db_id, letter_id, year, method, old_quality, page_count, json_path in rows:
        full_path = os.path.join(DATA_DIR.parent, json_path)
        if not os.path.exists(full_path):
            errors += 1
            continue

        try:
            with open(full_path) as f:
                data = json.load(f)
            text = data.get("content", {}).get("full_text", "")
            json_page_count = data.get("extraction", {}).get("page_count")
            effective_pages = json_page_count or page_count or 1
        except (json.JSONDecodeError, OSError):
            errors += 1
            continue

        if not text:
            errors += 1
            continue

        metrics = compute_quality_score(text, effective_pages)
        v3_score = metrics.final_score
        all_scores.append(v3_score)

        if v3_score < threshold:
            if skip_already_ocr and method == "olmocr":
                continue
            candidates.append({
                "db_id": db_id,
                "letter_id": letter_id or f"doc#{db_id}",
                "year": year,
                "v3_score": v3_score,
                "old_db_score": old_quality or 0.0,
                "extraction_method": method or "native",
                "page_count": effective_pages,
                "json_path": json_path,
            })

    # Sort worst first
    candidates.sort(key=lambda d: d["v3_score"])

    # Print v3 distribution
    print(f"\nV3 Quality Distribution ({len(all_scores)} docs scored, {errors} errors):")
    tiers = [
        ("< 0.50 (broken)", lambda s: s < 0.50),
        ("0.50-0.70 (degraded)", lambda s: 0.50 <= s < 0.70),
        ("0.70-0.80 (impaired)", lambda s: 0.70 <= s < 0.80),
        ("0.80-0.90 (minor issues)", lambda s: 0.80 <= s < 0.90),
        ("0.90+ (clean)", lambda s: s >= 0.90),
    ]
    for label, predicate in tiers:
        count = sum(1 for s in all_scores if predicate(s))
        print(f"  {label:30s} {count:6d}")

    # Candidate breakdown
    print(f"\nCandidates below {threshold:.2f}: {len(candidates)}")
    if candidates:
        method_counts = {}
        for c in candidates:
            m = c["extraction_method"]
            method_counts[m] = method_counts.get(m, 0) + 1
        for m, count in sorted(method_counts.items()):
            print(f"  {m}: {count}")

        total_pages = sum(c["page_count"] for c in candidates)
        est_cost = total_pages * 0.002
        print(f"\nTotal pages to OCR: {total_pages}")
        print(f"Estimated cost: ~${est_cost:.2f}")

    return candidates


def reextract_candidates(
    candidates: list[dict],
    limit: int | None,
    max_cost: float | None,
) -> dict:
    """
    Re-extract candidates using olmOCR with force_olmocr=True.

    Returns: {processed, improved, unchanged, errors, total_cost}
    """
    to_process = candidates[:limit] if limit else candidates

    # Backup DB before modifications
    backup_path = DB_PATH.with_suffix(f".db.bak-{datetime.now():%Y%m%d}")
    if not backup_path.exists():
        print(f"Backing up database to {backup_path.name}...")
        shutil.copy2(DB_PATH, backup_path)
    else:
        print(f"Backup already exists: {backup_path.name}")

    extractor = Extractor(skip_olmocr=False, force_olmocr=True, verbose=True)

    conn = get_connection()
    cursor = conn.cursor()

    processed = 0
    improved = 0
    unchanged = 0
    errors = 0

    for i, candidate in enumerate(to_process, 1):
        db_id = candidate["db_id"]
        letter_id = candidate["letter_id"]
        old_v3 = candidate["v3_score"]

        print(f"\n[{i}/{len(to_process)}] {letter_id} (v3={old_v3:.3f}, "
              f"method={candidate['extraction_method']}, year={candidate['year']})")

        # Fetch full DB row
        cursor.execute("SELECT * FROM documents WHERE id = ?", (db_id,))
        row = cursor.fetchone()
        if not row:
            print(f"  DB row not found, skipping")
            errors += 1
            continue

        doc_row = dict(row)

        # Reset status to pending so extractor will process it
        cursor.execute(
            "UPDATE documents SET extraction_status = 'pending' WHERE id = ?",
            (db_id,)
        )
        conn.commit()

        try:
            doc = extractor.process_document(doc_row)
            if doc:
                new_v3 = doc.extraction.quality_score

                if new_v3 > old_v3:
                    # Save improved result
                    json_path = extractor.save_document(doc)
                    update_extraction_status(
                        doc_id=db_id,
                        status="extracted",
                        method=doc.extraction.method,
                        quality=new_v3,
                        section_confidence=doc.sections.extraction_confidence,
                        json_path=str(json_path.relative_to(DATA_DIR.parent)),
                        needs_llm=(
                            doc.sections.extraction_confidence < 0.5
                            or not doc.sections.has_standard_format
                        ),
                    )
                    improved += 1
                    print(f"  IMPROVED: {old_v3:.3f} -> {new_v3:.3f} "
                          f"(method={doc.extraction.method})")
                else:
                    # No improvement — restore original status
                    cursor.execute(
                        "UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                        (db_id,)
                    )
                    conn.commit()
                    unchanged += 1
                    print(f"  UNCHANGED: {old_v3:.3f} -> {new_v3:.3f}, keeping original")
            else:
                # process_document returned None — restore status
                cursor.execute(
                    "UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                    (db_id,)
                )
                conn.commit()
                errors += 1
                print(f"  ERROR: process_document returned None")

        except Exception as e:
            # Restore status on any error
            cursor.execute(
                "UPDATE documents SET extraction_status = 'extracted' WHERE id = ?",
                (db_id,)
            )
            conn.commit()
            errors += 1
            print(f"  ERROR: {e}")

        processed += 1

        # Check cost limit
        current_cost = extractor.stats["total_olmocr_cost"]
        if max_cost is not None and current_cost >= max_cost:
            print(f"\nCost limit reached: ${current_cost:.4f} >= ${max_cost:.2f}")
            break

    conn.close()
    total_cost = extractor.stats["total_olmocr_cost"]

    return {
        "processed": processed,
        "improved": improved,
        "unchanged": unchanged,
        "errors": errors,
        "total_cost": total_cost,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Re-extract low-quality documents with olmOCR"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.80,
        help="v3 quality score cutoff (default: 0.80)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max documents to process"
    )
    parser.add_argument(
        "--max-cost", type=float, default=None,
        help="Halt if cumulative olmOCR cost exceeds this USD amount"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview candidates and cost estimate only"
    )
    parser.add_argument(
        "--skip-already-ocr", action="store_true",
        help="Skip documents already extracted with olmOCR"
    )
    args = parser.parse_args()

    # Check for API key (not needed for dry-run)
    if not args.dry_run and not os.environ.get("DEEPINFRA_API_KEY"):
        print("Error: DEEPINFRA_API_KEY not set. Run: source .env")
        sys.exit(1)

    # Phase 1: Discovery
    candidates = discover_candidates(args.threshold, args.skip_already_ocr)

    if not candidates:
        print("\nNo candidates found below threshold. Nothing to do.")
        return

    if args.dry_run:
        print(f"\nDRY RUN — worst 20 candidates:\n")
        for c in candidates[:20]:
            print(f"  {c['letter_id']:20s}  v3={c['v3_score']:.3f}  "
                  f"db={c['old_db_score']:.2f}  method={c['extraction_method']:12s}  "
                  f"year={c['year']}  pages={c['page_count']}")
        if len(candidates) > 20:
            print(f"  ... and {len(candidates) - 20} more")
        return

    # Phase 2: Re-extraction
    print(f"\n{'=' * 60}")
    print(f"STARTING RE-EXTRACTION")
    print(f"{'=' * 60}")
    effective_limit = args.limit or len(candidates)
    print(f"  Candidates: {len(candidates)}")
    print(f"  Processing: {min(effective_limit, len(candidates))}")
    if args.max_cost:
        print(f"  Cost limit: ${args.max_cost:.2f}")
    print()

    results = reextract_candidates(candidates, args.limit, args.max_cost)

    print(f"\n{'=' * 60}")
    print(f"RE-EXTRACTION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Processed:  {results['processed']}")
    print(f"  Improved:   {results['improved']}")
    print(f"  Unchanged:  {results['unchanged']}")
    print(f"  Errors:     {results['errors']}")
    print(f"  olmOCR cost: ${results['total_cost']:.4f}")


if __name__ == "__main__":
    main()
