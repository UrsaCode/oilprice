# oilprice

Twice-daily collection and storage of oil prices:

1. **International benchmarks** — Brent and WTI crude in USD per barrel.
   Sources tried in order, no API keys needed: Yahoo Finance, then FRED
   (official St. Louis Fed spot series — reliable from datacenter/CI IPs
   where Yahoo often blocks, lags a day or two), then the EIA spot series
   republished on GitHub.
2. **Worldwide local-currency view** — the international benchmark expressed
   in **every country's currency** (~190 countries), per barrel and per
   litre, using free USD exchange rates captured at the same moment
   (open.er-api.com, frankfurter.app fallback).
3. **Local pump prices** — actual market prices per litre. Built-in
   scrapers cover roughly 33 countries:

   | Source | Countries | Products | Currency |
   |---|---|---|---|
   | PSO, hamariweb | Pakistan | petrol, diesel, kerosene, light diesel | PKR |
   | EIA | United States | petrol, diesel | USD |
   | gov.uk (DESNZ) | United Kingdom | petrol, diesel | GBP |
   | EC Weekly Oil Bulletin | 27 EU member states | petrol, diesel, heating oil, LPG | EUR |
   | ANP | Brazil | petrol, premium, diesel, diesel S10, ethanol | BRL |
   | Ministry of Petroleum | Egypt | petrol 80/92/95, diesel, kerosene | EGP |
   | CRE open data | Mexico | petrol, premium, diesel | MXN |

   The Commission publishes the bulletin in euro for *every* member state,
   including those outside the eurozone, so those rows are stored as EUR
   rather than the national currency. US prices are published per gallon
   and converted to litres on the way in. Mexico is the one derived figure:
   the regulator publishes only per-station prices, so the stored national
   price is the median across reporting stations and its `source` says so.
   Countries without a scraper can be entered manually.

Everything is *stored only* (as requested) — no UI, no analysis. Three
formats are written on every run so the data is easy to consume later:

| Store | Path | Purpose |
|---|---|---|
| SQLite | `data/oilprice.db` | queryable history, primary store |
| CSV | `data/csv/*.csv` | append-only, diff-friendly history |
| JSON | `data/snapshots/<run>.json` | complete snapshot of each run |

## How "twice daily" works

Each collection belongs to a **run slot**: local date + `AM`/`PM`
(timezone defaults to `Asia/Karachi`). Re-running inside the same slot is
skipped (or overwrites with `--force`) — never duplicated. So any of the
schedulers below can fire as often as you like; you still get exactly two
clean data points per day.

## Quick start

```bash
pip install -r requirements.txt

python -m oilprice run              # collect + store once, right now
python -m oilprice show             # latest stored prices
python -m oilprice show --country PK  # + benchmark in PKR
```

## Scheduling options (pick one)

**A. GitHub Actions (recommended, no server needed).**
Already configured in `.github/workflows/collect.yml`: runs at 09:00 and
21:00 PKT, collects, and commits the new data back to this repository.
It activates once this branch's workflow file is on the default branch.

**B. Built-in scheduler** (keeps a process running):

```bash
python -m oilprice schedule --immediately
```

Runs at 09:00 and 21:00 local time by default.

**C. cron** on any Linux box:

```cron
0 9,21 * * * cd /path/to/oilprice && /usr/bin/python3 -m oilprice run >> data/cron.log 2>&1
```

## Manual entry for any country

Free, reliable pump-price APIs covering *all* countries don't exist
(commercial ones like GlobalPetrolPrices are paid). Until a scraper exists
for a country, record prices manually — they go into the same store:

```bash
python -m oilprice add-local --country AE --product petrol --price 3.02
# currency is inferred from the country (AED); override with --currency
```

## Adding a scraper for another country

1. Create `oilprice/fetchers/<country>.py` with a `fetch()` returning a
   list of `models.LocalPrice`.
2. Register it in `oilprice/fetchers/__init__.py` → `LOCAL_SCRAPERS`.

The pipeline runs every registered scraper each cycle; one country failing
only marks the run `partial`, it never blocks the rest.

A scraper may cover several countries at once — the registry key is used
only for logging, while each `LocalPrice` carries its own `country_code`.
The EU bulletin fetcher uses this to return every member state from one
file.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `OILPRICE_TZ` | `Asia/Karachi` | timezone for slots & scheduler |
| `OILPRICE_SCHEDULE_HOURS` | `9,21` | built-in scheduler hours |
| `OILPRICE_DATA_DIR` | `./data` | where all data is written |
| `OILPRICE_HTTP_TIMEOUT` / `OILPRICE_HTTP_RETRIES` | `30` / `3` | HTTP behaviour |

## Database schema

- `runs` — one row per slot: `run_id` (`2026-08-13-AM`), status
  (`ok` / `partial` / `failed`).
- `international_prices` — Brent/WTI in USD per barrel, per run.
- `fx_rates` — 1 USD → currency rate for ~160 currencies, per run.
- `benchmark_local` — **derived**: benchmark × FX per country, per barrel
  and per litre. This is the "oil price in local currency" for every
  country; it is *not* the pump price.
- `local_prices` — actual pump prices (scraped or manual), local currency.

Example queries:

```sql
-- Petrol price history in Pakistan
SELECT run_id, price FROM local_prices
WHERE country_code='PK' AND product='petrol' ORDER BY run_id;

-- Brent in PKR per litre over time
SELECT run_id, price_per_litre FROM benchmark_local
WHERE country_code='PK' AND benchmark='BRENT' ORDER BY run_id;
```

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Tests run fully offline (network fetchers are mocked).
