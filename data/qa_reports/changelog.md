# Pipeline Code Changes Changelog

## Iteration 1

### Extraction Run
- **Date**: 2026-02-06
- **Sample**: 100 documents stratified across 5 eras
- **Results**: 100/100 success, 76% have Q sections, 72% have C sections, 91% dates parsed
- **By era**:
  - 1975-1985: 10 docs, Q=2/10, C=1/10, date=9/10
  - 1986-1995: 15 docs, Q=9/15, C=7/15, date=11/15
  - 1996-2005: 25 docs, Q=21/25, C=20/25, date=23/25
  - 2006-2015: 25 docs, Q=21/25, C=21/25, date=23/25
  - 2016-2025: 25 docs, Q=23/25, C=23/25, date=25/25

### Issues Found (Systemic)

1. **S1: Closing boilerplate in analysis** (~70% of docs with sections)
   - Pattern: "If you have other questions on this matter, please contact me..."
   - Not caught by DOCUMENT_END_PATTERNS → entire closing paragraph included in analysis
   - Affects all eras, most prevalent in modern docs with sections

2. **S2: Page header contamination** (~28% of 2016+ docs)
   - Pattern: "File No. A-XX-XXX / Page No. N" embedded within sections
   - Some OCR variants with "4" for "A", "1" for "I" not caught

3. **S3: Footnote leak into conclusion** (12% of 2016+ docs)
   - Pattern: "word2 Informal assistance does not provide..."
   - Footnote number merges with last word at page boundaries

4. **S4: OCR section header variants** (isolated, 2 docs)
   - "ANALYSN" (I→N garble, doc 09-261)
   - "F'ACTS" (apostrophe insertion, doc 02-237)

5. **S5: MIN_SECTION_WORDS=10 too high** (3+ docs)
   - Valid short conclusions like "No." (1 word) or 9-word conclusions skipped
   - Docs: 03-123, 93-317, others

6. **S6: Withdrawal/decline letters as advice_letter** (3+ docs)
   - Documents about withdrawal of requests classified as "advice_letter"
   - Should be "correspondence"

7. **S7: Roman numeral section headers** (1+ docs)
   - "I. QUESTION", "II. CONCLUSION" not matched by parser

### Code Changes

**File: `scraper/section_parser.py`**

1. **Fix S1**: Added 6 closing boilerplate patterns to `DOCUMENT_END_PATTERNS`:
   - "If you have other/any/further questions"
   - "Should you have questions"
   - "If I can be of further assistance"
   - "Please do not hesitate to contact"
   - "If we/I can be of assistance"
   - "Please feel free to contact"

2. **Fix S5**: Lowered `MIN_SECTION_WORDS` from 10 to 1
   - Section header matching already provides strong gating
   - Allows valid short conclusions like "No."

3. **Fix S4**: Added OCR section header variants to `SECTION_PATTERNS`:
   - `ANALYSN` (I→N garble)
   - `F'ACTS` (apostrophe insertion)

4. **Fix S7**: Added Roman numeral prefixed headers to `SECTION_PATTERNS`:
   - "I. QUESTION", "II. CONCLUSION", "III. FACTS", "IV. ANALYSIS"

5. **Fix S3**: Added footnote boundary patterns to `BOILERPLATE_PATTERNS`:
   - "word2 Informal assistance does not provide..." (merged footnote)
   - Standalone "2 Informal assistance..." footnote
   - FPPC letterhead bleeding into sections (OCR-garbled)

6. **Fix S2**: Enhanced page header boilerplate pattern:
   - Added OCR prefix variants (A→4, I→1) to file number matching
   - Added newline tolerance between File No. and Page No.
   - Added "Re: File No." header variant

**File: `scraper/extractor.py`**

7. **Fix S6**: Enhanced `_determine_document_type()`:
   - Added withdrawal/decline detection patterns checked before prefix-based classification
   - Patterns: "withdraw request", "decline to issue", "withdrawal of request"
   - Returns "correspondence" for these documents
