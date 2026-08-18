"""Egyptian retail fuel prices (EGP per litre).

The Ministry of Petroleum publishes current prices as small HTML tables of
Type / Price / Unit.

Only rows priced per litre are read, which excludes bottled gas sold per
cylinder. Note the ministry's own page labels one row "piaster/liter" while
every other fuel row says "LE/liter"; the figure alongside it is plainly in
pounds like the rest (a piaster reading would be a hundredth of the real
pump price), so all per-litre rows are treated as pounds and a sanity bound
rejects anything outside a believable range.
"""

import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

EGYPT_URL = "https://www.petroleum.gov.eg/"

# Exact label (lower-case) -> our product name.
PRODUCTS = {
    "gasoline 80": "petrol_80",
    "gasoline 92": "petrol_92",
    "gasoline 95": "petrol_95",
    "solar": "diesel",
    "diesel": "diesel",
    "kerosene": "kerosene",
}

# Only rows whose unit mentions litres are pump prices.
LITRE_MARKERS = ("liter", "litre")

MIN_PRICE, MAX_PRICE = 1.0, 200.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def fetch() -> list[LocalPrice]:
    """Return current Egyptian pump prices, EGP per litre."""
    soup = BeautifulSoup(http.get(EGYPT_URL).text, "html.parser")
    fetched = _now_utc()
    found: dict[str, LocalPrice] = {}

    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if len(cells) < 3:
            continue
        label, raw_price, unit = cells[0].lower(), cells[1], cells[2].lower()
        product = PRODUCTS.get(label)
        if product is None or product in found:
            continue
        if not any(marker in unit for marker in LITRE_MARKERS):
            continue
        value = _to_float(raw_price)
        if value is None or not MIN_PRICE <= value <= MAX_PRICE:
            continue
        found[product] = LocalPrice(
            country_code="EG", product=product, price=round(value, 6),
            currency="EGP", fetched_utc=fetched, source="petroleum.gov.eg",
        )
    if not found:
        raise ValueError("No usable Egyptian fuel prices found at " + EGYPT_URL)
    return list(found.values())
