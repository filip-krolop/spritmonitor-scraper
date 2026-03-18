"""Configuration for the Spritmonitor scraper."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration loaded from environment / defaults."""

    # ── URLs ──────────────────────────────────────────────────────────
    BASE_URL = "https://www.spritmonitor.de"
    OVERVIEW_URL = f"{BASE_URL}/en/"                              # CHANGED
    AJAX_MODEL_URL = f"{BASE_URL}/en/ajaxModel.action"            # NEW
    LANG = "en"

    # ── HTTP ──────────────────────────────────────────────────────────
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "VroomBroom-DataBot/1.0 (data@vroombroom.app)",
    )
    REQUEST_DELAY_MIN = float(os.getenv("REQUEST_DELAY_MIN", "1.0"))
    REQUEST_DELAY_MAX = float(os.getenv("REQUEST_DELAY_MAX", "3.0"))
    RATE_LIMIT_WAIT = int(os.getenv("RATE_LIMIT_WAIT", "600"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

    # ── Cache ─────────────────────────────────────────────────────────
    CACHE_DIR = Path(os.getenv("CACHE_DIR", "./cache"))
    CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "7"))

    # ── Output ────────────────────────────────────────────────────────
    OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./output"))
    LOGS_DIR = Path(os.getenv("LOGS_DIR", "./logs"))
    PROGRESS_FILE = Path(os.getenv("PROGRESS_FILE", "./progress.json"))

    # ── Scraping ──────────────────────────────────────────────────────
    MAX_PAGES_PER_MODEL = int(os.getenv("MAX_PAGES_PER_MODEL", "50"))

    # ── Validation ────────────────────────────────────────────────────
    MIN_CONSUMPTION = 0.1
    MAX_CONSUMPTION = 60.0  # kWh/100km for EVs can be high
    LOW_CONFIDENCE_THRESHOLD = 5

    # ── Fuel-type mapping (Spritmonitor → normalised) ─────────────────
    FUEL_TYPE_MAP = {
        "super": "petrol",
        "super plus": "petrol",
        "super e10": "petrol",
        "super 95": "petrol",
        "super 98": "petrol",
        "regular": "petrol",
        "petrol": "petrol",
        "benzin": "petrol",
        "gasoline": "petrol",
        "premium": "petrol",
        "diesel": "diesel",
        "biodiesel": "diesel",
        "lpg": "lpg",
        "autogas": "lpg",
        "cng": "cng",
        "natural gas": "cng",
        "erdgas": "cng",
        "electric": "electric",
        "elektro": "electric",
        "electricity": "electric",
        "hybrid": "hybrid",
        "plug-in hybrid": "hybrid",
        "hydrogen": "hydrogen",
        "e85": "petrol",
        "two-stroke mix": "petrol",
    }

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)