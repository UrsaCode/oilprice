"""Shared data records passed between fetchers, pipeline and storage."""

from dataclasses import dataclass


@dataclass
class BenchmarkQuote:
    benchmark: str        # "BRENT" or "WTI"
    price_usd: float      # USD per barrel
    fetched_utc: str
    source: str


@dataclass
class LocalPrice:
    country_code: str     # ISO-3166 alpha-2, e.g. "PK"
    product: str          # petrol / diesel / kerosene / light_diesel ...
    price: float
    currency: str
    fetched_utc: str
    source: str
    unit: str = "litre"
