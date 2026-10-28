# statseat API contract (v0.3.1)

The contract between the statseat backend and the PWA: Pydantic models, fake data and a mock server, so the
engine, agents, app and games can be built in parallel and switched to the real backend without changing the app.

| File | What it is |
|---|---|
| `models.py` | Pydantic models of every request and response (the contract) |
| `tickets.py` | Ticket tiers and their rules, code format, hashing, the server-side ticket registry |
| `phases.py` | Which sections are unlocked in each phase (reuse it in the engine) |
| `mock.py` | FastAPI mock server that serves the fake data with the models |
| `openapi.json` | Exported OpenAPI document (`info.version` = `CONTRACT_VERSION`) |
| `fake/` | One JSON file per response; `fake/server/` holds server-only data (ticket hashes, correct answers) |
| `make_fake.py` | Builds `fake/` from `synthetic/` (universe `MERIDIA-1.0`, match `SYN-001`) |
| `registry.py` | Which model each fake file follows |

## Run

```bash
python3.12 -m venv app/.venv && source app/.venv/bin/activate
pip install -r app/requirements.txt
python -m app.contract.mock            # http://127.0.0.1:8000/docs (PORT, MOCK_PHASE to change)
python -m pytest app/tests             # fake data vs models, consistency, tiers, every endpoint
python -m app.contract.make_fake       # after editing make_fake.py
python -m app.contract.export_openapi  # after editing models.py or mock.py
```

**Real backend:** `python -m app.engine` serves the same paths and models; the match endpoints come from the
match engine (`app/engine/README.md`), the others from this mock until their tasks replace them.

Send the token as `Authorization: Bearer <token>`. The mock starts at half-time (`MOCK_PHASE=half_time`); a demo
ticket jumps between phases with `POST /api/demo/phase`.

## Tickets

| Tier | Letter | Unlocks | Devices |
|---|---|---|---|
| `silver` | `S` | Casual only: Analyst is refused with **403** `mode_not_allowed` | one |
| `gold` | `G` | Analyst (default), with switch to Casual | one |
| `demo` | `D` | gold + Inspector + demo phase panel (also the judges' ticket) | several |

The rules live in `tickets.TIER_RULES`; the session exposes `tier`, `allowed_modes`, `inspector_enabled`,
`demo_panel_enabled` and `multi_device`. Tier and `match_id` are stored with the token server-side and never come from
the client. A single-device ticket used from a second `device_id` gets **409** `ticket_in_use`; an unknown or
malformed code gets **401** `invalid_ticket`; a ticket outside its validity window gets **403** `ticket_not_valid`.

**Code format:** `GNT-<MATCH>-<S|G|D>-XXXX-XXXX` (MATCH = match id without the dash, e.g. `SYN001`; the 8 characters
come from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`). Case and spaces are ignored.

**Fake codes for testing** (public fake demo codes, valid 20 October to 10 November 2026):

| Tier | Codes | Seat |
|---|---|---|
| silver | `GNT-SYN001-S-49Y3-D435`, `GNT-SYN001-S-V89W-HHST`, `GNT-SYN001-S-HZX2-RQQH` | Foundry End N2, row 14, seats 21–23 |
| gold | `GNT-SYN001-G-LQEB-FJN5`, `GNT-SYN001-G-3ZGR-28VQ`, `GNT-SYN001-G-ZCFM-7SD3` | Main Stand W3, row 8, seats 11–13 |
| demo | `GNT-SYN001-D-MK9G-UGKC`, `GNT-SYN001-D-HGDG-VUJM`, `GNT-SYN001-D-5BDQ-97CR` | none (pass) |

**Hash only.** The server never stores a code: `fake/server/tickets.json` (the registry) keeps
`HMAC-SHA256(salt, code)`, the tier, the match, the seat, the validity and the image paths. The salt comes from
`STATSEAT_TICKET_SALT`; without it the server uses `tickets.DEV_SALT`, a **public, not secret** default used for the fake
tickets. A deployment sets its own salt (never in the repo) and rebuilds the registry; with a different salt the fake
codes stop working.

**Printed tickets of SYN-001 (from 24/10).** The ticket generator writes
[`synthetic/SYN-001/tickets/`](../../synthetic/SYN-001/tickets/README.md): images (SVG, PDF, PNG) and `tickets.json`
with tier, seat and `code_sha256`, the plain SHA-256 of the normalised code (Crockford base32: upper case, separators
removed, O read as 0 and I or L as 1 in the last 8 characters; the last character is a check character). Those codes
are derived from public inputs (universe seed, match id, generator code), so a salt would add no secrecy:
`tickets.load_registry` reads `tickets.json` as it is (`hash = "sha256"`, validity 20 October to 10 November 2026),
and the engine (`python -m app.engine` with `synthetic:SYN-001`) uses it instead of the fake registry. The published
codes (12 demo, 2 gold, 2 silver) are in the tickets README; the fake codes above keep working with the mock and
with `MATCH_SOURCE=fake`. To use another registry: `STATSEAT_TICKETS_FILE=<file>`.

A registry in the contract format (`statseat-tickets/1`, HMAC with a deployment salt) can still be built from a manifest
of plain codes (`{"match_id", "valid_from", "valid_until", "tickets": [{"ticket_id", "code", "tier", "seat", "files"}]}`)
with `STATSEAT_TICKET_SALT=<salt> python -m app.contract.tickets hash <manifest.json> <registry.json>`.

## Endpoints

| Method and path | Returns | Notes |
|---|---|---|
| `POST /api/auth/login` | `LoginResponse` | ticket code + device id → opaque token; the server maps it to tier and `match_id` |
| `GET /api/session`, `PUT /api/session/preferences` | `Session`, `Preferences` | mode (one of `allowed_modes`), up to 3 favourite players (of this match; locked once confirmed, v0.2.6), language |
| `GET /api/ticket`, `GET /api/ticket/image?format=svg\|pdf\|png` | `TicketInfo`, file | My ticket: tier, match, seat, validity, what the tier unlocks, image links (404 until the match generator writes the files); no personal data, never the code |
| `GET /api/match` | `MatchOverview` | teams, line-ups, officials (Pre-match) |
| `GET /api/match/state` | `MatchState` | phase, minute, score and which sections are unlocked |
| `GET /api/match/events` | `EventFeed` | key events already processed (DFL types in English, never `Delete`) |
| `GET /api/stats/live` | `LiveStats` | only during play |
| `GET /api/stats?mode=` | `MatchStats` | Data & Statistics (metrics of [`Materials/METRICS.md`](../../Materials/METRICS.md)); analyst-only fields are `null` in Casual (the app's Fan) |
| `GET /api/recap?mode=` | `Recap` | after the final whistle |
| `GET /api/recap/pdf?mode=` | PDF file | the final report, built by the server from the Recap, its key moments and the Data & Statistics of the same mode (v0.2.6); after the final whistle, English |
| `GET /api/highlights` | `HighlightList` | VAR moments (v0 fields) |
| `GET /api/games`, `GET /api/games/players` | `GameList`, `PlayerPicker` | games open only at half-time; `GameList.prize` is the sponsor's prize for the winner; the picker has only name, team and shirt number |
| `POST /api/games/tiki-taka-toe`, `…/{id}/moves` | `TttGame`, `TttMoveResult` | grid from real career-club and nationality crossings |
| `POST /api/games/guess-the-player?version=career\|transfers`, `…/{id}/answer` | `GuessRound`, `AnswerResult` | both versions; fees in EUR |
| `POST /api/games/quiz`, `…/{id}/answer` | `QuizRound`, `AnswerResult` | 4 options, answer from the stats |
| `GET /api/leaderboard` | `Leaderboard` | stadium leaderboard |
| `POST /api/archie/ask` | `ArchieAnswer` | text + grounding of every number + chart to open + Inspector trace |
| `POST /api/demo/phase` | `MatchState` | demo tickets only: jump to a phase |

Errors use `ApiError` (`code`, `message`, `section`, `unlocks_at_phase`); a locked section returns **423**.

## Rules kept by the contract

- `match_id` and the tier never come from the client (no path or query parameter has them).
- `inspector` fields are `null` for silver and gold tickets; Analyst data is refused (403) to silver tickets.
- **No look-ahead:** `MatchState`, `EventFeed`, `LiveStats`, `MatchStats` and `HighlightList` carry `as_of_s`, the
  playback time (seconds since kick-off, first-half added time included) they were computed at. The engine fills
  it from its playback window; nothing in a payload may use frames or events after it.
- Answers are timed by the server (`elapsed_ms`); correct answers never leave the server before the answer.
- All names are fictional and come from `synthetic/`. The match story (goals, shots, cards) is **fake**, not the
  output of the SYN-001 generator: Brackwater Athletic 2-1 Calderholm United.
- `Highlight` follows the **v0** draft of VAR: its fields may change after review.
- A metric the backend cannot compute (the source has no tracking, the method is pending) is `null` or an empty list
  and is listed in `unavailable` (`metric`, `reason`) in `LiveStats`, `MatchStats` and `Recap`. The app shows the
  reason (or hides the card), never a placeholder number. The mock's fake data fill every metric.

## Changes in v0.2.0

App name statseat; ticket tiers `silver`/`gold`/`demo`/`judge` replace `FAN`/`DEMO`/`JUDGE` (`Session.ticket_type` →
`Session.tier`, plus `allowed_modes` and `multi_device`); codes `GNT-…` stored as HMAC hashes; demo and judge on several
devices; `GET /api/ticket` and `/api/ticket/image`; `as_of_s` on the match payloads; the default mode follows the tier
(gold, demo, judge start in Analyst).

## Changes in v0.2.1

From the match engine: frame-based metrics can be `null` when the source has no tracking or the method is
pending, with the reason in the new `unavailable` list (`MetricUnavailable`). Now nullable: in `LiveStats`
`possession`, `momentum`, `top_speed`, `distance_covered`, `ball_circulation`, `crowd_noise_db`; in `MatchStats`
`distance_covered`, `ball_circulation`, `possession`, `effective_playing_time_min`, `momentum`, `crowd_noise`;
`TeamCounts.sprints`; in `FavouritePlayerRecap` `distance`, `top_speed_kmh`, `passes`, `pass_accuracy_pct`. Lists stay
lists (empty when unavailable). No field was removed or renamed: the fake data are unchanged.

**Server side, no API change:** the registry also reads the generator's `tickets.json` (SHA-256 of the
normalised code, Crockford aliases); the models and `openapi.json` are unchanged, so the version stays 0.2.1.

## Changes in v0.2.2

**No judge tier**: the judges use **demo** tickets, so they see the Inspector and can jump to any
phase whenever they want. `TicketTier` is now `silver` / `gold` / `demo`; codes are `GNT-<MATCH>-<S|G|D>-…`, and a
`J` code is refused with **401** `invalid_ticket`. The three fake judge codes and `fake/ticket_judge.json` are gone
(9 fake codes); SYN-001 has 12 printed demo tickets. Nothing else changed in the models or the fake data.

## Changes in v0.2.3

**No Polls, games only at half-time**: the `polls` value is gone from `Section` (the polls were
dropped on 25/10); `games` is unlocked only in `half_time` (no longer after the final whistle), so after half-time
`GameInfo.unlocks_at_phase` is `null` and the games endpoints answer **423**. New `GameList.prize` (`GamePrize`):
the invented match sponsor's prize for the half-time winner, from the universe (`match_preview.sponsor`).
The quiz covers the first half only. Fake data: `games.json` has the prize; `match_state_*.json` have no polls row.

## Changes in v0.2.4

**Second kit in the team colours**: `TeamColours.away_kit` (`KitColours`: `shirt`, `shorts`,
`socks`) from the universe (`club_details.<club>.kits.away`). The app uses the home colours for each team's mark and
switches the away team to its second kit when the two marks are too similar. Optional field: nothing else changed.

## Changes in v0.2.5

**Live and Data & Statistics redefined**:
- `data_stats` is open from kick-off: `first_half`, `half_time`, `second_half`, `full_time`. During play `MatchStats`
  has `scope = live`, `up_to_minute` = the scoreboard minute and the numbers of the engine's live tick (never later
  than `as_of_s`).
- `LiveStats` keeps only `score`, `shots` (`TeamShots`: shots, on target, goals) and `counts`; removed: `possession`,
  `momentum`, `top_speed`, `shot_speed`, `distance_covered`, `ball_circulation`, `crowd_noise_db`.
- New `TeamCounts.penalties` (penalty kicks taken).
- New `MatchStats.locked_charts`: the charts of the other version (Analyst), listed in Casual so the app can show them
  blurred as a preview; empty in Analyst.
- **Removed from the project:** crowd noise (`MatchStats.crowd_noise`, `CrowdNoise`, `Highlight.crowd_peak_db`,
  highlight type `crowd_peak`) and the pass network (`MatchStats.pass_network`, `PassNetwork`, `PassNode`,
  `PassEdge`). The `crowd` factor is gone from the highlight importance.
- Fake data: `stats_{mode}_live_first_half.json` and `stats_{mode}_live_second_half.json` (Data & Statistics during
  play); the mock serves them in the two halves.

## Changes in v0.2.6

**Account: 3 favourite players and the final report in PDF**:
- `Preferences.favourite_player_ids` has at most **3** players (`MAX_FAVOURITES`), no repeats (**422**
  `duplicate_player`). New `Preferences.favourites_locked`: the fan confirms exactly 3 players with `true` (otherwise
  **422** `favourites_incomplete`); after that any change to the favourites, or `false` again, is refused with **409**
  `favourites_locked`. Mode and language still change. A new session starts with **no** favourites (the fan chooses
  them); `fake/preferences.json` is only an example payload.
- New `GET /api/recap/pdf?mode=`: the final report as an A4 PDF (`application/pdf`, attachment
  `statseat-report-<match>-<mode>.pdf`), built by the server (`app/report/`, library **fpdf2**, pure Python, so the slim
  container needs no system libraries). It holds the Recap's numbers (score, goals, shots, on target, possession, key
  numbers), the favourite players' table and the Recap's `narrative` (a summary the code writes from the numbers).
  Same rules as `/api/recap`: **423** before full time, **403** for Analyst on silver.
  English only (the server's texts are English); text outside Latin-1 is replaced, since the PDF uses the core font.
- With the PWA served by the same server, `/api/recap/pdf` is registered before the app's catch-all route.

## Changes in v0.3.1

**The half-time games are real** (the games code; rules and logic in
[`app/games/`](../games/), used by the mock and therefore by the real backend). All additions are optional fields or new
paths, so older clients keep working:
- **Tic Tac Goal** (the name shown to fans since 26/10, once Tiki-Taka-Toe: an app with that name exists; ids and
  paths keep `tiki_taka_toe`): the match's grid, the same for every fan, chosen from the universe (career clubs and nationalities,
  never the two match clubs, every cell with at least 2 answers, mostly 2-3); a game against the fan-bot (random
  pairing with another fan is not built yet); 20 s per turn, a wrong or late answer loses the turn, a taken cell does
  not; three in a row wins; a full grid or 10 minutes without a three is a draw. Points: 10 per cell, 100 for a win, 50
  for a draw. New `TttGame.ends_at` and new **`GET /api/games/tiki-taka-toe/{game_session_id}`**, which applies the
  clocks (after 20 s the bot has played).
- **Guess the player:** 5 questions of 20 s per session, the same 5 players for every fan; the full career with the
  transfer fees and the current club hidden (`CareerClue.move`, `fee_eur` only for paid transfers, `hidden`);
  4 options from the same unit; `GuessRound.round_number` and `rounds_total`; a 6th question answers **409**
  `no_more_rounds`. The real game always answers `version: "career"`. Points as before: 100 plus up to 50 for speed.
- **`GET /api/crests/{club_id}`:** a club's crest (`synthetic/crests/`), public like any image of the app.
- The quiz is unchanged (fake round).
- **The final report** (`GET /api/recap/pdf`, same path and parameters) follows what the app shows the ticket: the
  Recap's summary and key moments, then the Data & Statistics of the same mode (tables, each chart as a small table
  or bars, the favourite players; in Casual the Analyst content is only named), and the Inspector's methods for demo
  tickets. `Recap.narrative` now says what it is: a summary the code writes from the numbers (description only).

## Changes in v0.3.0

**The metric decisions of 25 October** ([`Materials/METRICS.md`](../../Materials/METRICS.md)), fixed once
so that the app can be built on the fake data while the metrics work fills the real numbers into the same fields:
- **Fan and Analyst:** the app's names of the two modes; the values stay `casual` and `analyst`.
- **New models:** `Reliability` (share of the time measured and its symbol: high at least 95 %, medium 85-95 %, low
  below 85 %) on `Distance`, `BallCirculation` and `AccelerationLoad`; `PlayerFocus`, the card of a favourite player,
  the same in `MatchStats.player_focus` and `Recap.favourite_players` (replaces `FavouritePlayerRecap`, with the same
  field names plus sprints, touches, tackles, recoveries, fouls won, cards and the Analyst extras);
  `AccelerationLoad`; `CounterAttacks`; `EffectivePlayingTime` (per half, with the World Cup 2022 and Bundesliga
  references; replaces `effective_playing_time_min`); `GoalsVsXg` (replaces `LuckOrAim`: numbers only);
  `TeamLeaders` (top runner, sprinter, passer and the 3 players with the most touches); `PlayerRow` (the Analyst table of
  all players); `GoalMark` (goals on the charts over time); `MatchStats.ppda` (Analyst).
- **Changed:** `TeamCounts` gains `woodwork` and `offsides`, and `tackles` and `ball_recoveries` are in both modes;
  `PeakIntensity` is a block (team peaks, a series per minute, each player's peak); `TopSpeed` is 1 per team in Casual,
  up to 5 in Analyst; `MatchStats.shot_speed` is a list (the hardest shot of each team, headers excluded); `Shot`
  gains `is_penalty`, `is_header` and `speed_kmh`; `BallCirculation` is in **km/h**, with its series in Analyst only;
  `Possession` has no series; `TeamPasses` loses the histogram and gains `long_balls`; `ZoneMap` and `SpeedZones`
  gain an optional `player_id`; `MetricUnavailable` gains `available_from_minute` (the app shows "Available from 5'";
  null hides the card).
- **Charts** (one mirror chart for every series over time, home team above the axis): `momentum`, `shot_map`,
  `xg_cumulative`, `peak_intensity`, `acceleration_load`, `ball_circulation`, `zone_map`, `speed_zones`,
  `defensive_shape`. Fan: momentum, shot map and running intensity; the others are Analyst only and shown blurred in
  Fan. Distance, possession and pass lengths are not charts any more. `DATA_STATS_TABLES` lists the tables in display
  order; Archie can point to a table as to a chart.
- **Removed:** the 2D replay (`Highlight.replay`, `ReplayRef`, `Replay2D`, `ReplayFrame`, `ObjectPosition`, `Point3`
  and `GET /api/highlights/{id}/replay`): it is a roadmap item.
- **Fake data:** every fake stats and recap file carries a Player Focus card for every player who played; the mock
  serves the session's favourites only, in the order the fan chose them.
- **Engine:** fills what the events and the current frame metrics give (counts, woodwork, offsides, touches, fouls won,
  leaders, the players table, the Player Focus, circulation in km/h, effective time per half); the rest is null with
  the reason in `unavailable` until the metrics work.
