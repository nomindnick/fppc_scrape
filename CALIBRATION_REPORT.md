# Task 3.8 Calibration Report: Extraction Pipeline Review

**Date:** 2026-02-05
**Sample Size:** 50 documents (10 per era), 41 successful extractions, 9 failures
**Method:** Native extraction only (olmOCR skipped for speed)

---

## Executive Summary

The Phase 3A extraction pipeline was tested against a stratified random sample of 50 documents spanning all five eras (1975-2025). The results reveal a **strong core pipeline** that works very well for modern documents (2016+) but degrades significantly for older eras due to a combination of bugs and OCR challenges.

### Key Metrics by Era

| Era | Success Rate | Avg Quality | Avg Section Conf. | Q Found | C Found | Date Found |
|-----|-------------|-------------|-------------------|---------|---------|------------|
| 1975-1985 | 2/10 (20%) | 0.70 | 0.00 | 0/2 | 0/2 | 0/2* |
| 1986-1995 | 9/10 (90%) | 0.72 | 0.40 | 2/9 | 4/9 | 3/9 |
| 1996-2005 | 10/10 (100%) | 0.76 | 0.75 | 8/10 | 8/10 | 5/10 |
| 2006-2015 | 10/10 (100%) | 0.77 | 0.82 | 6/10 | 9/10 | 5/10 |
| 2016-2025 | 10/10 (100%) | 0.80 | 1.00 | 10/10 | 10/10 | 9/10 |

*Both dates found in 1975-1985 were incorrect (wrong date in document selected).

### Critical Finding

**~4,000 documents (29% of corpus) are completely blocked from extraction** due to a case-sensitive `.PDF` extension check. This is a one-line fix with massive impact.

### Pipeline Accuracy Assessment

Even among successfully extracted documents, systematic issues reduce quality:
- **Self-citation contamination**: 100% of documents include their own ID in `prior_opinions`
- **Section boundary bleeding**: ~80% of documents have footnote/boilerplate contamination in sections
- **Prior opinion under-extraction**: ~50-70% of cited advice letters are missed
- **Date parsing failures**: ~40% of pre-2016 documents have missing or incorrect dates
- **Classification accuracy**: Only ~40% of documents get a meaningful topic (vs. "other" or null)

---

## Issues Ranked by Priority

### Tier 1: CRITICAL — Must Fix Before Full Extraction Run

These bugs block extraction entirely or corrupt data at scale.

#### C1. Case-Sensitive `.PDF` Extension Check
- **Scope:** ~4,086 documents (1975-1989), 29% of corpus
- **File:** `scraper/extractor.py`, `_get_pdf_path()` method
- **Problem:** `filename.endswith(".pdf")` is case-sensitive. URLs ending in `.PDF` (uppercase, common pre-1986) produce lookup paths like `85210.PDF.pdf` which don't exist.
- **Fix:** Change to `filename.lower().endswith(".pdf")`
- **Effort:** 1 line
- **Impact:** Unblocks nearly a third of the entire corpus

#### C2. Null Document ID → `None.json` Collisions
- **Scope:** Multiple documents per year with missing `letter_id` in DB
- **File:** `scraper/extractor.py`, `save_document()` and ID generation
- **Problem:** When `letter_id` is `NULL` in the database, the JSON file is saved as `None.json`. Multiple null-ID documents in the same year overwrite each other. Also causes `TypeError: unsupported format string passed to NoneType.__format__`.
- **Fix:** (a) Parse "Our File No." from the document text as a fallback. (b) If still null, generate a deterministic ID from the PDF filename or DB row ID (e.g., `UNK-2023-{db_id}`). (c) Never allow `None` as a filename.
- **Effort:** ~20 lines
- **Impact:** Prevents data loss and crashes

#### C3. Self-Citation Contamination in `prior_opinions`
- **Scope:** 100% of documents (41/41 successful extractions)
- **File:** `scraper/citation_extractor.py` or post-processing in `extractor.py`
- **Problem:** The document's own file number (from "Our File No." lines and page headers like "File No. A-22-078") matches the prior opinion regex and gets added to the `prior_opinions` list. This means every document "cites itself," corrupting the citation graph and inflating citation counts.
- **Fix:** After extracting the document's own ID, filter it (and format variants) out of `prior_opinions`.
- **Effort:** ~5 lines
- **Impact:** Citation graph integrity — essential for the Phase 3.11 citation graph feature

---

### Tier 2: HIGH — Significantly Degrades Search/RAG Quality

These don't block extraction but seriously harm downstream usability.

#### H1. Footnote Boilerplate Bleeding into Sections
- **Scope:** ~80% of all documents (estimated 11,000+)
- **File:** `scraper/section_parser.py` or post-processing in `extractor.py`
- **Problem:** The standard FPPC footnote 1 ("The Political Reform Act is contained in Government Code Sections 81000 through 91014...") appears at the bottom of page 1, physically between section headers. The section parser captures it as part of the preceding section (usually QUESTION or CONCLUSION). This boilerplate then propagates into `embedding.qa_text`, adding identical noise to thousands of documents and severely degrading semantic search quality.
- **Fix:** Post-processing step to strip:
  - Content matching the footnote pattern (starts with superscript or "1" followed by "The Political Reform Act...")
  - Page headers matching `File No. [AI]?-?\d{2}-\d{3,4}\s*/?\s*Page\s*No\.\s*\d+`
  - "Government Code Sections 81000" boilerplate paragraphs
- **Effort:** ~30-50 lines of regex post-processing
- **Impact:** Directly improves `qa_text` quality for every document — this is the #1 search quality improvement

#### H2. OCR-Garbled Section Headers Not Matched
- **Scope:** ~40% of 1986-2010 documents (~3,000 documents)
- **File:** `scraper/section_parser.py`
- **Problem:** Common OCR corruptions prevent section header matching:
  - `Q` → `O`: "OUESTION" instead of "QUESTION" (most common, affects 4+ eras)
  - Space insertion: "CONCLUS TON", "ANALYSI S", "QUEST ION"
  - Character substitution: "ANALYSTS" for "ANALYSIS", "QUESTTON" for "QUESTION", "rACTS" for "FACTS"
  - "AI\\ALYSIS", "QrrEsrroNs", "QI.IESTION"
- **Fix:** Add OCR-tolerant patterns to `SECTION_PATTERNS`:
  ```
  r'(?:^|\n)\s{0,4}[OQ]U?EST(?:ION|TON|TION)S?\s*(?:\n|:)'
  r'(?:^|\n)\s{0,4}CONCLU\s*S?\s*(?:ION|TON)S?\s*(?:\n|:)'
  r'(?:^|\n)\s{0,4}[rF]ACTS?\s*(?:\n|:)'
  r'(?:^|\n)\s{0,4}ANA?L\s*YS[IT1]\s*S\s*(?:\n|:)'
  ```
- **Effort:** ~20 lines of additional patterns
- **Impact:** Recovers section extraction for thousands of 1986-2010 documents

#### H3. Date Parsing Fails on "Month DD,YYYY" Format
- **Scope:** ~50% of 1996-2015 documents (~2,500 documents)
- **File:** `scraper/extractor.py`, date parsing logic
- **Problem:** Many PDFs render dates with no space after the comma: "June 27,2002", "December 11,2001", "May26, 1998". The date regex requires a space and fails. OCR can also mangle digits ("lO" for "10", "L996" for "1996").
- **Fix:** Make date regex more permissive: `r'(Month)\s*(\d{1,2}),?\s*(\d{4})'` — allow optional/missing spaces around comma and between month and day.
- **Effort:** ~10 lines
- **Impact:** Recovers dates for ~2,500 documents

#### H4. Government Code Section 1090 Citations Not Captured
- **Scope:** ~20-30% of modern documents, growing trend
- **File:** `scraper/citation_extractor.py`
- **Problem:** The citation extractor only captures Government Code sections in the 81000-92000 range (the Political Reform Act). Sections 1090-1097.1 (conflict of interest in public contracts) are extensively cited in FPPC advice letters but fall outside this range. Document A-23-099, for example, has `government_code: []` despite citing Sections 1090, 1091, and 1091.5 extensively throughout. This also causes classification failures (Bug M2).
- **Fix:** Add a second extraction pass for Section 1090-1097 references, or expand the valid range to include them as a separate category (e.g., `section_1090_citations`).
- **Effort:** ~15 lines
- **Impact:** Captures a major category of legal citations; fixes downstream classification

#### H5. Prior Opinion Citation Under-Extraction
- **Scope:** ~50-70% of cited opinions missed across all eras
- **File:** `scraper/citation_extractor.py`
- **Problem:** The prior opinion regex misses many common citation formats:
  - "Name Advice Letter, No. A-YY-NNN" / "Name Advice Letter, No. 1-YY-NNN"
  - The "4-YY-NNN" prefix format (used pre-2005)
  - "Advice Letter No. YYYYY" (5-digit format without dashes)
  - Citations in footnotes
  - OCR-damaged references

  In the 2006-2015 sample, one document cited 12 prior opinions but only 5 were captured. In the 1996-2005 sample, approximately 15-20 cited opinions were missed across 10 documents.
- **Fix:** Expand the regex to cover:
  ```
  r'(?:Advice Letter|Memorandum),?\s*No\.?\s*([AI4]?-?\d{2}-?\d{3,4})'
  r'\b([AI]-\d{2}-\d{3})\b'  (existing, keep)
  r'\b(4-\d{2}-\d{3})\b'     (add: older format)
  r'\b(1-\d{2}-\d{3})\b'     (add: informal format)
  ```
- **Effort:** ~15-20 lines
- **Impact:** Citation graph completeness — currently capturing <50% of cross-references

---

### Tier 3: MEDIUM — Improves Quality but Not Blocking

#### M1. Date Parser Selects Wrong Date (Body Date vs. Letter Date)
- **Scope:** ~10-15% of all documents, especially pre-1995
- **Problem:** The parser grabs the first date found in the text, which may be an event date ("November 25, 1991, Thanksgiving"), a reporting period date ("September 30, 1992, end of reporting period"), or an attached document's date rather than the FPPC response date.
- **Fix:** (a) Prioritize dates in the first ~500 characters. (b) Look for dates adjacent to salutation ("Dear...") or letterhead patterns. (c) Use DB metadata `letter_date` as primary when available.
- **Effort:** ~20 lines

#### M2. Classification Defaults to "other" Too Frequently
- **Scope:** ~40-60% of documents across all eras
- **Problem:** The heuristic:citation_based classifier relies solely on counting Government Code sections by range. Documents about gifts (§82028), disclosure (§82048), honoraria (§89501), and Section 1090 contracting conflicts all get classified as "other" because their sections fall outside the narrow conflict/campaign/lobbying ranges.
- **Fix:** (a) Expand section ranges in the classifier. (b) Add keyword-based signals from the document text ("gift", "contribution", "campaign", "conflict of interest", "disclosure", "Form 700", "Section 1090"). (c) Consider a "gifts_and_honoraria" topic category.
- **Effort:** ~40 lines

#### M3. `requestor_title` Misparsed from Boilerplate
- **Scope:** All Section 1090 documents (~20% of modern corpus)
- **Problem:** The title parser matches "District Attorney" from the Section 1090 forwarding boilerplate paragraph rather than the actual requestor's title. Three documents in the 2016-2025 sample had this issue.
- **Fix:** Restrict title parsing to the header block (before "Dear...") and exclude the Section 1090 boilerplate.
- **Effort:** ~10 lines

#### M4. Document IDs Missing "A-" Prefix
- **Scope:** ~500 documents in the 2016-2019 transition era
- **Problem:** The DB `letter_id` for some 2016-2019 documents lacks the "A-" prefix (e.g., "17-140" instead of "A-17-140"). The PDF text contains the correct "Our File No. A-17-140" but this isn't used as a fallback.
- **Fix:** Parse "Our File No." from text when the DB letter_id appears incomplete. Normalize all IDs to include the type prefix.
- **Effort:** ~15 lines

#### M5. `requestor_name` Parsing Errors
- **Scope:** ~10-20% of pre-2000 documents
- **Problem:** OCR-split names ("Gi llis" → "Gi"), internal capitals ("McHugh" → "Mc"), newline bleeding ("Martello\nThis"), and wrong entity extraction ("Big" from "Big Green Campaign", "Davidian" from FPPC chairman instead of requestor).
- **Fix:** Improve name regex to handle compound names, stop at sentence boundaries, and prefer sender over addressee for incoming letters.
- **Effort:** ~20 lines

#### M6. Combined Section Headers Not Recognized
- **Scope:** ~5% of documents (estimated ~700)
- **Problem:** Some FPPC letters use "CONCLUSIONS AND ANALYSIS" as a single combined header rather than separate CONCLUSION and ANALYSIS headers. The parser fails to match this variant, causing the combined content to get dumped into the preceding FACTS section.
- **Fix:** Add patterns for "CONCLUSIONS? AND ANALYSIS" and split the matched content intelligently.
- **Effort:** ~15 lines

#### M7. Document-End Pattern Fails on OCR Variants
- **Scope:** ~20% of pre-2005 OCR documents
- **Problem:** The "Sincerely," pattern used to detect document end fails when OCR introduces spaces ("S incerely,") or substitutions ("S j.ncerely,").
- **Fix:** Make the pattern more permissive: `r'\n\s*S\s?i\s?n\s?c\s?e\s?r\s?e\s?l\s?y'`
- **Effort:** ~5 lines

---

### Tier 4: LOW — Nice to Have

#### L1. Incoming Letters vs. Response Letters Not Distinguished
- **Scope:** ~3-5% of corpus (incoming request letters filed alongside FPPC responses)
- **Problem:** Some entries in the database point to the requestor's incoming letter rather than the FPPC's response. These have inverted metadata (requestor name = FPPC official).
- **Fix:** Detect incoming letters via patterns ("Dear Chairman", absence of "Our File No.") and flag with `document_type: "incoming_request"`.

#### L2. Withdrawal/Non-Substantive Letters Not Flagged
- **Scope:** ~2% of corpus
- **Problem:** Withdrawal letters, referral letters, and other non-substantive correspondence are processed as advice letters. Their `qa_text` provides no useful content for search.
- **Fix:** Detect and flag with `document_type: "withdrawal"` or `"referral"`.

#### L3. `qa_text` Fallback Quality for No-Section Documents
- **Scope:** All documents without extracted Q/C sections (~30-40% of corpus)
- **Problem:** When no sections are found, `qa_text` falls back to the full text or is empty. Full-text fallback includes OCR-garbled letterheads.
- **Fix:** Better fallback: extract text between "Dear..." and "Sincerely," as the body, use first 500 words of body only.

#### L4. Government Code Range Citations Partially Missed
- **Scope:** ~10% of citations across all eras
- **Problem:** Some government code sections discussed in ranges ("Sections 89510-89522") or with OCR damage ("g2015" for "82015") are not captured.
- **Fix:** Handle range citations and common OCR substitutions.

#### L5. Regulation Under-Extraction
- **Scope:** ~20% of regulation citations missed
- **Problem:** Some regulation citations in the 18000 range are missed due to format variations (subsections, ranges, OCR damage).
- **Fix:** Expand regulation regex patterns.

---

## Impact Analysis: What Matters Most for Your Use Case

### For Search/RAG (Your Primary Goal)

The highest-impact fixes for search quality are, in order:

1. **C1 (`.PDF` extension)** — Unblocks 4,000 documents. No content = no search results.
2. **H1 (Boilerplate stripping)** — Directly cleans `qa_text`, the field used for embeddings. Without this fix, every document's embedding will contain identical footnote boilerplate, causing all documents to look similar to each other and degrading retrieval precision.
3. **H2 (OCR section headers)** — Recovers QUESTION/CONCLUSION for ~3,000 documents. These sections form the `qa_text`, so missing them means those documents have poor or empty embedding content.
4. **C2 (Null ID)** — Prevents data loss. Documents that overwrite each other are permanently lost from search results.
5. **C3 (Self-citation)** — If you plan any "related documents" or "cited by" features, this is essential.
6. **H3 (Date parsing)** — Enables date-range filtering in search, a key facet for legal research.

### For Citation Graph (Phase 3.11)

1. **C3 (Self-citation)** — Every edge in the graph is currently wrong by +1.
2. **H5 (Prior opinion under-extraction)** — The graph is missing 50-70% of its edges.
3. **H4 (Section 1090)** — Missing a major category of legal references.

### For Metadata Quality

1. **H3 + M1 (Date fixes)** — ~40% of dates are wrong or missing.
2. **M2 (Classification)** — ~40-60% classified as uninformative "other".
3. **M4 + C2 (ID fixes)** — Ensures every document has a correct, unique identifier.

---

## Recommended Fix Order

For maximum impact with minimum effort, implement fixes in this order:

| Order | Bug | Effort | Documents Fixed |
|-------|-----|--------|----------------|
| 1 | C1: `.PDF` extension | 1 line | ~4,086 |
| 2 | C2: Null ID fallback | ~20 lines | ~200+ |
| 3 | C3: Self-citation filter | ~5 lines | ~14,096 |
| 4 | H1: Boilerplate stripping | ~40 lines | ~11,000+ |
| 5 | H2: OCR section headers | ~20 lines | ~3,000 |
| 6 | H3: Date parsing | ~10 lines | ~2,500 |
| 7 | H4: Section 1090 citations | ~15 lines | ~3,000 |
| 8 | H5: Prior opinion regex | ~15 lines | ~10,000+ |
| 9 | M2: Classification expansion | ~40 lines | ~8,000 |
| 10 | M1: Date selection logic | ~20 lines | ~1,500 |

**Total effort for top 8 fixes: ~125 lines of code changes**
**Impact: Fixes systematic issues across the entire 14,096-document corpus**

---

## Appendix A: Sample Documents by Era

### 1975-1985 (2 success / 8 failure)
| ID | Year | Quality | Sections | Notes |
|----|------|---------|----------|-------|
| 76552 | 1976 | 0.80 | None found | OCR artifacts, wrong date, missed citations |
| 76381 | 1976 | 0.60 | None found | Multi-document bundle, wrong date |
| 85210 | 1985 | BLOCKED | N/A | `.PDF` extension bug — PDF has excellent native text |
| 78ADV-78-345 | 1978 | BLOCKED | N/A | `.PDF` extension bug |
| 76ADV-236 | 1976 | BLOCKED | N/A | `.PDF` extension bug |
| 76191 | 1976 | BLOCKED | N/A | `.PDF` extension bug — good native text |
| 83A222 | 1983 | BLOCKED | N/A | `.PDF` extension bug — good native text |
| 82A032 | 1982 | BLOCKED | N/A | `.PDF` extension bug — image-only, needs OCR |
| 83A114 | 1983 | BLOCKED | N/A | `.PDF` extension bug — good native text |
| 77ADV-77-369 | 1977 | BLOCKED | N/A | `.PDF` extension bug — image-only, needs OCR |

### 1986-1995 (9 success / 1 failure)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| None (91553) | 1991 | 0.72 | 0.00 | - | - | Null ID, wrong date, incoming letter |
| 95407 | 1995 | 0.65 | 0.35 | - | - | Severe OCR, Q→O, self-citation |
| 95-044 | 1995 | 0.80 | 0.90 | Y | Y | Footnote in QUESTION, classification wrong |
| 90479 | 1990 | 0.63 | 0.00 | - | - | Incoming letter, correct no-sections |
| 90-443 | 1990 | 0.72 | 0.35 | - | - | Q→O, boundary overflow, self-citation |
| 94-032 | 1994 | 0.80 | 0.55 | - | Y | Q→O ("OUESTIONS"), thin qa_text |
| 94-400 | 1994 | 0.80 | 0.90 | Y | Y | Conclusion includes boilerplate |
| 90-552 | 1990 | 0.80 | 0.55 | - | Y | Conclusion absorbs FACTS, self-citation |
| 92604 | 1992 | 0.60 | 0.00 | - | - | Wrong date, heavy OCR, incoming letter |
| 89-247 | 1989 | FAIL | N/A | - | - | process_document returned None |

### 1996-2005 (10 success / 0 failure)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 96-250 | 1996 | 0.80 | 0.90 | Y | Y | FACTS includes boilerplate, missed citations |
| 96-328 | 1996 | 0.80 | 0.90 | Y | Y | Self-citation, conclusion bleeds |
| 98-118 | 1998 | 0.80 | 0.90 | Y | Y | "May26" date fails, self-citation, missed opinions |
| 98-147 | 1998 | 0.80 | 0.90 | Y | Y | Conclusion bleeds, self-citation |
| 99-112 | 1999 | 0.80 | 0.55 | Y | - | No CONCLUSION, QUESTION includes footnotes |
| 00-133 | 2000 | 0.80 | 0.90 | Y | Y | "AI\\ALYSIS" garbled → ANALYSIS lost |
| 01-268 | 2001 | 0.72 | 0.95 | Y | Y | Date fails, name = "Martello\nThis" |
| 02-163 | 2002 | 0.72 | 0.90 | Y | Y | "ANALYSTS" garbled → ANALYSIS lost |
| 03-161 | 2003 | 0.64 | 0.00 | - | - | Withdrawal letter, no substantive content |
| 05-205 | 2005 | 0.72 | 0.60 | - | Y | "QUESTTON" garbled, self-citation |

### 2006-2015 (10 success / 0 failure)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| 09-014 | 2009 | 0.80 | 0.60 | - | Y | "QUESTTON" garbled, 5 missed opinions |
| 09-238 | 2009 | 0.72 | 0.60 | - | Y | "QrrEsrroNs" garbled, 6 missed opinions |
| 10-084 | 2010 | 0.80 | 0.65 | - | Y | "QUESTTON" + "AIIALYSIS" garbled, wrong date |
| 10-129 | 2010 | 0.70 | 1.00 | Y | Y | Conclusion heavily polluted with boilerplate |
| 10-166 | 2010 | 0.72 | 1.00 | Y | Y | Question includes all footnotes, date fails |
| 11-022 | 2011 | 0.80 | 1.00 | Y | Y | Question includes 3 footnotes |
| 11-245 | 2011 | 0.72 | 0.65 | - | Y | "QI.IESTION" garbled, conclusion polluted |
| 12-016 | 2012 | 0.80 | 1.00 | Y | Y | Conclusion includes boilerplate |
| 12-039 | 2012 | 0.80 | 1.00 | Y | Y | Conclusion includes boilerplate |
| 14-052 | 2014 | 0.80 | 0.65 | Y | - | "CONCLUSIONS AND ANALYSIS" combined header |

### 2016-2025 (10 success / 0 failure)
| ID | Year | Quality | Conf | Q | C | Key Issues |
|----|------|---------|------|---|---|-----------|
| A-22-078 | 2022 | 0.80 | 1.00 | Y | Y | Conclusion includes boilerplate |
| A-19-179 | 2020 | 0.80 | 1.00 | Y | Y | Question includes boilerplate, classified "other" |
| A-25-107 | 2025 | 0.80 | 1.00 | Y | Y | Question includes boilerplate, wrong requestor_title |
| 17-140 | 2017 | 0.80 | 1.00 | Y | Y | Question includes boilerplate, ID missing "A-" |
| None | 2023 | 0.80 | 1.00 | Y | Y | **NULL ID → None.json**, wrong requestor_title |
| 17-135 | 2017 | 0.80 | 1.00 | Y | Y | FACTS includes boilerplate, ID missing "A-" |
| A-24-047 | 2023 | 0.80 | 1.00 | Y | Y | Question includes boilerplate, year mismatch |
| A-23-099 | 2023 | 0.80 | 1.00 | Y | Y | **Zero govt code citations** (Section 1090 missed) |
| A-23-012 | 2023 | 0.80 | 1.00 | Y | Y | Conclusion includes boilerplate |
| A-25-075 | 2025 | 0.80 | 1.00 | Y | Y | Question includes boilerplate, missed footnote citation |

---

## Appendix B: Bug Cross-Reference Matrix

Which bugs affect which eras:

| Bug | 1975-85 | 1986-95 | 1996-05 | 2006-15 | 2016-25 |
|-----|---------|---------|---------|---------|---------|
| C1: `.PDF` extension | **YES** | partial | - | - | - |
| C2: Null ID | - | YES | - | - | YES |
| C3: Self-citation | YES | YES | YES | YES | YES |
| H1: Boilerplate bleed | - | YES | YES | YES | YES |
| H2: OCR section headers | - | YES | YES | YES | - |
| H3: Date "DD,YYYY" | - | - | YES | YES | - |
| H4: Section 1090 | - | - | - | partial | YES |
| H5: Prior opinion regex | YES | YES | YES | YES | YES |
| M1: Wrong date selected | YES | YES | partial | partial | - |
| M2: Classification "other" | YES | YES | YES | YES | YES |

---

## Appendix C: Methodology

### Sample Selection
- 10 documents randomly selected per era from documents with `download_status = 'downloaded'` and `extraction_status = 'pending'`
- Era boundaries: 1975-1985, 1986-1995, 1996-2005, 2006-2015, 2016-2025
- Total: 50 documents, 41 successful extractions

### Review Process
- Each extracted JSON file was read in full
- Section content was compared against the source PDF's full_text
- Citations were manually verified against text references
- Dates were cross-checked against document content
- Classification was evaluated against document subject matter
- Embedding `qa_text` was assessed for search/RAG utility

### Limitations
- olmOCR was skipped for speed; pre-1990 image-only PDFs were not tested with OCR
- Sample size of 10 per era is small; some bugs may be over- or under-represented
- Only native extraction was tested; olmOCR fallback quality not assessed
