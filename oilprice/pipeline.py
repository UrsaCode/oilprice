"""One collection cycle: fetch everything, store to SQLite + CSV + JSON.

A cycle is identified by local date + slot (AM/PM). Running it again in the
same slot overwrites that slot's rows instead of duplicating them.
"""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config, db
from .countries import load_countries
from .fetchers import LOCAL_SCRAPERS, fx, international

log = logging.getLogger(__name__)


def current_slot(now=None) -> tuple[str, str]:
    """Return (local_date, AM|PM) in the configured timezone."""
    now = now or datetime.now(config.LOCAL_TZ)
    return now.date().isoformat(), "AM" if now.hour < 12 else "PM"


def _derive_benchmark_local(run_id, quotes, rates):
    """Express each benchmark in every country's currency, per barrel/litre."""
    rows = []
    for code, info in load_countries().items():
        rate = rates.get(info["currency"])
        if rate is None:
            continue
        for q in quotes:
            per_barrel = q.price_usd * rate
            rows.append((
                run_id, code, info["name"], info["currency"], q.benchmark,
                round(per_barrel, 4),
                round(per_barrel / config.LITRES_PER_BARREL, 4),
            ))
    return rows


def _append_csv(path: Path, header: list[str], rows: list[list]):
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(header)
        writer.writerows(rows)


def _export(run_id, fetched_utc, quotes, rates, fx_source, local_prices,
            benchmark_rows):
    """Write diff-friendly CSV history plus a full JSON snapshot per run."""
    _append_csv(
        config.CSV_DIR / "international.csv",
        ["run_id", "fetched_utc", "benchmark", "price_usd_per_barrel", "source"],
        [[run_id, q.fetched_utc, q.benchmark, q.price_usd, q.source] for q in quotes],
    )
    _append_csv(
        config.CSV_DIR / "local_prices.csv",
        ["run_id", "fetched_utc", "country", "product", "price", "currency",
         "unit", "source"],
        [[run_id, p.fetched_utc, p.country_code, p.product, p.price,
          p.currency, p.unit, p.source] for p in local_prices],
    )

    snapshot = {
        "run_id": run_id,
        "fetched_utc": fetched_utc,
        "international_usd_per_barrel": {q.benchmark: q.price_usd for q in quotes},
        "fx_usd_rates": {"source": fx_source, "rates": rates},
        "local_prices": [vars(p) for p in local_prices],
        "benchmark_in_local_currency": [
            {"country": r[1], "name": r[2], "currency": r[3], "benchmark": r[4],
             "per_barrel": r[5], "per_litre": r[6]}
            for r in benchmark_rows
        ],
    }
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.SNAPSHOT_DIR / f"{run_id}.json", "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)


def run(force: bool = False) -> str:
    """Execute one collection cycle. Returns the final run status."""
    local_date, slot = current_slot()
    run_id = f"{local_date}-{slot}"
    fetched_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = db.connect()
    if not db.start_run(conn, run_id, local_date, slot, fetched_utc) and not force:
        log.info("Run %s already completed; skipping (use --force to redo)", run_id)
        return "skipped"

    failures = []

    # Every layer is best-effort: one blocked source must never prevent the
    # others from being collected and saved.

    # 1. International benchmarks.
    quotes = []
    try:
        quotes = international.fetch()
        db.save_international(conn, run_id, quotes)
        log.info("International: %s",
                 {q.benchmark: q.price_usd for q in quotes})
    except Exception as exc:
        failures.append("international")
        log.error("International fetch failed: %s", exc)

    # 2. FX rates + derived per-country benchmark prices.
    rates, fx_source, benchmark_rows = {}, None, []
    try:
        rates, fx_source = fx.fetch()
        db.save_fx(conn, run_id, fetched_utc, rates, fx_source)
        log.info("FX: %d currencies from %s", len(rates), fx_source)
        if quotes:
            benchmark_rows = _derive_benchmark_local(run_id, quotes, rates)
            db.save_benchmark_local(conn, run_id, benchmark_rows)
            log.info("Derived %d country benchmark rows", len(benchmark_rows))
    except Exception as exc:
        failures.append("fx")
        log.error("FX fetch failed: %s", exc)

    # 3. Local pump prices from every registered scraper (best effort each).
    local_prices = []
    for country, scraper in LOCAL_SCRAPERS.items():
        try:
            prices = scraper()
            local_prices.extend(prices)
            log.info("Local %s: %s", country,
                     {p.product: p.price for p in prices})
        except Exception as exc:
            failures.append(f"local:{country}")
            log.error("Local scrape for %s failed: %s", country, exc)
    if local_prices:
        db.save_local_prices(conn, run_id, local_prices)

    collected_anything = bool(quotes or rates or local_prices)
    if collected_anything:
        _export(run_id, fetched_utc, quotes, rates, fx_source, local_prices,
                benchmark_rows)

    if not collected_anything:
        status = "failed"
    elif failures:
        status = "partial"
    else:
        status = "ok"
    db.finish_run(conn, run_id, status)
    log.info("Run %s finished: %s%s", run_id, status,
             f" (failed: {', '.join(failures)})" if failures else "")
    return status
