"""Mexican retail fuel prices (MXN per litre).

The regulator publishes an open XML feed of prices at every individual
service station, not a national figure. The national price stored here is
therefore *derived*: the median across all reporting stations, computed in
this module. Every other country in the store records a published price, so
the source string says so explicitly.

The median is used rather than the mean because the feed is self-reported
per station and carries occasional absurd values; a single mistyped entry
would visibly drag a mean.
"""

import logging
import statistics
from datetime import datetime, timezone
from xml.etree import ElementTree

from ..models import LocalPrice
from . import http

log = logging.getLogger(__name__)

CRE_URL = "https://publicacionexterna.azurewebsites.net/publicaciones/prices"

# Feed price type -> our product name.
PRODUCTS = {
    "regular": "petrol",
    "premium": "petrol_premium",
    "diesel": "diesel",
}

MIN_PRICE, MAX_PRICE = 5.0, 100.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _collect(data: bytes) -> dict[str, list[float]]:
    # Parse the raw bytes, not decoded text: the feed carries a byte-order
    # mark and declares its own encoding, and the response has no charset
    # for requests to go on, so decoding first mangles the BOM into
    # characters the XML parser rejects at line 1.
    root = ElementTree.fromstring(data)
    samples: dict[str, list[float]] = {}
    for element in root.iter("gas_price"):
        product = PRODUCTS.get((element.get("type") or "").strip().lower())
        if product is None:
            continue
        try:
            value = float((element.text or "").strip())
        except ValueError:
            continue
        if MIN_PRICE <= value <= MAX_PRICE:
            samples.setdefault(product, []).append(value)
    return samples


def fetch() -> list[LocalPrice]:
    """Return national median Mexican pump prices, MXN per litre."""
    samples = _collect(http.get(CRE_URL).content)
    fetched = _now_utc()
    prices = []
    for product, values in samples.items():
        if not values:
            continue
        prices.append(LocalPrice(
            country_code="MX", product=product,
            price=round(statistics.median(values), 6),
            currency="MXN", fetched_utc=fetched,
            source="cre.gob.mx (median of " + str(len(values)) + " stations)",
        ))
    if not prices:
        raise ValueError("No usable Mexican station prices found at " + CRE_URL)
    return prices
