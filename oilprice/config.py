"""Configuration, overridable through environment variables."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Repository root (parent of the package directory).
ROOT = Path(__file__).resolve().parent.parent

# Where all collected data lives.
DATA_DIR = Path(os.environ.get("OILPRICE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("OILPRICE_DB_PATH", DATA_DIR / "oilprice.db"))
CSV_DIR = DATA_DIR / "csv"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# Timezone used to decide the AM/PM collection slot (default: Pakistan).
LOCAL_TZ = ZoneInfo(os.environ.get("OILPRICE_TZ", "Asia/Karachi"))

# Local times (hour, 24h) at which the built-in scheduler runs.
SCHEDULE_HOURS = tuple(
    int(h) for h in os.environ.get("OILPRICE_SCHEDULE_HOURS", "9,21").split(",")
)

# HTTP behaviour.
HTTP_TIMEOUT = int(os.environ.get("OILPRICE_HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.environ.get("OILPRICE_HTTP_RETRIES", "3"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# One oil barrel in litres (used to derive per-litre benchmark prices).
LITRES_PER_BARREL = 158.987

# One US liquid gallon in litres (EIA publishes US prices per gallon).
LITRES_PER_US_GALLON = 3.785411784
