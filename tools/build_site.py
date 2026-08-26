"""Reduce the collected record to the one file the published page reads.

The page is static and has no server, so everything it draws has to arrive as
one document. This walks the SQLite store and writes ``docs/summary.json``.

WHY A GENERATED FILE AND NOT THE DATABASE ITSELF. data/oilprice.db is the
primary store and it grows with every run; shipping it to a browser to draw
thirty-five bars would send megabytes to read kilobytes. The summary is a
projection, it is never a source, and it is not committed — the workflow that
publishes the page builds it from the database at deploy time, so the page
cannot drift from the record the way a checked-in extract would.

EVERY FIGURE HERE IS EITHER PUBLISHED OR DECLARED. Pump prices are exactly what
the scrapers stored. The USD column is the stored price divided by the exchange
rate captured in the same run, which is the only conversion this file performs
and the reason the rate travels beside it. The crude-per-litre reference is the
benchmark divided by litres per barrel, and it is one number for the whole
world rather than one per country: a barrel of Brent costs the same in London
and in Lahore, and the distance between that and the pump is the whole point of
the chart it is drawn on.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "oilprice.db"
DEFAULT_OUT = ROOT / "docs" / "summary.json"

LITRES_PER_BARREL = 158.987

# The four the page offers as a headline. The store holds nine more — ethanol,
# heating oil, LPG, the Egyptian octane grades — and they stay in the coverage
# table rather than the chart, because a bar chart comparing ethanol in Brazil
# with LPG in Poland would be drawing a comparison nobody can use.
HEADLINE = ("petrol", "diesel")

# How many runs of history the page draws. The store keeps every run; the page
# is a glance, and a chart of six months at two points a day is a smear.
HISTORY_RUNS = 120


def _rows(connection: sqlite3.Connection, sql: str, *args) -> list[sqlite3.Row]:
    return list(connection.execute(sql, args))


def _latest_run(connection: sqlite3.Connection) -> str | None:
    """The newest run that actually collected something.

    A ``failed`` run stored nothing, and a page headed by one would report an
    empty world rather than the last known state of a real one.
    """
    row = connection.execute(
        "SELECT run_id FROM runs WHERE status IN ('ok', 'partial') "
        "ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def _country_names(connection: sqlite3.Connection) -> dict[str, str]:
    """Code to name, from the derived table that already carries both.

    benchmark_local is written for every country on every run, so it names
    countries this project has no pump price for as well — which is what the
    coverage section needs to say how far the scrapers reach.
    """
    return {
        row["country_code"]: row["country_name"]
        for row in _rows(
            connection,
            "SELECT DISTINCT country_code, country_name FROM benchmark_local",
        )
    }


def _rates(connection: sqlite3.Connection, run_id: str) -> dict[str, float]:
    return {
        row["currency"]: row["usd_rate"]
        for row in _rows(
            connection,
            "SELECT currency, usd_rate FROM fx_rates WHERE run_id = ?",
            run_id,
        )
    }


def _in_usd(price: float, currency: str, rates: dict[str, float]) -> float | None:
    """The stored price at the rate captured in the same run, or nothing.

    A missing rate returns None rather than a guess. The page then draws no bar
    for that country and says why, which is the same rule the pipeline follows
    when a source is down: a gap stays a gap.
    """
    rate = rates.get(currency)
    if not rate or rate <= 0:
        return None
    return price / rate


def latest(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    """One entry per country that reported a headline price in this run."""
    names = _country_names(connection)
    rates = _rates(connection, run_id)

    countries: dict[str, dict] = {}
    for row in _rows(
        connection,
        "SELECT country_code, product, price, currency, source FROM local_prices "
        "WHERE run_id = ? AND unit = 'litre' ORDER BY country_code, product",
        run_id,
    ):
        code = row["country_code"]
        entry = countries.setdefault(
            code,
            {
                "code": code,
                "name": names.get(code, code),
                "currency": row["currency"],
                "source": row["source"],
                "rate": rates.get(row["currency"]),
                "prices": {},
            },
        )
        usd = _in_usd(row["price"], row["currency"], rates)
        entry["prices"][row["product"]] = {
            "local": round(row["price"], 4),
            "usd": None if usd is None else round(usd, 4),
        }

    return sorted(countries.values(), key=lambda one: one["name"])


def history(connection: sqlite3.Connection) -> dict:
    """The benchmark, and each country's headline products, run by run.

    Keyed by run so the page can draw any of them against one x axis without
    having to reconcile three different lists of dates.
    """
    runs = [
        row["run_id"]
        for row in _rows(
            connection,
            "SELECT run_id FROM runs WHERE status IN ('ok', 'partial') "
            "ORDER BY run_id DESC LIMIT ?",
            HISTORY_RUNS,
        )
    ][::-1]
    if not runs:
        return {"runs": [], "benchmarks": {}, "countries": {}}

    marks = ",".join("?" * len(runs))
    index = {run: position for position, run in enumerate(runs)}

    benchmarks: dict[str, list] = {}
    for row in _rows(
        connection,
        f"SELECT run_id, benchmark, price_usd FROM international_prices "
        f"WHERE run_id IN ({marks})",
        *runs,
    ):
        series = benchmarks.setdefault(row["benchmark"], [None] * len(runs))
        series[index[row["run_id"]]] = round(row["price_usd"], 2)

    # USD per litre, so a country's line can be read against any other's. The
    # rate is the one captured in that same run, never today's applied
    # backwards — a price and its rate belong to the same moment or the series
    # says something that was never true on any day.
    by_run_rates = {run: _rates(connection, run) for run in runs}
    countries: dict[str, dict[str, list]] = {}
    for row in _rows(
        connection,
        f"SELECT run_id, country_code, product, price, currency FROM local_prices "
        f"WHERE run_id IN ({marks}) AND unit = 'litre' "
        f"AND product IN ({','.join('?' * len(HEADLINE))})",
        *runs,
        *HEADLINE,
    ):
        usd = _in_usd(row["price"], row["currency"], by_run_rates[row["run_id"]])
        if usd is None:
            continue
        products = countries.setdefault(row["country_code"], {})
        series = products.setdefault(row["product"], [None] * len(runs))
        series[index[row["run_id"]]] = round(usd, 4)

    return {"runs": runs, "benchmarks": benchmarks, "countries": countries}


def build(db_path: Path, out_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    run_id = _latest_run(connection)
    if run_id is None:
        raise SystemExit(f"{db_path} holds no run that collected anything")

    run = connection.execute(
        "SELECT run_id, status, started_utc FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()

    benchmarks = {
        row["benchmark"]: round(row["price_usd"], 2)
        for row in _rows(
            connection,
            "SELECT benchmark, price_usd FROM international_prices WHERE run_id = ?",
            run_id,
        )
    }

    tally = {
        row["status"]: row["n"]
        for row in _rows(
            connection, "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
        )
    }

    countries = latest(connection, run_id)
    products = [
        row["product"]
        for row in _rows(
            connection,
            "SELECT DISTINCT product FROM local_prices ORDER BY product",
        )
    ]
    sources = [
        row["source"]
        for row in _rows(
            connection,
            "SELECT DISTINCT source FROM local_prices WHERE run_id = ? "
            "ORDER BY source",
            run_id,
        )
    ]

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": {
            "id": run["run_id"],
            "status": run["status"],
            "started_utc": run["started_utc"],
        },
        "litres_per_barrel": LITRES_PER_BARREL,
        "benchmarks_usd_per_barrel": benchmarks,
        "crude_usd_per_litre": {
            name: round(price / LITRES_PER_BARREL, 4)
            for name, price in benchmarks.items()
        },
        "headline_products": list(HEADLINE),
        "countries": countries,
        "history": history(connection),
        "totals": {
            "runs": sum(tally.values()),
            "by_status": tally,
            "countries_priced": len(countries),
            "products": products,
            "sources": sources,
            "first_run": connection.execute(
                "SELECT MIN(run_id) FROM runs"
            ).fetchone()[0],
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    arguments = parser.parse_args()

    payload = build(arguments.db, arguments.out)
    print(
        f"{arguments.out}: run {payload['run']['id']} ({payload['run']['status']}), "
        f"{len(payload['countries'])} countries, "
        f"{len(payload['history']['runs'])} runs of history"
    )


if __name__ == "__main__":
    main()
