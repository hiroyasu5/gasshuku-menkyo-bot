import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"

# Scraping
TARGET_START_DATE = "2026-07-26"
TARGET_LICENCE_TYPE = "AT"
HTTP_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds

# User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
