"""Bangladeshi retail fuel prices (BDT per litre).

The Bangladesh Petroleum Corporation publishes administered prices for
every petroleum product on one page, in Bengali.

Three details shape the parsing:

  * Prices are written in Bengali-Indic numerals, so digits are transliterated
    to ASCII before being read as numbers.
  * The unit is stated inside the price cell. Only Taka-per-litre rows are
    taken. This matters beyond excluding the per-cylinder LPG and
    per-metric-tonne bunker rows: jet fuel is quoted per litre too, but in
    US dollars, so filtering on "per litre" alone would store a dollar
    figure as though it were Taka.
  * Specialty solvents (MTT, JBO, SBPS) are ignored; only fuels with a
    counterpart elsewhere in the store are kept.
  * Bengali text is normalised before comparison. The same word can be
    encoded two ways - the page writes ya-nukta as one codepoint where a
    literal may carry the base letter plus a combining nukta - and the two
    forms are not equal as raw strings.

BPC serves an incomplete certificate chain - the leaf is valid for
bpc.gov.bd through its subject-alternative names, but the issuing
intermediate is not sent. Browsers fetch the missing certificate
themselves; Python does not. Rather than disable verification, the
intermediate ships in oilprice/data and is appended to the trusted roots
for this request only. It is a long-lived CA certificate (valid to 2036),
not the yearly-rotating leaf.
"""

import logging
import tempfile
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import certifi
from bs4 import BeautifulSoup

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

BPC_URL = "https://bpc.gov.bd/pages/static-pages/6922ddb6933eb65569e15fbc"

_INTERMEDIATE = Path(__file__).parent.parent / "data" / "bpc_gov_bd_intermediate.pem"

# Bengali-Indic digits, in value order.
BENGALI_DIGITS = "০১২৩৪৫৬৭৮৯"
_DIGIT_MAP = {ord(d): str(i) for i, d in enumerate(BENGALI_DIGITS)}

# "Taka/litre" - the only unit we accept. Jet fuel is also per litre but
# priced in US dollars, so the currency word is part of the test.
TAKA_PER_LITRE = "টাকা/লিটার"

def _normalise(text: str) -> str:
    """NFC-normalise so equivalent Bengali spellings compare equal."""
    return unicodedata.normalize("NFC", str(text or "")).strip()


# Bengali product name -> our product name.
_RAW_PRODUCTS = {
    "ডিজেল": "diesel",                 # diesel
    "কেরোসিন": "kerosene",   # kerosene
    "পেট্রোল": "petrol",     # petrol
    "অকটেন": "petrol_premium",         # octane (premium grade)
    "এলডিও": "light_diesel",           # LDO
    "ফার্নেস অয়েল": "furnace_oil",
}

PRODUCTS = {_normalise(name): product for name, product in _RAW_PRODUCTS.items()}

MIN_PRICE, MAX_PRICE = 10.0, 1000.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _ca_bundle() -> str:
    """Trusted roots plus the intermediate BPC omits, as one PEM file."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".pem", prefix="oilprice-bpc-", delete=False, encoding="utf-8")
    with handle as out:
        out.write(Path(certifi.where()).read_text(encoding="utf-8"))
        out.write(chr(10))
        out.write(_INTERMEDIATE.read_text(encoding="utf-8"))
    return handle.name


def _ascii_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def _price_in_taka_per_litre(cell: str) -> float | None:
    """Read a price cell, but only when it is quoted in Taka per litre."""
    if TAKA_PER_LITRE not in _normalise(cell).replace(" ", ""):
        return None
    converted = _ascii_digits(cell).replace(",", "")
    number = ""
    for char in converted:
        if char.isdigit() or char == ".":
            number += char
        elif number:
            break
    try:
        return float(number)
    except ValueError:
        return None


def _parse(html: str) -> list[LocalPrice]:
    soup = BeautifulSoup(html, "html.parser")
    fetched = _now_utc()
    found: dict[str, LocalPrice] = {}

    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        for index, cell in enumerate(cells):
            product = PRODUCTS.get(_normalise(cell))
            if product is None or product in found:
                continue
            for candidate in cells[index + 1:]:
                value = _price_in_taka_per_litre(candidate)
                if value is None or not MIN_PRICE <= value <= MAX_PRICE:
                    continue
                found[product] = LocalPrice(
                    country_code="BD", product=product, price=round(value, 6),
                    currency="BDT", fetched_utc=fetched, source="bpc.gov.bd",
                )
                break
    if not found:
        raise ValueError("No usable Bangladeshi fuel prices found at " + BPC_URL)
    return list(found.values())


def fetch() -> list[LocalPrice]:
    """Return current Bangladeshi administered pump prices, BDT per litre."""
    return _parse(http.get(BPC_URL, verify=_ca_bundle()).text)
