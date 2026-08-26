## What this changes

<!-- One or two sentences. If it adds a country, say which and from what source. -->

## Why

<!-- What was wrong, or what could not be done before. If a source changed, say how. -->

## Checklist

- [ ] `python -m pytest tests/ -v` passes, and the suite still runs offline
- [ ] Any new parser has a test built from **real captured markup**, not invented markup
- [ ] Nothing under `data/` is committed — that directory belongs to the collection workflow
- [ ] No figure is invented: an unreadable source fails loudly rather than guessing
- [ ] A new source sets `source` to something a reader can act on, and says so if the figure is derived
- [ ] `README.md`'s coverage table is updated if this adds or changes a country
