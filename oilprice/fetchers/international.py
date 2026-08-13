"""International crude benchmark prices (Brent, WTI) in USD per barrel.

Primary source: Stooq free CSV quotes (no API key).
Fallback: Yahoo Finance chart API (no API key).
"""

import csv
import io
import logging
from datetime import datetime, timezone

from ..models import BenchmarkQuote
from . import http

log = logging.getLogger(__name__)

# Stooq symbols: cb.f = Brent crude futures, cl.f = WTI crude futures.
STOOQ_SYMBOLS = {"cb.f": "BRENT", "cl.f": "WTI"}
STOOQ_URL = "https://stooq.com/q/l/?s=cb.f,cl.f&f=sd2t2ohlcv&h&e=csv"

YAHOO_SYMBOLS = {"BZ=F": "BRENT", "CL=F": "WTI"}
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _from_stooq() -> list[BenchmarkQuote]:
    resp = http.get(STOOQ_URL)
    quotes = []
    for row in csv.DictReader(io.StringIO(resp.text)):
        name = STOOQ_SYMBOLS.get(row.get("Symbol", "").lower())
        close = row.get("Close")
        if name and close and close not in ("N/D", ""):
            quotes.append(
                BenchmarkQuote(name, float(close), _now_utc(), "stooq.com")
            )
    if not quotes:
        raise ValueError("Stooq returned no usable quotes")
    return quotes


def _from_yahoo() -> list[BenchmarkQuote]:
    quotes = []
    for symbol, name in YAHOO_SYMBOLS.items():
        try:
            resp = http.get(YAHOO_URL.format(symbol=symbol))
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            if price:
                quotes.append(
                    BenchmarkQuote(name, float(price), _now_utc(), "finance.yahoo.com")
                )
        except Exception as exc:  # one symbol failing shouldn't kill the other
            log.warning("Yahoo fetch for %s failed: %s", symbol, exc)
    if not quotes:
        raise ValueError("Yahoo returned no usable quotes")
    return quotes


def fetch() -> list[BenchmarkQuote]:
    """Return Brent/WTI quotes, trying each source in order."""
    for fetcher in (_from_stooq, _from_yahoo):
        try:
            return fetcher()
        except Exception as exc:
            log.warning("%s failed: %s", fetcher.__name__, exc)
    raise RuntimeError("All international price sources failed")
