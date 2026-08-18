"""Data-source fetchers.

Local pump-price scrapers register themselves in LOCAL_SCRAPERS keyed by
ISO country code; the pipeline runs every registered scraper each cycle.
To support a new country, add a module with a `fetch()` function returning
a list of models.LocalPrice and register it here.

A scraper may cover more than one country: the key is used for logging and
failure reporting, while each LocalPrice carries its own country_code. The
EU entry uses this to return rows for every member state from the single
Weekly Oil Bulletin file.
"""

from . import brazil, egypt, eu, mexico, pakistan, uk, usa

# country code -> callable returning list[LocalPrice]
LOCAL_SCRAPERS = {
    "PK": pakistan.fetch,
    "US": usa.fetch,
    "GB": uk.fetch,
    "EU": eu.fetch,
    "BR": brazil.fetch,
    "EG": egypt.fetch,
    "MX": mexico.fetch,
}
