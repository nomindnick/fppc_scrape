#!/usr/bin/env python3
"""
OCR Engine Benchmark for FPPC Corpus (Task 4.2)

Benchmarks three OCR models available on DeepInfra against the existing native
extraction baseline. Samples documents across quality tiers and decades, runs
each model on the same rendered page images, and generates a comparison report.

Models:
  - olmOCR-2 (allenai/olmOCR-2-7B-1025) — current pipeline default
  - PaddleOCR-VL (PaddlePaddle/PaddleOCR-VL-0.9B) — compact, 109 languages
  - DeepSeek-OCR (deepseek-ai/DeepSeek-OCR) — encoder-decoder, claims 97% acc

Usage:
    python scripts/benchmark_ocr.py --dry-run
    python scripts/benchmark_ocr.py --models olmocr,paddle --sample-size 4 --pages 1
    python scripts/benchmark_ocr.py
"""

import argparse
import base64
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import fitz  # PyMuPDF

from scraper.config import DATA_DIR, RAW_PDFS_DIR
from scraper.db import get_connection
from scraper.quality import compute_quality_score

EXTRACTED_DIR = DATA_DIR / "extracted"


# =============================================================================
# Model Definitions
# =============================================================================


@dataclass
class ModelConfig:
    """Configuration for a DeepInfra OCR model."""

    name: str  # Display name
    model_id: str  # DeepInfra model string
    cost_input: float  # $/M input tokens
    cost_output: float  # $/M output tokens
    max_tokens: int = 4096


MODELS = {
    "olmocr": ModelConfig(
        name="olmOCR-2",
        model_id="allenai/olmOCR-2-7B-1025",
        cost_input=0.09,
        cost_output=0.19,
    ),
    "paddle": ModelConfig(
        name="PaddleOCR-VL",
        model_id="PaddlePaddle/PaddleOCR-VL-0.9B",
        cost_input=0.03,
        cost_output=0.10,
    ),
    "deepseek": ModelConfig(
        name="DeepSeek-OCR",
        model_id="deepseek-ai/DeepSeek-OCR",
        cost_input=0.03,
        cost_output=0.10,
    ),
}


# =============================================================================
# Quality Tiers
# =============================================================================

QUALITY_TIERS = {
    "broken": (0.0, 0.50),
    "degraded": (0.50, 0.70),
    "impaired": (0.70, 0.80),
    "control": (0.80, 0.90),
}

DECADES = {
    "1970s": (1975, 1979),
    "1980s": (1980, 1989),
    "1990s": (1990, 1999),
    "2000s": (2000, 2009),
}


# =============================================================================
# Result Dataclass
# =============================================================================


@dataclass
class ModelResult:
    """Results from a single model on a single document."""

    model_key: str
    model_name: str
    text_length: int = 0
    word_count: int = 0
    v3_score: float = 0.0
    dict_miss_ratio: float = 1.0
    has_section_headers: bool = False
    delta_vs_native: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    pages_processed: int = 0
    error: str | None = None


@dataclass
class DocumentResult:
    """Results for all models on a single document."""

    letter_id: str
    year: int
    quality_tier: str
    decade: str
    native_score: float
    native_word_count: int
    page_count: int
    pages_benchmarked: int
    model_results: dict[str, ModelResult] = field(default_factory=dict)


# =============================================================================
# Sampling
# =============================================================================


def select_sample(
    sample_size: int = 18, seed: int = 42
) -> list[dict]:
    """
    Select a stratified sample of documents across quality tiers and decades.

    Queries the DB for extracted documents, rescores them with v3, then picks
    documents to fill the tier×decade matrix.

    Returns list of dicts with: id, letter_id, year, pdf_url, v3_score, tier, decade
    """
    conn = get_connection()

    # Pull candidates: extracted docs with quality < 0.90 (v1 score in DB)
    # We over-sample because v1→v3 rescoring may shift some out of range
    # Note: page_count is NULL in DB — we get it from the JSON files instead
    rows = conn.execute(
        """
        SELECT id, letter_id, year_tag, pdf_url, extraction_quality
        FROM documents
        WHERE extraction_status = 'extracted'
          AND extraction_quality IS NOT NULL
          AND extraction_quality < 0.95
        ORDER BY extraction_quality ASC
        """,
    ).fetchall()
    conn.close()

    print(f"  Pulled {len(rows)} candidate documents from DB")

    # Rescore each candidate with v3 and bucket into tier×decade
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[dict]] = {}

    for row in rows:
        letter_id = row["letter_id"]
        year = row["year_tag"]
        if not letter_id or not year:
            continue

        # Load the extracted JSON to get full_text for v3 rescoring
        json_path = _find_json(letter_id, year)
        if not json_path:
            continue

        try:
            with open(json_path) as f:
                doc_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        full_text = doc_data.get("content", {}).get("full_text", "")
        page_count = doc_data.get("extraction", {}).get("page_count", 1)
        if not full_text or page_count <= 0:
            continue

        metrics = compute_quality_score(full_text, page_count)
        v3_score = metrics.final_score

        # Determine v3 tier
        tier = _score_to_tier(v3_score)
        if tier is None:
            continue  # Score >= 0.90, skip

        # Determine decade
        decade = _year_to_decade(year)
        if decade is None:
            continue  # Outside our decade range

        key = (tier, decade)
        if key not in buckets:
            buckets[key] = []

        buckets[key].append(
            {
                "id": row["id"],
                "letter_id": letter_id,
                "year": year,
                "pdf_url": row["pdf_url"],
                "v3_score": round(v3_score, 4),
                "tier": tier,
                "decade": decade,
                "page_count": page_count,
                "json_path": str(json_path),
            }
        )

        # Cap candidates per bucket to save time
        if len(buckets[key]) >= 50:
            continue

    # Pick 1 document per occupied cell
    all_cells = []
    for key, candidates in sorted(buckets.items()):
        rng.shuffle(candidates)
        all_cells.append(candidates[0])

    print(f"  Filled {len(all_cells)} cells in tier×decade matrix")
    print(f"  Matrix coverage:")
    for tier_name in QUALITY_TIERS:
        cells = [d for d in all_cells if d["tier"] == tier_name]
        decades_hit = [d["decade"] for d in cells]
        print(f"    {tier_name:>10s}: {', '.join(decades_hit) if decades_hit else '(empty)'}")

    # If sample_size < occupied cells, prioritize lower tiers
    if len(all_cells) > sample_size:
        # Sort by tier priority (broken first), then shuffle within tier
        tier_priority = {"broken": 0, "degraded": 1, "impaired": 2, "control": 3}
        all_cells.sort(key=lambda d: (tier_priority.get(d["tier"], 4), rng.random()))
        selected = all_cells[:sample_size]
        print(f"  Trimmed to {sample_size} (prioritizing low-quality tiers)")
    else:
        selected = list(all_cells)

    # If under sample_size, fill from broken and degraded tiers
    if len(selected) < sample_size:
        remaining = sample_size - len(selected)
        selected_ids = {d["id"] for d in selected}
        fill_pool = []
        for tier_name in ["broken", "degraded", "impaired"]:
            for key, candidates in buckets.items():
                if key[0] == tier_name:
                    for c in candidates:
                        if c["id"] not in selected_ids:
                            fill_pool.append(c)
        rng.shuffle(fill_pool)
        for doc in fill_pool[:remaining]:
            selected.append(doc)
        if fill_pool[:remaining]:
            print(f"  Filled {len(fill_pool[:remaining])} additional from low-quality tiers")

    # Sort by year for readability
    selected.sort(key=lambda d: (d["year"], d["letter_id"]))
    print(f"  Final sample: {len(selected)} documents")
    return selected


def _find_json(letter_id: str, year: int) -> Path | None:
    """Find the extracted JSON file for a document."""
    year_dir = EXTRACTED_DIR / str(year)
    if not year_dir.is_dir():
        return None

    # Try exact match first
    json_path = year_dir / f"{letter_id}.json"
    if json_path.exists():
        return json_path

    # Case-insensitive fallback
    target = letter_id.lower()
    for candidate in year_dir.iterdir():
        if candidate.stem.lower() == target and candidate.suffix == ".json":
            return candidate

    return None


def _score_to_tier(score: float) -> str | None:
    """Map a v3 score to a quality tier name, or None if >= 0.90."""
    for tier_name, (lo, hi) in QUALITY_TIERS.items():
        if lo <= score < hi:
            return tier_name
    return None


def _year_to_decade(year: int) -> str | None:
    """Map a year to a decade bucket name, or None if outside range."""
    for decade_name, (lo, hi) in DECADES.items():
        if lo <= year <= hi:
            return decade_name
    return None


# =============================================================================
# PDF Path Resolution (mirrors extractor.py:310-341)
# =============================================================================


def get_pdf_path(pdf_url: str, year: int) -> Path | None:
    """Resolve local PDF path from URL and year, with case-insensitive fallback."""
    filename = pdf_url.rstrip("/").split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    year_dir = RAW_PDFS_DIR / str(year)
    pdf_path = year_dir / filename

    if pdf_path.exists():
        return pdf_path

    # Case-insensitive fallback
    if year_dir.is_dir():
        target_stem = Path(filename).stem.lower()
        for candidate in year_dir.iterdir():
            if candidate.stem.lower() == target_stem and candidate.suffix.lower() == ".pdf":
                return candidate

    return None


# =============================================================================
# Page Rendering
# =============================================================================


def render_pages(pdf_path: Path, max_pages: int, dpi: int) -> list[bytes]:
    """
    Render PDF pages to PNG bytes.

    Returns list of PNG byte strings, one per page (up to max_pages).
    """
    pages = []
    with fitz.open(pdf_path) as doc:
        pages_to_render = min(len(doc), max_pages)
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for page_num in range(pages_to_render):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=mat)
            pages.append(pix.tobytes("png"))
    return pages


# =============================================================================
# OCR API Calls
# =============================================================================


def call_ocr_model(
    client,
    model_config: ModelConfig,
    page_images: list[bytes],
) -> dict:
    """
    Send page images to an OCR model and collect results.

    Returns dict with: text, input_tokens, output_tokens, total_tokens, cost, error
    """
    all_text = []
    total_input = 0
    total_output = 0
    total_tokens = 0

    for i, img_bytes in enumerate(page_images):
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")

        try:
            response = _call_with_retry(
                client,
                model_config,
                img_base64,
            )
        except Exception as e:
            return {
                "text": "\n\n".join(all_text),
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_tokens,
                "cost": _compute_cost(model_config, total_input, total_output),
                "pages_processed": i,
                "error": f"Page {i + 1}: {e}",
            }

        page_text = response.choices[0].message.content or ""
        all_text.append(page_text)

        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            p_in = getattr(usage, "prompt_tokens", 0) or 0
            p_out = getattr(usage, "completion_tokens", 0) or 0
            total_input += p_in
            total_output += p_out
            total_tokens += getattr(usage, "total_tokens", 0) or (p_in + p_out)

        # Rate limit between pages
        if i < len(page_images) - 1:
            time.sleep(0.5)

    return {
        "text": "\n\n".join(all_text),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_tokens,
        "cost": _compute_cost(model_config, total_input, total_output),
        "pages_processed": len(page_images),
        "error": None,
    }


def _call_with_retry(client, model_config: ModelConfig, img_base64: str, max_retries: int = 3):
    """Call the OCR API with exponential backoff retries."""
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model_config.model_id,
                max_tokens=model_config.max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}",
                                },
                            }
                        ],
                    }
                ],
            )
            return response
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = (2**attempt) * 1.0  # 1s, 2s, 4s
                print(f"      Retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
    raise last_error


def _compute_cost(config: ModelConfig, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost from token counts and model pricing."""
    return (input_tokens * config.cost_input + output_tokens * config.cost_output) / 1_000_000


# =============================================================================
# Document Processing
# =============================================================================


def process_document(
    doc: dict,
    model_keys: list[str],
    client,
    max_pages: int,
    dpi: int,
    output_dir: Path,
) -> DocumentResult | None:
    """
    Benchmark all selected models on a single document.

    Renders pages once, sends to each model, scores output with v3.
    Saves raw text and page images to output_dir.
    """
    letter_id = doc["letter_id"]
    year = doc["year"]
    page_count = doc["page_count"]

    # Resolve PDF path
    pdf_path = get_pdf_path(doc["pdf_url"], year)
    if not pdf_path:
        print(f"  SKIP {letter_id}: PDF not found")
        return None

    # Load native baseline from JSON
    try:
        with open(doc["json_path"]) as f:
            doc_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  SKIP {letter_id}: JSON error: {e}")
        return None

    native_text = doc_data.get("content", {}).get("full_text", "")
    native_metrics = compute_quality_score(native_text, page_count)

    # Create output directory for this document
    doc_dir = output_dir / "raw_outputs" / letter_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Save native baseline
    (doc_dir / "native.txt").write_text(native_text, encoding="utf-8")

    # Render pages
    try:
        page_images = render_pages(pdf_path, max_pages, dpi)
    except Exception as e:
        print(f"  SKIP {letter_id}: Render error: {e}")
        return None

    pages_benchmarked = len(page_images)

    # Save page PNGs
    for i, img_bytes in enumerate(page_images):
        (doc_dir / f"page_{i + 1}.png").write_bytes(img_bytes)

    result = DocumentResult(
        letter_id=letter_id,
        year=year,
        quality_tier=doc["tier"],
        decade=doc["decade"],
        native_score=native_metrics.final_score,
        native_word_count=native_metrics.total_words,
        page_count=page_count,
        pages_benchmarked=pages_benchmarked,
    )

    # Run each model
    for model_key in model_keys:
        model_config = MODELS[model_key]
        print(f"    {model_config.name}...", end="", flush=True)

        ocr_result = call_ocr_model(client, model_config, page_images)

        ocr_text = ocr_result["text"]
        # Score using pages_benchmarked (not full page_count) for fair comparison
        ocr_metrics = compute_quality_score(ocr_text, pages_benchmarked) if ocr_text else None

        model_result = ModelResult(
            model_key=model_key,
            model_name=model_config.name,
            text_length=len(ocr_text),
            word_count=len(ocr_text.split()) if ocr_text else 0,
            v3_score=ocr_metrics.final_score if ocr_metrics else 0.0,
            dict_miss_ratio=ocr_metrics.dict_miss_ratio if ocr_metrics else 1.0,
            has_section_headers=ocr_metrics.has_section_headers if ocr_metrics else False,
            delta_vs_native=(ocr_metrics.final_score - native_metrics.final_score) if ocr_metrics else 0.0,
            input_tokens=ocr_result["input_tokens"],
            output_tokens=ocr_result["output_tokens"],
            total_tokens=ocr_result["total_tokens"],
            cost_usd=ocr_result["cost"],
            pages_processed=ocr_result["pages_processed"],
            error=ocr_result["error"],
        )

        result.model_results[model_key] = model_result

        # Save raw output
        (doc_dir / f"{model_key}.txt").write_text(ocr_text, encoding="utf-8")

        status = f" v3={model_result.v3_score:.3f} (Δ{model_result.delta_vs_native:+.3f})"
        if model_result.error:
            status += f" [ERROR: {model_result.error}]"
        print(status)

        # Rate limit between models
        time.sleep(1.0)

    return result


# =============================================================================
# Report Generation
# =============================================================================


def generate_report(
    results: list[DocumentResult],
    model_keys: list[str],
    config: dict,
    output_dir: Path,
) -> str:
    """Generate a markdown benchmark report."""
    lines = []
    lines.append("# OCR Engine Benchmark Report")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Documents**: {len(results)}")
    lines.append(f"**Pages/doc**: {config['pages']} (max)")
    lines.append(f"**DPI**: {config['dpi']}")
    lines.append(f"**Models**: {', '.join(model_keys)}")
    lines.append("")

    # --- Summary Table ---
    lines.append("## Summary")
    lines.append("")

    # Header
    cols = ["Metric", "Native"]
    for mk in model_keys:
        cols.append(MODELS[mk].name)
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")

    # Avg v3 score
    native_scores = [r.native_score for r in results]
    row = ["Avg v3 score", f"{_mean(native_scores):.3f}"]
    for mk in model_keys:
        scores = [r.model_results[mk].v3_score for r in results if mk in r.model_results]
        row.append(f"{_mean(scores):.3f}" if scores else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Avg delta vs native
    row = ["Avg Δ vs native", "—"]
    for mk in model_keys:
        deltas = [r.model_results[mk].delta_vs_native for r in results if mk in r.model_results]
        val = _mean(deltas) if deltas else 0
        row.append(f"{val:+.3f}" if deltas else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Avg dict miss ratio
    row = ["Avg dict miss %", "—"]
    for mk in model_keys:
        misses = [r.model_results[mk].dict_miss_ratio for r in results if mk in r.model_results]
        row.append(f"{_mean(misses) * 100:.1f}%" if misses else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Section header preservation
    row = ["Section headers %", "—"]
    for mk in model_keys:
        headers = [r.model_results[mk].has_section_headers for r in results if mk in r.model_results]
        row.append(f"{sum(headers)}/{len(headers)}" if headers else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Avg cost/doc
    row = ["Avg cost/doc", "—"]
    for mk in model_keys:
        costs = [r.model_results[mk].cost_usd for r in results if mk in r.model_results]
        row.append(f"${_mean(costs):.4f}" if costs else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Total cost
    row = ["Total cost", "—"]
    for mk in model_keys:
        costs = [r.model_results[mk].cost_usd for r in results if mk in r.model_results]
        row.append(f"${sum(costs):.4f}" if costs else "—")
    lines.append("| " + " | ".join(row) + " |")

    # Errors
    row = ["Errors", "—"]
    for mk in model_keys:
        errs = [1 for r in results if mk in r.model_results and r.model_results[mk].error]
        row.append(str(sum(errs)))
    lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # --- By Quality Tier ---
    lines.append("## By Quality Tier")
    lines.append("")
    for tier_name in QUALITY_TIERS:
        tier_results = [r for r in results if r.quality_tier == tier_name]
        if not tier_results:
            continue

        lines.append(f"### {tier_name.title()} ({len(tier_results)} docs)")
        lines.append("")

        cols = ["Doc", "Year", "Native"]
        for mk in model_keys:
            cols.append(MODELS[mk].name)
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")

        for r in tier_results:
            row = [r.letter_id, str(r.year), f"{r.native_score:.3f}"]
            for mk in model_keys:
                if mk in r.model_results:
                    mr = r.model_results[mk]
                    row.append(f"{mr.v3_score:.3f} ({mr.delta_vs_native:+.3f})")
                else:
                    row.append("—")
            lines.append("| " + " | ".join(row) + " |")

        # Tier averages
        native_avg = _mean([r.native_score for r in tier_results])
        row = ["**Average**", "", f"**{native_avg:.3f}**"]
        for mk in model_keys:
            deltas = [r.model_results[mk].delta_vs_native for r in tier_results if mk in r.model_results]
            scores = [r.model_results[mk].v3_score for r in tier_results if mk in r.model_results]
            if scores:
                row.append(f"**{_mean(scores):.3f} ({_mean(deltas):+.3f})**")
            else:
                row.append("—")
        lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # --- By Decade ---
    lines.append("## By Decade")
    lines.append("")
    for decade_name in DECADES:
        decade_results = [r for r in results if r.decade == decade_name]
        if not decade_results:
            continue

        native_avg = _mean([r.native_score for r in decade_results])
        model_avgs = []
        for mk in model_keys:
            scores = [r.model_results[mk].v3_score for r in decade_results if mk in r.model_results]
            deltas = [r.model_results[mk].delta_vs_native for r in decade_results if mk in r.model_results]
            if scores:
                model_avgs.append(f"{MODELS[mk].name}: {_mean(scores):.3f} (Δ{_mean(deltas):+.3f})")
            else:
                model_avgs.append(f"{MODELS[mk].name}: —")

        lines.append(f"**{decade_name}** ({len(decade_results)} docs): native {native_avg:.3f} → {', '.join(model_avgs)}")
        lines.append("")

    # --- Cost Projection ---
    lines.append("## Cost Projection")
    lines.append("")
    lines.append("Estimated cost to re-extract all ~2,897 documents below v3 score 0.80:")
    lines.append("")

    for mk in model_keys:
        costs = [r.model_results[mk].cost_usd for r in results if mk in r.model_results]
        if not costs:
            continue
        avg_cost = _mean(costs)
        # Scale from benchmarked pages to assumed 3 pages average
        pages_benchmarked = _mean(
            [r.model_results[mk].pages_processed for r in results if mk in r.model_results]
        )
        if pages_benchmarked > 0:
            cost_per_page = avg_cost / pages_benchmarked
            # Estimate: average doc is ~5 pages, re-extract first 3
            projected_cost = cost_per_page * 3 * 2897
        else:
            projected_cost = 0
        lines.append(f"- **{MODELS[mk].name}**: ${avg_cost:.4f}/doc benchmarked → **${projected_cost:.2f}** projected (2,897 docs × 3 pages)")

    lines.append("")

    # --- Recommendation ---
    lines.append("## Recommendation")
    lines.append("")

    # Find best model by avg delta
    best_model = None
    best_delta = -float("inf")
    for mk in model_keys:
        deltas = [r.model_results[mk].delta_vs_native for r in results if mk in r.model_results]
        if deltas and _mean(deltas) > best_delta:
            best_delta = _mean(deltas)
            best_model = mk

    if best_model:
        costs = [r.model_results[best_model].cost_usd for r in results if best_model in r.model_results]
        lines.append(f"Best overall improvement: **{MODELS[best_model].name}** with avg Δ{best_delta:+.3f} at ${_mean(costs):.4f}/doc")
    else:
        lines.append("No model data available for recommendation.")

    lines.append("")

    # --- Per-Document Detail ---
    lines.append("## Per-Document Details")
    lines.append("")

    for r in results:
        lines.append(f"### {r.letter_id} ({r.year}, {r.quality_tier}, {r.decade})")
        lines.append(f"- Native: v3={r.native_score:.3f}, {r.native_word_count} words, {r.page_count} pages")
        for mk in model_keys:
            if mk in r.model_results:
                mr = r.model_results[mk]
                err = f" **ERROR**: {mr.error}" if mr.error else ""
                lines.append(
                    f"- {mr.model_name}: v3={mr.v3_score:.3f} (Δ{mr.delta_vs_native:+.3f}), "
                    f"{mr.word_count} words, {mr.pages_processed} pages, "
                    f"dict_miss={mr.dict_miss_ratio:.1%}, "
                    f"${mr.cost_usd:.4f} ({mr.total_tokens} tokens){err}"
                )
        lines.append("")

    return "\n".join(lines)


def _mean(values: list) -> float:
    """Safe mean that returns 0 for empty lists."""
    return sum(values) / len(values) if values else 0.0


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark OCR models on FPPC corpus documents",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview sample selection without running OCR",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=",".join(MODELS.keys()),
        help=f"Comma-separated model keys to test (default: {','.join(MODELS.keys())})",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=18,
        help="Number of documents to sample (default: 18)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Max pages per document (default: 3)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Render DPI (default: 150)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for sampling (default: 42)",
    )
    parser.add_argument(
        "--sample-file",
        type=str,
        default=None,
        help="Path to a previous sample_ids.json to reuse",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: data/ocr_benchmark/run_TIMESTAMP)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=5.0,
        help="Maximum total spend in USD before halting (default: $5)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse model keys
    model_keys = [k.strip() for k in args.models.split(",")]
    for mk in model_keys:
        if mk not in MODELS:
            print(f"ERROR: Unknown model key '{mk}'. Available: {', '.join(MODELS.keys())}")
            sys.exit(1)

    # Check API key (unless dry-run)
    api_key = os.environ.get("DEEPINFRA_API_KEY")
    if not args.dry_run and not api_key:
        print("ERROR: DEEPINFRA_API_KEY not set. Source .env or export the key.")
        sys.exit(1)

    # Sample selection
    print("Selecting sample...")
    if args.sample_file:
        with open(args.sample_file) as f:
            sample = json.load(f)
        print(f"  Loaded {len(sample)} documents from {args.sample_file}")
    else:
        sample = select_sample(sample_size=args.sample_size, seed=args.seed)

    if not sample:
        print("ERROR: No documents selected. Check DB and extraction status.")
        sys.exit(1)

    # Dry-run: just show sample
    if args.dry_run:
        print("\n=== DRY RUN: Sample Selection ===\n")
        print(f"{'Letter ID':<15} {'Year':>5} {'Tier':<10} {'Decade':<8} {'v3 Score':>9} {'Pages':>6}")
        print("-" * 60)
        for doc in sample:
            print(
                f"{doc['letter_id']:<15} {doc['year']:>5} {doc['tier']:<10} "
                f"{doc['decade']:<8} {doc['v3_score']:>9.4f} {doc['page_count']:>6}"
            )
        print(f"\nTotal: {len(sample)} documents")
        print(f"Models to benchmark: {', '.join(MODELS[mk].name for mk in model_keys)}")
        print(f"Pages per doc: {args.pages}, DPI: {args.dpi}")

        # Rough cost estimate
        est_pages = sum(min(d["page_count"], args.pages) for d in sample)
        print(f"\nEstimated total pages to process: {est_pages}")
        print(f"Estimated cost range: ${est_pages * 0.001:.2f} – ${est_pages * 0.01:.2f} per model")
        return

    # Set up output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = DATA_DIR / "ocr_benchmark" / f"run_{timestamp}"

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}")

    # Save config
    config = {
        "models": model_keys,
        "sample_size": args.sample_size,
        "pages": args.pages,
        "dpi": args.dpi,
        "seed": args.seed,
        "max_cost": args.max_cost,
        "timestamp": datetime.now().isoformat(),
    }
    (output_dir / "config.json").write_text(json.dumps(config, indent=2))

    # Save sample
    (output_dir / "sample_ids.json").write_text(json.dumps(sample, indent=2))

    # Create OpenAI client
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepinfra.com/v1/openai",
    )

    # Process documents
    results: list[DocumentResult] = []
    cumulative_cost = 0.0

    for i, doc in enumerate(sample):
        print(f"\n[{i + 1}/{len(sample)}] {doc['letter_id']} (year={doc['year']}, tier={doc['tier']}, v3={doc['v3_score']:.3f})")

        # Cost check
        if cumulative_cost >= args.max_cost:
            print(f"\n  HALT: Cumulative cost ${cumulative_cost:.4f} >= max ${args.max_cost:.2f}")
            break

        result = process_document(
            doc=doc,
            model_keys=model_keys,
            client=client,
            max_pages=args.pages,
            dpi=args.dpi,
            output_dir=output_dir,
        )

        if result:
            results.append(result)
            doc_cost = sum(mr.cost_usd for mr in result.model_results.values())
            cumulative_cost += doc_cost
            print(f"  Doc cost: ${doc_cost:.4f} | Cumulative: ${cumulative_cost:.4f}")

    # Save structured results
    results_data = []
    for r in results:
        rd = {
            "letter_id": r.letter_id,
            "year": r.year,
            "quality_tier": r.quality_tier,
            "decade": r.decade,
            "native_score": r.native_score,
            "native_word_count": r.native_word_count,
            "page_count": r.page_count,
            "pages_benchmarked": r.pages_benchmarked,
            "model_results": {k: asdict(v) for k, v in r.model_results.items()},
        }
        results_data.append(rd)

    (output_dir / "results.json").write_text(json.dumps(results_data, indent=2))

    # Generate report
    report = generate_report(results, model_keys, config, output_dir)
    (output_dir / "report.md").write_text(report)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Benchmark complete: {len(results)} documents processed")
    print(f"Total cost: ${cumulative_cost:.4f}")
    print(f"Results: {output_dir / 'results.json'}")
    print(f"Report:  {output_dir / 'report.md'}")
    print(f"{'=' * 60}")

    # Print quick summary table
    print(f"\n{'Model':<18} {'Avg v3':>8} {'Avg Δ':>8} {'Cost':>10}")
    print("-" * 46)
    native_avg = _mean([r.native_score for r in results])
    print(f"{'Native':<18} {native_avg:>8.3f} {'—':>8} {'—':>10}")
    for mk in model_keys:
        scores = [r.model_results[mk].v3_score for r in results if mk in r.model_results]
        deltas = [r.model_results[mk].delta_vs_native for r in results if mk in r.model_results]
        costs = [r.model_results[mk].cost_usd for r in results if mk in r.model_results]
        if scores:
            print(f"{MODELS[mk].name:<18} {_mean(scores):>8.3f} {_mean(deltas):>+8.3f} ${sum(costs):>9.4f}")


if __name__ == "__main__":
    main()
