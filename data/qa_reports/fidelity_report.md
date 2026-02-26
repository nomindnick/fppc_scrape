# Corpus Fidelity Verification Report

Generated: 2026-02-25 14:04

## Executive Summary

| Metric | Count | % |
|--------|------:|--:|
| Total extracted | 14,096 | 100% |
| Verified/trusted | 12,926 | 91.7% |
| Medium risk | 543 | 3.9% |
| High/critical risk | 627 | 4.4% |
| Unassessed | 0 | 0.0% |

## Risk Tier Distribution

| Risk Tier | Count | Description |
|-----------|------:|-------------|
| verified | 10,845 | Confirmed faithful (native or Haiku-verified) |
| low | 2,081 | Canary score > 0.70 — minor divergence, trusted |
| medium | 543 | Canary score 0.50-0.70 — some divergence |
| high | 605 | Canary score < 0.50 — significant divergence |
| critical | 22 | Description-mode or canary < 0.30 |

## Assessment Methods Used

| Method | Count | Description |
|--------|------:|-------------|
| native_trusted | 10,845 | PyMuPDF native extraction (deterministic, faithful) |
| tesseract_canary | 2,644 | Compared against Tesseract baseline |
| tesseract_fallback | 607 | Replaced with Tesseract extraction |

## olmOCR Fidelity Score Distribution

| Score Band | Count |
|-----------|------:|
| 0.90+ | 625 |
| 0.80-0.90 | 581 |
| 0.70-0.80 | 268 |
| 0.50-0.70 | 543 |
| < 0.50 | 627 |

## Phase Outcomes

### Phase 1: Tesseract Canary Scan
- Scanned: 3251 olmOCR documents
- Duration: 1.3 hours
- Description-mode detected: 9
- Tiers: critical=629, high=605, medium=543, low=1474

## Worst Documents (Critical/High Risk)

| ID | Letter ID | Year | Method | Fidelity | Risk |
|----|-----------|------|--------|----------|------|
| 5252 | 77ADV-77-367 | 1977 | olmocr | 0.000 | critical |
| 5540 | 78ADV-78-232 | 1978 | olmocr | 0.000 | critical |
| 5835 | 79-050 | 1979 | olmocr | 0.000 | critical |
| 5837 | 79-004 | 1979 | olmocr | 0.000 | critical |
| 5881 | 79-113 | 1979 | olmocr | 0.000 | critical |
| 5893 | 79-048 | 1979 | olmocr | 0.000 | critical |
| 6006 | 79-036 | 1979 | olmocr | 0.000 | critical |
| 6015 | 79-045 | 1979 | olmocr | 0.000 | critical |
| 8912 | 90617 | 1990 | olmocr | 0.000 | critical |
| 9160 | 90456 | 1990 | olmocr | 0.000 | critical |
| 6932 | 84157 | 1984 | olmocr | 0.003 | critical |
| 5907 | 79-003 | 1979 | olmocr | 0.004 | critical |
| 5950 | 79-141 | 1979 | olmocr | 0.006 | critical |
| 5948 | 79-007 | 1979 | olmocr | 0.006 | critical |
| 4553 | 75190 | 1975 | olmocr | 0.011 | critical |
| 8968 | 90493 | 1990 | olmocr | 0.016 | critical |
| 4953 | 76079 | 1976 | olmocr | 0.016 | critical |
| 9101 | 90352 | 1990 | olmocr | 0.029 | critical |
| 9434 | 90078 | 1990 | olmocr | 0.031 | critical |
| 9083 | 90396 | 1990 | olmocr | 0.040 | critical |
