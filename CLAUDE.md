# FPPC Advice Letter Scraper

## Project Overview

This project scrapes advice letters from the California Fair Political Practices Commission (FPPC) website and processes them into a searchable format. The FPPC has ~16,000 advice letters spanning 1975-2025 that provide guidance on political ethics, conflicts of interest, and campaign finance.

## Goals

1. Build a complete registry of all FPPC advice letters
2. Download and extract text from all PDFs
3. Store in a format suitable for:
   - Semantic search / RAG systems
   - A searchable web frontend
   - Potential ML training

## Project Structure

```
fppc_scrape/
├── CLAUDE.md              # This file
├── PLAN.md                # Detailed implementation plan
├── background_docs/       # Initial recon and research
├── test_scripts/          # Experiments and validation scripts
├── notes/
│   └── LEARNINGS.md       # Running log of discoveries
├── scraper/               # Production scraper code (to be built)
├── data/                  # SQLite DB, extracted JSON/text
└── raw_pdfs/              # Downloaded PDFs (gitignored)
```

## Key Technical Details

### FPPC Search Endpoint
- URL: `https://fppc.ca.gov/advice/advice-opinion-search.html`
- Year filter: `?SearchTerm=&tag1=/etc/tags/fppc/year/{YYYY}&tagCount=1`
- Pagination: `&page={N}` (1-indexed, 10 results per page)
- Response time: ~14 seconds per page

### HTML Structure
```html
<div class="hit">
    <a href="/content/dam/fppc/documents/advice-letters/...">Title</a>
    <div class="hit-tags">Filed under: Tag1, Tag2</div>
</div>
```

### PDF URL Patterns by Era
| Era | Pattern |
|-----|---------|
| 2016+ | `/advice-letters/{YYYY}/{YYNNN}.pdf` |
| 1995-2015 | `/advice-letters/1995-2015/{YYYY}/{YY-NNN}.pdf` |
| 1984-1994 | `/advice-letters/1984-1994/{YYYY}/{YYNNN}.pdf` |
| 1976-1983 | `/advice-letters/{YYYY}/{YYA###}.PDF` |

### Metadata Quality by Era
- **2020-2024**: Rich (name, letter ID, date, city)
- **1995-2019**: Sparse (year and letter number only)
- **1984-1994**: Partial (name, year, letter number)
- **1976-1983**: Rich (full details)

### PDF Extraction
- Modern PDFs (2010+): Clean native text extraction
- Older PDFs: May need OCR (Tesseract)
- Quality varies; some 1980s docs are image-only scans

## Current Status

**Completed:**
- Endpoint testing (all endpoints equivalent, ~14s response)
- Year filter URL pattern confirmed
- HTML parsing structure confirmed
- PDF extraction tested across eras
- OCR fallback tested (works but has ~5-10% error rate)

**Next:**
- Build SQLite document registry
- Implement year-by-year crawler
- Test on small year before full run

## Commands

```bash
# Activate virtual environment
source .venv/bin/activate

# Run test scripts
python test_scripts/01_test_endpoint_speed.py

# Dependencies
pip install pymupdf requests
# Tesseract OCR is system-installed
```

## Important Notes

- Be polite to the FPPC server: use 3+ second delays between requests
- The site is slow (~14s per page) - full crawl takes hours
- ~16,000 documents total, ~5GB of PDFs
- Some older PDFs need OCR; store extraction method in metadata
