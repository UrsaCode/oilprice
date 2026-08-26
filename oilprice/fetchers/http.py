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


def browser_get(url: str, **kwargs):
    """GET from a host that refuses a plain client, retries the same way.

    WHY A SECOND HTTP STACK EXISTS HERE. Some publishers sit behind a filter
    that decides what to serve from the TLS handshake rather than from the
    headers, and answers anything it does not recognise with an interstitial
    page carrying a 200. ``requests`` cannot pass that no matter what
    User-Agent it sends; opec.org is the source in this repository that needs
    it. ``curl_cffi`` performs the handshake the way a browser does, so the
    same public document arrives as the document.

    It is deliberately not the default. ``requests`` is the better-understood
    library and every other source here is happy with it, so this is the
    exception a fetcher opts into and says why in its docstring.
    """
    from curl_cffi import requests as impersonating

    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    headers.pop("User-Agent", None)   # the impersonated profile brings its own

    last_exc = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            resp = impersonating.get(
                url,
                headers=headers,
                timeout=config.HTTP_TIMEOUT,
                impersonate=config.IMPERSONATE,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            log.warning("GET %s failed (%s), retry in %ss", url, exc, wait)
            time.sleep(wait)
    raise last_exc
