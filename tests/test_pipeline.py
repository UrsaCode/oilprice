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


def _boom():
    raise RuntimeError("source down")


def test_partial_when_local_scrape_fails(isolated_data, monkeypatch):
    monkeypatch.setattr(pipeline.international, "fetch", lambda: FAKE_QUOTES)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setitem(pipeline.LOCAL_SCRAPERS, "PK", _boom)

    assert pipeline.run() == "partial"


def test_international_failure_still_saves_fx_and_local(isolated_data,
                                                        monkeypatch):
    monkeypatch.setattr(pipeline.international, "fetch", _boom)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setitem(pipeline.LOCAL_SCRAPERS, "PK", lambda: FAKE_LOCAL)

    assert pipeline.run() == "partial"

    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) c FROM local_prices").fetchone()["c"] == 2
    assert conn.execute("SELECT COUNT(*) c FROM fx_rates").fetchone()["c"] == 3
    # No benchmark conversion possible without international quotes.
    assert conn.execute(
        "SELECT COUNT(*) c FROM benchmark_local"
    ).fetchone()["c"] == 0


def test_failed_when_every_source_fails(isolated_data, monkeypatch):
    monkeypatch.setattr(pipeline.international, "fetch", _boom)
    monkeypatch.setattr(pipeline.fx, "fetch", _boom)
    monkeypatch.setitem(pipeline.LOCAL_SCRAPERS, "PK", _boom)

    assert pipeline.run() == "failed"
    # Nothing collected -> no exports written.
    assert not (isolated_data / "snapshots").exists()


def test_fred_parser(monkeypatch):
    from oilprice.fetchers import international

    csv_by_url = {
        international.FRED_URL.format(series="DCOILBRENTEU"):
            "DATE,DCOILBRENTEU\n2026-08-11,79.10\n2026-08-12,80.25\n2026-08-13,.\n",
        international.FRED_URL.format(series="DCOILWTICO"):
            "DATE,DCOILWTICO\n2026-08-12,76.40\n",
    }

    class FakeResp:
        def __init__(self, text):
            self.text = text

    monkeypatch.setattr(
        international.http, "get", lambda url: FakeResp(csv_by_url[url])
    )
    quotes = {q.benchmark: q for q in international._from_fred()}
    # "." (missing day) rows are skipped; latest numeric value wins.
    assert quotes["BRENT"].price_usd == 80.25
    assert quotes["WTI"].price_usd == 76.40


def test_github_dataset_parser(monkeypatch):
    from oilprice.fetchers import international

    csv_by_url = {
        international.GITHUB_DATASET_URL.format(
            branch="master", file="brent-daily.csv"):
            "Date,Price\n2026-08-10,92.74\n2026-08-11,93.26\n",
        international.GITHUB_DATASET_URL.format(
            branch="master", file="wti-daily.csv"):
            "Date,Price\n2026-08-11,84.77\n",
    }

    class FakeResp:
        def __init__(self, text):
            self.text = text

    def fake_get(url):
        if url not in csv_by_url:
            raise RuntimeError("404")
        return FakeResp(csv_by_url[url])

    monkeypatch.setattr(international.http, "get", fake_get)
    quotes = {q.benchmark: q for q in international._from_github_dataset()}
    assert quotes["BRENT"].price_usd == 93.26
    assert quotes["WTI"].price_usd == 84.77


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


def test_pakistan_pso_current_markup(monkeypatch):
    """Row-oriented table as served by psopk.com/en/fuels/fuel-prices."""
    html = """
    <table>
      <tr><th>Product Name</th><th>Rs./Litre</th></tr>
      <tr><td>PREMIER EURO 5</td><td>Rs.325.43/Ltr</td></tr>
      <tr><td>HI-CETANE DIESEL EURO 5</td><td>Rs.383.95/Ltr</td></tr>
      <tr><td>LDO</td><td>Rs.249.03/Ltr</td></tr>
      <tr><td>SKO</td><td>Rs.289.75/Ltr</td></tr>
      <tr><td>JP-1</td><td>Rs.324.8/Ltr</td></tr>
    </table>
    """

    class FakeResp:
        text = html

    monkeypatch.setattr(pakistan.http, "get", lambda url: FakeResp())
    prices = {p.product: p for p in pakistan._scrape_tables("http://x", "test")}
    assert prices["petrol"].price == 325.43
    assert prices["diesel"].price == 383.95
    assert prices["light_diesel"].price == 249.03
    assert prices["kerosene"].price == 289.75
    # Jet fuel is not one of our products.
    assert set(prices) == {"petrol", "diesel", "light_diesel", "kerosene"}


def test_pakistan_column_oriented_table(monkeypatch):
    """hamariweb lists products as headers with one row per date."""
    html = """
    <table>
      <tr><th>Date</th><th>Petrol</th><th>HS Diesel</th>
          <th>Light Diesel</th><th>Kerosene Oil</th></tr>
      <tr><td>Aug 14, 2026</td><td>325.43</td><td>383.95</td>
          <td>249.03</td><td>289.75</td></tr>
      <tr><td>Aug 13, 2026</td><td>324.98</td><td>382.79</td>
          <td>247.82</td><td>289.27</td></tr>
    </table>
    """

    class FakeResp:
        text = html

    monkeypatch.setattr(pakistan.http, "get", lambda url: FakeResp())
    prices = {p.product: p for p in pakistan._scrape_tables("http://x", "test")}
    # Most recent row (first) wins; the date column is never read as a price.
    assert prices["petrol"].price == 325.43
    assert prices["diesel"].price == 383.95
    assert prices["light_diesel"].price == 249.03
    assert prices["kerosene"].price == 289.75


def test_light_diesel_not_classified_as_diesel():
    """'Light Diesel' contains 'diesel'; the more specific product must win."""
    assert pakistan._classify("Light Diesel") == "light_diesel"
    assert pakistan._classify("Light Diesel Oil") == "light_diesel"
    assert pakistan._classify("LDO") == "light_diesel"
    assert pakistan._classify("HS Diesel") == "diesel"
    assert pakistan._classify("Hi-Cetane Diesel Euro 5") == "diesel"
    assert pakistan._classify("Premier Euro 5") == "petrol"
    assert pakistan._classify("Kerosene Oil") == "kerosene"
    assert pakistan._classify("Date") is None
    assert pakistan._classify("JP-1") is None


def test_slot_boundaries():
    tz = config.LOCAL_TZ
    assert pipeline.current_slot(datetime(2026, 8, 13, 9, 0, tzinfo=tz))[1] == "AM"
    assert pipeline.current_slot(datetime(2026, 8, 13, 21, 0, tzinfo=tz))[1] == "PM"
