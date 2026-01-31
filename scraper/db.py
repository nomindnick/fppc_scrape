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
