#!/usr/bin/env python3
"""
Fix date/year mismatches in the FPPC corpus.

Resolves 88 documents where parsed.date year doesn't match the file's year directory.
Rules:
  1. If day > 31 or month > 12 → null parsed.date (impossible date)
  2. If |parsed_year - file_year| > 1 and parsed_year > file_year + 1 → null parsed.date
     (future-dated relative to file year suggests OCR garble)
  3. If parsed_year < file_year - 1 → leave as-is (legitimate: FPPC compilation year
     often differs from letter date, especially pre-1985)

Keeps parsed.date_raw intact for human review.

Usage:
    python scripts/fix_dates.py --dry-run    # Preview changes
    python scripts/fix_dates.py              # Apply changes
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXTRACTED_DIR = "data/extracted"


def load_mismatches() -> list[dict]:
    """Load date mismatches from the validation report."""
    json_report = "data/qa_reports/corpus_validation.json"
    if not os.path.exists(json_report):
        print(f"Error: {json_report} not found. Run qa_corpus_validate.py --json first.")
        sys.exit(1)

    with open(json_report) as f:
        data = json.load(f)

    return data["checks"]["2_date_year"]["details"]


def find_json_path(doc_id: str) -> str | None:
    """Find JSON file for a doc ID."""
    import re
    for year_dir in sorted(os.listdir(EXTRACTED_DIR)):
        year_path = os.path.join(EXTRACTED_DIR, year_dir)
        if not os.path.isdir(year_path):
            continue
        safe_id = re.sub(r'[^\w\-.]', '_', doc_id)
        json_path = os.path.join(year_path, f"{safe_id}.json")
        if os.path.exists(json_path):
            return json_path
    return None


def should_null_date(mismatch: dict) -> tuple[bool, str]:
    """
    Determine if a date should be nulled.

    Returns:
        (should_null, reason)
    """
    parsed_date = mismatch["parsed_date"]
    file_year = mismatch["file_year"]
    parsed_year = mismatch["parsed_year"]
    delta = mismatch["delta"]

    # Rule 1: Impossible date (day > 31 or month > 12)
    try:
        parts = parsed_date.split("-")
        month = int(parts[1])
        day = int(parts[2])
        if month > 12 or day > 31 or month < 1 or day < 1:
            return True, f"Impossible date: month={month}, day={day}"
    except (ValueError, IndexError):
        return True, f"Malformed date: {parsed_date}"

    # Rule 2: Parsed year is significantly AFTER file year (future-dated)
    # This indicates OCR garble (e.g., 2001→2007 batch, 1980→2013)
    if delta > 1:
        return True, f"Parsed year {parsed_year} is {delta} years after file year {file_year}"

    # Rule 3: Parsed year before file year — leave as-is
    # Pre-1985 FPPC compiled old letters into later year directories
    if delta < -1:
        return False, f"Legitimate: letter from {parsed_year} compiled in {file_year} directory"

    return False, f"Within tolerance: delta={delta}"


def main():
    parser = argparse.ArgumentParser(description="Fix date/year mismatches in FPPC corpus")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    mismatches = load_mismatches()
    print(f"Found {len(mismatches)} date/year mismatches\n")

    nulled = 0
    skipped = 0
    errors = 0
    not_found = 0

    for m in mismatches:
        doc_id = m["doc_id"]
        should_null, reason = should_null_date(m)

        if not should_null:
            print(f"  SKIP {doc_id:20s}  {m['parsed_date']}  file={m['file_year']}  "
                  f"delta={m['delta']:+d}  — {reason}")
            skipped += 1
            continue

        json_path = find_json_path(doc_id)
        if not json_path:
            print(f"  NOT FOUND {doc_id:20s}  — JSON file missing (may have been deleted)")
            not_found += 1
            continue

        # Load and fix
        with open(json_path) as f:
            doc = json.load(f)

        old_date = doc.get("parsed", {}).get("date")
        if old_date is None:
            print(f"  ALREADY NULL {doc_id:20s}")
            skipped += 1
            continue

        # Null the date
        doc["parsed"]["date"] = None

        # Add note to parsing_notes
        existing_notes = doc.get("sections", {}).get("parsing_notes", "") or ""
        new_note = f"Date cleared: parsed {old_date} but file year is {m['file_year']}"
        if new_note not in existing_notes:
            if existing_notes:
                doc["sections"]["parsing_notes"] = f"{existing_notes}; {new_note}"
            else:
                doc["sections"]["parsing_notes"] = new_note

        if not args.dry_run:
            with open(json_path, "w") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)

        print(f"  {'[DRY RUN] ' if args.dry_run else ''}NULL {doc_id:20s}  "
              f"{old_date}  — {reason}")
        nulled += 1

    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN ' if args.dry_run else ''}SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total mismatches: {len(mismatches)}")
    print(f"  Dates nulled: {nulled}")
    print(f"  Skipped (legitimate): {skipped}")
    print(f"  Not found (deleted): {not_found}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    main()
