"""SQLite storage.

All timestamps are stored in UTC (ISO-8601). Every row belongs to a
collection *run*, identified by local date + slot (AM/PM), so running the
pipeline twice within the same slot never duplicates data.
"""

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,          -- e.g. 2026-08-13-AM
    local_date  TEXT NOT NULL,             -- date in LOCAL_TZ
    slot        TEXT NOT NULL,             -- AM or PM
    started_utc TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running'  -- running/ok/partial/failed
);

-- International crude benchmarks in USD.
CREATE TABLE IF NOT EXISTS international_prices (
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    fetched_utc  TEXT NOT NULL,
    benchmark    TEXT NOT NULL,            -- BRENT / WTI
    price_usd    REAL NOT NULL,            -- USD per barrel
    source       TEXT NOT NULL,
    PRIMARY KEY (run_id, benchmark)
);

-- USD -> currency exchange rates captured at collection time.
CREATE TABLE IF NOT EXISTS fx_rates (
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    fetched_utc TEXT NOT NULL,
    currency    TEXT NOT NULL,
    usd_rate    REAL NOT NULL,             -- 1 USD = usd_rate <currency>
    source      TEXT NOT NULL,
    PRIMARY KEY (run_id, currency)
);

-- International benchmark expressed in each country's local currency.
-- Derived data: benchmark USD price x FX rate (NOT the pump price).
CREATE TABLE IF NOT EXISTS benchmark_local (
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    country_code     TEXT NOT NULL,
    country_name     TEXT NOT NULL,
    currency         TEXT NOT NULL,
    benchmark        TEXT NOT NULL,
    price_per_barrel REAL NOT NULL,
    price_per_litre  REAL NOT NULL,
    PRIMARY KEY (run_id, country_code, benchmark)
);

-- Actual local market (pump) prices, scraped or manually entered.
CREATE TABLE IF NOT EXISTS local_prices (
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    fetched_utc  TEXT NOT NULL,
    country_code TEXT NOT NULL,
    product      TEXT NOT NULL,            -- petrol / diesel / kerosene ...
    price        REAL NOT NULL,
    currency     TEXT NOT NULL,
    unit         TEXT NOT NULL DEFAULT 'litre',
    source       TEXT NOT NULL,
    PRIMARY KEY (run_id, country_code, product)
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def start_run(conn, run_id, local_date, slot, started_utc) -> bool:
    """Register a run. Returns False if this run already completed."""
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row and row["status"] == "ok":
        return False
    conn.execute(
        "INSERT INTO runs (run_id, local_date, slot, started_utc) VALUES (?,?,?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET started_utc=excluded.started_utc, "
        "status='running'",
        (run_id, local_date, slot, started_utc),
    )
    conn.commit()
    return True


def finish_run(conn, run_id, status):
    conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
    conn.commit()


def save_international(conn, run_id, quotes):
    conn.executemany(
        "INSERT OR REPLACE INTO international_prices "
        "(run_id, fetched_utc, benchmark, price_usd, source) VALUES (?,?,?,?,?)",
        [(run_id, q.fetched_utc, q.benchmark, q.price_usd, q.source) for q in quotes],
    )
    conn.commit()


def save_fx(conn, run_id, fetched_utc, rates, source):
    conn.executemany(
        "INSERT OR REPLACE INTO fx_rates "
        "(run_id, fetched_utc, currency, usd_rate, source) VALUES (?,?,?,?,?)",
        [(run_id, fetched_utc, cur, rate, source) for cur, rate in rates.items()],
    )
    conn.commit()


def save_benchmark_local(conn, run_id, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO benchmark_local "
        "(run_id, country_code, country_name, currency, benchmark, "
        " price_per_barrel, price_per_litre) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


def save_local_prices(conn, run_id, prices):
    conn.executemany(
        "INSERT OR REPLACE INTO local_prices "
        "(run_id, fetched_utc, country_code, product, price, currency, unit, source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (run_id, p.fetched_utc, p.country_code, p.product, p.price,
             p.currency, p.unit, p.source)
            for p in prices
        ],
    )
    conn.commit()
