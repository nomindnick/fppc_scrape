# FPPC Scraping Plan

## Goal

Scrape all ~16,000 FPPC advice letters (1975-2025), extract text, and store in a searchable format for downstream use (RAG, search UI, ML training).

---

## Phase 1: Build Document Registry ✓ COMPLETE

**Objective:** Crawl all search result pages and build a SQLite database of document metadata before downloading any PDFs.

**Status:** Complete - 14,132 documents indexed across 51 years (1975-2025)

### Why Registry First?
- Resumability: can stop/restart without losing progress
- Deduplication: catch duplicates before downloading
- Planning: know exactly how many PDFs, which need OCR, etc.
- Separation of concerns: crawling ≠ downloading ≠ extracting

### Schema Design

```sql
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,

    -- From search results
    pdf_url TEXT UNIQUE NOT NULL,
    title_text TEXT,              -- Raw title from search result
    year_tag INTEGER,             -- Year from "Filed under" or URL
    tags TEXT,                    -- Other tags, comma-separated
    source_page_url TEXT,         -- Which search page we found it on

    -- Parsed from title (when available)
    requestor_name TEXT,
    letter_id TEXT,               -- e.g., "A-24-006", "I-23-177"
    letter_date TEXT,             -- e.g., "January 23, 2024"
    city TEXT,

    -- Download status
    download_status TEXT DEFAULT 'pending',  -- pending, downloaded, failed
    downloaded_at TEXT,
    pdf_size_bytes INTEGER,
    pdf_sha256 TEXT,

    -- Extraction status
    extraction_status TEXT DEFAULT 'pending',  -- pending, native, ocr, failed
    extraction_method TEXT,       -- 'native' or 'tesseract'
    extraction_quality REAL,      -- 0-1 score
    page_count INTEGER,
    word_count INTEGER,

    -- Timestamps
    scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);

CREATE INDEX idx_year ON documents(year_tag);
CREATE INDEX idx_download_status ON documents(download_status);
CREATE INDEX idx_extraction_status ON documents(extraction_status);
```

### Crawl Strategy

**Option A: Year-by-year (RECOMMENDED)**
- Filter by year, paginate through each year
- ~50 years × ~20-130 pages/year
- Pro: Natural checkpoints, easy to resume
- Pro: Can prioritize years (e.g., recent first)

**Option B: All results, no filter**
- 1,622 pages at 10 results/page
- Pro: Simpler code
- Con: No natural resume points, oldest first

**Decision:** Use Option A (year-by-year)

### Tasks

- [x] Create SQLite database with schema
- [x] Write crawler that:
  - [x] Iterates years (1975-2025)
  - [x] For each year, paginates through all results
  - [x] Parses each result (title, PDF URL, tags)
  - [x] Inserts into database (skip if URL exists)
  - [x] Logs progress
- [x] Add rate limiting (4 second delays)
- [x] Add checkpoint/resume logic (track last completed year+page)
- [x] Test on 2-3 years before full run

### Implementation

Created `scraper/` module with:
- `config.py` - Configuration constants (URLs, delays, paths)
- `db.py` - Database setup and operations
- `parser.py` - HTML parsing and title metadata extraction
- `crawler.py` - Main crawler with CLI interface

**CLI Commands:**
```bash
python -m scraper.crawler --init          # Initialize database
python -m scraper.crawler --year 2024     # Crawl specific year
python -m scraper.crawler --all           # Full crawl with resume
python -m scraper.crawler --stats         # Show statistics
python -m scraper.crawler --clear-checkpoint  # Clear checkpoint
```

**Tested:**
- Year 2024 (modern format): 82 documents, rich metadata parsed
- Year 2000 (sparse format): Letter IDs extracted correctly
- Retry logic handles intermittent network failures
- Checkpoint saves after each page for resume
- Duplicate detection via UNIQUE constraint on pdf_url

### Final Crawl Results

**Total: 14,132 documents** across 51 years (1975-2025)

| Decade | Documents | Notes |
|--------|-----------|-------|
| 1975-1979 | 1,171 | Earliest records |
| 1980-1989 | 3,380 | Peak year: 1990 (1,109 docs) |
| 1990-1999 | 4,671 | Busiest decade |
| 2000-2009 | 2,473 | |
| 2010-2019 | 1,954 | |
| 2020-2025 | 670 | Through Jan 2025 |

**Runtime:** ~2.5 hours total (two crawl runs)

---

## Phase 2: Download PDFs

**Objective:** Download all PDFs to local storage.

### Tasks

- [ ] Query database for documents where `download_status = 'pending'`
- [ ] Download each PDF with:
  - [ ] Polite delays (2-3 seconds between requests)
  - [ ] Retry logic (3 attempts with backoff)
  - [ ] Compute SHA256 hash
  - [ ] Store in `raw_pdfs/{year}/{filename}.pdf`
- [ ] Update database: status, size, hash, timestamp
- [ ] Handle failures gracefully (mark as 'failed', continue)

### Storage Estimate
- 14,132 documents (actual count from registry)
- Average ~300KB each (based on samples: 127KB to 933KB)
- Total: ~4-5GB estimated

### Estimated Time
- 2-3 seconds per download
- ~16,000 files
- ~10-13 hours

---

## Phase 3: Extract Text

**Objective:** Extract text from all PDFs, using OCR when necessary.

### Extraction Pipeline

```
For each PDF:
1. Try native extraction (PyMuPDF)
2. Compute quality score:
   - word_count / page_count ratio
   - % alphabetic characters
   - presence of expected patterns (QUESTION, date, etc.)
3. If quality < threshold:
   - Extract page images
   - Run Tesseract OCR
   - Re-score
4. Parse structured fields:
   - Date
   - File number (A-XX-XXX, I-XX-XXX)
   - Requestor name
   - Sections (QUESTION, CONCLUSION, ANALYSIS)
5. Store extracted text + metadata
```

### Output Format

```
data/
├── documents.db          # SQLite with metadata
├── extracted/
│   ├── 2024/
│   │   ├── 24006.json    # Structured extraction
│   │   └── 24006.txt     # Raw text
│   ├── 2015/
│   └── ...
```

JSON structure:
```json
{
  "id": "A-24-006",
  "year": 2024,
  "date": "2024-01-23",
  "requestor": "Alan J. Peake",
  "city": "Bakersfield",
  "letter_type": "formal",
  "extraction_method": "native",
  "page_count": 4,
  "word_count": 1713,
  "sections": {
    "question": "...",
    "conclusion": "...",
    "facts": "...",
    "analysis": "..."
  },
  "full_text": "...",
  "pdf_url": "https://...",
  "tags": ["Advice Letter", "2024"]
}
```

### Tasks

- [ ] Build extraction module
- [ ] Define quality threshold (experiment with samples)
- [ ] Implement OCR fallback
- [ ] Write section parser (QUESTION/CONCLUSION/etc.)
- [ ] Store results as JSON + txt files
- [ ] Update database with extraction status

### Estimated Time
- Native extraction: ~0.5 seconds per PDF
- OCR: ~5-10 seconds per page
- Estimate 20-30% need OCR
- Total: ~5-10 hours

---

## Phase 4: Enrichment (Optional)

**Objective:** Add structured metadata not available from extraction.

### Potential Enrichments

1. **Topic Classification**
   - Use 2020+ monthly reports as training labels
   - Categories: Conflict of Interest, Campaign Finance, Revolving Door, Section 84308
   - Train simple classifier or use LLM for low-confidence cases

2. **Citation Extraction**
   - Find Government Code sections cited (e.g., "Section 87100")
   - Find Regulation references (e.g., "Regulation 18703")
   - Build citation index

3. **Entity Extraction**
   - Government agencies mentioned
   - Positions/titles discussed

### Tasks

- [ ] Scrape monthly reports (2020-2025) for summaries + tags
- [ ] Build topic classifier
- [ ] Write citation regex extractor
- [ ] Apply to all documents

---

## Phase 5: Output for Downstream Use

**Objective:** Package data for RAG/search/ML use.

### Outputs

1. **For Search UI**
   - SQLite database with full metadata
   - JSON files for each document
   - Consider: Elasticsearch/Meilisearch index

2. **For RAG**
   - Chunked text (paragraph level)
   - Embeddings (sentence-transformers)
   - Vector store (ChromaDB, FAISS, etc.)

3. **For ML Training**
   - JSONL export
   - Train/test splits by year

### Tasks

- [ ] Define final schema for search
- [ ] Build chunking logic
- [ ] Generate embeddings
- [ ] Export formats

---

## Test Scripts Status

| Script | Purpose | Status |
|--------|---------|--------|
| `01_test_endpoint_speed.py` | Compare endpoints | ✓ Done |
| `02_inspect_page_structure.py` | HTML structure | ✓ Done |
| `03_test_year_filter.py` | Year filter URL | ✓ Done |
| `04_test_pdf_extraction.py` | Text extraction | ✓ Done |
| `05_check_pdf_images.py` | Image detection | ✓ Done |
| `06_test_ocr.py` | Tesseract OCR | ✓ Done |

---

## Open Questions

1. **Commission Opinions**: Include the ~100 commission opinions? (Recommend: yes)
2. **Monthly Reports**: Scrape 2020-2025 reports separately for summaries? (Recommend: yes, they're gold)
3. **Storage**: Keep raw PDFs long-term or just extracted text?
4. **OCR threshold**: What word count triggers OCR fallback? (Try: < 100 words for 2+ page doc)

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Rate limiting/blocking | Polite delays, rotate user-agent, respect robots.txt |
| Site structure change | Save raw HTML samples, version selectors |
| OCR quality | Store extraction_method flag, allow re-extraction |
| Data loss | Checkpoint frequently, backup database |
| Large storage | gitignore raw_pdfs, consider cloud storage |

---

## Next Immediate Steps

1. ~~**Create database and crawler skeleton**~~ ✓ DONE
   - ~~Set up SQLite with schema~~
   - ~~Write basic pagination + parsing~~
   - ~~Test on one year~~

2. ~~**Test pagination thoroughly**~~ ✓ DONE
   - ~~Walk all pages of a small year (e.g., 2024 with 82 results)~~
   - ~~Verify we capture all documents~~
   - ~~Check for edge cases (last page, empty results)~~

3. ~~**Run full registry crawl**~~ ✓ DONE
   - ~~14,132 documents indexed~~
   - ~~51 years covered (1975-2025)~~
   - ~~~2.5 hours runtime~~

4. **Begin Phase 2: Download PDFs**
   - Build downloader module with same patterns (retry, checkpoint)
   - Download all 14,132 PDFs to `raw_pdfs/{year}/`
   - Update database with download status, size, hash
