"""Pakistan local pump prices (PKR per litre).

Official prices are set by OGRA and published by retailers. We scrape the
Pakistan State Oil (PSO) product-price page first and fall back to
hamariweb's petroleum price page. Scrapers are keyword-driven rather than
tied to exact page markup, so minor site redesigns keep working; if a site
changes beyond recognition the fetch fails loudly and the pipeline records
the run as partial instead of storing wrong numbers.

Prices can also be entered manually:  python -m oilprice add-local ...
"""

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

PSO_URL = "https://psopk.com/en/product-and-services/product-prices"
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
    text = text.lower()
    for product, keywords in PRODUCT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return product
    return None


def _extract_price(text: str) -> float | None:
    for match in _PRICE_RE.finditer(text.replace(",", "")):
        value = float(match.group(1))
        if MIN_PRICE <= value <= MAX_PRICE:
            return value
    return None


def _scrape_tables(url: str, source: str) -> list[LocalPrice]:
    """Generic table scraper: rows whose label matches a product keyword."""
    soup = BeautifulSoup(http.get(url).text, "html.parser")
    found: dict[str, LocalPrice] = {}
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        product = _classify(cells[0])
        if not product or product in found:
            continue
        # First plausible number in the remaining cells is the price.
        for cell in cells[1:]:
            price = _extract_price(cell)
            if price is not None:
                found[product] = LocalPrice(
                    country_code="PK", product=product, price=price,
                    currency="PKR", fetched_utc=_now_utc(), source=source,
                )
                break
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
