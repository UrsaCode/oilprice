"""Small HTTP helper with retries, shared by all fetchers."""

import logging
import time

import requests

from .. import config

log = logging.getLogger(__name__)


def get(url: str, **kwargs) -> requests.Response:
    """GET with a browser-ish User-Agent and exponential-backoff retries."""
    headers = {"User-Agent": config.USER_AGENT, **kwargs.pop("headers", {})}
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
