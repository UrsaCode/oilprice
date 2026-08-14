"""Command-line interface.

  python -m oilprice run              # one collection cycle now
  python -m oilprice schedule         # keep running at the configured hours
  python -m oilprice add-local ...    # manually record a local pump price
  python -m oilprice show             # latest stored prices
"""

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config, db, pipeline
from .countries import currency_for
from .models import LocalPrice

log = logging.getLogger("oilprice")


def cmd_run(args):
    status = pipeline.run(force=args.force)
    print(f"Collection finished with status: {status}")
    if status == "failed":  # nothing at all was collected
        raise SystemExit(1)


def _next_run_time(now):
    """Next scheduled datetime (local tz) from SCHEDULE_HOURS."""
    for offset_days in (0, 1):
        day = now + timedelta(days=offset_days)
        for hour in sorted(config.SCHEDULE_HOURS):
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return candidate
    raise RuntimeError("unreachable")


def cmd_schedule(args):
    hours = ", ".join(f"{h:02d}:00" for h in sorted(config.SCHEDULE_HOURS))
    print(f"Scheduler started; collecting daily at {hours} ({config.LOCAL_TZ})")
    if args.immediately:
        _safe_run()
    while True:
        now = datetime.now(config.LOCAL_TZ)
        nxt = _next_run_time(now)
        wait = (nxt - now).total_seconds()
        print(f"Next collection at {nxt:%Y-%m-%d %H:%M %Z} "
              f"(in {wait / 3600:.1f}h)")
        time.sleep(wait)
        _safe_run()


def _safe_run():
    try:
        status = pipeline.run()
        print(f"[{datetime.now(config.LOCAL_TZ):%H:%M}] run status: {status}")
    except Exception as exc:
        log.error("Scheduled run failed: %s", exc)


def cmd_add_local(args):
    currency = args.currency or currency_for(args.country)
    if not currency:
        raise SystemExit(
            f"Unknown country {args.country!r}; pass --currency explicitly."
        )
    local_date, slot = pipeline.current_slot()
    run_id = f"{local_date}-{slot}"
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = db.connect()
    db.start_run(conn, run_id, local_date, slot, now_utc)
    price = LocalPrice(
        country_code=args.country.upper(), product=args.product,
        price=args.price, currency=currency, unit=args.unit,
        fetched_utc=now_utc, source="manual",
    )
    db.save_local_prices(conn, run_id, [price])
    print(f"Saved: {price.country_code} {price.product} = "
          f"{price.price} {price.currency}/{price.unit} (run {run_id})")


def cmd_show(args):
    conn = db.connect()
    run = conn.execute(
        "SELECT run_id, status FROM runs ORDER BY started_utc DESC LIMIT 1"
    ).fetchone()
    if not run:
        print("No data collected yet. Run: python -m oilprice run")
        return
    print(f"Latest run: {run['run_id']} (status: {run['status']})\n")

    print("International benchmarks (USD/barrel):")
    for row in conn.execute(
        "SELECT benchmark, price_usd, source FROM international_prices "
        "WHERE run_id = ?", (run["run_id"],)
    ):
        print(f"  {row['benchmark']:<6} {row['price_usd']:>10.2f}   [{row['source']}]")

    rows = conn.execute(
        "SELECT country_code, product, price, currency, unit, source "
        "FROM local_prices WHERE run_id = ?", (run["run_id"],)
    ).fetchall()
    if rows:
        print("\nLocal pump prices:")
        for row in rows:
            print(f"  {row['country_code']} {row['product']:<13} "
                  f"{row['price']:>10.2f} {row['currency']}/{row['unit']}"
                  f"   [{row['source']}]")

    if args.country:
        print(f"\nBenchmark in local currency ({args.country.upper()}):")
        for row in conn.execute(
            "SELECT benchmark, currency, price_per_barrel, price_per_litre "
            "FROM benchmark_local WHERE run_id = ? AND country_code = ?",
            (run["run_id"], args.country.upper()),
        ):
            print(f"  {row['benchmark']:<6} {row['price_per_barrel']:>14.2f} "
                  f"{row['currency']}/barrel  "
                  f"({row['price_per_litre']:.2f}/litre)")


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="oilprice")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run one collection cycle now")
    p.add_argument("--force", action="store_true",
                   help="re-collect even if this slot already completed")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("schedule", help="run forever at the configured hours")
    p.add_argument("--immediately", action="store_true",
                   help="also collect once right away")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("add-local", help="manually record a local pump price")
    p.add_argument("--country", required=True, help="ISO code, e.g. PK")
    p.add_argument("--product", required=True,
                   help="petrol / diesel / kerosene / ...")
    p.add_argument("--price", required=True, type=float)
    p.add_argument("--currency", help="defaults to the country's currency")
    p.add_argument("--unit", default="litre")
    p.set_defaults(func=cmd_add_local)

    p = sub.add_parser("show", help="print the latest stored prices")
    p.add_argument("--country", help="also show benchmark in this country's currency")
    p.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
