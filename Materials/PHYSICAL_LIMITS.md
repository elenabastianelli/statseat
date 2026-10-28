# Physical limits and reference values

Reference values used by the data analysis agent (`docs/DATA_ANALYSIS_GUIDELINES.md` v4.2, sections 6, 7, 8 and 10). Rule identifiers (for example `R-ACC-BAND`) are those of the guidelines.

**How to read the "Use" column:**

| Use | Meaning |
|---|---|
| **Outlier** | A sample beyond this value is physically impossible and is excluded through the validity mask, with its window |
| **Band** | A plausibility band. A value beyond it is **not excluded**: a very short exceedance is noise and its value is not used; a longer one is kept and reported with "≈" |
| **Handling** | A window or duration used by a handling rule |
| **Realism** | A typical range. It is used to judge whether synthetic data and reports look realistic, never to exclude samples |
| **Metric** | A threshold that defines a metric we compute now. Never used for outliers |
| **Future metric** | An effort threshold documented for possible future metrics. It is **not used now**, and **never** for outliers |
| **Convention** | How a metric is defined and reported |

All values are configurable in `data_pipeline/insidethegame_data/config.py`. When a value changes, update this file and the guidelines together.

---

## 1. Player speed

| Quantity | Value | Use | Source |
|---|---|---|---|
| **Maximum player speed** | **38 km/h (10.56 m/s)**, a fixed limit (`R-SPEED-LIMIT`), with ±1 s around each violation | **Outlier** | Rounded up from the highest verified match speed (see the next rows). A small margin above the record; TRACAB speed error is only 0.03–0.09 m/s (Linke et al. 2020) |
| Premier League record (Opta tracking, since 2020/21) | 37.38 km/h, Micky van de Ven, 31 Jan 2024 | Reference | [premierleague.com](https://www.premierleague.com/news/3884168); [tottenhamhotspur.com](https://www.tottenhamhotspur.com/news/2024/february/van-de-ven-officially-the-fastest-ever-premier-league-player) |
| Premier League, second fastest | 37.31 km/h, Kyle Walker, May 2023 | Reference | [premierleague.com, fastest players](https://www.premierleague.com/en/news/4573557/the-fastest-premier-league-players-ever-include-micky-van-de-ven-kyle-walker-mohamed-salah-adama-traore) |
| Bundesliga top speeds | about 36.5 km/h | Reference | Widely reported (for example Alphonso Davies); no single official source found |
| Higher claimed values (for example 39.2 km/h, Thierry Henry) | – | Not used | Unverified: they predate modern tracking or come from non-official sources |
| FIFA World Cup 2022 final, highest top speed | 33.6 km/h | Reference | FIFA post-match report ([FIFA Training Centre](https://www.fifatrainingcentre.com/en/fwc2022/fwc2022.php)) |
| Six DFL matches, highest valid speed (v3 run) | 32.9–36.1 km/h per match; p99.9 28.0–28.8 km/h. The highest, 36.1 km/h on J03WMX, is a smooth 3 s sprint that the provider's speed confirms: valid | Reference | Our reports, `reports/qc/SUMMARY.md` |

## 2. Player acceleration

| Quantity | Value | Use | Source |
|---|---|---|---|
| **Tangential acceleration band** | **−8.5 to +7.5 m/s²** (`R-ACC-BAND`) | **Band** | **Team reference band.** **Source to be cited:** the literature source of these values is known to the team and still has to be added here. Supporting evidence is listed in the next rows |
| Short exceedance | Beyond the band for **≤ 0.2 s** (5 samples at 25 Hz) | **Band** | **Noise**: the acceleration of those samples is not used; no window; positions, speed and distance are untouched. Team decision (25 Oct 2026), in line with the reviews that recommend a minimum effort duration (Delves et al. 2021; Ellens et al. 2022) |
| Longer exceedance | Beyond the band for **> 0.2 s** | **Band** | The value is kept and reported as **"≈ value"** (uncertain), never excluded. Team decision (25 Oct 2026) |
| Filter window for acceleration | **19 samples at 25 Hz (0.76 s)**, chosen by the team on 25 Oct 2026 from the sensitivity test of 13, 19 and 25 samples after the v4 run (on the six DFL matches: 182 / 78 / 19 noise runs and 36 / 8 / 3 `approx` runs); speed and distance always use 13 samples | Handling | Guidelines sections 7.2 and 7.3 (`R-SG-ACC`, `R-ACC-WINDOW-TEST`). No standard window exists; `databallpy` uses 35 samples for acceleration ([documentation](https://databallpy.readthedocs.io/en/latest/getting_started/preprocessing_options.html)) |
| Provider saturation | DFL/TRACAB `A` capped at ±7.99 m/s² | Evidence | Measured on J03WPY |
| Observed values (v3 run, 13-sample window) | p0.1 between −5.72 and −5.46 m/s², p99.9 between +4.71 and +4.92 m/s² over the six DFL matches; 216 episodes beyond the band (113 accelerations, 103 decelerations), mostly just beyond it | Evidence | Our reports, `reports/qc/SUMMARY.md` |
| Asymmetry: braking is harder than accelerating | High-intensity decelerations are more frequent than equivalent accelerations in elite team sports | Evidence | Harper, Carling & Kiely (2019), *Sports Medicine* 49:1923–1947, [doi:10.1007/s40279-019-01170-1](https://doi.org/10.1007/s40279-019-01170-1) |
| Asymmetry: braking forces | Peak braking forces up to 2.7 times those of the first steps of a maximal acceleration | Evidence | Harper, McBurnie, Santos et al. (2022), *Sports Medicine* 52:2321–2354, [doi:10.1007/s40279-022-01693-0](https://doi.org/10.1007/s40279-022-01693-0) |
| Reporting acceleration data | Few studies report their filter; reviews recommend reporting the filter and a minimum effort duration | Evidence | Delves et al. (2021), *Sports Medicine – Open* 7:45, [PMC8245618](https://pmc.ncbi.nlm.nih.gov/articles/PMC8245618); Ellens et al. (2022), *J Sports Sci* 40(16), [doi:10.1080/02640414.2022.2054535](https://doi.org/10.1080/02640414.2022.2054535) |
| Measurement accuracy (optical tracking, TRACAB) | RMSE 0.06–0.18 m (position), 0.03–0.09 m/s (speed), 0.06–0.27 m/s² (acceleration); errors grow at high speed | Evidence | Linke, Link & Lames (2020), *PLoS ONE* 15(3): e0230179, [doi:10.1371/journal.pone.0230179](https://doi.org/10.1371/journal.pone.0230179) |
| Measurement accuracy (10 Hz GPS) | Acceleration not reliable above about 4 m/s² | Evidence | Díaz-Soto et al. (2021, 2022), in the Elicit report in this folder |

## 3. Ball

| Quantity | Value | Use | Source |
|---|---|---|---|
| **Maximum ball speed** | **211 km/h (58.6 m/s)**, applied **only in live play** (`ball_status = live`, `R-BALL-LIMIT`) | **Outlier** | Fastest shot widely reported: Ronny Heberson, Sporting CP, 2006, 210.9 km/h. It is not an official record (no body certifies shot speeds); for example [soccerblade.com](https://soccerblade.com/how-fast-soccer-ball-kicked/) |
| Ball exclusion window | **±0.25 s** around a live-play ball jump or a live ball speed above 211 km/h | Handling | `R-BALL-WINDOW`. Half the 13-sample Savitzky–Golay window (6 samples at 25 Hz): what one bad sample can contaminate. Team decision (25 Oct 2026; 0.5 s in v3) |
| Ball window after a restart | **0.25 s** after each dead→live transition | Handling | `R-BALL-WINDOW` (0.5 s in v3) |
| Maximal instep kicks (measured) | 18–35 m/s (65–126 km/h); peak reported 32.1 m/s | **Realism** | Kellis & Katis (2007), Biomechanical characteristics and determinants of instep soccer kick, *J Sports Sci Med* 6:154–165, [jssm.org](https://www.jssm.org/hf.php?id=jssm-06-154.xml) |
| Male players, instep kick | 27.9 ± 1.3 m/s | **Realism** | Kinetic analysis of instep and side-foot kick in female and male soccer players, *Procedia Engineering* (2016), [sciencedirect.com](https://www.sciencedirect.com/science/article/pii/S1877705816306634) |
| Six DFL matches, live play (v3 run) | Valid ball speed max 104.4–119.8 km/h per match; p99.9 91.5–94.3 km/h | Reference | Our measurement (Savitzky–Golay speed on valid live samples), `reports/qc/SUMMARY.md` |
| Pass speed, 99th percentile (DFL) | 94.86 km/h (26.35 m/s) in the v4 calibration; recomputed whenever the calibration is rebuilt | Reference | `calibration/ball.json`. Since v4.1 a kick faster than this, and not a header, **is a shot** (`R-SHOT-POWER`); it also widens the direction margin to 8 m. Never a limit, never a reason to exclude a slow shot |
| **Ball acceleration** | **No limit** | – | Foot–ball contact lasts about 9–12 ms (Kellis & Katis 2007, citing Asami & Nolte 1983 and Asai et al. 1995). The resulting impulsive accelerations cannot be resolved at 25 Hz, and kicks, deflections, bounces and saves are all legitimate |
| Ball size and mass | Circumference 68–70 cm (radius about 0.11 m); mass 410–450 g | Reference | IFAB Laws of the Game, Law 2, [theifab.com](https://www.theifab.com/laws/latest/the-ball/) |
| Goal size | 7.32 m between the posts, 2.44 m to the underside of the crossbar | Reference | IFAB Laws of the Game, Law 1 (used by `R-GOAL` and `R-WOODWORK`) |

## 4. Speed zones, sprints and effort thresholds

**Speed zones and sprints are metrics now** (guidelines section 10.5). They are **not** validity limits and are **never used to detect outliers**.

| Threshold | Value | Use | Source |
|---|---|---|---|
| FIFA speed zones | 0–7, 7–15, 15–20, 20–25, > 25 km/h | **Metric** (`R-ZONES`) | FIFA post-match reports ([FIFA Training Centre](https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/what-distances-did-teams-cover.php)) |
| Sprint | Speed **at or above 25 km/h** (the FIFA threshold, the same as the top speed zone), for **at least 1 s**; two sprints less than 1 s apart count as one (decided in the metrics review, implemented in the metrics work) | **Metric** (`R-SPRINT`) | FIFA (threshold); J-League uses ≥ 25 km/h for ≥ 1 s (see `Materials/METRICS.md` §19) |
| High-speed running (Varley) | ≥ 4.17 m/s (15 km/h) | Reference | Varley et al. (2017), *IJSPP*, [doi:10.1123/ijspp.2016-0534](https://doi.org/10.1123/ijspp.2016-0534). Our metrics use the FIFA zones |
| Sprint (Varley) | ≥ 7.0 m/s (25.2 km/h) | Reference | Varley et al. (2017). Our metrics use the FIFA threshold of 25 km/h |

The acceleration thresholds below count *intense actions*. They are kept in case we add acceleration counts later.

| Threshold | Value | Use | Source |
|---|---|---|---|
| High-intensity acceleration | **≥ 2.78 m/s²** (equal to 10 km/h per second), counted from a standing start | Future metric | Varley & Aughey (2013), Acceleration profiles in elite Australian soccer, *Int J Sports Med* 34:34–39, [doi:10.1055/s-0032-1316315](https://doi.org/10.1055/s-0032-1316315). Also used by Varley et al. (2017) |
| High-intensity acceleration and deceleration (commercial GPS) | ≥ 3 m/s² | Future metric | [STATSports](https://statsports.com/article/maximum-acceleration-and-deceleration-metric-considerations-and-uses) |
| High and very high intensity acceleration and deceleration | > 2.5 m/s² and > 3.5 m/s² | Future metric | Harper et al. (2019), see section 2 |

## 5. Reporting convention for physical metrics

| Item | Convention | Source |
|---|---|---|
| Time base | Physical metrics (distance, distance per zone, sprints, top speed) are computed over the **whole time on the pitch** (ball in play **and** out of play), the way they are usually published | FIFA Technical Committee analysis: a wide midfielder's 10,902 m total included 3,413 m while the ball was out of play ([Human Kinetics excerpt](https://us.humankinetics.com/blogs/excerpt/physical-demands-on-the-soccer-player)) |
| Splits | Also reported **separately**: ball in play / ball out of play, and in possession / out of possession | FIFA post-match reports |
| Ball status | Taken from the provider's data, **never derived** from a stationary ball (a stationary ball does not mean the game is stopped). If the provider does not supply it, the ball-status splits are not reported | Team decision |
| Uncertain accelerations | Values from exceedances of the band longer than 0.2 s are shown as "≈ value", to 0.1 m/s² (`R-ACC-REPORT`) | Team decision (25 Oct 2026) |
