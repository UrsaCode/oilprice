"""United States retail fuel prices (USD per litre).

The EIA publishes weekly national and regional pump prices at
https://www.eia.gov/petroleum/gasdiesel/ as HTML tables, one per fuel,
captioned "... (dollars per gallon)". We read the national ("U.S.") row of
the gasoline and diesel tables and convert to litres so US rows stay
comparable with every other country in the store.

The EIA API would be tidier but requires an API key; these tables do not.

Note the tables carry "Change from" delta columns to the right of the
price columns, so prices are located by their date header rather than by
position - reading a fixed offset would silently pick up a delta.
"""

import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .. import config
from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

EIA_URL = "https://www.eia.gov/petroleum/gasdiesel/"

# Caption keyword -> our product name, most specific first.
CAPTION_PRODUCTS = (("gasoline", "petrol"), ("diesel", "diesel"))

# Labels used for the national average row.
NATIONAL_LABELS = ("u.s.", "us", "u.s")

# Sanity bounds for USD per gallon.
MIN_PRICE, MAX_PRICE = 0.5, 25.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]


def _is_date(text: str) -> bool:
    """True for EIA's MM/DD/YY column headers."""
    parts = text.strip().split("/")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def _to_float(text: str) -> float | None:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _latest_price_column(rows) -> int | None:
    """Column index of the right-most (most recent) date header."""
    for row in rows:
        indexes = [i for i, c in enumerate(_cells(row)) if _is_date(c)]
        if indexes:
            return max(indexes)
    return None


def _national_price(table) -> float | None:
    rows = table.find_all("tr")
    column = _latest_price_column(rows)
    if column is None:
        return None
    for row in rows:
        cells = _cells(row)
        if not cells or cells[0].strip().lower() not in NATIONAL_LABELS:
            continue
        if column >= len(cells):
            return None
        value = _to_float(cells[column])
        if value is not None and MIN_PRICE <= value <= MAX_PRICE:
            return value
    return None


def _product_for(caption: str) -> str | None:
    for keyword, product in CAPTION_PRODUCTS:
        if keyword in caption:
            return product
    return None


def fetch() -> list[LocalPrice]:
    """Return the latest US national pump prices, in USD per litre."""
    soup = BeautifulSoup(http.get(EIA_URL).text, "html.parser")
    prices: dict[str, LocalPrice] = {}
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption is None:
            continue
        text = caption.get_text(" ", strip=True).lower()
        # Skip the by-state breakdown and anything not priced per gallon.
        if "per gallon" not in text:
            continue
        product = _product_for(text)
        if product is None or product in prices:
            continue
        per_gallon = _national_price(table)
        if per_gallon is None:
            continue
        prices[product] = LocalPrice(
            country_code="US", product=product,
            price=round(per_gallon / config.LITRES_PER_US_GALLON, 6),
            currency="USD", fetched_utc=_now_utc(), source="eia.gov",
        )
    if not prices:
        raise ValueError("No usable US fuel prices found at " + EIA_URL)
    return list(prices.values())
