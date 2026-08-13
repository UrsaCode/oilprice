"""Data-source fetchers.

Local pump-price scrapers register themselves in LOCAL_SCRAPERS keyed by
ISO country code; the pipeline runs every registered scraper each cycle.
To support a new country, add a module with a `fetch()` function returning
a list of models.LocalPrice and register it here.
"""

from . import pakistan

# country code -> callable returning list[LocalPrice]
LOCAL_SCRAPERS = {
    "PK": pakistan.fetch,
}
