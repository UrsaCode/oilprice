"""Offline tests: full pipeline with mocked network, plus parser checks.

Run with:  python -m pytest tests/ -v
"""

import json
from datetime import datetime, timezone

import pytest

from oilprice import config, db, pipeline
from oilprice.fetchers import pakistan
from oilprice.models import BenchmarkQuote, LocalPrice

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

FAKE_QUOTES = [
    BenchmarkQuote("BRENT", 80.5, NOW, "test"),
    BenchmarkQuote("WTI", 76.2, NOW, "test"),
]
FAKE_RATES = {"PKR": 280.0, "USD": 1.0, "EUR": 0.92}
FAKE_LOCAL = [
    LocalPrice("PK", "petrol", 275.6, "PKR", NOW, "test"),
    LocalPrice("PK", "diesel", 285.3, "PKR", NOW, "test"),
]


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "oilprice.db")
    monkeypatch.setattr(config, "CSV_DIR", tmp_path / "csv")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    return tmp_path


def test_full_run_with_mocked_sources(isolated_data, monkeypatch):
    monkeypatch.setattr(pipeline.international, "fetch", lambda: FAKE_QUOTES)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setitem(pipeline.LOCAL_SCRAPERS, "PK", lambda: FAKE_LOCAL)

    assert pipeline.run() == "ok"

    conn = db.connect()
    intl = conn.execute("SELECT * FROM international_prices").fetchall()
    assert {r["benchmark"] for r in intl} == {"BRENT", "WTI"}

    pk = conn.execute(
        "SELECT * FROM benchmark_local WHERE country_code = 'PK' "
        "AND benchmark = 'BRENT'"
    ).fetchone()
    assert pk["currency"] == "PKR"
    assert pk["price_per_barrel"] == pytest.approx(80.5 * 280.0)
    assert pk["price_per_litre"] == pytest.approx(
        80.5 * 280.0 / config.LITRES_PER_BARREL
    )

    pump = conn.execute(
        "SELECT price FROM local_prices WHERE country_code='PK' "
        "AND product='petrol'"
    ).fetchone()
    assert pump["price"] == 275.6

    # Second run in the same slot is skipped, not duplicated.
    assert pipeline.run() == "skipped"
    assert conn.execute(
        "SELECT COUNT(*) c FROM international_prices"
    ).fetchone()["c"] == 2

    # Exports exist.
    assert (isolated_data / "csv" / "international.csv").exists()
    snapshots = list((isolated_data / "snapshots").glob("*.json"))
    assert len(snapshots) == 1
    snap = json.loads(snapshots[0].read_text())
    assert snap["international_usd_per_barrel"]["BRENT"] == 80.5


def test_partial_when_local_scrape_fails(isolated_data, monkeypatch):
    def boom():
        raise RuntimeError("site down")

    monkeypatch.setattr(pipeline.international, "fetch", lambda: FAKE_QUOTES)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setitem(pipeline.LOCAL_SCRAPERS, "PK", boom)

    assert pipeline.run() == "partial"


def test_pakistan_table_parser(monkeypatch):
    html = """
    <table>
      <tr><th>Product</th><th>Price</th></tr>
      <tr><td>Premier Euro 5</td><td>Rs. 275.60 /Ltr</td></tr>
      <tr><td>Hi-Cetane Diesel</td><td>Rs. 285.34 /Ltr</td></tr>
      <tr><td>Kerosene Oil</td><td>183.16</td></tr>
    </table>
    """

    class FakeResp:
        text = html

    monkeypatch.setattr(pakistan.http, "get", lambda url: FakeResp())
    prices = {p.product: p for p in pakistan._scrape_tables("http://x", "test")}
    assert prices["petrol"].price == 275.60
    assert prices["diesel"].price == 285.34
    assert prices["kerosene"].price == 183.16
    assert all(p.currency == "PKR" for p in prices.values())


def test_slot_boundaries():
    tz = config.LOCAL_TZ
    assert pipeline.current_slot(datetime(2026, 8, 13, 9, 0, tzinfo=tz))[1] == "AM"
    assert pipeline.current_slot(datetime(2026, 8, 13, 21, 0, tzinfo=tz))[1] == "PM"
