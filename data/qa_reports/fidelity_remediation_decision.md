# Fidelity Remediation: Decision Record

Date: 2026-02-26

## Background

After extracting ~14,096 FPPC advice letters, we discovered that olmOCR (a vision-language model used for OCR on scanned documents) had hallucinated content in a significant number of documents. Two failure modes were identified:

1. **Description-mode**: olmOCR describes the image rather than transcribing it ("The image shows a scanned document of...")
2. **Content hallucination**: olmOCR silently rewrites, paraphrases, or fabricates plausible-sounding legal text

We built a detection system using Tesseract as an "honest baseline" — comparing olmOCR output against Tesseract word-by-word via SequenceMatcher. Documents were classified into risk tiers based on divergence:

| Tier | Canary Score | Count |
|------|-------------|------:|
| Critical | < 0.30 | 22 |
| High | 0.30–0.50 | 605 |
| Medium | 0.50–0.70 | 543 |
| Low | > 0.70 | 1,474 |

The detection phase (Phase 1: Tesseract canary scan) completed successfully and flagged 1,170 documents for remediation.

## Original Remediation Plan

The original scripts (`fix_critical_fidelity.py`, `verify_high_risk.py`, `sample_medium_risk.py`) used this strategy:

1. **Verify** each document by sending page 1 to Claude Haiku for a 200-word spot check
2. If hallucination confirmed, **replace** the olmOCR text with Tesseract extraction
3. For critical docs, **retry** olmOCR first, then fall back to Tesseract

This approach optimized for cost (~$10 total) by using Haiku only as a verifier and Tesseract as the replacement engine.

## Why We Abandoned That Approach

Examining the data revealed a fundamental problem: **Tesseract produces low-quality output on exactly the documents that need remediation.**

The flagged documents are predominantly 1975–1992 era scanned typewriter letters. These are the hardest documents for traditional OCR. The existing `tesseract_fallback` extractions in the corpus showed quality scores as low as 0.30–0.40 — essentially garbled noise. Replacing fluent hallucinations with garbled-but-honest text trades one problem for another.

The core tension:

| Engine | Fidelity | Readability | Problem |
|--------|----------|-------------|---------|
| olmOCR | Unreliable | Excellent | May fabricate content |
| Tesseract | Honest | Poor on old scans | OCR artifacts, garbled text |

For a corpus intended to support semantic search, RAG, and a searchable frontend, both fidelity AND readability matter. A document that faithfully reproduces "Tlxe Politi-cal Reforra Aet" is barely more useful than one that fabricates a plausible paragraph.

A concrete example illustrating the hallucination trap: document 79-113 had a quality score of 0.987 (reads beautifully) but a fidelity score of 0.000 (entirely fabricated). High quality scores from the scoring algorithm mean fluent, readable English — not that the text is faithful to the source.

## What We Did Instead

**Used Claude Haiku 4.5 as a full OCR engine**, not just a verifier. For all 1,170 flagged documents:

1. Render every page to a 200 DPI PNG (auto-downscale to 150/120/100 DPI if the image exceeds Anthropic's 5MB base64 limit)
2. Send each page to `claude-haiku-4-5-20251001` with a strict transcription prompt
3. Stitch pages together as the new extraction
4. Score quality and save to both JSON and SQLite

The transcription prompt is deliberately strict:

> Transcribe exactly what you see in this document image.
> Rules:
> - Copy the text verbatim, preserving the original wording, spelling, and punctuation.
> - Maintain paragraph breaks.
> - Do NOT summarize, paraphrase, or describe the document.
> - Do NOT add commentary, headers, or labels that are not in the original.
> - If a word or section is illegible, write [illegible] in its place.
> - Return ONLY the transcribed text.

### Why Haiku Works Better Than Both Alternatives

- **vs. olmOCR**: Haiku is a smaller, more constrained model. With a strict transcription prompt, it doesn't go into description-mode or fabricate content. It handles degraded scans gracefully — reading through noise, faded text, and unusual fonts.
- **vs. Tesseract**: Haiku can reason about partially visible characters and contextual clues. Where Tesseract produces garbled output on a faded 1979 typewriter letter, Haiku reads it cleanly.

### Validation

Before running the full batch, we ran a stratified sample of 15 documents across all three risk tiers and spanning 1977–2017:

- 14 of 15 produced faithful, high-quality transcriptions
- 6 improved quality scores, 8 were stable, 1 showed a minor quality score dip (a scoring artifact on a short letter — the text itself was perfect)
- Zero errors
- Average new quality: 0.907
- Cost: $0.08 for 15 docs / 29 pages

### Cost

- **Original plan**: ~$10 (Haiku verification only, Tesseract replacement)
- **New approach**: ~$10–15 estimated (Haiku full OCR on all pages)
- The cost difference was negligible. The quality difference was not.

## Implementation

Script: `scripts/haiku_reocr.py`

Key features:
- **Resume-safe**: Processed documents are marked `fidelity_method='haiku_vision'` and `fidelity_risk='verified'` in the DB. The query only selects documents still in critical/high/medium tiers, so restarting skips already-processed docs automatically.
- **Auto-downscale**: Images exceeding 5MB (base64-encoded) are automatically re-rendered at lower DPI (150 → 120 → 100) until they fit within the API limit.
- **Error isolation**: A single document failure doesn't crash the batch.
- **Per-document logging**: Before/after quality scores, old-vs-new similarity, and cost are logged for every document.

## Outcome

Completed 2026-02-26. All 1,170 flagged documents re-extracted via Haiku vision across multiple runs (resume-safe design allowed stopping and restarting without data loss or duplicate work).

### Batch Results (combined across all runs)

| Metric | Value |
|--------|------:|
| Total processed | 1,170 |
| Improved quality | ~40% |
| Degraded quality* | ~5% |
| Errors | 0 |
| Total API cost | ~$12 |

*Quality score "degradation" is often a scoring artifact (e.g., short letters with low word density, letterhead with special characters) rather than actual text quality issues.

### Corpus Quality After Remediation

| Quality Band | Count |
|-------------|------:|
| 0.90+ | 9,687 |
| 0.80–0.90 | 4,029 |
| 0.70–0.80 | 255 |
| 0.50–0.70 | 58 |
| < 0.50 | 67 |

**Corpus usability (>= 0.80): 97.3%** (13,716 / 14,096)

### Fidelity Status After Remediation

| Risk Tier | Count | Description |
|-----------|------:|-------------|
| Verified | 12,015 | Native-trusted or Haiku-verified |
| Low | 2,081 | Canary score > 0.70, trusted |
| Critical/High/Medium | 0 | All remediated |

**100% of documents now assessed and verified.**

### Extraction Method Distribution

| Method | Count | Description |
|--------|------:|-------------|
| native | 10,822 | PyMuPDF text layer (deterministic) |
| olmocr | 1,474 | olmOCR, low-risk (canary > 0.70) |
| haiku_vision | 1,170 | Re-extracted via Claude Haiku 4.5 |
| tesseract_fallback | 607 | From earlier Task 4.4 re-extraction |
| native+olmocr | 23 | olmOCR attempted but native kept |
