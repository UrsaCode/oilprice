"""USD-base exchange rates for converting benchmark prices to local currencies.

Primary source: open.er-api.com (free, no key, ~160 currencies).
Fallback: frankfurter.app (ECB reference rates, ~30 major currencies).
"""

import logging

from . import http

log = logging.getLogger(__name__)

ER_API_URL = "https://open.er-api.com/v6/latest/USD"
FRANKFURTER_URL = "https://api.frankfurter.app/latest?from=USD"


def _from_er_api() -> tuple[dict, str]:
    data = http.get(ER_API_URL).json()
    if data.get("result") != "success":
        raise ValueError(f"er-api returned {data.get('result')!r}")
    return data["rates"], "open.er-api.com"


def _from_frankfurter() -> tuple[dict, str]:
    data = http.get(FRANKFURTER_URL).json()
    rates = data["rates"]
    rates["USD"] = 1.0
    return rates, "frankfurter.app"


def fetch() -> tuple[dict, str]:
    """Return ({currency: units-per-USD}, source_name)."""
    for fetcher in (_from_er_api, _from_frankfurter):
        try:
            return fetcher()
        except Exception as exc:
            log.warning("%s failed: %s", fetcher.__name__, exc)
    raise RuntimeError("All FX rate sources failed")
