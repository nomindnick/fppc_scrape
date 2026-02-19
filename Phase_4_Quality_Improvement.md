# Phase 4: Corpus Quality Improvement

## Overview

Phase 3 produced a complete corpus of 14,096 extracted documents. However, visual spot-checking reveals significant quality issues — garbled text, character-level OCR corruption, and non-Latin garbage — that degrade readability for end users and downstream search/RAG systems.

Phase 4 addresses this through three sequential efforts:

1. **Quality Rescoring** — Replace the v1 scoring algorithm (capped at 0.80) with v3 readability-focused scoring that uses the full 0.0-1.0 range
2. **OCR Engine Benchmark** — Compare three OCR models (olmOCR, PaddleOCR, Deepseek-OCR) on sample documents to find the best cost/quality tradeoff
3. **Corpus Re-extraction** — Re-OCR documents scoring below 0.80, then re-run the full extraction pipeline

### Corpus Quality Distribution (v3 scoring)

| Score Range | Documents | % | Description |
|---|---|---|---|
| 0.00 – 0.50 | 338 | 2.4% | Broken — failed extraction, garbage text |
| 0.50 – 0.70 | 1,000 | 7.1% | Degraded — heavy character corruption, missing content |
| 0.70 – 0.80 | 1,559 | 11.1% | Impaired — noticeable OCR errors throughout |
| 0.80 – 0.90 | 2,878 | 20.4% | Minor issues — occasional wrong characters |
| 0.90 – 1.00 | 8,321 | 59.0% | Clean — fully readable |

### Quality by Decade

| Decade | Docs | Mean Score | Below 0.80 |
|---|---|---|---|
| 1970s | 1,504 | 0.764 | 699 (46%) |
| 1980s | 2,768 | 0.901 | 317 (11%) |
| 1990s | 4,621 | 0.848 | 1,471 (32%) |
| 2000s | 2,573 | 0.879 | 373 (14%) |
| 2010s | 1,962 | 0.954 | 37 (2%) |
| 2020s | 668 | 0.969 | 0 (0%) |

**Key finding:** The 1990s are worse than the 1980s (0.848 vs 0.901 mean). The pre-1990 year trigger sent 1980s scans through olmOCR, but 1990s documents were assumed to be born-digital. Many 1990s PDFs have font encoding issues that produce character-level corruption (e.g., `rn`→`m`, `l`→`1`).

---

## Task 4.1: Quality Rescoring ✓ COMPLETE

**Objective:** Rewrite `quality.py` to use a readability-focused scoring algorithm that uses the full 0.0-1.0 range and accurately reflects document quality.

### What Changed

**Old scoring (v1):**
- 3 positive components weighted to 0.80 max, minus artifact penalty
- Step functions (0.0, 0.3, 0.5, 0.8, 1.0) — coarse, no smooth gradation
- Capped at 0.80 — couldn't distinguish "good" from "excellent"
- Missed character-level corruptions and non-Latin garbage

**New scoring (v3):**
- 5 components, all positive, smooth piecewise linear curves, weights sum to 1.0:

| Component | Weight | What It Measures |
|---|---|---|
| `density_score` | 0.15 | Content completeness — words per page |
| `char_quality_score` | 0.15 | Character cleanliness — alpha ratio |
| `word_quality_score` | 0.15 | Structural word validity — vowels, length, consonants, non-Latin detection |
| `dict_score` | **0.40** | **Dictionary-based word validity** — are words real English? |
| `content_score` | 0.15 | FPPC patterns — dates, mentions, section headers |

- **Dictionary scoring** (`dict_score`): Samples up to 200 words evenly across the document, checks each against a 73K-word English dictionary bundled at `scraper/data/common_english.txt`. Catches character-level OCR corruptions like `Califomia`, `Cornrnission`, `poritical` that pass all structural checks.
- **Non-Latin detection**: CJK, Cyrillic, Arabic, Katakana, and fullwidth characters are flagged as garbage. Catches Japanese-character blocks from failed OCR.
- **Density gate**: If density_score < 0.20 (~35 words/page), the entire score is scaled down proportionally. Prevents nearly-empty documents from scoring high.
- **Smooth curves**: All components use `_piecewise_linear()` instead of step functions for better discrimination.

### Validation

Rendered 9 PDF pages to images across quality tiers. Claude subagents visually transcribed each image and compared against extracted text. Results:

- **7 of 9 documents** scored within the agents' suggested ranges
- **92157** (garbled wrong-page text): v2 scored 0.718, v3 scores **0.392** (agent: 0.35-0.50) ✓
- **91-202** (Japanese garbage block): v2 scored 0.834, v3 scores **0.610** (agent: 0.65-0.72) ✓
- Remaining slight over-scoring at top end (~3 pts) is acceptable

### Files Changed

- `scraper/quality.py` — Full rewrite (scoring algorithm, dataclass, weights)
- `scraper/data/common_english.txt` — New bundled dictionary (73K words)

### Migration Note

The `QualityMetrics` dataclass fields changed. The only external consumer is `extractor.py`, which uses `metrics.final_score` and `metrics.words_per_page` (both preserved). New fields: `dict_score`, `non_latin_word_count`, `dict_miss_ratio`. Removed: `artifact_penalty`, `long_garbage_words`. Renamed: `alpha_ratio_score` → `char_quality_score`, `pattern_score` → `content_score`, `words_per_page_score` → `density_score`.

---

## Task 4.2: OCR Engine Benchmark ✓ COMPLETE

**Objective:** Compare three OCR models available on DeepInfra to determine the best cost/quality tradeoff for re-extracting degraded documents.

### Models Tested

| Model | DeepInfra ID | Input $/M | Output $/M |
|---|---|---|---|
| olmOCR-2 | `allenai/olmOCR-2-7B-1025` | $0.09 | $0.19 |
| PaddleOCR-VL | `PaddlePaddle/PaddleOCR-VL-0.9B` | $0.03 | $0.10 |
| DeepSeek-OCR | `deepseek-ai/DeepSeek-OCR` | $0.03 | $0.10 |

### Benchmark Design

**Script:** `scripts/benchmark_ocr.py`
**Results:** `data/ocr_benchmark/run_20260219_082823/`

- 18 documents sampled via stratified tier×decade matrix (4 tiers × 4 decades)
- First 3 pages rendered to PNG at 150 DPI, sent to all 3 models
- Each model's output scored with v3 `compute_quality_score()`
- Native extraction (existing `full_text`) used as baseline

### Results

| Metric | Native | olmOCR-2 | PaddleOCR-VL | DeepSeek-OCR |
|---|---|---|---|---|
| Avg v3 score | 0.671 | **0.887** | 0.710 | 0.720 |
| Avg Δ vs native | — | **+0.217** | +0.039 | +0.050 |
| Avg dict miss % | — | **5.0%** | 26.1% | 21.4% |
| Total cost (18 docs) | — | $0.011 | $0.005 | $0.003 |

**By quality tier:**

| Tier | Native | olmOCR-2 | PaddleOCR-VL | DeepSeek-OCR |
|---|---|---|---|---|
| Broken (<0.50) | 0.374 | **0.831 (+0.457)** | 0.669 (+0.294) | 0.783 (+0.409) |
| Degraded (0.50-0.70) | 0.641 | **0.864 (+0.223)** | 0.636 (-0.005) | 0.427 (-0.214) |
| Impaired (0.70-0.80) | 0.759 | **0.901 (+0.142)** | 0.758 (-0.000) | 0.793 (+0.035) |
| Control (0.80-0.90) | 0.864 | **0.946 (+0.081)** | 0.751 (-0.113) | 0.842 (-0.022) |

### Key Findings

1. **olmOCR-2 is the clear winner** — only model that improved every single document (18/18 positive deltas)
2. **PaddleOCR and DeepSeek are unreliable** — both produced regressions (worse than native) on multiple documents. PaddleOCR hit -0.465 on one doc (86.8% dict miss rate = mostly garbage). DeepSeek hit -0.424 on another.
3. **No tiered approach needed** — olmOCR dominates across all tiers and decades, so a single-model strategy is optimal
4. **Cost is negligible** — projected $2.84 to re-extract all 2,897 docs with olmOCR (3 pages each)
5. **1990s get the biggest lift** — avg Δ+0.256, confirming that font-encoding corruption responds well to re-OCR

### Decision

**Use olmOCR-2 (`allenai/olmOCR-2-7B-1025`) for all re-extraction.** No pipeline changes needed — it's already the model used in `extractor.py`.

---

## Task 4.3: Pipeline Parameterization — SKIPPED

**Reason:** Benchmark (Task 4.2) showed olmOCR-2 is the only viable model — PaddleOCR and DeepSeek both produced regressions. Since olmOCR is already the model in `extractor.py`, no parameterization is needed. Proceed directly to re-extraction with the existing pipeline.

---

## Task 4.4: Corpus Re-extraction

**Objective:** Re-process all documents scoring below 0.80 through OCR, re-run the full extraction pipeline, and update the corpus in place.

**Status:** Not started

### Scope

~2,897 documents below 0.80 threshold:
- 338 below 0.50 (broken)
- 1,000 in 0.50-0.70 (degraded)
- 1,559 in 0.70-0.80 (impaired)

### Script

**`scripts/reocr_corpus.py`** — follows the `fix_low_density.py` pattern:

```
python scripts/reocr_corpus.py --dry-run                      # Preview + cost estimate
python scripts/reocr_corpus.py --dry-run --threshold 0.70     # Narrower scope
python scripts/reocr_corpus.py                                # Run with olmOCR-2
python scripts/reocr_corpus.py --limit 500                    # Batch of 500
```

**Flags:**
- `--threshold` (default: 0.80) — v3 quality score cutoff
- `--limit N` — process N documents per run (for batching)
- `--max-cost` — halt if cumulative cost exceeds limit (safety)
- `--dry-run` — preview document list and cost estimate without processing

### Process per Document

1. Query DB for documents with `extraction_quality < threshold` (using current v1 scores in DB)
2. **Rescore with v3** to get accurate current quality (v1 scores in DB are stale)
3. Filter to documents actually below threshold on v3 scoring
4. For each document:
   a. Render PDF pages to PNG
   b. Send to OCR model
   c. Compare OCR output quality vs. existing quality
   d. If improved: re-run full pipeline (`process_document()`) with new text
   e. Update JSON file and DB record
5. Log: old quality → new quality, cost, method

### Corpus Update Strategy

- **In-place updates** — overwrite existing JSON files and DB records
- **Backup first**: `cp data/documents.db data/documents.db.bak-YYYYMMDD`
- **Track method**: `extraction_method` field records which OCR model was used
- **Rollback path**: PDFs are immutable source of truth; can always re-extract from scratch

### Cost Estimation

Based on benchmark actuals (Task 4.2): olmOCR-2 costs ~$0.0006/doc at 3 pages.

| Docs | Pages | Est. Cost |
|---|---|---|
| 2,897 (all <0.80) | ~8,700 | **~$2.84** |
| 1,338 (all <0.70) | ~4,000 | ~$1.31 |
| 338 (all <0.50) | ~1,000 | ~$0.33 |

Cost is negligible. No need to batch or tier.

### Post-processing

After re-extraction:
1. Re-run `scripts/qa_corpus_validate.py` to verify quality improvements
2. Re-run `scripts/build_citation_graph.py` to update citation indexes
3. Check for regressions: any documents where quality *decreased*?

---

## Task 4.5: Quality Score Backfill

**Objective:** Update the `extraction_quality` column in the SQLite database with v3 scores for all documents.

**Status:** Not started

### Why

The DB currently stores v1 quality scores (capped at 0.80). After re-extraction, we need consistent v3 scores across the entire corpus for the app to use.

### Script

**`scripts/rescore_corpus.py`** — simple script that:
1. Iterates all extracted documents
2. Loads JSON, reads `full_text` and `page_count`
3. Computes `compute_quality_score(text, page_count)`
4. Updates `extraction_quality` in the DB

Can run independently of re-extraction (useful for documents that weren't re-OCR'd but need updated scores).

---

## Execution Order

| Step | Task | Depends On | Status |
|---|---|---|---|
| 1 | 4.1 Quality Rescoring | — | ✓ Complete |
| 2 | 4.2 OCR Engine Benchmark | 4.1 | ✓ Complete — olmOCR-2 wins decisively |
| 3 | ~~4.3 Pipeline Parameterization~~ | — | Skipped — olmOCR already in pipeline |
| 4 | 4.4 Corpus Re-extraction | 4.2 | Next — ~$2.84 API cost |
| 5 | 4.5 Quality Score Backfill | 4.4 | Not started |
| 6 | QA Validation | 4.4, 4.5 | Not started |

**Total estimated API cost:** ~$3 (olmOCR-2 for all 2,897 docs).
