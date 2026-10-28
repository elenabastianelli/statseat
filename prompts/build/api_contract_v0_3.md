# API contract v0.3.0 for the metrics, 2D replay removed

- **Date:** 2026-10-25
- **Tool:** Claude Code (Claude Opus 5.5), plan approved by the team before execution
- **Area:** `[APP > Data & Statistics]`

## Prompt (English version of the team prompt)

> Let's go back to what I must prepare for the Data & Statistics screens: make a plan of what we have to do. The app developer must be able to build the
> new Data & Statistics screens once, without touching them again. Also remove the 2D replay task and the replay
> from every part of the project, because we have no time: put it in the roadmap, we will show it at delivery.

## Plan approved by the team (summary)

- Fix the shape of the data **once**: API contract v0.3.0 from the metric decisions of `Materials/METRICS.md`
  (Fan and Analyzer versions, one mirror chart for every series over time, reliability symbol, "available from"
  minute, Player Focus shared by Data & Statistics, Recap and PDF, new metrics: acceleration load, woodwork, most
  touches, counter-attacks, offsides, PPDA).
- Complete fake data, so the app is built now and the metrics work later fills the same fields with real numbers.
- Engine: the counts that the events already give, null with a reason for everything the metrics work computes.
- App: only the minimum so that nothing breaks (types, Fan and Analyzer labels in 8 languages, goals vs xG,
  possession bar); the new screens are the Data & Statistics screens.
- 2D replay removed from the contract, the mock, the engine, the app and the documents; it is a roadmap item.

## Result

Contract v0.3.0 (`app/contract/models.py`, changes listed in `app/contract/README.md`), fake data regenerated,
engine and PDF updated, app tests and type check green (app 106 tests, web 27 tests).
