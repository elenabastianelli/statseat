# Methods

How the data are made, how every number is computed, how the agents are kept honest, and what is not good enough yet. Each number below names the file it comes from.

## 1. The synthetic match

The challenge asks for synthetic match data. We did not write a CSV of events: we built a small football world and played a match in it.

- **The universe.** The invented country of **Meridia**: 44 clubs in two divisions (20 and 24), with squads, careers, coaches, officials, a stadium and its history, all in English and all original ([`generator/`](../generator/), output in [`synthetic/`](../synthetic/)). No real person, club or brand.
- **The match, SYN-001.** Brackwater Athletic v Calderholm United, "The Estuary Derby", Meridian League matchday 9, at the Lanternworks Stadium (60,400 seats): events in the DFL taxonomy and **player and ball tracking at 25 Hz** for the whole match ([`synthetic/SYN-001/`](../synthetic/SYN-001/)).
- **How it is generated.** Deterministic code with a fixed seed, in three roles: *Scout* builds the universe, *Tactician* writes the match script (the goals, the saved penalty, the key moments), and the simulation plays the rest: player positions from calibrated team shapes and attributes on a 1–99 scale, passes, shots and outcomes from calibrated rates, and the ball's flight with air drag, gravity and bounces ([`ball.py`](../generator/insidethegame_gen/match/ball.py)). *Linesman* then measures realism (section 2).
- **Calibration.** Every rate and distribution comes from **aggregate statistics** of six matches of the open DFL/Sportec dataset (Bassek et al. 2025, *Scientific Data* 12:195, CC-BY 4.0), stored in [`calibration/`](../calibration/). The raw data stay on the developer's machine: they are never in the repository, the app or the video, and real players are never named.
- **Pre-match data.** Line-ups, officials, stadium capacity, tickets sold, crests and kits are in [`match_info.json`](../synthetic/SYN-001/match_info.json); the 9 matchday documents that Kit Man reads are in [`synthetic/SYN-001/kitman/`](../synthetic/SYN-001/kitman/).

## 2. How realistic it is

We measure it instead of claiming it ([`reports/realism/`](../reports/realism/README.md), numbers in [`realism.json`](../reports/realism/realism.json)).

- **Same pipeline on both sides.** The six real matches and the synthetic ones (SYN-001 plus 6 replicas of the same fixture without the script) go through the same data preparation and the same metric code.
- **107 metrics** in six groups: physical by role, ball and game, passes, shots, events, team shape.
- **Test:** the two-sample Kolmogorov–Smirnov distance D against the real distribution, plus where the synthetic median falls in the range of the six real medians. Green if D ≤ 0.15 and the median is in range; yellow if D is between 0.15 and 0.3 or the median is just outside; red otherwise.
- **Result:** **21 green, 22 yellow, 64 red.** Shots are the most realistic group (8 green of 10). The physical group is the reddest: players accelerate in steps, 3–5 times more often than in the real data, and passes travel too slowly (about 34 km/h against 50–54). We publish the red lights because they tell the truth about the data the app runs on; fixing them needs a new movement model (see [ROADMAP.md](ROADMAP.md)).
- **Noise:** with six replicas, the count of red metrics moves by about ±11 by chance alone.

## 3. Data preparation

The same deterministic code runs on real and synthetic matches ([`DATA_ANALYSIS_GUIDELINES.md`](DATA_ANALYSIS_GUIDELINES.md), version 4.2). The main rules:

- **Any provider, one canonical schema.** Pitch, frame rate, coordinates and the edges of the tracked area are discovered for every match.
- **Our own kinematics.** Speeds and accelerations come from cleaned positions with one Savitzky–Golay filter; a provider's speeds are used only in tests.
- **Validity mask.** Every anomaly (a jump, an impossible speed, a gap) removes ±1 s around it. Gaps are filled only if the player reappears within 6 m. A distance with unmeasured time is shown with "≈", and every tracking number in the app carries a reliability symbol for the share of time actually measured.
- **Physical limits.** Players at most 38 km/h, the ball at most 211 km/h in live play ([`PHYSICAL_LIMITS.md`](../Materials/PHYSICAL_LIMITS.md)).
- **Events located from the trajectories.** A kick belongs to the player nearest the ball at the contact frame; a goal is confirmed only when the ball crosses between the posts. On the six real matches the pipeline finds every goal (16 of 16).
- **No look-ahead.** In the live app an event is never processed before its frames are released.

## 4. The metrics

Every metric shown in the app is defined in [`Materials/METRICS.md`](../Materials/METRICS.md): what it measures, how the literature defines it, how we compute it, how it appears in the app and how Archie explains it. All choices are recorded there. Some of them:

| Metric | Our definition |
|---|---|
| Distance, speed zones, sprints | From our smoothed speeds; goalkeepers out of team totals; FIFA speed zones (0–7, 7–15, 15–20, 20–25, >25 km/h) |
| Peak intensity | Rolling 5-minute window, 1 s step, per player; team = mean of the outfield players on the pitch |
| Ball circulation | Ball speed in possession, without the 2 s after shots and clearances, in km/h |
| Possession | Time-based, live play only |
| Shots on target | Goals + saves; woodwork counted apart; penalties included |
| Momentum | Who is pushing over the last 5 minutes, from −1 to +1 |
| PPDA, counter-attacks, defensive line, zone map | Analyst metrics, defined in `METRICS.md` |

### Expected goals (xG)

A logistic model on the shot's geometry at the located kick frame ([`calibration/xg_model.json`](../calibration/xg_model.json)):

```
logit(xG) = −3.19 + 2.84·θ − 0.91·n
```

- θ is the visible goal angle in radians and n the number of opponents in the triangle between the ball and the posts.
- The slope of the angle is fixed from a large published fit (Soccermatics, 8,451 shots, `logit(xG) = −3.52 + 2.84·θ`); the intercept and the opponents' coefficient are fitted on the 124 located open-play shots of the six real matches (13 goals; the model's xG sums to 13.0).
- Penalties: a fixed 0.79, the value reported for Opta's model.
- Words for the fan: xG ≥ 0.30 "big chance", 0.10–0.30 "decent chance", below 0.10 "half chance". No "lucky" or "clinical": the fan sees goals next to xG and decides.

### Offside check (for Archie only)

The statistics count only the referee's offside decisions. When a fan asks "was it offside?", The Analyst runs a tracking check at the frame of the last pass by a team-mate ([`app/engine/offside.py`](../app/engine/offside.py)):

```
margin = attacker x − max(second-last opponent x, ball x)
offside if margin > 2 m · onside if margin < −2 m · otherwise "too close to call with our data"
```

The 2 m band covers the body parts ahead of the single tracked point, the tracking error and half a frame. Archie never contradicts the referee: on the six real matches, of the 13 official offsides, 1 comes out "offside", 9 "too close to call", 0 "onside" and 3 cannot be checked.

### Key moments (VAR)

VAR is deterministic code ([`app/engine/highlights.py`](../app/engine/highlights.py)). It finds candidate moments from the released events and frames (goals, missed penalties, woodwork, big saves with xG ≥ 0.12, big chances missed with xG ≥ 0.20, near misses, red cards…), and scores each one:

```
score = base × minute factor × game-state factor + xG bonus
```

using only what was known at that instant: a goal at 88' that puts a team ahead is "a lead goal", never "the winner". Labels and "why it mattered" come from English templates. The timeline shows the moments above a threshold; the Recap keeps every goal and then the best moments, up to 10.

## 5. Keeping the agents honest

- **The tools decide, the model writes.** The Analyst can only read numbers through 12 deterministic tools ([`analyst_tools.py`](../app/agents/analyst_tools.py)). The tools take the fan's session from the server: they cannot read past the current minute, cannot change match, and return null, with the reason, for a number that the fan's ticket does not show.
- **Grounding validator** ([`grounding.py`](../app/agents/grounding.py)). It reads every number in the answer, in any language ("4,1 km", "4.1 km", "≈ 4 km", "#9", "4号"), and accepts it only if it matches a tool result, Kit Man's answer or the MATCH CONTEXT at the precision written. A number written as a shirt number is checked against the line-ups only. If a number does not match, Archie rewrites once; then the answer is replaced by a line with no number.
- **Instructions in the repository.** Archie's, The Analyst's, Kit Man's and The Ref's instructions are versioned in [`prompts/`](../prompts/) and created on Foundry by `python -m app.agents setup`.
- **Tests without the model.** Every automatic test runs against a scripted fake Foundry behind the real Agent Framework client: no network, no token ([`app/tests/`](../app/tests/)).
- **Live tests.** The agents were tested on Foundry with saved answers ([`prompts/archie/`](../prompts/archie/)). The last test of Archie (15 questions in Italian, English, Spanish and Chinese, Fan and Analyst, live and full time) followed the rules on **15 of 15**: every answer passed the grounding at the first attempt, no emoji, no invented number ([`foundry_test_AG5.md`](../prompts/archie/foundry_test_AG5.md)).

### Cost and latency (measured)

| What | Tokens | Cost | Time | Source |
|---|---|---|---|---|
| 15 questions to Archie | 175,234 in all | $0.018 in all | 2.4–8 s each; 12 s with Kit Man | [`foundry_test_AG5.md`](../prompts/archie/foundry_test_AG5.md) |
| A question with Kit Man | about 16,600 | about $0.0017 | 10–13 s | [`foundry_test_AG2_kitman.json`](../prompts/archie/foundry_test_AG2_kitman.json) |
| The Ref, one quiz in one language | about 3,600 | – | about 3 s, then from memory | [`app/agents/README.md`](../app/agents/README.md) |

All agents use one model, `gpt-6-luna`. Starting The Analyst in parallel with Archie cut a question with numbers from about 12 s to about 6 s.

## 6. Known limits

We list them because a judge, or a club, should know them.

- **Movement realism.** Players accelerate in steps and passes are slow (section 2). They can only be fixed together, with a new movement model.
- **One header** (50', saved) is located on the rebound, 2.24 s later, so its xG is 0.05 instead of about 0.21.
- **Shot speed.** A hard shot that goes out of the tracked area within about 8 frames can be measured too fast (122–130 km/h). It does not happen in SYN-001.
- **PPDA is low** (2.5–3.2 against 8–15 in the literature) because every duel of the defending team counts as a defensive action.
- **Acceleration load** is computed by the engine but not shown in the app, and Archie never uses it: the steps in the players' accelerations (section 2) make it unreliable.
- **Real data:** 6 of 144 shots are not found on the tracking.
- **VAR:** 4 rules have no example in SYN-001 (a foul that stops a dangerous break, a goal-line clearance, a red card, a goal ruled out for offside). They are tested only in the unit tests.
- **Demo architecture:** one replica with the state in memory, one match per deployment ([ARCHITECTURE.md](ARCHITECTURE.md)).
