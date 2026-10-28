# generator

The synthetic data generator of InsideTheGame, all in English. It contains:
- **Scout v1**: builds the fictional universe and the match information of `SYN-001`;
- the **match generator** (Tactician v0.1): the events and the 25 Hz tracking of `SYN-001` and of its replicas (see [Match generator](#match-generator)).

The Linesman, the full realism report, is `data_pipeline/insidethegame_data/realism.py`; its results are in `reports/realism/`.

Everything here is invented. The country of **Meridia**, its 44 clubs, the Lanternworks Stadium and every person are fictional; any resemblance to real ones is coincidental. Nationalities and birthplaces abroad use real country and city names. No real data is used: the DFL data only feeds the calibration, as aggregates.

## Set up (once)

```bash
cd generator
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Generate

```bash
cd generator
.venv/bin/python -m insidethegame_gen            # seed 2026 by default; --seed N for another universe
.venv/bin/python -m pytest -q
```

The run prints every check. **If any check fails, nothing is written.** The same seed and the same content files always give the same files, byte for byte (a test runs the generator twice and compares the hashes; the report lists the input and output hashes).

| Output | Content |
|---|---|
| `synthetic/universe/universe.json` | the universe: competitions, 44 clubs, roll of honour, the two squads (profiles, careers with transfer fees, honours, season numbers, form, availability), staff, coaching history, officials, stadium, results, next round, tables, top scorers, head-to-head, match preview with the match sponsor and the prize for the games |
| `synthetic/SYN-001/match_info.json` | the match, with the structure of the DFL `matchinformation` file: line-ups with English position codes, head coach and bench staff per team, referees with English roles, environment with the forecast, the stadium capacity (`stadium_capacity`) and the tickets sold (`tickets_sold`); for each team the kit worn (`kit`) and the repository paths of its crest (`crest_file`, `synthetic/crests/<club>.svg`) and of its two kits (`kit_files.home`, `kit_files.away`, `synthetic/kits/<club>_<kit>.svg`), drawn by `insidethegame_gen.crests` |
| `synthetic/SYN-001/kitman/*.md` | the knowledge base of the Kit Man agent, one document per topic, each tagged with `match_id` |
| `synthetic/schema/*.schema.json` | JSON Schema of the two files, exported from the Pydantic models |
| `synthetic/README.md` | one-page overview of the generated data (the match, the two clubs, the stadium, the league, an example Tiki-Taka-Toe grid) |
| `reports/synthetic/universe/README.md` | the validation report, the games feasibility, what the attributes 1-99 mean in physical terms and the reproducibility hashes |

## Digital tickets

A ticket code unlocks **statseat**, the fan app, for one match. The tier of the ticket decides what the app shows.

```bash
cd generator
.venv/bin/python -m insidethegame_gen.tickets SYN-001
.venv/bin/python -m insidethegame_gen.tickets SYN-001 --qr-url https://<demo-host>/login   # QR opens the app
```

Run it after the universe and the match information: it reads `synthetic/universe/universe.json` and `synthetic/SYN-001/match_info.json`. It prints 8 checks; **if any check fails, nothing is written**. The same inputs always give the same files, byte for byte, PDF and PNG included (a test builds twice and compares; another test fails if the committed tickets are out of date).

| Tier | Letter | statseat | Tickets | Seats |
|---|---|---|---|---|
| silver | S | Casual only; one device | 120 | the home sectors of all four stands |
| gold | G | Analyst, can switch to Casual; one device | 30 | the central home sectors along the touchlines (W3, W4, E4, E5) |
| demo | D | gold + Inspector mode + demo panel (jumps between match phases); several devices. The hackathon judges use these | 12 | one block in the Club Level of the Main Stand (W8, gate H) |

| Output in `synthetic/SYN-001/tickets/` | Content |
|---|---|
| `tickets.json` | the registry: per ticket the tier, modes and features (`inspector`, `demo_panel`, `multi_device`), the seat (gate, stand, sector, row, seat), the image files and an alt text. **Only `code_sha256` of each code, never the plain code** |
| `README.md` | for the judges: what each tier unlocks, how to log in, the **plain DEMO codes** and the four silver and gold sample codes |
| `svg/`, `pdf/`, `png/` | every DEMO ticket plus four silver and four gold samples: a portrait mobile ticket (SVG 720 × 1280, PDF 540 × 960 pt, PNG 1080 × 1920 px) with crests, date, stadium, seat, tier ribbon and the statseat box with the code and its QR code |

- **Codes**: `GNT-SYN001-<S|G|D>-XXXX-XXXX`, Crockford base32 (no I, L, O, U). The last character is a Luhn mod 32 check character, so any single mistyped character is caught. `validate_code(code)` returns `(ok, tier, match_id)` and accepts lower case, spaces, missing dashes and O/I/L for 0/1. `code_sha256(code)` hashes the normalised code: the server stores and looks up only that.
- **Demo credentials, not secrets**: codes are derived from public inputs (universe seed, match id, tier, ticket number), so anyone with the repository can regenerate them. In production the codes would come from a secret key in Azure Key Vault and the server would store salted hashes.
- **Seats**: gates, stands and sectors come from the stadium data (each sector has exactly one gate); rows and seat numbers are synthetic, about 80 seats per row. Home tiers never get the away section (S6, S7, gate F).
- **Crests**: the tickets use `synthetic/crests/<club_id>.svg` when it exists; otherwise a plain placeholder shield with the club's initials in its home-kit colours. Re-run the command after adding crests.
- **Safety**: every text on the tickets, in the README and in the alt texts is checked against `content/blocklist.json`; no plain code appears in `tickets.json`, and only the published codes appear anywhere.

## Conventions

| Topic | Rule |
|---|---|
| Codes | English only (guidelines section 12): positions `GK, LCB, RCB, CB, LB, RB, LWB, RWB, CDM, LDM, RDM, LCM, RCM, LM, RM, CAM, LW, RW, ST, LS, RS`, position groups `GK, CB, FB, DM, CM, AM, W, ST`, staff roles `head_coach, assistant_coach, ...`, referee roles `referee, assistant_referee_1, assistant_referee_2, fourth_official, video_assistant_referee, assistant_video_assistant_referee`. No DFL code anywhere |
| Positions | every player has a natural position; starters play the formation slot (exactly the slots of the formation), substitutes are listed with their natural position |
| Names | every person (players, staff, coaches, officials, chairmen, legends, other league players) has `first_name`, `last_name`, `title`, `nickname`. No surname is repeated in the universe; first names are not repeated in the match and never copy an official's; surnames in the match never start with the same three letters |
| Money | transfer fees are integers in **euros** (`fee_eur`, `meta.currency = "EUR"`). Loans, free transfers, returns from loan and academy promotions are 0. No salaries |
| Seasons | labels like `2026/27`; the Super Shield is labelled by year (`2018`) and is played in August at the start of the season |
| Matchday | only the match of the ticket: results up to matchday 8, fixtures of matchday 10, nothing about the other games of matchday 9 |

## How it works

| Part | Made by | Why |
|---|---|---|
| Clubs, stadium, staff, chairmen, crests, records, coach profiles, club history, legends, traditions, roll of honour, match programme and forecast, the match sponsor (Quayspoke Cycles, with a Lamplighters Store voucher for the games winner), rules guide, the building blocks of player profiles (`insidethegame_gen/content/`) | hand-written with Claude Opus 5.5 in Claude Code, reviewed by the team | creative text that should read well |
| Player names, ages, birthplaces, careers and fees, coaching history, fixtures, results, tables, the season simulation (minutes, goals, assists, cards, injuries, bans), line-ups, top scorers, team news, key duels, officials' numbers | seeded random generators (`scout.py`, `season.py`, `careers.py`, `profiles.py`) | they must add up: the players' goals equal the team's goals, the tables follow from the results, careers have no gaps, honours follow from the careers |
| Player attributes 1-99 | `attributes.py`: a seeded latent model per player (quality, style, strengths, age) on the DFL percentile scale of `calibration/attribute_scales.json` | the scale must be calibrated on real data, and the numbers must agree with the profile texts (see [Player attributes 1-99](#player-attributes-1-99)) |

For the games, careers are drawn more often through twelve "hub" clubs, and each of the four main foreign nationalities has a scouting network of three clubs (`content/names.json`), so club x club and club x nationality crossings have several answers.

## Player attributes 1-99

Six attributes per player, on **our own scale calibrated on DFL data** (`calibration/attribute_scales.json`, 6 DFL matches, Bassek et al. 2025, CC-BY 4.0). Attribute k is the **k-th percentile among players of the same position group**: a winger with pace 90 reaches about the DFL p90 top speed of wingers. The numbers are relative to the group, so compare players of different positions through the physical value.

| Attribute | DFL quantity (per player-match) | Unit |
|---|---|---|
| `pace` | top speed: maximum of our valid speed | km/h |
| `acceleration` | peak positive tangential acceleration on valid samples | m/s² |
| `stamina` | distance per 90 minutes of measured time | m |
| `work_rate` | distance above 20 km/h per 90 minutes of measured time | m |
| `passing` | pass success share (next touch by a team-mate), player-matches with at least 20 passes | share 0-1 |
| `shot_power` | peak ball speed in the 0.5 s after the kick of a confirmed open-play shot (per shot, one scale for every group) | m/s |

- **Mapping**: `attributes.physical_value(scales, attribute, group, k)` returns `values[k-1]` of the group's table (linear between integer percentiles). A group without a table uses the percentile-wise mean of its neighbour groups (CM: DM and AM; ST passing: AM and W), and the overall table only as a last resort, because its low percentiles are goalkeepers' values. Goalkeepers always use their own tables.
- **How the numbers are made** (deterministic, one seeded stream per player, `"<seed>:attributes:<player id>"`): a latent score from `attribute_profile.quality`, three shared factors (speed, endurance, technique) and noise, shifted by the main and second playing style, the strengths and an age curve, turned into a percentile with the normal distribution.
- **Consistent with the texts**: a strength or a main style that names an attribute (for example the strength "pace" or the style "pacy") guarantees a minimum value (`attributes.floors_for`); check 23 verifies it.
- **Not in the Kit Man documents**: UNIVERSO-SPEC does not ask for them, so the documents did not change.
- The report (`reports/synthetic/universe/README.md`) shows the physical value of 10, 50 and 90 per group and the two captains as examples.

## Checks (`validate.py`)

Each check returns pass, warn or fail with values. Every check is a fail.

1. References and ids are unique and resolve (players, staff, coaches, set-piece takers, scorers, match staff).
2. Each competition has the declared number of clubs.
3. Names: no surname repeated in the universe; distinct first names in the match, never an official's; unique nicknames.
4. Same name schema for every person; no title or nickname inside a name.
5. No famous real person, club, stadium or brand (`content/blocklist.json`).
6. No alcohol, betting or tobacco words or phrases anywhere, including the Kit Man documents (prohibitions such as "smoke-free" are allowed).
7. Line-ups: 11 starters in exactly the formation's English codes, 9 substitutes with a goalkeeper, one captain, no duplicate, no null, head coach in the match staff.
8. Squad and line-up aligned: shirt, club, position, status and captaincy; every player not selected has a reason consistent with his availability.
9. Ages, heights, staff, officials and coaches plausible.
10. Careers: contiguous, end at the current club, valid moves and fees, loans with their parent club, `academy_graduate` consistent with the first spell, coaches consistent with the coaching history.
11. Coaching history: one head coach per club per season, never at two clubs at once.
12. Trophies: player honours follow from the roll of honour and the spells; the match clubs' honours agree with the roll.
13. Results and standings are consistent.
14. Only the match of the ticket: no other game of matchday 9 in the data or the documents.
15. Current-season numbers add up (goals, starts, minutes, assists, match logs, form).
16. Head-to-head: the last derby's scorers were at the club that season.
17. Top scorers match the players' numbers and the clubs' goals.
18. Stadium: sectors add up to the capacity, every sector has exactly one gate; the match information has the stadium's capacity and no more tickets sold than seats.
19. Match information: 6 officials with English roles and card numbers for the referees, forecast in time order, the crest and both kit files of each team exist, no result yet.
20. Data for Kit Man (UNIVERSO-SPEC section 2) is complete.
21. Kit Man documents: separate Starting XI and Substitutes sections with the right players and positions, a section per player, no DFL code.
22. Games: at least 3 foreign nationalities with 4+ players, each crossing at least 3 clubs with 2+ answers, enough valid 3x3 grids (the report gives the count).
23. Attributes 1-99: all six for every player, integers in 1-99; every attribute and group has a DFL table (or the documented fallback); mapped top speeds below 38 km/h and shot speeds below 211 km/h; no number below what the player's strengths or main style promise.

## Match generator

It turns a match **script** (the key moments) into DFL-like **events** and **25 Hz tracking** of 22 players and the ball. Everything is invented and in English.

```bash
cd generator
.venv/bin/python -m insidethegame_gen.match SYN-001                          # synthetic/SYN-001/{events.json, positions.parquet}
.venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6             # + synthetic/replicas/SYN-001-r1..r6 (git-ignored)
.venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6 --only-replicas   # only the replicas (about 23 s each)
.venv/bin/python -m insidethegame_gen.match SYN-001 --overwrite              # existing files are never replaced without it
.venv/bin/python -m pytest -q tests/test_match.py                            # 29 tests, under a minute
.venv/bin/python -m pytest -q tests/test_e6_rules.py                         # 13 tests of the final match fixes rules (headers, post, kick space)
```

A full match takes about 23 s. `positions.parquet` is about 13 MB. Two runs give byte-identical files.

| Function (`insidethegame_gen.match`) | Use |
|---|---|
| `generate_match(script_path_or_dict, seed, out_dir)` | a scripted match. `seed=None` derives the seed from the universe seed and the match id. Returns a summary: score, the actual minute of every moment, calibration used, file hashes, and `kick_ownership` (the kicks where another player is nearer to the ball than the kicker, noise-free) |
| `generate_replica(match_id, k, out_dir)` | same fixture and squads, no story: Markov chains only, random stoppage time, substitutions, cards and goals from xG. The replica folder also gets its own `match_info.json` (the fixture's, with the replica's `match_id`: `replica_match_info`), so the data pipeline reads it like any synthetic match |
| `realism_report(match_dir)` | quick sanity statistics from the files: distance, speed zones, top speed, sprints per player, event counts, passes and shots per team, ball in play. The full **Linesman** realism report (synthetic vs DFL distributions through the same preparation pipeline, KS distance, traffic light) is `data_pipeline/insidethegame_data/realism.py`; its results are in `reports/realism/` |

**How it works.**
1. **Script.** `synthetic/<id>/script.json` is validated against the line-ups (`script.py`).
2. **Possession chains.** Between the moments, a Markov model over pitch zones and actions (`events.py`) decides every touch, using the real positions of all players.
3. **Director.** `director.py` biases possession before each moment, then plays the scripted sequence with the same executors.
4. **Players.** They follow the mean position of their role given the ball and possession, plus receiving runs, pressing, runs in behind and the goalkeeper's behaviour. A speed- and acceleration-limited controller keeps a minimum separation between players (`players.py`).
5. **Ball.** It follows drag, gravity, bounce and post physics (`ball.py`).
6. **Outcomes after the simulation** (`outcomes.py`). A random draw only decides the intent (aim, how well a ball is struck, who goes for it). Kick events are created at the kick frame without a result; the engine logs what the ball does (touches with the player, goals, posts, balls out, fouls, offsides), and the result is read afterwards: a pass is `successfullyCompleted` only if a team-mate touches the ball next; a shot is `SuccessfulShot`, `SavedShot` (goalkeeper touch), `BlockedShot` (other defender), `ShotWoodWork` or `ShotWide` (over the goal line); a duel is won by the player whose team touches the ball next. A player touches the ball only within reach (receiver 1.6 m, blocker 1.8 m, goalkeeper 1.9 m): a pass nobody reaches runs on as a loose ball. Shots from a restart are nested as DFL: `FreeKick` with sub-type `ShotAtGoal/<result>`.
7. **Noise and times.** Positions get smooth measurement noise of about 0.1 m; event locations N(0, 0.8 m). Event times have **no delay**: `time_utc` is the time of the event's frame, the second-half kick-off included (`writer.py`).
8. **Contacts the pipeline can see** (`events.py`). A header exists only at a real contact at head height (player within 0.6 m of the ball, ball 1.5-2.7 m high); a cross nobody meets is a loose ball. The scripted post keeps everyone out of reach: nobody within 2 m of the ball around the contact, nobody on the rebound for 0.6 s. Around every kick the kicker is the nearest player (the pipeline's `R-TOUCH-NEAREST`): players in the way step aside before a pass, shot, clearance or cross, and nobody but the taker stands within 2 m of a restart spot.

Before writing, `api.py` checks these things and stops with an error if any fails:
- every moment is in its scripted minute, and the scripted goals, penalty, post and save really end that way in the simulation;
- the scripted corner header is a real contact at head height (`director.py`);
- the score is right;
- the scripted sprinter is the fastest player;
- no live ball enters a goal without a goal;
- every successful pass ends with a team-mate's touch within 2 m of the ball, every shot result matches the trajectory (goal-line crossing, goalkeeper or defender touch, post contact, ball over the goal line), every restart shot has a result;
- nothing is overwritten without `--overwrite`.

**Calibration.** Every file in `calibration/` is read when present (`calibration.py`). Missing or unrecognised files fall back to literature defaults, and the summary lists which parameters are calibrated:
- pace and acceleration scales;
- top speed and distance per role group;
- role position maps;
- pass success, pass length and shot rate by zone;
- shot outcomes;
- ball in play;
- ball speeds;
- xG.

Players' top speed and acceleration come from the universe's 1-99 `pace` and `acceleration` attributes through `attributes.physical_value` (the DFL table of the position group; CM = mean of DM and AM), since the event-time fix. Without attributes (an old universe) pace is estimated from `attribute_profile`. The wingers' table is flat between p75 and p90 (van Dalfsen and Peyrol both about 32.9 km/h): the director gives the scripted sprinter a sprint top speed above everyone else's, so no attribute needs changing. The pass intent sits 0.12 below the DFL success share of the zone, because the written result also counts contested and loose balls won back.

The output format, the story of `SYN-001`, the comparison with DFL and the limitations are in the data card [`synthetic/SYN-001/README.md`](../synthetic/SYN-001/README.md).

## Package layout (`insidethegame_gen/`)

| Module | Role |
|---|---|
| `schema.py` | Pydantic models of the universe and of the match information, English code lists and labels |
| `attributes.py` | player attributes 1-99: the seeded model, the story floors and the mapping to physical values (`physical_value`) |
| `scout.py` | the deterministic builder: league, names, squads, line-ups, top scorers, previews |
| `season.py` | squad drafts and the round-by-round season simulation |
| `careers.py` | careers, transfer fees, coaching history, player honours |
| `profiles.py` | nicknames, playing styles, curiosities, caps from the hand-written building blocks |
| `names.py` | the name picker with the surname rule and the blocklist |
| `games.py` | Tiki-Taka-Toe criteria, crossings, grid count and example grid |
| `validate.py` | the checks |
| `kitman_docs.py` | renders the Kit Man documents |
| `overview.py` | renders `synthetic/README.md` |
| `__main__.py` | command line: build, render, validate, write, report |
| `tickets/` | the statseat digital tickets: `codes.py` (format, check character, hashes), `registry.py` (tiers silver, gold, demo; seats, `tickets.json`, checks), `render.py` (SVG template to SVG, PDF and PNG), `readme.py` (the judges' README), `__main__.py` (command line) |
| `match/` | the match generator: `script.py` (script models and validation), `events.py` (engine, executors, Markov model, ball log), `outcomes.py` (results after the simulation and their checks), `director.py` (scripted moments), `players.py` (roster, role positions, controller, noise), `ball.py` (ball physics), `calibration.py` (defaults and optional DFL calibration), `pitch.py` (geometry, clock), `writer.py` (parquet and JSON), `report.py` (sanity statistics), `api.py` and `__main__.py` (functions and command line) |
| `content/` | hand-written content, name pools and the blocklist |
