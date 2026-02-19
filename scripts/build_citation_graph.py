#!/usr/bin/env python3
"""
Build citation graph: populate cited_by fields and document known gaps.

Two outputs:
1. Populate citations.cited_by in every JSON file with a reverse index
   of which documents cite each document.
2. Generate data/known_gaps.json listing citation targets not in the corpus,
   sorted by citation count.

Uses the same ID normalization logic as qa_corpus_validate.py to match
citation references across different ID formats (A-82-060, 82A060, 82-060, etc).

Usage:
    python scripts/build_citation_graph.py --dry-run   # Preview without writing
    python scripts/build_citation_graph.py              # Build graph and write files
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXTRACTED_DIR = "data/extracted"
KNOWN_GAPS_PATH = "data/known_gaps.json"


def build_id_lookup(known_ids: set[str]) -> dict[str, str]:
    """
    Build a mapping from all ID variants to canonical doc_id.

    Unlike qa_corpus_validate.py's version (which returns a flat set),
    this returns a dict mapping each variant to the canonical ID stored
    in the corpus. This allows us to normalize citation targets.
    """
    variant_to_canonical = {}

    for kid in known_ids:
        # The canonical form is always the doc_id as stored
        variant_to_canonical[kid] = kid

        # A-82-060 → 82-060, 82060, A82060
        m = re.match(r'^([AIM])-(\d{2})-(\d{3,4})$', kid)
        if m:
            prefix, yy, nnn = m.group(1), m.group(2), m.group(3)
            for v in [f"{yy}-{nnn}", f"{yy}{nnn}", f"{prefix}{yy}{nnn}"]:
                variant_to_canonical.setdefault(v, kid)
            continue

        # 92-289 → A-92-289, I-92-289, M-92-289, 92289
        m = re.match(r'^(\d{2})-(\d{3,4})$', kid)
        if m:
            yy, nnn = m.group(1), m.group(2)
            for prefix in ["A", "I", "M"]:
                variant_to_canonical.setdefault(f"{prefix}-{yy}-{nnn}", kid)
            variant_to_canonical.setdefault(f"{yy}{nnn}", kid)
            continue

        # 88367 → A-88-367, I-88-367, M-88-367, 88-367
        m = re.match(r'^(\d{2})(\d{3,4})$', kid)
        if m:
            yy, nnn = m.group(1), m.group(2)
            for prefix in ["A", "I", "M"]:
                variant_to_canonical.setdefault(f"{prefix}-{yy}-{nnn}", kid)
            variant_to_canonical.setdefault(f"{yy}-{nnn}", kid)
            continue

        # 82A060 → A-82-060, 82-060
        m = re.match(r'^(\d{2})([AIM])(\d{3,4})$', kid)
        if m:
            yy, prefix, nnn = m.group(1), m.group(2), m.group(3)
            variant_to_canonical.setdefault(f"{prefix}-{yy}-{nnn}", kid)
            variant_to_canonical.setdefault(f"{yy}-{nnn}", kid)
            continue

    return variant_to_canonical


def main():
    parser = argparse.ArgumentParser(description="Build citation graph for FPPC corpus")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    # Phase 1: Load all documents and build ID index
    print("Loading documents...")
    json_files = sorted(glob.glob(os.path.join(EXTRACTED_DIR, "**", "*.json"), recursive=True))

    documents = {}  # doc_id → (path, doc_data)
    known_ids = set()

    for path in json_files:
        try:
            with open(path) as f:
                doc = json.load(f)
            doc_id = doc.get("id") or os.path.basename(path).replace(".json", "")
            documents[doc_id] = (path, doc)
            known_ids.add(doc_id)
        except (json.JSONDecodeError, OSError):
            continue

    print(f"  Loaded {len(documents)} documents")

    # Phase 2: Build ID variant lookup
    variant_to_canonical = build_id_lookup(known_ids)
    print(f"  Built ID lookup with {len(variant_to_canonical)} variants for {len(known_ids)} canonical IDs")

    # Phase 3: Build citation graph (forward and reverse)
    print("Building citation graph...")

    # cited_by[canonical_target_id] = set of citing doc_ids
    cited_by = defaultdict(set)
    # dangling[cited_id_as_written] = set of citing doc_ids
    dangling = defaultdict(set)

    total_edges = 0
    total_resolved = 0
    total_dangling = 0

    for doc_id, (path, doc) in documents.items():
        prior_opinions = doc.get("citations", {}).get("prior_opinions", [])
        for cited_id in prior_opinions:
            total_edges += 1

            # Try to resolve to canonical ID
            canonical = variant_to_canonical.get(cited_id)
            if canonical:
                cited_by[canonical].add(doc_id)
                total_resolved += 1
            else:
                dangling[cited_id].add(doc_id)
                total_dangling += 1

    print(f"  Total citation edges: {total_edges}")
    print(f"  Resolved to corpus: {total_resolved} ({total_resolved/max(total_edges,1)*100:.1f}%)")
    print(f"  Dangling (not in corpus): {total_dangling} ({len(dangling)} unique targets)")

    # Phase 4: Write cited_by into each document
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Writing cited_by to JSON files...")

    docs_updated = 0
    docs_unchanged = 0

    for doc_id, (path, doc) in documents.items():
        old_cited_by = doc.get("citations", {}).get("cited_by", [])
        new_cited_by = sorted(cited_by.get(doc_id, set()))

        if old_cited_by == new_cited_by:
            docs_unchanged += 1
            continue

        doc["citations"]["cited_by"] = new_cited_by
        docs_updated += 1

        if not args.dry_run:
            with open(path, "w") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)

    print(f"  Updated: {docs_updated} docs")
    print(f"  Unchanged: {docs_unchanged} docs")

    # Phase 5: Generate known_gaps.json
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Generating {KNOWN_GAPS_PATH}...")

    gaps = []
    for target_id, citing_docs in sorted(dangling.items(), key=lambda x: -len(x[1])):
        gaps.append({
            "id": target_id,
            "cited_by_count": len(citing_docs),
            "example_citing_docs": sorted(citing_docs)[:10],
        })

    gaps_data = {
        "description": "FPPC advice letters cited by the corpus but not found as documents",
        "total_gaps": len(gaps),
        "total_dangling_edges": total_dangling,
        "gaps": gaps,
    }

    if not args.dry_run:
        os.makedirs(os.path.dirname(KNOWN_GAPS_PATH), exist_ok=True)
        with open(KNOWN_GAPS_PATH, "w") as f:
            json.dump(gaps_data, f, indent=2, ensure_ascii=False)
        print(f"  Written: {len(gaps)} gap entries")
    else:
        print(f"  Would write: {len(gaps)} gap entries")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN ' if args.dry_run else ''}CITATION GRAPH SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Corpus documents: {len(documents)}")
    print(f"  Documents with cited_by: {sum(1 for d in cited_by.values() if d)}")
    print(f"  Most-cited document: ", end="")
    if cited_by:
        most = max(cited_by.items(), key=lambda x: len(x[1]))
        print(f"{most[0]} ({len(most[1])} citations)")
    else:
        print("(none)")
    print(f"  Known gaps: {len(gaps)} unique IDs")
    if gaps:
        print(f"  Top 5 gaps:")
        for g in gaps[:5]:
            print(f"    {g['id']}: cited by {g['cited_by_count']} docs")


if __name__ == "__main__":
    main()
