"""United Kingdom retail fuel prices (GBP per litre).

The Department for Energy Security and Net Zero publishes weekly road fuel
prices at gov.uk. The statistics page links a CSV of the full history in
pence per litre; we take its last usable row and convert to pounds.

The CSV asset URL carries a version hash that changes on each weekly
release, so the link is discovered from the statistics page rather than
hardcoded.
"""

import csv
import io
import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

UK_PAGE_URL = "https://www.gov.uk/government/statistics/weekly-road-fuel-prices"

# Header keywords identifying each pump-price column.
PETROL_KEYWORDS = ("ulsp", "unleaded", "petrol")
DIESEL_KEYWORDS = ("ulsd", "diesel")

# Sanity bounds for pence per litre.
MIN_PENCE, MAX_PENCE = 50.0, 500.0

PENCE_PER_POUND = 100.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _csv_link(page_html: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.lower().split("?")[0].endswith(".csv"):
            if href.startswith("/"):
                return "https://www.gov.uk" + href
            return href
    return None


def _price_columns(header: list[str]) -> dict[str, int]:
    """Map product -> column index, using only the pump-price columns."""
    columns: dict[str, int] = {}
    for index, name in enumerate(header):
        lowered = name.lower()
        # Duty and VAT columns share the ULSP/ULSD prefixes; only the
        # pump-price columns carry a price.
        if "pump price" not in lowered:
            continue
        if any(k in lowered for k in PETROL_KEYWORDS):
            columns.setdefault("petrol", index)
        elif any(k in lowered for k in DIESEL_KEYWORDS):
            columns.setdefault("diesel", index)
    return columns


def _parse_csv(text: str) -> list[LocalPrice]:
    if text and text[0] == chr(0xFEFF):
        text = text[1:]
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    if len(rows) < 2:
        raise ValueError("UK fuel price CSV has no data rows")
    columns = _price_columns(rows[0])
    if not columns:
        raise ValueError("UK fuel price CSV has no recognisable price columns")

    # Rows are oldest first; the last one with usable numbers is current.
    for row in reversed(rows[1:]):
        found = []
        for product, index in columns.items():
            if index >= len(row):
                continue
            try:
                pence = float(row[index])
            except ValueError:
                continue
            if not MIN_PENCE <= pence <= MAX_PENCE:
                continue
            found.append(LocalPrice(
                country_code="GB", product=product,
                price=round(pence / PENCE_PER_POUND, 6),
                currency="GBP", fetched_utc=_now_utc(), source="gov.uk",
            ))
        if found:
            return found
    raise ValueError("UK fuel price CSV had no usable price rows")


def fetch() -> list[LocalPrice]:
    """Return the latest UK pump prices, in GBP per litre."""
    link = _csv_link(http.get(UK_PAGE_URL).text)
    if link is None:
        raise ValueError("No CSV link found on " + UK_PAGE_URL)
    return _parse_csv(http.get(link).text)
