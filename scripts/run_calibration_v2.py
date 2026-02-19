#!/usr/bin/env python3
"""
Calibration v2: Re-run extraction on 50 fresh documents after 8 bug fixes.

Selects 10 random documents per era, excluding the previous calibration sample,
extracts them, and saves structured results for review.
"""

import json
import os
import sys
import random

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.db import get_connection
from scraper.extractor import Extractor, EXTRACTED_DIR
from scraper.schema import to_json

# Previous sample IDs to exclude
PREV_SAMPLE_FILE = "data/calibration_sample_ids.json"

ERA_BOUNDARIES = [
    ("1975-1985", 1975, 1985),
    ("1986-1995", 1986, 1995),
    ("1996-2005", 1996, 2005),
    ("2006-2015", 2006, 2015),
    ("2016-2025", 2016, 2025),
]

PER_ERA = 10


def load_previous_sample_ids():
    """Load document IDs from the previous calibration to exclude them."""
    try:
        with open(PREV_SAMPLE_FILE) as f:
            data = json.load(f)
        # Extract the db IDs (first element of each tuple)
        return {item[0] for item in data}
    except FileNotFoundError:
        return set()


def select_sample(exclude_ids: set[int]) -> list[dict]:
    """Select 10 random documents per era, excluding previous sample."""
    conn = get_connection()
    cursor = conn.cursor()

    all_docs = []
    era_counts = {}

    for era_name, start_year, end_year in ERA_BOUNDARIES:
        # Get candidates
        cursor.execute("""
            SELECT * FROM documents
            WHERE download_status = 'downloaded'
            AND year_tag >= ? AND year_tag <= ?
            ORDER BY RANDOM()
            LIMIT 200
        """, (start_year, end_year))

        candidates = [dict(row) for row in cursor.fetchall()]

        # Filter out previous sample
        candidates = [c for c in candidates if c["id"] not in exclude_ids]

        # Take first PER_ERA
        selected = candidates[:PER_ERA]
        all_docs.extend(selected)
        era_counts[era_name] = len(selected)
        print(f"  {era_name}: selected {len(selected)} documents (from {len(candidates)} candidates)")

    conn.close()
    return all_docs


def run_extraction(docs: list[dict]) -> list[dict]:
    """Run extraction on all documents and collect results."""
    extractor = Extractor(skip_olmocr=True, verbose=True)

    results = []

    for i, doc_row in enumerate(docs, 1):
        doc_id = doc_row["id"]
        letter_id = doc_row.get("letter_id")
        year = doc_row.get("year_tag")

        print(f"\n[{i}/{len(docs)}] Processing doc#{doc_id} ({letter_id or 'no-id'}, {year})...")

        result_entry = {
            "db_id": doc_id,
            "letter_id": letter_id,
            "year": year,
            "pdf_url": doc_row.get("pdf_url", ""),
            "era": None,
            "status": "error",
            "error": None,
            # Extraction results (populated on success)
            "extracted_id": None,
            "quality_score": None,
            "section_confidence": None,
            "has_standard_format": None,
            "question_found": False,
            "conclusion_found": False,
            "facts_found": False,
            "analysis_found": False,
            "date_found": None,
            "date_raw": None,
            "requestor_name": None,
            "requestor_title": None,
            "document_type": None,
            "gov_code_count": 0,
            "regulation_count": 0,
            "prior_opinion_count": 0,
            "external_citation_count": 0,
            "classification_topic": None,
            "classification_confidence": None,
            "self_citation_filtered": False,
            "word_count": 0,
            "page_count": 0,
            "parsing_notes": None,
            "qa_text_length": 0,
            "qa_source": None,
            "json_path": None,
        }

        # Determine era
        for era_name, start_year, end_year in ERA_BOUNDARIES:
            if start_year <= year <= end_year:
                result_entry["era"] = era_name
                break

        try:
            doc = extractor.process_document(doc_row)

            if doc is None:
                result_entry["status"] = "failed"
                result_entry["error"] = "process_document returned None"
                results.append(result_entry)
                continue

            # Save extracted document
            json_path = extractor.save_document(doc)

            # Populate results
            result_entry["status"] = "success"
            result_entry["extracted_id"] = doc.id
            result_entry["quality_score"] = doc.extraction.quality_score
            result_entry["section_confidence"] = doc.sections.extraction_confidence
            result_entry["has_standard_format"] = doc.sections.has_standard_format
            result_entry["question_found"] = doc.sections.question is not None
            result_entry["conclusion_found"] = doc.sections.conclusion is not None
            result_entry["facts_found"] = doc.sections.facts is not None
            result_entry["analysis_found"] = doc.sections.analysis is not None
            result_entry["date_found"] = doc.parsed.date
            result_entry["date_raw"] = doc.parsed.date_raw
            result_entry["requestor_name"] = doc.parsed.requestor_name
            result_entry["requestor_title"] = doc.parsed.requestor_title
            result_entry["document_type"] = doc.parsed.document_type
            result_entry["gov_code_count"] = len(doc.citations.government_code)
            result_entry["regulation_count"] = len(doc.citations.regulations)
            result_entry["prior_opinion_count"] = len(doc.citations.prior_opinions)
            result_entry["external_citation_count"] = len(doc.citations.external)
            result_entry["classification_topic"] = doc.classification.topic_primary
            result_entry["classification_confidence"] = doc.classification.confidence
            result_entry["word_count"] = doc.extraction.word_count
            result_entry["page_count"] = doc.extraction.page_count
            result_entry["parsing_notes"] = doc.sections.parsing_notes
            result_entry["qa_text_length"] = len(doc.embedding.qa_text) if doc.embedding.qa_text else 0
            result_entry["qa_source"] = doc.embedding.qa_source
            result_entry["json_path"] = str(json_path)

            # Check if self-citation was present in the raw text but filtered
            # (We can detect this by checking if the doc's own ID would match prior opinion patterns)
            if doc.id and not doc.id.startswith("UNK-"):
                result_entry["self_citation_filtered"] = True  # We always filter now

        except Exception as e:
            result_entry["status"] = "error"
            result_entry["error"] = str(e)

        results.append(result_entry)

    return results


def compute_summary(results: list[dict]) -> dict:
    """Compute summary statistics from extraction results."""
    total = len(results)
    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] != "success"]

    summary = {
        "total": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": len(successes) / total if total > 0 else 0,
    }

    if successes:
        summary["avg_quality"] = sum(r["quality_score"] for r in successes) / len(successes)
        summary["avg_section_confidence"] = sum(r["section_confidence"] for r in successes) / len(successes)
        summary["question_found_count"] = sum(1 for r in successes if r["question_found"])
        summary["conclusion_found_count"] = sum(1 for r in successes if r["conclusion_found"])
        summary["facts_found_count"] = sum(1 for r in successes if r["facts_found"])
        summary["analysis_found_count"] = sum(1 for r in successes if r["analysis_found"])
        summary["date_found_count"] = sum(1 for r in successes if r["date_found"])
        summary["has_gov_code"] = sum(1 for r in successes if r["gov_code_count"] > 0)
        summary["has_regulations"] = sum(1 for r in successes if r["regulation_count"] > 0)
        summary["has_prior_opinions"] = sum(1 for r in successes if r["prior_opinion_count"] > 0)
        summary["avg_qa_text_length"] = sum(r["qa_text_length"] for r in successes) / len(successes)

        # By era
        era_stats = {}
        for era_name, _, _ in ERA_BOUNDARIES:
            era_results = [r for r in results if r["era"] == era_name]
            era_successes = [r for r in era_results if r["status"] == "success"]
            era_stats[era_name] = {
                "total": len(era_results),
                "success": len(era_successes),
                "avg_quality": sum(r["quality_score"] for r in era_successes) / len(era_successes) if era_successes else 0,
                "avg_confidence": sum(r["section_confidence"] for r in era_successes) / len(era_successes) if era_successes else 0,
                "question_found": sum(1 for r in era_successes if r["question_found"]),
                "conclusion_found": sum(1 for r in era_successes if r["conclusion_found"]),
                "date_found": sum(1 for r in era_successes if r["date_found"]),
            }
        summary["by_era"] = era_stats

    return summary


def main():
    print("=" * 60)
    print("CALIBRATION v2: Post-Fix Extraction Review")
    print("=" * 60)

    # Load previous sample to exclude
    prev_ids = load_previous_sample_ids()
    print(f"\nExcluding {len(prev_ids)} documents from previous calibration")

    # Select fresh sample
    print("\nSelecting 50 fresh documents (10 per era)...")
    docs = select_sample(prev_ids)
    print(f"\nTotal selected: {len(docs)} documents")

    # Save sample IDs for reproducibility
    sample_ids = [[d["id"], d["year_tag"], d.get("letter_id"), d.get("pdf_url")] for d in docs]
    with open("data/calibration_v2_sample_ids.json", "w") as f:
        json.dump(sample_ids, f, indent=2)
    print("Sample IDs saved to data/calibration_v2_sample_ids.json")

    # Run extraction
    print("\n" + "-" * 60)
    print("Running extraction pipeline...")
    print("-" * 60)
    results = run_extraction(docs)

    # Compute summary
    summary = compute_summary(results)

    # Save full results
    output = {
        "summary": summary,
        "results": results,
    }
    with open("data/calibration_v2_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results saved to data/calibration_v2_results.json")

    # Print summary
    print("\n" + "=" * 60)
    print("CALIBRATION v2 SUMMARY")
    print("=" * 60)
    print(f"Success: {summary['success_count']}/{summary['total']} ({summary['success_rate']:.0%})")
    if summary.get('avg_quality'):
        print(f"Avg Quality: {summary['avg_quality']:.2f}")
        print(f"Avg Section Confidence: {summary['avg_section_confidence']:.2f}")
        print(f"Questions Found: {summary['question_found_count']}/{summary['success_count']}")
        print(f"Conclusions Found: {summary['conclusion_found_count']}/{summary['success_count']}")
        print(f"Dates Found: {summary['date_found_count']}/{summary['success_count']}")
        print(f"Has Gov Code Citations: {summary['has_gov_code']}/{summary['success_count']}")
        print(f"Has Prior Opinions: {summary['has_prior_opinions']}/{summary['success_count']}")

    print("\nBy Era:")
    for era_name, stats in summary.get("by_era", {}).items():
        print(f"  {era_name}: {stats['success']}/{stats['total']} success, "
              f"Q:{stats['question_found']}, C:{stats['conclusion_found']}, "
              f"D:{stats['date_found']}, "
              f"quality:{stats['avg_quality']:.2f}, conf:{stats['avg_confidence']:.2f}")

    print(f"\nFailure details:")
    for r in results:
        if r["status"] != "success":
            print(f"  doc#{r['db_id']} ({r['letter_id'] or 'no-id'}, {r['year']}): {r['error']}")


if __name__ == "__main__":
    main()
