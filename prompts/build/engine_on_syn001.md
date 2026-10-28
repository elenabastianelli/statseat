# The match engine on SYN-001

- **Date:** 2026-10-24
- **Tool:** Claude Code (Claude Opus 5.5)
- **Area:** `[ENGINE > Synthetic data]`

## Prompt (English version of the team prompt)

> SYN-001 from the match generator is in synthetic/SYN-001/. Start the engine with MATCH_SOURCE=synthetic:SYN-001
> (app/engine/sources/synthetic.py) and compare the files with the format of app/engine/README.md: fix the adapter or
> tell me what to change in the generator. Then run the tests, try /docs from pre-match to full time with a demo code,
> check that the statistics are plausible (distances, speeds, possession) and update the task list and the plan.

Decided with the data lead before starting: neither a converter in `generator/` nor a second format in the engine. The engine
reads SYN-001 **through the data pipeline**, like DFL data: the pipeline's `io_synthetic` adapter returns the same tables as
`io_dfl`, the committed match profile gives the transform, kick-offs and event clock offset, and one mapping turns
DFL attributes into the engine's events. Tickets: the server reads the generator's `tickets.json` as it is (SHA-256
of the normalised code), because its codes are derived from public inputs and a salt would add no secrecy.

## Result

- `app/engine/sources/provider.py`: shared pipeline tables -> `MatchData` for `dfl:<id>` and `synthetic:<id>`; set
  pieces with a nested shot also yield a `ShotAtGoal` (free-kick goals count); provider xG dropped.
- `app/engine/sources/synthetic.py` and `dfl.py`: thin loaders on top of it; `synthetic:SYN-001` is the default source.
- `app/engine/frame_stats.py`: the kick of a shot is located on the tracking (shooter within 1.5 m of the ball near
  the annotated time), because events are annotated about 1 s late. 21 of 23 SYN-001 kicks within 0.16 s of the truth.
- `app/engine/server.py`: printed tickets of the synthetic match; empty highlights with a real match until VAR.
- `app/contract/tickets.py`: the registry also reads the generator's `tickets.json` (Crockford normalisation).
- Tests: the engine fixture rewritten in the generator's format with a hand-written profile (truncation still
  passes), a test on the committed SYN-001 (2-1, 36.0 km/h sprint, Hennebry's 88' winner as fastest shot), tests of
  the printed tickets. 101 tests.
- Checked in headless Chrome at phone size with a printed DEMO ticket in every phase, and the ticket image in My ticket.
