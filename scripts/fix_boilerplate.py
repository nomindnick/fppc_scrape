#!/usr/bin/env python3
"""
Clean boilerplate text from section fields and qa_text in existing JSON documents.

Applies the same clean_section_content() function used in production to all
section fields (question, conclusion, facts, analysis) and rebuilds qa_text.

This is a one-off fix for ~145 documents where boilerplate leaked through
before the production code was fixed to apply cleaning at embedding time.

Usage:
    python scripts/fix_boilerplate.py --dry-run    # Preview changes
    python scripts/fix_boilerplate.py              # Apply changes
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.section_parser import BOILERPLATE_PATTERNS, clean_section_content

EXTRACTED_DIR = "data/extracted"

# Compile patterns once for checking
COMPILED_PATTERNS = []
for i, pat in enumerate(BOILERPLATE_PATTERNS):
    try:
        compiled = re.compile(pat, re.IGNORECASE | re.DOTALL)
        COMPILED_PATTERNS.append((f"pattern_{i}", compiled))
    except re.error:
        pass


def has_boilerplate(text: str) -> list[str]:
    """Check if text contains any boilerplate pattern. Returns list of pattern names."""
    if not text:
        return []
    hits = []
    for name, compiled in COMPILED_PATTERNS:
        if compiled.search(text):
            hits.append(name)
    return hits


def rebuild_qa_text(doc: dict) -> str:
    """Rebuild qa_text from cleaned sections, matching extractor logic."""
    sections = doc.get("sections", {})
    parts = []

    # Prefer extracted Q/C, fall back to synthetic
    q = sections.get("question") or sections.get("question_synthetic")
    c = sections.get("conclusion") or sections.get("conclusion_synthetic")

    if q:
        parts.append(f"QUESTION: {q}")
    if c:
        parts.append(f"CONCLUSION: {c}")

    if parts:
        return "\n\n".join(parts)

    # Fallback: keep existing first_500_words
    return doc.get("embedding", {}).get("first_500_words", "")


def main():
    parser = argparse.ArgumentParser(description="Clean boilerplate from FPPC corpus")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    json_files = sorted(glob.glob(os.path.join(EXTRACTED_DIR, "**", "*.json"), recursive=True))
    print(f"Scanning {len(json_files)} JSON files for boilerplate...\n")

    total_fixed = 0
    total_scanned = 0
    field_fix_counts = {"question": 0, "conclusion": 0, "facts": 0, "analysis": 0, "qa_text": 0}

    for path in json_files:
        total_scanned += 1
        try:
            with open(path) as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            continue

        doc_id = doc.get("id", os.path.basename(path))
        sections = doc.get("sections", {})
        embedding = doc.get("embedding", {})
        changed = False
        fields_cleaned = []

        # Clean section fields
        for field in ["question", "conclusion", "facts", "analysis"]:
            text = sections.get(field)
            if not text:
                continue

            cleaned = clean_section_content(text)
            if cleaned != text:
                sections[field] = cleaned
                changed = True
                fields_cleaned.append(field)
                field_fix_counts[field] += 1

        # Rebuild qa_text if sections changed, or directly clean qa_text if it has boilerplate
        qa_text = embedding.get("qa_text", "")
        if changed:
            # Sections were cleaned, so rebuild qa_text from cleaned sections
            new_qa_text = rebuild_qa_text(doc)
            if new_qa_text != qa_text:
                embedding["qa_text"] = new_qa_text
                if "qa_text" not in fields_cleaned:
                    fields_cleaned.append("qa_text")
                    field_fix_counts["qa_text"] += 1
        elif has_boilerplate(qa_text):
            # qa_text has boilerplate (likely from first_500_words fallback) — clean it directly
            cleaned_qa = clean_section_content(qa_text)
            if cleaned_qa != qa_text:
                embedding["qa_text"] = cleaned_qa
                fields_cleaned.append("qa_text")
                field_fix_counts["qa_text"] += 1
                changed = True

        if changed:
            total_fixed += 1
            fields_str = ", ".join(fields_cleaned)
            print(f"  {'[DRY RUN] ' if args.dry_run else ''}{doc_id}: cleaned {fields_str}")

            if not args.dry_run:
                with open(path, "w") as f:
                    json.dump(doc, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN ' if args.dry_run else ''}SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Scanned: {total_scanned}")
    print(f"  Fixed: {total_fixed}")
    print(f"  By field:")
    for field, count in sorted(field_fix_counts.items()):
        if count > 0:
            print(f"    {field}: {count}")


if __name__ == "__main__":
    main()
