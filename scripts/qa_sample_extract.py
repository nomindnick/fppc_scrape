"""
QA sampling and extraction script.

Selects a stratified random sample of documents and runs the extraction pipeline.
Tracks which documents have been sampled to avoid re-sampling across iterations.

Usage:
    python scripts/qa_sample_extract.py --iteration 1
    python scripts/qa_sample_extract.py --iteration 2
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.config import DATA_DIR, PROJECT_ROOT
from scraper.db import get_connection, add_extraction_columns, update_extraction_status
from scraper.extractor import Extractor

QA_DIR = DATA_DIR / "qa_reports"
EXTRACTED_DIR = DATA_DIR / "extracted"
SAMPLED_IDS_FILE = QA_DIR / "sampled_doc_ids.json"

# Stratified sampling distribution (total: 100)
ERA_DISTRIBUTION = [
    (1975, 1985, 10),   # Early era
    (1986, 1995, 15),   # Transitional era
    (1996, 2005, 25),   # Standard format emerging
    (2006, 2015, 25),   # Modern format
    (2016, 2025, 25),   # Current format
]


def load_sampled_ids() -> set:
    """Load previously sampled document IDs."""
    if SAMPLED_IDS_FILE.exists():
        data = json.loads(SAMPLED_IDS_FILE.read_text())
        return set(data.get("all_ids", []))
    return set()


def save_sampled_ids(all_ids: set, iteration_map: dict):
    """Save sampled document IDs."""
    data = {
        "all_ids": sorted(all_ids),
        "by_iteration": iteration_map,
    }
    SAMPLED_IDS_FILE.write_text(json.dumps(data, indent=2))


def sample_documents(iteration: int) -> list[dict]:
    """Select stratified random sample, excluding previously sampled docs."""
    previously_sampled = load_sampled_ids()
    conn = get_connection()
    cursor = conn.cursor()

    samples = []
    for start_year, end_year, count in ERA_DISTRIBUTION:
        # Build exclusion list for SQL
        placeholders = ",".join("?" * len(previously_sampled)) if previously_sampled else "0"
        exclude_params = list(previously_sampled) if previously_sampled else []

        query = f"""
            SELECT * FROM documents
            WHERE download_status = 'downloaded'
            AND year_tag >= ? AND year_tag <= ?
            AND id NOT IN ({placeholders})
            ORDER BY RANDOM()
            LIMIT ?
        """
        params = [start_year, end_year] + exclude_params + [count]
        cursor.execute(query, params)
        era_samples = [dict(row) for row in cursor.fetchall()]
        samples.extend(era_samples)
        print(f"  {start_year}-{end_year}: sampled {len(era_samples)}/{count}")

    conn.close()
    return samples


def extract_documents(samples: list[dict], iteration: int) -> list[dict]:
    """Run the extraction pipeline on sampled documents."""
    add_extraction_columns()
    extractor = Extractor(skip_olmocr=True, verbose=True)

    results = []
    for i, doc_row in enumerate(samples, 1):
        doc_id = doc_row["id"]
        letter_id = doc_row.get("letter_id") or f"doc#{doc_id}"
        year = doc_row.get("year_tag", 0)

        print(f"\n[{i}/{len(samples)}] {letter_id} ({year})")

        try:
            doc = extractor.process_document(doc_row)
            if doc:
                json_path = extractor.save_document(doc)

                # Update DB
                update_extraction_status(
                    doc_id=doc_id,
                    status="extracted",
                    method=doc.extraction.method,
                    quality=doc.extraction.quality_score,
                    section_confidence=doc.sections.extraction_confidence,
                    json_path=str(json_path.relative_to(DATA_DIR.parent)),
                    needs_llm=doc.sections.extraction_confidence < 0.5 or not doc.sections.has_standard_format,
                )

                results.append({
                    "doc_id": doc_id,
                    "letter_id": doc.id,
                    "year": year,
                    "json_path": str(json_path),
                    "quality_score": doc.extraction.quality_score,
                    "section_confidence": doc.sections.extraction_confidence,
                    "has_standard_format": doc.sections.has_standard_format,
                    "has_question": doc.sections.question is not None,
                    "has_conclusion": doc.sections.conclusion is not None,
                    "has_facts": doc.sections.facts is not None,
                    "has_analysis": doc.sections.analysis is not None,
                    "extraction_method": doc.extraction.method,
                    "document_type": doc.parsed.document_type,
                    "date_parsed": doc.parsed.date is not None,
                    "gov_code_count": len(doc.citations.government_code),
                    "regulation_count": len(doc.citations.regulations),
                    "prior_opinion_count": len(doc.citations.prior_opinions),
                    "word_count": doc.extraction.word_count,
                    "page_count": doc.extraction.page_count,
                    "parsing_notes": doc.sections.parsing_notes,
                    "status": "success",
                })
                print(f"  OK: quality={doc.extraction.quality_score:.2f}, "
                      f"confidence={doc.sections.extraction_confidence:.2f}, "
                      f"Q={doc.sections.question is not None}, C={doc.sections.conclusion is not None}")
            else:
                results.append({
                    "doc_id": doc_id,
                    "letter_id": letter_id,
                    "year": year,
                    "status": "failed",
                    "error": "process_document returned None",
                })
                print(f"  FAILED: returned None")

        except Exception as e:
            results.append({
                "doc_id": doc_id,
                "letter_id": letter_id,
                "year": year,
                "status": "error",
                "error": str(e),
            })
            print(f"  ERROR: {e}")

    return results


def main():
    parser = argparse.ArgumentParser(description="QA sample and extract")
    parser.add_argument("--iteration", type=int, required=True, help="Iteration number (1-5)")
    args = parser.parse_args()

    iteration = args.iteration
    print(f"=" * 60)
    print(f"QA ITERATION {iteration}: Sampling and Extracting")
    print(f"=" * 60)

    # Sample
    print(f"\nStep 1: Sampling 100 stratified documents...")
    samples = sample_documents(iteration)
    print(f"Total sampled: {len(samples)}")

    # Track sampled IDs
    all_sampled = load_sampled_ids()
    new_ids = [s["id"] for s in samples]
    all_sampled.update(new_ids)

    # Load existing iteration map
    if SAMPLED_IDS_FILE.exists():
        existing = json.loads(SAMPLED_IDS_FILE.read_text())
        iteration_map = existing.get("by_iteration", {})
    else:
        iteration_map = {}
    iteration_map[str(iteration)] = new_ids
    save_sampled_ids(all_sampled, iteration_map)

    # Extract
    print(f"\nStep 2: Running extraction pipeline...")
    results = extract_documents(samples, iteration)

    # Save results
    results_file = QA_DIR / f"iteration_{iteration}_extraction_results.json"
    results_file.write_text(json.dumps(results, indent=2))

    # Summary
    success = [r for r in results if r.get("status") == "success"]
    failed = [r for r in results if r.get("status") != "success"]

    print(f"\n{'=' * 60}")
    print(f"EXTRACTION SUMMARY - Iteration {iteration}")
    print(f"{'=' * 60}")
    print(f"Total: {len(results)}, Success: {len(success)}, Failed: {len(failed)}")

    if success:
        has_q = sum(1 for r in success if r.get("has_question"))
        has_c = sum(1 for r in success if r.get("has_conclusion"))
        has_date = sum(1 for r in success if r.get("date_parsed"))
        avg_quality = sum(r.get("quality_score", 0) for r in success) / len(success)
        avg_confidence = sum(r.get("section_confidence", 0) for r in success) / len(success)

        print(f"\nHas QUESTION: {has_q}/{len(success)} ({100*has_q/len(success):.1f}%)")
        print(f"Has CONCLUSION: {has_c}/{len(success)} ({100*has_c/len(success):.1f}%)")
        print(f"Has date: {has_date}/{len(success)} ({100*has_date/len(success):.1f}%)")
        print(f"Avg quality: {avg_quality:.3f}")
        print(f"Avg confidence: {avg_confidence:.3f}")

        # By era
        print(f"\nBy era:")
        for start, end, _ in ERA_DISTRIBUTION:
            era_docs = [r for r in success if start <= r.get("year", 0) <= end]
            if era_docs:
                era_q = sum(1 for r in era_docs if r.get("has_question"))
                era_c = sum(1 for r in era_docs if r.get("has_conclusion"))
                era_d = sum(1 for r in era_docs if r.get("date_parsed"))
                print(f"  {start}-{end}: {len(era_docs)} docs, "
                      f"Q={era_q}/{len(era_docs)}, C={era_c}/{len(era_docs)}, "
                      f"date={era_d}/{len(era_docs)}")

    if failed:
        print(f"\nFailed documents:")
        for f in failed:
            print(f"  {f.get('letter_id')}: {f.get('error', 'unknown')}")

    print(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    main()
