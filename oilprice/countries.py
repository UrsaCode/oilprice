"""Static country -> currency reference data."""

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "countries.json"


@lru_cache(maxsize=1)
def load_countries() -> dict:
    """Return {iso2: {"name": ..., "currency": ...}} for all countries."""
    with open(_DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def currency_for(country_code: str) -> str | None:
    info = load_countries().get(country_code.upper())
    return info["currency"] if info else None
