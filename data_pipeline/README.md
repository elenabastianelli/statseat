# data_pipeline

Deterministic, tested Python for loading and preparing football tracking and event data, starting with the DFL/Sportec open data
(Bassek et al., 2025, *Scientific Data* 12:195, doi:10.1038/s41597-025-04505-y, CC-BY 4.0).
All rules come from [`docs/DATA_ANALYSIS_GUIDELINES.md`](../docs/DATA_ANALYSIS_GUIDELINES.md) **v4.2** and the limits from
[`Materials/PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md). Every rule has an ID (for example `R-GOAL`): the code, the
preparation log, the reports and the test names use it.

## Set up (once)

```bash
cd data_pipeline
python3.12 -m venv .venv          # Python 3.12 is required (system python3 may be older)
.venv/bin/pip install -r requirements.txt
```

`.venv/` is git-ignored.

## Prepare one match

Put the three XML files of the match in `data/raw/dfl/` (git-ignored), then:

```bash
cd data_pipeline
.venv/bin/python -m insidethegame_data.qc J03WPY
```

It takes about 20 s for a full match once the tracking cache exists; the first run of a match also parses the 400 MB XML and writes
a Parquet cache (about 1-1.5 minutes more). Options:

| Option | What it does |
|---|---|
| `--reports-dir DIR` | write the report folders to `DIR/<id>/` instead of `reports/qc/<id>/` (trial runs; frame-level files then go to `DIR/frames/<id>/v4.2/`) |
| `--frames-dir DIR` | put the frame-level outputs elsewhere (keep it git-ignored) |
| `--no-frames` | skip the frame-level outputs |
| `--overwrite-frames` | replace existing frame-level outputs (ask the team first) |
| `--no-acc-window-test` | skip the acceleration window sensitivity test (`R-ACC-WINDOW-TEST`) |
| `--no-kloppy` | skip the kloppy cross-check |
| `--synthetic-dir DIR` | with `--source synthetic`: read the match folder `DIR` instead of `synthetic/<id>/` (for example a replica in `synthetic/replicas/`) |

| Path | Content | In git? |
|---|---|---|
| `reports/qc/<id>/README.md` | preparation report (section 13): a short **To review** list, match profile, preparation log, exclusions and gaps, acceleration band, goals, shots, passes, clearances, provider comparison, anomaly catalogue, parameters and hashes | yes (aggregates only) |
| `reports/qc/<id>/match_profile.json` | discovered match profile (section 3), including tracking-area edges, parking values and `ball_height_supplied` | yes |
| `reports/qc/<id>/qc_results.json` | preparation log, mask and gap summary, per-player summary, events (goals, shots, passes, clearances), acceleration band and window test, input hashes, configuration, library versions | yes |
| `reports/qc/<id>/acc_window_sensitivity.csv` / `.md` | `R-ACC-WINDOW-TEST` table for this match (13, 19, 25 samples) | yes |
| `reports/qc/frames/<id>/v4.2/` | `tracking_canonical.parquet` (every sample with `valid_kinematics`, `exclusion_reason`, `estimated`, `discarded`, `acc_status`, our speed and acceleration), `anomaly_catalogue.csv` (with the provider events near each unknown jump), `events_canonical.csv` (every event with its `sync_*`, `type_check` and outcome columns), `goal_crossings.csv` | **no** (git-ignored) |
| `reports/qc/frames/<id>/id_labels.json` | real data only: the anonymous label of each person (`home CB 1`) and its provider object ID (`R-ANON-LABELS`) | **no** (git-ignored) |
| `reports/qc/SUMMARY.md` | comparison across matches, with the acceleration window sensitivity table | yes (aggregates only) |
| `data/interim/dfl/<id>/*.parquet` | tidy tracking cache | **no** (git-ignored) |

For real data the committable files contain **no provider object IDs**: people are anonymous labels such as `home CB 1` (team side, role
group, number in ID order); the label table stays in the git-ignored `id_labels.json`. Synthetic matches keep their fictional IDs.

Outputs contain no wall-clock time: two runs on the same inputs and configuration are byte-identical. Frame-level files and the cache are
never overwritten silently. v4.2 writes its frame-level files to `v4.2/`, so the files of earlier versions are kept.

The block between `<!-- analyst-notes:start -->` and `<!-- analyst-notes:end -->` in a report is kept when the report is regenerated.

## The six DFL matches, the summary and the sensitivity tables

```bash
cd data_pipeline
for m in J03WMX J03WOH J03WOY J03WPY J03WQQ J03WR9; do .venv/bin/python -m insidethegame_data.qc "$m" || break; done
.venv/bin/python -m insidethegame_data.summary        # DFL matches prepared with v4.2 -> reports/qc/SUMMARY.md
```

The summary lists only DFL matches prepared with the current guidelines (pass match IDs to choose them; a match still prepared with
an older version is refused with a message). Each match's sensitivity table is in `reports/qc/<id>/acc_window_sensitivity.md`;
the summary has the table for all matches.

## What v4.2 checks (section 9, events located from trajectories)

The annotated event is never changed; our checks add columns next to it.

| Rule | In short |
|---|---|
| `R-GOAL`, `R-GOAL-FREEZE` | a ball entering between the posts and under the bar (a ball already out over the goal line stays out until it is back on the pitch for 0.2 s) is a **goal** when the next restart is a kickoff, or with no restart and no VAR / disallowed-goal / foul / offside event before the final whistle (`confirmed_by = end_of_period`); a **disallowed goal** when another restart follows (with `disallowed_reason` from `GoalDisallowed`); otherwise **unconfirmed**. A ball frozen by the provider just before the line is extrapolated for at most 0.08 s. The goal shot is searched backwards up to the crossing frame; a defender's deflection of a wide shot is an own goal. Penalties and direct free kicks are matched at the restart's kick and stay out of the open-play shot checks and of xG |
| `R-TOUCH`, `R-TOUCH-NEAREST` | a kick belongs to the nearest player or one within 0.3 m of the nearest; a ground touch needs the ball at or below 1.5 m |
| `R-AERIAL`, `R-SHOT-HEADER` | aerial touches (ball above 1.5 m on its incoming path, 1.6 m for shots, direction change >= 10 deg, \|a\| peak >= 2 m/s2): headers are located |
| `R-SHOT-SOFT`, `R-SHOT-SPLIT` | soft kicks (placed shots) and a block 0.08 s after the kick |
| `R-SHOT-DIRECTION`, `R-SHOT-NOT-PASS`, `R-SHOT-POWER`, `R-SHOT-OUTCOME`, `R-WOODWORK`, `R-SHOT-CHECK` | direction over the whole free flight (within 6 m beyond a post, 8 m from outside the box or above the power threshold), not a pass to a team-mate who receives it, power above the DFL pass p99 confirms a shot (not headers), a flight away from the goal with an opponent within 1 m at the kick is blocked, woodwork from the ball's path, then the decision table |
| `R-LATE-ANNOTATION`, `R-DUEL` | a search window that is mostly dead ball extends 20 s back; an aerial duel won with a shot may share the shot's frame |
| `R-PASS-KICK`, `R-PASS-OUTCOME` | a located pass is confirmed by its kick; its outcome (team-mate, opponent, out of play, open space) is kept apart |
| `R-CLEARANCE` | a located clearance needs 4 of 7 conditions (C4: an opponent within 4 m) |
| `R-LIVE-ONLY`, `R-LIVE-BUFFER` | live play only (the ball inside the goal is the exception); in live mode an event waits for its frames and forward checks stay `pending` |

The `R-SHOT-POWER` threshold is read from `calibration/ball.json` (`pass_speed_ms.p99`, 94.86 km/h in the v4 calibration); without it the
configuration fallback (94.86 km/h) is used, and the report says which one.

## Prepare a synthetic match

Our own synthetic matches go through **the same code path** (guidelines sections 2.3 and 12). The generator writes a folder `synthetic/<match_id>/`:

| File | Content |
|---|---|
| `match_info.json` | DFL `matchinformation` structure in English: teams, line-ups with English positions, pitch size, planned kickoff |
| `positions.parquet` | one row per object per frame: `period`, `frame` (period 1 from 10000, period 2 from 100000, 25 Hz), `time_utc` (UTC), `object_id` (universe player id or `BALL`), `object_type` (`player`/`ball`), `team_side` (`home`/`away`/`none`), `x_m`, `y_m`, `z_m` (centre origin, metres; `z_m` ball only), `ball_status` (`live`/`dead`), `ball_possession` (`home`/`away`/`none`) |
| `events.json` | `{"match_id", "universe_id", "generator_version", "seed", "events": [...]}`; each event has `event_id`, `event_type` (DFL taxonomy, never `Delete`), `sub_type`, `time_utc` (annotation time), `period`, `team_side`, `player_id`, `x_m`, `y_m`, `attributes` (DFL names) and `true_frame` (generator truth, **tests only**) |

```bash
cd data_pipeline
.venv/bin/python -m insidethegame_data.qc SYN-001 --source synthetic
.venv/bin/python -m insidethegame_data.qc SYN-001 --source synthetic --frames-dir ../data/interim/qc_frames/SYN-001   # frame-level outputs elsewhere (git-ignored)
.venv/bin/python -m insidethegame_data.qc SYN-001-r1 --source synthetic --synthetic-dir ../synthetic/replicas/SYN-001-r1 \
    --reports-dir ../reports/qc/frames/_realism/reports/qc                                                        # a replica, outside synthetic/<id>/
```

`formats.detect_format` recognises the folder ("InsideTheGame synthetic", adapter `io_synthetic`). `io_synthetic.py` returns the same tables as the DFL loader:
- team sides become team IDs, and live/dead and home/away become the DFL codes;
- the DFL attributes the pipeline reads are filled **only when missing**: `Team`/`Player` from `team_side`/`player_id`, `GameSection` from the period, and kickoff `TeamLeft`/`TeamRight` from the goalkeepers' mean x (logged);
- `true_frame` becomes the tests-only `provider_calculated_frame`, like DFL `CalculatedFrame`, and never locates anything;
- the origin, direction, clock offset, edges and every other property are then discovered as for any provider.

The report says "InsideTheGame synthetic match" in its attribution. There is no kloppy cross-check (kloppy has no reader for our format).

## Calibration files for the generator

After preparing the DFL matches (frame-level outputs in `reports/qc/frames/<id>/v4.2/`, plus the git-ignored `id_labels.json` that maps
the anonymous labels of the committed profiles back to the IDs, in memory only):

```bash
cd data_pipeline
.venv/bin/python -m insidethegame_data.calibration            # every DFL match with frame-level outputs (synthetic matches are never used)
.venv/bin/python -m insidethegame_data.calibration J03WPY     # or a subset
```

It takes about 15 s for the six matches. It writes aggregate-only JSON files to `calibration/` at the repo root (committed, with CC-BY attribution).
- Every file has a `meta` block: source and DOI, matches used, configuration and hash, SHA-256 of the input files, library versions, pipeline version, conventions and `notes` with the n behind every number and its caveats.
- There is no timestamp, so two runs are byte-identical.
- Events located by any kick method (ground touch, aerial touch, soft kick, goal backwards) count as located; the accelerations of `noise` samples are never used.

| File | Content |
|---|---|
| `physical_profiles.json` | per role group (GK, CB, FB, DM, CM, AM, W, ST; LM/RM are in W):<br>- distance per 90 and its splits (ball in/out of play, in/out of possession)<br>- distance per FIFA speed zone, top speed, sprints (> 25 km/h for >= 1 s), acceleration peak<br>- distance per 5 minutes (fatigue)<br>STATS = n, mean, sd, p10-p90 over player-matches with >= 60 valid minutes. **Per 90 of measured (valid) time** |
| `position_maps.json` | mean position per role conditioned on the ball cell (12 x 8) and on possession:<br>- team-attacking coordinates (attacks +x, left is +y)<br>- mirror-pooled (RB -> LB, ...)<br>- cells with < 50 samples use the role's mean plus the role group's shift; `source` says which |
| `event_model.json` | event frequencies per 90 per team; pass length, success and direction by zone (6 x 4); zone transitions; shots (rate by zone, outcomes, headers); set pieces; possession; ball-in-play share; fouls, cautions |
| `ball.json` | pass, long-pass and shot speeds (our kinematics; shot speeds without headers), circulation speed, progression speed, air time of high passes; `pass_speed_ms.p99` is read by `R-SHOT-POWER` |
| `xg_model.json` | logistic xG (`Materials/METRICS.md` section 9, v4.2): `logit(xG) = a + 2.84 * visible angle + b * opponents in the shot triangle`, the angle slope fixed (Soccermatics, open Wyscout data), only a and b fitted on DFL; penalty 0.79; n, calibration table, leave-one-match-out check, checks and examples. Same `features` / `coef` / `intercept` format as before (`coef` = [0, 2.84, b]). Use `calibration.xg(...)` |
| `attribute_scales.json` | tables to map 1-99 attributes (pace, acceleration, stamina, work rate, passing, shot power) to physical values and back (`attribute_to_value`, `value_to_attribute`). v4.2: each table stores only 21 quantiles (p2.5 ... p97.5, no minimum or maximum); the 99 `values` the generator reads are interpolated from them, flat in the tails; shot power from shot speeds without headers |
| `artefact_profile.json` | provider artefact rates per player-hour (jumps, swaps, frozen runs, edges, overlaps, speed-limit episodes), identity inheritance at substitutions, event clock offset and dispersion; `acc_band`: `R-ACC-BAND` acceleration spikes (`noise` / `approx` runs by sign, per player-hour and mean run length, per match and pooled) |
| `realism_reference.json` | the DFL reference of the Linesman realism report: for each of the 107 metrics of `realism_metrics.py`, 21 quantiles (p2.5, p5, p10, p15, ..., p90, p95, p97.5; **no minimum and no maximum**; known limit, accepted, guidelines section 19: a quantile at a whole-number position can coincide with one player-match value), `n` and the range of the per-match medians (no person or object IDs; match IDs only in `meta`); physical metrics by role group GK, CB, FB, MF (DM + CM + AM), W, ST |

`--only FILE ...` writes only the named files (every file is still computed). To add or refresh the realism reference without touching the other
seven files, from the qc outputs in the Linesman work folder:

```bash
cd data_pipeline
.venv/bin/python -m insidethegame_data.calibration --repo ../reports/qc/frames/_realism --out ../calibration --only realism_reference.json
```

## Linesman: realism report of a synthetic match

The Linesman check (guidelines section 12) compares a synthetic match and its replicas with the six DFL matches, **through the same
preparation pipeline and the same metric code** (`realism_metrics.py`) on both sides.

```bash
cd generator && .venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6 --only-replicas   # replicas, git-ignored (about 25 s each)
cd ../data_pipeline
for m in J03WMX J03WOH J03WOY J03WPY J03WQQ J03WR9; do                                             # DFL reference (only to rebuild it)
  .venv/bin/python -m insidethegame_data.qc "$m" --reports-dir ../reports/qc/frames/_realism/reports/qc; done
.venv/bin/python -m insidethegame_data.calibration --repo ../reports/qc/frames/_realism --out ../calibration --only realism_reference.json
.venv/bin/python -m insidethegame_data.realism SYN-001 --replicas 6                                # the report
```

The last command prepares `SYN-001` and `SYN-001-r1..r6` with `qc` into the git-ignored work folder `reports/qc/frames/_realism/` (about
10 s each; a match already prepared there is reused, delete its folders to prepare it again), computes every metric and writes:

| Path | Content | In git? |
|---|---|---|
| `reports/realism/README.md` | the report: a traffic-light table per metric group, a few sentences per group, the ranked list "What to improve in the generator" | yes |
| `reports/realism/realism.json` | every number: per metric the synthetic and DFL medians, the KS distance D (pooled and match alone), its chance level, the median rule, the light; mean positions per role; shot outcome diagnostics | yes |
| `reports/realism/figures/*.png` | one figure per metric group plus the mean positions per role | yes |
| `reports/qc/frames/_realism/` | the qc outputs of the DFL and synthetic matches used by the report | **no** (git-ignored) |

The rules: one-sample KS distance D of the synthetic sample against the DFL CDF interpolated linearly between its 21 quantiles (0 below p2.5 and
1 from p97.5; against the full DFL distribution D can differ by at most 0.025 in the tails and 0.05, the spacing of two stored levels, in between),
and the position of the synthetic median relative to the range of the DFL per-match medians. **Green** = D <= 0.15 and the median inside; **yellow** = D in (0.15, 0.30] or
the median just outside (within 10 % of the range width); **red** = D > 0.30 or the median far outside. Metrics with one value per match or per
team-match (ball in play, possession, counts per team, shot outcomes) use the median rule only. Options: `--replicas N`, `--work-dir`,
`--reference`, `--out`, `--no-qc` (fail instead of preparing a missing match). From Python, `realism.realism_report(match_id, replicas, ...)`
returns the same dictionary as `realism.json` (a ready tool for a Foundry Linesman agent). Two runs give byte-identical files.

## Tests

```bash
cd data_pipeline
.venv/bin/python -m pytest -q
```

The tests use only small synthetic fixtures; they never use real data. Each test of a rule carries its ID in its name
(for example `test_r_parking_shared_value_is_discovered_and_a_single_frozen_player_is_not`).
- `tests/synthetic.py`: a 100 x 60 m pitch with a corner origin, in DFL format;
- `tests/synthetic_itg.py`: a 100 x 64 m match in our synthetic folder format, with a pass, a goal followed by a kickoff, a 0.3 s annotation delay and kickoffs without sides;
- `tests/arrays.py`: tiny trajectories with hand-computed answers;
- `tests/scenes.py`: tiny event scenes (goals, headers, soft kicks, blocks, woodwork, clearances, live mode) for `tests/test_events_sync.py` and `tests/test_clearance.py`;
- `tests/test_calibration.py`: hand-computed calibration values;
- `tests/test_realism.py`: the Linesman (KS distance from quantiles against SciPy and hand-worked cases, median rule, traffic light, rolling
  peak and acceleration counts, the qc `--synthetic-dir` override, an end-to-end run on `synthetic_itg.py` with its determinism, and no person
  or object IDs in `realism_reference.json`).

## Package layout (`insidethegame_data/`)

| Module | Role |
|---|---|
| `config.py` | every parameter (guidelines section 15), with a configuration hash; the provisional values of v4 were reviewed in v4.2 |
| `anonymise.py` | `R-ANON-LABELS`: anonymous labels for the reports of real data and the git-ignored label table |
| `formats.py` | format detection, unit inference, a second tiny adapter (long CSV) |
| `io_dfl.py` | DFL XML loaders (SI units, UTC, `Delete` dropped), Parquet cache |
| `io_synthetic.py` | adapter for our synthetic match folders (same tables as `io_dfl`; missing DFL attributes filled and logged; `true_frame` for tests only) |
| `mapping.py` + `../reference/dfl_to_english.yaml` | reviewed DFL -> English mapping (roles, formations, values, shot outcomes) |
| `tidy.py` | `R-SORT`, `R-DEDUP`, `R-RESAMPLE`, `R-MISSING` |
| `profile.py`, `tables.py` | match profile (sampling, frame ranges, pitch, origin, direction, transform, playing time) |
| `edges.py` | tracking-area edges (`R-EDGE`) and parking values (`R-PARKING`), discovered per match |
| `prepare.py` | swaps, frozen tracks, edges, parking, gaps, substitutions (identity inheritance), exits/re-entries, jumps, overlaps, speed limit, validity mask, acceleration band (`acc_status`), Savitzky-Golay kinematics, ball windows |
| `gaps.py` | gap estimation (spline, Kalman/RTS, straight line), only when the player resumes < 6 m away |
| `acc_window.py` | `R-ACC-WINDOW-TEST` (13, 19, 25 samples) and its table |
| `timealign.py` | kickoff per period, tracking start offset, event clock offset, restarts and substitutions located on the tracking |
| `touches.py` | ground and aerial touches, soft kicks, kicks, next touch, woodwork contacts (live play only) |
| `goals.py` | `R-GOAL`: crossings, the forward look to the next restart, the goal shot backwards |
| `outcomes.py` | free flight, shot direction, team-mate check, power, outcome and decision; pass outcome; clearance conditions |
| `events_sync.py` | the event driver: candidates, order-preserving alignment, location from the synchronised frame, type checks, live buffer; aggregates for the report |
| `jump_context.py` | `R-JUMP-CONTEXT`: provider events within +-2 s of each unknown jump |
| `metrics.py` | per-player summary (distance with splits and "≈", speed zones, sprints, acceleration peaks with "≈"), shot statistics, provider comparison (tests only) |
| `report.py`, `summary.py`, `qc.py` | preparation report, cross-match summary and the command-line entry point (`--source dfl` or `synthetic`) |
| `calibration.py` | calibration aggregates for the generator (guidelines section 11) from the frame-level outputs; `xg()`, `attribute_to_value()`, `value_to_attribute()` |
| `realism_metrics.py` | the Linesman metrics, one code path for DFL and synthetic matches (physical by role group, ball and play, passes, shots, events, team shape) and the DFL reference |
| `realism.py`, `realism_output.py` | the Linesman: KS distance from quantiles, median rule, traffic light, `realism_report()` and CLI; figures and the README |
| `kinematics.py`, `segments.py`, `crosscheck.py` | Savitzky-Golay helper (Butterworth kept only to repeat the filter comparison for a new provider), run-length helpers, kloppy cross-check |

The v2 modules (`checks.py`, `derived.py`, `anomalies.py`, `plots.py`) were removed in v4 (`R-LEGACY-CLEANUP`).
