# Metrics in the code (xG, momentum, rolling peak, acceleration load, offside check)

- **Date:** 2026-10-26 (night)
- **Tool:** Claude Code (Claude Opus 5.5) with the `data-analyst` subagent (`.claude/agents/data-analyst.md`); plan
  approved by the team before execution
- **Area:** `[DATA > Metrics]`

## Prompt (English version of the team prompt)

> Use the data-analyst subagent. Read Materials/METRICS.md (the "Decisions" table and sections 1-26) and the app
> metrics table (what Fan and Analyzer show). Write the metrics in data_pipeline/insidethegame_data/metrics.py,
> deterministic and with one test each, and wire them into the engine (app/engine/stats.py and frame_stats.py) in the
> order of the metrics work. The xG model is ready: use calibration/xg_model.json as it is, with penalties at 0.79. For
> offsides (section 26), try offside_check on the 13 DFL offsides and tell me how many come out "offside" and how many
> "too close to call". Fill the fields of contract v0.3.0 (app/contract/models.py), already in place with fake data:
> do not change their shape; if a field is really missing, ask me first. Remove a field from "unavailable" when you
> fill it. No DFL data in the repo. At the end: green tests, no "Not available" on SYN-001 for the metrics written,
> update the task list and the plan, save the prompt in prompts/, commit without push.

During the night the team lead removed the planned pause after block 3 ("go on to the end; the synthetic match is
regenerated tomorrow and we re-check then").

## Plan approved by the team (summary)

- **Order of the blocks** (one commit each, so finished blocks are usable if time runs out):
  1. xG of every shot and cumulative xG;
  2. corrections between code and decisions (located shot map, length of successful passes and long balls,
     goalkeepers out of team totals, rolling 5-minute peak per player, sprints merged under 1 s, ball circulation
     without the 2 s after shots and clearances, shots on target without woodwork);
  3. goals vs xG and momentum;
  4. acceleration load, counter-attacks, the offside check for Archie, the rest of Player Focus and the players table;
  5. zone map, defensive line, PPDA, tackles won and ball recoveries.
- **Agreement with the agents task:** the metrics work fills the engine and writes the offside function plus a note on
  where each "available after the metrics work" number lives; it never touches `app/agents/`. It reaches `main` with all tests
  green; an app test broken by the metrics work is fixed inside the metrics work with the smallest change to an expectation, never to the tools.
- **Rules given to the subagent:** work in a separate git worktree on its own branch; the contract shape never
  changes; edits with the Edit tool; no look-ahead (the engine sees only the released frames and events); the Fan
  version never receives an Analyzer-only number; DFL data only locally and only as aggregates.
- **Checks:** the xG sum on the six DFL calibration matches returns to 13.0; no xG outside 0-1; momentum within
  [-1, 1]; two runs give the same numbers; the real server with a demo ticket, phase by phase, shows no "Not
  available" for the metrics written.

## Follow-up prompt (after the first checks)

> Bounded follow-up: locate each shot's kick with the pipeline's kick candidates, as for the pass kick, and keep it
> only if the DFL located share rises and the xG over the calibration's 124 shots gets closer to 13.0. Diagnose the
> two official DFL offsides that came out "onside": fix only a clear identification bug, with a unit test, never the
> 2 m band or the definition.

## Result

- **Metrics:** every field of contract v0.3.0 is filled on SYN-001, in both versions and in every phase.
- **Offside check:** `app/engine/offside.py`; the note for the first Foundry integration is in `app/engine/README.md`.
- **Tests:** pipeline 238, generator 159 (1 skipped), app 138, web 27.
- **Checks:**
  - DFL xG: 13.01 over the 124 calibration shots (13 goals); 138 of 144 shots located on the tracking.
  - DFL offsides (13 official): 1 offside, 9 too close to call, 0 onside, 3 not checkable.
  - Two runs give identical payloads.
  - Full-time statistics take about 2 s.
- **Open for the team lead:** 21 implementation choices, listed in the team's plan as proposals.
