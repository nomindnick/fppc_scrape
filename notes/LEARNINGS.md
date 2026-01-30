# FPPC Scraping Learnings

Running log of discoveries from test scripts. Update as we go.

---

## Endpoints

| Endpoint | URL | Notes |
|----------|-----|-------|
| Legacy search | `/advice/advice-opinion-search.html` | Works fine, ~14s response |
| Law advice search | `/the-law/opinions-and-advice-letters/law-advice-search.html` | ~15s response |
| Transparency portal | `/transparency/form-700-filed-by-public-officials/advice-letter-search.html` | ~15s response |
| Monthly reports | `/advice/advice-opinion-search/advice-letter-reports/{year}-advice-letter-reports.html` | 2020-2025 only. Has summaries + tags |

**Test results (01_test_endpoint_speed.py, 2025-01-30):**
- All endpoints ~14-15 seconds (not the 30-60s previously reported)
- All return same content (16,213 total results)
- All have identical pagination (1622 pages)
- **Recommendation**: Use legacy endpoint (simplest URL)

---

## URL Patterns

### Year Filter
- **CONFIRMED**: `tag1=/etc/tags/fppc/year/{YYYY}` with `tagCount=1`
- Example: `?SearchTerm=&tag1=/etc/tags/fppc/year/2024&tagCount=1`

### PDF URLs by Era (CONFIRMED from test results)
| Era | Pattern | Example |
|-----|---------|---------|
| 2016+ | `/advice-letters/{YYYY}/{YYNNN}.pdf` | `/2024/23177.pdf` |
| 1995-2015 | `/advice-letters/1995-2015/{YYYY}/{YY-NNN}.pdf` | `/2015/15-230A.pdf` |
| 1984-1994 | `/advice-letters/1984-1994/{YYYY}/{YYNNN}.pdf` | `/1990/90695.pdf` |
| 1982-1983 | `/advice-letters/{YYYY}/{YYA###}.PDF` | `/1982/82A032.PDF` |

Note: Filenames are inconsistent (some have dashes, some don't, some uppercase .PDF)

---

## HTML Structure (CONFIRMED)

```html
<div class="hit">
    <a href="/content/dam/fppc/documents/advice-letters/...">Title Text</a>
    <div class="hit-tags">Filed under: Tag1, Tag2</div>
</div>
```

- 10 results per page
- Pagination: "Page X of Y" format
- Total shown near top: "X results"

---

## Metadata Quality by Era (CONFIRMED via 03_test_year_filter.py)

| Era | Quality | Title Format | Available Fields |
|-----|---------|--------------|------------------|
| 2020-2024 | **Rich** | "Tony Loresti - I-23-177 - January 11, 2024 - San Jose" | Name, Letter ID, Date, City |
| 1995-2019 | **Sparse** | "Year: 2015 Advice Letter # 15-230A" | Year, Letter # only |
| 1984-1994 | **Partial** | "Rynearson, Mark Year: 1990 Advice Letter # 90695" | Name, Year, Letter # |
| 1976-1983 | **Rich** | "Mr. Francis LaSage - A-82-032 - February 22, 1982 - Escalon" | Name, Letter ID, Date, City |

**Surprise**: 1980s data is BETTER than 2000s-2010s data. The "dark ages" are roughly 1995-2019.

---

## Volume by Year (sample)

| Year | Results | Pages |
|------|---------|-------|
| 2024 | 82 | 9 |
| 2015 | 221 | 23 |
| 2000 | 250 | 25 |
| 1990 | 1,299 | 130 |
| 1982 | 253 | 26 |

Note: 1990 has way more letters than other years. Worth investigating if this is real or data quality issue.

---

## Rate Limiting

- Observed behavior: No issues with 3-second delays between requests
- Safe delay between requests: 3 seconds seems fine
- No blocks or throttling observed yet

---

## PDF Text Extraction (TESTED 2025-01-30)

### Summary by Era

| Era | Native Extraction | Quality | Notes |
|-----|-------------------|---------|-------|
| 2024 | Excellent | Clean text, QUESTION/CONCLUSION sections, date/file# extractable | Digital native |
| 2015 | Excellent | Same as 2024 | Digital native |
| 2000 | Good with issues | Some OCR artifacts in text layer ("Fatn Poltrtcal"), merged words | Scanned + OCR'd |
| 1990 | Partial | 487 words, no date extraction | Older scan |
| 1982 | **FAILED** | Only 4 words - image-based scan | Needs OCR |

### Key Finding: Scanned vs Digital

- **~2010+ letters**: Digital native PDFs with clean text layers
- **Pre-2010 letters**: Mix of OCR'd scans (variable quality) and image-only scans
- **1980s letters**: Often pure image scans with no text layer

### OCR Test Results (Tesseract 5.3.4 on 1982 document)

- Recovered 379 words from document that yielded only 4 via native extraction
- **BUT**: OCR misread "1982" as "1962" (8/6 confusion common)
- Some garbled text but core legal content readable
- Conclusion: OCR is usable as fallback but expect ~5-10% error rate on dates/numbers

### Recommended Extraction Pipeline

1. Try native text extraction (PyMuPDF)
2. Compute quality score (word count, char ratio, etc.)
3. If quality < threshold (e.g., < 100 words for 2+ page doc):
   - Extract page images
   - Run Tesseract OCR
4. Store both extraction method and quality score in metadata

---

## Letter Type Prefixes

| Prefix | Meaning | Confirmed |
|--------|---------|-----------|
| A | Formal Advice | Yes (from PDF content) |
| I | Informal Assistance | Yes (from PDF content) |
| M | Memorandum (?) | Not yet verified |

---

## Next Steps

1. ~~Test PDF text extraction quality across eras~~ ✓ DONE
2. Test pagination (walking through pages)
3. Build document registry schema (SQLite)
4. Scrape monthly reports (2020+) for summaries/tags
5. Estimate how many documents need OCR (sample more years)

---

## Quirks and Gotchas

- Default search returns oldest first (1982), not newest
- Some letters span multiple IDs (e.g., "A-24-001, A-24-002, A-24-003")
- File extensions vary: `.pdf` vs `.PDF`
- 1995-2019 era has minimal metadata - will need PDF parsing
