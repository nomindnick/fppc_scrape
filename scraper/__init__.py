"""FPPC Advice Letter Scraper - Document Registry Module."""

from .config import DB_PATH, RAW_PDFS_DIR, START_YEAR, END_YEAR
from .db import init_db, get_stats, get_download_stats
from .downloader import download_pending, print_download_stats
from .citation_extractor import extract_citations, CitationResult

__all__ = [
    "DB_PATH",
    "RAW_PDFS_DIR",
    "START_YEAR",
    "END_YEAR",
    "init_db",
    "get_stats",
    "get_download_stats",
    "download_pending",
    "print_download_stats",
    "extract_citations",
    "CitationResult",
]
