# statseat match engine

The engine replays a pre-generated match "as if live" (10x by default), knows the phase of the match, computes the
statistics with deterministic code and serves them with the models of the API contract (`app/contract/`, v0.3.0).
**Rule "no future":** the rest of the backend reads the match only through a window that releases frames and events
up to the current replay instant, and every event is processed only after its frames.

## Run

From the repo root (Python 3.12):

```bash
python3.12 -m venv app/.venv && source app/.venv/bin/activate   # once
pip install -r app/requirements.txt                              # once (now includes numpy, pandas, scipy, pyarrow)
python -m app.engine                                             # http://127.0.0.1:8000/docs
python -m pytest -q app/tests                                    # contract + engine tests
```

In `/docs`: log in with `POST /api/auth/login` (with SYN-001, the codes printed on the tickets in
[`synthetic/SYN-001/tickets/README.md`](../../synthetic/SYN-001/tickets/README.md); with `MATCH_SOURCE=fake`, the fake
codes of `app/contract/README.md`), press **Authorize** and paste the token. Loading SYN-001 takes about 30 s at
start-up; the first statistics of a whole half take a few seconds, then they are cached. `GET /api/engine/status` (no login) shows the stadium clock moving; with a demo code,
`POST /api/demo/phase` jumps between phases.

| Variable | Default | Meaning |
|---|---|---|
| `MATCH_SOURCE` | `synthetic:SYN-001` | `synthetic:<match_id>`, `dfl:<match_id>` or `fake` (below) |
| `ENGINE_SPEED` | `10` | replay speed: match seconds per real second |
| `ENGINE_PRE_MATCH_S` | `300` | match seconds of pre-match before kick-off (30 s at 10x) |
| `ENGINE_START_PHASE` | `pre_match` | where the stadium clock starts (`first_half`, `half_time`, ...) |
| `ENGINE_AUTOSTART` | `1` | `0` keeps the stadium clock paused |
| `ENGINE_EVENT_BUFFER_S` | `5` | an event is released this long after its time, once its frames are in |
| `ENGINE_STATS_TICK_S` | `10` (`60` with frames) | live statistics are computed every N match seconds |
| `PORT`, `HOST` | `8000`, `127.0.0.1` | server address |
| `STATSEAT_WEB_DIST` | `web/dist` | the built PWA (`npm run build`), served on the same origin as `/api` when it exists (the container of [`infra/`](../../infra/README.md)) |

**The app switches from the mock to this server without changes**: same paths, same models. The engine owns
`GET /api/match/state`, `GET /api/match/events`, `GET /api/stats/live`, `GET /api/stats`, `GET /api/recap`, its PDF
`GET /api/recap/pdf` (built by `app/report/` from the same recap, statistics and key moments), `POST /api/demo/phase` and `GET /api/health`; it adds `POST /api/demo/clock` (a demo ticket pauses or resumes its own
replay) and `GET /api/engine/status`. Everything else (login, session, My ticket, match overview, games, leaderboard,
Ask Archie) is still served by the contract mock, mounted in the same app and driven by the engine's phase. Each of
those endpoints moves to its own task without touching the app. Two exceptions with a real match
(not `fake`): **tickets** are the printed ones of `synthetic/<id>/tickets/tickets.json` (unless `STATSEAT_TICKETS_FILE`
says otherwise), and **highlights** are chosen by VAR in [`highlights.py`](highlights.py), because the mock's highlights tell
the fake story.

**Clocks.** Silver and gold tickets follow one stadium clock. A demo ticket follows it too until it uses the demo
panel; from then on it has its own replay cursor: a jump to `first_half` or `second_half` starts the replay at the
kick-off and it runs at `ENGINE_SPEED`, stopping on its own at half-time and full-time; `pre_match`, `half_time` and
`full_time` hold still.

## MATCH_SOURCE

| Value | What | Frames | Where it works |
|---|---|---|---|
| `synthetic:SYN-001` (default) | the synthetic match of the match generator, read through the data pipeline like any provider (below) | yes | everywhere (files and match profile are committed) |
| `dfl:J03WPY` (any DFL id) | real DFL/Sportec data through the data pipeline | yes | **only** where `data/raw/dfl/` has the XML files and `reports/qc/<id>/match_profile.json` exists (run `python -m insidethegame_data.qc <id>` once). Local development only, never in the app or the video |
| `fake` | the fake key events of `app/contract/fake/events.json` (34 events, the fake 2-1 story) | no | everywhere |

With `fake` the frame-based metrics (distance, speeds, possession, ball circulation...) are `null` and listed in
`unavailable` with the reason "needs tracking frames". The fake feed's own `xg` and `speed_kmh` qualifiers are dropped:
the engine never repeats a number it did not compute.

## The no-look-ahead window (show this to the judges)

All of it is one class, `MatchWindow` in [`window.py`](window.py) (about 100 lines with comments). The loaded match is
private to it; `MatchWindow.at(cursor_t)` returns an immutable `Snapshot` with only:

1. frames with time `<= cursor_t`;
2. events with time `+ ENGINE_EVENT_BUFFER_S <= cursor_t`: an event is released only after the frames that follow it
   (guidelines 9.3 step 7, "an event is never processed before its frames");
3. the phase, from a state machine fed only with the released `KickOff` and `FinalWhistle` events
   ([`phases.py`](phases.py); sections per phase from `app/contract/phases.py`);
4. data of a period only after its kick-off has been released, and no frames of a period after its final whistle;
5. `as_of_s`, the playing time of the instant (seconds since kick-off, first-half added time included), from the
   released whistles only. Every payload carries it.

Statistics (`stats.py`, `frame_stats.py`) and the API receive a `Snapshot`, never the match. `MatchWindow.check()`
re-checks rules 1, 2 and "frames before events" against the full match. Only the replay controller
(`clock.phase_starts`) reads the whistle times of the whole file, to know where to put the cursor for a phase jump; it
returns positions, never data.

Tests in [`app/tests/test_engine.py`](../tests/test_engine.py):
- **truncation**: the statistics at minute t are identical with the whole match and with a file cut at minute t, on
  the fake match and on a small synthetic match with frames (and a deliberately leaky window makes it fail);
- the window never returns a frame or an event after the cursor; an event appears only 5 s after its time, with its
  frames; phases follow the whistles in order (a kick-off after a goal changes nothing);
- demo jumps only for demo tickets and per session; contract models for every payload in every phase; two replays
  give identical payloads; live statistics never come from a later instant.

## Statistics

Names and models from `specs/METRICHE.md` and `app/contract/models.py`. A metric that cannot be computed is `null` (or
an empty list) and appears in `unavailable` with its reason (contract v0.2.1).

Methods: `Materials/METRICS.md` (decisions of 25 October). The pure functions live in
`data_pipeline/insidethegame_data/metrics.py`; `frame_stats.py` applies them to the released frames; every parameter
is in `PrepConfig` (`data_pipeline/insidethegame_data/config.py`). **Goalkeepers are out of every team total**
(distance and splits, speed zones, sprints, intensity, acceleration load, zone map, shape) and appear as players.

| Metric | From | Notes |
|---|---|---|
| score, shots, on target, penalties, corners, fouls, cards, offsides, woodwork | events | on target = goal or saved (never woodwork, blocked or wide); a second yellow counts as a yellow and a red; a penalty is a shot with `penalty: true` (DFL `Penalty` set piece) |
| shot map | frames (located kick) | each shot is located once: the shooter's best-scoring kick candidate of the pipeline within 5 s of the event (`metrics.shot_kick`: ground touch, header or soft kick he owns, as the calibration locates shots), else `frame_stats.kick_index` (the last frame in [t − 2.5 s, t + 1 s] with the shooter within 1.5 m of the ball), else the annotated position (counted in the Inspector); that frame gives the map position (ball, attack-normalised), the xG features and the shot speed. On the six DFL matches 138 of 144 open-play shots are located (130 by kick candidate), within 0.2 s of the calibration's frame for 123 of its 124 |
| xG (Analyst), goals vs xG (both) | frames | `metrics.shot_xg` = the calibration's `shot_features` + `xg` with the committed `calibration/xg_model.json` (visible angle, opposing outfield players with valid positions in the triangle); penalties 0.79; direct free kicks use the model; cumulative step series from 0 at 0'; goals vs xG = numbers only. `metrics.chance_word` gives big / decent / half chance (≥ 0.30, 0.10–0.30, < 0.10) for Archie and the Recap |
| passes and accuracy; average length and long balls (Analyst) | `Play` events; frames | length of the successful passes only (annotated complete): from the kick located by the pipeline's touches (`metrics.pass_reception`, best kick candidate, no alignment) to the team-mate's reception; long ball ≥ 32 m; a pass is measured once the frames of its reception search (10 s) are released |
| tackles won, ball recoveries | events + frames | tackle won = duel won by the defending player (`winner_role`); recovery = ball claimed or intercepted or tackle won, confirmed by a live possession change from the opponent to the team within 3 s (`metrics.possession_gain_frame`), one per change |
| counter-attacks | events + frames | recovery in the own half in live play, then a shot of the same team within 15 s, ball live and never the opponent's in between (`metrics.counter_attacks`); Analyst: the list |
| distance covered (with splits), top speed | frames, `insidethegame_data.prepare.prepare_half` + `metrics.player_summary` | validity mask, gaps, Savitzky–Golay, "≈" precision rule |
| speed zones (Analyst, teams and favourites), sprints | frames | FIFA zones on valid samples; sprint (`R-SPRINT`, `metrics.sprint_runs`) = at least 25 km/h for at least 1 s on valid, measured samples, merged when less than 1 s apart, never bridged |
| peak intensity | frames | rolling five-minute window, 1 s step, per player (`metrics.rolling_intensity`); a team = mean of its outfield players on the pitch for the whole window; series = the window ending at each minute; Analyst: each player's peak |
| acceleration load | frames | Σ \|dv/dt\| dT over valid samples with acceleration measured or approx, km/h; per minute played; Analyst: 5-minute series (teams, favourites) |
| possession, effective playing time | frames | provider ball possession and ball status, live samples |
| ball circulation | frames | median ball speed while live and in the team's possession, valid ball samples, without the 2 s after each shot and clearance (the adapter keeps the pipeline's clearance flag) |
| shot speed | frames | peak valid ball speed in the 0.5 s after the located kick; headers excluded. Since the event-time fix the SYN-001 event time is exactly the kick frame |
| momentum (both) | frames | last five minutes of the current period (from its kick-off at the start of a period): 0.5 × territory (possession time with the ball in the opponent's final third) + 0.5 × danger (xG share); no shot: territory only; no final-third time: danger only; neither: 0; one point per minute |
| zone map (Analyst, teams and favourites) | frames | 1 m grid of valid live samples, Gaussian σ 3 m reflected at the lines, 5 × 4 m display cells (21 × 17), attacking left to right |
| defensive line and team length (Analyst) | frames | per live frame with every outfield player valid: mean of the 2 deepest, robust length; 5-minute means |
| PPDA (Analyst) | events + frames | opponent passes at their located kick in the team's attacking 60 % per defensive action there (duels it defended, fouls, balls claimed); no action yet: no value, listed as `ppda.<TEAM>` |
| offside check (Archie only) | events + frames | [`offside.py`](offside.py): official offsides and goals, at the located pass frame; margin to max(second-last opponent, ball) with a 2 m band; never a statistic |

Series over time: 5-minute buckets end at 5', 10' … 45', then 50' … 90'; the last bucket of a half holds its added
time. Per-minute series (momentum, peak intensity) label added time as 45 or 90 and keep the latest value.

The engine reuses the data pipeline instead of copying it: [`pipeline.py`](pipeline.py) appends `data_pipeline/` to
`sys.path` (it is a plain folder with its own venv, not an installed package) and `app/requirements.txt` pins the
same library versions. A Docker image must copy `data_pipeline/` next to `app/`. Engine choices on top of the pipeline:
tracking-area edges are discovered on the released frames of each period (offline: on the whole match), and the
preparation runs on the frames released so far, so the last second of a live window can change once more frames arrive.

**Speed.** Statistics are cached per snapshot; a finished half is prepared once. With frames, live statistics are
computed every 60 match seconds in a background thread, and the fan gets the latest ready ones meanwhile (an earlier
instant, never a later one: `as_of_s` says which). On the real DFL J03WPY one period takes about 7 s to prepare (many
gaps to estimate); the synthetic fixture about 0.3 s. With the metrics work metrics, full-time statistics of SYN-001 take about
2.2 s (1.7 s before the metrics work); a DFL match about 5 s.

## Adapter interface

An adapter is a function that returns a `MatchData` ([`sources/base.py`](sources/base.py)); `sources/__init__.py`
maps `MATCH_SOURCE` to it. The shape is the same for every source:

- `events`: `Event(event_id, type, period, t, team_side, player_id, secondary_player_id, x, y, attrs)` sorted by `t`,
  DFL types in English, never `Delete`, `attrs` with English keys;
- `frames`: `{period: PeriodFrames}` wide arrays (frames x line-up players) with `t`, `frame`, `x`, `y`, ball `bx`,
  `by`, `bz`, `bstatus` (1 live, 0 dead), `bposs` (1 home, 2 away), or `None` for events-only sources;
- `lineup`, pitch size, `full_event_stream` (True when passes and duels are in the feed) and `notes` for the Inspector.

**Time** is one feed timeline in seconds, zero at the first-half kick-off, half-time break included. **Coordinates**
are metres from the centre spot, raw orientation with the home team attacking +x in the first half (guidelines section
4); payloads are attack-normalised as the contract says. A new provider needs only a new function: tidy its tracking
into the columns of `data_pipeline` and call `periods_from_tidy`.

## SYN-001 through the data pipeline

`synthetic:<id>` and `dfl:<id>` share one path ([`sources/provider.py`](sources/provider.py)): the pipeline loaders
return the same tables (`io_synthetic` for our generator's files, `io_dfl` for DFL XML), the committed match profile
`reports/qc/<id>/match_profile.json` gives the canonical transform, the kick-off of each period, the end of the first
half and the event clock offset, and one function maps DFL event attributes to the engine's English events. The
generator's format is described in [`synthetic/SYN-001/README.md`](../../synthetic/SYN-001/README.md); after a new
match is generated, run `cd data_pipeline && .venv/bin/python -m insidethegame_data.qc <id> --source synthetic` once to
write its profile.

| DFL in the feed | Engine event |
|---|---|
| `ShotAtGoal` with sub-type `SuccessfulShot`, `SavedShot`, `ShotWide`, `BlockedShot`, `ShotWoodWork`, `OtherShot` | `outcome` goal, saved, wide, blocked, woodwork, wide |
| set piece (`KickOff`, `FreeKick`, `CornerKick`, `ThrowIn`, `GoalKick`) with nested `ShotAtGoal[/<result>]` | the set piece **and** a `ShotAtGoal` (`<event_id>-shot`), so a free-kick goal counts; without a result: no `outcome`, counted as off target |
| `Play`, or a set piece with nested `Play` | `outcome` complete/incomplete from `Evaluation`, receiver from `Recipient` |
| `Foul` (`Fouler`, `Fouled`, `TeamFouler`), `TacklingGame` (`Winner`, `Loser`, `WinnerTeam`), `Caution` (`CardColor`), `Substitution` (`PlayerOut`, `PlayerIn`) | team, player and second player as the contract says; `card` yellow/second_yellow/red |
| `TacklingGame` `WinnerRole` (`withoutBallControl` / `withBallControl`; our generator `withoutBall` / `withBall`) | `winner_role` without_ball / with_ball (a tackle won is a duel won by the defending player) |
| `OtherBallAction` with `DefensiveClearance` (the pipeline's `events_sync.event_kind`) | `clearance: true` (the ball circulation leaves out the 2 s after it) |
| provider `xG`, `FinalWhistle` `FinalResult`, other attributes | dropped (numbers not computed by the engine; the final score is the future) |

The profile's event clock offset corrects a provider whose events are annotated late or early. Since the event-time fix the
generator writes every SYN-001 event at the exact time of its frame, so the offset is 0.00 s in both halves (it was
−0.9 s with the old 1 s annotation delay); each event is still released only 5 s after its corrected time, after its
frames. The generator's `true_frame` is never read. The tests run a small fixture in the same format with a
hand-written profile, plus one test on the committed SYN-001 (2-1, the 35.9 km/h scripted sprint, Hennebry's 88'
winner as the fastest shot).

## For the agents: where each "available after the metrics work" number now lives

`p` is the Data & Statistics payload the tools already build (`stats.match_stats(core, mode, ...)`); `card` is
`stats.player_focus(core, pid, analyzer)`. Fan/Analyst gating is already done in the payload: an Analyst-only field
is `null` in Fan.

- `shots.xg`: per shot `p["shot_map"]["shots"][i]["xg"]` (Analyst, null in Fan); per team `p["xg"]["teams"]` (Analyst).
- `expected_goals.xg`: `p["goals_vs_xg"]` (both versions: goals and xG per team); Analyst also `p["xg"]["teams"]`,
  `p["xg"]["cumulative"]` and `p["xg"]["method"]`; chance words: `frame_stats.metrics.chance_word(xg, frame_stats.CFG)`.
- `passes.avg_length_m` and `long_balls`: `p["passes"][i]["avg_length_m"]` and `["long_balls"]` (Analyst); per
  player `p["players"][i]["long_balls"]`.
- `team_stats.counter_attacks`: `p["counter_attacks"]["teams"]` (counts, both versions); Analyst also `["items"]`.
- `team_stats.ppda`: `p["ppda"]` (Analyst, list of team values); a team without a defensive action in its pressing
  zone is missing and listed in `p["unavailable"]` as `ppda.<TEAM>`.
- `speeds.acceleration_load`: `p["acceleration_load"]["teams"]` (`total_kmh`, `per_minute_kmh`, `reliability`);
  Analyst also `["series"]`.
- player `acceleration_load_kmh`: `card["acceleration_load_kmh"]` (both versions); `p["players"][i]` for the table.
- player `xg`: `card["xg"]` (Analyst, null in Fan); `p["players"][i]["xg"]`.
- `offside_check.verdict` and `margin_m`: `app.engine.offside.offside_check(snap, event_id, engine._prep)` returns
  `verdict` ("offside", "onside", "too close to call with our data" or None), `margin_m`, `pass_event_id`, `frame`,
  `minute`, `attacker_id` and `reason`; `event_id` is an official `Offside` event or a goal (`ShotAtGoal` with outcome
  goal) among `snap.events`; for a `minute` argument, pick that minute's `Offside` (or goal) event first. Archie never
  contradicts the referee (phrases in `Materials/METRICS.md` §26).
- `stats.LOAD_PENDING` stays defined for the tools; no engine payload uses it any more.

## Pending

- Crowd noise and pass network were dropped on 25/10.
- Highlights: VAR in [`highlights.py`](highlights.py), deterministic, from the released events, the located shots
  (xG) and the ball's tracking: candidates, merging, score and English labels of `specs/VAR.md`; `/api/highlights` lists
  the moments with a score of at least 45, the recap's `highlight_ids` every goal and then the best, up to 10.
  `importance` = score / 156 x 100 (156 is the highest score of the formula), the raw score is in the Inspector.
  Thresholds are constants at the top of the module (`BIG_SAVE_XG` 0.12, `BIG_CHANCE_XG` 0.20). No model call.
  With `MATCH_SOURCE=fake`, the mock's highlights (fake story).
- The recap narrative is a factual summary that the code writes from the numbers (no model call).
- One process, state in memory (as the MVP plan says): sessions and demo clocks are lost on restart.
