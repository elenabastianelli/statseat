# SYN-001: data card

> **Everything here is invented.** Brackwater Athletic, Calderholm United, the Lanternworks Stadium and every player are fictional (universe `MERIDIA-1.0`). No real tracking or event data is stored here. Real DFL data was used only as **aggregate** calibration statistics (see "Calibration").

**Brackwater Athletic 2-1 Calderholm United**, "The Estuary Derby", Meridian League 2026/27, matchday 9, Lanternworks Stadium (pitch 105 x 68 m). Kick-off 15:00 local time (+01:00) on 24 October 2026; the first kick is at 14:00:06.480 UTC.

## Files

| File | What it is |
|---|---|
| `match_info.json` | line-ups, officials, venue with its capacity and the tickets sold, weather, the crest and kit files of each team (written by Scout, DFL `matchinformation` structure) |
| `script.json` | the approved story: key moments, stoppage time, "who is pushing" every 5 minutes (input of the generator) |
| `events.json` | 1,244 events in the DFL taxonomy, in English (never `Delete`) |
| `positions.parquet` | 25 Hz tracking of the 22 players on the pitch and the ball: 3,268,346 rows, 13.0 MB |
| `kitman/`, `tickets/` | Kit Man documents and digital tickets (other generators) |

The 6 replicas used for the realism report are in `synthetic/replicas/SYN-001-r1` to `-r6`. They are git-ignored, so regenerate them with the command below.

### `positions.parquet`

One row per object per frame, frame-major: period, frame, then the ball row followed by the home and away players. Only objects on the pitch have rows. Compression is zstd with dictionary encoding.

| Column | Type | Meaning |
|---|---|---|
| `period` | int8 | 1 or 2 |
| `frame` | int32 | 25 Hz; period 1 runs from 10000 to 79650, period 2 from 100000 to 172450 (as in DFL). Frame 10000 / 100000 is the kick-off |
| `time_utc` | timestamp[ns, UTC] | kick-off of the period + (frame - first frame) x 40 ms. The second half starts 941 s after the first-half whistle |
| `object_id` | dictionary | universe player id (`BRA-08`) or `BALL` |
| `object_type` | dictionary | `player` or `ball` |
| `team_side` | dictionary | `home`, `away`, `none` (ball) |
| `x_m`, `y_m` | float32 | metres, centre origin, rounded to 0.01 m. +x points to the Harbour Stand (south goal), +y to the Lamplighters Stand (east). The Main Stand, with the dugouts and the substitution zone, is the touchline y = -34. Brackwater attack +x in the first half, so the 88' winner is scored at the Foundry End |
| `z_m` | float32 | height of the ball centre (0.11 m on the grass); NaN for players |
| `ball_status` | dictionary | `live` or `dead`, on every row |
| `ball_possession` | dictionary | `home`, `away`, `none`, on every row. While the ball is dead it is the team that takes the restart |

### `events.json`

`{"match_id", "universe_id", "generator_version": "match-0.1.0", "seed", "events": [...]}`. Every event has these fields:

- `event_id`, `event_type`, `sub_type`, `period`, `team_side`, `player_id`;
- `time_utc`: the time of the event's frame, ISO 8601 UTC with milliseconds;
- `x_m`, `y_m`: annotated location, in the same reference system as the tracking;
- `attributes`: DFL names;
- `true_frame`: generator truth, **for tests only**.

- **No annotation delay** (since the event-time fix). `time_utc` is exactly the time of the event's frame, for every event, the kick-offs of both halves included. A restart's frame is the kick: the first frame with the ball live. The data pipeline measures an event clock offset of 0.00 s in both halves. Only the location is annotated like a human operator: N(0, 0.8 m) noise.
- **Outcomes come from the simulation** (since the event-time fix). The random draw is only the intent (where a pass or shot is aimed, how well it is struck). Every kick event is created at its kick frame without a result; after the simulation the generator reads who really touched the ball next, and where it went:
  - pass: next touch by a team-mate -> `successfullyCompleted`, `Recipient` = that team-mate; otherwise `unsuccessful` (`Recipient` = the intended team-mate, as DFL);
  - shot: ball in the goal -> `SuccessfulShot`; goalkeeper touch -> `SavedShot`; other defender -> `BlockedShot`; post or bar -> `ShotWoodWork`; over the goal line -> `ShotWide`;
  - duel (`TacklingGame`): the winner is the player whose team touches the ball next.

  A player touches the ball only if it passes within his reach (1.6 m for a receiver, 1.8 m for a blocking defender, 1.9 m for a diving goalkeeper, noise-free). A pass the receiver cannot reach runs on as a loose ball, a shot nobody can get to goes past. Before writing, the generator checks every successful pass, every shot result and every restart shot against the trajectories, and stops if one does not match.
- **Set pieces are nested as in DFL.** `KickOff`, `ThrowIn`, `GoalKick`, `CornerKick`, `FreeKick` and `Penalty` (the scripted penalty, with `BuildUp`, `CausingPlayer`, `FouledPlayer`, `ProspectiveTaker`) carry the inner action in `sub_type` and its attributes: `Play` (with `Evaluation`, `Height`, `Recipient`) or `ShotAtGoal/<result>` (for example `FreeKick` with `ShotAtGoal/SavedShot`, with the shot attributes). There is no duplicate `Play` or `ShotAtGoal`. SYN-001 has no direct free-kick shot; the replicas have some.
- **Attributes:**
  - `Team` and `Player` on every event with a team or player;
  - `Winner`, `Loser`, `WinnerTeam`, `Type`, `DribbleEvaluation` on `TacklingGame`;
  - `Fouler`, `Fouled`, `TeamFouler` on `Foul`;
  - `CardColor` on `Caution`;
  - `PlayerIn`, `PlayerOut`, `PlayingPosition` on `Substitution` (`player_id` is the outgoing player);
  - `TypeOfShot`, `Goalkeeper`, `DistanceToGoal`, `InsideBox` and `xG` on `ShotAtGoal`. `xG` uses the same formula as `calibration/xg_model.json`; the model is weak, so use it to rank chances, not as a precise probability;
  - `GameSection`, `TeamLeft`, `TeamRight` on `KickOff`;
  - `DefensiveClearance` on clearances (`OtherBallAction` without it is a carry or dribble).
- Referee events (`FinalWhistle`, `VideoAssistantAction`) have `team_side = "none"` and `player_id = null`.

## The story (every moment lands in its scripted minute; actual match clock in brackets)

| Minute | Moment |
|---|---|
| 12' (11:31) | Kinsella-Ward (BRA-11) nutmegs Drummond (CAL-02) on the left wing, crosses, Calderholm clear |
| 23' (22:37) | **0-1**: Fontanieu (CAL-08) intercepts, van Dalfsen (CAL-07) breaks down the right and crosses low, Brightwell (CAL-09) scores with his left foot |
| 31' (30:24) | Wilstead (CAL-04) is booked for a foul on Shimazaki (BRA-10) |
| 41' (40:35) | **1-1**: Lefevre-Saunier's (BRA-03) inswinging corner from the right, header by Ibekwe-Nnadi (BRA-05) |
| 45+2 | Half time |
| 53' (52:40) | Cotterell (BRA-04) brings down Brightwell (CAL-09) in the box; Kirkhurst (BRA-01) **saves van Dalfsen's (CAL-07) penalty** and the score stays 1-1 |
| 58' (57:25) | O Fearghail's (CAL-10) shot hits the post; Brackwater clear the rebound |
| 61'-65' | The most intense spell. At 63' Peyrol (BRA-27) chases a long ball down the right: **34.5 km/h, the fastest sprint of the match** (next best 31.7 km/h) |
| 67' (66:30) | Ulvestad (BRA-09) goes down under Arregui Villalba's (CAL-06) challenge in the box; play goes on; **VAR check**, **no penalty** |
| 70' (69:13, 69:17) | Graycombe (CAL-37) for the booked Wilstead; Harrowell (BRA-49) for Peyrol |
| 76' (75:08, 75:10) | Okonkwo-Bassey (CAL-19) for O Fearghail; Netherby (BRA-46) for Ulvestad |
| 81' (80:42) | Kirkhurst (BRA-01) pushes Okonkwo-Bassey's shot round the post |
| 88' (87:27) | **2-1**: Hennebry (BRA-08) from about 26 m, **the fastest shot of the match** (about 100 km/h from the positions) |
| 90+4 | Full time |

## How it was generated

`cd generator && .venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6 [--overwrite]` takes about 23 s per match (the package is described in [`generator/README.md`](../../generator/README.md#match-generator)).

1. **Script.** `script.json` is validated against the line-ups. Every actor must be on the pitch at that minute. Substitution limits (5 per team, 3 windows) and cards are checked. The score must add up.
2. **Possession chains.** A Markov model over pitch zones (6 x 4, team-attacking coordinates) and actions decides each touch: pass, long ball, through ball, cross, carry, dribble or duel, shot, clearance, foul, offside, ball out. It uses the real positions of all players at that moment. Rates are adjusted by team style (Brackwater possession, Calderholm direct with a high press) and by the "who is pushing" profile. Before a pass, shot, clearance or cross, players nearer to the ball than the kicker step away, and nobody but the taker stands within 2 m of a restart spot, so each kick belongs to its kicker as the pipeline sees it (`R-TOUCH-NEAREST`).
3. **Director.** Before each scripted moment it biases possession and territory, keeps a short possession, then plays the scripted sequence with the same executors. The ball physics and the events therefore stay consistent. A check fails the generation if a moment misses its minute, the score differs, the scripted goal, penalty, post or save does not happen in the simulation, the scripted header has no real contact at head height, the sprinter is not the fastest, or a live ball enters a goal without a goal.
4. **Players.** Each player moves towards the mean position of his role, given the ball position and possession (calibrated maps). Overlays add receiving runs (the receiver reaches the ball when it arrives), pressing, runs in behind, goalkeeper positioning and rushes, set-piece layouts, celebrations and substitutions. The controller limits speed and acceleration:
   - top speed per player from his `pace` attribute (see below), never above 35.2 km/h, or 36.2 km/h for the scripted sprint, against the hard limit of 38 km/h;
   - acceleration capacity a0 (1 - v / vmax) from his `acceleration` attribute; braking up to about 7 m/s²;
   - a minimum separation between players;
   - a smooth "wander" for natural movement.
5. **Ball.**
   - Rolling passes: dv/dt = -a - c v².
   - Lofted balls, crosses and shots: quadratic air drag plus gravity.
   - Bounces, parries and deflections: numerical free flight.
   - Goals cross the line between the posts under the bar; the woodwork really bounces off the post, with nobody within 2 m of the ball at the contact and nobody touching the rebound at once.
   - A header exists only when the player really meets the ball at head height (within 0.6 m, ball 1.5-2.7 m high); a cross nobody meets is a loose ball.
   - Unscripted kicks stay at or below 29 m/s.
6. **Measurement noise.**
   - Players: sigma about 0.1 m, smooth in time (about 0.8 s correlation), a little larger at high speed. Optical tracking errors are smooth, so frame-by-frame noise would invent speed and distance.
   - Live ball: 0.03 m.
   - Dead ball: perfectly still, as in DFL.
7. **Outcomes and annotation**: results from the simulation, event times exactly on the frame, location noise (as described for `events.json`).

**Seeds.** seed = first 15 hex digits of sha256("2026:SYN-001:0") = `483593128784587545`. Replica k uses "2026:SYN-001:k". Separate PCG64 streams are used for events, player parameters, wander, measurement noise, annotation (location noise only) and plan. No wall-clock value is written. Two runs give byte-identical files:

| File | sha256 |
|---|---|
| `positions.parquet` | `6a8374b12503dc589fb947ff3780127aeee3ad30c0fb47d12e7c940dbd5c08ca` |
| `events.json` | `5f1704125eb41666cf4e070d7b3d2248d0adf517d191dc9e011fba5b0f9d3797` |

**Player speed** (since the event-time fix). The top speed and the acceleration of each player come from his 1-99 `pace` and `acceleration` attributes in the universe, through `attributes.physical_value`: the DFL table of his position group in `calibration/attribute_scales.json`, with the documented fallback (CM = mean of DM and AM). The wingers' table is flat between p75 and p90, so van Dalfsen (CAL-07, pace 87) and Peyrol (BRA-27, pace 79) both get about 32.9 km/h; the director gives the scripted sprinter a sprint top speed above every other player's, so Peyrol stays the fastest without changing any attribute (34.5 km/h measured from the positions, next best 31.7 km/h).

## Calibration

**Calibrated on DFL aggregates** (`calibration/*.json`, from 6 DFL/Sportec matches: Bassek, Rein, Weber & Memmert 2025, *Scientific Data* 12:195, doi:10.1038/s41597-025-04505-y, **CC-BY 4.0**):
- pace and acceleration scales;
- top speed and distance per 90 by role group;
- role mean positions by ball cell and possession;
- pass success, pass length and shot rate by zone;
- shot outcome shares and head share;
- ball-in-play share;
- shot and long-pass speeds and high-pass air time;
- the xG coefficients.

**Hand-tuned defaults (v0.1):**
- controller gains and the effort per role group, tuned so that distance per 90 and the speed zones match the DFL p50;
- dead-ball durations, with a feedback that keeps ball in play near the DFL share;
- the action mix of the Markov model, tuned so that per-90 event counts approach the DFL means;
- measurement and location noise;
- the pass intent (the zone's DFL success share - 0.12): the written result comes from the simulation, where contested and loose balls won back also count as completed passes.

**SYN-001 against DFL** (per team per 90; DFL = mean of the six matches):

| | SYN-001 | DFL |
|---|---|---|
| Ball in play | 57.0 % (replicas 56.8-60.5 %) | 56.3 % |
| Distance per 90, outfield (median of players with at least 60 valid minutes, per 90 valid minutes) | CB 9.7, FB 10.5, DM 11.1, CM 11.9, AM 11.1, W 11.2, ST 10.1 km | 9.7, 10.1, 11.0, 11.8, 11.4, 10.9, 10.2 km |
| Goalkeepers | 5.3 and 5.6 km; top speed 24.9 and 24.2 km/h | 5.6 km; 24.2 km/h (p50) |
| Top speed, median player | 30.5 km/h (max 34.5, the scripted sprint; replicas max 31.9-33.1) | 29.9-31.6 km/h by role group |
| Passes (`Play`, set-piece passes included) | 384 | 372 |
| Shots (the penalty included) | 10.5 (replicas 9.3-13.2) | 11.3 |
| Duels (`TacklingGame`) | 87.9 | 95.6 |
| Fouls / throw-ins | 9.5 / 35.2 | 10.7 / 20.5 |
| Goal kicks / corners | 6.2 / 1.9 | 8.1 / 3.9 |
| Pass success (from the simulation) | 83 % home, 75 % away (80 % overall; replicas 74-85 %) | 79 % |

The data pipeline prepares SYN-001 like any provider (`cd data_pipeline && .venv/bin/python -m insidethegame_data.qc SYN-001 --source synthetic`; report in `reports/qc/SYN-001/`):
- 99.83 % of player samples are valid;
- 45 overlap episodes (DFL about 57 per match);
- no tracking-area edge and no physical-limit episode;
- event clock offset **0.00 s** in both halves (it was -0.9 s with the annotation delay); the second-half `KickOff` is paired with its own frame;
- 680 of 690 passes confirmed by their kick; for 508 of the 528 successful passes that the pipeline follows, the next touch it sees on the tracking is a team-mate's (96 %); 12 of 14 clearances confirmed;
- 19 of the 21 shots located and confirmed: all 3 goals by the goal-line crossing, the scripted 58' post by the woodwork rule (`R-WOODWORK`), 3 blocked shots, 2 saves by a goalkeeper touch (the scripted 81' save included) and 10 wide. Not located: a shot blocked right at the kick (42') and a shot where a defender is nearer to the ball than the shooter at the kick (75'). The 50' header labelled saved is matched to the rebound shot that follows it, so its observed outcome is wide. The penalty is a restart (`Penalty`), outside these 21.

## Limitations (v0.1)

- **Rule-based movement.** There is no man-marking or overlapping run. Offsides come from the positions: attackers stay onside except runs in behind that start too early, and a pass is whistled offside only when the receiver is beyond the second-last opponent at the kick (SYN-001 has 3). Distances per 90 match DFL because they were tuned, not because they emerge from tactics; high-speed running does not: wide players and midfielders sprint more often than in DFL, strikers less.
- **Player controller and pass rhythm (known, left as they are).** The controller makes about 3-5 times the DFL count of accelerations above 3 m/s² per 90 minutes, and passes are slow: about 34 km/h against 50-54 km/h in DFL, and about 18 m long against 14-16 m. In the final match fixes fixing either one alone made the physical profile worse, so they need a dedicated calibration of the controller together with the pass rhythm.
- **Hand-set Markov action mix.** DFL zone transitions are not fitted yet. Too few goal kicks and corners, too many throw-ins; possessions last about 10 s against 4-5 s in DFL.
- **Goalkeepers.** They rush out to balls in their area and claim crosses, but there are no dives in the tracking: a save is reached with the hands, about 1.5 m from the tracked centre.
- **Only the story's cards, penalty, goals and substitutions.** SYN-001 has 1 caution (DFL about 2 per team), one scripted penalty (53', saved) and no random goals. There are no red cards, injuries or dropped balls in v0.1. The replicas have random cards, random goals from xG and 3-5 random substitutions, but no penalty.
- **Ball in play in the replicas** is 56.8-60.5 %, a little higher than DFL: they have no scripted stoppages such as the VAR check.
- **Shots and blocks.** A shot can only be meant to be blocked when a defender stands in the shooting lane, and a keeper or defender who cannot reach the ball does not touch it. SYN-001 has 22 shots (DFL about 23 per match), 4 of them blocked; the replicas have 19-28 shots and 4-9 blocked. The room made around every kick still leaves one SYN-001 shot (75') with a defender nearer to the ball than the shooter at the kick.
- **Shot-speed measurement artefact (pipeline and engine).** When a hard shot goes out of play within about 8 frames of the kick, the Savitzky-Golay filter (`mode="interp"`) at the end of the live segment overshoots and reads 122-130 km/h, against a launch of at most 104 km/h (29 m/s, the cap of unscripted kicks). It does not occur in the final SYN-001 (fastest shot 100.6 km/h, the 88' goal), but other generated matches can show it.
- **Ball skip on a failed interception.** When an interception fails, the ball's run-on skips two frames: a jump of up to about 1.5 m in one frame (9 frames in SYN-001). The Savitzky-Golay smoothing keeps it out of the measured speeds here (fastest live ball 100.6 km/h). It is known and left as it is: the fix changes the whole match, which then has to pass every check again.
- **Realism noise.** With 6 replicas the traffic light of the realism report (`reports/realism/`) moves by about ±11 red metrics from replica sampling alone, so a generator change is judged on its own metrics and on the direction of each group, not on the total.
- **Clean tracking.** No provider artefacts (jumps, swaps, frozen runs, tracking-area edges) and no referee tracks. `calibration/artefact_profile.json` could add them later.
- **Event times are exact.** There is no annotation delay or jitter (DFL operators annotate restarts about 1 s before the physical restart, other events with some scatter). The generator's `true_frame` equals the frame of `time_utc`.
- **Stadium roar.** No crowd-noise signal is generated: it was dropped from the MVP.
