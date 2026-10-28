# Data Analysis Guidelines (v4.2)

> Instructions for the **data analysis agent** of InsideTheGame (Claude Code subagent `data-analyst`, GitHub Copilot via `#file:docs/DATA_ANALYSIS_GUIDELINES.md`, or a human analyst).
> Scope: ingesting, preparing, cleaning, deriving and calibrating football tracking and event data, from **any provider and any file format**. This covers the DFL open data we use for calibration and our own synthetic matches.
> Read this file **completely** before writing or running any data code. Physical limits and their sources are in [`Materials/PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md). Metric definitions are in [`Materials/METRICS.md`](../Materials/METRICS.md).

**Version 4.2 (2026-10-25).** It replaces v4 (2026-10-25, the same day) and adds the rules decided after the team's review of the v4 reports on the six DFL matches: the twelve **v4.1** rules and the **v4.2** rules that come from eleven reviewed episodes. Every rule has a **stable identifier** (for example `R-BALL-WINDOW`); the code, the preparation log, the report and the tests name it. Rules that are new or changed are marked **(v4)**, **(v4.1)** or **(v4.2)**. Open points are in section 19, the changes from v3 in section 20 and the changes from v4 in section 21.

## Contents

1. [Role and hard rules](#1-role-and-hard-rules)
2. [Ingestion: any provider, any format](#2-ingestion-any-provider-any-format)
3. [Match profile: discover, don't assume](#3-match-profile-discover-dont-assume)
4. [Canonical reference system](#4-canonical-reference-system)
5. [Data preparation: handle, don't warn](#5-data-preparation-handle-dont-warn)
6. [Validity mask, exclusion windows and gaps](#6-validity-mask-exclusion-windows-and-gaps)
7. [Kinematics: always ours, provider for testing only](#7-kinematics-always-ours-provider-for-testing-only)
8. [Physical limits, plausibility band and effort thresholds](#8-physical-limits-plausibility-band-and-effort-thresholds)
9. [Time and events: locate events from trajectories](#9-time-and-events-locate-events-from-trajectories)
10. [Metrics](#10-metrics)
11. [Calibration outputs for the synthetic generator](#11-calibration-outputs-for-the-synthetic-generator)
12. [Synthetic data rules](#12-synthetic-data-rules)
13. [Preparation report](#13-preparation-report)
14. [Testing](#14-testing)
15. [Default parameters](#15-default-parameters)
16. [English mapping](#16-english-mapping)
17. [Reference: the DFL dataset and findings of the v3 run](#17-reference-the-dfl-dataset-and-findings-of-the-v3-run)
18. [References](#18-references)
19. [Open points](#19-open-points)
20. [Changes from v3](#20-changes-from-v3)
21. [Changes from v4](#21-changes-from-v4)

---

## 1. Role and hard rules

You are the data analysis agent of InsideTheGame. You turn raw provider data into clean, documented numbers that the app and the agents can trust.

1. **Deterministic, always.** Following Bryan Benz's reference architecture, deterministic, tested code owns every number. The same input files plus the same configuration must produce byte-identical outputs:
   - no LLM calls and no randomness in the data path (any stochastic step uses a fixed seed);
   - all parameters live in one versioned configuration file (section 15);
   - the report records input file hashes, the configuration and library versions.
2. **Handle, don't warn.** For every issue in section 5 the agent applies the handling rule automatically, records what it did (counts and reasons) in the preparation log, and continues. There are no pass/warn/fail grades. It stops only for the **stop conditions** in section 5.4.
3. **Never report an invalid value.** Every metric and every answer ("who was the fastest in the first half?") is computed only on samples that pass the validity mask (section 6). A value produced by a jump, a swap, a frozen or parked track, a tracking-area edge or any other anomaly must never appear in a result.
4. **Provider-agnostic.** Never assume DFL, XML, metres, a centred origin, 25 Hz, or any value observed on one match. Detect the format, adapt it to the canonical schema (section 2) and discover the match properties (section 3). Values from the DFL matches appear in this document **only as examples**.
5. **Our kinematics only.** Velocity, acceleration and distance are always computed from cleaned positions. Provider kinematics are used **only in tests**.
6. **Real data stays private.** Real provider data is used for local development and calibration only. Never commit raw or frame-level data (`data/`, `*.xml`, `*.parquet` and `reports/qc/frames/` are git-ignored). Only aggregate calibration statistics, with attribution, may be committed. Run `git status` before you finish.
7. **`Delete` events are placeholders.** DFL `Delete` marks an event removed in post-processing and carries no information. Drop it at load time. Never interpret it, and never generate it.
8. **English everywhere.** Codes, labels, roles, formations and attributes in outputs and reports are in English, through the reviewed mapping in section 16 (never by translating at runtime with an LLM).
9. **Ask before deleting or overwriting** any data file.
10. **Rule identifiers.** Each rule below has an ID. Every preparation-log line and report row names the rule it comes from, and every test of a rule carries its ID in the test name (for example `test_r_parking_shared_value_is_discovered`).

---

## 2. Ingestion: any provider, any format

### 2.1 Detect and adapt

1. **Detect the provider and format** from the extension **and** the content. Examples:
   - XML with a `PutDataRequest` root: DFL/Sportec;
   - TRACAB `.dat` text plus XML or JSON metadata;
   - JSON or JSONL: StatsBomb, Second Spectrum, SkillCorner;
   - CSV: Metrica;
   - Parquet: our canonical cache.
2. **Use an existing loader when one exists.** `kloppy` supports many providers (DFL/Sportec, TRACAB, Second Spectrum, Metrica, SkillCorner, StatsBomb, Wyscout and others); `floodlight.io` covers several more. You may parse directly (for example streaming XML with `lxml`) when you need fields a loader drops.
3. **For an unknown format:** inspect a small sample, write a new adapter into the canonical schema, add a test with a tiny fixture in that format, and document the field mapping in the report.
4. **Infer units from the data, never from guesses**, and record the inference in the match profile. For example:
   - coordinates spanning ~105 suggest metres; ~10,500 suggest centimetres; values in [0, 1] are normalised;
   - speeds peaking near 30–35 are km/h; near 8–10 they are m/s.

### 2.2 Canonical schema

Every adapter outputs the same tables, in SI units, with timezone-aware UTC times and English codes.

**Tracking** (one row per object per frame):

`match_id, period, frame, time_utc, match_clock_s, object_id, object_type (player|ball|referee), team_side (home|away|none), x_m, y_m, z_m, ball_status (live|dead|unknown), ball_possession (home|away|none), provider_speed_ms, provider_accel_ms2, provider_step_m`

The three `provider_*` columns are **kept for tests only** and are empty when the provider does not supply them.

**Prepared tracking** (output of sections 5–7, frame-level, git-ignored) adds:

`valid_kinematics, exclusion_reason, estimated, discarded, speed_ms, acc_tan_ms2, acc_status (measured|noise|approx|none), distance_m`

**Events**:

`match_id, period, event_id, time_utc, event_type, player_id, team_side, x_m, y_m` (annotated location, in the canonical reference system), `attributes` (English keys and values), `sync_time_utc, sync_frame, sync_x_m, sync_y_m, sync_certainty, sync_method, type_check (confirmed|not_confirmed|not_applicable|pending)`

**Prepared events** (section 9) add:

`event_kind (pass|shot|clearance|duel|restart|substitution), type_check_basis, type_check_reason, aerial, shot_outcome_observed (goal|disallowed_goal|woodwork|saved|blocked|wide|none), shot_goal_line_y_m, shot_power_kmh, shot_power_evidence, pass_outcome (teammate|opponent|out_of_play|open_space|unresolved), clearance_conditions, clearance_score`

Added in v4.2 (fields are only ever added, never renamed): `sync_window_extended` (`R-LATE-ANNOTATION`), `shot_wide_margin_m` (`R-SHOT-DIRECTION`), `shot_blocked_at_kick` (`R-SHOT-OUTCOME`), `shot_header` (`R-SHOT-POWER`), `goal_confirmed_by`, `own_goal`, `goal_scorer_id` (`R-GOAL`).

`type_check = pending` exists only in live mode (section 9.12).

**Goal crossings** (section 9.6, one row per ball crossing of a goal line between the posts and under the bar): `match_id, period, frame, time_utc, attacking_side, y_line_m, z_line_m, goal_check (goal|disallowed_goal|unconfirmed|play_continued), next_restart_type, shot_event_id, shooter_mismatch`; added in v4.2: `crossing_method (observed|extrapolated), extrapolated_s, confirmed_by (kickoff|end_of_period), disallowed_reason, goal_disallowed_event_id, scorer_id, own_goal, deflected_by_id, pre_deflection_on_target`. The value set of `goal_check` does not change: a goal confirmed by the end of the period is a `goal` with `confirmed_by = end_of_period`.

**Line-up and match information**: players (ID, shirt number, role code and group in English, starter flag), staff (English role), referees, pitch size, formation (English), kickoff times, venue (country in English), plus provider metadata kept as-is in a `source_*` field for traceability.

### 2.3 Our own synthetic data

The synthetic generator follows the structure of the DFL files, with English codes, and is read by the same adapters. Its rules are in section 12.

---

## 3. Match profile: discover, don't assume

Before preparing a match, build its **match profile** (`reports/qc/<match_id>/match_profile.json`). Every later step reads parameters from it.

| Field | How to discover it |
|---|---|
| `provider`, `format`, `units` | Detection rules of section 2.1 |
| `fs_hz` per period | `1 / median(ΔT)` from timestamps |
| `frame_ranges` | First and last frame per period, plus the offset between periods |
| `pitch_length_m`, `pitch_width_m` | From metadata. If missing, infer from the extents of the goalkeepers and of ball restarts (record that it was inferred) |
| `origin`, `axis_orientation` | Coordinate extents, ball position at kickoff, goalkeeper sides |
| `event_to_canonical_transform` | Offset, scale and axis signs fitted on events vs the ball position (section 4) |
| `direction_per_period` | Kickoff metadata (DFL: `TeamLeft`/`TeamRight`), cross-checked with goalkeeper mean x |
| `kickoff_utc` per period and `tracking_start_offset_s` | Section 9.1 |
| `tracking_area_edges` | `R-EDGE` (section 5.2.1) |
| `parking_values` **(v4)** | `R-PARKING` (section 5.2.2) |
| `event_clock_offset_s` and `event_offset_dispersion_s` per period | Section 9.3, step 1 |
| `provider_kinematics_supplied` | Which provider speed, acceleration and step channels exist (tests only) |
| `ball_status_supplied` | Whether the provider supplies ball status (section 10) |
| `ball_height_supplied` **(v4)** | Whether the ball has a usable `z` (needed by `R-AERIAL`, `R-GOAL` and `R-WOODWORK`) |
| `objects`, `substitutions`, `cards`, `delete_events` | Line-up, events and per-object frame sets. Objects that belong to no team (for example tracked referees) are kept out of player preparation and recorded here |
| `event_types` **(v4.2)** | Events per English type name (`R-MAP-EN`, section 16.4), the types outside the DFL 23-type list and any unmapped type |
| `playing_time` | Gross and net per period, compared with tracking duration and with the time where `ball_status = live` |

---

## 4. Canonical reference system

All outputs use **one** reference system, whatever the provider:

- **origin at the centre spot**, in metres;
- **x along the length** of the pitch (from −L/2 to +L/2), **y along the width** (from −W/2 to +W/2), right-handed. The pitch size is taken from the match profile;
- **raw orientation**: as seen in the tracking data. The **home team attacks towards +x in the first period** after normalisation; the profile records the original direction;
- **attacking-normalised coordinates** (`x_att`, `y_att`): an extra pair of columns where the team in possession, or the player's own team, always attacks towards +x. Use them for any team or player metric that depends on direction.

**Events are transformed** into the same system with the fitted transform (offset, scale and the sign of each axis). The sign of y must be checked on synchronised events, never assumed: on J03WPY, flipping y makes the event–ball fit 29.9 m worse (median event–ball distance 5.69 m with the right sign).

---

## 5. Data preparation: handle, don't warn

### 5.1 Handling rules

The agent applies every rule automatically, in the order shown, and logs one line per rule in the form `rule ID, count, examples, note`. The complete order of operations, including kinematics and events, is in section 6.4.

| # | ID | Issue | Automatic handling | Logged |
|---|---|---|---|---|
| 1 | `R-DELETE` | `Delete` or other placeholder events | Drop at load | Raw events, dropped, kept |
| 2 | `R-SORT` | Rows not ordered (frames, timestamps, events) | **Sort** by (period, object, frame or time); sort events by time with a stable sort | Tracking rows and event pairs out of order |
| 3 | `R-DEDUP` | Duplicate rows (same object, period, frame) | Keep the first; if the duplicates differ, keep the one closest to its neighbours | Duplicates removed |
| 4 | `R-RESAMPLE` | Irregular sampling (ΔT varying by more than 5 % of the median) | Resample to the profile's `fs_hz` on the regular grid; samples that cannot be resampled become gaps (section 6.3) | Samples resampled, largest deviation |
| 5 | `R-MISSING` | Missing positions (NaN or missing frames) | Gap (section 6.3), reason `missing` | Missing frames and NaN samples |
| 6 | `R-UNITS` | Timezones and units | Convert to UTC and SI units | Time zones found, inferred units |
| 7 | `R-MAP-EN` | Provider-specific or German codes | Map to English (section 16), including every event type (section 16.4, v4.2) | Codes mapped, codes unmapped; event types outside the DFL 23-type list |
| 8 | `R-BALL-DEAD`, `R-BALL-WINDOW` **(v4)** | Ball while `ball_status = dead` (some providers freeze it); ball windows | Positions kept for display. Ball kinematics invalid while dead (`dead_ball`) and for **0.25 s** after each dead→live restart (`restart`); **±0.25 s** around live-play ball jumps and ball limit violations (section 6.2) | Dead samples, restarts, restart jumps ignored |
| 9a | `R-EDGE` | Positions pinned at a tracking-area edge (section 5.2.1) | `outside_tracking_area`: invalid, gap, window | Edges, runs, players, samples (and how many in live play) |
| 9b | `R-PARKING` **(v4, v4.1)** | Frozen tracks parked at a repeated value beyond a line (section 5.2.2), shared by at least 3 players | `parked`: invalid, gap, window | Parking values, runs, players, samples |
| 10 | `R-FROZEN` | **Frozen tracks**: identical positions for ≥ 1 s | Invalid; the frozen samples become a gap (section 6.3). The first sample of the run is the last real measurement and stays valid (not at an edge or a parking value) | Runs, estimated, discarded, methods, median resume distance |
| 11 | `R-JUMP` | Position jumps (step > `v_max · ΔT`) | **Players**: detect in every phase (they corrupt the filtered signal around them), ±1 s, cause classified (section 5.5). **Ball**: only in live play (jumps between dead and live phases are expected), ±0.25 s | Episodes by cause, in live play / dead ball |
| 12 | `R-SWAP`, `R-SWAP-RESTORE` **(v4)** | Identity swaps | Labels swapped back **only** when a mutual swap is followed by its mutual correction for the same pair **within 120 s**; in every case ±1 s around the swap (and around its correction) for both players (section 5.5) | Incidents, labels restored, windowed only |
| 13 | `R-SUB-INHERIT` **(v4)** | **Substitutions: identity inheritance** (section 5.3) | Outgoing player invalid from the `Substitution` event (or a frozen tail). Incoming player: inheritance detected at < 0.5 m; with inheritance the re-acquisition jump is searched; without a jump the incoming player is valid after 1 s | One row per substitution (section 5.3) |
| 14 | `R-EXIT-REENTRY` | **Exit or re-entry** | A frozen, edge-pinned or parked run that ends with the end of the track is an **exit**: the run is invalid. A frozen run off the pitch that ends with the player back on the pitch is a **re-entry**: the run is invalid, and the player is invalid until 2 s after he is back | Exits, re-entries |
| 15 | `R-OVERLAP-CROWD` | Overlaps (< 0.3 m for ≥ 0.2 s); jumps in crowding (≥ 3 players within 3 m) | ±1 s for both players (overlap) or the jumping player (crowding) | Episodes |
| 16 | `R-SPEED-LIMIT`, `R-BALL-LIMIT` | Player speed > 38 km/h; live ball speed > 211 km/h | `limit`: ±1 s (players), ±0.25 s (ball) | Episodes outside any other window |
| 16 | `R-ACC-BAND` **(v4)** | Player tangential acceleration outside −8.5 to +7.5 m/s² | **Never excludes a sample.** An exceedance lasting ≤ 0.2 s is `noise` (its acceleration is not used); a longer one is `approx` (kept, reported with "≈"). Section 7.2 | Runs and samples by class (`noise` / `approx`) and sign |
| 17 | `R-UNKNOWN`, `R-JUMP-CONTEXT` **(v4)** | **Unknown anomalies** (no signature matched) | ±1 s, **whatever the cause**. For unknown jumps, the provider events within ±2 s are recorded; the report separates unknown jumps in live play and in dead ball (section 5.5) | Episodes, live / dead, event types nearby |
| 18 | `R-TRACK-START` | Tracking starts before or after kickoff | Nothing to repair: the match clock starts at kickoff (section 9.1) and the profile records the offset | Offset per period |

### 5.2 Beyond the lines: tracking-area edges and parking values

Optical tracking systems only see a limited area. Some providers pin positions outside it to a fixed coordinate, often freezing the other coordinate too, and some park lost tracks at a fixed value. Both are **not real positions**. They are two separate rules.

#### 5.2.1 `R-EDGE`: tracking-area edges (discovered per match and per provider)

**Discovery.** For each match, a coordinate value is a **tracking-area edge** when all of these hold:
1. it lies **beyond a pitch line** (|x| > L/2 or |y| > W/2), on any side;
2. it is reached **exactly** (after rounding to 4 decimals) by at least **25 player samples** (about 1 s at 25 Hz);
3. it is the **outermost** value observed on that side of that axis (a clamp cannot be exceeded);
4. its samples come from at least **2 distinct positions** (the other coordinate differs) **or 2 separate runs**. Runs are counted **along each player's track**, never on a flattened array.

**Marking.** Samples at an edge, and the samples of the same run of identical positions next to them, are `outside_tracking_area`: invalid, window of the run plus 1 s on each side, gap handled by section 6.3. Record each edge in the profile with its samples, runs, players and the candidates rejected (with the reason). **Never hard-code an edge value**: different stadiums, providers and camera set-ups have different edges, or none.

**Examples (v3 run).** Four of the six DFL matches have edges, always at x = ±55.5 m (3 m behind each goal line); J03WMX and J03WOY have none. On J03WPY: 1,610 samples in 8 runs from 4 players (goalkeepers fetching the ball behind the goal before goal kicks, a striker running past the goal line 10 s before a shot, a winger with the other coordinate frozen); 244 other candidates were rejected (frozen positions, not edges).

#### 5.2.2 `R-PARKING` (v4, v4.1): parking values beyond the lines, on frozen tracks

A **parking value** is a coordinate value beyond a pitch line where the provider leaves **several frozen tracks**. It is not an edge (it need not be the outermost value), and it applies **only to frozen tracks**.

**Discovery** (after `R-EDGE`, on the same match):
1. take the frozen runs of `R-FROZEN` (identical position for ≥ 1 s) whose position lies beyond a pitch line;
2. group them by the coordinate that lies beyond the line, per axis and side, rounded to 4 decimals;
3. a value is a parking value when its runs come from **at least 3 different players** (v4.1; 2 in v4) and total **at least 25 samples**, and the value is not a tracking-area edge. A substitute who inherits the outgoing player's frozen track counts as the same track (`R-SUB-INHERIT`).

**Marking.** Every run of identical consecutive positions (at least 2 samples) sitting at a parking value is `parked`, **including its first sample**: a parked position is never a real measurement. Window: the run plus 1 s on each side (as for edges). The run is a gap (section 6.3), and its resume distance is measured from the **last sample before the run**. A parked run that ends the track is an exit (`R-EXIT-REENTRY`).

**Logged.** Each parking value (axis, side, value, distance beyond the line, samples, runs, players) in the profile and the preparation log.

**Why.** On J03WOY, y = +36.54 m (2.5 m beyond the touchline) is reached exactly by 2,066 samples from 7 players. It was not an edge in v3 because a single frozen player reached a value further out, so its first samples were treated as real positions. Seven players frozen at the same value to 0.1 mm cannot be a coincidence.

**Why at least 3 players (v4.1).** The v4 run also found x = −52.9 m on J03WOY (0.4 m behind the goal line, next to the corner flag), shared by only 2 players. It was a goal celebration: the tracking froze the players standing side by side at the corner during the 76 s stoppage before the kickoff, both with the previous sample at −52.85 m. Two frozen players can share a value by coincidence at the provider's coordinate resolution; three cannot. The samples stay excluded (they are frozen, `R-FROZEN`); only the label changes.

### 5.3 `R-SUB-INHERIT` (v4): substitutions and identity inheritance

**Evidence (v3 run).** Over the six matches there are 57 substitutions. In 29 of them the incoming player's track starts **at the outgoing player's last position** on the next frame: the operator moves the identity onto the existing track. This happens in all 9 substitutions of J03WPY but only in 3–5 of the 9–10 of the other matches. Three half-time substitutions have no inheritance (the outgoing player is not tracked in that period). On J03WPY, 7 inheriting tracks **jump** to the substitute's real position 0.5–28.4 s later (up to 37 m); one outgoing track has a 7.8 s frozen tail and the incoming track a 21.1 s frozen head. An earlier idea (substitutions within 10 m of the halfway line are immediate) was contradicted by these data and is not used.

**Rule.**
1. **Outgoing player**: invalid (`substitution`) from the `Substitution` event time, corrected by the period's event clock offset, or from the start of a frozen tail, whichever is earlier, to the end of the track.
2. **Inheritance is detected, never assumed**: it exists only when the incoming track's first sample is **< 0.5 m** from the outgoing player's last sample and at most **1 frame** later. If the outgoing player is not tracked in the period, there is no inheritance.
3. **With inheritance**, always search the **re-acquisition jump**: the first position jump (`R-JUMP`) of the incoming track within 60 s of its first sample.
   - Jump found: the incoming player is invalid from his first sample until the jump plus **2 s** of stabilisation.
   - No jump: the incoming player is invalid for his **first 1 s only**, then valid.
4. **Without inheritance**, no jump is searched: the incoming player is invalid for his **first 1 s only**, then valid.
5. A frozen head of the incoming track is a gap that is discarded, with reason `substitution`.
6. **Distance from the halfway line** (|x| of the outgoing player's last position) is logged as **information only**; it changes nothing.

**Logged** (one row per substitution): period, team, players out and in, inheritance distance and frame gap, inheritance yes/no, outgoing frozen tail, incoming frozen head, jump time and size (or "no jump"), invalid durations of both players, distance from the halfway line.

Always check for the same pattern with other providers; never assume it.

### 5.4 Stop conditions (the only reasons to stop)

The agent stops and asks only if the data is unusable:
- no time information at all (no timestamps and no frame rate);
- no identity for the objects;
- a file that cannot be parsed by any adapter.

A missing pitch size, kickoff time, ball status or ball height is **not** a stop condition: infer what can be inferred (section 3), record it, and skip only what depends on the missing piece.

### 5.5 Identity swaps and unexplained jumps

**`R-SWAP` and `R-SWAP-RESTORE` (v4).**
- A **mutual swap**: two players jump on the same frame, each landing within 1 m of the other's previous position.
- Labels are swapped back **only** when a mutual swap is followed by its **mutual correction for the same pair within 120 s**: the labels between the two frames are exchanged. A single mutual swap without a correction is windowed only, because we cannot tell which side is wrong.
- A **one-sided swap** (a jump whose new position matches a team-mate's or opponent's position within ±1 s, with a jump of that partner) is windowed only.
- In every case: ±1 s around the swap and around its correction, for both players. Swaps are handled **before** any other rule.

**Jump causes (`R-JUMP`).** Each player jump episode gets one suspected cause, in this order: `substitution` (inside a substitution window), `swap`, track loss (a frozen, edge-pinned or parked run resumes; reason `jump`), `reentry` (≥ 10 s clearly off the pitch in the previous 30 s), `crowding` (≥ 3 players within 3 m), otherwise `unknown`. All are excluded with ±1 s.

**`R-JUMP-CONTEXT` (v4).** For every `unknown` jump episode:
- record the provider events (after `R-DELETE`) whose annotated time, corrected by the period's event clock offset, is within **±2 s** of the first jump frame: event ID, type, team, and whether the jumping player is the event's player. These go into the git-ignored anomaly catalogue;
- the report gives the counts of unknown jumps **in live play** and **in dead ball** (ball status at the first jump frame), and the event types found nearby.

This is for finding the causes later; it never changes the exclusion.

---

## 6. Validity mask, exclusion windows and gaps

### 6.1 The mask

Every player sample carries:
- `valid_kinematics` (true or false);
- `exclusion_reason`: the rule that excluded it. When several rules exclude the same sample, the **first in this priority list** is recorded: `outside_tracking_area`, `parked`, `frozen`, `missing`, `jump`, `swap`, `substitution`, `exit`, `reentry`, `crowding`, `overlap`, `limit`, `unknown`. Ball samples can also be `dead_ball` or `restart`;
- `estimated` and `discarded` (section 6.3);
- `acc_status` **(v4)**: `measured`, `noise` or `approx` (`R-ACC-BAND`, section 7.2), or `none` where there is no valid acceleration.

**Every metric uses only samples with `valid_kinematics = true`.** Acceleration values also need `acc_status` = `measured` or `approx` (never `noise`).

Samples in segments shorter than the filter window have no kinematics and get `unknown`.

### 6.2 Windows

| Anomaly | Excluded interval |
|---|---|
| Jump, swap, overlap, crowding, speed-limit violation, unknown | From 1 s before the first anomalous frame to 1 s after the last one |
| Frozen track | The frozen samples, plus 1 s after the track resumes |
| Outside the tracking area (`R-EDGE`), parked (`R-PARKING`) | The pinned or parked samples, plus 1 s on each side |
| Substitution, exit, re-entry | `R-SUB-INHERIT`, `R-EXIT-REENTRY` |
| Ball, live play (`R-BALL-WINDOW`) **(v4)** | ±0.25 s around a live-play ball jump or a live ball speed above 211 km/h; 0.25 s after each dead→live restart |
| Acceleration outside the band (`R-ACC-BAND`) **(v4)** | **No window**: positions, speed and distance are untouched |

**Why ±1 s for players.** Speed uses a Savitzky–Golay window of 13 samples (0.52 s), so one bad sample affects ±6 samples (±0.24 s) of speed. Acceleration applies a second filter of 19 samples (section 7.2; up to 25 in the sensitivity test), so one bad sample can affect up to ±15 samples (±0.60 s) of acceleration (±18 with 25 samples). One second covers both with a margin. The literature has no single standard for exclusion windows, so the choice is documented.

**Why ±0.25 s for the ball (v4).** Ball speed uses the 13-sample filter, so one bad ball sample contaminates ±6 samples (0.24 s; `frames(0.25 s)` = 6 at 25 Hz). The v3 window of 0.5 s removed more valid ball samples than any bad sample can affect, including touches and the first part of shots.

### 6.3 Gaps: estimate only when it is safe (`R-GAP`)

A **gap** is a stretch where a player's true position is unknown: missing samples, a frozen track, samples pinned at a tracking-area edge or parked at a parking value.

| How the gap ends | Handling |
|---|---|
| The player resumes **less than 6 m** from the last valid position | **Estimate** the path: cubic spline (`floodlight.transforms.interpolation.interpolate_spline`, k = 3) for gaps ≤ 0.5 s; Kalman smoother (forward plus RTS, constant acceleration, measurement noise σ = 0.1 m, white-jerk process noise 50 (m/s³)²·s) for gaps ≤ 2 s; a straight line beyond 2 s (a lower bound). Estimated samples are flagged `estimated = true` |
| The player resumes **6 m or more** away, or never resumes | **Discard**: no interpolation, because we do not know what happened. The stretch is flagged `discarded = true` |

- **Estimated samples count only for distance.** They never count for speed, acceleration, speed zones, sprints or any peak value.
- Never bridge across periods, substitutions or the boundary between two different players' tracks.
- Other excluded windows (around jumps, swaps, limit violations, unknown anomalies) are **not** estimated. Their distance is not counted.
- The share of time on the pitch that is **not directly measured** (estimated plus discarded plus other exclusions) drives the precision of reported distances (section 10.2).

### 6.4 Order of operations for one match

1. Load, detect and map (`R-DELETE` to `R-MAP-EN`); build the match profile (section 3) and the canonical transform (section 4). The profile includes the event clock offset (section 9.3, step 1).
2. Discover tracking-area edges (`R-EDGE`), then parking values (`R-PARKING`). Offline they are discovered on the whole match; the live engine discovers them per period on the frames released so far.
3. Identity swaps (`R-SWAP`, `R-SWAP-RESTORE`): labels restored before anything else.
4. Gaps: edges, parking values, frozen runs and missing samples (`R-EDGE`, `R-PARKING`, `R-FROZEN`, `R-MISSING`); exits, re-entries and track heads (`R-EXIT-REENTRY`); estimate or discard (`R-GAP`).
5. Substitutions (`R-SUB-INHERIT`).
6. Player jumps with their cause (`R-JUMP`, `R-UNKNOWN`, `R-JUMP-CONTEXT`).
7. Overlaps (`R-OVERLAP-CROWD`).
8. Kinematics on continuous segments that break at gaps, track ends and jumps (`R-SG-SPEED`, `R-SG-ACC`), so that filter edges never fall inside valid data.
9. Speed limit (`R-SPEED-LIMIT`).
10. Apply the mask (first rule wins, section 6.1).
11. Acceleration band on valid samples (`R-ACC-BAND`).
12. Distance: valid speed × ΔT plus the estimated paths.
13. Ball (`R-BALL-DEAD`, `R-BALL-WINDOW`, `R-JUMP` for the ball, `R-BALL-LIMIT`).
14. Events (section 9.3, steps 1–10).
15. Metrics (section 10).
16. Acceleration window sensitivity test (`R-ACC-WINDOW-TEST`, section 7.3).
17. Report (section 13).

---

## 7. Kinematics: always ours, provider for testing only

### 7.1 Source

- **Velocity, speed, acceleration and distance are always computed by us from the cleaned positions** with the Savitzky–Golay filter (section 7.2). One method for every provider and for synthetic data.
- **Provider kinematics** (for example DFL `S`, `A`, `D`) are **used only in tests**: they measure the accuracy of our pipeline. They are reported in a "provider comparison" section of the report. They **never** influence masks, choices, metrics or answers.
- **Distance** = Σ (valid speed × ΔT) plus the estimated gap segments (section 6.3).

**Why (evidence from J03WPY, v3 run).**

| Finding | What it means |
|---|---|
| At the 192 raw position-jump frames (steps up to 45.0 m), provider `S` stays below 5.33 m/s, and `D` equals our step in only 3.1 % of them | `S` and `D` come from the provider's own internally smoothed trajectory, not from the published X/Y |
| Our step against `D` on valid samples: p95 difference 2.17 cm, r = 0.894 | `D` is not fully consistent with the published positions |
| During 31,192 frozen samples, the mean provider `S` is 0.019 m/s | When tracking loses a player, the provider's speed is lost too |
| During identity swaps, `S` follows the (wrong) label | The provider's speed can belong to the wrong player |
| Provider `A` is capped at ±7.99 m/s² | Provider acceleration saturates |
| On valid samples, our Savitzky–Golay speed matches `S` (p95 difference 0.042 m/s, r = 1.000) and `A` (p95 0.289 m/s², r = 0.989) | Computing ourselves loses no accuracy |

### 7.2 Savitzky–Golay (the only filter)

**`R-SG-SPEED`.** Velocity, speed and distance, for players and for the ball:
- polynomial order 2, window `w = odd(round(fs × 0.5))`, which gives **13 samples (0.52 s) at 25 Hz**;
- velocity: `savgol_filter(x, w, 2, deriv=1, delta=1/fs, mode="interp")` per axis; speed = |v|.

This window does not change in v4: a longer window would flatten real speed peaks.

**`R-SG-ACC` (v4, v4.1).** Player acceleration:
- the same filter, applied to (vx, vy) from `R-SG-SPEED`, with its own window `w_a = odd(round(fs × f_a))`;
- **`f_a = 0.75`: 19 samples (0.76 s) at 25 Hz** (v4.1), chosen by the team from the sensitivity test of section 7.3 after the v4 run. Velocity, speed and distance stay at 13 samples;
- tangential (signed) acceleration = (v · a) / |v|, set to 0 when |v| is below 10⁻⁶ m/s;
- segments shorter than `w_a` have no acceleration (`acc_status = none`), even when they have a speed.

Ball touch detection (section 9.5) keeps the 13-sample window for the ball's acceleration: its thresholds were tuned on it.

**Common settings.**
- **Edges**: `mode="interp"`. Segments shorter than the window stay NaN.
- **Derivative scheme**: offline central (zero phase); live engine **backward** (causal); forward differences are never used.

**`R-ACC-BAND` (v4): the acceleration band.** Applied to valid player samples after the mask (section 6.4, step 11). The band is −8.5 to +7.5 m/s² (section 8).
1. Find the runs of consecutive valid samples with tangential acceleration < −8.5 or > +7.5 m/s².
2. A run of at most `frames(0.2 s)` samples (**5 samples at 25 Hz**) is **noise**: `acc_status = noise`. The acceleration of those samples is not used anywhere. **No window**, and positions, speed, distance and `valid_kinematics` are untouched.
3. A longer run (≥ 6 samples at 25 Hz) is **uncertain**: `acc_status = approx`. The value stays and is reported as **"≈ value"** (section 10.2). It is **not** excluded.
4. Every other valid sample with an acceleration is `measured`.

**Logged:** runs and samples by class (noise, approx) and by sign (acceleration, deceleration), per match and per player.

**Why.** In v3 the band excluded samples with ±1 s windows: 216 episodes over the six matches, all of them accelerations (113) or decelerations (103), mostly just beyond the band. Reviews of acceleration data note that almost no study reports its filter, and recommend reporting the filter and a **minimum effort duration** (Delves et al. 2021; Ellens et al. 2022): an exceedance that lasts a few samples is noise, not an effort.

**Decision record: the filter comparison (J03WPY, 2026-10-21).**

| | Savitzky–Golay (order 2, 0.52 s) | Butterworth 2 Hz | Butterworth 3 Hz |
|---|---|---|---|
| Peak speed | 9.30 m/s | 9.31 m/s | 9.32 m/s |
| Peak \|acc\| | 10.5 m/s² | 17.5 m/s² | 26.1 m/s² |

Butterworth was zero-phase, 4th order. The filters give the same speeds. Savitzky–Golay reproduces the provider's `S` and `A` best. Butterworth inflates acceleration peaks. **Decision: Savitzky–Golay is the only filter.** The Butterworth function stays in the code only as the tool of this comparison, never in the data path. Repeat the comparison only when a new provider or format is introduced.

### 7.3 `R-ACC-WINDOW-TEST` (v4): choosing the acceleration window

**No standard window exists** for 25 Hz tracking. A longer window lowers the noise but flattens real peaks: it is a bias–variance trade-off, and the best point depends on the noise of the data (arXiv:1808.10489). In football, `databallpy` uses Savitzky–Golay of order 2 with **25 samples for the ball and 35 for acceleration**, a longer window for the second derivative.

**The test.** On every prepared match, compute player acceleration with `w_a` = 13, 19 and 25 samples (`f_a` = 0.5, 0.75, 1.0), always on the same cleaned positions and with speed at 13 samples. For each window, report per match and over all matches:
1. **band exceedances that remain**: runs and samples, split into `noise` (≤ 0.2 s) and `approx` (> 0.2 s), and by sign;
2. valid tangential acceleration: p0.1 / p99.9 and min / max;
3. **diagnostic only**: speed and distance recomputed with the same window `w_a`, to show what a longer window would do to them. Per player, the change in maximum speed (median and maximum absolute change, km/h) and in total distance (median and maximum relative change, %) against 13 samples. These values are never used in results.

**Invariance.** In v4 the acceleration band never excludes a sample, so the choice of `w_a` cannot change the validity mask, the maximum speeds or the distances used in results. A test checks this.

**Output.** A table "Acceleration window sensitivity" in `reports/qc/SUMMARY.md`, and the per-match numbers in `qc_results.json` (`acc_window_sensitivity`).

**Choice (v4.1, made on 25 October 2026).** On the six DFL matches, the v4 run gave 182 noise runs and 36 `approx` runs with 13 samples, 78 and 8 with 19 samples, 19 and 3 with 25 samples. The team chose **19 samples**: most short spikes disappear, while real acceleration peaks are flattened less than with 25 samples. Being deterministic, the re-run changed only the acceleration values and their `acc_status`. The test keeps running on every match, so the choice can be reviewed for a new provider.

---

## 8. Physical limits, plausibility band and effort thresholds

All values and sources: [`Materials/PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md).

**Physical limits** decide whether a sample is possible. A sample beyond them is excluded through the mask.

| Quantity | Limit | Rule | Basis |
|---|---|---|---|
| Player speed | **≤ 38 km/h (10.56 m/s)**, a fixed limit | `R-SPEED-LIMIT` | Highest verified match speed: 37.38 km/h (Van de Ven, Premier League / Opta, 2024) |
| Player step per frame | ≤ v_max · ΔT (0.42 m at 25 Hz) | `R-JUMP` | Derived from the speed limit |
| Ball speed, **live play only** | **≤ 211 km/h (58.6 m/s)** | `R-BALL-LIMIT` | Fastest reported shot (Heberson, 2006, unofficial) |
| Ball acceleration | **No limit** | – | Contact lasts 9–12 ms: kicks, deflections, bounces and saves are impulsive and legitimate |

**Plausibility band (v4).** Player tangential acceleration has a band, **−8.5 to +7.5 m/s²**, that **never excludes** a sample (`R-ACC-BAND`, section 7.2): exceedances of ≤ 0.2 s are noise, longer ones are reported with "≈". Supporting evidence: provider saturation at ±7.99 m/s²; braking is harder than accelerating (Harper et al. 2019, 2022). The literature source of the band's values is still to be cited (section 19).

**Realism ranges** are never used to exclude samples. For example, maximal instep kicks measure 18–35 m/s (Kellis & Katis 2007): we use this to judge whether synthetic data and reports look realistic.

**Speed zones and sprints (v4)** are now metrics, not future thresholds: the FIFA zones 0–7, 7–15, 15–20, 20–25 and above 25 km/h, and **sprints above 25 km/h** (section 10.5). They are never used to detect outliers.

**Effort thresholds for acceleration** (2.78 m/s² from Varley & Aughey 2013; ±3 m/s²; 2.5 and 3.5 m/s² from Harper et al. 2019) are documented for **possible future metrics**. **They are not used now, and never to detect outliers.**

---

## 9. Time and events: locate events from trajectories

### 9.1 Start of each period and match clock

- **Primary source: the `KickOff` event timestamp of that period.** On J03WPY the first-half `KickOff` (11:01:28.309 UTC) agrees with `KickoffTime` in match information within 10 ms.
- **Tracking may start before or after the kickoff** (`R-TRACK-START`). Over the six DFL matches the first tracked frame comes between −0.55 s and +1.61 s from the `KickOff` event (J03WPY: +1.01 s and +0.95 s). Record the offset per period; it is not an error. Never assume its size for another provider: measure it.
- **Match clock** = time since that period's kickoff (displayed as 45:00 + elapsed time in the second period).
- **Fallbacks, in order, when there is no kickoff event:**
  1. kickoff time in the match metadata;
  2. the first dead→live transition of the provider's ball status with the ball within 2 m of the centre spot;
  3. the first tracking frame.

  Record which source was used.

### 9.2 Why the event time cannot be trusted

Event timestamps are human annotations. Their offset from the tracking is **not constant**:

| Component | Size | Source |
|---|---|---|
| Systematic part | −0.37 s across the dataset | Paper |
| Systematic part per period | From −0.44 s (J03WMX) to +2.19 s (J03WQQ) over the six matches; J03WPY +0.07 s and +0.68 s | Our measurement (v3 run) |
| Random part | SD 1.82 s, maximum 27 s | Paper |
| Random part per period | MAD 0.35–1.16 s over the six matches | Our measurement (v3 run) |

The event time zone also differs: +01:00 on J03WQQ and J03WR9, +02:00 on the others (tracking is in UTC).

Event types have specific biases (J03WPY):
- set pieces: the event time is not the restart time (corners: the dead→live transition comes a median 1.39 s after the event);
- substitutions: the frame-set switch comes −0.2 to +21.2 s after the event;
- kickoff after a goal: the event comes 3.35 s before the dead→live transition.

**Problems this causes if ignored:**
1. event-anchored metrics computed on the wrong frame (for example a shot speed measured before the kick or after the save);
2. wrong context: possession attributed to the wrong team, wrong event zone or distance;
3. misaligned timeline and replays (the clip shows another action);
4. events counted in the wrong minute or phase;
5. close events in the wrong order, which corrupts sequences and pass networks;
6. in live mode, events arriving before or after their frames.

### 9.3 Principle and order of the event steps

**The event time is a hint; trajectories decide.** The annotated event is **never changed**: our checks add fields (`sync_*`, `type_check`, outcomes) next to it. The steps run in this order:

1. **Clock offset (`R-CLOCK-OFFSET`).** Per period, compute the offsets between anchor events and their physical counterparts: restarts and kickoffs ↔ the provider's dead→live transitions (paired at most 30 s apart). The robust median is `event_clock_offset_s`; the spread (MAD) is `event_offset_dispersion_s`. The **search window** for each event is `max(5 s, 4 × MAD)`, capped at 30 s (5 s on all six DFL matches). If all offsets are negligible, the provider's events are already synchronised: record this and use them as they are.
2. **Restarts and substitutions (`R-RESTART-SUB`).** A restart (throw-in, corner, free kick, goal kick, kickoff, penalty) is located at the provider's dead→live transition nearest to its corrected time, inside the search window. A substitution is located at the frame-set switch.
3. **Goals (`R-GOAL`, `R-GOAL-FREEZE`, section 9.6)**: located first, looking forward, and used as fixed anchors.
4. **Ball touches and candidates** (section 9.5): ground touches, aerial touches and, for shots only, soft kicks, owned by the player (`R-TOUCH-NEAREST`, v4.2); a search window that is mostly dead ball extends backwards (`R-LATE-ANNOTATION`, v4.2); for duels, the duel signature (section 9.10).
5. **Order-preserving alignment (`R-ALIGN`, section 9.5)**, with the duel–shot exception of `R-DUEL` (v4.2).
6. **Location (`R-SYNC-LOCATION`).** The event's zone and distance come from the ball and player positions **at `sync_frame`**, never from the annotated coordinates, which are only a hint for the search. Zones in attacking-normalised coordinates: thirds at x_att = ±L/6; penalty area 16.5 m deep and 40.32 m wide (IFAB Law 1).
7. **Type checks and outcomes, from what happens next**: shots (section 9.7), passes (9.8), clearances (9.9), duels (9.10). An event whose trajectory does not confirm its type gets `type_check = not_confirmed`, with a reason, and is listed in the report.
8. **Highlights (`R-HIGHLIGHT`, section 9.11)**: anchored on the physical outcome, walking backwards.
9. **Metrics not tied to an event** (distance per minute, peak intensity, possession or ball circulation per 5 minutes) are computed on the **tracking clock**, which is reliable. The event offset does not affect them; only the period start (9.1) does. Event-based counts per period (for example shots per 15 minutes) use `sync_time_utc`.
10. **Live mode (`R-LIVE-BUFFER`, section 9.12)**: an event is never processed before its frames.

Store `sync_time_utc`, `sync_frame`, `sync_certainty`, `sync_method` and the residual (sync time minus corrected time) for every located event.

### 9.4 `R-LIVE-ONLY` (v4): live play only

**Every check on an action or on the position of a person** in section 9 uses only frames with `ball_status = live`: touches (ground, aerial, soft kicks), duels, kick conditions, next touches, free flights, shot direction, team-mates near the trajectory, outcomes, woodwork, clearance conditions. A flight or a search **ends at the first dead frame**.

**The one exception:** the ball samples **inside the goal** (beyond the goal line, between the posts and under the bar), which `R-GOAL` uses even when they are dead. Since v4.2 the ball's kinematics use them too: a live segment that ends with the ball going into a goal continues through the in-goal samples that follow it, up to the first sample that leaves the goal or that the provider freezes (position held), so that a touch just before the line is not lost at the filter edge (`R-GOAL` step 4). Touches are still found only on live frames.

**Not affected:** the cleaning rules of section 5 (player jumps are detected in every phase) and the physical metrics of section 10.1 (whole time on the pitch).

### 9.5 Ball touches and the alignment

**`R-TOUCH`: ground touch (v4.2).** A local maximum of the ball's horizontal |a| (Savitzky–Golay, 13 samples, live segments that break at dead frames and at live-play ball jumps) of at least **8 m/s²**, in live play, with a player within **2 m** of the ball within ±0.2 s of the peak. It is a **ground** touch only when the ball is **at or below 1.5 m** at the peak (v4.2; any height without ball height). A higher peak stays a **touch** (a goalkeeper catching a high ball, a chest control: it ends a free flight and can be a next touch), but it is never a ground kick; it can be a kick only as an aerial touch (`R-AERIAL`).
- A touch is a **kick** of that player (pass, shot, clearance) when he owns it (`R-TOUCH-NEAREST`), the ball then moves away from him by at least **1 m within 0.5 s**, and **the next touch is not his own** (otherwise it is a dribbling touch, and the pass or shot comes later).
- Two touches less than **0.2 s** apart are one touch, except after a shot (`R-SHOT-SPLIT`).

*Why the height (v4.2).* In the v4 run a "kick" was found with the ball 4.9 m above the shooter (J03WPY): an |a| bump of a high ball cannot be a kick with the foot.

**`R-TOUCH-NEAREST` (v4.2): whose touch it is.** A touch is a kick of a player only if he is the **nearest player** to the ball or within a margin of the nearest: **0.3 m for a ground touch, 0.5 m for an aerial touch** (`R-AERIAL`). Distances are measured as for the reach test of `R-TOUCH`: each player's **closest approach** to the ball over the live frames within **±0.2 s** of the |a| peak. It applies to **every kick**: passes, shots, clearances and the goal shot of `R-GOAL`; for soft kicks (`R-SHOT-SOFT`) the comparison is made at the soft-kick frame itself, with the ground margin. The touch table also records the **contact frame** (the frame of the smallest player–ball distance), for diagnostics.

*Why 0.5 m for aerial touches (team decision).* Tracking measures the torso, not the head: in aerial duels the defender's torso was 0.31–0.41 m nearer to the ball than the header taker's in 6 of the 8 header shots that the 0.3 m margin removed on the six DFL matches.

*Why.* In v4 a touch went to any player within 2 m. In 3 of the 9 shots "away from the goal" the located frame was in fact an opponent's touch (J03WMX: the goalkeeper 0.03 m from the ball), and in 8 of the 14 shots "directed at a team-mate" the nearest player at the kick was someone else.

*How "around the peak" was defined (a comparison run on the six DFL matches with the other v4.2 rules on).* Comparing the players at a single **contact frame** instead of their closest approaches located 4,429 passes (−3.2 points against v4, more than the 2 points allowed), 105 shots and 89 confirmed shots, and it did not find the true kick of the 3 episodes above: they only became "not located". The closest approaches located 4,511 passes (−1.4 points), 116 shots and 96 confirmed shots; without the rule, 4,580, 133 and 110 (all with a 0.3 m margin for every touch; the final numbers are in section 21). *Known cost:* a player who kicks under close pressure, with an opponent's torso more than the margin closer to the ball than his own at its closest, loses the kick (section 19).

**`R-AERIAL` (v4, v4.1): aerial touch.** A frame i in live play where all of these hold:
1. **height**: the ball's height **on its incoming path** to i, the median of the heights of the live samples from i − 6 to i (half a filter window, 7 samples at 25 Hz), is above **1.5 m** (**1.6 m** when the candidate is a shot: `R-SHOT-HEADER`). v4.1: a single sample decided in v4, and one wrong sample (for example a height reset to 0 between airborne samples) could hide a header;
2. **change of direction**: the angle between the ball's horizontal velocity half a filter window before and half a filter window after i (i − 6 and i + 6 samples at 25 Hz, so that the two estimates do not share the impulse) is at least **10°** (v4.1; 20° in v4), with both speeds at least 1 m/s;
3. **change of acceleration**: i is a local maximum of the ball's horizontal |a| of at least **2 m/s²** (v4.1; 4 m/s² in v4: a header gives a softer impulse than a kick, so the ground threshold of 8 m/s² does not apply);
4. **a player within 2 m** (horizontal distance) of the ball within ±0.2 s.

Aerial touches are candidates for passes and clearances (1.5 m) and for shots (1.6 m), with the same kick conditions as `R-TOUCH` (who touches the ball next decides whether it is a kick), and they count as touches when the next touch is searched. Without ball height (`ball_height_supplied = false`) the rule is skipped and the report says so. *Why:* of the 29 header shots in the six DFL matches only 18 were located in v3 and 20 in v4.

**`R-SHOT-SOFT` (v4, v4.2): soft kick (shots only).** A placed shot or a tap-in can give an |a| peak below 8 m/s². For the annotated shooter, a frame i in live play is a soft-kick candidate when:
1. i is a local minimum of the ball–shooter distance d, with d(i) ≤ 2 m;
2. d grows by **more than 1 m within 0.3 s** (`frames(0.3 s)`, 8 samples at 25 Hz);
3. the next touch is not the shooter's own;
4. no ground or aerial touch of the shooter **that is itself a kick** (the ball moves away from him after it) lies within ±0.2 s of i (v4.2: a filter-edge peak on the frame where the ball crosses the goal line and the provider freezes it is not a kick and vetoes nothing; J03WOH, episode 6);
5. the shooter is the nearest player at i, or within 0.3 m of the nearest (`R-TOUCH-NEAREST`).

**`R-SHOT-SPLIT` (v4): kick and block close together (shots only).** A defender who blocks a shot from 1–2 m touches the ball about 0.1 s after the kick. For shots only, two touches **0.08 s** apart (2 frames at 25 Hz) are two separate touches:
- in the alignment, the minimum separation between a shot and the next kick-type event is 0.08 s instead of 0.2 s;
- in the next-touch search after a shot (its outcome), touches from 0.08 s after the kick are considered.

Everywhere else the 0.2 s merge stays.

**Candidates.** For each pass, shot or clearance, the candidates are the kicks of the annotated player inside the search window around the corrected time: ground touches, aerial touches and, for shots, soft kicks. Score = 1 − 0.5 × |t − t_c| / window − 0.5 × d_min / 2 m. At most 8 candidates per event. `sync_method`: `kick_signature`, `aerial_signature`, `soft_kick_signature`, `goal_backwards`, `restart_transition`, `duel_min_distance`, `dead_live_transition`, `frameset_switch`; or, when not located, `no_signature_in_window` or `dropped_by_order`.

**`R-LATE-ANNOTATION` (v4.2): annotations made late, during a stoppage.** When more than **50 %** of the samples in the search window [t_c − window, t_c + window] are dead ball, the search for a kick extends **backwards to the previous live phase**, up to **20 s** before the corrected time. The score then uses the extended half-width (t_c minus the start of the extended window) instead of the window, so a candidate far from the corrected time still scores ≥ 0 and nearer candidates still score higher. The event gets `sync_window_extended = true`, and the report counts these windows. It applies to passes, shots and clearances. *Why:* five header shots were annotated 5–16 s after the contact, three of them while the ball was dead throughout the window (J03WMX, J03WOH, J03WQQ, J03WR9). A window that is mostly live is never extended: an annotation 5–7 s late during live play stays unlocated (section 19).

**`R-ALIGN`: order-preserving alignment.** A Needleman–Wunsch style dynamic programme over the events of a period, in time order, picks at most one candidate per event (or none) to maximise the total score, so that:
- the located frames keep the order of the events;
- two kick-type events are at least 0.2 s apart (0.08 s after a shot, `R-SHOT-SPLIT`);
- located restarts and located goal shots are **fixed anchors**, with weight 2 (an ordinary candidate scores at most 1);
- **(v4.2)** the one exception of `R-DUEL` (section 9.10): an aerial duel won with `WinnerAction = shot` by the player of the next shot may share the shot's frame, before or after it.

Ties are broken by the first state in insertion order, so the result is deterministic.

### 9.6 `R-GOAL` (v4, v4.1, v4.2): goals, confirmed looking forward

Goals are located **first** (step 3), from the ball alone.

1. **Crossings.** For each goal, find every frame f where the ball passes the goal line: s·x(f − 1) < L/2 ≤ s·x(f), with s = +1 for the goal at +x and −1 for the goal at −x, and `ball_status = live` at f − 1. The sample at f is used **whatever its status**: the frames with the ball inside the goal are never discarded (`R-LIVE-ONLY` exception). Interpolate y and z at the line between f − 1 and f. The crossing is **inside the goal** when |y| ≤ 3.66 + 0.11 m and z ≤ 2.44 + 0.11 m (goal 7.32 × 2.44 m and ball radius, IFAB Laws 1 and 2); without ball height, only y is tested. `crossing_method = observed`.
   - **Entry rule (v4.2).** A goal needs the ball to enter the goal **by crossing the goal line between the posts**: its ground projection crosses the goal line between the posts, under the bar. A ball that has already gone over the goal line outside the frame and then appears "inside the goal" from the side or from behind is not a goal. In practice: once the ball is **wholly over** a goal line (its centre beyond the line by more than the ball radius, 0.11 m, IFAB Law 9) after crossing it **outside the frame** (wide of a post or over the bar: where the ball crossed the line decides, not where it is afterwards), it stays **out until it has been back on the pitch for at least 0.2 s** (`frames(0.2 s)`, 5 samples at 25 Hz); crossings while it is out are ignored. One or two wrong provider samples cannot bring it back, while a ball that only grazes the line within the tracking error (within the ball radius) never goes out. *Why:* on J03WOH a corner went out 1.1 m wide of the post at 1.6 m height; two wrong samples at z = 0 put it back in front of the goal, and the next sample "crossed" between the posts: a fake `disallowed_goal` in v4 (episode 2).
   - **`R-GOAL-FREEZE` (v4.2).** Some providers freeze the ball when they set it dead: they hold its last position (and reset its height) until the restart, so a ball still 2 cm to 0.7 m before the line is never seen inside the goal. When the sample after the last live sample is **dead and frozen** (horizontal position held), extrapolate the **last live velocity** for at most **2 frames (0.08 s)**; if the line is reached inside the frame, it is a crossing at the first frozen frame with `crossing_method = extrapolated` (and `extrapolated_s`, the time to the line). Only when **no player is within 1 m** of the ball at the last live sample (nobody can stop it). It is still confirmed only by step 3. *Why:* two goals of the v4 run had no crossing for this reason (J03WMX: last live sample 2 cm before the line at 71 km/h, goalkeeper 1.13 m away; J03WQQ: 0.70 m before the line at 84 km/h, goalkeeper 1.54 m away; episodes 3 and 4).
2. **The ball goes dead.** The ball must become dead within **5 s** of the crossing. If play continues, the crossing is `play_continued`: not a goal.
3. **Look forward to the next restart.** Take the first restart after the crossing: the first restart event whose located frame (step 2) is after f or, if none is located, the first restart event whose corrected time is after f.
   - `KickOff` → **`goal`** (confirmed, `confirmed_by = kickoff`);
   - any other restart → **`disallowed_goal`**. **(v4.2)** It takes `disallowed_reason` from the `Reason` qualifier of a `GoalDisallowed` event of the attacking team between the crossing (minus the search window) and that restart (for example `handBall`), with `goal_disallowed_event_id`;
   - **no restart before the end of the period (v4.1):** a **`goal` confirmed by the end of the period** (`confirmed_by = end_of_period`; the period is extended until a penalty is completed) when the ball entered between the posts (step 1, entry rule), went dead within 5 s (step 2), no restart comes before the end of the period, and **no** `VideoAssistantAction`, `GoalDisallowed`, `Foul` or `Offside` event comes between the crossing (minus the search window, the tolerance of annotated times) and the period's `FinalWhistle`. A `Caution` alone does not block (a player can be cautioned for the celebration). Otherwise (or without a `FinalWhistle`) `unconfirmed`, listed for review. *Why:* the last-minute penalty of J03WR9 (96') was followed only by the final whistle, 78.9 s later, with no event in between (episode 5).
4. **Backwards to the shot.** The shot of a goal, of a disallowed goal or **(v4.2) of an unconfirmed crossing** is the **last kick by a player of the team attacking that goal up to and including the crossing frame f** (v4.2; f − 1 in v4), searched backwards to the last dead→live restart before it. Touches by opponents in between (deflections, goalkeeper fingertips) are allowed. Kicks are the candidates of section 9.5 (ground touches, aerial touches ≥ 1.6 m and soft kicks), here for any player of the attacking team, with the in-goal samples in the ball's kinematics (`R-LIVE-ONLY` exception, section 9.4). *Why (v4.2):* on J03WOH the scorer tapped the ball in 0.2 s before the line; the |a| peak fell on the crossing frame, at the edge of the live segment (the provider froze the ball on the next sample), so v4 found an earlier pass across the box instead (episode 6).
   - If the action starts with a restart taken by the attacking team and no later attacking kick exists (a direct free kick, a penalty), the kick is the restart's dead→live frame (`restart_transition`).
   - If no attacking kick exists (for example an own goal), the goal has no located shot and is listed.
   - **Attribution with a deflection (v4.1).** A team-mate's deflection is already the last attacking kick: the goal goes to the **team-mate**. When a player of the **defending** team deflects the ball (a ground or an aerial touch, `R-TOUCH`, `R-AERIAL`) between the kick and the crossing, fit the direction of the flight **before the first deflection** (`R-SHOT-DIRECTION`, posts only):
     - it was going on target (the fitted line crosses the goal line between the posts, ball radius included): the goal stays with the **shooter**;
     - it was not (wide, or away from the goal): an **own goal** of the last defending player who touched the ball (`own_goal = true`, `scorer_id` = that player, listed for review);
     - the direction cannot be fitted (fewer than 3 samples): the goal stays with the shooter.

     A high |a| bump without the aerial change of direction (for example on a curling shot that a diving goalkeeper misses) is not a deflection; nor is a peak within ±0.2 s of the crossing, which can be the ball stopping in the net or a provider freeze at the filter edge (as for `R-WOODWORK`, step 3: on J03WMX the last live sample before the freeze was given to the goalkeeper 1.13 m away).
   - `scorer_id` is the attributed scorer. If it is not the annotated scorer, set `shooter_mismatch = true` and list the goal.
5. **Anchor.** The goal shot is matched to the annotated goal shot of the same team, corrected time within the search window of the crossing: an open-play `ShotAtGoal` with a goal outcome, or **a set-piece shot** (a `Penalty` or a direct `FreeKick` whose nested sub-type is `ShotAtGoal`; DFL nests the shot inside the restart). For a disallowed goal or an unconfirmed crossing any annotated shot of the team can be matched, a goal outcome first. It gets `sync_method = goal_backwards` (or `restart_transition`) and is a fixed anchor of `R-ALIGN`. A set-piece shot stays a restart: it never enters the open-play shot checks or xG.

**Logged:** crossings by result (`goal`, `disallowed_goal`, `unconfirmed`, `play_continued`), by method (`observed`, `extrapolated`) and goals by confirmation (`kickoff`, `end_of_period`); annotated goals without a goal crossing; goal crossings without an annotated goal; shooter mismatches; own goals; disallowed goals by reason.

**Why.** In v3 a goal was checked only at the crossing (11 of the 13 annotated goals confirmed). Balls over the bar that drop behind the goal, and a dead ball that the provider freezes and whose height it resets, looked like goals. The restart that follows tells them apart: only a goal is followed by a kickoff. v4 confirmed 13 of the 16 annotated goals (3 of them penalties); v4.2 confirms 16 of 16, with one correct disallowed goal (handball, J03WMX) and no fake goal.

### 9.7 Shots

Each located shot has a **free flight**: from the kick frame k to the end frame e (inclusive), the first of: the next touch by any player (ground or aerial), the goal crossing (`R-GOAL`), a woodwork contact (`R-WOODWORK`), or the last live frame before the ball goes dead; at most 10 s. A touch or a deflection mid-flight ends the free flight, so the direction is always measured on the stretch **before** it, and an opposing touch gives the outcome `blocked` or `saved` (v4.1).

**`R-SHOT-DIRECTION` (v4, v4.1).** The direction is measured on the **whole free flight**, not at one instant.
1. Fit x(t) and y(t) linearly in time (least squares, attacking-normalised coordinates) over the live ball samples from k to e; at least **3 samples** are needed, otherwise the direction is `direction_undetermined`.
2. If the fitted x-velocity is not positive (towards the attacked goal), the direction is `away_from_goal`.
3. `shot_goal_line_y_m` = y of the fitted line at x = L/2; for a goal or disallowed goal, the interpolated crossing y of `R-GOAL`.
4. The direction passes when the ball crosses the goal line within the **margin** beyond a post: **8 m** (v4.1) for a kick more than **16.5 m from the goal line** (outside the box) and for a shot **above the power threshold** of `R-SHOT-POWER`; **6 m** otherwise (`shot_wide_margin_m` records it). Otherwise `direction_outside_8m` or `direction_outside_6m`.

**`R-SHOT-NOT-PASS` (v4, v4.1).** It is **not a shot** if the ball is directed at a team-mate who **really receives it** (v4.1): the next touch is a team-mate's, and at the kick frame that team-mate was **ahead of the ball** (positive projection on the fitted direction, before the goal line) and **within 2 m** of the fitted trajectory line. Reason `directed_at_teammate`. The check is skipped when the direction is undetermined, and overridden by `R-SHOT-POWER`. *Why (v4.1):* in the v4 run the team-mate on the line received the ball in only 2 of the 14 shots "directed at a team-mate"; several were real shots at 80–90 km/h that passed him.

**`R-SHOT-POWER` (v4, v4.1).** `shot_power_kmh` = the peak valid live ball speed in the 0.5 s after the kick, stopped before the next touch. When it is above the **99th percentile of DFL pass speeds** (the same measure on located passes: **94.86 km/h** in the v4 calibration; read from `calibration/ball.json`, `pass_speed_ms.p99`, or from the configuration when absent) and the kick is **not a header** (ball above 1.5 m at the kick, `shot_header`; the annotated body part only without ball height), `shot_power_evidence = true`: the ball cannot be a pass, so **it is a shot** (v4.1): `R-SHOT-CHECK` confirms it with basis `shot_power`, before the direction step. Power never excludes a slow shot. *Why (v4.1):* no pass of the six DFL matches is faster than this, so a faster kick is a shot even when its flight is short or deflected.

**`R-WOODWORK` (v4, v4.1, v4.2).**
1. **Candidate:** an |a| peak (≥ 8 m/s², any height) in live play whose ball **path** comes within **0.5 m** of a post or of the crossbar of either goal. The path is the set of segments between consecutive samples within ±0.2 s of the peak (starting in live play, without position jumps); the distance is the **segment-to-segment** distance (v4.2), not the distance of a single sample: at 79 km/h the ball moves 0.88 m between two frames, and the SYN-001 post at 58' stayed 0.57 m from the nearest sample. When the ball **bounces between two samples**, its path between them is not one straight segment but the incoming line (through the two samples before) and the outgoing line (through the two samples after) meeting at the contact: that meeting point is part of the path when it lies ahead of the last sample before and behind the first sample after, within 2.5 steps in all. On SYN-001 at 58' it puts the contact 0.18 m from the post. Posts: vertical segments x = ±L/2, y = ±3.66 m, 0 ≤ z ≤ 2.44 m; crossbar: x = ±L/2, |y| ≤ 3.66 m, z = 2.44 m; 3-D. The 0.5 m covers the post thickness, the ball radius and the tracking error. Without ball height, only the posts are tested (2-D) and the report says that the crossbar cannot be detected.
2. **Decision (v4.2):** the candidate is woodwork, not a player's touch, when
   - (i) the path is within **0.3 m** of the frame and **every player is at least 1 m** from the ball, or
   - (ii) the ball is **closer to the frame than to any player by more than 0.5 m**.

   Player distances are the minimum over the live frames within ±0.2 s of the contact sample (the path sample nearest to the frame); for a ball **higher than 2.3 m** the distance is 3-D, from a point 2.3 m above the player's position (a standing player's reach). The v4 condition "no player within 2 m" is dropped (v4.1). A woodwork contact is never a player's touch.
3. **The net is not the woodwork (v4.2, team decision).** No woodwork once the ball is in the goal: a candidate is dropped when the ball enters the goal between the posts (a crossing of `R-GOAL` step 1, observed or extrapolated, after which the ball goes dead within 5 s, step 2) within **±0.2 s** of the |a| peak. Such a peak is the ball stopping in the net, spread by the filter, or a contact on the way in, which is a goal and never a woodwork hit (`R-WOODWORK-STAT`). It is not a woodwork contact and counts in no woodwork statistic. *Why:* in the first v4.2 run 6 of the 9 contacts on the six DFL matches were goals entering just inside a post or just under the bar (one of them the goal extrapolated over a frozen ball).

   *Why:* in v4, 0 of the 2 annotated woodwork shots were recognised: on J03WMX the ball came 0.18 m from the post with a defender 1.18 m away (v4 called it a block); on J03WR9 the ball came 0.17 m from the bar at 2.49 m with the goalkeeper 0.80 m away horizontally (episode 9).

**`R-SHOT-OUTCOME` (v4, v4.2).** The observed outcome (`shot_outcome_observed`) is, in this order:
1. **`goal`** or **`disallowed_goal`**: the shot is the goal shot of `R-GOAL` (the goal search follows opponent touches and stops at a team-mate's touch or at a dead ball);
2. **(v4.2) blocked at the kick:** when the free flight heads **away from the goal** (`away_from_goal`) and an **opponent is within 1 m** of the ball at the kick frame, the kick and the block fell in the same |a| peak and the fitted flight is the rebound: **`blocked`**, or **`saved`** when that opponent is the goalkeeper (the meaning of a goalkeeper touch below). `shot_blocked_at_kick = true`;
3. otherwise the **first** of these in the free flight:
   - **`woodwork`**: `R-WOODWORK`;
   - **`saved`**: a touch by the **opposing goalkeeper**, in live play;
   - **`blocked`**: a touch by an opposing outfield player **within 2 s** of the kick;
   - **`wide`**: the flight ends with the ball out of play;
4. otherwise **`none`**.

A shot that hits the woodwork and then goes in is a goal.

**`R-SHOT-CHECK` (v4, v4.2): the decision, in this order.**

| Step | Condition | `type_check` | Basis or reason |
|---|---|---|---|
| 0 | Shot not located | `not_confirmed` | `event not located` |
| 1 | Outcome `goal` or `disallowed_goal` | `confirmed` | `goal` / `disallowed_goal` |
| 2 | **(v4.2)** Blocked at the kick (`R-SHOT-OUTCOME` step 2) | `confirmed` | `blocked` / `saved` |
| 3 | `R-SHOT-NOT-PASS` holds (the team-mate receives it) and no power evidence | `not_confirmed` | `directed_at_teammate` |
| 4 | **(v4.1)** Power evidence: above the pass p99, not a header | `confirmed` | `shot_power` |
| 5 | `R-SHOT-DIRECTION` fails | `not_confirmed` | `direction_outside_8m`, `direction_outside_6m`, `away_from_goal`, `direction_undetermined` |
| 6 | Outcome `woodwork`, `saved`, `blocked` or `wide` | `confirmed` | the outcome |
| 7 | Otherwise | `not_confirmed` | `no_coherent_outcome` |

Every `not_confirmed` shot is in the review list of the report, with its outcome, `shot_goal_line_y_m` and the annotated outcome.

**Why.** In v3 only 66 of the 144 shots were confirmed: a wide or blocked shot was confirmed only when the ball headed into the goal frame ±1 m, measured 0.2 s after the kick. v4 confirmed 84; the review of the v4 report showed that the main cause of the remaining ones was the attribution of the kick (anyone within 2 m), more than the direction. The literature confirms that the hardest part, with independent event and tracking data like ours, is finding the instant of the action: with rules, shot detection reaches F = 0.65 (Bischofberger et al. 2024). A possession change as an extra trigger (Vidal-Codina et al. 2022) is an open point (section 19).

### 9.8 Passes

**`R-PASS-KICK` (v4).** A pass is **confirmed by its kick**: a located pass (ground or aerial kick of the annotated player) is `confirmed`, basis `kick`. It is `not_confirmed` only when it is not located (reason `event not located`).

**`R-PASS-OUTCOME` (v4).** The outcome is kept **separately** (`pass_outcome`), from the next touch after the kick:
- `teammate`: the next touch is a team-mate's;
- `opponent`: the next touch is an opponent's;
- `out_of_play`: the ball goes dead before any touch;
- `open_space`: no touch within 10 s while the ball is live, after the ball travelled at least 5 m;
- `unresolved`: anything else.

The report compares `pass_outcome` with the provider's evaluation (diagnostic only). *Why:* in v3 an intercepted pass was `not_confirmed` ("next touch by an opponent"), which mixed two questions: did the pass happen, and did it succeed.

### 9.9 `R-CLEARANCE` (v4): clearances

**Location.** As a pass: a ground or aerial (≥ 1.5 m) kick of the annotated player (DFL: `OtherBallAction` with `DefensiveClearance = true`).

**Type check.** A located clearance is `confirmed` when **at least 4 of these 7 conditions** hold (all in live play, `R-LIVE-ONLY`); `clearance_conditions` keeps the seven results and `clearance_score` their count:

| # | Condition | Exact test |
|---|---|---|
| C1 | Very short possession | The clearing player has no other touch in the 1 s before the kick |
| C2 | Powerful kick or high ball | Peak valid ball speed in the 0.5 s after the kick ≥ 72 km/h (20 m/s), or ball z ≥ 3 m during the free flight |
| C3 | Ball towards a zone without team-mates, away from the own goal | At the end of the free flight the ball is further from the own goal than at the kick (larger x_att), and no team-mate of the clearing player is within 5 m of it |
| C4 | Pressure on the ball **(v4.1)** | At the kick, **at least one opponent within 4 m** of the ball (v4: at least 3 players within 3 m, which almost never held) |
| C5 | Header by a defender in his own crowded penalty area | An aerial touch (≥ 1.5 m) by a player of group CB or FB (section 16), with the ball in his own penalty area and at least 3 opponents inside it |
| C6 | Start from the own half | x_att of the ball at the kick < 0 |
| C7 | Possession change or ball out soon after | Within 3 s of the kick, the next touch is an opponent's or the ball goes dead |

Otherwise `not_confirmed`, with the conditions met listed.

### 9.10 `R-DUEL` (v4.2): tackles and duels

Located at the live frame that minimises max(d(winner, ball), d(loser, ball)), among the local minima with both players within **3 m** of the ball. `type_check = not_applicable`.

**(v4.2) An aerial duel won with a shot.** DFL annotates an aerial duel (`Type = air`) with `WinnerAction = shot` just before the winner's header, but its location falls on the header contact, 1 frame before or after it, so the order constraint of `R-ALIGN` could drop the shot. When such a duel is won by the player of the **next** shot (the next kick-type event), the pair may share a frame, in either order: the duel must still follow the events before it, but its own frame does not constrain the events after it. The order constraint is relaxed for that pair only. *Why:* three header shots were dropped this way in v4 (J03WOY, J03WR9 ×2; episode 10).

### 9.11 `R-HIGHLIGHT`: anchor highlights on the physical outcome, then go backwards

| Outcome | How it is detected in the tracking |
|---|---|
| Goal | `R-GOAL` (`goal`; a `disallowed_goal` is a separate moment) |
| Woodwork | `R-WOODWORK` |
| Save | `R-SHOT-OUTCOME` = `saved` |

From the outcome, walk **backwards** to the start of the action: the later of the last change of possession to the attacking team (provider's possession column) and the last dead→live restart. The clip runs from there to a few seconds after the outcome. Timestamps are never trusted for this.

### 9.12 `R-LIVE-BUFFER`: live mode, an event is never processed before its frames

- Each incoming event waits in a buffer until the tracking stream has passed its corrected timestamp plus the search window. It is then located **backwards** in the frames already received.
- **Checks that look forward** (`R-GOAL` waits for the next restart; shot and pass outcomes for up to 10 s; blocks for 2 s; clearance conditions C3 and C7) are evaluated only when the frames they need have arrived. Until then `type_check = pending`.
- Events whose frames never arrive are dropped from event-anchored metrics and logged.

### 9.13 Where these rules come from

- the paper (Bassek et al. 2025): the offset statistics, and cost-function synchronisation of passes and shots with DataBallPy and Needleman–Wunsch alignment;
- the literature: locating events from trajectories (ETSY; ELASTIC); rule-based event detection and its limits (Bischofberger et al. 2024); possession changes as triggers (Vidal-Codina et al. 2022);
- **our own decisions**, from the six DFL matches and the team's review of the v3 reports: kickoff as period start, restarts at dead→live transitions, substitutions at the frame-set switch, goals confirmed by the next kickoff, shots by direction plus outcome, passes by the kick, clearances by 4 of 7 conditions, backward highlights and the live buffer;
- **the review of the v4 reports (v4.1 / v4.2)**, episode by episode with the figures of each case: the goal entry rule and the frozen ball, the end of the period, the attribution of kicks to the nearest player, the woodwork path, late annotations and the duel–shot pair (section 21).

---

## 10. Metrics

### 10.1 Convention: the classic one, with splits

Physical metrics follow the convention used in published football data (FIFA, data providers).
- They are computed over the **whole time on the pitch**, ball in play **and** out of play.
- They are **also** reported separately for **ball in play / ball out of play** and **in possession / out of possession**.

**Ball status** comes from the provider's data (our synthetic data always includes it). It is **never derived from a stationary ball**: a stationary ball does not mean the game is stopped. If a provider does not supply it, the ball-status splits are not reported and the report says so.

Always use **valid samples only** (section 6). Always state the method and parameters.

### 10.2 Precision of reported values

**Distances (`R-DIST-PRECISION`).** Let e be the share of the player's time on the pitch that is **not directly measured** (estimated, discarded or excluded). Distances are reported with a precision that matches e:

| Not measured (e) | Reported as | Example |
|---|---|---|
| e < 1 % | Value as computed | 9.87 km |
| 1 % ≤ e < 5 % | "≈", to 0.1 km | ≈ 9.9 km |
| e ≥ 5 % | "≈", to 0.5 km | ≈ 10 km |

On J03WPY e is 0.4–5.6 % per player, so most distances carry "≈" to 0.1 km.

**Accelerations (`R-ACC-REPORT`, v4).** An acceleration value from a sample with `acc_status = approx` is reported as **"≈ value"**, to 0.1 m/s² (for example "≈ −8.9 m/s²"). Values from `measured` samples are reported as computed. `noise` samples are never used. A player's peak acceleration and deceleration are the maximum and minimum over valid samples with `acc_status` = `measured` or `approx`, with "≈" when the peak sample is `approx`.

The Inspector mode shows the measured, estimated and discarded parts and the coverage.

### 10.3 Example

"Who was the fastest player in the first half?" is the maximum of **our** speed over first-period samples with `valid_kinematics = true` (never estimated samples), per player. The answer includes the match-clock time of the peak, so that the Inspector mode and a replay can show it.

### 10.4 Candidates

The final list of metrics is decided by the team; definitions are in [`Materials/METRICS.md`](../Materials/METRICS.md). Candidates:

| Metric | Definition |
|---|---|
| Total distance | Σ (valid speed × ΔT) plus estimated gap segments, over the whole time on the pitch; with splits; precision rule of 10.2 |
| Distance per speed zone | FIFA zones, `R-ZONES` (10.5); with splits |
| Sprints | `R-SPRINT` (10.5) |
| Top speed | Maximum of our speed over valid, measured samples (km/h) |
| Peak acceleration and deceleration | `R-ACC-REPORT` (10.2), with "≈" when uncertain |
| Peak intensity | Highest distance per minute over rolling 1-minute and 5-minute windows (tracking clock) |
| Shot speed | Peak ball speed in the 0.5 s after `sync_frame` of a confirmed shot, valid ball samples |
| Shots, shots on target, woodwork | `R-SHOT-COUNTS`, `R-WOODWORK-STAT` (10.6) |
| **Ball circulation speed in possession** | Median and distribution of ball speed while live and in a team's possession, per team, per third and per 5 minutes; a variant excludes shots and clearances |
| Pass length and outcome | From synchronised passes, using the locations at `sync_frame` and `pass_outcome` |
| Effective playing time | Time with `ball_status = live` |
| Metabolic power (optional) | Osgnach et al. 2010 |
| Acceleration counts (future) | Only if the team adopts the acceleration effort thresholds of section 8 |

### 10.5 Speed zones and sprints (v4)

**`R-ZONES`.** Distance per zone = Σ (speed × ΔT) over valid samples whose speed falls in the zone. Zones (FIFA): **[0, 7), [7, 15), [15, 20), [20, 25) and [25, ∞) km/h**. Estimated gap distance is not assigned to a zone ("unassigned"), because its speed is not measured. The zones plus the unassigned part add up to the total distance.

**`R-SPRINT`.** A sprint is a run of **consecutive valid, measured samples** with speed **at or above 25 km/h** (the FIFA sprint threshold, the same 25 km/h boundary as the top speed zone: exactly 25.0 km/h counts), lasting **at least 1 s** (`frames(1 s)`, 25 samples at 25 Hz). Any invalid or estimated sample ends the run. Two sprints separated by fewer than `frames(1 s)` valid, measured samples below the threshold count as one; an invalid or estimated sample is never bridged (`Materials/METRICS.md` §19, decision 21; decided in the metrics review, implemented in the metrics work).

### 10.6 Shots, shots on target and woodwork (v4)

**`R-SHOT-COUNTS` (v4, v4.1).**
- Shot statistics use the **shot events and their outcome label**: the provider's annotation for real data, the label computed by the simulation for synthetic data, mapped to English (section 16.3). The checks of section 9.7 never relabel a shot; whether a metric counts only confirmed shots is defined in `Materials/METRICS.md` §8.
- **Shots on target = goals + saved shots.** Woodwork, blocked and wide shots are **not** on target (the Opta and FIFA convention).
- **(v4.1) Penalties count** in shots, shots on target and goals, as Opta does; so do the other set-piece shots (direct free kicks), which DFL nests inside the restart event. They are also counted separately (`penalties`, `penalty_goals`, `set_piece_shots`), and the xG of a penalty is separate (a fixed 0.79, section 11). A set-piece shot whose taker is not annotated counts for its team only. This is the pipeline's count (`shot_statistics`); the app engine follows the same convention in its own task.

**`R-WOODWORK-STAT`.** **Woodwork hits** are a statistic of their own, per team and per player: the shots whose outcome is `woodwork`. A shot that hits the woodwork and goes in is a goal, not a woodwork hit.

The observed outcome (`shot_outcome_observed`) is compared with the label in the report (a cross-table) and drives the highlights (`R-HIGHLIGHT`).

---

## 11. Calibration outputs for the synthetic generator

Save to `calibration/` as JSON: aggregates only, committed, with CC-BY attribution. Each file carries metadata: `source`, `matches_used`, `config_hash`, `input_hashes`, `library_versions`, `created_utc`, `pipeline_version`.

- **Physical profiles per role group** (section 16): distributions and percentiles of total distance and its splits, distance per zone, sprints and top speed; per-minute profiles that show fatigue.
- **Attribute scale 1–99** (`attribute_scales.json`, v4.2): mappings from attributes to physical values (top speed → pace, distance → stamina, shot speed → shot power, pass success → passing). Each table stores **only 21 quantiles** (p2.5, p5, p10, p15, …, p90, p95, p97.5; no minimum and no maximum), like `realism_reference.json`. The 99 `values` that the generator reads (`values[k−1]` is the physical value of attribute k) are **interpolated linearly from those quantiles**, flat below p2.5 and above p97.5 (attributes 1–2 equal p2.5, 98–99 equal p97.5). *Why:* with a few player-matches per role group, the percentiles 1–99 were almost the values of single real players; 21 quantiles without minimum or maximum store far fewer of them, though a stored quantile can still coincide with one (section 19, point 10: accepted). **Shot power** uses the shot speeds without headers (see **Ball** below). The file format the generator reads does not change.
- **Expected goals** (`xg_model.json`, v4.2, `Materials/METRICS.md` §9): `logit(xG) = a + 2.84·θ + b·n`, with θ the visible goal angle and n the opposing outfield players in the shot triangle. The angle slope **2.84 is fixed** (an offset: the Soccermatics angle-only fit on 8,451 shots of the 2017/18 Premier League in the open Wyscout data, Pappalardo et al. 2019, CC BY 4.0); only **a** and **b** are fitted, on the located open-play DFL shots (label: the annotated `SuccessfulShot`), two parameters as the rule of about 10 events per variable allows (Peduzzi et al. 1996), with a small L2 penalty on the standardised b. A **penalty** is a fixed **0.79**. The file keeps the format the generator reads: `features` = [`distance_m`, `visible_angle_rad`, `opponents_in_triangle`], `coef` = [0, 2.84, b], `intercept` = a; it also records the fixed slope and its source, `penalty_xg`, the numbers of shots and goals, a calibration table (predicted vs observed goals), the leave-one-match-out log-loss against a constant rate, the checks (b < 0; 6 m central > penalty spot > edge of the box; the sum of xG against the goals) and examples (6 m central, penalty spot in open play, edge of the box, a tight angle). *Why:* the first fit (three free slopes on 13 goals) gave the visible angle a negative coefficient; the generator uses the same model to decide which shots score, so it is re-run after the calibration is rebuilt.
- **Event model**: event-type frequencies, zone transitions, pass length and success by zone, shot locations and outcomes (including woodwork). Use synchronised locations. **The provider's annotated outcomes are the calibration truth** (for example how often a pass succeeds in each zone); our trajectory checks are diagnostics and never relabel them.
- **Ball**: distributions of shot speed, pass speed and ball circulation speed. **Shot speed (v4.2) excludes headers, everywhere in the calibration**: `ball.json` `shot_speed_ms`, the `shot_power` table of `attribute_scales.json` and the shot-speed metric of the realism reference and report (`shots.shot_speed_kmh`). A header is a shot with the ball **above 1.5 m at the kick** (`aerial_min_height_m`), the same test as the header exception of `R-SHOT-POWER` (the pipeline's `shot_header` flag; the annotated body part only without ball height). Soft shots stay in. Shot speed is the peak valid ball speed in the 0.5 s after the kick of a confirmed open-play shot. *Why:* a header is slower for another reason than a weak kick, and the app reports shot speed without headers too. `ball.json` stores the **99th percentile of pass speed** (`pass_speed_ms.p99`, not stored by the v3 calibration, which stops at p90), which `R-SHOT-POWER` reads; it is recomputed whenever the calibration is rebuilt after an analyzer run.
- **People in calibration (v4.2).** The committed match profile of a real match names people by anonymous labels (`R-ANON-LABELS`, section 13); the calibration maps them back to the IDs of the frame-level files with the git-ignored label table, in memory only. No calibration file stores a person or object ID.
- **Provider artefact profile**: rates of jumps, swaps, frozen runs, tracking-area edges, parking values and substitution inheritance per match. The synthetic data can then include realistic artefacts.

---

## 12. Synthetic data rules

Updated 2026-10-22. The generator lives in `generator/`; its outputs are in `synthetic/`.

**Format and language**
- The generator follows the **structure** of the DFL files (for example `match_info.json` mirrors DFL `matchinformation`), so the same adapters read real and synthetic data.
- **All codes are in English, natively**: player roles (GK, LCB, RCB, CB, LB, RB, LWB, RWB, CDM, LDM, RDM, LCM, RCM, LM, RM, CAM, LW, RW, ST, LS, RS), formations, referee roles and attribute values. The German-to-English mapping of section 16 is never needed for synthetic data; the adapters accept both DFL and English codes.
- No `Delete` events, ever.

**Content (the universe)**
- **No surname is repeated** among all the people of the universe (players, staff, officials, people named in stories). At most 1–2 exceptions in the whole universe, only when the full names are clearly distinct, and **never** between a player and an official, a coach or a staff member, nor within the same club or the same match. Repeated first names are kept to a minimum: at most once per match, never the same as an official's. The validator checks this.
- Fictional names only; the blocklist of real people, clubs, stadiums and brands, and of alcohol, betting and tobacco words, always applies.
- The universe contains the data that **Kit Man** needs to answer fans.
- The universe is rich enough for the **games**. The grid criteria are **only the clubs of a player's career and nationality** (no trophies, awards, role or age). Careers spread across the clubs (other than the two teams of the match) and foreign nationalities (at least 3, with 4 or more players each across the two squads) are varied enough that every club × club and club × nationality crossing used in a grid has at least 2 valid answers; the validator checks this. Each career step carries its transfer fee (zero for loans and free transfers). Trophies of clubs and players stay in the universe as content for Kit Man. **The valid answers of the games are only the players of the two teams of the match.**

**Match data (tracking and events)**
- Ball status (live or dead) and ball possession are **always present**.
- Realistic measurement noise on positions (σ ≈ 0.1 m, larger at high speed) and, optionally, the provider artefact profile from section 11.
- In the live stream, **every event is emitted after its frames**.
- Provider-style `S` and `A` values are **optional** and used **only in tests** (section 7).
- Kinematics stay within the physical limits of section 8. Ball speeds follow the realism range of `Materials/PHYSICAL_LIMITS.md` (maximal kicks 18–35 m/s).
- **Realism report**: synthetic vs real distributions, with histograms and Kolmogorov–Smirnov tests, produced by running the same preparation pipeline on both (the Linesman check).

---

## 13. Preparation report

`reports/qc/<match_id>/README.md` (aggregates only; frame-level outputs stay in the git-ignored `reports/qc/frames/`). **No pass/warn/fail.** Every row names its rule ID.

**`R-ANON-LABELS` (v4.2): anonymous labels for real data.** Every file written to `reports/qc/<match_id>/` (README, `qc_results.json`, `match_profile.json`, `acc_window_sensitivity.*`) and `reports/qc/SUMMARY.md` for a **real** match contains **no provider object ID** (for DFL, `DFL-OBJ-…`). A player is `<team side> <role group> <n>`, for example `home CB 1`: the role group comes from the position mapping of section 16.1 (`unassigned` for players without a position code), and n counts within the team and the role group in the order of the object IDs, so the labels are deterministic. A tracked object outside the line-up is `<team label> <n>` (for example `referee 1`). The label ↔ ID table is written **only** to the git-ignored `reports/qc/frames/<match_id>/id_labels.json`, so that episodes can be found again on the local machine; frame-level outputs keep the IDs. Synthetic matches (fictional IDs) are unchanged. A test fails if `DFL-OBJ-` appears in any report file of a DFL-format match.

The report contains:

1. **Match profile**: everything discovered, including any inferred value, the tracking-area edges and the parking values.
2. **Preparation log**: one line per handling rule (section 5.1) with counts.
3. **Exclusion and gap summary**: share of samples and of time excluded, estimated and discarded, by reason, overall and per player (role code in English); one row per substitution (`R-SUB-INHERIT`).
4. **Acceleration band**: runs and samples classified `noise` and `approx`, by sign and per player (`R-ACC-BAND`).
5. **Time alignment and events**: kickoff sources, tracking start offset, event clock offset and dispersion; events located by kind and method, synchronisation certainty; goal crossings by result (`R-GOAL`); shots by observed outcome against the annotated outcome; passes by `pass_outcome` against the provider's evaluation; clearances by score; the **review list**: every shot, pass and clearance `not_confirmed`, every goal `unconfirmed` or with `shooter_mismatch`.
6. **Provider comparison (tests only)**: our speed, acceleration and step against the provider's, and our synchronised frame against the provider's own (for example DFL `CalculatedFrame`), where supplied.
7. **Diagnostic anomaly catalogue**: counts by suspected cause; unknown jumps split by live play and dead ball, with the event types within ±2 s (`R-JUMP-CONTEXT`); examples of `unknown`. It is for later study, not a warning list.
8. **Parameters, input hashes, library versions.**
9. **Plain-English summary** for the team: what the data can and cannot support.

`reports/qc/SUMMARY.md` compares the matches and contains the **acceleration window sensitivity** table (`R-ACC-WINDOW-TEST`).

---

## 14. Testing

`pytest` with small synthetic fixtures (never real data), with values worked out by hand. Each test name carries the rule ID.

**Loading and preparation**
- `R-SORT`, `R-DEDUP`: unsorted and duplicated input is sorted and de-duplicated without error.
- `R-DELETE`: `Delete` events are dropped.
- `R-MAP-EN`: German codes and formations are mapped to English; DFL shot outcomes are mapped (section 16.3).
- `R-UNITS`: unit inference works for metres, centimetres and normalised coordinates.
- A second tiny format (for example CSV) loads into the same canonical schema.

**Reference system, edges and parking values**
- Corner-origin and centre-origin inputs give the same canonical coordinates; flipped y is detected.
- `R-EDGE`: an edge is **discovered** on a fixture with a different pitch size and an edge at a different distance, including one beyond a touchline; a value that is not the outermost, or that comes from one frozen position only, is rejected.
- `R-PARKING`: a value beyond a touchline shared exactly by frozen runs of 2 players (≥ 25 samples in total), not the outermost, is a parking value; its samples, **including the first sample of each run**, are `parked`, with 1 s on each side; the resume distance is measured from the sample before the run. A single frozen player beyond the line is not a parking value (only `frozen`). An edge value is never a parking value.

**Validity, gaps and kinematics**
- `R-SG-SPEED`: a straight run at 8 m/s gives 8 ± 0.05 m/s. `R-SG-ACC`: a constant 3 m/s² acceleration is recovered within ±0.1 m/s² with windows of 13, 19 and 25 samples.
- `R-JUMP`: a 2 m jump creates a ±1 s exclusion window.
- `R-GAP`: a frozen run that resumes 3 m away is estimated (`estimated = true`); one that resumes 8 m away is discarded.
- `R-SWAP-RESTORE`: a mutual swap corrected 100 s later has its labels restored; one corrected 130 s later is windowed only; both get ±1 s for both players.
- `R-SUB-INHERIT`: an incoming track starting < 0.5 m from the outgoing player's last position, with a jump 10 s later, is invalid until the jump plus 2 s; the same without a jump is invalid for 1 s only; a track starting 0.6 m away has no inheritance, no jump search and is invalid for 1 s only; the halfway-line distance is logged and changes nothing.
- `R-EXIT-REENTRY`: a frozen or parked run that ends the track is an exit.
- `R-ACC-BAND`: exceedances of 4 and 5 samples (0.16 and 0.20 s) are `noise`, with `valid_kinematics`, speed and distance unchanged and no window; an exceedance of 6 samples (0.24 s) is `approx` and its value is kept; a speed of 39 km/h is `limit` with ±1 s.
- `R-ACC-WINDOW-TEST`: the mask, the maximum speeds and the distances are identical with windows of 13, 19 and 25 samples; the test table lists the exceedances per window.
- `R-JUMP-CONTEXT`: an unknown jump with an event 1.5 s later lists that event; an event 2.5 s later is not listed; a jump with the ball dead is counted under dead ball.
- `R-BALL-WINDOW`: a live-play ball jump excludes ±0.25 s (6 frames at 25 Hz on each side); a restart excludes the first 0.25 s; a ball sample 0.3 s after a restart is valid. Ball jumps at dead→live restarts are ignored.

**Metrics and precision**
- "Fastest in the first half" ignores jump spikes and estimated samples.
- Total distance includes ball-out-of-play time, and the splits add up to the total.
- `R-DIST-PRECISION`: the rule gives "9.87 km", "≈ 9.9 km" and "≈ 10 km" for e = 0.5 %, 3 % and 8 %.
- `R-ACC-REPORT`: a peak from an `approx` sample is shown with "≈"; `noise` samples never give a peak.
- `R-ZONES`: zone distances plus the unassigned estimated distance add up to the total.
- `R-SPRINT`: a 1.2 s run at 27 km/h is one sprint; a 0.9 s run is none; an estimated sample ends a sprint.
- `R-SHOT-COUNTS`, `R-WOODWORK-STAT`: on target = goals + saved; woodwork counted separately, per team and per player.

**Time and events**
- The match clock starts at the kickoff event.
- `R-CLOCK-OFFSET`: with a simulated event delay (for example +2 s ± 1 s), the offset is recovered and events are located on the right frames.
- `R-LIVE-ONLY`: a ball |a| peak with a player within reach during dead ball creates no touch; a duel minimum in dead ball is not used.
- `R-GOAL`: a crossing inside the goal followed by a `KickOff` is a goal; followed by a `FreeKick` it is a disallowed goal; a ball over the bar is not a goal even if a kickoff follows; a crossing whose first sample beyond the line is dead is still found; a crossing after which play continues for more than 5 s is not a goal; the goal shot is the last attacking kick before the crossing, with an opponent deflection in between.
- `R-AERIAL`, `R-SHOT-HEADER`: a ball at 1.7 m with a 30° change of direction, a 5 m/s² peak and the shooter within 2 m gives a header shot; at 1.55 m it is an aerial touch for a pass or clearance but not a header shot; without a change of direction there is no aerial touch.
- `R-SHOT-SOFT`: a placed shot with an |a| peak of 6 m/s² and a ball–shooter distance growing by 1.2 m in 0.3 s is located; with 0.9 m it is not; a dribble (next touch by the same player) is not.
- `R-SHOT-SPLIT`: a shot blocked 2 frames after the kick is located at the kick, with outcome `blocked`; two touches 2 frames apart in a pass are one touch.
- `R-SHOT-DIRECTION`: a line crossing the goal line 5.9 m beyond a post passes; 6.1 m fails; fewer than 3 flight samples is undetermined.
- `R-SHOT-NOT-PASS`, `R-SHOT-POWER`: a team-mate 1.5 m from the line, ahead of the ball, makes a slow shot `not_confirmed`; with power above the 99th percentile the check is overridden; a team-mate behind the ball is ignored; a slow shot that is saved is confirmed.
- `R-SHOT-OUTCOME`, `R-WOODWORK`: saved (goalkeeper touch, live), blocked (outfield opponent at 1.9 s; at 2.1 s it is not a block), woodwork (contact 0.3 m from a post, no player within 2 m), wide (ball out of play).
- `R-PASS-KICK`, `R-PASS-OUTCOME`: an intercepted pass is confirmed with outcome `opponent`; the outcomes `teammate`, `out_of_play` and `open_space` are recognised.
- `R-CLEARANCE`: a clearance that meets exactly 4 conditions is confirmed; with 3 it is not; each condition C1–C7 has its own unit test.
- `R-HIGHLIGHT`: a replay anchored on a simulated goal walks back to the last change of possession.
- `R-LIVE-BUFFER`: an event that arrives before its frames is not processed until those frames have arrived; a goal stays `pending` until the frames of the next restart arrive.
- The rules kept from v3 (`R-TOUCH`, `R-ALIGN`, `R-RESTART-SUB`, `R-SYNC-LOCATION`, `R-DUEL`) keep their existing tests, renamed with the rule ID.

**Rules new or changed in v4.1 / v4.2** (`tests/test_v42_rules.py`, `tests/test_anonymise.py` and the files named)
- `R-SG-ACC`: the acceleration window is 19 samples at 25 Hz, speed 13.
- `R-PARKING`: a value shared by 3 frozen players is a parking value; with 2 it is not (`test_edges.py`).
- `R-MAP-EN`: the 7 DFL types outside the 23-type list are mapped; an unknown type is `UNMAPPED_<type>` (`test_mapping_formats.py`).
- `R-GOAL` entry rule: a ball out wide with 2 wrong samples back on the pitch gives no crossing, also when its first sample over the line is already between the posts (episode 2); 5 samples back make it valid again; a ball grazing the line within the radius never goes out.
- `R-GOAL-FREEZE`: a crossing 0.008 s ahead of a frozen ball is an extrapolated goal; not with a player 0.86 m away, nor 0.12 s ahead.
- `R-GOAL` end of the period: no restart and a final whistle give a goal (`end_of_period`); a VAR event before the whistle leaves it unconfirmed (with the shot still found and matched), a caution alone does not; without a final whistle it is unconfirmed. A disallowed goal takes the `Reason` of a `GoalDisallowed`.
- `R-GOAL` step 4: the in-goal samples continue the ball's kinematics until the provider freezes the ball; a tap-in just before the line is the goal shot, not the pass across the box; attribution: a defender's deflection of a wide shot is an own goal, of a shot on target the shooter's goal, a team-mate's deflection the team-mate's goal.
- `R-TOUCH-NEAREST`: an opponent 0.07 m from the ball takes a pass from a player 0.8 m away; one 1.1 m away does not; one 0.6 m away leaves the player within 0.3 m; closest approaches are compared even at different frames; a header taker 0.40 m further than the opponent keeps an aerial touch with the 0.5 m margin (not with 0.3 m).
- `R-TOUCH` height: a peak with the ball at 1.8 m is not a ground kick but stays a touch (a goalkeeper's catch ends the flight). `R-AERIAL`: one wrong height sample at the contact does not hide a header; a 15° turn with a 2.5 m/s² peak is an aerial touch (it was not with 20° / 4 m/s²).
- `R-SHOT-DIRECTION`: 8 m beyond a post for a kick 20 m out or above the power threshold, 6 m for a kick 12 m out. `R-SHOT-NOT-PASS`: a team-mate on the line who receives the ball makes a slow shot not confirmed; one who does not receive it is ignored. `R-SHOT-POWER`: power above the p99 confirms a shot (`shot_power`), never a header. `R-SHOT-OUTCOME`: a flight away from the goal with an opponent 0.6 m away at the kick is blocked (saved when he is the goalkeeper); 1.4 m away it is not.
- `R-WOODWORK`: a path passing 0.10 m from the post between two samples 0.51 and 0.71 m away is woodwork; a player 0.5 m from the ball takes the contact, one 0.7 m or 1.2 m away does not; a ball 0.26 m above the bar at 2.7 m is woodwork with the 3-D reach (the same keeper takes a ball at 2.2 m); a bounce reconstructed between two samples gives the contact at the post; a ball that enters the goal just inside the post and stops in the net is not woodwork.
- `R-CLEARANCE` C4: one opponent 3.5 m from the ball meets it, 4.5 m does not, a team-mate never (`test_clearance.py`).
- `R-LATE-ANNOTATION`: a pass annotated 8 s late in a dead window is located 8 s back, flagged; in a live window it is not. `R-DUEL` / `R-ALIGN`: an aerial duel with `WinnerAction = shot` won by the shooter and located one frame after the header no longer drops the shot.
- `R-SHOT-COUNTS`: penalties count in shots, on target and goals, also as `penalties`; an ordinary free kick is not a shot (`test_metrics_time.py`).
- `R-ANON-LABELS`: labels by side, role group and number in ID order; no `DFL-OBJ-` in any report file or in the summary of a DFL-format match; the label table only in the frame-level folder; calibration maps labels back to IDs; synthetic matches keep their IDs.
- Calibration (`test_calibration.py`): the xG fit with the fixed slope recovers a and a negative b; the model file keeps the generator's format and passes its checks; attribute scales store 21 quantiles and interpolate 99 non-decreasing values, flat in the tails; shot speeds exclude headers (the `R-SHOT-POWER` header test) and keep soft shots; the levels where a stored quantile is one value are found exactly.

**Provider comparison and determinism**
- Provider comparison is computed when provider channels exist, and it changes no result.
- Two runs on the same input give identical output hashes.

**Code hygiene: `R-LEGACY-CLEANUP` (v4).** The v2 modules `checks.py`, `derived.py`, `anomalies.py` and `plots.py` in `data_pipeline/insidethegame_data/` are deleted (only `checks.py` imports `derived.py`; nothing else imports them). The two test files that test live code are renamed after what they test: `tests/test_checks.py` (it tests `tidy.py`) becomes `tests/test_tidy.py`, and `tests/test_anomalies.py` (it tests swaps and overlaps in `prepare.py`) becomes `tests/test_swaps_overlaps.py`.

---

## 15. Default parameters

Keep these in one configuration file (`data_pipeline/insidethegame_data/config.py`, `guidelines_version = "v4.2 (2026-10-25)"`) and record them in every report. Physical values: [`Materials/PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md). Durations become samples with `frames(s) = round(s × fs)`. The values marked † in v4 (chosen by the analyst) were reviewed after the v4 run: those touched by the v4.1 / v4.2 rules changed (parking players, aerial thresholds, woodwork), the others are confirmed and lose the †. Calibration parameters (xG, attribute scales) are in `CalibrationConfig` of `calibration.py` (section 11).

| Parameter | Config name | Default | Rule |
|---|---|---|---|
| Savitzky–Golay order | `savgol_polyorder` | 2 | `R-SG-SPEED` |
| Window for velocity, speed, distance, ball | `savgol_window_fraction_of_fs` | 0.5 → odd(round(fs/2)) = 13 samples at 25 Hz | `R-SG-SPEED` |
| Window for player acceleration | `acc_window_fraction_of_fs` | **0.75 → 19 samples** at 25 Hz (v4.1; was 0.5) | `R-SG-ACC` |
| Windows of the sensitivity test | `acc_window_test_fractions` | (0.5, 0.75, 1.0) | `R-ACC-WINDOW-TEST` |
| Player speed limit | `player_speed_max_kmh` | 38 km/h (10.56 m/s) | `R-SPEED-LIMIT` |
| Acceleration band | `player_acc_min_ms2`, `player_acc_max_ms2` | −8.5, +7.5 m/s² | `R-ACC-BAND` |
| Longest exceedance that is noise | `acc_noise_max_s` | 0.2 s (5 samples) | `R-ACC-BAND` |
| Ball speed limit (live play) | `ball_speed_max_kmh` | 211 km/h (58.6 m/s) | `R-BALL-LIMIT` |
| Player jump step | – | v_max · ΔT (0.42 m at 25 Hz) | `R-JUMP` |
| Player exclusion window | `exclusion_window_s` | ±1 s | section 6.2 |
| Ball window (jumps, limits) | `ball_window_s` | ±0.25 s | `R-BALL-WINDOW` |
| Ball window after a restart | `ball_restart_s` | 0.25 s | `R-BALL-WINDOW` |
| Frozen track | `frozen_min_s` | identical position for ≥ 1 s | `R-FROZEN` |
| Gap estimation | `gap_resume_max_m`, `gap_spline_max_s`, `gap_kalman_max_s`, `kalman_meas_sigma_m`, `kalman_jerk_psd` | resume < 6 m; spline ≤ 0.5 s; Kalman/RTS ≤ 2 s (σ = 0.1 m, 50 (m/s³)²·s); straight line beyond | `R-GAP` |
| Tracking-area edge | `edge_min_samples`, `edge_value_decimals` | ≥ 25 identical samples beyond a line (4 decimals), outermost, ≥ 2 positions or runs | `R-EDGE` |
| Parking value | `parking_min_samples`, `parking_min_tracks` | ≥ 25 samples, **≥ 3 players** (v4.1; was 2) | `R-PARKING` |
| Substitution | `sub_inherit_max_m`, `sub_inherit_max_frame_gap`, `sub_jump_search_s`, `sub_stabilisation_s`, `sub_no_jump_invalid_s` | < 0.5 m, ≤ 1 frame; jump searched within 60 s; jump + 2 s; without a jump 1 s | `R-SUB-INHERIT` |
| Re-entry stabilisation | `reentry_stabilisation_s` | 2 s | `R-EXIT-REENTRY` |
| Swap | `swap_distance_m`, `swap_window_s`, `swap_pair_max_s` | < 1 m, partner match within ±1 s, correction within 120 s | `R-SWAP`, `R-SWAP-RESTORE` |
| Overlap | `overlap_distance_m`, `overlap_min_s` | < 0.3 m for ≥ 0.2 s | `R-OVERLAP-CROWD` |
| Crowding | `crowding_radius_m`, `crowding_min_players` | ≥ 3 players within 3 m | `R-OVERLAP-CROWD` |
| Events around an unknown jump | `jump_context_s` | ±2 s | `R-JUMP-CONTEXT` |
| Event search window | `event_search_min_s`, `event_search_mad_factor`, `event_search_max_s` | max(5 s, 4 × MAD of anchor offsets), capped at 30 s | `R-CLOCK-OFFSET` |
| Late annotation | `late_annotation_dead_share`, `late_annotation_max_s` | window more than 50 % dead ball → search back up to 20 s before the corrected time (v4.2) | `R-LATE-ANNOTATION` |
| Ground touch | `touch_acc_min_ms2`, `touch_reach_m`, `touch_peak_tolerance_s`, `ground_touch_max_height_m` | \|a\| ≥ 8 m/s², player within 2 m, ±0.2 s; ball ≤ 1.5 m at the peak (v4.2) | `R-TOUCH` |
| Nearest player | `touch_nearest_margin_m`, `touch_nearest_margin_aerial_m` | closest approach within ±0.2 s the smallest or within 0.3 m of it; **0.5 m for aerial touches** (v4.2) | `R-TOUCH-NEAREST` |
| Kick | `kick_away_s`, `kick_away_min_m`, `kick_min_separation_s` | ball ≥ 1 m away within 0.5 s; touches < 0.2 s apart merge | `R-TOUCH`, `R-ALIGN` |
| Shot touch separation | `shot_touch_min_separation_s` | 0.08 s (2 frames) | `R-SHOT-SPLIT` |
| Soft kick | `soft_kick_away_m`, `soft_kick_window_s` | > 1 m within 0.3 s | `R-SHOT-SOFT` |
| Aerial touch | `aerial_min_height_m`, `header_shot_min_height_m` | 1.5 m; 1.6 m for shots, on the incoming path (median of the live samples i − 6 … i, v4.1) | `R-AERIAL`, `R-SHOT-HEADER` |
| Aerial touch signature | `aerial_dir_change_min_deg`, `aerial_acc_min_ms2`, `aerial_min_speed_ms` | **≥ 10°**; \|a\| peak **≥ 2 m/s²** (v4.1; were 20° and 4 m/s²); speeds ≥ 1 m/s | `R-AERIAL` |
| Alignment | `sync_max_candidates`, `sync_anchor_weight` | 8 candidates; anchors weight 2 | `R-ALIGN` |
| Duel won with a shot | `duel_aerial_type`, `duel_shot_winner_actions` | `Type = air`, `WinnerAction = shot` (v4.2) | `R-DUEL`, `R-ALIGN` |
| Goal frame | `goal_width_m`, `crossbar_height_m`, `ball_radius_m` | 7.32 m, 2.44 m (IFAB Law 1), 0.11 m (Law 2) | `R-GOAL`, `R-WOODWORK` |
| Ball dead after a goal crossing | `goal_dead_within_s` | 5 s | `R-GOAL` |
| Entry rule | `goal_reentry_min_s` | back on the pitch for 0.2 s (5 samples) after an exit outside the frame (v4.2) | `R-GOAL` |
| Frozen ball | `goal_freeze_extrapolate_s`, `goal_freeze_player_min_m` | at most 0.08 s (2 frames); no player within 1 m (v4.2) | `R-GOAL-FREEZE` |
| End of the period | `goal_end_blocking_events`, `final_whistle_event` | `VideoAssistantAction`, `GoalDisallowed`, `Foul`, `Offside` between the crossing (minus the search window) and the `FinalWhistle` (v4.1) | `R-GOAL` |
| Disallowed reason | `goal_disallowed_event` | `GoalDisallowed`, qualifier `Reason` (v4.2) | `R-GOAL` |
| Shot direction | `shot_wide_margin_m`, `shot_wide_margin_long_m`, `shot_long_range_m`, `shot_flight_min_samples` | 6 m beyond a post; **8 m** for kicks more than 16.5 m from the goal line or above the power threshold (v4.1); ≥ 3 flight samples | `R-SHOT-DIRECTION` |
| Team-mate near the trajectory | `shot_teammate_line_m` | 2 m, and he receives the ball (v4.1) | `R-SHOT-NOT-PASS` |
| Shot power | `shot_power_window_s`, `pass_speed_p99_kmh` | 0.5 s; 94.86 km/h when `calibration/ball.json` has no `pass_speed_ms.p99` | `R-SHOT-POWER` |
| Block | `shot_block_max_s`, `shot_block_at_kick_m` | 2 s; away from the goal with an opponent within 1 m at the kick (v4.2) | `R-SHOT-OUTCOME` |
| Woodwork | `woodwork_distance_m`, `woodwork_close_m`, `woodwork_player_min_m`, `woodwork_margin_m`, `player_reach_height_m` | path within 0.5 m (candidate); woodwork if within 0.3 m and every player ≥ 1 m, or closer to the frame than to any player by > 0.5 m; 3-D reach above 2.3 m (v4.2) | `R-WOODWORK` |
| Next touch, open space | `next_touch_max_s`, `pass_open_space_min_m` | 10 s; 5 m | `R-PASS-OUTCOME`, section 9.7 |
| Clearance | `clearance_min_conditions` | 4 of 7 | `R-CLEARANCE` |
| Clearance conditions | `clear_short_possession_s`, `clear_power_min_kmh`, `clear_high_ball_m`, `clear_free_radius_m`, `clear_opponent_radius_m`, `clear_min_opponents`, `clear_box_min_opponents`, `clear_after_s` | 1 s; 72 km/h; 3 m; 5 m; **C4: ≥ 1 opponent within 4 m** (v4.1); 3 opponents; 3 s | `R-CLEARANCE` |
| Duel | `duel_reach_m` | 3 m | `R-DUEL` |
| Speed zones | `speed_zones_kmh` | boundaries 7, 15, 20, 25 km/h | `R-ZONES` |
| Sprint | `sprint_min_kmh`, `sprint_min_s`, `sprint_merge_gap_s` | ≥ 25 km/h; ≥ 1 s; sprints less than 1 s apart merge (decided in the metrics review, implemented in the metrics work) | `R-SPRINT` |
| Distance precision | `precision_exact_below`, `precision_tenth_below` | e < 1 % exact; 1–5 % "≈" to 0.1 km; ≥ 5 % "≈" to 0.5 km | `R-DIST-PRECISION` |
| Live event buffer | – | corrected timestamp plus search window | `R-LIVE-BUFFER` |

Removed from v3: `shot_toward_goal_margin_m` (1 m) and `shot_direction_delay_s` (0.2 s), replaced by `R-SHOT-DIRECTION`.

---

## 16. English mapping

The mapping applies **only when ingesting real provider data** (for example DFL for calibration). Our synthetic data is produced in English natively and needs no translation.

All codes are mapped through a reviewed, versioned file (`data_pipeline/reference/dfl_to_english.yaml`), never translated at runtime.
- An unknown or missing code keeps the original in `source_code`, gets `UNMAPPED_<code>` (or `UNMAPPED_NONE`) in the English field, and is listed in the report so that the mapping file can be updated.

### 16.1 DFL playing positions

Verified on the six matches from each team's formation and the players' mean attacking-normalised positions in the first period.

| DFL code | German meaning | English role | Group | Evidence (mean x_att, y_att) |
|---|---|---|---|---|
| TW | Torwart | GK – Goalkeeper | GK | −36.7, 0 |
| IVL / IVR / IVZ | Innenverteidiger links / rechts / zentral | LCB / RCB / CB | CB | −15, ±8 / −1 |
| LV / RV | Linker / Rechter Verteidiger | LB / RB | FB | −9, ±18 |
| DML / DMR / DMZ | Defensives Mittelfeld links / rechts / zentral | LDM / RDM / CDM | DM | −6 to −2, ±2 |
| **DLM / DRM** | Defensives linkes / rechtes Mittelfeld | **LWB / RWB – Wing back** | **FB** | −4 to −2, **±19–20** (3-3-2-2) |
| **HL / HR** | Halblinks / Halbrechts | **LCM / RCM – Central midfielder** | **CM** | +3 to +4, ±6–8 (3-3-2-2) |
| LM / RM | Linkes / Rechtes Mittelfeld | LM / RM – Wide midfielder | W | −7 to −5, ±17–19 (3-4-1-2) |
| ZO | Zentrale Offensive | CAM – Attacking midfielder | AM | +4.4, 0 |
| OLM / ORM | Offensives linkes / rechtes Mittelfeld | LW / RW – Winger | W | +4 to +5, ±13 |
| STZ / STL / STR | Sturm zentral / links / rechts | ST / LS / RS – Striker | ST | +11, ±2 |

### 16.2 Formations and other German values

| German | English |
|---|---|
| `4-4-2 Double-6 offensive Outliner` | `4-4-2 (double pivot, attacking wide midfielders)` |
| Plain numeric formations (e.g. `3-3-2-2`, `4-2-3-1`) | Kept as they are |
| `Fußball` | football |
| `Ligabetrieb` | league match |
| `Heimtrikot` / `Auswärtstrikot` | home kit / away kit |
| `Deutschland` | Germany |
| `trainer` / `assistantTrainer` | head coach / assistant coach |

Event types (section 16.4) and most attribute values in DFL are already English and stay as they are, in snake_case for our outputs.

### 16.3 Shot outcomes (v4)

DFL records the outcome of a `ShotAtGoal` as a sub-type. The six DFL matches contain 144 shots: 13 `SuccessfulShot`, 37 `SavedShot`, 32 `BlockedShot`, 56 `ShotWide`, 2 `ShotWoodWork`, 4 `OtherShot`.

| DFL sub-type | English outcome | On target |
|---|---|---|
| `SuccessfulShot` | goal | yes |
| `SavedShot` | saved | yes |
| `BlockedShot` | blocked | no |
| `ShotWide` | wide | no |
| `ShotWoodWork` | woodwork | no (counted separately) |
| `OtherShot` | wide | no |

`OtherShot` → wide follows the engine's decision of 24 October. The mapping belongs in the mapping file.

### 16.4 Event types (v4.2)

Every DFL event type is in the mapping file (`event_types`), with its English snake_case name and whether it is in the DFL 23-type list (the dataset's list, `Delete` included). The six matches also contain **8 types outside that list**: `Penalty` (a restart, handled since v4) and the 7 below, which were "unknown" before v4.2. The report logs the events per type, the types outside the list and any unmapped type (`R-MAP-EN`).

| DFL type | English name | Events in the six matches | Used by |
|---|---|---|---|
| `GoalDisallowed` | goal_disallowed | 1 | `R-GOAL` (`disallowed_reason` from `Reason`; blocks a goal confirmed by the end of the period) |
| `BallDeflection` | ball_deflection | 3 | – |
| `OtherPlayerAction` | other_player_action | 2 | – |
| `PlayerNotSentOff` | player_not_sent_off | 4 | – |
| `PossessionLossBeforeGoal` | possession_loss_before_goal | 7 | – |
| `Run` | run | 8 | – |
| `SitterPrevented` | sitter_prevented | 2 | – |

---

## 17. Reference: the DFL dataset and findings of the v3 run

**Dataset.** Bassek, Rein, Weber & Memmert (2025), Scientific Data 12:195, CC-BY 4.0.
- Seven matches; **we have six** (J03WMX in the Bundesliga; J03WOH, J03WOY, J03WPY, J03WQQ, J03WR9 in the 2. Bundesliga).
- Each match has three XML files: match information, events and positions.
- Tracking uses TRACAB Gen5 (16–20 cameras, 25 Hz).
- Frame fields: `N`, `T`, `X`, `Y` (m), `D` (cm), `S` (km/h), `A` (m/s²), `M`. The ball also has `Z`, `BallPossession` (1 = home, 2 = away) and `BallStatus` (0 = dead, 1 = live). Events may carry the provider's own synchronised frame (`CalculatedFrame`, mostly on shots), used **only in tests**.

**Findings of the v3 run (2026-10-23) on the six matches.** These are examples only; every value is re-discovered per match. Full numbers: `reports/qc/SUMMARY.md` and `reports/qc/<match_id>/`.

| Match | Valid player samples | Jump episodes (max step) | Frozen runs ≥ 1 s | Tracking-area edges | Substitutions (with inheritance) | Event clock offset P1 / P2 | Event time zone | Top valid speed |
|---|---|---|---|---|---|---|---|---|
| J03WMX | 98.91 % | 22 (1.5 m) | 321 | none | 10 (3) | −0.40 / −0.44 s | +02:00 | 36.1 km/h |
| J03WOH | 98.42 % | 194 (45.3 m) | 360 | x = ±55.5 m | 10 (3) | +1.59 / +1.37 s | +02:00 | 33.8 km/h |
| J03WOY | 98.55 % | 129 (49.9 m) | 356 | none (parking value y = +36.54 m) | 10 (5) | +1.77 / +1.63 s | +02:00 | 33.2 km/h |
| J03WPY | 98.04 % | 191 (45.0 m) | 559 | x = ±55.5 m | 9 (9) | +0.07 / +0.68 s | +02:00 | 33.5 km/h |
| J03WQQ | 98.80 % | 153 (58.6 m) | 208 | x = ±55.5 m | 9 (5) | +2.19 / +1.75 s | +01:00 | 32.9 km/h |
| J03WR9 | 98.62 % | 162 (64.1 m) | 382 | x = ±55.5 m | 9 (4) | +1.35 / +1.01 s | +01:00 | 33.6 km/h |

| Property | Value (six matches, v3 run) |
|---|---|
| Sampling | Exactly 25 Hz; no missing, duplicated or NaN samples; frames ordered |
| Coordinates | Tracking centred (±52.5, ±34); events corner-origin, same y sign; all pitches 105 × 68 m |
| Frame numbering (J03WPY) | Starts at N = 10000 and N = 100000 |
| Playing time | `BallStatus = 1` time equals DFL net time |
| Valid ball samples | 51.5–58.2 % (live play only, outside restarts and anomalies) |
| Jump causes (851 episodes) | Identity swaps 378 (44 %), unknown 227 (27 %), track loss 152 (18 %), crowding 55 (6 %), substitutions 23 (3 %), re-entries 16 (2 %) |
| Other artefacts | 2,186 frozen runs ≥ 1 s (most resume < 6 m away and are estimated); 341 overlap episodes; 216 acceleration-band episodes (113 accelerations, 103 decelerations), no speed above 38 km/h |
| Valid peaks | Player speed max 32.9–36.1 km/h (p99.9 28.0–28.8); tangential acceleration p0.1 between −5.72 and −5.46 m/s², p99.9 between +4.71 and +4.92 m/s² (13-sample window; min / max were capped by the v3 band at −8.48 / +7.50); live ball speed max 104.4–119.8 km/h (p99.9 91.5–94.3) |
| J03WMX top speed | 36.1 km/h: a smooth 3 s sprint, provider `S` agrees; valid |
| Referees | Tracked on J03WMX only; kept out of player preparation |
| Events located | Passes 4,579 of 4,752 (96.4 %); shots 122 of 144 (84.7 %); clearances 103 of 125 (82.4 %); duels 1,118 of 1,219 (91.7 %) |
| Located frame vs provider `CalculatedFrame` (tests only) | Within 0.2 s for 115 of 121 shots, median difference 0.04 s |
| Shot confirmation (v3 rules) | 66 of 144 confirmed; 11 of 13 goals confirmed; no shot that was not a goal confirmed as a goal |
| Pass check (v3 rules) | 91.7 % of the passes annotated `successfullyCompleted` confirmed by a team-mate's next touch; 75 % of the `unsuccessful` ones not confirmed (next touch by an opponent) |
| Ball speeds (calibration) | Located passes: median 14.6 m/s, p90 22.1 m/s, p99 about 95 km/h; confirmed open-play shots: median 21.8 m/s, p90 29.0 m/s |

**J03WPY in detail (v3 run).** 98.04 % of player samples valid; 27,205 samples estimated and 4,000 discarded; 60 `Delete` events of 1,504 dropped; 139 event pairs out of time order; 191 jump episodes on 29 players (median 2.01 m, 133 in dead ball); 26 mutual swaps, one of them paired with its correction (labels restored); 559 frozen runs; ball: 95 restarts, 76 position jumps at restarts ignored, 4 live-play jump episodes; tracking-area edge at |x| = 55.5 m (section 5.2.1); identity inheritance in all 9 substitutions (section 5.3); provider kinematics in section 7.1.

---

## 18. References

**Dataset and validation**
- Bassek, M. et al. (2025). An integrated dataset of spatiotemporal and event data in elite soccer. *Scientific Data* 12:195. https://doi.org/10.1038/s41597-025-04505-y
- Linke, D., Link, D., Lames, M. (2020). Football-specific validity of TRACAB's optical video tracking systems. *PLoS ONE* 15(3): e0230179. https://doi.org/10.1371/journal.pone.0230179

**Physical limits and thresholds**: all sources in [`Materials/PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md), including:
- Harper et al. (2019, 2022), *Sports Medicine*;
- Varley & Aughey (2013), *Int J Sports Med*;
- Kellis & Katis (2007), *J Sports Sci Med*;
- the Premier League top-speed records;
- the FIFA speed zones (FIFA Training Centre);
- IFAB Laws 1 and 2.

**Processing**
- Savitzky, A., Golay, M. J. E. (1964). *Analytical Chemistry* 36(8), 1627–1639.
- Optimal smoothing window and the bias–variance trade-off: arXiv:1808.10489. https://arxiv.org/pdf/1808.10489
- Delves, R. I. M., Aughey, R. J., Ball, K., Duthie, G. M. (2021). The quantification of acceleration events in elite team sport: a systematic review. *Sports Medicine – Open* 7:45. https://pmc.ncbi.nlm.nih.gov/articles/PMC8245618
- Ellens, S. et al. (2022). Techniques to derive and clean acceleration and deceleration data of athlete tracking technologies in team sports: a scoping review. *J Sports Sci* 40(16). https://doi.org/10.1080/02640414.2022.2054535
- databallpy, preprocessing options (Savitzky–Golay windows for ball and acceleration): https://databallpy.readthedocs.io/en/latest/getting_started/preprocessing_options.html
- Osgnach, C. et al. (2010). https://doi.org/10.1249/MSS.0b013e3181ae5cfd

**Synchronisation and event detection**
- Van Roy, M., Cascioli, L., Davis, J. (2023). ETSY: a rule-based approach to event and tracking data synchronization. https://dtai.cs.kuleuven.be/sports/blog/etsy:-a-rule-based-approach-to-event-and-tracking-data-synchronization
- ELASTIC: event–tracking data synchronization in soccer without annotated event locations. https://arxiv.org/abs/2508.09238
- Kwiatkowski, M. The right way to synchronize event and tracking data. https://kwiatkowski.io/sync.soccer
- Bischofberger, J., Baca, A., Schikuta, E. (2024). Event detection in football: improving the reliability of match analysis. *PLoS ONE* 19(4). https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11025972/
- Vidal-Codina, F., Evans, N., El Fakir, B., Billingham, J. (2022). Automatic event detection in football using tracking data. *Sports Engineering* 25. https://www.catalyzex.com/paper/automatic-event-detection-in-football-using

**Libraries**
- kloppy: https://kloppy.pysport.org
- floodlight: https://floodlight.readthedocs.io
- databallpy: https://databallpy.readthedocs.io
- SciPy `savgol_filter`: https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html

---

## 19. Open points

1. ~~**Acceleration filter window.**~~ **Closed (v4.1):** 19 samples, chosen from the sensitivity table (section 7.3).
2. ~~**Sprint minimum duration.**~~ **Closed (decided in the metrics review, implemented in the metrics work):** `R-SPRINT` is ≥ 25 km/h for ≥ 1 s, and two sprints less than 1 s apart count as one (`Materials/METRICS.md` §19, decision 21).
3. **Possession change as a helper for locating shots.** After a saved, blocked or wide shot the possession almost always passes to the other team; the instant of the change could bound the search for the kick (Vidal-Codina et al. 2022). **Still not implemented** (reviewed after v4.2: "not now").
4. **Source of the acceleration band.** The literature source of −8.5 / +7.5 m/s² is still to be cited in `Materials/PHYSICAL_LIMITS.md`.
5. ~~**Parameters marked † in section 15.**~~ **Closed (v4.2):** reviewed after the v4 run; the values touched by the v4.1 / v4.2 rules changed, the others are confirmed (section 15).
6. **A general rule for wrong ball samples (v4.2, not now).** One or two ball samples that jump out and back, or a height of exactly 0 between airborne samples, are not real positions. Treating them as "position not usable" for every touch (not only for the goal entry rule of `R-GOAL`) would change all touches: to be decided and tested on its own.
7. **Aerial soft flick (v4.2, not now).** An aerial touch where the player runs on with the ball (the ball does not move 1 m away within 0.5 s) could be accepted as a kick when the next touch is not his (J03WMX, episode 10c).
8. ~~**The cost of `R-TOUCH-NEAREST` in aerial duels.**~~ **Closed (v4.2, team decision):** the margin is 0.5 m for aerial touches (section 9.5). Still open: kicks under close pressure, where an opponent's torso is more than 0.3 m nearer than the kicker's (part of the 1.1-point drop of located passes).
9. **Late annotations during live play (v4.2, decided: not searched).** `R-LATE-ANNOTATION` extends the window only when it is mostly dead ball. A late annotation during live play is **not** searched beyond the normal window, so that a wrong, earlier kick of the same player is never taken. Two annotations 5–7 s late during live play therefore stay unlocated: the J03WQQ header 5.3 s late and the J03WR9 crossbar shot 6.4 s late (its crossbar contact is detected by `R-WOODWORK`, but the shot is not located, so it carries no woodwork outcome).
10. ~~**Stored quantiles that coincide with one player-match value.**~~ **Known limit, accepted (v4.2, team decision): CC-BY aggregates.** The calibration stores quantiles by linear interpolation, so a stored quantile can coincide with one player-match value: when its position p × (n − 1) / 100 is a whole number (for example the median of an odd n). The same holds for the per-match medians of `match_median_range` and the percentiles of `physical_profiles.json`, and a group with very few player-matches (CM, n = 2) stores a mean and a standard deviation. The open DFL data are CC-BY and these files hold aggregates without any person or object ID, so the method stays as it is. `calibration.exact_order_statistic_levels` gives the levels concerned; the Linesman report prints the count for the realism reference.

---

## 20. Changes from v3

- **Ball windows** ±0.25 s around live-play jumps and limits, and 0.25 s after restarts (were 0.5 s): `R-BALL-WINDOW`.
- **Acceleration band** no longer excludes samples: ≤ 0.2 s is noise, longer is "≈" (`R-ACC-BAND`, `R-ACC-REPORT`). The acceleration window is chosen with a sensitivity test (`R-SG-ACC`, `R-ACC-WINDOW-TEST`); speed and distance stay at 13 samples.
- **Parking values** beyond the lines on frozen tracks, separate from tracking-area edges (`R-PARKING`); the edge conditions decided on 23 October are now written in `R-EDGE`.
- **Substitutions**: inheritance threshold 0.5 m kept; without a re-acquisition jump the substitute is valid after 1 s (was 1 s + 2 s); halfway-line distance logged only (`R-SUB-INHERIT`).
- **Swaps**: labels restored only when the correction comes within 120 s (`R-SWAP-RESTORE`). **Unknown jumps**: events within ±2 s recorded, live and dead ball separated (`R-JUMP-CONTEXT`).
- **Live play only** for every check on actions and positions of people, except the ball inside the goal (`R-LIVE-ONLY`).
- **Goals** confirmed looking forward by the next kickoff, disallowed goals recognised, located first (`R-GOAL`).
- **Shots**: direction over the whole free flight within 6 m beyond a post, team-mate check, power as extra evidence, outcomes including woodwork and blocks within 2 s (`R-SHOT-*`, `R-WOODWORK`); located also from headers, soft kicks and kick–block pairs 0.08 s apart (`R-AERIAL`, `R-SHOT-HEADER`, `R-SHOT-SOFT`, `R-SHOT-SPLIT`).
- **Passes** confirmed by the kick, with the outcome kept separately (`R-PASS-KICK`, `R-PASS-OUTCOME`). **Clearances** confirmed by 4 of 7 conditions (`R-CLEARANCE`).
- **Speed zones and sprints** are metrics now, with sprints above 25 km/h (`R-ZONES`, `R-SPRINT`). **Shots on target** = goals + saved; **woodwork** is a statistic of its own (`R-SHOT-COUNTS`, `R-WOODWORK-STAT`).
- **Legacy v2 modules** deleted and two tests renamed (`R-LEGACY-CLEANUP`).
- Example numbers updated with the v3 run on the six matches; every rule has an identifier.

---

## 21. Changes from v4

Decided by the team after the review of the v4 reports on the six DFL matches: the twelve **v4.1** rules, the woodwork path, and the **v4.2** rules from eleven reviewed episodes.

**v4.1**
- **Goal at the end of the period** (`R-GOAL` step 3): no restart, the ball dead within 5 s, the entry rule met and no `VideoAssistantAction`, `GoalDisallowed`, `Foul` or `Offside` before the `FinalWhistle` → a goal with `confirmed_by = end_of_period`; otherwise `unconfirmed`.
- **Attribution with a deflection** (`R-GOAL` step 4): a defender's deflection of a shot that was not going on target is an own goal; of a shot on target, the shooter's goal; a team-mate's deflection, the team-mate's goal.
- **`R-SHOT-NOT-PASS`** only when the team-mate really receives the ball.
- **`R-SHOT-POWER`**: above the p99 of DFL pass speeds (94.86 km/h), and not a header, a kick is a shot (`R-SHOT-CHECK` basis `shot_power`, before the direction step).
- **`R-SHOT-DIRECTION`**: 8 m beyond a post for kicks more than 16.5 m from the goal line and above the power threshold; 6 m otherwise.
- **Blocks in flight**: the free flight ends at a touch mid-flight, the direction is measured before it, the outcome is blocked or saved.
- **`R-AERIAL`**: the height on the incoming path; thresholds 10° and 2 m/s² (were 20° and 4 m/s²).
- **`R-WOODWORK`**: without the condition "no player within 2 m".
- **`R-CLEARANCE` C4**: at least one opponent within 4 m of the ball at the kick.
- **`R-PARKING`**: at least 3 players.
- **`R-SG-ACC`**: 19 samples (open point 1 closed).
- **`R-SHOT-COUNTS`**: penalties (and the other set-piece shots) count in shots, shots on target and goals; their xG is separate.

**v4.2**
- **`R-WOODWORK`**: the distance is measured on the ball's path between consecutive samples (including a bounce between two samples); woodwork when the path is within 0.3 m of the frame and every player at least 1 m away, or when the ball is closer to the frame than to any player by more than 0.5 m (3-D reach above 2.3 m); never once the ball is in the net (a goal entry within ±0.2 s).
- **`R-MAP-EN`**: the 7 DFL event types outside the 23-type list are mapped (section 16.4); `R-GOAL` takes `disallowed_reason` from `GoalDisallowed`.
- **`R-GOAL` entry rule**: after an exit over the goal line outside the frame the ball stays out until it has been back on the pitch for 0.2 s.
- **`R-GOAL-FREEZE`**: a frozen dead ball is extrapolated for at most 0.08 s, with no player within 1 m (`crossing_method = extrapolated`).
- **`R-GOAL` step 4**: the backward shot search runs on unconfirmed crossings too, up to and including the crossing frame, with the in-goal samples in the ball's kinematics (`R-LIVE-ONLY` exception); a soft kick is vetoed only by an own touch that is itself a kick (`R-SHOT-SOFT`); a deflection is a ground or an aerial touch.
- **`R-TOUCH-NEAREST`**: a kick belongs to the nearest player, or one within 0.3 m of the nearest (0.5 m for aerial touches; closest approaches within ±0.2 s); a **ground** touch needs the ball at or below 1.5 m (a higher peak stays a touch); a flight away from the goal with an opponent within 1 m at the kick is **blocked** (saved by a goalkeeper).
- **`R-LATE-ANNOTATION`**: a search window that is more than 50 % dead ball extends backwards up to 20 s.
- **`R-DUEL` / `R-ALIGN`**: an aerial duel won with `WinnerAction = shot` by the next shot's player may share the shot's frame.
- **`R-ANON-LABELS`**: the committable reports of real data name people by anonymous labels (`home CB 1`); the label table stays in the git-ignored frame-level folder.
- **Calibration** (section 11): `xg_model.json` with the visible-angle slope fixed at 2.84 and only a and b fitted, penalty 0.79; `attribute_scales.json` stores only 21 quantiles and interpolates the 99 values; shot speed without headers (ball above 1.5 m at the kick) in `ball.json`, the `shot_power` scale and the realism metric; stored quantiles that can coincide with one player-match value are an accepted limit (CC-BY aggregates).
- **Not now** (section 19): a general rule for wrong ball samples; the aerial soft flick; the possession change as a helper for shots; late annotations during live play are not searched beyond the normal window.

**Effect on the six DFL matches (v4 → v4.2).** Goals confirmed 13 → 16 of 16 (one correct disallowed goal, no fake goal, no shooter mismatch); shots located 126 → 124 and confirmed 84 → 104 of 144; header shots located 20 → 24 of 29; woodwork contacts 3, none at a goal entry (one annotated woodwork shot now located with observed woodwork, the other annotated during live play 6.4 s late and not searched); clearances confirmed 59 → 89 of 125; passes confirmed by their kick 96.3 % → 95.2 %; located frame within 0.2 s of the provider's frame (tests only) 116 of 125 → 117 of 122. Details: `reports/qc/SUMMARY.md`.
