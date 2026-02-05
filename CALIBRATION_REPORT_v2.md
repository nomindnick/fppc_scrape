# Task 3.8 Calibration Report v2: Post-Fix Extraction Pipeline Review

**Date:** 2026-02-05
**Sample Size:** 50 documents (10 per era), 50 successful extractions, 0 failures
**Method:** Native extraction only (olmOCR skipped for speed)
**Context:** Re-calibration after implementing 8 bug fixes (C1-C3, H1-H5)

---

## Executive Summary

After implementing 8 calibration bug fixes, the extraction pipeline shows **dramatic improvement in success rate** (100% vs 82%) and **meaningful gains in section/citation extraction**. However, several systematic issues remain, most notably a pervasive **self-citation filter bug** affecting 25/50 documents and **persistent boilerplate contamination** in 32/50 documents.

### v1 vs v2 Headline Comparison

| Metric | v1 (Baseline) | v2 (Post-Fix) | Change |
|--------|--------------|---------------|--------|
| **Success Rate** | 41/50 (82%) | **50/50 (100%)** | +18% |
| **Avg Quality Score** | 0.76 | 0.72 | -0.04* |
| **Avg Section Confidence** | 0.71 | 0.64 | -0.07* |
| **Questions Found** | 26/41 (63%) | 31/50 (62%) | ~same |
| **Conclusions Found** | 31/41 (76%) | 34/50 (68%) | ~same |
| **Dates Found** | 22/41 (54%) | 41/50 (82%) | +28% |
| **Has Gov Code Citations** | ~30/41 (73%) | 41/50 (82%) | +9% |

*Quality/confidence averages are lower because v2 includes 10 newly-unblocked 1975-1985 docs with inherently poor OCR text. When comparing only the 4 eras present in v1, quality is comparable or improved.

### Key Metrics by Era

| Era | Success | Avg Quality | Avg Conf. | Q Found | C Found | Date Found |
|-----|---------|-------------|-----------|---------|---------|------------|
| 1975-1985 | **10/10** (was 2/10) | 0.60 | 0.07 | 1/10 | 1/10 | 8/10 |
| 1986-1995 | **10/10** (was 9/10) | 0.70 | 0.44 | 4/10 | 5/10 | 5/10 |
| 1996-2005 | 10/10 (was 10/10) | 0.75 | 0.89 | 10/10 | 9/10 | **10/10** |
| 2006-2015 | 10/10 (was 10/10) | 0.74 | 0.83 | 7/10 | 10/10 | 5/10 |
| 2016-2025 | 10/10 (was 10/10) | 0.79 | 0.94 | 9/10 | 9/10 | 9/10 |

---

## What Improved (Bug Fixes Verified)

### C1: `.PDF` Extension Case-Sensitivity --- VERIFIED FIXED
- **Impact:** 1975-1985 era went from 2/10 to **10/10** success
- All 10 documents with `.PDF` uppercase extensions now extract successfully
- This unblocks ~4,000 documents (29% of corpus)

### C2: Null Document ID Recovery --- VERIFIED FIXED
- **3 null-ID documents** successfully recovered IDs from text:
  - doc#10318: recovered "I-91-495" from "Our FiIe No. I-91-495"
  - doc#4341: recovered "A-23-079" from "Our File No. A-23-079"
  - doc#4125: recovered "A-21-011" from "Our File No. A-21-011"
  - doc#4411: recovered "A-23-165" from "Our File No. A-23-165"
- **1 document** correctly used synthetic fallback: "UNK-91-10230" (OCR too garbled for recovery)
- Zero `None.json` files produced

### H3: Date Parsing --- VERIFIED IMPROVED
- Dates like "September 27,2002" (no space after comma) and "April6, 2001" (no space after month) now parse correctly
- 1996-2005 era improved from 5/10 to **10/10** dates found
- Overall date extraction: 22/41 (54%) -> 41/50 (82%)

### H4: Section 1090 Citations --- VERIFIED FIXED
- Document 16-079-1090 (a Section 1090 letter) now captures `government_code: ["1090", "1091", "1091.5"]`
- Section 1090 correctly classified as `conflicts_of_interest`
- **New issue identified:** 4 documents capture "1090" from disclaimer text ("not under Section 1090") as a false positive citation

### H5: Prior Opinion Regex --- VERIFIED IMPROVED
- OCR prefix mapping (4->A, 1->I) working
- Document A-23-165 captured 6 prior opinions from footnotes (A-18-098, A-20-072, A-20-085, A-20-113, A-21-043, A-21-154)
- Document 09-063 captured 11 prior opinions (including legitimate cross-references)
- Remaining gaps: OCR-garbled IDs like "A-eg-gg6", "A-15-1 Si", "l-03-217" still missed

### H1: Boilerplate Removal --- PARTIALLY WORKING
- Some boilerplate patterns stripped successfully
- **BUT:** The PRA footnote ("The Political Reform Act is contained in Government Code Sections 81000...") still leaks into sections in **32/50 documents**
- Root cause: OCR variants of the footnote ("Gorernment", "sectiors", "gl)14") and superscript markers rendered as "I", "t", "'" don't match the regex

### H2: OCR Section Headers --- PARTIALLY WORKING
- Some OCR patterns matched (e.g., "QUESTTON" matched in some documents)
- **BUT:** Variants like "QT.JESTTON", "QTJESTTON", "OUESTI ON", "CONCLUSfONS", "FACT S", "A}[ALYSIS", "AI\ALYSIS" still missed
- 3/10 docs in 2006-2015 era had unmatched OCR question headers

### C3: Self-Citation Filter --- NOT WORKING FOR MOST DOCUMENTS
- **25/50 documents** still have self-citations leaked into `prior_opinions`
- Root cause: `_build_self_id_variants()` only handles IDs starting with `[AIM]-`
- When the DB stores "90-753" but text says "A-90-753", the filter doesn't match
- When the DB stores "84263" but text says "A-84-263", the filter doesn't match
- Affects nearly all pre-2016 documents where the DB ID lacks the letter prefix

---

## Remaining Issues --- Prioritized

### Tier 1: CRITICAL --- Fix Before Full Extraction Run

#### C3v2. Self-Citation Filter Prefix Mismatch (25/50 docs, 50%)
- **Scope:** All documents where DB `letter_id` lacks the A-/I-/M- prefix
- **Root Cause:** `_build_self_id_variants()` only generates variants for IDs matching `^[AIM]-`. For IDs like "90-753", "84263", "07-164", it generates only the literal string. But `_normalize_prior_opinion()` converts text references like "A-90-753" or "I-07-164" which don't match.
- **Fix:** In `_build_self_id_variants()`, also generate prefix variants:
  - For "YY-NNN" format: add "A-YY-NNN", "I-YY-NNN"
  - For "YYYYY" format: add "A-YY-NNN" decomposed form
  - For "83A195" format: add "A-83-195" decomposed form
  - Also use the `letter_id` recovered from text (if available) as a variant source
- **Effort:** ~15 lines
- **Impact:** Fixes citation graph for 50% of corpus

#### B1. PRA Footnote Boilerplate Still Leaking (32/50 docs, 64%)
- **Scope:** All eras, especially 1996-2015 where OCR varies the format
- **Root Cause:** The `BOILERPLATE_PATTERNS` regex requires "The Political Reform Act is contained in Government Code Sections 81000" but OCR renders this as:
  - "I Government Code sections 81000 - gl)14" (superscript "1" -> "I")
  - "t Government Code sections 81000-91014" (superscript "1" -> "t")
  - "' Gorernment Code sections 81000" (superscript "1" -> "'")
  - "Go,r\"..r*\"nt Code Sections SIOOO-91015" (heavy OCR garble)
  - Standard form but with varying whitespace and line breaks
- **Fix:** Make boilerplate patterns much more permissive:
  - `r'[1ItI\'"]?\s*(?:The\s+)?(?:Political\s+Reform\s+Act|Gov[eornmt.\s]+Code\s+[Ss]ections?\s+81000).*?(?:unless otherwise indicated|California Code of Regulations)\.?\s*'`
  - Add OCR-tolerant variants
  - Apply with `re.DOTALL` to span line breaks
- **Effort:** ~20 lines
- **Impact:** Cleans qa_text for 64% of corpus --- #1 RAG quality improvement

### Tier 2: HIGH --- Significantly Improves Quality

#### H6. OCR Date Month Misspellings (9/50 docs, 18%)
- **Scope:** Primarily 1986-1995 and 2006-2015 eras
- **Problem:** OCR garbles month names: "Idy" (July), "Iuly" (July with capital I), "htly" (July), "ÅtgUl,l" (August). Also "L99L" for "1991", "1-99 O" for "1990".
- **Fix:** Add OCR month name mapping (Idy->July, Iuly->July, htly->July, etc.) and OCR-tolerant year patterns (L->1 substitution, ignore spaces/hyphens in 4-digit years)
- **Effort:** ~25 lines
- **Impact:** Recovers dates for ~2,000 OCR-affected documents

#### H7. Additional OCR Section Header Patterns (6/50 docs, 12%)
- **Scope:** 1986-2010 era documents
- **Problem:** Several OCR header variants not yet matched:
  - `QT.JESTTON`, `QTJESTTON` (period/no-period variants)
  - `OUESTI ON` (space inside word)
  - `CONCLUSfONS` (f for I)
  - `FACT S` (space in word)
  - `A}[ALYSIS`, `AI\ALYSIS` (bracket/backslash substitution)
  - `OUESTTON` (double garble)
- **Fix:** Add more OCR-tolerant patterns to SECTION_PATTERNS:
  - `r'^[ \t]{0,4}Q[T.]?[JTIUE]*E?S?T[TI]?ON'` (catch-all for QUESTION variants)
  - `r'^[ \t]{0,4}CONCLU\s*S?\s*[fI]?\s*ONS?'` (CONCLUSfONS, etc.)
  - `r'^[ \t]{0,4}FACT\s*S'` (FACT S with space)
  - `r'^[ \t]{0,4}A[I}\]\\N]*[LA]*YS[IT1]\s*S'` (ANALYSIS OCR variants)
- **Effort:** ~15 lines
- **Impact:** Recovers sections for ~1,500 documents

#### H8. Section 1090 Disclaimer False Positive (4/50 docs, 8%)
- **Scope:** Modern advice letters that mention Section 1090 in disclaimer text
- **Problem:** Text like "not under other general conflict of interest prohibitions such as common law conflict of interest or Section 1090" causes "1090" to be captured as a citation even though it's a disclaimer
- **Fix:** Add negative lookbehind or context check: skip 1090 captures that appear within "not under...Section 1090" or "not providing advice under...Section 1090" patterns
- **Effort:** ~10 lines
- **Impact:** Prevents false classification for ~1,000 modern letters

### Tier 3: MEDIUM --- Improves Quality But Not Blocking

#### M1v2. Classification Still Defaults to "other" (15/50 docs, 30%)
- **Scope:** All eras
- **Problem:** Documents about lobbying (86xxx), gifts/honoraria (895xx, 82028), disclosure (82048), and campaign filing (84200-84303) still classified as "other"
- **Root Cause:** `TOPIC_RANGES` in classifier.py is too narrow:
  - Lobbying range 86100-86400 doesn't cover 86115(b) etc.
  - Gift sections (82028, 89503, 89506) not mapped
  - Campaign filing (84200-84303) not in campaign_finance range
  - Disclosure (82048) not in conflicts_of_interest
- **Fix:** Expand TOPIC_RANGES:
  - Add `range(82015, 82055)` to conflicts_of_interest (gifts/disclosure)
  - Add `range(89500, 89602)` to a new "gifts_honoraria" topic or to conflicts_of_interest
  - Add `range(84200, 84304)` to campaign_finance
  - Add `range(86100, 86120)` to lobbying
- **Effort:** ~20 lines
- **Impact:** Better classification for ~4,000 documents

#### M2v2. Requestor Name Parsing Errors (5/50 docs, 10%)
- **Scope:** All eras
- **Problem:** Name parsed as "Witham\nThis", "Cameron\nThis", "Breezei\nThank", "Mac" (should be "MacLeamy"), "Gi" (OCR split)
- **Fix:** Add sentence boundary detection: stop name capture at `\n`, period, comma-followed-by-lowercase
- **Effort:** ~10 lines

#### M3v2. Requestor Title from Section 1090 Boilerplate (2/50 docs, 4%)
- **Scope:** Section 1090 advice letters
- **Problem:** Title parsed as "District Attorney" from "forwarded...to the District Attorney's Office" boilerplate, not the actual requestor's title
- **Fix:** Restrict title parsing to text before "Dear..." salutation
- **Effort:** ~5 lines

#### M4v2. "Cal. Adm. Code" Regulation Citations Missed (2/50 docs, 4%)
- **Scope:** Pre-1988 documents
- **Problem:** Before 1988, California regulations were cited as "Cal. Adm. Code" not "Cal. Code Regs." The pattern `r'2\s+Cal\.?\s+Adm\.?\s+Code.*?(\\d{5}(?:\\.\\d+)?)'` is missing
- **Fix:** Add pattern to REGULATION_PATTERNS
- **Effort:** ~3 lines

#### M5v2. Non-Standard Documents Not Flagged (3/50 docs, 6%)
- **Scope:** Incoming letters (requestor -> FPPC), declination letters (W- prefix), short confirmations
- **Problem:** These are classified as "advice_letter" but contain no substantive legal analysis
- **Fix:** Detect and flag document_type: "incoming_request" (no "Our File No."), "declination" (W- prefix or "decline to provide written advice"), "confirmation" (very short)
- **Effort:** ~15 lines

### Tier 4: LOW --- Nice to Have

#### L1. Date Selects Event Date Instead of Letter Date (3/50 docs)
- Picks "December 10, 1990" (call date) instead of "January 15, 1991" (response date)
- Picks "January 1, 1991" (effective date) instead of "September 24, 1991" (letter date)

#### L2. Image-Only PDFs Produce Unusable Text (1/50 docs)
- Document 76030 has 8 words and quality score 0.125 -- needs OCR

#### L3. Multi-Part PDFs Dilute Extraction (3/50 docs)
- PDFs containing request letter + response + attachments + acknowledgment letter
- All content extracted together, inflating word count and diluting qa_text

#### L4. OCR-Garbled Gov Code Sections Missed (1/50 docs)
- "(582034)" and "15512034)" not matched as Section 82034

#### L5. Prior Opinions in "FPPC Ops." Format Missed (1/50 docs)
- "2 FPPC Ops. (No. 75-169)" not matched by PRIOR_OPINION_PATTERNS

---

## Impact Analysis: What Matters Most

### For Search/RAG (Primary Goal)

| Priority | Fix | Impact on qa_text |
|----------|-----|-------------------|
| 1 | B1: PRA footnote cleanup | Removes identical boilerplate from 64% of embeddings |
| 2 | H7: OCR section headers | Recovers Q/C for ~1,500 docs -> better qa_text |
| 3 | H6: OCR date parsing | Enables date filtering for ~2,000 more docs |
| 4 | M1v2: Classification | Better topic facets for search |

### For Citation Graph (Phase 3.11)

| Priority | Fix | Impact |
|----------|-----|--------|
| 1 | C3v2: Self-citation prefix | Removes ~50% of false citation edges |
| 2 | H8: 1090 disclaimer | Removes ~1,000 false gov code citations |
| 3 | M4v2: Cal. Adm. Code | Captures pre-1988 regulation citations |

---

## Recommended Fix Order

| Order | Bug | Effort | Documents Fixed |
|-------|-----|--------|----------------|
| 1 | C3v2: Self-citation prefix mismatch | ~15 lines | ~7,000 |
| 2 | B1: PRA footnote boilerplate (OCR variants) | ~20 lines | ~9,000 |
| 3 | H6: OCR date month misspellings | ~25 lines | ~2,000 |
| 4 | H7: Additional OCR section headers | ~15 lines | ~1,500 |
| 5 | H8: Section 1090 disclaimer false positive | ~10 lines | ~1,000 |
| 6 | M1v2: Classification range expansion | ~20 lines | ~4,000 |
| 7 | M2v2: Requestor name parsing | ~10 lines | ~1,500 |
| 8 | M4v2: Cal. Adm. Code regulations | ~3 lines | ~500 |

**Total effort for top 5 fixes: ~85 lines of code changes**
**Impact: Fixes systematic issues across ~20,000 document-instances**

---

## Appendix A: Era-Level Bug Cross-Reference

| Bug | 1975-85 | 1986-95 | 1996-05 | 2006-15 | 2016-25 |
|-----|---------|---------|---------|---------|---------|
| C3v2: Self-cite prefix | 2/10 | 5/10 | 8/10 | 10/10 | 2/10 |
| B1: PRA footnote | 0/10 | 6/10 | 10/10 | 7/10 | 9/10 |
| H6: OCR date month | 0/10 | 5/10 | 0/10 | 4/10 | 0/10 |
| H7: OCR section headers | 0/10 | 3/10 | 2/10 | 3/10 | 0/10 |
| H8: 1090 disclaimer | 0/10 | 0/10 | 0/10 | 0/10 | 4/10 |
| M1v2: Classification | 3/10 | 5/10 | 2/10 | 1/10 | 0/10 |

---

## Appendix B: Sample Documents by Era

### 1975-1985 (10/10 success --- was 2/10)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 78ADV-78-039 | 1978 | 0.72 | 0.0 | - | - | No sections (informal letter format), date correct |
| 76ADV-250 | 1976 | 0.63 | 0.0 | - | - | No sections, date correct |
| 84263 | 1984 | 0.80 | 0.75 | Y | Y | Self-cite leaked (A-84-263), Cal. Adm. Code missed |
| 82A037 | 1982 | 0.63 | 0.0 | - | - | Date OCR: "1922" for "1982", near-empty text |
| 80A012 | 1980 | 0.51 | 0.0 | - | - | Severely degraded OCR, few words extracted |
| 76ADV-534 | 1976 | 0.72 | 0.0 | - | - | Cal. Adm. Code missed, no sections |
| 76030 | 1976 | 0.13 | 0.0 | - | - | Image-only PDF, 8 words, needs OCR |
| 82A128 | 1982 | 0.72 | 0.0 | - | - | No sections, date correct |
| 83A195 | 1983 | 0.60 | 0.0 | - | - | Self-cite leaked (A-83-195) |
| 77A-290 | 1977 | 0.60 | 0.0 | - | - | No sections, date correct |

### 1986-1995 (10/10 success --- was 9/10)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 90-753 | 1990 | 0.72 | 0.0 | - | - | Self-cite leaked, wrong date (event vs letter), no sections |
| I-91-495 | 1991 | 0.72 | 0.55 | - | Y | ID recovered from text, "OUESTI ON" not matched, boilerplate in C |
| 92335 | 1992 | 0.55 | 0.0 | - | - | Incoming letter (not FPPC advice), date OCR fail |
| 90467 | 1990 | 0.72 | 0.0 | - | - | Incoming memorandum, name garbled |
| 92-309 | 1992 | 0.80 | 0.9 | Y | Y | Self-cite leaked, boilerplate in C, classified "other" should be campaign |
| 89-090 | 1989 | 0.72 | 0.0 | - | - | Self-cite leaked, multi-part PDF |
| 90-395 | 1990 | 0.63 | 0.55 | Y | - | Self-cite leaked, Q/C merged, heavy OCR garble |
| 88460 | 1988 | 0.68 | 0.9 | Y | Y | Self-cite leaked, boilerplate in C |
| 87209 | 1987 | 0.80 | 0.9 | Y | Y | Self-cite leaked, classified "other" should be lobbying |
| UNK-91-10230 | 1991 | 0.72 | 0.55 | - | Y | Synthetic ID (OCR too garbled), wrong date, "OUESTTON" unmatched |

### 1996-2005 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 02-012 | 2002 | 0.72 | 0.95 | Y | Y | Self-cite leaked (I-02-012), boilerplate in F |
| 01-186 | 2001 | 0.80 | 0.95 | Y | Y | Self-cite leaked, boilerplate in C, classified "other" |
| 96-072 | 1996 | 0.80 | 0.90 | Y | Y | Self-cite leaked, boilerplate in F, missed 2 prior opinions |
| 04-218 | 2004 | 0.72 | 0.95 | Y | Y | Self-cite leaked, boilerplate in F |
| 01-038 | 2001 | 0.72 | 0.95 | Y | Y | Self-cite leaked, Q contaminated with letterhead |
| 98-046 | 1998 | 0.80 | 0.90 | Y | Y | Self-cite leaked, boilerplate in F |
| 96-137 | 1996 | 0.80 | 0.90 | Y | Y | Boilerplate in F, missed prior opinion "1-94-403" |
| 02-305 | 2002 | 0.72 | 0.95 | Y | Y | Self-cite leaked, boilerplate in F |
| 96-109 | 1996 | 0.80 | 0.85 | Y | Y | Self-cite leaked, "FACT S" not matched, Q contaminated |
| 05-163 | 2005 | 0.63 | 0.60 | Y* | - | Declination letter, false Q match, severe OCR |

### 2006-2015 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 07-164 | 2007 | 0.72 | 0.80 | Y | Y | Self-cite leaked, boilerplate in Q, F empty |
| 07-090 | 2007 | 0.72 | 0.90 | Y | Y | Self-cite leaked, date "Idy23,2007" failed |
| 14-116 | 2014 | 0.72 | 0.65 | - | Y | Self-cite leaked, date garbled, Q not found (extreme OCR) |
| 08-002 | 2008 | 0.72 | 0.95 | Y | Y | Self-cite leaked, boilerplate in C |
| 09-178 | 2009 | 0.70 | 0.80 | Y | Y | Self-cite leaked, date "Iuly" failed, boilerplate in C |
| 15-186 | 2015 | 0.80 | 1.00 | Y | Y | Self-cite leaked, boilerplate in F |
| 09-063 | 2009 | 0.72 | 0.95 | Y | Y | Self-cite leaked, 11 prior opinions found (good!), boilerplate in Q |
| 15-240 | 2015 | 0.80 | 1.00 | Y | Y | Self-cite leaked, missed ~3 OCR-garbled prior opinions |
| 10-059 | 2010 | 0.80 | 0.65 | - | Y | Self-cite leaked, "QT.JESTTON" not matched |
| 09-156 | 2009 | 0.80 | 0.60 | - | Y | Self-cite leaked, date "htly" failed, "QTJESTTON" not matched |

### 2016-2025 (10/10 success)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| A-23-140 | 2023 | 0.80 | 1.00 | Y | Y | Boilerplate in C, wrong requestor_title (DA boilerplate) |
| A-25-125 | 2025 | 0.80 | 1.00 | Y | Y | Boilerplate in C, 1090 false positive |
| 16-079-1090 | 2016 | 0.72 | 0.45 | - | - | Self-cite leaked (I-16-079), 1090 correctly captured! |
| A-22-067 | 2022 | 0.80 | 1.00 | Y | Y | Boilerplate in C, wrong requestor_title (DA) |
| 16-140 | 2016 | 0.72 | 0.90 | Y | Y | Self-cite leaked, date failed ("July 18. 2016"), OCR garble |
| A-23-079 | 2023 | 0.80 | 1.00 | Y | Y | ID recovered from text, boilerplate in C |
| A-21-011 | 2021 | 0.80 | 0.95 | Y | Y | ID recovered from text, clean extraction |
| A-21-010 | 2021 | 0.80 | 1.00 | Y | Y | Boilerplate in C |
| A-18-154 | 2018 | 0.80 | 1.00 | Y | Y | Boilerplate in C, 1090 false positive |
| A-23-165 | 2023 | 0.80 | 1.00 | Y | Y | ID recovered, 6 prior opinions found, boilerplate in C, 1090 false positive |

---

## Appendix C: Methodology

### Sample Selection
- 10 documents randomly selected per era from documents with `download_status = 'downloaded'`
- Previous calibration sample (50 docs) excluded to ensure fresh data
- Era boundaries: 1975-1985, 1986-1995, 1996-2005, 2006-2015, 2016-2025

### Review Process
- Each era batch reviewed by a dedicated subagent
- Each subagent read all 10 extracted JSON files in full (including full_text)
- Verified: section content against full_text, citations against text mentions, dates against document headers, classification against subject matter, qa_text for search utility
- Self-citation filter verified by checking document's own ID against prior_opinions list

### Data Files
- Sample IDs: `data/calibration_v2_sample_ids.json`
- Full results: `data/calibration_v2_results.json`
- Previous baseline: `CALIBRATION_REPORT.md`
