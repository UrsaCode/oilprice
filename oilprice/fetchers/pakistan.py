"""Pakistan local pump prices (PKR per litre).

Official prices are set by OGRA and published by retailers. We scrape the
Pakistan State Oil (PSO) fuel-price page first and fall back to hamariweb's
petroleum price page. Scrapers are keyword-driven rather than tied to exact
page markup, and handle both table orientations in use — PSO lists one
product per row, hamariweb puts products in the header with one row per
date. If a site changes beyond recognition the fetch fails loudly and the
pipeline records the run as partial instead of storing wrong numbers.

OGRA publishes the authoritative notifications at
https://www.ogra.org.pk/notified-petroleum-prices, but only as linked PDFs,
so it is not scraped here.

Prices can also be entered manually:  python -m oilprice add-local ...
"""

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

PSO_URL = "https://psopk.com/en/fuels/fuel-prices"
HAMARIWEB_URL = "https://hamariweb.com/finance/petroleum_prices/"

# Keywords (lowercase) that identify each product in scraped text.
PRODUCT_KEYWORDS = {
    "petrol": ("premier euro", "super", "petrol", "motor gasoline", "pmg"),
    "diesel": ("hi-cetane", "high speed diesel", "hsd", "diesel"),
    "kerosene": ("kerosene", "sko"),
    "light_diesel": ("light diesel", "ldo"),
}

# Sanity bounds for PKR/litre — reject obviously wrong parses.
MIN_PRICE, MAX_PRICE = 50.0, 2000.0

_PRICE_RE = re.compile(r"(\d{2,4}(?:[.,]\d{1,2})?)")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _classify(text: str) -> str | None:
    """Map a label to a product, preferring the most specific match.

    Labels overlap ("Light Diesel" contains "diesel"), so the longest
    matching keyword wins rather than whichever product is checked first.
    """
    text = text.lower()
    best_product, best_len = None, 0
    for product, keywords in PRODUCT_KEYWORDS.items():
        for kw in keywords:
            if kw in text and len(kw) > best_len:
                best_product, best_len = product, len(kw)
    return best_product


def _extract_price(text: str) -> float | None:
    for match in _PRICE_RE.finditer(text.replace(",", "")):
        value = float(match.group(1))
        if MIN_PRICE <= value <= MAX_PRICE:
            return value
    return None


def _cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]


def _price(product: str, value: float, source: str) -> LocalPrice:
    return LocalPrice(
        country_code="PK", product=product, price=value,
        currency="PKR", fetched_utc=_now_utc(), source=source,
    )


def _parse_row_oriented(soup, source: str) -> dict[str, LocalPrice]:
    """One product per row: label in the first cell, price alongside it."""
    found: dict[str, LocalPrice] = {}
    for row in soup.find_all("tr"):
        cells = _cells(row)
        if len(cells) < 2:
            continue
        product = _classify(cells[0])
        if not product or product in found:
            continue
        # First plausible number in the remaining cells is the price.
        for cell in cells[1:]:
            price = _extract_price(cell)
            if price is not None:
                found[product] = _price(product, price, source)
                break
    return found


def _parse_column_oriented(soup, source: str) -> dict[str, LocalPrice]:
    """One product per column: products in the header, one row per date.

    Prices are read by column index from the first data row, so a leading
    date column is never mistaken for a price.
    """
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for header_index, header in enumerate(rows):
            columns = {
                i: product
                for i, cell in enumerate(_cells(header))
                if (product := _classify(cell))
            }
            if len(columns) < 2:
                continue
            for data_row in rows[header_index + 1:]:
                cells = _cells(data_row)
                found: dict[str, LocalPrice] = {}
                for i, product in columns.items():
                    if i >= len(cells) or product in found:
                        continue
                    price = _extract_price(cells[i])
                    if price is not None:
                        found[product] = _price(product, price, source)
                if found:
                    return found
            break  # header found but no usable data row; try next table
    return {}


def _scrape_tables(url: str, source: str) -> list[LocalPrice]:
    """Scrape pump prices, accepting either table orientation."""
    soup = BeautifulSoup(http.get(url).text, "html.parser")
    found = _parse_row_oriented(soup, source)
    if "petrol" not in found and "diesel" not in found:
        found = _parse_column_oriented(soup, source) or found
    if "petrol" not in found and "diesel" not in found:
        raise ValueError(f"No recognisable fuel prices found at {url}")
    return list(found.values())


def fetch() -> list[LocalPrice]:
    """Return current Pakistani pump prices, trying each source in order."""
    for url, source in ((PSO_URL, "psopk.com"), (HAMARIWEB_URL, "hamariweb.com")):
        try:
            return _scrape_tables(url, source)
        except Exception as exc:
            log.warning("Pakistan scrape from %s failed: %s", source, exc)
    raise RuntimeError("All Pakistan pump-price sources failed")
