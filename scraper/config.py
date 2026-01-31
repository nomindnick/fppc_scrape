"""Configuration constants for the FPPC scraper."""

from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# URLs
BASE_URL = "https://fppc.ca.gov/advice/advice-opinion-search.html"
YEAR_FILTER = "?SearchTerm=&tag1=/etc/tags/fppc/year/{year}&tagCount=1"
PAGE_PARAM = "&page={page}"

# Request settings
DELAY_SECONDS = 4  # Polite delay between requests
TIMEOUT = 120
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # Exponential backoff multiplier
HEADERS = {"User-Agent": "FPPC-Research-Bot/1.0 (academic research)"}

# Database
DB_PATH = DATA_DIR / "documents.db"
CHECKPOINT_PATH = DATA_DIR / "checkpoint.json"

# Year range
START_YEAR = 1975
END_YEAR = 2025
