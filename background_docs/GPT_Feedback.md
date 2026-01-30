GPT-5.2 Feedback

Here’s my take after reading your recon notes (and poking around the current FPPC site structure a bit). I’ll focus on the stuff that will most improve reliability + reduce pain, and I’ll also answer a couple of your “open questions” with what I could verify.

## The biggest “upgrade” to your plan: don’t anchor on the slow legacy page

Your notes focus on `https://fppc.ca.gov/advice/advice-opinion-search.html` and mention 30–60s loads per page. That may still be true for that legacy endpoint, but there are *newer* search entry points that appear to serve the same archive and are easier to work with:

- **“The Law” Advice Letter Search** (`/the-law/opinions-and-advice-letters/law-advice-search.html`) ([FPPC](https://www.fppc.ca.gov/the-law/opinions-and-advice-letters/law-advice-search.html?SearchTerm=18-149&tag1=na&tagCount=1 "Search Advice Letters"))

- **Transparency Portal Advice Letter Search** (`/transparency/form-700-filed-by-public-officials/advice-letter-search.html`) ([FPPC](https://fppc.ca.gov/transparency/form-700-filed-by-public-officials/advice-letter-search.html?SearchTerm=2023&page=7&tag1=%2F&tagCount=1 "Advice Letter Search"))

Both expose the same general UI concept (SearchTerm + Year dropdown + pagination) and—critically—both results pages contain direct PDF links that are very scrape-friendly. ([FPPC](https://www.fppc.ca.gov/the-law/opinions-and-advice-letters/law-advice-search.html?SearchTerm=18-149&tag1=na&tagCount=1 "Search Advice Letters"))

If you do nothing else: **build your scraper against the newer `law-advice-search.html` or the transparency version**, and keep the old `advice-opinion-search.html` only as a fallback. (Also: the old page’s copy claims “since 1984,” while the newer pages say “since 1975,” and 1970s letters are indeed accessible.) ([FPPC](https://www.fppc.ca.gov/advice/advice-opinion-search.html?utm_source=chatgpt.com "Search Advice Letters - FPPC - CA.gov"))

## Your “Year filter URL pattern” question: you’re basically one step away

You flagged: “TODO document exact URL parameter for year filtering.”

What I can confirm: **the Year dropdown options themselves have values that look like**:

`/etc/tags/fppc/year/2024`, `/etc/tags/fppc/year/1975`, etc. ([FPPC](https://fppc.ca.gov/transparency/form-700-filed-by-public-officials/advice-letter-search.html?SearchTerm=2023&page=7&tag1=%2F&tagCount=1 "Advice Letter Search"))

That strongly suggests the “year filter” is implemented via the existing `tag1` mechanism you already observed (`tagCount=1`). In other words, your “year filter URL” is *very likely*:

- `tag1=/etc/tags/fppc/year/2024` (URL-encoded), with `tagCount=1`

I didn’t get a clean, indexed example URL that shows `tag1=%2Fetc%2Ftags%2Ffppc%2Fyear%2F2024` in the wild (search engines often don’t index every variant), but **the dropdown option values are clear evidence of what the site expects**. ([FPPC](https://fppc.ca.gov/transparency/form-700-filed-by-public-officials/advice-letter-search.html?SearchTerm=2023&page=7&tag1=%2F&tagCount=1 "Advice Letter Search"))

Practical tip: in your browser, pick a year and hit Search, then just copy the resulting URL—your scraper can reproduce it exactly thereafter.

## Your “PDF URL patterns” question: yes, they’re predictable by era

You asked whether you can derive PDF URLs without scraping every listing. The answer is: **sometimes**, and it depends on the year range. Here are real examples:

### Modern (at least 2020s): per-year folder, filename is YYNNN

Example: A‑25‑050 is stored as:

- `.../advice-letters/2025/25050.pdf` ([FPPC](https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2025/25050.pdf "Melissa M. Crosthwaite - A-25-050 - May 5, 2025 - Santa Ana"))  
  Example: I‑23‑009 is:

- `.../advice-letters/2023/23009.pdf` ([FPPC](https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2023/23009.pdf "Gary S. Winuk - I-23-009 - April 7, 2023 - Sacramento"))

This is great because you can treat `Our File No. A-25-050` ⇒ `25050.pdf`.

### 1995–2015: grouped folder + year folder + filename “YY-NNN.pdf”

Example (1996 letter):

- `.../advice-letters/1995-2015/1996/96-149.pdf` ([FPPC](https://www.fppc.ca.gov/content/dam/fppc/documents/advice-letters/1995-2015/1996/96-149.pdf "Year: 1996 Advice Letter # 96-149"))

### 1984–1994: grouped folder + year folder + filename like “90481.pdf”

Example (1990):

- `.../advice-letters/1984-1994/1990/90481.pdf` ([FPPC](https://www.fppc.ca.gov/content/dam/fppc/documents/advice-letters/1984-1994/1990/90481.pdf "Sands, John Year: 1990 Advice Letter # 90481"))

### 1976–1983: grouped folder + year folder + filename like “77ADV-77-026.PDF”

Example (1977):

- `.../advice-letters/1976-1983/1977/77ADV-77-026.PDF` ([FPPC](https://www.fppc.ca.gov/content/dam/fppc/documents/advice-letters/1976-1983/1977/77ADV-77-026.PDF "Year: 1977ADV Advice Letter # 77ADV-77-026"))

**Implication:** your scraper can absolutely learn these patterns and use them for:

- URL validation (catch broken links)

- De-duplication

- possibly “gap filling” if listing pages miss a document

…but I wouldn’t go full brute-force “guess every possible filename” unless you have a reason. The safest approach is still: **crawl listing pages → collect actual URLs → download PDFs.**

## Your “A / I / M meaning” question: you can infer a lot just from the PDFs

You suspected:

- A = Advice

- I = Informal

- M = Memorandum

From the PDFs:

- An **I‑23‑009** letter explicitly says it is being treated as “informal assistance” and even notes the immunity distinction. ([FPPC](https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2023/23009.pdf "Gary S. Winuk - I-23-009 - April 7, 2023 - Sacramento"))

- An **A‑25‑050** letter reads like formal staff advice and has the classic “QUESTION / CONCLUSION / FACTS / ANALYSIS” structure. ([FPPC](https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2025/25050.pdf "Melissa M. Crosthwaite - A-25-050 - May 5, 2025 - Santa Ana"))

So: your hunch about **I = informal assistance** is strongly supported. (M is still “verify,” but your guess is reasonable.)

Also: don’t be shocked if you encounter additional prefixes besides A/I/M. Older PDFs and OCR can show weird artifacts; you’ll want to treat “prefix” as a *field* but not assume a closed set.

## Metadata quality: your “must parse PDFs for older letters” conclusion is correct — and here’s the wrinkle

Your recon says older letters may be raw filenames and inconsistent. 100% true, and it’s worse than “missing metadata”:

- Some old PDFs are **scanned with messy OCR** (you’ll see garbling and incorrect characters), which means “extract date/requestor/file-no from text layer” is not always reliable. ([FPPC](https://www.fppc.ca.gov/content/dam/fppc/documents/advice-letters/1984-1994/1990/90481.pdf "Sands, John Year: 1990 Advice Letter # 90481"))

So I’d design PDF processing like this:

1. **Try text extraction** (fast).

2. Compute a quick “quality heuristic” (e.g., too few alphabetic chars, or too many replacement glyphs, or low token count on page 1).

3. If it fails: **OCR only page 1** first (often enough to get date + file no + requestor), and OCR the rest only if you actually need full-text search.

That keeps OCR costs bounded.

## I’d tweak your overall approach into a more “pipeline” shape

Your Phase 1–4 is solid. I’d just tighten it into something that’s resilient and makes re-runs painless.

### Step A: Build a canonical “document registry” table first

Before you download PDFs, crawl listing pages and store rows like:

- `doc_id` (your own stable ID)

- `source_listing_url`

- `pdf_url`

- `year_tag` (from “Filed under”)

- `title_text` (whatever the listing shows)

- `scraped_at`

- `pdf_sha256` (once downloaded)

- `status` (queued/downloaded/failed)

This gives you resumability and makes it easy to re-crawl later without duplicating downloads.

### Step B: Download PDFs with polite throttling + caching

Even if you’re not getting blocked today, behave like you *will* eventually:

- 1–2 concurrent downloads max (or even single-threaded)

- random jitter between requests

- conditional requests if you re-run (If-Modified-Since / ETag if they send them)

- clear user-agent + contact email

### Step C: Extract structured fields from PDFs (especially from page 1)

For modern letters, you can reliably parse:

- date

- requestor name

- organization

- city

- “Our File No.”

- section headings (“QUESTION”, “CONCLUSION”…)

You can see how cleanly those appear in recent PDFs. ([FPPC](https://fppc.ca.gov/content/dam/fppc/documents/advice-letters/2025/25050.pdf "Melissa M. Crosthwaite - A-25-050 - May 5, 2025 - Santa Ana"))

Even for older letters, page 1 often still has the essentials (sometimes with OCR cleanup).

### Step D: Index for search (hybrid > embeddings-only)

For attorney use, I’d do:

- **BM25 / keyword index** (fast, predictable, citations feel “legal research-y”)

- plus **embeddings** as a second channel (semantic recall)

- plus a lightweight reranker (optional)

And I’d expose filters that matter in practice:

- year

- letter type (A/I/…)

- statute/reg citations (parse “Section 87100”, “Regulation 18703”, etc.)

- topic tags (for 2020+ you get them from the monthly reports; for older you can model/heuristic them)

## One suggestion you didn’t mention: treat the 2020+ monthly reports as “training labels”

You already like those pages because they have:

- summaries

- category tags

That is *gold* for bootstrapping classifiers:

- Use the 2020+ corpus as labeled data

- Train a lightweight topic classifier (even just logistic regression on TF‑IDF) to tag older letters

- Then use an LLM *only* when the classifier is low confidence

This keeps the pipeline cheaper and more deterministic.

## Don’t forget the “law changed” / “not precedential” UX bits

The legacy advice search page explicitly warns that:

- advice letters reflect law at the time

- advice letters aren’t precedential for other parties

- statutes/regs change ([FPPC](https://www.fppc.ca.gov/advice/advice-opinion-search.html?utm_source=chatgpt.com "Search Advice Letters - FPPC - CA.gov"))

If you’re building a tool for legal work-product, I’d put those warnings right in the UI:

- show letter date prominently

- show “current law may differ” banner

- always link back to the official PDF URL

It’ll save you from accidentally over-weighting a 1990 letter in a 2026 fact pattern.

## Commission Opinions: yes, include them (and they’re small)

Your recon asks whether to include Commission Opinions. My vote: **yes**.

- There are “over 100” opinions (so, tiny compared to advice letters). ([FPPC](https://www.fppc.ca.gov/advice/opinion-search.html "Search Commission Opinions"))

- There’s a Commission Opinion Index PDF that is already pretty structured (name, cite, FPPC no., date, notes). ([FPPC](https://www.fppc.ca.gov/content/dam/fppc/NS-Documents/LegalDiv/Commission%20Opinion%20Index/opinion%20index%202018.pdf "Adams, Janet K"))

That index is basically a ready-made metadata table; you can ingest it and then link to the underlying opinion PDFs (or at least make them findable by FPPC number).

## A couple of pointed follow-ups (so I can tailor recommendations)

1. Is this **strictly internal** (you + maybe your firm), or are you imagining a **public-facing** tool?

2. Do you want the system to store **full PDFs locally**, or just store **extracted text + metadata** and always deep-link to FPPC-hosted PDFs?

3. What search stack would you prefer: “simple and local” (SQLite FTS + embeddings) or “real search engine” (OpenSearch/Elastic/Meilisearch/Typesense + vectors)?

If you answer those, I can suggest a concrete architecture (and a scraping + indexing code skeleton) that fits your tolerance for infrastructure and maintenance.
