"""Small HTTP helper with retries, shared by all fetchers."""

import logging
import time

import requests

from .. import config

log = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "text/csv,application/json;q=0.8,*/*;q=0.7"),
    "Accept-Language": "en-US,en;q=0.9",
}


def get(url: str, **kwargs) -> requests.Response:
    """GET with browser-like headers and exponential-backoff retries."""
    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    last_exc = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            resp = requests.get(
                url, headers=headers, timeout=config.HTTP_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** attempt
            log.warning("GET %s failed (%s), retry in %ss", url, exc, wait)
            time.sleep(wait)
    raise last_exc
