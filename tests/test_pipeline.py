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
    monkeypatch.setattr(pipeline, "LOCAL_SCRAPERS", {"PK": lambda: FAKE_LOCAL})

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
    monkeypatch.setattr(pipeline, "LOCAL_SCRAPERS", {"PK": _boom})

    assert pipeline.run() == "partial"


def test_international_failure_still_saves_fx_and_local(isolated_data,
                                                        monkeypatch):
    monkeypatch.setattr(pipeline.international, "fetch", _boom)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setattr(pipeline, "LOCAL_SCRAPERS", {"PK": lambda: FAKE_LOCAL})

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
    monkeypatch.setattr(pipeline, "LOCAL_SCRAPERS", {"PK": _boom})

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


# --- United States (EIA) -----------------------------------------------

EIA_HTML = """
<table>
  <caption>U.S. Regular Gasoline Prices*(dollars per gallon) full history XLS</caption>
  <tr><td></td><td></td><td></td><td></td><td>Change from</td></tr>
  <tr><td></td><td>07/27/26</td><td>08/03/26</td><td>08/10/26</td>
      <td>2 year ago</td><td>year ago</td><td>week ago</td></tr>
  <tr><td>U.S.</td><td>4.096</td><td>4.079</td><td>4.006</td>
      <td>0.592</td><td>0.888</td><td>-0.073</td></tr>
  <tr><td>East Coast (PADD1)</td><td>3.997</td><td>3.944</td><td>3.884</td>
      <td>0.558</td><td>0.879</td><td>-0.060</td></tr>
</table>
<table>
  <caption>States</caption>
  <tr><td></td><td>07/27/26</td><td>08/03/26</td><td>08/10/26</td></tr>
  <tr><td>California</td><td>5.489</td><td>5.495</td><td>5.428</td></tr>
</table>
<table>
  <caption>U.S. On-Highway Diesel Fuel Prices*(dollars per gallon) full history XLS</caption>
  <tr><td></td><td></td><td></td><td></td><td>Change from</td></tr>
  <tr><td></td><td>07/27/26</td><td>08/03/26</td><td>08/10/26</td>
      <td>2 year ago</td><td>year ago</td><td>week ago</td></tr>
  <tr><td>U.S.</td><td>5.313</td><td>5.348</td><td>5.257</td>
      <td>1.553</td><td>1.503</td><td>-0.091</td></tr>
</table>
"""


def test_usa_parser(monkeypatch):
    from oilprice.fetchers import usa

    class FakeResp:
        text = EIA_HTML

    monkeypatch.setattr(usa.http, "get", lambda url: FakeResp())
    prices = {p.product: p for p in usa.fetch()}

    # Latest date column (08/10/26) wins, converted from USD/gallon to /litre.
    assert prices["petrol"].price == pytest.approx(
        4.006 / config.LITRES_PER_US_GALLON, abs=1e-6)
    assert prices["diesel"].price == pytest.approx(
        5.257 / config.LITRES_PER_US_GALLON, abs=1e-6)
    assert all(p.currency == "USD" and p.unit == "litre" and p.country_code == "US"
               for p in prices.values())
    assert set(prices) == {"petrol", "diesel"}


def test_usa_ignores_change_from_columns(monkeypatch):
    """The 'Change from' deltas sit after the dates and must never be read."""
    from oilprice.fetchers import usa

    class FakeResp:
        text = EIA_HTML

    monkeypatch.setattr(usa.http, "get", lambda url: FakeResp())
    prices = {p.product: p for p in usa.fetch()}
    # 0.888 (a year-ago delta) would convert to ~0.235; the real value is ~1.06.
    assert prices["petrol"].price > 1.0


# --- United Kingdom (DESNZ) --------------------------------------------

UK_CSV = chr(0xFEFF) + chr(10).join([
    "Date,ULSP (Ultra low sulphur unleaded petrol) Pump price in pence/litre,"
    "ULSD (Ultra low sulphur diesel) Pump price in pence/litre,"
    "ULSP duty,ULSD duty,ULSP VAT,ULSD VAT",
    "27/07/2026,156.13,173.97,52.95,52.95,20,20",
    "03/08/2026,159.89,179.19,52.95,52.95,20,20",
    "10/08/2026,162.15,181.97,52.95,52.95,20,20",
]) + chr(10)

UK_PAGE_HTML = (
    '<html><body><a href="/media/abc123/Weekly_Fuel_Prices.xlsx">XLSX</a>'
    '<a href="/media/def456/CSV__2018_-__.csv">CSV</a></body></html>'
)


def _uk_fake_get(csv_text):
    """Serve the statistics page, then the CSV it links to."""
    from oilprice.fetchers import uk as _uk

    def fake_get(url):
        class Resp:
            text = UK_PAGE_HTML if url == _uk.UK_PAGE_URL else csv_text
        return Resp()
    return fake_get


def test_uk_parser(monkeypatch):
    from oilprice.fetchers import uk

    monkeypatch.setattr(uk.http, "get", _uk_fake_get(UK_CSV))
    prices = {p.product: p for p in uk.fetch()}

    # Last row is the most recent; pence converted to pounds.
    assert prices["petrol"].price == pytest.approx(1.6215)
    assert prices["diesel"].price == pytest.approx(1.8197)
    assert all(p.currency == "GBP" and p.country_code == "GB"
               for p in prices.values())


def test_uk_skips_trailing_blank_rows(monkeypatch):
    from oilprice.fetchers import uk

    monkeypatch.setattr(
        uk.http, "get", _uk_fake_get(UK_CSV + ",,,,,," + chr(10) + chr(10)))
    prices = {p.product: p for p in uk.fetch()}
    assert prices["petrol"].price == pytest.approx(1.6215)


# --- European Union (Weekly Oil Bulletin) ------------------------------

def _eu_workbook():
    """Build a workbook shaped like the real Weekly Oil Bulletin."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["in EUR", "Euro-super 95  (I)", "Gas oil automobile Aut",
               " Gas oil de chauffage ", " Fuel oil - Schweres H",
               " Fuel oil -Schweres He", "GPL pour moteur LPG mo"])
    ws.append(["2026-08-10 00:00:00", "1000 l", "1000 l", "1000 l", "t", "t", "1000 l"])
    ws.append(["Germany", 2143, 2149, 1315.7, None, None, 1090.38])
    ws.append(["France", 2014.06, 2169.04, 1646.17, None, None, 1018.11])
    ws.append(["Poland", 1696.19, 1863.79, 1390.73, 697.67, 527.21, 700.99])
    ws.append(["Austria", 1721, 1965, 1542.87])            # short row
    ws.append(["Malta", 1340, 1210, 1000])
    ws.append(["CE/EC/EG EUR27_2020 (I", 1910.68, 2012.67, 1416.59])   # aggregate
    ws.append(["CE/EC/EG Euro Area 20 ", 1973.22, 2049.22, 1399.60])   # aggregate
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


EU_PAGE_HTML = (
    '<html><body>'
    '<a href="/document/download/uuid-1_en?filename=Oil_Bulletin_Duties.xlsx">duties</a>'
    '<a href="/document/download/uuid-2_en?filename=Weekly%20prices%20with%20taxes.xlsx">'
    'prices with taxes</a>'
    '</body></html>'
)


def _eu_fake_get():
    """Serve the bulletin page, then the workbook it links to."""
    from oilprice.fetchers import eu as _eu
    book = _eu_workbook()

    def fake_get(url):
        class Resp:
            text = EU_PAGE_HTML
            content = book
        return Resp()
    return fake_get


def test_eu_parser(monkeypatch):
    from oilprice.fetchers import eu

    monkeypatch.setattr(eu.http, "get", _eu_fake_get())
    prices = eu.fetch()
    by_country = {}
    for p in prices:
        by_country.setdefault(p.country_code, {})[p.product] = p.price

    # EUR per 1000 litres -> EUR per litre.
    assert by_country["DE"]["petrol"] == pytest.approx(2.143)
    assert by_country["DE"]["diesel"] == pytest.approx(2.149)
    assert by_country["FR"]["petrol"] == pytest.approx(2.01406)
    assert by_country["MT"]["petrol"] == pytest.approx(1.340)

    # Country names resolve to ISO codes via the shared reference data.
    assert "PL" in by_country and "AT" in by_country

    # Aggregate rows (EU27, Euro Area) are not countries.
    assert not [p for p in prices if p.country_code.startswith("CE")]
    assert len(by_country) == 5


def test_eu_prices_are_labelled_eur_not_national_currency(monkeypatch):
    """The bulletin converts everything to EUR, including for non-euro states."""
    from oilprice.fetchers import eu

    monkeypatch.setattr(eu.http, "get", _eu_fake_get())
    poland = [p for p in eu.fetch() if p.country_code == "PL"]
    assert poland, "Poland should be present"
    # Poland's currency is PLN, but these figures are euro-denominated.
    assert all(p.currency == "EUR" for p in poland)


def test_eu_skips_tonne_priced_heavy_fuel_oil(monkeypatch):
    """Heavy fuel oil is quoted per tonne; mixing units would corrupt the store."""
    from oilprice.fetchers import eu

    monkeypatch.setattr(eu.http, "get", _eu_fake_get())
    prices = eu.fetch()
    assert all(p.unit == "litre" for p in prices)
    assert all("fuel_oil" not in p.product for p in prices)
    # Poland has heavy-fuel-oil values in the sheet; they must not appear.
    pl = {p.product for p in prices if p.country_code == "PL"}
    assert pl == {"petrol", "diesel", "heating_oil", "lpg"}


def test_all_scrapers_registered():
    from oilprice.fetchers import LOCAL_SCRAPERS
    assert set(LOCAL_SCRAPERS) == {"PK", "US", "GB", "EU"}


def test_multi_country_scraper_logs_every_country(isolated_data, monkeypatch,
                                                  caplog):
    """A multi-country source must not be reported as one country alone."""
    multi = [
        LocalPrice("DE", "petrol", 2.14, "EUR", NOW, "test"),
        LocalPrice("FR", "petrol", 2.01, "EUR", NOW, "test"),
        LocalPrice("PL", "petrol", 1.69, "EUR", NOW, "test"),
    ]
    monkeypatch.setattr(pipeline.international, "fetch", lambda: FAKE_QUOTES)
    monkeypatch.setattr(pipeline.fx, "fetch", lambda: (FAKE_RATES, "test-fx"))
    monkeypatch.setattr(pipeline, "LOCAL_SCRAPERS", {"EU": lambda: multi})

    with caplog.at_level("INFO"):
        assert pipeline.run() == "ok"
    assert "3 countries" in caplog.text

    conn = db.connect()
    stored = conn.execute(
        "SELECT DISTINCT country_code FROM local_prices").fetchall()
    assert {r["country_code"] for r in stored} == {"DE", "FR", "PL"}


def test_slot_boundaries():
    tz = config.LOCAL_TZ
    assert pipeline.current_slot(datetime(2026, 8, 13, 9, 0, tzinfo=tz))[1] == "AM"
    assert pipeline.current_slot(datetime(2026, 8, 13, 21, 0, tzinfo=tz))[1] == "PM"
