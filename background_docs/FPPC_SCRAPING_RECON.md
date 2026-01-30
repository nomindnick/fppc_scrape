# FPPC Advice Letter Scraping: Reconnaissance & Approach

**Document Purpose:** Capture findings from initial exploration of the FPPC website to inform scraper development.

**Last Updated:** January 28, 2025

---

## 1. Data Sources Overview

The FPPC has **16,207 advice letters** spanning 1975-2025. There are two ways to access them:

### Source A: Monthly Report Pages (2020-2025)
- **URL Pattern:** `https://www.fppc.ca.gov/advice/advice-opinion-search/advice-letter-reports/{year}-advice-letter-reports.html`
- **Coverage:** ~5 years (2020-2025 only)
- **Advantages:**
  - Pre-written summaries for each letter
  - Category tags (Conflict of Interest, Campaign, Revolving Door, Section 84308)
  - Direct PDF links
  - Clean, structured HTML
- **Disadvantages:**
  - Only recent letters
  - Estimated ~500-600 letters total

### Source B: Main Search Interface (1975-2025)
- **URL:** `https://fppc.ca.gov/advice/advice-opinion-search.html`
- **Coverage:** All 16,207 letters
- **Advantages:**
  - Complete archive back to 1975
  - Year filtering available
- **Disadvantages:**
  - No summaries (just links to PDFs)
  - Inconsistent metadata quality (see Section 3)
  - Very slow (30-60 seconds per page load)
  - 10 results per page, no option to increase

---

## 2. Search Interface URL Patterns

### Base Search (no filters)
```
https://fppc.ca.gov/advice/advice-opinion-search.html?SearchTerm=&tag1=na&tagCount=1
```
Returns all 16,207 results.

### Pagination
```
https://fppc.ca.gov/advice/advice-opinion-search.html?page={N}&SearchTerm=&tagCount=1
```
- Page numbers are 1-indexed
- 10 results per page
- Total pages shown at bottom: "Page X of Y"

### Year Filter
Selecting a year from the dropdown changes results. Need to capture exact URL pattern when year is selected.

**TODO:** Document the exact URL parameter for year filtering.

---

## 3. Metadata Quality by Era

Metadata extracted from search results varies dramatically by time period:

### Recent Era (2020-2024): Rich
```
Tony Loresti - I-23-177 - January 11, 2024 - San Jose
Filed under: Advice Letter, 2024
```
**Available:** Requestor name, Letter ID, Full date, City, Year tag

### Middle Era (2010s): Sparse
```
Year: 2011 Advice Letter # 11-184
Filed under: 2011, Advice Letter
```
**Available:** Year, Letter number only

### Old Era (1980s and earlier): Inconsistent
Some have full metadata:
```
L.B. Elam - A-82-054 - May 10, 1982 - Sacramento
Filed under: 1982, Advice Letter
```

Others are just raw filenames:
```
82A142.PDF
Filed under: No tags assigned
```

**Implication:** Cannot rely on search result metadata for older letters. Must extract information from PDF content itself.

---

## 4. Letter ID Formats Observed

| Format | Example | Meaning (suspected) |
|--------|---------|---------------------|
| `A-YY-NNN` | A-82-054 | Advice letter |
| `I-YY-NNN` | I-23-177 | Informal advice (?) |
| `M-YY-NNN` | M-82-047 | Memorandum (?) |
| `YY-NNN` | 11-184 | Simplified format |
| Raw filename | 82A142.PDF | No structured ID |

**Note:** The prefix letters (A, I, M) may indicate different letter types. Worth investigating.

---

## 5. File Types

- **Majority:** PDF files
- **Occasional:** Word documents (.doc/.docx) have been observed
- **Assumption:** All PDFs should be text-searchable (ADA compliance requirement for state agencies)
- **Risk:** Some older scanned documents may have poor OCR or no text layer

---

## 6. Technical Constraints

| Factor | Value | Impact |
|--------|-------|--------|
| Page load time | 30-60 seconds | Scraping will be slow |
| Results per page | 10 (fixed) | Many pages to iterate |
| Total results | 16,207 | ~1,621 pages if no filtering |
| Server-side rendering | Yes | No hidden API; simple HTML parsing |
| Rate limiting | Unknown | Should add delays between requests |

### Time Estimates (full scrape, no year filter)
- At 30 sec/page: ~13.5 hours
- At 45 sec/page: ~20 hours
- Plus PDF download time

### Time Estimates (by year, ~50 years)
- Average ~324 letters/year → ~33 pages/year
- Per year at 30 sec/page: ~16 minutes
- Total: ~13 hours (similar, but resumable)

---

## 7. Recommended Approach

### Phase 1: Recent Letters (2020-2025) via Monthly Reports
- Use structured monthly report pages
- Get summaries + category tags for free
- Estimated ~500-600 letters
- Fast and clean

### Phase 2: Historical Letters (1975-2019) via Search Interface
- Scrape year-by-year for resumability
- Extract whatever metadata is available from search results
- Download all PDFs
- Extract full text from PDFs

### Phase 3: Enrich Historical Data
- Parse PDF text to extract:
  - Requestor name (usually in header/salutation)
  - Date issued
  - Government Code sections cited
- Use keyword heuristics or LLM to classify:
  - Conflict of Interest vs Campaign Finance
  - Sub-categories within conflicts

### Phase 4: Build Search
- Chunk documents (paragraph level for granular matching)
- Generate embeddings (sentence-transformers)
- Hybrid search: semantic + keyword
- Display results with context previews

---

## 8. Open Questions

1. **Year filter URL:** What's the exact URL parameter when a year is selected?

2. **PDF URL patterns:** Are PDF URLs predictable from letter IDs, or must we scrape them from search results?

3. **Word documents:** How common are they? Same URL pattern as PDFs?

4. **Rate limiting:** Does FPPC block or throttle after many requests?

5. **Letter type prefixes:** What do A, I, M designations mean? Does this map to conflict vs campaign?

6. **Commission Opinions:** These are separate from Advice Letters and appear to have their own search. Include them?

---

## 9. Sample Results Page HTML Structure

**TODO:** Save raw HTML from a results page to analyze exact element structure for parsing.

Key elements to identify:
- Container for each result
- Link element (href to PDF)
- Text content (requestor, ID, date, city)
- "Filed under" tags
- Pagination controls

---

## 10. Files Created So Far

| File | Purpose | Status |
|------|---------|--------|
| `fppc_scraper.py` | Monthly reports scraper (2020-2025) | Draft - needs testing |
| `fppc_search.py` | Semantic search interface | Draft - needs data |
| `README.md` | Usage documentation | Draft |

**Next:** Revise scraper to handle year-by-year search interface scraping for 1975-2019.

---

## Appendix: Screenshots Reference

Screenshots captured during recon (January 28, 2025):
1. Network tab showing server-side rendering (no API)
2. Search results for 1982 - mixed metadata quality
3. Search results for 2011 - sparse format
4. Search results for 2024 - rich format

These confirm the metadata quality degradation for older letters.
