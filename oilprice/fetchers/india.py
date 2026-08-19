"""Indian retail fuel prices (INR per litre).

The Petroleum Planning & Analysis Cell, the statistics body of the Ministry
of Petroleum & Natural Gas, publishes daily petrol and diesel prices for the
four metro cities. The figures are only released as a PDF, so the file is
located from the price page and its first page is read as text.

The PDF holds two side-by-side tables - petrol on the left, diesel on the
right, both in Rs./Litre - each with Delhi, Mumbai, Chennai and Kolkata
columns and one row per date, newest first. Delhi is the conventional
reference city for Indian fuel prices and is what gets stored; the source
string records that choice.

Reachability note: ppac.gov.in refuses connections from some networks. When
it cannot be reached the run is simply marked partial for India, as with
any other blocked source.
"""

import io
import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

PPAC_PAGE_URL = (
    "https://ppac.gov.in/retail-selling-price-rsp-of-petrol-diesel-and-"
    "domestic-lpg/rsp-of-petrol-and-diesel-in-metro-cities-since-16-6-2017"
)
PPAC_BASE = "https://ppac.gov.in"

# The daily metro price file: MS (motor spirit) and HSD (high speed diesel).
PDF_MARKER = "dailypricemshsd"
CURRENT_LABEL = "current"

# Column order within each of the two tables.
CITIES = ("Delhi", "Mumbai", "Chennai", "Kolkata")
REFERENCE_CITY = "Delhi"

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")

MIN_PRICE, MAX_PRICE = 10.0, 500.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_date(token: str) -> bool:
    """True for PPAC's DD-Mon-YY date tokens."""
    parts = token.split("-")
    if len(parts) != 3:
        return False
    day, month, year = parts
    return day.isdigit() and year.isdigit() and month.lower() in MONTHS


def _pdf_link(page_html: str) -> str | None:
    """Locate the current daily price PDF, preferring the 'Current' link."""
    soup = BeautifulSoup(page_html, "html.parser")
    fallback = None
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if PDF_MARKER not in href.lower():
            continue
        full = PPAC_BASE + href if href.startswith("/") else href
        if anchor.get_text(" ", strip=True).strip().lower() == CURRENT_LABEL:
            return full
        if fallback is None:
            fallback = full
    return fallback


def _parse_page_text(text: str) -> dict[str, float]:
    """Read the newest row: date, four petrol prices, date, four diesel."""
    city_index = CITIES.index(REFERENCE_CITY)
    for line in text.splitlines():
        tokens = line.split()
        # A data row carries a date, four prices, then a second date and
        # four more prices. Requiring the second date keeps header and
        # footer lines out.
        if len(tokens) < 10 or not _is_date(tokens[0]) or not _is_date(tokens[5]):
            continue
        try:
            petrol = float(tokens[1 + city_index])
            diesel = float(tokens[6 + city_index])
        except ValueError:
            continue
        if not (MIN_PRICE <= petrol <= MAX_PRICE
                and MIN_PRICE <= diesel <= MAX_PRICE):
            continue
        return {"petrol": petrol, "diesel": diesel}
    return {}


def _parse_pdf(data: bytes) -> dict[str, float]:
    reader = PdfReader(io.BytesIO(data))
    if not reader.pages:
        raise ValueError("PPAC price PDF has no pages")
    return _parse_page_text(reader.pages[0].extract_text() or "")


def fetch() -> list[LocalPrice]:
    """Return the latest Indian pump prices for Delhi, INR per litre."""
    link = _pdf_link(http.get(PPAC_PAGE_URL).text)
    if link is None:
        raise ValueError("No daily price PDF linked from " + PPAC_PAGE_URL)
    values = _parse_pdf(http.get(link).content)
    if not values:
        raise ValueError("PPAC price PDF had no usable price row")
    fetched = _now_utc()
    return [
        LocalPrice(
            country_code="IN", product=product, price=round(price, 6),
            currency="INR", fetched_utc=fetched,
            source="ppac.gov.in (" + REFERENCE_CITY + ")",
        )
        for product, price in values.items()
    ]
