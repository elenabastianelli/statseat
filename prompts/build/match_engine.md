# Match engine and the no-look-ahead window

- **Date:** 2026-10-24
- **Tool:** Claude Code (Claude Opus 5.5)
- **Area:** `[ENGINE > Simulator and statistics]`

## Prompt (English version of the team prompt)

> Create the match engine in app/engine/ (Python 3.12, FastAPI) following the project plan and the
> contract in app/contract/: a simulator that replays the match chosen by MATCH_SOURCE (fake = the fake events of
> app/contract/fake/; dfl:J03WPY only where the DFL data are; synthetic:SYN-001 when it is ready) at a configurable
> speed (10x), a phase state machine driven by KickOff and FinalWhistle, deterministic statistics from data_pipeline,
> and a demo endpoint to jump between phases. Rule "no future": the rest of the backend reads the match only through a
> window that returns frames and events up to the current replay instant, and every event is processed only after its
> frames. Add a truncation test: the statistics at minute t must be identical with the whole match or with a file cut
> at minute t. Add the other tests and explain to me in Italian how to start it.

Design agreed with the team before coding (from the orchestrating agent):
1. Sources selected by MATCH_SOURCE behind one adapter interface that yields the canonical schema of data_pipeline:
   `fake` (events only: frame metrics null with a reason), `dfl:<id>` (local only, reuse data_pipeline loading and
   preparation, never commit DFL data, tests skipped without it), `synthetic:SYN-001` (adapter written against the
   expected the match generator files, clear "not available yet" error, exact file format documented for the data lead; completing it is a
   pending step).
2. Replay clock with configurable speed (default 10x), start/pause; silver, gold and judge follow one stadium clock,
   each demo session has its own cursor, moved by the demo phase-jump endpoint.
3. Phase state machine from KickOff and FinalWhistle, reusing app/contract/phases.py.
4. The no-look-ahead window as the only access path: frames and events up to the replay instant, an event visible only
   after its frames, `as_of_s` on every response; one small, well-documented class to show to judges.
5. Deterministic stats with the metric names of the team's spec and the contract models: events (score, shots, cards, corners,
   fouls), frames when present (distance, top speed) via data_pipeline; xG and momentum null with reason "method
   pending the metrics review" unless Materials/METRICS.md is enough to implement simply.
6. A real FastAPI backend (`python -m app.engine`) that owns the match endpoints and delegates the others to the
   existing mock, so the app can switch with no change; tier rules from tickets.py.
7. Tests: truncation (fake and a small synthetic fixture with frames), no future data, events after their frames,
   phase order, demo jump only for demo tickets and per session, contract validation, determinism; keep the 72 tests.
8. Run the backend briefly: the minute advances and the phase changes via the demo endpoint.

## Result

- `app/engine/`: `window.py` (`MatchWindow`, `Snapshot`: the guarantee), `phases.py`, `clock.py` (stadium and demo
  clocks), `sources/` (`fake`, `dfl`, `synthetic` adapters and the canonical `MatchData`), `frame_stats.py` (metrics
  from frames with `insidethegame_data.prepare.prepare_half` and `metrics.player_summary`), `stats.py` (events and
  contract payloads), `engine.py`, `server.py` (engine endpoints + the mock for the rest), `pipeline.py` (imports
  data_pipeline through `sys.path`), `README.md` (run, sources, window, adapter interface, SYN-001 file format).
- Contract v0.2.1: frame-based metrics nullable, with the reason in a new `unavailable` list; fake data unchanged.
- `app/tests/test_engine.py` (19 tests) and `app/tests/engine_fixture.py` (a tiny match in the SYN-001 format);
  91 tests green in total. The DFL test runs only where the DFL files are.
- Choices: event buffer 5 s (minimum event search window of the guidelines); xG and momentum left null (the xG
  coefficients come from the calibration calibration); edges discovered per period on the released frames; live stats every
  60 match seconds with frames, computed in the background, never from a later instant.
