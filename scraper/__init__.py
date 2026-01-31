"""FPPC Advice Letter Scraper - Document Registry Module."""

from .config import DB_PATH, START_YEAR, END_YEAR
from .db import init_db, get_stats

__all__ = [
    "DB_PATH",
    "START_YEAR",
    "END_YEAR",
    "init_db",
    "get_stats",
]
