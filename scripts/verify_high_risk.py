#!/usr/bin/env python3
"""
Claude Haiku vision verification for high-risk documents.

For docs where the Tesseract canary flagged strong divergence (canary_score < 0.50),
use Claude Haiku as a vision model to read the first page and compare against olmOCR.

This is NOT a full re-extraction — it's a targeted fidelity check. We send page 1
as an image, ask Haiku to transcribe the first ~200 words, then compare that against
the olmOCR output. If they diverge strongly, the olmOCR likely hallucinated.

Requires: ANTHROPIC_API_KEY

Usage:
    python scripts/verify_high_risk.py --dry-run         # Preview high-risk docs
    python scripts/verify_high_risk.py                   # Verify all high-risk
    python scripts/verify_high_risk.py --limit 10        # Verify 10 docs
    python scripts/verify_high_risk.py --threshold 0.50  # Custom canary threshold
"""

import argparse
import base64
import json
import os
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
from scraper.db import get_connection, update_extraction_status, update_fidelity
from scraper.quality import compute_quality_score

CANARY_REPORT = DATA_DIR / "qa_reports" / "canary_scan.json"
VERIFY_REPORT = DATA_DIR / "qa_reports" / "high_risk_verification.json"

# Haiku pricing (per million tokens)
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


def render_page_to_png(pdf_path: str, page_num: int = 0, dpi: int = 200) -> bytes:
    """Render a PDF page to PNG bytes."""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    finally:
        doc.close()


def get_olmocr_first_words(json_path: str, n_words: int = 200) -> str:
    """Get the first N words from the olmOCR extraction."""
    full_path = os.path.join(str(DATA_DIR.parent), json_path)
    with open(full_path) as f:
        data = json.load(f)
    text = data.get("content", {}).get("full_text", "")
    words = text.split()[:n_words]
    return " ".join(words)


def normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison."""
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_with_tesseract(pdf_path: str, max_pages: int = 20) -> str:
    """Extract full text via Tesseract as fallback."""
    import fitz

    texts = []
    doc = fitz.open(pdf_path)
    pages = min(len(doc), max_pages)

    for page_num in range(pages):
        page = doc[page_num]
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--dpi", "300", "-l", "eng"],
            input=img_bytes,
            capture_output=True,
            timeout=60,
        )
        texts.append(result.stdout.decode("utf-8", errors="replace"))

    doc.close()
    return "\n\n".join(texts)


def verify_with_haiku(
    client: anthropic.Anthropic,
    pdf_path: str,
    olmocr_first_words: str,
    model: str = "claude-haiku-4-5-20251001",
) -> dict:
    """
    Send page 1 to Claude Haiku, compare its reading against olmOCR.

    Returns dict with:
        haiku_text: what Haiku read
        similarity: 0.0-1.0 comparison
        is_hallucinated: True if similarity < 0.60
        is_unreadable: True if Haiku couldn't read the image
        input_tokens, output_tokens, cost
    """
    img_bytes = render_page_to_png(pdf_path, page_num=0, dpi=200)
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
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
                        {
                            "type": "text",
                            "text": VERIFICATION_PROMPT,
                        },
                    ],
                }],
            )

            haiku_text = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            cost = (input_tokens / 1_000_000 * HAIKU_INPUT_COST
                    + output_tokens / 1_000_000 * HAIKU_OUTPUT_COST)

            if "UNREADABLE" in haiku_text.upper():
                return {
                    "haiku_text": haiku_text,
                    "similarity": 0.0,
                    "is_hallucinated": False,
                    "is_unreadable": True,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost,
                }

            # Compare Haiku's reading against olmOCR's first 200 words
            h_norm = normalize_for_comparison(haiku_text)
            o_norm = normalize_for_comparison(olmocr_first_words)

            # Word-level comparison
            h_words = h_norm.split()
            o_words = o_norm.split()
            similarity = SequenceMatcher(None, h_words, o_words).ratio()

            return {
                "haiku_text": haiku_text,
                "similarity": round(similarity, 4),
                "is_hallucinated": similarity < 0.40,
                "is_unreadable": False,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
            }

        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                time.sleep(2 ** (attempt + 1))
            else:
                raise

    return {
        "haiku_text": "",
        "similarity": 0.0,
        "is_hallucinated": False,
        "is_unreadable": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "error": "All retries exhausted",
    }


def get_high_risk_docs(threshold: float = 0.50) -> list[dict]:
    """Load high-risk docs from the canary scan report."""
    if not CANARY_REPORT.exists():
        print(f"Error: canary scan report not found at {CANARY_REPORT}")
        sys.exit(1)

    with open(CANARY_REPORT) as f:
        data = json.load(f)

    high_risk = [
        r for r in data["results"]
        if r.get("risk_tier") == "high" and not r.get("error")
    ]
    return high_risk


def main():
    parser = argparse.ArgumentParser(
        description="Claude Haiku vision verification for high-risk docs"
    )
    parser.add_argument("--limit", type=int, help="Max documents to verify")
    parser.add_argument("--threshold", type=float, default=0.50,
                        help="Canary score threshold for high-risk (default: 0.50)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--update-db", action="store_true",
                        help="Update fidelity columns in database")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    high_risk = get_high_risk_docs(args.threshold)
    print(f"Found {len(high_risk)} high-risk documents (canary < {args.threshold})")

    if args.dry_run:
        est_cost = len(high_risk) * 0.015  # ~$0.015 per doc
        print(f"Estimated cost: ~${est_cost:.2f}")
        for d in high_risk[:20]:
            print(f"  {d['letter_id']:20s} canary={d['canary_score']:.3f} year={d['year']}")
        if len(high_risk) > 20:
            print(f"  ... and {len(high_risk) - 20} more")
        return

    client = anthropic.Anthropic(api_key=api_key)
    conn = get_connection()
    cursor = conn.cursor()

    to_verify = high_risk[:args.limit] if args.limit else high_risk

    results = []
    verified_ok = 0
    hallucinated = 0
    unreadable = 0
    fixed = 0
    errors = 0
    total_cost = 0.0

    for i, doc_info in enumerate(to_verify, 1):
        doc_id = doc_info["doc_id"]
        letter_id = doc_info["letter_id"]

        # Get DB row for PDF path resolution
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            errors += 1
            continue
        doc_row = dict(row)

        pdf_path = resolve_pdf_path(doc_row)
        if not pdf_path:
            errors += 1
            continue

        json_path = doc_row.get("json_path")
        if not json_path:
            errors += 1
            continue

        olmocr_words = get_olmocr_first_words(json_path)

        result = verify_with_haiku(client, pdf_path, olmocr_words)
        result["doc_id"] = doc_id
        result["letter_id"] = letter_id
        result["year"] = doc_info["year"]
        result["canary_score"] = doc_info["canary_score"]
        results.append(result)
        total_cost += result.get("cost", 0)

        if result.get("error"):
            errors += 1
            status = "ERROR"
        elif result["is_unreadable"]:
            unreadable += 1
            status = "UNREADABLE"
            if args.update_db:
                update_fidelity(doc_id, 0.5, "haiku_unreadable", "medium")
        elif result["is_hallucinated"]:
            hallucinated += 1
            status = f"HALLUCINATED (sim={result['similarity']:.3f})"

            # Re-extract with Tesseract
            try:
                tess_text = extract_with_tesseract(pdf_path)
                page_count = doc_row.get("page_count") or 1
                metrics = compute_quality_score(tess_text, page_count)

                if metrics.final_score > 0.3 and len(tess_text.split()) > 20:
                    full_json = os.path.join(str(DATA_DIR.parent), json_path)
                    with open(full_json) as f:
                        doc_json = json.load(f)

                    doc_json["content"]["full_text"] = tess_text
                    doc_json["content"]["full_text_markdown"] = None
                    doc_json["extraction"]["method"] = "tesseract_fallback"
                    doc_json["extraction"]["quality_score"] = metrics.final_score
                    doc_json["extraction"]["word_count"] = len(tess_text.split())
                    doc_json["extraction"]["char_count"] = len(tess_text)

                    with open(full_json, "w") as f:
                        json.dump(doc_json, f, indent=2, ensure_ascii=False)

                    update_extraction_status(
                        doc_id=doc_id,
                        status="extracted",
                        method="tesseract_fallback",
                        quality=metrics.final_score,
                    )
                    if args.update_db:
                        update_fidelity(doc_id, 0.9, "haiku_verified_tesseract", "low")
                    fixed += 1
                    status += " → FIXED (Tesseract)"
            except Exception as e:
                status += f" → fix failed: {e}"

            if args.update_db and doc_id not in [r["doc_id"] for r in results if "→ FIXED" in str(r)]:
                update_fidelity(doc_id, result["similarity"], "haiku_verified", "high")
        else:
            verified_ok += 1
            status = f"VERIFIED OK (sim={result['similarity']:.3f})"
            if args.update_db:
                update_fidelity(doc_id, result["similarity"], "haiku_verified", "verified")

        print(f"[{i}/{len(to_verify)}] {letter_id}: {status}")

        # Rate limit protection
        time.sleep(0.5)

    conn.close()

    # Save report
    VERIFY_REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_verified": len(to_verify),
        "verified_ok": verified_ok,
        "hallucinated": hallucinated,
        "fixed": fixed,
        "unreadable": unreadable,
        "errors": errors,
        "total_cost": round(total_cost, 4),
        "results": results,
    }
    with open(VERIFY_REPORT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'=' * 60}")
    print("HIGH-RISK VERIFICATION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total verified:  {len(to_verify)}")
    print(f"  Verified OK:     {verified_ok}")
    print(f"  Hallucinated:    {hallucinated}")
    print(f"  Fixed:           {fixed}")
    print(f"  Unreadable:      {unreadable}")
    print(f"  Errors:          {errors}")
    print(f"  Total cost:      ${total_cost:.4f}")
    print(f"  Report:          {VERIFY_REPORT}")


if __name__ == "__main__":
    main()
