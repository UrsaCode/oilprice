# Contributing

The most useful thing anyone can do here is **tell us a source broke**, or
**add a country**. Both are covered below. Everything else is ordinary.

## The one rule

**Never invent a number.** If a source is ambiguous, unreachable, or has
changed beyond recognition, the fetch must fail loudly. A run that fails for
one country is marked `partial` and the other thirty-four still store cleanly;
a run that guesses is worse than one that stops, because a guess is
indistinguishable from a reading once it is in the database.

That rule is why there are sanity bounds on every parser, why `_classify`
prefers the longest matching keyword, and why Mexico's median and the
benchmark-times-FX figures both say in their `source` column that they are
derived. If a change you are making needs an exception to this rule, it
probably needs a different change.

## Getting set up

```bash
git clone https://github.com/UrsaCode/oilprice
cd oilprice
python -m venv .venv && . .venv/Scripts/activate    # or bin/activate
pip install -r requirements.txt pytest

python -m pytest tests/ -v      # fully offline, no source site is touched
python -m oilprice run          # a real collection, writes to data/
python -m oilprice show         # what is stored now
```

To look at the published page locally:

```bash
python tools/build_site.py      # writes docs/summary.json from the database
python -m http.server -d docs 4173
```

## A source stopped working

This is the common one. Sites redesign, and a keyword-driven parser survives
most of that but not all of it. Please open a
[source-broken issue](https://github.com/UrsaCode/oilprice/issues/new?template=source-broken.yml)
with the country and, if you can, **the fragment of HTML or CSV that no longer
parses**. That fragment is worth more than a description of it: it goes
straight into `tests/test_pipeline.py` as a fixture, so the fix arrives with a
test that would have caught the break.

Every parser test in this repository is a real capture of a real page. None of
them are invented markup, which is deliberate — a test against markup we made
up proves the parser reads what we imagined, not what the source publishes.

## Adding a country

1. Create `oilprice/fetchers/<country>.py` with a module docstring saying
   **where the figures come from and what is odd about that source**. Read
   `india.py` for a PDF source, `mexico.py` for a derived national figure, and
   `bangladesh.py` for one that needs a certificate the site fails to serve.
2. Give it a `fetch()` returning `list[models.LocalPrice]`. Each record carries
   its own `country_code`, so one fetcher may cover many countries — `eu.py`
   returns all twenty-seven from a single spreadsheet.
3. Set `source` to something a reader can act on. `"ppac.gov.in (Delhi)"` says
   which city; `"cre.gob.mx (median of 10623 stations)"` says the figure is
   derived and how. `"scraped"` says nothing.
4. Register it in `oilprice/fetchers/__init__.py` under `LOCAL_SCRAPERS`.
5. Add a test with captured markup, and add the row to the table in `README.md`.

Products should use the existing names where they fit — `petrol`, `diesel`,
`kerosene`, `light_diesel` — so cross-country queries keep working. Add a new
name only for a genuinely new product, as Brazil did for `ethanol`.

Prices are stored **per litre in the local currency, exactly as published**.
Convert units on the way in if the source publishes gallons, as `usa.py` does,
and leave the currency alone: the exchange rate is captured separately at the
same moment, and converting at fetch time would throw away the published
figure.

## Pull requests

- Keep the change to one thing. A new country and a refactor of the HTTP layer
  are two pull requests.
- Tests must pass offline: `python -m pytest tests/ -v`. CI runs them on 3.12
  and 3.13.
- Never commit anything under `data/` in a code pull request. That directory is
  written by the collection workflow, and a hand-edited row there is a figure
  nobody published.
- Match the surrounding style. Comments here explain *why* a thing is done the
  way it is, especially where the obvious approach was wrong; they do not
  restate what the line does.

## What is out of scope

Analysis, forecasting, price predictions, and any feature that produces a
number no publisher published. The page under `docs/` may chart what is stored
and may convert it at a captured rate, and that is the limit. This repository
is a record.
