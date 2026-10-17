# Changelog

All notable changes to Paisa Wapas. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Live NPCI data scraper** (`data/scrape_sources.py`, `make sources`). Pulls
  NPCI's published UPI statistics through their own JSON API and writes
  `data/real_distributions.json`:
  - bank-wise remitter performance (top-50: volume share, approved %,
    business-decline %, technical-decline %)
  - monthly UPI volume/value across two financial years
  - daily UPI volumes for a recent month (salary-day cycle evidence)
  - bank downtime/incident list with hours down

  npci.org.in sits behind an F5 JavaScript challenge, so requests go through
  the monid CLI's context.dev scraping proxy (~$0.001/request). Raw responses
  are cached under `data/raw_sources/`; `--fresh` refetches.
- Console and dashboard footers carrying project identity, data provenance,
  the synthetic-data disclaimer, and links to the changelog and compliance
  policy.
- `app/server.py` serves the repo docs those footers link to (`CHANGELOG.md`,
  `policy.json`) read-only from the repo root.

### Changed

- `data/generator.py` now reads `data/real_distributions.json` when present:
  `BANK_WEIGHTS` comes from real NPCI volume shares and the mandate-decline
  weights are re-split by the published business:technical ratio. Without the
  file it falls back to the documented assumptions, so a fresh clone still
  runs offline.
- Regenerated the frozen batch and evaluation on the real distributions.
  Headline result moved from +121% to **+164% recovered vs the cron baseline**
  (₹31.5L of ₹43.6L at risk, 72.2% recovery rate, 1,624 attempts).
- `data/SOURCES.md` documents which numbers are now scraped live and which
  remain assumptions.

### Removed

- Welcome splash overlay on the operator console — the console now opens
  straight to the live view.

### Notes on honesty

- Real NPCI decline data is ~95:5 business:technical. A 10% technical-decline
  floor is applied so the bank-downtime recovery path stays demonstrable; the
  raw unmodified split is kept alongside it in `real_distributions.json` and
  is disclosed rather than hidden.
- The Autopay-specific ecosystem endpoint rejects the parameter shapes tried
  so far, so mandate-specific bank data is not yet incorporated. UPI-wide
  figures ground the simulator in the meantime.

## [0.1.0] — Initial build

### Added

- Five-stage recovery pipeline: ingest → diagnose → decide → act → learn.
- Contextual-bandit retry-timing engine (the decision brain); Claude used only
  for plain-English explanations and Hinglish customer nudges.
- Seeded synthetic batch of 1,000 failed payments (`data/generator.py`) and a
  deterministic outcome simulator with a salary-day balance cycle, bank
  downtime windows, and per-customer responsiveness.
- Evaluation harness (`eval/run_eval.py`) comparing the agent against
  retry-immediately and cron-every-24h baselines on the same frozen batch,
  writing `eval/results.json` and `ledger.sqlite`.
- Compliance guardrails in `policy.json`, enforced structurally: retry caps,
  contact-hour windows, opt-out handling, and human approval above an amount
  ceiling. Violations are asserted to be zero in the eval.
- Full audit trail per case (`make inspect`) and a published exception list of
  cases the agent could not recover, with reasons.
- Live operator console (`make app`) and batch-results dashboard
  (`make dashboard`).
