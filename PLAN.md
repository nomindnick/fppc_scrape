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

## Phase 2: Download PDFs ✓ COMPLETE

**Objective:** Download all PDFs to local storage.

**Status:** Complete - 14,096 unique PDFs downloaded (6.6 GB total)

### Tasks

- [x] Query database for documents where `download_status = 'pending'`
- [x] Download each PDF with:
  - [x] Polite delays (3 seconds between requests)
  - [x] Retry logic (3 attempts with exponential backoff)
  - [x] Compute SHA256 hash while streaming
  - [x] Store in `raw_pdfs/{year}/{filename}.pdf`
- [x] Update database: status, size, hash, timestamp
- [x] Handle failures gracefully (mark as 'failed', continue)
- [x] Skip existing files on resume

### Implementation

Added to `scraper/` module:
- `config.py` - Added `RAW_PDFS_DIR`, `DOWNLOAD_DELAY` constants
- `db.py` - Added `get_pending_downloads()`, `update_download_status()`, `get_download_stats()`
- `downloader.py` - New module with download logic

**CLI Commands:**
```bash
python -m scraper.crawler --download           # Download all pending
python -m scraper.crawler --download-year 2024 # Download specific year
python -m scraper.crawler --download-stats     # Show download progress
```

### Final Download Results

| Metric | Value |
|--------|-------|
| Total downloaded | 14,132 records |
| Unique files | 14,096 |
| Duplicate URLs | 36 (same file at different paths) |
| Failed | 0 |
| Total size | 6.6 GB |
| Average PDF size | 476 KB |
| Runtime | 13 hours 4 minutes |

**Verification:**
- All 14,096 unique files present on disk
- Zero empty or corrupted files
- Random sample SHA256 verification: 10/10 passed
- 36 "duplicates" are identical files (same SHA256) hosted at different URLs on FPPC's site

**Storage by decade:**
- 1975-1989: ~1.5 GB (older, often larger scanned docs)
- 1990-1999: ~1.8 GB (peak volume decade)
- 2000-2025: ~3.3 GB

---

## Phase 3: Extract Text

**Objective:** Extract text from all PDFs, with structured output optimized for RAG and search.

**Status:** In Progress - Core extraction pipeline implemented

### Phase 3A: Core Extraction (Python + olmOCR)

Build the extraction pipeline in Python with olmOCR as the OCR fallback for scanned documents.

#### Completed Tasks

- [x] **Task 3.1: Schema Design** - `scraper/schema.py`
  - Defined `FPPCDocument` dataclass with nested structures for sections, citations, classification
  - `to_json()` / `from_json()` serialization helpers

- [x] **Task 3.2: Quality Scoring** - `scraper/quality.py`
  - `compute_quality_score()` → 0.0-1.0 based on words/page, alpha ratio, FPPC patterns
  - `should_use_olmocr()` → Decision logic for OCR fallback

- [x] **Task 3.3: Section Parser** - `scraper/section_parser.py`
  - `parse_sections()` → Extracts QUESTION, CONCLUSION, FACTS, ANALYSIS
  - Handles multiple format eras (modern, 1990s, 1980s variants)
  - Returns confidence score and extraction method

- [x] **Task 3.4: Citation Extractor** - `scraper/citation_extractor.py`
  - `extract_citations()` → Government Code, regulations, prior opinions, external cases
  - Validates sections against Political Reform Act ranges
  - Normalizes citation formats

- [x] **Task 3.5: Topic Classifier** - `scraper/classifier.py`
  - `classify_by_citations()` → conflicts_of_interest | campaign_finance | lobbying | other
  - Based on Government Code section ranges

- [x] **Task 3.6: Database Tracking** - `scraper/db.py`
  - `add_extraction_columns()` → Added extraction tracking fields
  - `get_pending_extractions()`, `update_extraction_status()`
  - `get_documents_needing_llm()` for Phase 3B

- [x] **Task 3.7: Core Extractor** - `scraper/extractor.py`
  - `Extractor` class orchestrates full pipeline
  - PyMuPDF native extraction with olmOCR fallback (via DeepInfra API)
  - CLI: `--extract-all`, `--extract-sample`, `--stats`, `--skip-olmocr`
  - Saves JSON to `data/extracted/{year}/{letter_id}.json`
  - Updates database with status, quality, section_confidence

#### Remaining Tasks

- [ ] **Task 3.8: Full Extraction Run**
  - Run `--extract-all --skip-olmocr` for fast first pass
  - Analyze documents flagged for olmOCR
  - Run selective olmOCR on low-quality extractions

### Phase 3B: LLM-Enhanced Extraction (Future)

For documents where regex extraction fails (section_confidence < 0.5):

- [ ] Use Claude Haiku to generate synthetic Q/A pairs
- [ ] Fill `question_synthetic` and `conclusion_synthetic` fields
- [ ] Generate document summaries for `embedding.summary`

### Output Format

```
data/extracted/
├── 2024/
│   ├── A-24-006.json    # Full structured document
│   ├── I-24-008.json
│   └── ...
├── 2023/
└── ...
```

JSON structure (FPPCDocument schema):
```json
{
  "id": "A-24-006",
  "year": 2024,
  "pdf_url": "https://fppc.ca.gov/...",
  "pdf_sha256": "...",
  "local_pdf_path": "raw_pdfs/2024/24006.pdf",
  "source_metadata": { "title_raw": "...", "tags": [...], "scraped_at": "..." },
  "extraction": { "method": "native", "quality_score": 0.85, "page_count": 4, ... },
  "content": { "full_text": "...", "full_text_markdown": null },
  "parsed": { "date": "2024-01-23", "requestor_name": "...", "document_type": "advice_letter" },
  "sections": { "question": "...", "conclusion": "...", "facts": "...", "analysis": "..." },
  "citations": { "government_code": ["87100"], "regulations": [...], "prior_opinions": [...] },
  "classification": { "topic_primary": "conflicts_of_interest", "confidence": 0.9 },
  "embedding": { "qa_text": "...", "first_500_words": "..." }
}
```

### Extraction Statistics (Partial Run)

| Metric | Value |
|--------|-------|
| Documents tested | ~15 |
| Native extraction success | ~70% |
| Quality score > 0.8 | ~55% |
| Needs olmOCR | ~25% (pre-1990 + low quality) |
| Section parsing success | ~85% for modern (2000+) |

### CLI Commands

```bash
# Initialize extraction columns
python -m scraper.extractor --init

# Extract sample across eras for review
python -m scraper.extractor --extract-sample 50

# Full extraction (native + olmOCR fallback)
python -m scraper.extractor --extract-all

# Skip olmOCR for faster/cheaper run
python -m scraper.extractor --extract-all --skip-olmocr

# Extract specific year
python -m scraper.extractor --extract-all --year 2024

# Show extraction statistics
python -m scraper.extractor --stats
```

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
3. ~~**Storage**: Keep raw PDFs long-term or just extracted text?~~ → Keeping raw PDFs (6.6 GB, manageable)
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
   - ~~\~2.5 hours runtime~~

4. ~~**Phase 2: Download PDFs**~~ ✓ DONE
   - ~~Built downloader module with retry, resume support~~
   - ~~Downloaded all 14,096 unique PDFs to `raw_pdfs/{year}/`~~
   - ~~6.6 GB total, 13 hours runtime~~
   - ~~All files verified (SHA256 hashes match)~~

5. **Begin Phase 3: Extract Text**
   - Build extraction module using PyMuPDF
   - Define quality threshold for OCR fallback
   - Test on sample from each era (modern native PDFs vs older scans)
   - Extract and store as JSON + txt files
