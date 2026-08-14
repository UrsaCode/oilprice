"""International crude benchmark prices (Brent, WTI) in USD per barrel.

Sources are tried in order until one works (none needs an API key):
  1. Stooq live quotes        - real-time futures, but blocks some cloud IPs
  2. Yahoo Finance chart API  - real-time, but rate-limits datacenter IPs
  3. FRED (St. Louis Fed)     - official spot series, datacenter-friendly,
                                lags up to a couple of business days

GitHub Actions runners are typically served by FRED; running locally you
will usually get the real-time Stooq/Yahoo quotes.
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

# FRED daily spot price series (Europe Brent / WTI Cushing), CSV download.
FRED_SERIES = {"DCOILBRENTEU": "BRENT", "DCOILWTICO": "WTI"}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


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


def _from_fred() -> list[BenchmarkQuote]:
    quotes = []
    for series, name in FRED_SERIES.items():
        try:
            resp = http.get(FRED_URL.format(series=series))
            # Last row with a numeric value ("." marks missing days).
            for row in reversed(list(csv.reader(io.StringIO(resp.text)))):
                if len(row) != 2:
                    continue
                try:
                    price = float(row[1])
                except ValueError:
                    continue
                quotes.append(BenchmarkQuote(
                    name, price, _now_utc(),
                    f"fred.stlouisfed.org ({row[0]})",
                ))
                break
        except Exception as exc:
            log.warning("FRED fetch for %s failed: %s", series, exc)
    if not quotes:
        raise ValueError("FRED returned no usable quotes")
    return quotes


def fetch() -> list[BenchmarkQuote]:
    """Return Brent/WTI quotes, trying each source in order."""
    for fetcher in (_from_stooq, _from_yahoo, _from_fred):
        try:
            return fetcher()
        except Exception as exc:
            log.warning("%s failed: %s", fetcher.__name__, exc)
    raise RuntimeError("All international price sources failed")
