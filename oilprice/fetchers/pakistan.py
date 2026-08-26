"""Pakistan local pump prices (PKR per litre).

Pakistani prices are set by OGRA and notified fortnightly; the notified
figure is the pump price. Three sources are tried in order.

**ek litre (https://eklitre.pk) is the primary source.** It is a public
record of Pakistani fuel prices that keeps every OGRA notification since
2006 together with the archived page each figure was read from, and serves
them as JSON. That makes it the only Pakistani source here that states an
*effective date* alongside the price, so a run knows whether it is storing
today's notification or a fortnight-old one still in force. Its
``/v1/prices`` endpoint carries all four products this fetcher stores.

The two retail pages remain as fallbacks: Pakistan State Oil's fuel-price
page, then hamariweb's petroleum price page. Both are scraped
keyword-driven rather than tied to exact markup, and handle either table
orientation in use — PSO lists one product per row, hamariweb puts
products in the header with one row per date. Neither states an effective
date, so a price from them is dated only by the moment it was fetched.

OGRA publishes the authoritative notifications at
https://www.ogra.org.pk/notified-petroleum-prices, but only as linked PDFs,
which is the work ek litre already does.

Prices can also be entered manually:  python -m oilprice add-local ...
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

EKLITRE_URL = "https://eklitre.pk/v1/prices"
PSO_URL = "https://psopk.com/en/fuels/fuel-prices"
HAMARIWEB_URL = "https://hamariweb.com/finance/petroleum_prices/"

# How far back to ask ek litre for notifications. Prices are notified at
# least monthly, so a window this wide always contains one; asking for the
# whole twenty-year series to read its last row would be wasteful.
EKLITRE_WINDOW_DAYS = 120

# ek litre's own column names for the products stored here.
EKLITRE_PRODUCTS = ("petrol", "diesel", "kerosene", "light_diesel")

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


# --- ek litre -----------------------------------------------------------

def _newest_notification(payload: dict) -> tuple[list[str], list]:
    """The latest-dated row of ``/v1/prices``, with its column names.

    Rows are arrays under a stated ``columns`` header, so the header is
    read rather than assumed: a column added upstream would otherwise
    shift every position and make each product read as its neighbour.
    Rows arrive oldest first, but the newest is picked by date rather than
    by position, so ordering is never something this fetcher relies on.
    """
    columns = payload.get("columns")
    rows = payload.get("notifications")
    if not columns or not rows:
        raise ValueError("ek litre returned no notifications")
    if "effective_date" not in columns:
        raise ValueError("ek litre payload has no effective_date column")

    when = columns.index("effective_date")
    return columns, max(rows, key=lambda row: row[when])


def _from_eklitre() -> list[LocalPrice]:
    """Read the newest OGRA notification ek litre holds.

    The marks ek litre serves beside the series — ``contradicted``,
    ``disputed`` — are deliberately not consulted. They flag readings two
    publishers state differently, and the figure in the row is what was
    notified either way; a run that dropped a marked row would be storing
    a different price from the one at the pump.
    """
    start = date.today() - timedelta(days=EKLITRE_WINDOW_DAYS)
    payload = http.get(
        EKLITRE_URL,
        params={"from": start.isoformat()},
        headers={"Accept": "application/json"},
    ).json()

    columns, row = _newest_notification(payload)
    effective = row[columns.index("effective_date")]
    source = "eklitre.pk (notified " + str(effective) + ")"

    found = []
    for product in EKLITRE_PRODUCTS:
        if product not in columns:
            continue
        value = row[columns.index(product)]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue          # not notified for this date
        if not MIN_PRICE <= float(value) <= MAX_PRICE:
            log.warning("ek litre %s price %s out of bounds", product, value)
            continue
        found.append(_price(product, float(value), source))

    if not any(p.product in ("petrol", "diesel") for p in found):
        raise ValueError(
            "ek litre notification " + str(effective) + " carries no fuel prices"
        )
    return found


# --- retail page scrapers -----------------------------------------------

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
    sources = (
        ("eklitre.pk", _from_eklitre),
        ("psopk.com", lambda: _scrape_tables(PSO_URL, "psopk.com")),
        ("hamariweb.com", lambda: _scrape_tables(HAMARIWEB_URL, "hamariweb.com")),
    )
    for name, read in sources:
        try:
            return read()
        except Exception as exc:
            log.warning("Pakistan fetch from %s failed: %s", name, exc)
    raise RuntimeError("All Pakistan pump-price sources failed")
