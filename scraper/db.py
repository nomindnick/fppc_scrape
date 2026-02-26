"""Database setup and operations for the FPPC document registry."""

import sqlite3
from pathlib import Path

from .config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create database tables and indexes."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,

            -- From search results
            pdf_url TEXT UNIQUE NOT NULL,
            title_text TEXT,
            year_tag INTEGER,
            tags TEXT,
            source_page_url TEXT,

            -- Parsed from title (when available)
            requestor_name TEXT,
            letter_id TEXT,
            letter_date TEXT,
            city TEXT,

            -- Download status
            download_status TEXT DEFAULT 'pending',
            downloaded_at TEXT,
            pdf_size_bytes INTEGER,
            pdf_sha256 TEXT,

            -- Extraction status
            extraction_status TEXT DEFAULT 'pending',
            extraction_method TEXT,
            extraction_quality REAL,
            page_count INTEGER,
            word_count INTEGER,

            -- Timestamps
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_year ON documents(year_tag)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_download_status ON documents(download_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_extraction_status ON documents(extraction_status)")

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def insert_document(doc: dict) -> bool:
    """
    Insert a document into the database.

    Returns True if inserted, False if already exists (duplicate URL).
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO documents (
                pdf_url, title_text, year_tag, tags, source_page_url,
                requestor_name, letter_id, letter_date, city
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.get("pdf_url"),
                doc.get("title_text"),
                doc.get("year_tag"),
                doc.get("tags"),
                doc.get("source_page_url"),
                doc.get("requestor_name"),
                doc.get("letter_id"),
                doc.get("letter_date"),
                doc.get("city"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Duplicate URL
        return False
    finally:
        conn.close()


def document_exists(pdf_url: str) -> bool:
    """Check if a document with this URL already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM documents WHERE pdf_url = ?", (pdf_url,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_stats() -> dict:
    """Get database statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total count
    cursor.execute("SELECT COUNT(*) FROM documents")
    stats["total"] = cursor.fetchone()[0]

    # By year
    cursor.execute("""
        SELECT year_tag, COUNT(*) as count
        FROM documents
        GROUP BY year_tag
        ORDER BY year_tag DESC
    """)
    stats["by_year"] = {row["year_tag"]: row["count"] for row in cursor.fetchall()}

    # By download status
    cursor.execute("""
        SELECT download_status, COUNT(*) as count
        FROM documents
        GROUP BY download_status
    """)
    stats["by_download_status"] = {row["download_status"]: row["count"] for row in cursor.fetchall()}

    # By extraction status
    cursor.execute("""
        SELECT extraction_status, COUNT(*) as count
        FROM documents
        GROUP BY extraction_status
    """)
    stats["by_extraction_status"] = {row["extraction_status"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return stats


def get_document_count() -> int:
    """Get total document count."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM documents")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_year_count(year: int) -> int:
    """Get document count for a specific year."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM documents WHERE year_tag = ?", (year,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def check_duplicates() -> list[tuple[str, int]]:
    """Check for duplicate PDF URLs (should be none due to UNIQUE constraint)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pdf_url, COUNT(*) as count
        FROM documents
        GROUP BY pdf_url
        HAVING COUNT(*) > 1
    """)
    duplicates = [(row["pdf_url"], row["count"]) for row in cursor.fetchall()]
    conn.close()
    return duplicates


def get_pending_downloads(year: int | None = None, limit: int | None = None) -> list[dict]:
    """
    Query documents with download_status='pending'.

    Args:
        year: Optional year filter
        limit: Optional limit on number of results

    Returns:
        List of document dicts with id, pdf_url, year_tag
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT id, pdf_url, year_tag FROM documents WHERE download_status = 'pending'"
    params: list = []

    if year is not None:
        query += " AND year_tag = ?"
        params.append(year)

    query += " ORDER BY year_tag DESC, id"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def update_download_status(
    doc_id: int,
    status: str,
    size: int | None = None,
    sha256: str | None = None,
) -> None:
    """
    Update a document's download status, size, hash, and timestamps.

    Args:
        doc_id: Document ID
        status: New download status ('downloaded', 'failed', 'pending')
        size: PDF file size in bytes
        sha256: SHA256 hash of the PDF
    """
    conn = get_connection()
    cursor = conn.cursor()

    if status == "downloaded":
        cursor.execute(
            """
            UPDATE documents
            SET download_status = ?,
                pdf_size_bytes = ?,
                pdf_sha256 = ?,
                downloaded_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, size, sha256, doc_id),
        )
    else:
        cursor.execute(
            """
            UPDATE documents
            SET download_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, doc_id),
        )

    conn.commit()
    conn.close()


def get_download_stats() -> dict:
    """
    Get download-specific statistics.

    Returns:
        Dict with pending, downloaded, failed counts and total size
    """
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Counts by status
    cursor.execute("""
        SELECT download_status, COUNT(*) as count
        FROM documents
        GROUP BY download_status
    """)
    for row in cursor.fetchall():
        stats[row["download_status"]] = row["count"]

    # Total downloaded size
    cursor.execute("""
        SELECT SUM(pdf_size_bytes) as total_size
        FROM documents
        WHERE download_status = 'downloaded'
    """)
    result = cursor.fetchone()
    stats["total_size_bytes"] = result["total_size"] or 0

    # By year (pending only)
    cursor.execute("""
        SELECT year_tag, COUNT(*) as count
        FROM documents
        WHERE download_status = 'pending'
        GROUP BY year_tag
        ORDER BY year_tag DESC
    """)
    stats["pending_by_year"] = {row["year_tag"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return stats


def add_extraction_columns() -> None:
    """
    Add extraction tracking columns to the documents table.

    Safe to call multiple times - silently ignores columns that already exist.
    Run this before starting extraction to ensure schema is ready.
    """
    conn = get_connection()
    cursor = conn.cursor()

    new_columns = [
        ("extracted_at", "TEXT"),
        ("section_confidence", "REAL"),
        ("json_path", "TEXT"),
        ("needs_llm_extraction", "INTEGER DEFAULT 0"),
        ("llm_extracted_at", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()


def add_fidelity_columns() -> None:
    """
    Add fidelity verification columns to the documents table.

    Columns:
        fidelity_score  — 0.0-1.0, how closely extraction matches source PDF
        fidelity_method — how fidelity was assessed (tesseract_canary, haiku_verified, native_trusted)
        fidelity_risk   — tier label (critical, high, medium, low, verified)

    Safe to call multiple times.
    """
    conn = get_connection()
    cursor = conn.cursor()

    new_columns = [
        ("fidelity_score", "REAL"),
        ("fidelity_method", "TEXT"),
        ("fidelity_risk", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()


def backfill_native_fidelity() -> int:
    """
    Backfill fidelity for native-extracted docs (PyMuPDF extracts embedded text directly).

    Native extraction is deterministic and faithful — it reads the actual text
    layer from the PDF, so fidelity_score=1.0 is correct.

    Returns:
        Number of rows updated.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE documents
        SET fidelity_score = 1.0,
            fidelity_method = 'native_trusted',
            fidelity_risk = 'verified'
        WHERE extraction_status = 'extracted'
        AND extraction_method = 'native'
        AND fidelity_score IS NULL
    """)
    updated = cursor.rowcount

    # native+olmocr means olmOCR was tried but native was kept — same trust level
    cursor.execute("""
        UPDATE documents
        SET fidelity_score = 1.0,
            fidelity_method = 'native_trusted',
            fidelity_risk = 'verified'
        WHERE extraction_status = 'extracted'
        AND extraction_method = 'native+olmocr'
        AND fidelity_score IS NULL
    """)
    updated += cursor.rowcount

    conn.commit()
    conn.close()
    return updated


def update_fidelity(
    doc_id: int,
    score: float,
    method: str,
    risk: str,
) -> None:
    """
    Update fidelity verification fields for a document.

    Args:
        doc_id: Document ID
        score: Fidelity score 0.0-1.0
        method: Assessment method (tesseract_canary, haiku_verified, tesseract_fallback)
        risk: Risk tier (critical, high, medium, low, verified)
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE documents
        SET fidelity_score = ?,
            fidelity_method = ?,
            fidelity_risk = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (score, method, risk, doc_id))
    conn.commit()
    conn.close()


def get_pending_extractions(year: int | None = None, limit: int | None = None) -> list[dict]:
    """
    Query documents ready for text extraction.

    Args:
        year: Optional year filter
        limit: Optional limit on number of results

    Returns:
        List of document dicts (full row data)
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM documents
        WHERE download_status = 'downloaded'
        AND (extraction_status = 'pending' OR extraction_status IS NULL)
    """
    params: list = []

    if year is not None:
        query += " AND year_tag = ?"
        params.append(year)

    query += " ORDER BY year_tag DESC, id"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_documents_needing_llm(limit: int | None = None) -> list[dict]:
    """
    Query documents flagged for LLM-based extraction (Phase 3B).

    Returns documents that:
    - Have been extracted (extraction_status = 'extracted')
    - Are flagged for LLM processing (needs_llm_extraction = 1)
    - Haven't been LLM-processed yet (llm_extracted_at IS NULL)

    Args:
        limit: Optional limit on number of results

    Returns:
        List of document dicts (full row data)
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM documents
        WHERE extraction_status = 'extracted'
        AND needs_llm_extraction = 1
        AND llm_extracted_at IS NULL
        ORDER BY year_tag DESC, id
    """

    if limit is not None:
        query += " LIMIT ?"
        cursor.execute(query, (limit,))
    else:
        cursor.execute(query)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def update_extraction_status(
    doc_id: int,
    status: str,
    method: str | None = None,
    quality: float | None = None,
    section_confidence: float | None = None,
    json_path: str | None = None,
    needs_llm: bool = False,
) -> None:
    """
    Update extraction status and related fields for a document.

    Args:
        doc_id: Document ID
        status: New extraction status ('extracted', 'error', 'pending')
        method: Extraction method used ('native', 'olmocr', 'native+olmocr')
        quality: Text quality score (0.0-1.0)
        section_confidence: Section parsing confidence (0.0-1.0)
        json_path: Path to the extracted JSON file
        needs_llm: Whether document needs LLM extraction in Phase 3B
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE documents
        SET extraction_status = ?,
            extracted_at = CURRENT_TIMESTAMP,
            extraction_method = COALESCE(?, extraction_method),
            extraction_quality = COALESCE(?, extraction_quality),
            section_confidence = COALESCE(?, section_confidence),
            json_path = COALESCE(?, json_path),
            needs_llm_extraction = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, method, quality, section_confidence, json_path,
         1 if needs_llm else 0, doc_id),
    )

    conn.commit()
    conn.close()


def update_llm_extraction_status(doc_id: int, section_confidence: float) -> None:
    """Mark a document as LLM-processed (Phase 3B)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE documents
        SET llm_extracted_at = CURRENT_TIMESTAMP,
            needs_llm_extraction = 0,
            section_confidence = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (section_confidence, doc_id))
    conn.commit()
    conn.close()
