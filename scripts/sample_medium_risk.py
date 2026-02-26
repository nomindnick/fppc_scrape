#!/usr/bin/env python3
"""
Statistical sampling of medium-risk documents (canary_score 0.50-0.70).

Verifies a random 10% sample with Claude Haiku vision to estimate the true
error rate. If error rate < 5%, marks entire medium tier as acceptable.
If >= 5%, recommends expanding to full verification.

Requires: ANTHROPIC_API_KEY

Usage:
    python scripts/sample_medium_risk.py --dry-run         # Preview sample plan
    python scripts/sample_medium_risk.py                   # Run sampling
    python scripts/sample_medium_risk.py --sample-pct 0.20 # 20% sample instead of 10%
"""

import argparse
import base64
import json
import os
import random
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import anthropic

from scraper.config import DATA_DIR, RAW_PDFS_DIR
from scraper.db import get_connection, update_fidelity

CANARY_REPORT = DATA_DIR / "qa_reports" / "canary_scan.json"
SAMPLE_REPORT = DATA_DIR / "qa_reports" / "medium_risk_sampling.json"

HAIKU_INPUT_COST = 0.80
HAIKU_OUTPUT_COST = 4.00

VERIFICATION_PROMPT = """Read the text in this document image. Transcribe the first 200 words exactly as written, preserving the original wording. Do not summarize or paraphrase — copy the exact text you see.

If the image is too blurry to read, respond with: UNREADABLE

Return ONLY the transcribed text, no commentary."""


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


def render_page_to_png(pdf_path: str, dpi: int = 200) -> bytes:
    """Render first page of PDF to PNG bytes."""
    import fitz
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    finally:
        doc.close()


def get_olmocr_first_words(json_path: str, n_words: int = 200) -> str:
    """Get first N words from olmOCR extraction."""
    full_path = os.path.join(str(DATA_DIR.parent), json_path)
    with open(full_path) as f:
        data = json.load(f)
    text = data.get("content", {}).get("full_text", "")
    return " ".join(text.split()[:n_words])


def normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison."""
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def verify_with_haiku(client, pdf_path: str, olmocr_words: str) -> dict:
    """Send page 1 to Haiku, compare reading against olmOCR."""
    img_bytes = render_page_to_png(pdf_path)
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": VERIFICATION_PROMPT},
                    ],
                }],
            )

            haiku_text = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens / 1e6 * HAIKU_INPUT_COST
                    + output_tokens / 1e6 * HAIKU_OUTPUT_COST)

            if "UNREADABLE" in haiku_text.upper():
                return {"similarity": 0.0, "is_unreadable": True,
                        "is_hallucinated": False, "cost": cost,
                        "haiku_text": haiku_text}

            h_words = normalize_for_comparison(haiku_text).split()
            o_words = normalize_for_comparison(olmocr_words).split()
            similarity = SequenceMatcher(None, h_words, o_words).ratio()

            return {
                "similarity": round(similarity, 4),
                "is_hallucinated": similarity < 0.40,
                "is_unreadable": False,
                "cost": cost,
                "haiku_text": haiku_text,
            }

        except (anthropic.RateLimitError, anthropic.APIStatusError):
            time.sleep(2 ** (attempt + 1))

    return {"similarity": 0.0, "is_hallucinated": False, "is_unreadable": False,
            "cost": 0.0, "error": "retries exhausted"}


def get_medium_risk_docs() -> list[dict]:
    """Load medium-risk docs from canary report."""
    if not CANARY_REPORT.exists():
        print(f"Error: {CANARY_REPORT} not found")
        sys.exit(1)

    with open(CANARY_REPORT) as f:
        data = json.load(f)

    return [
        r for r in data["results"]
        if r.get("risk_tier") == "medium" and not r.get("error")
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Statistical sampling of medium-risk fidelity docs"
    )
    parser.add_argument("--sample-pct", type=float, default=0.10,
                        help="Fraction to sample (default: 0.10 = 10%%)")
    parser.add_argument("--min-sample", type=int, default=20,
                        help="Minimum sample size (default: 20)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update-db", action="store_true",
                        help="Update fidelity columns based on results")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    medium = get_medium_risk_docs()
    print(f"Found {len(medium)} medium-risk documents (canary 0.50-0.70)")

    sample_size = max(args.min_sample, int(len(medium) * args.sample_pct))
    sample_size = min(sample_size, len(medium))

    random.seed(args.seed)
    sample = random.sample(medium, sample_size)

    est_cost = sample_size * 0.015
    print(f"Sample size: {sample_size} ({args.sample_pct * 100:.0f}%)")
    print(f"Estimated cost: ~${est_cost:.2f}")

    if args.dry_run:
        print(f"\nSample documents:")
        for d in sample[:20]:
            print(f"  {d['letter_id']:20s} canary={d['canary_score']:.3f} year={d['year']}")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    conn = get_connection()
    cursor = conn.cursor()

    results = []
    verified_ok = 0
    hallucinated = 0
    unreadable = 0
    errors = 0
    total_cost = 0.0

    for i, doc_info in enumerate(sample, 1):
        doc_id = doc_info["doc_id"]
        letter_id = doc_info["letter_id"]

        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            errors += 1
            continue
        doc_row = dict(row)

        pdf_path = resolve_pdf_path(doc_row)
        json_path = doc_row.get("json_path")
        if not pdf_path or not json_path:
            errors += 1
            continue

        olmocr_words = get_olmocr_first_words(json_path)
        result = verify_with_haiku(client, pdf_path, olmocr_words)
        result["doc_id"] = doc_id
        result["letter_id"] = letter_id
        result["canary_score"] = doc_info["canary_score"]
        results.append(result)
        total_cost += result.get("cost", 0)

        if result.get("error"):
            errors += 1
            status = "ERROR"
        elif result["is_unreadable"]:
            unreadable += 1
            status = "UNREADABLE"
        elif result["is_hallucinated"]:
            hallucinated += 1
            status = f"HALLUCINATED (sim={result['similarity']:.3f})"
        else:
            verified_ok += 1
            status = f"OK (sim={result['similarity']:.3f})"

        print(f"[{i}/{sample_size}] {letter_id}: {status}")
        time.sleep(0.5)

    conn.close()

    # Compute error rate
    checked = verified_ok + hallucinated
    error_rate = hallucinated / checked if checked > 0 else 0.0

    print(f"\n{'=' * 60}")
    print("MEDIUM-RISK SAMPLING RESULTS")
    print(f"{'=' * 60}")
    print(f"  Sample size:     {sample_size}")
    print(f"  Verified OK:     {verified_ok}")
    print(f"  Hallucinated:    {hallucinated}")
    print(f"  Unreadable:      {unreadable}")
    print(f"  Errors:          {errors}")
    print(f"  Error rate:      {error_rate * 100:.1f}%")
    print(f"  Total cost:      ${total_cost:.4f}")

    # Decision
    if error_rate < 0.05:
        decision = "ACCEPT"
        print(f"\n  DECISION: Error rate < 5% — medium tier is acceptable.")
        print(f"  Recommendation: Mark entire medium tier as 'low' risk.")
    else:
        decision = "EXPAND"
        print(f"\n  DECISION: Error rate >= 5% — medium tier needs full verification.")
        print(f"  Recommendation: Run verify_high_risk.py on medium tier.")
        remaining_cost = (len(medium) - sample_size) * 0.015
        print(f"  Estimated cost for full verification: ~${remaining_cost:.2f}")

    # Update DB if requested
    if args.update_db and decision == "ACCEPT":
        print("\nUpdating database: marking all medium-risk docs as 'low'...")
        for doc_info in medium:
            update_fidelity(
                doc_id=doc_info["doc_id"],
                score=doc_info["canary_score"],
                method="tesseract_canary",
                risk="low",
            )
        print(f"Updated {len(medium)} documents")
    elif args.update_db:
        # Only update the verified sample
        for r in results:
            if r.get("error") or r.get("is_unreadable"):
                continue
            risk = "verified" if not r["is_hallucinated"] else "high"
            update_fidelity(
                doc_id=r["doc_id"],
                score=r["similarity"],
                method="haiku_verified",
                risk=risk,
            )

    # Save report
    SAMPLE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "medium_tier_total": len(medium),
        "sample_size": sample_size,
        "sample_pct": args.sample_pct,
        "verified_ok": verified_ok,
        "hallucinated": hallucinated,
        "unreadable": unreadable,
        "errors": errors,
        "error_rate": round(error_rate, 4),
        "decision": decision,
        "total_cost": round(total_cost, 4),
        "results": results,
    }
    with open(SAMPLE_REPORT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport: {SAMPLE_REPORT}")


if __name__ == "__main__":
    main()
