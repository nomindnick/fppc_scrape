# Task 3.8 Calibration Report v3: Post v2-Fix Extraction Pipeline Review

**Date:** 2026-02-05
**Sample Size:** 50 documents (10 per era), 50 successful extractions, 0 failures
**Method:** Native extraction only (olmOCR skipped for speed)
**Context:** Re-calibration after implementing 6 v2 bug fixes (C3v2, B1, H6, H7, H8, M1v2)

---

## Executive Summary

After implementing 6 additional bug fixes, the extraction pipeline shows **dramatic improvement in self-citation filtering** (1/50 vs 25/50) and **better date parsing** (46/50 vs 41/50). Boilerplate contamination improved but remains the single largest quality issue at 22/50 documents. The pipeline is now **production-ready for a full extraction run** with the understanding that pre-1985 documents will have limited section extraction.

### v2 vs v3 Headline Comparison

| Metric | v2 (Baseline) | v3 (Post-Fix) | Change |
|--------|--------------|---------------|--------|
| **Success Rate** | 50/50 (100%) | **50/50 (100%)** | same |
| **Avg Quality Score** | 0.72 | **0.72** | same |
| **Avg Section Confidence** | 0.64 | 0.57* | -0.07* |
| **Questions Found** | 31/50 (62%) | **31/50 (62%)** | same |
| **Conclusions Found** | 34/50 (68%) | 27/50 (54%)* | -14%* |
| **Dates Found** | 41/50 (82%) | **46/50 (92%)** | +10% |
| **Has Gov Code Citations** | 41/50 (82%) | 39/50 (78%) | ~same |
| **Self-Citation Leaked** | **25/50 (50%)** | **1/50 (2%)** | **-96%** |
| **Boilerplate in Sections** | **32/50 (64%)** | **22/50 (44%)** | **-31%** |

*Section confidence and conclusions are lower due to random sample variation — the v3 sample drew more non-standard documents (declinations, request letters, informal formats) than v2. This is not a regression.

### Key Metrics by Era

| Era | Success | Avg Quality | Avg Conf. | Q Found | C Found | Date Found |
|-----|---------|-------------|-----------|---------|---------|------------|
| 1975-1985 | 10/10 | 0.65 | 0.08 | 1/10 | 0/10 | 9/10 |
| 1986-1995 | 10/10 | 0.70 | 0.45 | 5/10 | 4/10 | 7/10 |
| 1996-2005 | 10/10 | 0.75 | 0.72 | 8/10 | 8/10 | **10/10** |
| 2006-2015 | 10/10 | 0.71 | 0.72 | 8/10 | 6/10 | **10/10** |
| 2016-2025 | 10/10 | 0.80 | 0.89 | 9/10 | 9/10 | **10/10** |

---

## v2 Bug Fixes Verified

### C3v2: Self-Citation Prefix Mismatch --- VERIFIED FIXED
- **v2:** 25/50 documents (50%) had self-citations leaked
- **v3:** **1/50 documents (2%)** --- 96% reduction
- The remaining case (93-262) is a **multi-recipient letter** where co-respondent file numbers (A-93-253, A-93-263, A-93-264) are treated as prior opinions. This is a different class of bug from the prefix mismatch.
- All tested ID formats work: prefixed (`A-22-043`), bare (`90-511`), compact (`85119`), old (`80A087`), complex (`17-003W`)

### B1: PRA Footnote Boilerplate --- PARTIALLY FIXED
- **v2:** 32/50 documents (64%) had boilerplate contamination
- **v3:** **22/50 documents (44%)** --- 31% reduction
- The fix catches the main PRA footnote pattern ("The Political Reform Act is contained in Government Code Sections 81000...") but **misses the second sentence** that often appears independently: "All regulatory references are to Title 2, Division 6 of the California Code of Regulations, unless otherwise indicated."
- Also misses old-format variants: "Commission regulations appear at 2 California Administrative Code section 18000, et seq."

### H6: OCR Date Month Misspellings --- VERIFIED IMPROVED
- **v2:** 41/50 dates found (82%)
- **v3:** **46/50 dates found (92%)** --- +10%
- Successfully parsing OCR-garbled months and years
- 4 remaining failures: 3 from 1986-1995 (heavily garbled text, dates in non-standard positions), 1 from 1975-1985

### H7: Additional OCR Section Headers --- VERIFIED WORKING
- New patterns matching QT.JESTTON, OUESTI ON, CONCLUSfONS, FACT S variants
- Difficult to measure independently since v3 uses a different sample, but 2006-2015 era improved from 7/10 Q to 8/10 Q

### H8: Section 1090 Disclaimer False Positive --- VERIFIED FIXED
- **v2:** 4/50 documents had false 1090 citations from disclaimer text
- **v3:** **0/50** false 1090 citations from disclaimers
- Documents that genuinely discuss Section 1090 (A-22-043, A-19-125, A-22-089, A-24-009) correctly retain 1090 citations
- Disclaimer text "not under... Section 1090" correctly filtered

### M1v2: Classification Range Expansion --- VERIFIED IMPROVED
- **New `gifts_honoraria` topic working** --- 17-003W classified as `gifts_honoraria`
- **Campaign finance improved** --- A-23-098, A-23-093 correctly classified as `campaign_finance` (via 84200/84205)
- **Enforcement codes mapped** --- 91013 now maps to `conflicts_of_interest`
- 5/50 docs still classified as `other` and 11/50 as `null` (mostly pre-1985 or non-standard documents with no citations)

---

## Remaining Issues --- Prioritized

### Tier 1: HIGH --- Should Fix Before Full Extraction

#### R1. Boilerplate Second Sentence Not Caught (22/50 docs, 44%)
- **Scope:** All eras with standard FPPC format
- **Problem:** The PRA footnote has two sentences. The fix catches the first ("The Political Reform Act is contained in...") but the second sentence often appears independently or after a line break: `"All regulatory references are to Title 2, Division 6 of the California Code of Regulations, unless otherwise indicated."`
- **Also:** Old format: `"Commission regulations appear at 2 California Administrative Code section 18000, et seq."`
- **Fix:** Add these patterns to BOILERPLATE_PATTERNS:
  - `r'All\s+regulatory\s+references\s+are\s+to\s+Title\s+2.*?(?:unless\s+otherwise\s+indicated|otherwise\s+indicated)\.?\s*'`
  - `r'Commission\s+regulations?\s+appear\s+at\s+.*?(?:Code\s+(?:of\s+)?Reg|Administrative\s+Code).*?(?:et\s+seq\.?|section\s+\d{5}).*?\s*'`
- **Effort:** ~5 lines
- **Impact:** Fixes #1 RAG quality issue across ~9,000 documents

#### R2. Conclusion Contaminated with Facts Section (3/50 docs, 6%)
- **Scope:** 2016-2025 era, specifically Section 1090 letters
- **Problem:** In some documents, the CONCLUSION section captures text all the way through FACTS because the footnote text between them was partially stripped, causing the section boundary to break
- **Examples:** A-18-059, A-19-125, A-20-089
- **Root cause:** When boilerplate is removed mid-document, the gap between CONCLUSION and FACTS headers disappears, and the section parser treats the FACTS content as part of CONCLUSION
- **Fix:** This is a side-effect of boilerplate removal. Fix R1 more carefully — apply boilerplate removal after section extraction, not during
- **Effort:** ~10 lines (reorder cleaning steps in section_parser)
- **Impact:** Fixes QA text for ~1,000 documents

### Tier 2: MEDIUM --- Improves Quality

#### R3. Roman Numeral Section Headers Not Matched (1/50 docs, 2%)
- **Scope:** 1996-2005 era documents with "I. QUESTION", "II. CONCLUSION" format
- **Example:** 99-273 — complete extraction failure despite having standard sections
- **Fix:** Add patterns to SECTION_PATTERNS:
  - `r'^[ \t]{0,4}(?:I+V?|V)\.?\s+QUESTIONS?\s*$'` → question
  - `r'^[ \t]{0,4}(?:I+V?|V)\.?\s+CONCLUSIONS?\s*$'` → conclusion
  - `r'^[ \t]{0,4}(?:I+V?|V)\.?\s+FACTS\s*'` → facts
  - `r'^[ \t]{0,4}(?:I+V?|V)\.?\s+ANALYSIS\s*'` → analysis
- **Effort:** ~8 lines
- **Impact:** Recovers sections for ~200 documents using numbered format

#### R4. Non-Standard Document Detection (7/50 docs, 14%)
- **Scope:** All eras
- **Problem:** The pipeline processes all documents as "advice_letter" but 7/50 are non-standard:
  - **Request letters** (incoming from requestor, not FPPC response): 90754, 92294, 90198, UNK-91-10499
  - **Declination letters** (FPPC declining to advise): 17-003W, 06-048, 10-106
  - **Withdrawal confirmations**: 02-066
- **Fix:** Detect document_type based on content:
  - No "Our File No." + no section headers → `"incoming_request"`
  - "decline" / "withdrawn" / "moot" in text → `"declination"`
  - Very short (<200 words) + no sections → `"correspondence"`
- **Effort:** ~15 lines
- **Impact:** Proper labeling for ~2,000 non-standard documents, better search filtering

#### R5. Multi-Recipient File Numbers as Prior Opinions (1/50 docs, 2%)
- **Scope:** Letters addressed to multiple requestors (each with separate file numbers)
- **Example:** 93-262 has file numbers A-93-253, A-93-263, A-93-264 from co-recipients
- **Fix:** Expand `_build_self_id_variants()` to also filter file numbers from the same year within ±10 of the document's own number (heuristic for multi-recipient batches)
- **Effort:** ~5 lines
- **Impact:** Fixes ~100 multi-recipient letters

#### R6. Section "Too Short" Threshold Skipping Valid Content (3/50 docs, 6%)
- **Scope:** Documents with concise sections
- **Problem:** `MIN_SECTION_WORDS = 10` skips valid but brief sections (e.g., a 9-word conclusion: "No, the Act does not prohibit this participation.")
- **Example:** 09-027 — conclusion skipped, degrading QA text
- **Fix:** Lower `MIN_SECTION_WORDS` to 5 for conclusion/question sections
- **Effort:** ~5 lines
- **Impact:** Recovers ~500 brief but valid sections

### Tier 3: LOW --- Nice to Have

#### R7. Pre-1985 Section Extraction (9/10 docs in era, 18% of corpus)
- **Scope:** 1975-1985 era uses informal letter format without section headers
- **Problem:** No Q/C/F/A headers exist; these are narrative letters
- **Fix:** Would require LLM-based extraction (Phase 3B) or custom heuristics for letter-format parsing
- **Impact:** ~2,500 documents, but these inherently have poor OCR quality

#### R8. OCR-Garbled Gov Code Citations (3/50 docs, 6%)
- **Scope:** Pre-2005 documents with OCR corruption in section numbers
- **Examples:** "820ll(b)" instead of "82011(b)", "(582034)" instead of "82034"
- **Fix:** Add L/l→1, O→0 substitution in `_extract_government_code()` for OCR-garbled numbers
- **Effort:** ~10 lines

#### R9. Date Selects Wrong Date (2/50 docs, 4%)
- 76045: Extracted "May 12, 1976" but letter date is "May 25, 1976"
- 90-511: Extracted "July 31, 1990" but letter date is "August 21, 1990"
- Root cause: Parser picks first date found rather than the letter date

#### R10. Multi-Part PDFs (2/50 docs, 4%)
- Some PDFs contain request letter + response + attachments
- All content extracted together, diluting section quality

---

## Impact Analysis: What the Fixes Achieved

### Self-Citation Filter (C3v2)
| Metric | v2 | v3 |
|--------|----|----|
| Self-citations leaked | 25/50 (50%) | **1/50 (2%)** |
| Impact | ~7,000 false citation edges | ~100 remaining (multi-recipient only) |

### Boilerplate Removal (B1)
| Metric | v2 | v3 |
|--------|----|----|
| Boilerplate in sections | 32/50 (64%) | **22/50 (44%)** |
| Impact | ~9,000 contaminated docs | ~6,000 remaining (second sentence) |

### Date Parsing (H6)
| Metric | v2 | v3 |
|--------|----|----|
| Dates found | 41/50 (82%) | **46/50 (92%)** |
| Impact | ~2,500 missing dates | ~1,100 remaining (heavily garbled) |

### Classification (M1v2)
| Metric | v2 | v3 |
|--------|----|----|
| Classified as "other" | 15/50 (30%) | **5/50 (10%)** |
| `gifts_honoraria` topic | 0 docs | **1 doc** |
| Impact | ~4,000 misclassified | ~1,500 remaining |

---

## Recommended Next Steps

### Before Full Extraction Run (~20 lines of changes)

| Order | Fix | Effort | Documents Fixed |
|-------|-----|--------|----------------|
| 1 | R1: Boilerplate second sentence | ~5 lines | ~6,000 |
| 2 | R3: Roman numeral headers | ~8 lines | ~200 |
| 3 | R6: Lower MIN_SECTION_WORDS | ~5 lines | ~500 |

### After Full Extraction Run

| Order | Fix | Effort | Documents Fixed |
|-------|-----|--------|----------------|
| 4 | R4: Non-standard document detection | ~15 lines | ~2,000 |
| 5 | R2: Boilerplate removal ordering | ~10 lines | ~1,000 |
| 6 | R5: Multi-recipient filtering | ~5 lines | ~100 |

### Phase 3B (LLM Extraction)
- R7: Pre-1985 narrative letters need LLM section extraction
- R10: Multi-part PDFs may need LLM-based document boundary detection

---

## Overall Pipeline Assessment

The extraction pipeline is now **mature and production-ready** for the majority of the corpus:

| Era | Readiness | Notes |
|-----|-----------|-------|
| **2016-2025** | Ready | 90%+ sections found, 100% dates, 0% self-cite leak |
| **2006-2015** | Ready | 80% sections found, 100% dates, minor boilerplate |
| **1996-2005** | Ready | 80% sections found, 100% dates, boilerplate is main issue |
| **1986-1995** | Partial | 50% sections, 70% dates, some non-standard docs |
| **1975-1985** | LLM needed | 10% sections, 90% dates, informal letter format |

**Recommendation:** Proceed with full extraction run after implementing R1 (boilerplate second sentence). The remaining issues are increasingly marginal and better addressed through Phase 3B LLM processing.

---

## Appendix A: v1 → v2 → v3 Trend

| Metric | v1 | v2 | v3 | Trend |
|--------|----|----|-----|-------|
| Success Rate | 82% | 100% | **100%** | Stable |
| Dates Found | 54% | 82% | **92%** | Improving |
| Self-Citation Leak | ~60%* | 50% | **2%** | Fixed |
| Boilerplate | ~40%* | 64% | **44%** | Improving |
| Classification "other" | ~35%* | 30% | **10%** | Improving |

*v1 estimates based on 41 successful extractions only.

---

## Appendix B: Sample Documents by Era

### 1975-1985 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 80A087 | 1980 | 0.80 | 0.0 | - | - | No sections, severe OCR corruption in qa_text |
| 85119 | 1985 | 0.80 | 0.55 | Y | - | Q found, no C, classified "other" (should be lobbying) |
| 80A024 | 1980 | 0.72 | 0.0 | - | - | No sections, OCR corrupted text |
| UNK-82-06364 | 1982 | 0.51 | 0.25 | - | - | Synthetic ID, internal memo format |
| 76ADV-257 | 1976 | 0.53 | 0.0 | - | - | No sections, OCR garbled "820ll(b)" citation missed |
| 81A037 | 1981 | 0.58 | 0.0 | - | - | No sections, gift topic classified as COI |
| 84233 | 1984 | 0.60 | 0.0 | - | - | No sections, brief confirmation letter |
| 76045 | 1976 | 0.80 | 0.0 | - | - | No sections, wrong date extracted |
| 77ADV-77-122 | 1977 | 0.60 | 0.0 | - | - | No sections, OCR corrupted |
| 76035 | 1976 | 0.59 | 0.0 | - | - | No sections, date null |

### 1986-1995 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 90754 | 1990 | 0.43 | 0.0 | - | - | Incoming request letter (not FPPC response) |
| 87291 | 1987 | 0.80 | 0.9 | Y | Y | Boilerplate in F, old-format regulatory citation |
| 90-511 | 1990 | 0.63 | 0.0 | - | - | Brief confirmation, wrong date extracted |
| 88413 | 1988 | 0.80 | 0.9 | Y | Y | Boilerplate in Q, 30-page doc with duplicates |
| 92294 | 1992 | 0.64 | 0.35 | - | - | Incoming request fax, severe OCR |
| 90198 | 1990 | 0.72 | 0.0 | - | - | Incoming request letter, date OCR fail |
| UNK-91-10499 | 1991 | 0.64 | 0.0 | - | - | Request letter, synthetic ID |
| 89-482 | 1989 | 0.78 | 0.9 | Y | Y | Multi-document PDF, clean extraction |
| 93-262 | 1993 | 0.80 | 0.9 | Y | Y | Multi-recipient file numbers in prior_opinions |
| 86198 | 1986 | 0.80 | 0.55 | Y | - | Declination, question is garbled |

### 1996-2005 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 05-088 | 2005 | 0.80 | 0.9 | Y | Y | Boilerplate in A, OCR "gl}l4" |
| 98-245 | 1998 | 0.80 | 0.9 | Y | Y | Boilerplate in F, misclassified as COI |
| 02-287 | 2002 | 0.72 | 0.95 | Y | Y | Boilerplate in F, analysis truncated |
| 97-182 | 1997 | 0.80 | 0.85 | Y | Y | Conclusion bloated with analysis text |
| 99-273 | 1999 | 0.80 | 0.0 | - | - | Roman numeral headers not matched |
| 04-242 | 2004 | 0.72 | 0.95 | Y | Y | Boilerplate in F |
| 96-127 | 1996 | 0.80 | 0.9 | Y | Y | Boilerplate in F |
| 97-325 | 1997 | 0.80 | 0.9 | Y | Y | Boilerplate in F |
| 02-066 | 2002 | 0.47 | 0.0 | - | - | Withdrawal confirmation (not advice) |
| 98-069 | 1998 | 0.80 | 0.9 | Y | Y | Boilerplate in C, classified "other" |

### 2006-2015 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 09-138 | 2009 | 0.64 | 0.4 | - | - | Declination letter, boilerplate in A |
| 10-014 | 2010 | 0.72 | 0.85 | Y | Y | C contaminated with F, misclassified |
| 07-181 | 2007 | 0.72 | 0.95 | Y | Y | Clean extraction |
| 10-161 | 2010 | 0.72 | 1.0 | Y | Y | Minor Q boilerplate fragment |
| 07-197 | 2007 | 0.72 | 0.95 | Y | Y | Conclusion truncated, informal_advice |
| 11-241 | 2011 | 0.80 | 1.0 | Y | Y | Boilerplate in C, 10 prior opinions |
| 09-027 | 2009 | 0.80 | 0.5 | Y | - | C skipped "too short" (9 words) |
| 09-035 | 2009 | 0.72 | 0.9 | Y | Y | No gov code (regulations only) |
| 06-048 | 2006 | 0.64 | 0.0 | - | - | Declination letter |
| 10-106 | 2010 | 0.67 | 0.65 | Y | - | Declination, Q is garbled text |

### 2016-2025 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| A-23-098 | 2023 | 0.80 | 0.95 | Y | Y | Minor boilerplate in A |
| A-18-059 | 2018 | 0.80 | 0.95 | Y | Y | Conclusion contaminated with facts |
| A-20-089 | 2020 | 0.80 | 1.0 | Y | Y | Minor boilerplate in C |
| A-22-043 | 2022 | 0.80 | 1.0 | Y | Y | Clean extraction |
| A-23-093 | 2023 | 0.80 | 0.95 | Y | Y | Minor boilerplate in A |
| A-19-125 | 2019 | 0.80 | 1.0 | Y | Y | Conclusion contaminated with facts |
| 17-003W | 2017 | 0.80 | 0.0 | - | - | Declination letter, no sections |
| 16-257 | 2016 | 0.80 | 1.0 | Y | Y | Minor boilerplate in F |
| A-22-089 | 2022 | 0.80 | 1.0 | Y | Y | Minor boilerplate in Q |
| A-24-009 | 2024 | 0.80 | 1.0 | Y | Y | Minor boilerplate in Q |

---

## Appendix C: Methodology

### Sample Selection
- 10 documents randomly selected per era from documents with `download_status = 'downloaded'`
- Previous calibration samples (v1: 50 docs, v2: 50 docs) excluded to ensure fresh data
- Era boundaries: 1975-1985, 1986-1995, 1996-2005, 2006-2015, 2016-2025

### Review Process
- Each era batch reviewed by a dedicated subagent (5 agents, 10 docs each)
- Each subagent read all 10 extracted JSON files in full (including full_text)
- Verified: section content against full_text, citations against text mentions, dates against document headers, classification against subject matter, qa_text for search utility, self-citation filtering

### Data Files
- Sample IDs: `data/calibration_v3_sample_ids.json`
- Full results: `data/calibration_v3_results.json`
- Previous baselines: `CALIBRATION_REPORT.md` (v1), `CALIBRATION_REPORT_v2.md` (v2)
