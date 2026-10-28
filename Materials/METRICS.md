# Match metrics: what we compute and how

One file for every metric shown in the app (Live, Data & Statistics, Recap). Each metric says what it measures, how the literature defines it, how **we** compute it, how Archie explains it to a fan, and which choices we made.

**Why this file exists.**
- **Deterministic code.** The analyst computes every number deterministically (`docs/DATA_ANALYSIS_GUIDELINES.md`); the AI only explains.
- **Explainable.** When a fan asks Archie "how do you calculate that?", the answer comes from here.
- **Documented.** Judges look for documented, reproducible methods.

**General rules** (from [`docs/DATA_ANALYSIS_GUIDELINES.md`](../docs/DATA_ANALYSIS_GUIDELINES.md)):
- **Valid samples only.** Every metric uses only samples that pass the validity mask; distances follow the "≈" precision rule (section 10).
- **Located events.** Event-based metrics use the event location at the synchronised frame (section 9.3), not the provider's annotated coordinates.
- **Physical limits and speed zones** come from [`PHYSICAL_LIMITS.md`](PHYSICAL_LIMITS.md).
- **Same code for real and synthetic data.** It runs on real (DFL) and synthetic (SYN-001) data alike. Models that need fitting (for example xG) are fitted on the DFL calibration matches only, never on synthetic data. Values that the synthetic generator writes into its own events (for example an `xG` attribute) are never used by the app.

## How the metrics appear in the app

Decided on 25 October 2026.

- **Two versions.** **Fan** (silver ticket) and **Analyst** (gold ticket). In Fan, the Analyst-only metrics are shown **blurred**, as a preview with "Unlock with Gold".
- **Live** (during the two halves only): the event feed plus, per team, **goals, shots, shots on target, penalties, yellow and red cards**. Nothing else.
- **Data & Statistics** (from kick-off to the end, both versions): **all** the metrics, **tables at the top, charts below**.
- **Favourite players.** Up to **3**, chosen once and never changed. Each gets a Player Focus card. **Fan:** minutes played, distance (≈), top speed with the minute, sprints, acceleration load, passes (number and accuracy), shots and goals, touches, tackles and recoveries, fouls committed and won, cards. **Analyst:** also xG, the speed zones, the zone map and the acceleration load every 5 minutes.
- **Reliability symbol.** In the app each tracking-based number carries a symbol for the share of time actually measured (tracking gaps); the explanation is in the Inspector. The "≈" of distances stays.
- **One chart over time.** Every per-team series over time (peak intensity, acceleration load, ball circulation, momentum, defensive line) uses the **same mirror chart**: the home team above the axis, the away team below, so the fan sees at a glance which side is higher at each moment. Two complementary values (possession, which sums to 100 %) use a single bar instead. Momentum is a single signed value, drawn as an area above or below the same axis.
- **Removed from the project:** the stadium roar (§18), the pass network (§16), pressure on the ball carrier (§13) and polls.

## Decisions

All open choices were closed on 25 October 2026. Earlier: possession is time-based.

| # | Choice | Decision |
|---|---|---|
| 1 | Goalkeepers in team totals (§1, §2, §19, §22) | Excluded from team totals, shown as individual players |
| 2 | Substitutes (§1, §22) | Real values with the minutes played beside them; no "per 90 minutes" |
| 3 | Top speed (§3) | Maximum of our smoothed speed, no minimum duration |
| 4 | Peak intensity (§4) | Rolling 5-minute window, 1 s step, per player; team = mean of the outfield players on the pitch |
| 5 | Shot speed (§5) | Headers excluded |
| 6 | Ball circulation (§6) | Samples in the 2 s after shots and clearances excluded; no minimum possession length |
| 7 | Possession (§7) | Time-based, live play only; one normalised bar in both versions |
| 8 | Shots on target (§8) | Goals + saves. Cleared off the line = blocked. Woodwork counted apart (§23). Penalties count in shots, shots on target and goals |
| 9 | xG model (§9) | Model updating: fixed visible-angle slope from a large published fit, intercept and opponents in the shot triangle fitted on the DFL shots |
| 10 | Penalty xG (§9) | Fixed 0.79 |
| 11 | Chance words (§9) | xG ≥ 0.30 "big chance", 0.10–0.30 "decent chance", < 0.10 "half chance" (our thresholds) |
| 12 | Goals vs xG (§10) | Numbers only, no "lucky" or "clinical" words; team level |
| 13 | Pass length (§11) | Successful passes only; long ball ≥ 32 m; no histogram |
| 14 | Effective playing time (§12) | Per half and total, with the World Cup 2022 and Bundesliga references |
| 15 | Momentum (§13) | 5-minute window, weights 0.5/0.5, no xT |
| 16 | Pressure on the ball carrier (§13) | Removed (medium cost, low priority) |
| 17 | PPDA (§13) | Kept, Analyst, one number per team |
| 18 | Shot map (§14) | Located positions; filled = goal, hollow = no goal (blocked shots included), × = woodwork |
| 19 | Zone map (§15) | σ = 3 m, live play only, always attacking left to right; Analyst, per team and favourite players |
| 20 | Defensive line (§17) | Mean of the 2 deepest outfield players; team length after dropping one player at each end |
| 21 | Sprints (§19) | Speed ≥ 25 km/h for ≥ 1 s; two sprints less than 1 s apart count as one |
| 22 | Fouls won (§20) | Shown for the favourite players |
| 23 | Ball recoveries (§21) | From events, checked against the possession column |
| 24 | Acceleration load (§22) | ∫ \|dv/dt\| dt (change of speed), as in the literature |
| 25 | Most touches (§24) | From events (each event in which the player plays the ball), as Opta |
| 26 | Counter-attacks (§25) | Ball won in own half, then a shot by the same team within 15 s without losing the ball |
| 27 | Offsides (§26) | Statistics: official offside events only. Tracking check for Archie only, with a 2 m uncertainty band |

## Status

The list of metrics is the team's choice of 23 October, revised on 25 October.

| # | Metric | Fan | Analyst | Status |
|---|---|---|---|---|
| 1 | Distance covered (with splits) | ✔ | ✔ | [written](#1-distance-covered) |
| 2 | Distance per speed zone (FIFA zones) | blurred | ✔ | [written](#2-distance-per-speed-zone-fifa-zones) |
| 3 | Top speed | ✔ | ✔ | [written](#3-top-speed) |
| 4 | Peak intensity ("most intense 5 minutes") | ✔ | ✔ | [written](#4-peak-intensity-most-intense-5-minutes) |
| 5 | Shot speed | ✔ | ✔ | [written](#5-shot-speed) |
| 6 | Ball circulation speed in possession | ✔ | ✔ | [written](#6-ball-circulation-speed-in-possession) |
| 7 | Possession | ✔ | ✔ | [written](#7-possession) |
| 8 | Shots and shots on target | ✔ | ✔ | [written](#8-shots-and-shots-on-target) |
| 9 | Expected goals (xG) and cumulative xG | blurred | ✔ | [written](#9-expected-goals-xg) |
| 10 | Goals vs xG | ✔ | ✔ | [written](#10-goals-vs-xg) |
| 11 | Passes: number, accuracy, length | ✔ | ✔ | [written](#11-passes-number-accuracy-length) |
| 12 | Effective playing time | ✔ | ✔ | [written](#12-effective-playing-time) |
| 13 | Momentum ("who is pushing") and PPDA | ✔ (PPDA blurred) | ✔ | [written](#13-momentum-who-is-pushing-and-ppda) |
| 14 | Shot map | ✔ | ✔ | [written](#14-shot-map) |
| 15 | Zone map (heatmap) | blurred | ✔ | [written](#15-zone-map-heatmap) |
| 16 | ~~Pass network~~ | | | [removed](#16-pass-network-removed) |
| 17 | Defensive line and team length | blurred | ✔ | [written](#17-defensive-line-and-team-length) |
| 18 | ~~Stadium roar~~ | | | [removed](#18-stadium-roar-removed) |
| 19 | Number of sprints | ✔ | ✔ | [written](#19-number-of-sprints) |
| 20 | Corners, fouls, cards | ✔ | ✔ | [written](#20-corners-fouls-cards) |
| 21 | Tackles and ball recoveries | ✔ | ✔ | [written](#21-tackles-and-ball-recoveries) |
| 22 | **Acceleration load** | ✔ | ✔ | [new](#22-acceleration-load) |
| 23 | **Woodwork** | ✔ | ✔ | [new](#23-woodwork) |
| 24 | **Most touches** | ✔ | ✔ | [new](#24-most-touches) |
| 25 | **Counter-attacks** | ✔ | ✔ | [new](#25-counter-attacks) |
| 26 | **Offsides** | ✔ | ✔ | [new](#26-offsides) |

"✔" in Fan can still mean a simpler view than Analyst: each section says what each version shows, under *In the app*.

**How to add a metric:** copy the six headings of a section below (*What it measures*, *Definitions in the literature*, *Our definition*, *In the app*, *How Archie explains it*, *Choices*). Cite every source with a link, and add it to [`RESOURCES.md`](RESOURCES.md).

---

## 1. Distance covered

### What it measures

How far a player (and, summed, a team) has run, in km. It is the most familiar physical number for fans.

### Definitions in the literature

- **FIFA post-match reports** (World Cup 2022, TRACAB Gen5 optical tracking at 25 Hz): distance over the whole match, regular time plus added time. Team totals sum all outfield players who played, substitutes included, goalkeepers excluded. Distances are also split **in possession / out of possession** using FIFA's phases of play ([FIFA Training Centre, method](https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/background-and-method.php); [distances](https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/what-distances-did-teams-cover.php)).
- **Ball out of play counts.** In a FIFA analysis, a wide midfielder covered 10,902 m in total, 3,413 m of them while the ball was out of play (see [`PHYSICAL_LIMITS.md`](PHYSICAL_LIMITS.md) §5). Published distances therefore include stoppages.
- **Accuracy:** TRACAB position error is 0.06–0.18 m and speed error 0.03–0.09 m/s (Linke, Link & Lames 2020), small enough for distance.

### Our definition

Already fixed in the guidelines (§10.1, §10.2):

```
distance = Σ (valid speed × ΔT) + estimated gap segments        over the whole time on the pitch
```

- **Splits:** by half; ball in play / out of play (provider's ball status, never derived); in possession / out of possession (provider's possession column).
- **Precision:** the "≈" rule of §10.2, from the share of time not measured.
- **Team:** sum of the outfield players, substitutes included, **goalkeepers excluded** (as FIFA does). Goalkeepers appear only as individual players.
- **Substitutes:** the real distance with the minutes played beside it, never "per 90 minutes": scaled to 90 minutes, ten minutes of play would give inflated numbers.
- **Data needed:** player positions and speeds, validity mask, ball status, possession.

### In the app

- **Fan:** team total split into ball in play and ball out of play; who ran the most; the 3 favourite players.
- **Analyst:** also in possession / out of possession, per player and per half.

### How Archie explains it

> "We add up every metre each player runs, 25 times a second, from the moment he steps on the pitch, including when the ball is out of play, like FIFA does. Ulvestad has run 10.4 km so far."

### Choices

- **Goalkeepers:** excluded from the team total, shown individually.
- **Substitutes:** real distance with minutes played, no "per 90".

---

## 2. Distance per speed zone (FIFA zones)

### What it measures

How a player's distance splits between walking, jogging, running, high-speed running and sprinting: the *quality* of the running, not only the quantity.

### Definitions in the literature

- **FIFA zones** (World Cups 2018 and 2022): 0–7, 7–15, 15–20, 20–25 and > 25 km/h. **High intensity** is > 20 km/h and **sprinting** > 25 km/h ([FIFA Training Centre](https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/what-distances-did-teams-cover.php)).
- **No consensus on thresholds.** The systematic review by Gualtieri et al. (2023) finds no agreed absolute thresholds for high-speed running and sprinting in adult players. Sprint thresholds are typically 25–25.2 km/h (Varley et al. 2017: 7.0 m/s = 25.2 km/h).
- Zones are applied to **instantaneous speed**: with optical tracking at 25 Hz, each sample falls into one zone.

### Our definition

```
distance_zone_k = Σ over valid samples with speed in zone k of (speed × ΔT)
zones: [0,7) [7,15) [15,20) [20,25) [25,∞) km/h
```

- **At each sample** (25 per second) we take our own speed, computed from the cleaned positions; the stretch to the next sample goes into the zone of that speed.
- **Exactly 25.0 km/h** falls in the last zone and counts towards a sprint (§19 uses "≥ 25").
- **Team:** sum of the outfield players.
- **Estimated gap segments** are not assigned to a zone, because their speed is not measured. They appear as "unassigned" in the Inspector mode.

### In the app

- **Fan:** blurred. The number of sprints (§19) replaces it.
- **Analyst:** stacked bars per team; per player for the favourite players.

### How Archie explains it

> "We split each player's running by speed, using FIFA's five zones: walking up to 7 km/h, jogging to 15, running to 20, high intensity to 25, and sprinting above that."

### Choices

- **Unassigned estimated distance:** shown only in the Inspector mode.

---

## 3. Top speed

### What it measures

The highest running speed reached by a player, in km/h, with the moment it happened.

### Definitions in the literature

- **Bundesliga "Speed check"** (DFL, positional data at 25 frames per second): a player's top speed is his fastest measured single frame ([Amazon / Bundesliga Match Facts](https://www.aboutamazon.eu/news/amazon-web-services/new-bundesliga-match-fact-will-showcase-the-speed-on-the-pitch)).
- **Premier League** (Opta tracking) publishes top speeds in the same way; the record is 37.38 km/h (see [`PHYSICAL_LIMITS.md`](PHYSICAL_LIMITS.md) §1).
- **Single-frame peaks are sensitive to noise.** Linke et al. (2020) show that tracking errors grow at high speed. This is why research often smooths the speed or requires a minimum duration.

### Our definition

Already fixed in the guidelines (§10.3):

```
top_speed = max(our smoothed speed)        over samples with valid_kinematics = true (never estimated samples)
```

- **Speed** is our own smoothed speed (guidelines §7), not the provider's, so noise spikes are already damped. Samples above 38 km/h are outliers and are excluded by the validity mask.
- **No minimum duration:** with the v4 guidelines, the mask and the filter already remove false peaks.
- **Reported with** the match-clock time of the peak, linked to the timeline.

### In the app

- **Fan:** the fastest player of the match and the fastest of each team, with the minute linked to the timeline.
- **Analyst:** the 5 fastest players of each team.

### How Archie explains it

> "It's the fastest speed we measured for him, after smoothing out the camera noise. Peyrol hit 34.1 km/h in the 63rd minute, on that counter-attack."

### Choices

- **Minimum duration:** none; we trust the smoothing and the validity mask.

---

## 4. Peak intensity ("most intense 5 minutes")

### What it measures

The hardest spell of running in the match: the highest distance per minute over a short moving window. It tells the fan *when* the game was at its most intense.

### Definitions in the literature

- **Rolling windows, not fixed blocks.** Fixed blocks (0–5, 5–10 minutes…) miss the true peak, because the most intense spell rarely starts on a block boundary:
  - Varley, Elias & Aughey (2012) found that predefined windows underestimated peak high-velocity running by up to 25 % compared with rolling windows ([IJSPP 7(2):183–185](https://vuir.vu.edu.au/9091/));
  - Fereday et al. (2020) found that fixed blocks underestimate the peak by about 7–10 % for total distance and 12–25 % for high-speed distance ([Fereday et al. 2020](https://doi.org/10.1016/j.jsams.2020.01.002)).
- **Window lengths:** Delaney et al. (2018) computed peak values over **moving averages of 1 to 10 minutes** in professional soccer ([*Science and Medicine in Football* 2(2):86–92](https://doi.org/10.1080/24733938.2017.1383623)). Peaks of different lengths often fall at different times; peak total distance tends to come early in each half ([Biology of Sport, 2022](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9536360/)).

### Our definition

```
intensity_player(t) = distance covered by the player in [t − 5 min, t] / 5        m/min, rolling, step 1 s (tracking clock)
intensity_team(t)   = mean of intensity_player(t) over the outfield players on the pitch (goalkeepers excluded)
peak = max over t of intensity(t)
```

- **Player level first:** each player's metres per minute over the last 5 minutes; a substitute counts from his entry.
- **Team level:** the **mean** of its outfield players on the pitch, so a substitution does not change the scale.
- **Ball out of play is included** (same convention as distance). The window uses the tracking clock, so the half-time break is never inside a window.

### In the app

- **Fan:** the mirror chart of metres per minute over time, with each team's peak marked.
- **Analyst:** the same chart, plus the table of each player's peak.

### How Archie explains it

> "We slide a five-minute window along the match and look for the stretch where the team ran the most metres per minute. Brackwater's peak came just after their equaliser."

### Choices

- **Window:** rolling 5 minutes, step 1 s.
- **Team:** mean of the outfield players, goalkeepers excluded.

---

## 5. Shot speed

### What it measures

How fast the ball left the foot on a shot, in km/h. The fan's "what a strike!" number.

### Definitions in the literature

- **Bundesliga Match Facts "Shot Speed"** (DFL and AWS, 2023): measured from the ball's positional data at the beginning of the shot, when the ball reaches its highest speed ([bundesliga.com](https://www.bundesliga.com/en/bundesliga/news/new-match-fact-aws-shot-speed-stat-25227); [AWS blog](https://aws.amazon.com/blogs/machine-learning/bundesliga-match-facts-shot-speed-who-fires-the-hardest-shots-in-the-bundesliga)).
- **Biomechanics:** maximal instep kicks reach 18–35 m/s (65–126 km/h) (Kellis & Katis 2007; see [`PHYSICAL_LIMITS.md`](PHYSICAL_LIMITS.md) §3).

### Our definition

Already fixed in the guidelines (§10.4):

```
shot_speed = max(ball speed) over valid ball samples in [sync_frame, sync_frame + 0.5 s]        of a confirmed shot
```

- **Valid ball samples only** (ball outlier limit 211 km/h, live play only).
- **Headers excluded** (`TypeOfShot = head`, present in both DFL and synthetic events): a header's speed says nothing about the strike.

### In the app

- **Fan:** the hardest shot of the match and of each team, with the shooter and the minute.
- **Analyst:** the table of shots with their speed.

### How Archie explains it

> "We measure the ball's speed from the tracking in the half-second after it's struck and take the peak. Ulvestad's shot left his boot at 112 km/h."

### Choices

- **Headers:** excluded.

---

## 6. Ball circulation speed in possession

### What it measures

How quickly a team moves the ball while it has it: a measure of tempo. Slow, patient circulation and fast, direct play look very different here, even with the same possession percentage.

### Definitions in the literature

- **Event-based pace.** KU Leuven's pace-of-play analysis measures how fast the ball moves between players. It counts only actions that move the ball from one player to another (passes, crosses, shots) and leaves out carries and take-ons ([DTAI Sports](https://dtai.cs.kuleuven.be/sports/blog/%22aannemen-kijken-spelen%22:-analyzing-player-pace-of-play-in-soccer)). Its stated limit: event data do not show how long the ball takes to reach the receiver.
- **Pace within possessions** (*Journal of Sports Analytics*, [JSA-200581](https://sage.cnpereading.com/doi/10.3233/JSA-200581)): short possessions do not give a reliable pace, so only possessions with three or more passes are used; pace also depends on the area of the pitch.
- **Rhythm of ball transmission** (Spain at the 2010 World Cup): faster circulation went with shorter attacking sequences ([UFV repository](https://locus.ufv.br/handle/123456789/34674)).
- **No standard provider metric** exists. Tracking data let us measure the ball itself instead of inferring its speed from events.

### Our definition

Already fixed in the guidelines (§10.4):

```
circulation(team, window) = median of ball speed over valid ball samples
                            with ball_status = live and possession = team,
                            excluding the 2 s after each shot and each clearance
```

- **Exclusion:** the samples in the 2 s after a shot or a clearance are left out, so a single hard shot or a long clearance does not inflate the tempo.
- **No minimum possession length:** with the ball itself measured 25 times a second, short possessions do not distort the median.
- **Reliability symbol** from the share of valid ball samples.
- **Distribution** (quartiles) kept for the Inspector mode.

### In the app

- **Fan:** one number per team (km/h) with the reliability symbol, and one sentence ("Brackwater move the ball faster").
- **Analyst:** also the 5-minute values over time, in the mirror chart.

### How Archie explains it

> "It's how fast the ball travels while a team has it. We measure the ball itself, 25 times a second, and take the typical value, leaving out the moments right after shots and clearances. Calderholm's ball moves at 19 km/h, Brackwater's at 23: they're playing quicker."

### Choices

- **Shots and clearances:** excluded for 2 s.
- **Minimum possession length:** none.

---

## 7. Possession

### What it measures

The share of the match each team had the ball, in %.

### Definitions in the literature

- **Pass-based (event data).** Some providers estimate possession from the share of passes each team makes, not from time on the ball. It is a proxy, and it can differ from time-based figures.
- **Time-based (tracking data).** Link & Hoernig (2017) detect individual and team ball possession from positional data (player–ball distance, direction of motion, speed, ball acceleration). Time when no team controls the ball is a separate category ([Link & Hoernig 2017, *PLoS ONE*](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5503225/)).
- **The DFL data** include a possession flag for every frame, from the provider.

### Our definition

```
possession_home = live time with possession = home / live time with possession = home or away
```

- **Source:** the provider's possession column (DFL; always present in our synthetic data).
- **Live play only**: stoppages are excluded (the engine counts only samples with the ball in play).

### In the app

- **Fan and Analyst:** the same single bar, normalised to 100 %, with both percentages.

### How Archie explains it

> "It's the share of the time the ball was in play that each team had it. Stoppages don't count. Brackwater have had it 58 % of the time. TV figures can differ a little, because some providers count passes instead of time."

### Choices

- **Time-based**, live play only.
- **No per-5-minute chart:** the single bar is enough.

---

## 8. Shots and shots on target

### What it measures

How many times each team tried to score, and how many of those attempts would have gone in without the goalkeeper.

### Definitions in the literature

- **Opta:** a **shot** is any clear attempt to score. A **shot on target** goes in, or would have gone in without a save by the goalkeeper or a block by a last-line defender. Shots hitting the woodwork and blocked shots are **not** on target ([Opta Event Definitions](https://statsperform.com/opta-event-definitions); [The Analyst, Opta stats definitions](https://theanalyst.com/articles/opta-football-stats-definitions)).
- **Opta pages disagree** on a ball cleared off the line: one calls it a blocked shot, the more recent definitions call it a shot on target.
- **DFL event data** record every shot with its outcome (`SuccessfulShot`, `SavedShot`, `BlockedShot`, `ShotWide`, `ShotWoodWork`, `OtherShot`).

### Our definition

- **Shots:** the provider's shot events (`ShotAtGoal`), own goals excluded. Their location and speed come from the located frame (guidelines §9.3).
- **On target = goals + saves.** A ball **cleared off the line** by a defender counts as **blocked**, consistent with the guidelines. **Woodwork** is never on target and is counted apart (§23).
- **Penalties** count in shots, shots on target and goals, as Opta does; their xG is a fixed value (§9).
- **Data needed:** shot events with their outcome.

### In the app

- **Live:** goals, shots, shots on target and penalties per team.
- **Fan:** the same counts per team in Data & Statistics.
- **Analyst:** also per player (not per half).

### How Archie explains it

> "A shot is any real attempt at goal. It's 'on target' if it went in or the keeper had to save it. Hitting the post doesn't count, and neither does a block."

### Choices

- **Cleared off the line:** blocked.
- **Penalties:** included in the counts.

---

## 9. Expected goals (xG)

### What it measures

The probability that a shot becomes a goal, from 0 to 1, given the situation in which it was taken. Summed over a team's shots it gives the **xG** of the team; plotted over time it gives the **cumulative xG**, which shows whether the scoreline reflects the chances.

### Definitions in the literature

**Event-based models** (shot location only):
- **Pollard & Reep (1997)** used logistic regression on shots from the 1986 World Cup. Their candidate variables included distance from the goal, angle, the distance to the nearest defender and whether the move started from a set piece. Later work by Pollard, Ensum & Taylor on the 2002 World Cup (930 shots) kept distance, angle and the space from the nearest defender among the significant factors ([summary](https://en.wikipedia.org/wiki/Expected_goals)). This is the origin of today's xG models, and of our three features.
- **Robberechts & Davis (2020)** compared xG models and how much data each needs. Their basic features are:
  - the shot's x and y;
  - the distance to the centre of the goal;
  - the **angle to the goal, measured as the width of goal mouth visible to the shooter**;
  - the body part (head or foot).

  Main finding: the more complex the learner and the more features, the more data it needs (up to five seasons for gradient-boosted trees). Their experiments use open-play shots only (no penalties, no direct free kicks).
- **Soccermatics** (Sumpter) fits logistic xG models on **8,451 shots** of the 2017/18 Premier League from the open Wyscout data, with **the same visible-angle formula as ours**. The angle-only model is `logit(xG) = −3.52 + 2.84·θ`; in its larger model the angle keeps a positive coefficient alongside the distance ([Soccermatics, fitting the xG model](https://soccermatics.readthedocs.io/en/latest/gallery/lesson2/plot_xGModelFit.html)). The open Wyscout data are described by Pappalardo et al. (2019) and released under CC BY 4.0 ([*Scientific Data* 6:236](https://doi.org/10.1038/s41597-019-0247-7)).

**Tracking-based models** (positions of all players at the moment of the shot):
- **Lucey et al. (2015)**, about 10,000 shots from a season of tracking data. Besides location, **defender proximity**, the interaction of the surrounding players, the speed of play and the game phase all change the chance of scoring.
- **Anzer & Bauer (2021)**, 105,627 Bundesliga shots with synchronised positional and event data (the same provider family as our DFL calibration data).
  - **Features:** shot location (distance and angle), speed of the shooter, **defenders in the line of the shot**, **goalkeeper position**, **pressure on the shooter**, type of shot, ball control, free kick. Headers and direct free kicks have their own sub-models.
  - **Most important** (SHAP analysis): distance and angle, then the goalkeeper's distance to goal, then the maximum individual pressure on the shooter.
  - **Defenders in the line of the shot** lower xG roughly linearly from zero to four defenders, then the effect levels off.
- **Mead, O'Hare & McMenemy (2023)** add player and team ability and use machine-learning models over many seasons ([*PLOS ONE*](https://doi.org/10.1371/journal.pone.0282295)). Out of reach for one match of data, and a roadmap idea.

**Penalties.** Providers give every penalty the same fixed value, from historical conversion rates: **0.79** for Opta, as reported by FBref ([FBref, xG explained](https://aws.fbref.com/en/expected-goals-model-explained)); 0.78 in the 2022 update of the Hudl StatsBomb model ([Hudl](https://www.hudl.com/blog/expected-goals-xg-explained)).

**How many goals a fit needs.** For logistic regression, fewer than about **10 events per estimated variable** give biased coefficients (Peduzzi et al. 1996, [*J Clin Epidemiol* 49(12):1373–1379](https://doi.org/10.1016/S0895-4356(96)00236-3)). Our six DFL matches have **126 located open-play shots and 13 goals**: enough for one or two parameters, not three.

### Our definition

**Why the first fit was replaced.** The first model fitted all three slopes (distance, visible angle, opponents in the triangle) on the 126 DFL shots. With 13 goals and two strongly related features (distance and angle), the fit gave the **visible angle a negative coefficient** (−0.95): the more goal the shooter could see, the lower the xG. A shot from 6 m in front of goal came out at 0.17, and no shot was above 0.35. Its leave-one-match-out log-loss (0.332) was barely better than a constant rate (0.341).

**Model (model updating):** a published shape for the geometry, recalibrated on our data.

```
logit(xG) = a + 2.84·θ + b·n
```

| Symbol | Feature | How it is computed | Where its value comes from |
|---|---|---|---|
| `θ` | Visible goal angle (rad) | Angle between the lines from the ball to the two posts: `θ = atan(w·x / (x² + y² − (w/2)²))`, plus π if the denominator is negative. `x` is the distance from the goal line, `y` the lateral distance from the goal centre, `w` = 7.32 m | Slope **2.84, fixed**, from the Soccermatics angle-only fit (8,451 shots) |
| `n` | Opponents in the shot triangle | Opposing **outfield** players inside the triangle formed by the ball and the two posts (valid positions only) | Slope `b` **fitted on the DFL shots** (only tracking data have it) |
| `a` | Intercept | – | **Fitted on the DFL shots** |

- **Why the angle alone:** it already contains the distance (from the centre: 1.09 rad at 6 m, 0.64 rad at 11 m, 0.29 rad at 25 m) and also the side. A shot from 6 m on the byline sees little goal, and the angle knows it; the distance alone would rate it like a central shot. Its known limit: long-range shots come out slightly high.
- **Fitting:** `a` and `b` on the open-play shots of the six DFL calibration matches (two parameters on 13 goals), with the angle term as a fixed offset. Saved with the model: the coefficients, the number of shots and goals, and a calibration table (predicted vs observed goals) in `calibration/xg_model.json`.
- **Checks after fitting:** the signs (`b` < 0), the calibration table, and a few examples (6 m central, penalty spot in open play, edge of the box, a tight angle).
- **Penalties:** fixed **0.79**, counted in the team's xG.
- **Headers, goalkeeper position, direct free kicks:** no feature of their own (too few goals). Direct free kicks use the same model. This is a stated limit.
- **Synthetic matches:** the generator uses the same `xg_model.json` to decide which shots score, so the generator must be re-run after the model changes. The `xG` attribute that the generator writes into its events is never used by the app.
- **Data needed:** the synchronised shot frame, ball and player positions, the shooting team, the attacked goal.

**Chance words** (our thresholds, used by Archie and the Recap):
- xG ≥ 0.30: "big chance";
- 0.10–0.30: "decent chance";
- below 0.10: "half chance".

**Possible improvement (roadmap):** fit both distance and angle on the open Wyscout data (about 1,900 matches), then recalibrate `a` and `b` on the DFL shots.

### In the app

- **Fan:** blurred, except the "Goals vs xG" row (§10).
- **Analyst:** xG per team and per shot, the cumulative xG (step chart) and the shot map with the dot size equal to xG.

### How Archie explains it

> "xG is the chance a shot had of going in, judged by how much of the goal the shooter could see and how many defenders were in the way. An xG of 0.30 means that, from that spot, about 3 shots in 10 are goals."

### Choices

- **Model:** visible angle (fixed slope from a large public fit) and opponents in the triangle, recalibrated on DFL.
- **Penalties:** fixed 0.79.
- **No extra features** (goalkeeper, headers) with 13 goals.

---

## 10. Goals vs xG

### What it measures

The goals a team scored next to the xG of its chances, that is, how many goals those shots were "worth". It is the simplest way to show a fan whether the scoreline reflects the chances.

### Definitions in the literature

- **Goals minus xG** (often called "finishing over expected", G − xG) is the standard way to read xG against the scoreline ([The Analyst, Opta stats definitions](https://theanalyst.com/articles/opta-football-stats-definitions); [Footovision](https://www.footovision.com/what-are-expected-goals-xG)).
- **One match is noise.** xG models describe averages over many shots. Robberechts & Davis (2020) show how much data is needed even to fit them, and a single-match difference mixes finishing skill, goalkeeping and luck.

### Our definition

```
goals(team)   (own goals excluded)        next to        xG(team) = Σ xG of the team's shots (§9)
```

- **Numbers only.** Over one match we cannot tell skill from luck, so the app shows **no judgement words** ("clinical", "lucky", "wasteful") and no threshold.
- **Team level only.** For favourite players, the Analyst version shows their own xG.

### In the app

- **Fan:** one row per team, for example "Goals 2 · xG 0.9".
- **Analyst:** the same row, next to the xG per shot and the cumulative xG.

### How Archie explains it

> "Brackwater's chances were worth about 0.9 goals, and they scored 2. Over a single match you can't say whether that's sharp finishing or luck: you'd need many games to tell."

### Choices

- **No judgement words**, no threshold; team level.

---

## 11. Passes: number, accuracy, length

### What it measures

How many passes a team (or player) attempted, how many reached a teammate, and how long the successful ones were.

### Definitions in the literature

- **Opta:** a pass is an attempted delivery of the ball from one player to a teammate. Crosses, goalkeeper throws and throw-ins are counted separately ([Opta Event Definitions](https://optaplayerstats.statsperform.com/en_GB/opta-event-definitions)). **Pass accuracy** is successful passes over attempted passes.
- **DFL event data** record passes and crosses with the player, the recipient and the outcome (successful or not).
- **Length:** providers usually report "long balls" above a fixed length rather than a distribution; for Opta a long ball is a pass of more than 35 yards, about 32 m ([Sports Illustrated, 2018](https://www.si.com/soccer/2018/11/23/surprising-statistics-reveal-chelsea-and-manchester-city-best-long-ball-premier-league-sides)).

### Our definition

- **Passes:** DFL `Play` events that are passes (crosses counted apart, as Opta); throw-ins, goal kicks and corners counted apart as set pieces.
- **Accuracy:** successful / attempted, per team and per player.
- **Length of successful passes only:** distance between the **located** start (ball at `sync_frame`, guidelines §9.3) and the ball at the frame where the teammate receives it. The pipeline already computes it (`pass_length_m`). Unsuccessful passes have no length in the app.
- **Long balls:** successful passes of **32 m or more** (Opta's 35 yards).

### In the app

- **Fan:** number and accuracy per team; who made the most passes.
- **Analyst:** also the average length, the number of long balls and the per-player table. No histogram.

### How Archie explains it

> "We count every pass from the match events, and check whether it reached a teammate. For the length we use the tracking: where the ball was kicked and where the teammate picked it up. Anything over 32 metres is a long ball."

### Choices

- **Length:** successful passes only.
- **Long ball:** ≥ 32 m. The calibration of the generator still uses 30 m and moves to 32 m at its next run.

---

## 12. Effective playing time

### What it measures

How many minutes the ball was actually in play. Fans are often surprised by how low it is.

### Definitions in the literature

- **Siegle & Lames (2012)**, 16 Bundesliga matches: about 108 interruptions per match, and the match was stopped for 38 % of the time; throw-ins and free kicks are the most frequent stoppages ([*Journal of Sports Sciences* 30(7)](https://doi.org/10.1080/02640414.2012.667877)).
- **FIFA World Cup 2022:** with the instruction to add the time lost, average effective playing time rose to about 59 minutes, with matches between 55 and 67 minutes (Pierluigi Collina, reported by the Associated Press, 30 November 2022: [mynorthwest.com](https://sports.mynorthwest.com/other-sports/world-cup-fans-see-more-active-play-from-stoppages-directive)).

### Our definition

Already fixed in the guidelines (§10.4):

```
effective_time = Σ ΔT over frames with ball_status = live        (provider's ball status, never derived)
```

- **Per half and per match**, also as a % of the total time played.
- **If the provider gives no ball status,** the metric is not reported.

### In the app

- **Fan and Analyst:** a table row, "The ball was in play for 56 minutes out of 96", per half and in total, with the two references (World Cup 2022 about 59 minutes; Bundesliga about 62 % of the time in play).

### How Archie explains it

> "We add up only the time the ball was actually in play, using the official ball-in-play signal. Out of 96 minutes, the ball was alive for 56: a little below the World Cup 2022 average of about 59."

### Choices

- **References:** World Cup 2022 and Bundesliga, both shown.

---

## 13. Momentum ("who is pushing") and PPDA

### What it measures

- **Momentum**: which team is on top **right now**, over a short recent window. It answers the fan's question "who is pushing?" better than possession, because keeping the ball far from goal is not pushing.
- **PPDA (passes per defensive action)**: how hard a team presses, over the match.

### Definitions in the literature

**Pressing intensity (event-based):**
- **PPDA** (Colin Trainor; described by Stats Perform and the Premier League): the opponent's passes in the defending team's attacking 60 % of the pitch, divided by the defending team's defensive actions there (tackles, interceptions, challenges, fouls). **Lower PPDA = more intense pressing** ([Stats Perform](https://www.statsperform.com/resource/how-we-measure-pressure/); [Premier League](https://www.premierleague.com/news/4250153)).

**Territory and threat (team level, over time):**
- **Field tilt:** a team's share of the touches or passes in the final third. Providers differ on touches vs passes. It filters out "safe" possession in deep areas ([glossary](https://www.sportmonks.com/glossary/key-data-points)).
- **Possession value and expected threat:**
  - **Opta "Match Momentum"**, as explained by Flashscore: every on-ball action gets a value for how much it changes the chance of scoring; the values are summed per minute and compared between the two teams ([Flashscore](https://www.flashscore.com/news/momentum/W82o7eCa/)).
  - **Expected Threat (xT), Singh (2018):** a grid model that gives each zone the probability of scoring from there ([Singh](https://karun.in/blog/expected-threat.html)).

**Pressure on a player (tracking-based):** Andrienko et al. (2017) define a pressure zone around the ball carrier ([*Data Mining and Knowledge Discovery*](https://doi.org/10.1007/s10618-017-0513-2)). We considered it and **dropped it** on 25 October: medium cost for a low-priority number.

### Our definition

**Momentum (team level).** It combines territory and danger over the **last 5 minutes of match time**:

```
tilt_home  = home possession time with the ball in the opponent's final third
             / (home + away possession time in their respective final thirds)
danger_home = home xG / (home xG + away xG)          (if no shots in the window: use tilt only)

momentum = (0.5·tilt_home + 0.5·danger_home) − (0.5·tilt_away + 0.5·danger_away)      range −1 … +1
```

- **Positive values** mean the home team is pushing.
- **Data needed:** ball position and status, possession (provider column), xG of the shots in the window (§9).
- **Why this form:** like field tilt it rewards play near the opponent's goal, not safe possession; like possession-value models it does not depend only on shots. It needs no extra model beyond xG.

**PPDA (team level, whole match so far):**

```
PPDA(team) = opponent passes in the team's pressing zone / team's defensive actions in the same zone
pressing zone = the 60 % of the pitch nearest to the opponent's goal (the team's attacking 60 %)
```

- **Passes:** the opponent's `Play` events (passes), at their located position.
- **Defensive actions:** the team's `TacklingGame`, `Foul` and `BallClaiming` events in the zone.

### In the app

- **Momentum, Fan and Analyst:** the mirror chart over time (one signed value, drawn as an area above or below the axis).
- **PPDA:** blurred in Fan; in Analyst one number per team in the table.

### How Archie explains it

> "Momentum shows who is on top right now. It looks at the last five minutes: how much each team played in the other team's final third, and how good their chances were. Right now the chart leans towards Brackwater."

> "PPDA counts how many passes a team lets the opponent make before trying to win the ball back. Calderholm's is 8, Brackwater's 14: Calderholm are pressing much harder."

### Choices

- **Momentum:** 5-minute window, weights 0.5/0.5, no xT (a roadmap idea).
- **Pressure on the ball carrier:** removed.
- **PPDA:** kept, Analyst only.

---

## 14. Shot map

### What it measures

Where each shot was taken from, which ones were goals and, for Analyst, how good each chance was.

### Definitions in the literature

- **Standard visual** of providers and analysts: one marker per shot on a half pitch, goals highlighted, marker size proportional to xG ([Footovision](https://www.footovision.com/what-are-expected-goals-xG); plotting example in [mplsoccer](https://mplsoccer.readthedocs.io/)).
- **Event vs tracking location.** Provider event coordinates are annotated by hand. We use the location at the synchronised frame (guidelines §9.3), the same one the xG model uses (§9).

### Our definition

- **Point:** ball position at the shot's `sync_frame` (the **located** position, not the annotated one), mirrored so that each team attacks the same goal on the map.
- **Symbols:** filled marker = goal; hollow marker = not a goal (blocked shots included, with no symbol of their own); **×** = woodwork. Colour = team.
- **Data needed:** located shot events, outcome, xG.

### In the app

- **Fan:** the dots, with goals filled and woodwork as ×.
- **Analyst:** also the marker area proportional to xG.

### How Archie explains it

> "Each dot is a shot, placed where the ball was when it was struck. The filled ones went in, and the crosses hit the woodwork. In the Analyst view, bigger dots were better chances."

### Choices

- **Blocked shots:** plain "not a goal" markers.
- **Woodwork:** ×.

---

## 15. Zone map (heatmap)

### What it measures

Where on the pitch a player (or a team) spent his time while the ball was in play. It is popular with fans who follow a favourite player.

### Definitions in the literature

- **Tracking heatmaps** count the time spent in each area. **Event heatmaps** count only actions on the ball. The two correlate only moderately, and the result depends on the scale at which positions are grouped ([Heatmaps in soccer: event vs tracking datasets, arXiv 2106.04558](https://arxiv.org/abs/2106.04558)).
- **Smoothing:** heatmaps are usually a grid of counts smoothed with a kernel (kernel density estimation). A fixed bandwidth or a data-driven rule such as Silverman's can be used ([arXiv 2505.14711](https://arxiv.org/abs/2505.14711); [mplsoccer KDE example](https://mplsoccer.readthedocs.io/en/latest/gallery/pitch_plots/plot_kde.html)).

### Our definition

```
heat(cell) = Σ ΔT over valid samples with the player in the cell        grid 105 × 68 cells of 1 m
smoothed with a Gaussian kernel, σ = 3 m; normalised so the cells sum to 100 % of the time shown
```

- **Tracking-based**, valid samples only, **live play only** (where the player stands at a throw-in is not interesting).
- **Direction:** always attacking left to right, whichever half.
- **Team:** sum of the outfield players.

### In the app

- **Fan:** blurred.
- **Analyst:** per team and for each favourite player.

### How Archie explains it

> "The map shows where he spent his time while the ball was in play, from the tracking. The hotter the colour, the more time he spent there."

### Choices

- **σ = 3 m**, live play only, attacking left to right.
- **Filters** (per half, in or out of possession): not in this version.

---

## 16. Pass network (removed)

**Removed on 25 October 2026**: it would have had to be written from scratch. It is replaced by the players with the most touches (§24). The method we had planned (López Peña & Touchette 2012, [arXiv 1206.6904](https://arxiv.org/abs/1206.6904)) stays a roadmap idea.

---

## 17. Defensive line and team length

### What it measures

How high a team defends (the height of its last line) and how compact it is (the distance between its deepest and its most advanced players).

### Definitions in the literature

- **Team length and width** are computed from the positions of the outfield players: length along the pitch, width across it, often with the team centroid. They are standard measures of collective tactical behaviour ([Folgado et al., length, width and centroid distance](https://research.hanze.nl/en/publications/length-width-and-centroid-distance-as-measures-of-teams-tactical-/)).
- **Reference values.** A systematic review of professional matches reports team lengths of about 31–46 m and widths of about 35–48 m, and shows that they depend on where the ball is ([*Biology of Sport*, systematic review](https://www.termedia.pl/Reference-values-for-collective-tactical-behaviours-based-on-positional-data-in-professional-football-matches-a-systematic-review,78,43095,0,1.html)).
- **Line height** is usually the position of the deepest outfield defenders, measured from the team's own goal line; measured relative to the ball it is a strong indicator of defensive success ([arXiv 2511.06191](https://arxiv.org/abs/2511.06191)).

### Our definition

Per frame, live play, valid samples, outfield players only (goalkeeper excluded):

```
defensive_line = mean distance from own goal line of the 2 deepest outfield players        (m)
team_length   = distance along the pitch between the deepest and the most advanced outfield player,
                after dropping one player at each end                                       (m)
```

- **Averaged** per 5 minutes.

### In the app

- **Fan:** blurred.
- **Analyst:** the 5-minute values in the mirror chart, one series per team.

### How Archie explains it

> "The defensive line is how far from their own goal the last defenders stand, on average. Calderholm's line has dropped from 38 to 29 metres since they went ahead: they're sitting deeper."

### Choices

- **Line:** mean of the two deepest outfield players.
- **Length:** robust version (one player dropped at each end).

---

## 18. Stadium roar (removed)

**Removed on 25 October 2026.** With no real microphone data, the roar would have been entirely simulated. It stays, at most, a roadmap idea.

---

## 19. Number of sprints

### What it measures

How many times a player (or a team) sprinted. It is the Fan replacement for the speed zones.

### Definitions in the literature

- **Threshold:** FIFA counts sprinting above 25 km/h ([FIFA Training Centre](https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/what-distances-did-teams-cover.php)); Varley et al. (2017) use 7.0 m/s (25.2 km/h).
- **Minimum duration:** a sprint is an *effort*, not a single sample. The J-League counts a sprint when the speed stays above the threshold for at least 1 s, and moved the threshold from 24 to 25 km/h in 2023 to match FIFA ([JSSF, 2023](https://www.shobix.co.jp/jssf/tempfiles/journal/2023/237.pdf)). Varley et al. (2017) show that the counting rules (minimum duration, dwell time) change the counts a lot.

### Our definition

```
sprint = a run of consecutive valid samples with speed ≥ 25 km/h lasting ≥ 1 s
```

- **Merging:** two sprints separated by less than 1 s below the threshold count as one.
- **Valid samples only**; a gap with estimated samples ends the sprint.
- **Team:** sum of the outfield players.

### In the app

- **Fan:** team total and the player with the most sprints.
- **Analyst:** also the per-player table.

### How Archie explains it

> "A sprint is any run at 25 km/h or more, FIFA's sprint speed, held for at least a second. Brackwater have made 142 sprints, 21 of them by Kinsella-Ward."

### Choices

- **Threshold:** ≥ 25 km/h (so exactly 25.0 km/h is a sprint, as in the speed zones).
- **Merging gap:** 1 s.

---

## 20. Corners, fouls, cards

### What it measures

The basic match counts every fan looks for.

### Definitions in the literature

- **Laws of the Game** (IFAB): a corner kick is awarded when the whole ball passes over the goal line, last touched by a defender, without a goal being scored (Law 17); fouls and misconduct, and the yellow and red cards, are defined in Law 12 ([IFAB, Laws of the Game](https://www.theifab.com/laws/latest/)).
- **Provider events:** the DFL event data record corners, fouls (with the player who fouled and the player fouled) and cards with player, team and time.

### Our definition

- **Counts from events:** corners, fouls committed, yellow cards, red cards (a second yellow counts as a yellow and a red, as usually reported).
- **Per team.** Fouls won are also counted per player, for the favourite players.
- **Offsides** are in §26.

### In the app

- **Live:** yellow and red cards per team.
- **Fan and Analyst** (the same): corners, fouls and cards per team in the Data & Statistics table; fouls committed and won in each favourite player's card.

### How Archie explains it

> "These come straight from the official match events: 6 corners to Brackwater, 4 to Calderholm."

### Choices

- **No split by half.**
- **Fouls won:** shown for the favourite players.

---

## 21. Tackles and ball recoveries

### What it measures

The defensive work that does not show up elsewhere: how often a player won the ball back.

### Definitions in the literature

- **Opta:** a **tackle** is a legal challenge on the ground in which a player takes the ball from an opponent in possession; it is won or lost depending on who has the ball afterwards. An **interception** is when a player reads a pass and cuts it out. A **ball recovery** is when a player wins back a loose ball ([Opta Event Definitions](https://optaplayerstats.statsperform.com/en_GB/opta-event-definitions)).
- **DFL event data** record tackling games (duels) with the winner, and the possession changes.
- **Ball recovery time** (Bundesliga Match Facts): the time a team needs to win the ball back after losing it, a team-level view of the same idea.

### Our definition

- **Tackles won:** DFL tackling games won by the defending player.
- **Ball recoveries:** changes of possession to the team in live play that are not a pass by the opponent to it: interceptions, won tackles, loose balls won; from the events, checked against the possession column.

### In the app

- **Fan:** per team and for the favourite players.
- **Analyst:** also the table of all players.

### How Archie explains it

> "A recovery is any time a player wins the ball back for his team, by a tackle, by cutting out a pass or by picking up a loose ball. Hennebry has 9, more than anyone."

### Choices

- **Recoveries:** from events, checked against the possession column.

---

## 22. Acceleration load

### What it measures

How much a player has **changed speed** over the match: every start, stop and burst, including the short ones at low speed that distance and sprints miss. It is the "hidden effort" of a player who never sprints but never stops either.

### Definitions in the literature

- **Average acceleration.** Delaney et al. (2016) analysed professional rugby league with **average acceleration/deceleration** next to distance and metabolic power, over moving averages of 1 to 10 minutes, because high-intensity efforts often happen at low speed ([*IJSPP* 11(6):802–809](https://doi.org/10.1123/ijspp.2015-0424)). Delaney et al. (2018) used the same measure in professional soccer ([*Science and Medicine in Football* 2(2):86–92](https://doi.org/10.1080/24733938.2017.1383623)). The metric is described as the mean magnitude of the changes in speed over a period.
- **Acceleration load** (Catapult): the sum of the absolute acceleration values, derived from the smoothed speed, so accelerations and decelerations weigh the same ([Catapult support](https://support.catapultsports.com/hc/en-us/articles/360001559976-Acceleration-load-acceleration-density-and-acceleration-density-index)).
- **Not PlayerLoad.** PlayerLoad (Boyd, Ball & Aughey 2011) comes from body-worn accelerometers and sums the changes of acceleration ([IJSPP](https://vuir.vu.edu.au/9088/)): a different quantity, which tracking cannot measure.

### Our definition

```
acceleration_load(player) = Σ |a_tan| · ΔT        over samples with acceleration measured or approximate
a_tan = d(speed)/dt, our tangential acceleration (guidelines §7: Savitzky–Golay, acceleration window)
rate = acceleration_load / minutes played
```

- **Change of speed only** (`acc_tan`, in absolute value), as in the literature: a curve run at constant speed adds nothing.
- **Samples:** acceleration measured or "≈" (`acc_status`); estimated gaps are left out. The reliability symbol follows the share of time not measured, as for distance.
- **Units:** m/s (total change of speed), shown in km/h; the rate in km/h per minute played.
- **Substitutes:** the real value with the minutes played beside it, no "per 90 minutes".
- **Team:** sum of the outfield players; in the chart over time, the team's value per minute in each 5-minute window.

### In the app

- **Fan:** per team and for the favourite players (table).
- **Analyst:** also per player, and the 5-minute values in the mirror chart for the teams (lines for the favourite players).

### How Archie explains it

> "We add up every change of speed a player makes, speeding up or slowing down, 25 times a second. Meulenkamp hardly sprinted, but his acceleration load is the highest on the pitch: he never stopped adjusting."

### Choices

- **Formula:** ∫ |dv/dt| dt (not the square of the acceleration, which has no source and amplifies noise).
- **Acceleration:** tangential (change of speed), not the vector.
- **Team:** sum of the outfield players.

---

## 23. Woodwork

### What it measures

How many shots hit the post or the bar: the near misses fans remember.

### Definitions in the literature

- **Opta** counts shots hitting the woodwork as **not on target** ([The Analyst, Opta stats definitions](https://theanalyst.com/articles/opta-football-stats-definitions)).
- **DFL event data** give them their own outcome, `ShotWoodWork` (2 in our six DFL matches).

### Our definition

- **Count** of shot events with outcome `ShotWoodWork`, per team and per player. They are shots, never shots on target (§8).

### In the app

- **Fan:** per team.
- **Analyst:** also per player; on the shot map they are ×.

### How Archie explains it

> "That's the second time Brackwater have hit the woodwork. It counts as a shot, but not as a shot on target."

### Choices

- Counted apart from shots on target.

---

## 24. Most touches

### What it measures

Which players were most involved with the ball: the top 3 of each team. It replaces the pass network (§16) with a simpler number.

### Definitions in the literature

- **Opta "touches"**: the sum of all the events in which a player touches the ball ([Opta Event Definitions](https://optaplayerstats.statsperform.com/en_GB/opta-event-definitions)).

### Our definition

- **Touch** = an event in which the player plays the ball: passes (`Play`), shots, tackling games won, `BallClaiming`, `OtherBallAction`, and set pieces taken (throw-ins, corners, free kicks, goal kicks). Event-based, so it works the same on DFL and synthetic data.
- **Top 3 per team.**

### In the app

- **Fan and Analyst:** the 3 players with the most touches for each team; each favourite player's card shows his touches.

### How Archie explains it

> "A touch is any time a player plays the ball: a pass, a shot, a tackle won, a throw-in. Meulenkamp has had 71 touches, more than anyone on the pitch."

### Choices

- **From events**, not from the tracking touches.

---

## 25. Counter-attacks

### What it measures

How many times a team won the ball in its own half and got a shot away quickly: the most exciting attacks for a fan.

### Definitions in the literature

- **Opta "fast break"**: an attempt created after a team quickly turns defence into attack, winning the ball in its own half (as reported by [Squawka](https://www.squawka.com/en/?p=1413124)).
- **StatsBomb** separates related categories ("fast attacks from deep") and notes that definitions overlap ([On the anatomy of a counter-attack](https://statsbomb.com/2017/07/on-the-anatomy-of-a-counter-attack)).

### Our definition

```
counter-attack = a ball recovery (§21) in the team's own half, in live play,
                 followed by a shot by the same team within 15 s, without losing possession
```

- **15 s** is our threshold: Opta's wording says "quickly" without a number.
- **Data needed:** recoveries (events and possession column), located positions, shots.

### In the app

- **Fan:** the number per team.
- **Analyst:** also the list, with the minutes linked to the timeline.

### How Archie explains it

> "A counter-attack is when a team wins the ball in its own half and gets a shot away within 15 seconds. Calderholm have done it three times: that's how they got their goal."

### Choices

- **Own half** and **15 s**, our definition, aligned with Opta's "fast break".

---

## 26. Offsides

### What it measures

- **In the statistics:** how many times each team was caught offside, from the **official decisions**.
- **For Archie only:** when a fan asks "was it offside?", how far the player was from the line, by our tracking, with an honest margin.

### Definitions in the literature

- **Law 11 (IFAB):** a player is in an offside position if **any part of the head, body or feet** (arms excluded) is in the opponents' half and nearer to the opponents' goal line than both the ball and the second-last opponent, **at the moment a teammate plays the ball**. There is no offside offence directly from a goal kick, a throw-in or a corner ([IFAB, Laws of the Game](https://www.theifab.com/laws/latest/)).
- **How precise the official decision is.** FIFA's semi-automated offside technology tracks **up to 29 points of each player's body, 50 times per second**, with a sensor in the ball at 500 Hz to find the kick point ([FIFA](https://inside.fifa.com/technical/football-technology/football-technologies-and-innovations-at-the-fifa-womens-world-cup-2023/semi-automated-offside-technology)).
- **Uncertainty zone.** A University of Bath study (Soltani, 2022) found that the timing of the kick is hard to judge and suggested drawing **thicker lines to show the uncertainty zone** for marginal decisions ([University of Bath](https://www.bath.ac.uk/announcements/motion-capture-reveals-why-var-in-football-struggles-with-offside-decisions/)).
- **Our tracking** has **one point per player at 25 Hz**, with a position error of up to 0.18 m (Linke et al. 2020).

### Our definition

**Statistics:** the count of official `Offside` events per team (DFL: 13 in six matches, about 2 per match).

**Tracking check (`offside_check`, a deterministic tool of The Analyst, for Archie only):**

```
at the located frame of the pass (sync_frame), in the attacking direction:
margin = attacker position − max(second-last opponent position (goalkeeper included), ball position)
verdict = "offside"                        if margin >  2 m
          "onside"                         if margin < −2 m
          "too close to call with our data" otherwise
```

- **Band of 2 m**, our deliberately cautious choice: it covers, with a wide margin, the body parts that can be ahead of the tracked point, the tracking error and half a frame of timing. Inside the band we never say.
- **Only two cases are checked:** the official offsides and the last pass before each goal. We never search all passes for "missed offsides".
- **Tested** on the 13 DFL offsides: each should come out "offside" or "too close to call".

### In the app

- **Live:** not shown (Live is fixed).
- **Fan and Analyst:** offsides per team in the Data & Statistics table.
- **The tracking check is not shown in the statistics:** Archie uses it when asked; the Inspector shows the margin and the band.

### How Archie explains it

Archie **never contradicts the referee**:

> "Yes, he was about 2.5 metres beyond the second-last defender." (margin above the band)

> "Too tight for me to see for sure, but the referee called offside." (inside the band)

> "I can't confirm it from my data; the official decision was offside." (if our data ever said "onside")

> "Onside by about 3 metres." / "Too tight for my data to tell, and the goal stood." (goals)

> "Why can't you tell? We only know one point per player, 25 times a second. The official system follows 29 points of the body, 50 times a second."

### Choices

- **Statistics:** official decisions only.
- **Band:** 2 m. **Checked cases:** official offsides and goals only.

---

## Sources

- Pollard, R. & Reep, C. (1997). Measuring the effectiveness of playing strategies at soccer. *Journal of the Royal Statistical Society, Series D*, 46(4), 541–550. https://www.jstor.org/stable/2988603
- Expected goals (summary of Pollard, Ensum & Taylor's work on the 2002 World Cup). https://en.wikipedia.org/wiki/Expected_goals
- Robberechts, P. & Davis, J. (2020). How data availability affects the ability to learn good xG models. *Machine Learning and Data Mining for Sports Analytics* (MLSA 2020), Springer, 17–27. https://dtai.cs.kuleuven.be/sports/blog/how-data-availability-affects-the-ability-to-learn-good-xg-models
- Sumpter, D. Soccermatics: fitting the xG model (open Wyscout data, Premier League 2017/18). https://soccermatics.readthedocs.io/en/latest/gallery/lesson2/plot_xGModelFit.html
- Pappalardo, L. et al. (2019). A public data set of spatio-temporal match events in soccer competitions. *Scientific Data* 6:236. https://doi.org/10.1038/s41597-019-0247-7
- Peduzzi, P., Concato, J., Kemper, E., Holford, T. R. & Feinstein, A. R. (1996). A simulation study of the number of events per variable in logistic regression analysis. *Journal of Clinical Epidemiology* 49(12), 1373–1379. https://doi.org/10.1016/S0895-4356(96)00236-3
- Lucey, P., Bialkowski, A., Monfort, M., Carr, P. & Matthews, I. (2015). "Quality vs quantity": improved shot prediction in soccer using strategic features from spatiotemporal data. MIT Sloan Sports Analytics Conference. https://www.sloansportsconference.com/research-papers/quality-vs-quantity-improved-shot-prediction-in-soccer-using-strategic-features-from-spatiotemporal-data
- Anzer, G. & Bauer, P. (2021). A goal scoring probability model for shots based on synchronized positional and event data in football (soccer). *Frontiers in Sports and Active Living*, 3:624475. https://doi.org/10.3389/fspor.2021.624475
- Mead, J., O'Hare, A. & McMenemy, P. (2023). Expected goals in football: improving model performance and demonstrating value. *PLOS ONE* 18(4): e0282295. https://doi.org/10.1371/journal.pone.0282295
- FBref: xG explained (penalty value 0.79, Opta). https://aws.fbref.com/en/expected-goals-model-explained
- Hudl StatsBomb: expected goals explained (penalty value 0.78 since 2022). https://www.hudl.com/blog/expected-goals-xg-explained
- Andrienko, G. et al. (2017). Visual analysis of pressure in football. *Data Mining and Knowledge Discovery*, 31(6), 1793–1839. https://doi.org/10.1007/s10618-017-0513-2
- Stats Perform: How we measure pressure (PPDA and pressing). https://www.statsperform.com/resource/how-we-measure-pressure/
- Premier League: Passes per defensive action explained. https://www.premierleague.com/news/4250153
- Field tilt (definitions vary by provider): https://www.sportmonks.com/glossary/key-data-points
- Flashscore: Introducing Match Momentum (Opta data). https://www.flashscore.com/news/momentum/W82o7eCa/
- Singh, K. (2018). Introducing Expected Threat (xT). https://karun.in/blog/expected-threat.html
- Linke, D., Link, D. & Lames, M. (2020). Football-specific validity of TRACAB's optical video tracking systems. *PLoS ONE* 15(3): e0230179. https://doi.org/10.1371/journal.pone.0230179
- FIFA Training Centre, World Cup 2022 physical analysis: method https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/background-and-method.php and distances https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/what-distances-did-teams-cover.php
- Gualtieri, A. et al. (2023). High-speed running and sprinting in professional adult soccer: current thresholds definition, match demands and training strategies. A systematic review. *Frontiers in Sports and Active Living* 5:1116293. https://doi.org/10.3389/fspor.2023.1116293
- Varley, M. C. et al. (2017). Methodological considerations when quantifying high-intensity efforts in team sport using global positioning system technology. *IJSPP*. https://doi.org/10.1123/ijspp.2016-0534
- Varley, M. C., Elias, G. P. & Aughey, R. J. (2012). Current match-analysis techniques' underestimation of intense periods of high-velocity running. *IJSPP* 7(2), 183–185. https://vuir.vu.edu.au/9091/
- Fereday, K. et al. (2020). A comparison of rolling averages versus discrete time epochs for assessing the worst-case scenario locomotor demands of professional soccer match-play. *Journal of Science and Medicine in Sport*. https://doi.org/10.1016/j.jsams.2020.01.002
- Delaney, J. A. et al. (2018). Modelling the decrement in running intensity within professional soccer players. *Science and Medicine in Football* 2(2), 86–92. https://doi.org/10.1080/24733938.2017.1383623
- Delaney, J. A., Duthie, G. M., Thornton, H. R., Scott, T. J., Gay, D. & Dascombe, B. J. (2016). Acceleration-based running intensities of professional rugby league match play. *IJSPP* 11(6), 802–809. https://doi.org/10.1123/ijspp.2015-0424
- Catapult: acceleration load, acceleration density and acceleration density index. https://support.catapultsports.com/hc/en-us/articles/360001559976-Acceleration-load-acceleration-density-and-acceleration-density-index
- Boyd, L. J., Ball, K. & Aughey, R. J. (2011). The reliability of MinimaxX accelerometers for measuring physical activity in Australian football. *IJSPP*. https://vuir.vu.edu.au/9088/
- Temporal distribution of peak running demands relative to match minutes in elite football (2022). *Biology of Sport*. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9536360/
- Amazon / Bundesliga Match Facts, speed: https://www.aboutamazon.eu/news/amazon-web-services/new-bundesliga-match-fact-will-showcase-the-speed-on-the-pitch
- Bundesliga Match Facts, Shot Speed: https://www.bundesliga.com/en/bundesliga/news/new-match-fact-aws-shot-speed-stat-25227 and https://aws.amazon.com/blogs/machine-learning/bundesliga-match-facts-shot-speed-who-fires-the-hardest-shots-in-the-bundesliga
- KU Leuven DTAI Sports: analyzing player pace of play in soccer. https://dtai.cs.kuleuven.be/sports/blog/%22aannemen-kijken-spelen%22:-analyzing-player-pace-of-play-in-soccer
- Pace of play within possessions. *Journal of Sports Analytics*. https://sage.cnpereading.com/doi/10.3233/JSA-200581
- Ball circulation and possession time of Spain at the 2010 World Cup (UFV). https://locus.ufv.br/handle/123456789/34674
- Link, D. & Hoernig, M. (2017). Individual ball possession in soccer. *PLoS ONE*. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5503225/
- Opta Event Definitions (Stats Perform). https://optaplayerstats.statsperform.com/en_GB/opta-event-definitions and https://statsperform.com/opta-event-definitions
- The Analyst: Opta football stats definitions. https://theanalyst.com/articles/opta-football-stats-definitions
- Sports Illustrated (2018), Opta long-ball definition. https://www.si.com/soccer/2018/11/23/surprising-statistics-reveal-chelsea-and-manchester-city-best-long-ball-premier-league-sides
- Footovision: What are Expected Goals (xG). https://www.footovision.com/what-are-expected-goals-xG
- Siegle, M. & Lames, M. (2012). Game interruptions in elite soccer. *Journal of Sports Sciences* 30(7), 619–624. https://doi.org/10.1080/02640414.2012.667877
- Associated Press (30 November 2022): World Cup fans see more active play from stoppages directive. https://sports.mynorthwest.com/other-sports/world-cup-fans-see-more-active-play-from-stoppages-directive
- Heatmaps in soccer: event vs tracking datasets. arXiv 2106.04558. https://arxiv.org/abs/2106.04558
- Space evaluation at the starting point of soccer transitions (KDE bandwidth). arXiv 2505.14711. https://arxiv.org/abs/2505.14711
- mplsoccer: KDE plots. https://mplsoccer.readthedocs.io/en/latest/gallery/pitch_plots/plot_kde.html
- López Peña, J. & Touchette, H. (2012). A network theory analysis of football strategies. arXiv 1206.6904. https://arxiv.org/abs/1206.6904
- Folgado, H. et al. (2014). Length, width and centroid distance as measures of teams tactical performance in youth football. *European Journal of Sport Science* 14(sup1). https://research.hanze.nl/en/publications/length-width-and-centroid-distance-as-measures-of-teams-tactical-/
- Reference values for collective tactical behaviours based on positional data in professional football matches: a systematic review. *Biology of Sport*. https://www.termedia.pl/Reference-values-for-collective-tactical-behaviours-based-on-positional-data-in-professional-football-matches-a-systematic-review,78,43095,0,1.html
- Prediction-based evaluation of back-four defense with spatial control in soccer. arXiv 2511.06191. https://arxiv.org/abs/2511.06191
- J-League sprint definition (≥ 25 km/h for ≥ 1 s from 2023). *Japanese Journal of Science and Football*, 2023. https://www.shobix.co.jp/jssf/tempfiles/journal/2023/237.pdf
- IFAB, Laws of the Game (Law 11 offside, Law 12 fouls and misconduct, Law 17 corner kick). https://www.theifab.com/laws/latest/
- FIFA: semi-automated offside technology. https://inside.fifa.com/technical/football-technology/football-technologies-and-innovations-at-the-fifa-womens-world-cup-2023/semi-automated-offside-technology
- University of Bath (2022): Motion capture reveals why VAR in football struggles with offside decisions. https://www.bath.ac.uk/announcements/motion-capture-reveals-why-var-in-football-struggles-with-offside-decisions/
- Squawka: Opta's "fast break" definition. https://www.squawka.com/en/?p=1413124
- StatsBomb (2017): On the anatomy of a counter-attack. https://statsbomb.com/2017/07/on-the-anatomy-of-a-counter-attack
