"""International crude benchmark prices (Brent, WTI) in USD per barrel.

Sources are tried in order until one works (none needs an API key):
  1. Yahoo Finance chart API  - real-time, but rate-limits datacenter IPs
  2. FRED (St. Louis Fed)     - official spot series, lags a couple of
                                business days; also throttles some cloud IPs
  3. datasets/oil-prices      - EIA spot series republished on GitHub
                                (raw.githubusercontent.com), always
                                reachable from CI; lags a few business days

Running locally you will usually get the real-time Yahoo quotes; GitHub
Actions runners typically fall through to FRED or the GitHub dataset.

Stooq was the original first source, but it retired its free CSV quote
endpoint (/q/l/ now 404s for every symbol on both stooq.com and stooq.pl)
and put a JavaScript browser check in front of the quote pages, so it can
no longer be read without a browser engine.
"""

import csv
import io
import logging
from datetime import datetime, timezone

from ..models import BenchmarkQuote
from . import http

log = logging.getLogger(__name__)

YAHOO_SYMBOLS = {"BZ=F": "BRENT", "CL=F": "WTI"}
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# FRED daily spot price series (Europe Brent / WTI Cushing), CSV download.
FRED_SERIES = {"DCOILBRENTEU": "BRENT", "DCOILWTICO": "WTI"}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# EIA spot series republished daily in the datasets/oil-prices GitHub repo.
GITHUB_DATASET_FILES = {"brent-daily.csv": "BRENT", "wti-daily.csv": "WTI"}
GITHUB_DATASET_URL = (
    "https://raw.githubusercontent.com/datasets/oil-prices/{branch}/data/{file}"
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _from_github_dataset() -> list[BenchmarkQuote]:
    quotes = []
    for filename, name in GITHUB_DATASET_FILES.items():
        for branch in ("master", "main"):
            url = GITHUB_DATASET_URL.format(branch=branch, file=filename)
            try:
                resp = http.get(url)
            except Exception as exc:
                log.warning("GitHub dataset fetch %s failed: %s", url, exc)
                continue
            # CSV of "Date,Price" rows; last line is the latest observation.
            for row in reversed(list(csv.reader(io.StringIO(resp.text)))):
                if len(row) != 2:
                    continue
                try:
                    price = float(row[1])
                except ValueError:
                    continue
                quotes.append(BenchmarkQuote(
                    name, price, _now_utc(),
                    f"github.com/datasets/oil-prices ({row[0]})",
                ))
                break
            break
    if not quotes:
        raise ValueError("GitHub dataset returned no usable quotes")
    return quotes


def fetch() -> list[BenchmarkQuote]:
    """Return Brent/WTI quotes, trying each source in order."""
    for fetcher in (_from_yahoo, _from_fred, _from_github_dataset):
        try:
            return fetcher()
        except Exception as exc:
            log.warning("%s failed: %s", fetcher.__name__, exc)
    raise RuntimeError("All international price sources failed")
