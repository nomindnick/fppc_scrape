#!/usr/bin/env python3
"""
Fix duplicate records in the FPPC corpus.

Resolves 39 duplicate groups found by qa_corpus_validate.py:
- 82AXXX vs A-82-XXX pairs (same SHA256): keep A-82-XXX, delete 82AXXX
- UNK-82-* vs 82A* pairs (same SHA256): keep 82A*, delete UNK-82-*
- Format variants (88-367/88367, 17-082/17082): keep dashed form
- Amendment pairs (*a): keep both, add duplicate_note
- A-23-096 vs A-23-098: keep A-23-096, delete A-23-098 (crawl error)
- 81A161 + 82A005 + A-82-005 triple: keep 81A161 + A-82-005, delete 82A005

Usage:
    python scripts/fix_duplicates.py --dry-run    # Preview changes
    python scripts/fix_duplicates.py              # Apply changes
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.db import get_connection

EXTRACTED_DIR = "data/extracted"


def find_json_path(doc_id: str) -> str | None:
    """Find the JSON file for a document ID by searching year directories."""
    for year_dir in sorted(os.listdir(EXTRACTED_DIR)):
        year_path = os.path.join(EXTRACTED_DIR, year_dir)
        if not os.path.isdir(year_path):
            continue
        safe_id = re.sub(r'[^\w\-.]', '_', doc_id)
        json_path = os.path.join(year_path, f"{safe_id}.json")
        if os.path.exists(json_path):
            return json_path
    return None


def classify_group(ids: list[str]) -> dict:
    """
    Classify a duplicate group and determine resolution.

    Returns:
        {keep: [ids_to_keep], delete: [ids_to_delete], action: str, note: str}
    """
    ids_set = set(ids)

    # Special case: A-23-096 vs A-23-098 (crawl error — both point to same PDF)
    if "A-23-096" in ids_set and "A-23-098" in ids_set:
        return {
            "keep": ["A-23-096"],
            "delete": ["A-23-098"],
            "action": "delete_crawl_error",
            "note": "Both point to 23096.pdf — A-23-098 is a crawl error",
        }

    # Special case: 81A161 + 82A005 + A-82-005 triple
    if "81A161" in ids_set and "82A005" in ids_set and "A-82-005" in ids_set:
        return {
            "keep": ["81A161", "A-82-005"],
            "delete": ["82A005"],
            "action": "delete_triple",
            "note": "82A005 duplicates A-82-005; 81A161 is a different letter ID",
        }

    # Amendment pairs: ID vs ID + 'a' suffix
    if len(ids) == 2:
        for a, b in [(ids[0], ids[1]), (ids[1], ids[0])]:
            if b == a + "a":
                return {
                    "keep": ids,
                    "delete": [],
                    "action": "annotate_amendment",
                    "note": "Amendment PDF identical to parent — FPPC replaced original",
                }

    # 82AXXX vs A-82-XXX pairs
    if len(ids) == 2:
        for a, b in [(ids[0], ids[1]), (ids[1], ids[0])]:
            m_old = re.match(r'^82A(\d{3,4})$', a)
            m_new = re.match(r'^A-82-(\d{3,4})$', b)
            if m_old and m_new and m_old.group(1) == m_new.group(1):
                return {
                    "keep": [b],
                    "delete": [a],
                    "action": "delete_old_format",
                    "note": f"Same SHA256, keeping modern format {b}",
                }

    # UNK-82-* vs 82A* pairs
    if len(ids) == 2:
        for a, b in [(ids[0], ids[1]), (ids[1], ids[0])]:
            if a.startswith("UNK-82-") and b.startswith("82A"):
                return {
                    "keep": [b],
                    "delete": [a],
                    "action": "delete_unk",
                    "note": f"Same SHA256, keeping named ID {b}",
                }

    # 82A025 vs A-81-925 (cross-year old/modern format)
    if len(ids) == 2:
        for a, b in [(ids[0], ids[1]), (ids[1], ids[0])]:
            m_old = re.match(r'^82A\d{3,4}$', a)
            m_new = re.match(r'^A-\d{2}-\d{3,4}$', b)
            if m_old and m_new:
                return {
                    "keep": [b],
                    "delete": [a],
                    "action": "delete_old_format",
                    "note": f"Same SHA256, keeping modern format {b}",
                }

    # Format variants: dashed vs compact (e.g., 88-367 vs 88367)
    if len(ids) == 2:
        for a, b in [(ids[0], ids[1]), (ids[1], ids[0])]:
            m_dash = re.match(r'^(\d{2})-(\d{3,4})$', a)
            m_compact = re.match(r'^(\d{2})(\d{3,4})$', b)
            if m_dash and m_compact:
                if m_dash.group(1) == m_compact.group(1) and m_dash.group(2) == m_compact.group(2):
                    return {
                        "keep": [a],
                        "delete": [b],
                        "action": "delete_compact_format",
                        "note": f"Same SHA256, keeping dashed form {a}",
                    }

    # Fallback: keep the first (sorted) ID, delete the rest
    sorted_ids = sorted(ids)
    return {
        "keep": [sorted_ids[0]],
        "delete": sorted_ids[1:],
        "action": "delete_unknown",
        "note": f"Unclassified duplicate group, keeping {sorted_ids[0]}",
    }


def delete_document(doc_id: str, dry_run: bool) -> bool:
    """Delete a document's JSON file and DB row."""
    json_path = find_json_path(doc_id)

    if json_path and os.path.exists(json_path):
        if not dry_run:
            os.remove(json_path)
        print(f"    {'[DRY RUN] Would delete' if dry_run else 'Deleted'} JSON: {json_path}")
    else:
        print(f"    JSON file not found for {doc_id}")

    # Find and delete DB row by letter_id or json_path
    conn = get_connection()
    cursor = conn.cursor()

    # Try matching by letter_id
    cursor.execute("SELECT id, letter_id, json_path FROM documents WHERE letter_id = ?", (doc_id,))
    rows = cursor.fetchall()

    if not rows and json_path:
        # Try matching by json_path
        rel_path = json_path
        cursor.execute("SELECT id, letter_id, json_path FROM documents WHERE json_path = ?",
                        (rel_path,))
        rows = cursor.fetchall()

    if rows:
        for row in rows:
            if not dry_run:
                cursor.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
            print(f"    {'[DRY RUN] Would delete' if dry_run else 'Deleted'} "
                  f"DB row: id={row['id']}, letter_id={row['letter_id']}")
    else:
        print(f"    DB row not found for {doc_id}")

    if not dry_run:
        conn.commit()
    conn.close()
    return bool(rows)


def annotate_amendment(ids: list[str], note: str, dry_run: bool) -> None:
    """Add a duplicate_note to amendment pair JSON files."""
    for doc_id in ids:
        json_path = find_json_path(doc_id)
        if not json_path:
            print(f"    JSON not found for {doc_id}, skipping annotation")
            continue

        with open(json_path) as f:
            doc = json.load(f)

        # Add note to parsing_notes
        existing_notes = doc.get("sections", {}).get("parsing_notes", "") or ""
        new_note = f"duplicate_note: {note}"
        if new_note in existing_notes:
            print(f"    {doc_id} already annotated, skipping")
            continue

        if existing_notes:
            doc["sections"]["parsing_notes"] = f"{existing_notes}; {new_note}"
        else:
            doc["sections"]["parsing_notes"] = new_note

        if not dry_run:
            with open(json_path, "w") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)

        print(f"    {'[DRY RUN] Would annotate' if dry_run else 'Annotated'} {doc_id}")


def main():
    parser = argparse.ArgumentParser(description="Fix duplicate records in FPPC corpus")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    # Load duplicate groups from validation report
    json_report_path = "data/qa_reports/corpus_validation.json"
    if not os.path.exists(json_report_path):
        print(f"Error: {json_report_path} not found. Run qa_corpus_validate.py --json first.")
        sys.exit(1)

    with open(json_report_path) as f:
        data = json.load(f)

    dup_groups = data["checks"]["3_duplicates"]["details"]
    print(f"Found {len(dup_groups)} duplicate groups to resolve\n")

    total_deleted = 0
    total_annotated = 0
    total_errors = 0

    for hash_key, ids in sorted(dup_groups.items()):
        resolution = classify_group(ids)
        print(f"Group {hash_key[:8]}: {ids}")
        print(f"  Action: {resolution['action']} — {resolution['note']}")
        print(f"  Keep: {resolution['keep']}, Delete: {resolution['delete']}")

        if resolution["action"] == "annotate_amendment":
            annotate_amendment(ids, resolution["note"], args.dry_run)
            total_annotated += len(ids)
        else:
            for doc_id in resolution["delete"]:
                success = delete_document(doc_id, args.dry_run)
                if success:
                    total_deleted += 1
                else:
                    total_errors += 1

        print()

    print("=" * 60)
    print(f"{'DRY RUN ' if args.dry_run else ''}SUMMARY")
    print("=" * 60)
    print(f"  Groups processed: {len(dup_groups)}")
    print(f"  Documents deleted: {total_deleted}")
    print(f"  Documents annotated: {total_annotated}")
    print(f"  Errors: {total_errors}")


if __name__ == "__main__":
    main()
