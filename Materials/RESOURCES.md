# Resources

Useful sources for the data analysis, metrics, visualisation and AI parts of InsideTheGame.

> The PDFs in this folder are kept **locally only** (git-ignored): one is a third-party, copyrighted report with real player names. They are listed here with their public source so that anyone can get them.
> Real data and reports are used **for inspiration and calibration only**. The app and the demo use synthetic data with fictional teams and players.

## Contents

- [1. Material in this folder](#1-material-in-this-folder)
- [2. Visualisation and metric inspiration](#2-visualisation-and-metric-inspiration)
- [3. Python libraries](#3-python-libraries)
- [4. Open datasets](#4-open-datasets)
- [5. Methods and papers](#5-methods-and-papers)
- [6. Learning](#6-learning)
- [7. Microsoft and the hackathon](#7-microsoft-and-the-hackathon)

---

## 1. Material in this folder

| File (local only) | What it is | Source | Why it matters to us |
|---|---|---|---|
| `Bassek_et_al-2025-Scientific_Data.pdf` | Data descriptor of the DFL open dataset (7 Bundesliga / 2. Bundesliga matches: match information, events, 25 Hz tracking). CC-BY 4.0 | Paper: https://doi.org/10.1038/s41597-025-04505-y · Data: https://doi.org/10.6084/m9.figshare.28196177 · Code: https://github.com/spoho-datascience/idsse-data | The dataset we use to calibrate the synthetic generator. Explains file formats, units, tracking errors and event–tracking synchronisation |
| `Elicit - Analyzing football data metrics and methodologies - Report.pdf` | AI-assisted literature summary (Elicit, 10 sources) on football metrics, thresholds, filtering and outlier handling | Generated with https://elicit.com | Summarises thresholds (HSR, sprint, accelerations), filtering choices and the call for transparent reporting |
| `report_128083.pdf` | FIFA Post-Match Summary Report, FIFA World Cup 2022 Final | FIFA Training Centre, WC 2022 analysis hub: https://www.fifatrainingcentre.com/en/fwc2022/fwc2022.php | A professional reference for match reports: phases of play, line breaks, offers to receive, pressures, speed zones (0–7 / 7–15 / 15–20 / 20–25 / > 25 km/h), physical data |

Our processing rules derived from this material are in [`docs/DATA_ANALYSIS_GUIDELINES.md`](../docs/DATA_ANALYSIS_GUIDELINES.md).
Physical limits, realism ranges and effort thresholds, each with its source, are in [`PHYSICAL_LIMITS.md`](PHYSICAL_LIMITS.md).

---

## 2. Visualisation and metric inspiration

| Source | Link | Ideas for us |
|---|---|---|
| Footovision: Dynamic Pitch Control | https://www.footovision.com/visualizing-positioning-and-player-decisions-the-innovation-of-dynamic-pitch-control | Which team "controls" each area, based on how quickly players can reach it. A roadmap idea, together with the 2D replay of VAR's moments |
| Footovision: What are Expected Goals (xG) | https://www.footovision.com/what-are-expected-goals-xG | Fan-friendly xG explanations: cumulative xG timeline, goals minus xG ("luck or finishing?") |
| Footovision: Playing Areas Summary widget | https://www.footovision.com/visualizing-the-different-events-of-a-team-on-the-pitch-the-innovation-of-the-playing-areas-summary-widget | Zone map of a team's actions, a "what happens next" view, arrow thickness for shot quality |
| DataMetrics Department | https://datametricsdepartment.com | Structure of professional pre-match and post-match player and club reports |
| FIFA Enhanced Football Intelligence | https://www.fifatrainingcentre.com/en/fwc2022/fwc2022.php | Phases of play, line breaks, offers to receive, defensive pressure, speed zones |
| Opta Analyst | https://theanalyst.com | Accessible data storytelling for fans |
| Karun Singh: Expected Threat (xT) | https://karun.in/blog/expected-threat.html | A simple, explainable model of how much a ball move increases the chance of scoring |

---

## 3. Python libraries

| Library | Link | Use |
|---|---|---|
| kloppy | https://kloppy.pysport.org · https://github.com/PySport/kloppy | Load event and tracking data from many providers, DFL/Sportec included, into one model |
| floodlight | https://floodlight.readthedocs.io · https://github.com/floodlight-sports/floodlight | DFL parser, filters (Savitzky–Golay, Butterworth, Kalman), gap interpolation (spline), kinematics, space control. Used by the DFL dataset authors |
| databallpy | https://databallpy.readthedocs.io · https://github.com/Alek050/databallpy | Preprocessing (filtering, velocity and acceleration) and **event–tracking synchronisation** |
| mplsoccer | https://mplsoccer.readthedocs.io | Pitch plots, heatmaps, pass networks, shot maps |
| socceraction | https://github.com/ML-KULeuven/socceraction | Action valuation (VAEP, xT) from event data |
| SciPy `savgol_filter` | https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html | Savitzky–Golay smoothing and derivatives |
| FilterPy | https://filterpy.readthedocs.io | Kalman filter and RTS smoother |
| LaurieOnTracking | https://github.com/Friends-of-Tracking-Data-FoTD/LaurieOnTracking | Tutorial code: tracking data, velocities, pitch control (Metrica sample data) |
| PySport open-source directory | https://opensource.pysport.org | A catalogue of open-source sports analytics packages |

---

## 4. Open datasets

Use them only for inspiration or calibration, and check each licence before use. The app itself uses synthetic data only.

| Dataset | Link | Content |
|---|---|---|
| DFL / IDSSE (our calibration data) | https://doi.org/10.6084/m9.figshare.28196177 | 7 matches: match information, events, 25 Hz tracking (CC-BY 4.0) |
| StatsBomb Open Data | https://github.com/statsbomb/open-data | Event data (some competitions include 360 freeze-frames) |
| Metrica Sports sample data | https://github.com/metrica-sports/sample-data | Anonymised tracking and event data (3 matches) |
| SkillCorner Open Data | https://github.com/SkillCorner/opendata | Broadcast tracking data |

---

## 5. Methods and papers

**Tracking data quality and processing**
- Linke, Link & Lames (2020). Football-specific validity of TRACAB's optical video tracking systems. *PLoS ONE* 15(3): e0230179. https://doi.org/10.1371/journal.pone.0230179
- Ellens et al. (2022). Techniques to derive and clean acceleration and deceleration data of athlete tracking technologies in team sports: a scoping review. *J Sports Sci* 40(16). https://doi.org/10.1080/02640414.2022.2054535
- Ellens et al. (2024). Effect of data-processing methods on acceleration summary metrics of GNSS devices in elite Australian football. *Sensors* 24(13):4383. https://www.mdpi.com/1424-8220/24/13/4383
- Teixeira et al. (2022). Integrating physical and tactical factors in football using positional data: a systematic review. *PeerJ*. https://doi.org/10.7717/peerj.14381

**Physical metrics and thresholds**
- Varley et al. (2017). Methodological considerations when quantifying high-intensity efforts in team sport using GPS. *IJSPP*. https://doi.org/10.1123/ijspp.2016-0534
- Varley, Elias & Aughey (2012). Current match-analysis techniques' underestimation of intense periods of high-velocity running. *IJSPP* 7(2), 183–185. https://vuir.vu.edu.au/9091/
- Delaney et al. (2018). Modelling the decrement in running intensity within professional soccer players. *Science and Medicine in Football* 2(2), 86–92. https://doi.org/10.1080/24733938.2017.1383623
- Delaney et al. (2016). Acceleration-based running intensities of professional rugby league match play. *IJSPP* 11(6), 802–809. https://doi.org/10.1123/ijspp.2015-0424
- Catapult: acceleration load, acceleration density and acceleration density index. https://support.catapultsports.com/hc/en-us/articles/360001559976-Acceleration-load-acceleration-density-and-acceleration-density-index
- Boyd, Ball & Aughey (2011). The reliability of MinimaxX accelerometers for measuring physical activity in Australian football (PlayerLoad). *IJSPP*. https://vuir.vu.edu.au/9088/
- Osgnach et al. (2010). Energy cost and metabolic power in elite soccer. *Med Sci Sports Exerc*. https://doi.org/10.1249/MSS.0b013e3181ae5cfd

**Expected goals and action value** (how we use them: [`METRICS.md`](METRICS.md))
- Pollard & Reep (1997). Measuring the effectiveness of playing strategies at soccer. *JRSS Series D* 46(4), 541–550. https://www.jstor.org/stable/2988603
- Lucey et al. (2015). "Quality vs quantity": improved shot prediction in soccer using strategic features from spatiotemporal data. MIT Sloan Sports Analytics Conference. https://www.sloansportsconference.com/research-papers/quality-vs-quantity-improved-shot-prediction-in-soccer-using-strategic-features-from-spatiotemporal-data
- Robberechts & Davis (2020). How data availability affects the ability to learn good xG models. MLSA 2020, Springer. https://dtai.cs.kuleuven.be/sports/blog/how-data-availability-affects-the-ability-to-learn-good-xg-models
- Anzer & Bauer (2021). A goal scoring probability model for shots based on synchronized positional and event data in football (soccer). *Front Sports Act Living* 3:624475. https://doi.org/10.3389/fspor.2021.624475
- Soccermatics (Sumpter): fitting the xG model on open Wyscout data (8,451 Premier League shots; source of our fixed visible-angle slope). https://soccermatics.readthedocs.io/en/latest/gallery/lesson2/plot_xGModelFit.html
- Pappalardo et al. (2019). A public data set of spatio-temporal match events in soccer competitions (Wyscout, CC BY 4.0). *Scientific Data* 6:236. https://doi.org/10.1038/s41597-019-0247-7
- Mead, O'Hare & McMenemy (2023). Expected goals in football: improving model performance and demonstrating value. *PLOS ONE* 18(4): e0282295. https://doi.org/10.1371/journal.pone.0282295
- Peduzzi et al. (1996). A simulation study of the number of events per variable in logistic regression analysis. *J Clin Epidemiol* 49(12), 1373–1379. https://doi.org/10.1016/S0895-4356(96)00236-3
- Penalty xG: FBref (0.79, Opta) https://aws.fbref.com/en/expected-goals-model-explained · Hudl StatsBomb (0.78 since 2022) https://www.hudl.com/blog/expected-goals-xg-explained
- Expected Threat (xT): https://karun.in/blog/expected-threat.html
- VAEP and xT implementations: https://github.com/ML-KULeuven/socceraction

**Pressure, pressing and momentum** (how we use them: [`METRICS.md`](METRICS.md))
- Andrienko et al. (2017). Visual analysis of pressure in football. *Data Mining and Knowledge Discovery* 31(6), 1793–1839. https://doi.org/10.1007/s10618-017-0513-2 (accepted version: https://openaccess.city.ac.uk/id/eprint/17464/)
- Stats Perform: How we measure pressure (PPDA). https://www.statsperform.com/resource/how-we-measure-pressure/
- Premier League: Passes per defensive action explained. https://www.premierleague.com/news/4250153
- Field tilt (provider glossary): https://www.sportmonks.com/glossary/key-data-points
- Flashscore: Introducing Match Momentum (Opta data). https://www.flashscore.com/news/momentum/W82o7eCa/

**Other match metrics** (how we use them: [`METRICS.md`](METRICS.md))
- FIFA Training Centre, World Cup 2022 physical analysis (method, distances, speed zones): https://www.fifatrainingcentre.com/en/fwc2022/physical-analysis/background-and-method.php
- Gualtieri et al. (2023). High-speed running and sprinting thresholds in professional adult soccer: a systematic review. *Front Sports Act Living* 5:1116293. https://doi.org/10.3389/fspor.2023.1116293
- Fereday et al. (2020). Rolling averages versus discrete time epochs for worst-case locomotor demands. *J Sci Med Sport*. https://doi.org/10.1016/j.jsams.2020.01.002
- Bundesliga Match Facts, Shot Speed: https://www.bundesliga.com/en/bundesliga/news/new-match-fact-aws-shot-speed-stat-25227
- Link & Hoernig (2017). Individual ball possession in soccer. *PLoS ONE*. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5503225/
- Siegle & Lames (2012). Game interruptions in elite soccer. *J Sports Sci* 30(7), 619–624. https://doi.org/10.1080/02640414.2012.667877
- Opta Event Definitions: https://optaplayerstats.statsperform.com/en_GB/opta-event-definitions; The Analyst: https://theanalyst.com/articles/opta-football-stats-definitions
- KU Leuven DTAI, pace of play: https://dtai.cs.kuleuven.be/sports/blog/%22aannemen-kijken-spelen%22:-analyzing-player-pace-of-play-in-soccer
- López Peña & Touchette (2012). A network theory analysis of football strategies. https://arxiv.org/abs/1206.6904
- Heatmaps in soccer: event vs tracking datasets. https://arxiv.org/abs/2106.04558
- Folgado et al. (2014). Length, width and centroid distance as measures of teams tactical performance in youth football. https://research.hanze.nl/en/publications/length-width-and-centroid-distance-as-measures-of-teams-tactical-/
- Reference values for collective tactical behaviours in professional football: a systematic review. *Biology of Sport*. https://www.termedia.pl/Reference-values-for-collective-tactical-behaviours-based-on-positional-data-in-professional-football-matches-a-systematic-review,78,43095,0,1.html
- Nevill, Balmer & Williams (2002). Crowd noise and refereeing decisions. *Psychol Sport Exerc* 3(4). https://doi.org/10.1016/S1469-0292(01)00033-4
- IFAB Laws of the Game (Law 11 offside, Law 12 fouls, Law 17 corners): https://www.theifab.com/laws/latest/
- FIFA: semi-automated offside technology (29 body points at 50 Hz, ball sensor at 500 Hz). https://inside.fifa.com/technical/football-technology/football-technologies-and-innovations-at-the-fifa-womens-world-cup-2023/semi-automated-offside-technology
- University of Bath (2022): motion capture and the uncertainty zone of offside decisions. https://www.bath.ac.uk/announcements/motion-capture-reveals-why-var-in-football-struggles-with-offside-decisions/
- Opta "fast break" (counter-attack), as reported by Squawka: https://www.squawka.com/en/?p=1413124 · StatsBomb (2017), On the anatomy of a counter-attack: https://statsbomb.com/2017/07/on-the-anatomy-of-a-counter-attack

**Pitch control**
- Spearman, W. (2018). *Beyond Expected Goals*. MIT Sloan Sports Analytics Conference: the physics-based pitch control model.
- Friends of Tracking tutorials on pitch control: https://www.youtube.com/@friendsoftracking755

**Event–tracking synchronisation**
- Kwiatkowski: The right way to synchronize event and tracking data. https://kwiatkowski.io/sync.soccer
- Van Roy, Cascioli & Davis (2024). ETSY: a rule-based approach to event and tracking data synchronization.

---

## 6. Learning

- **Friends of Tracking** (YouTube): https://www.youtube.com/@friendsoftracking755 · code: https://github.com/Friends-of-Tracking-Data-FoTD
- **Soccermatics** (David Sumpter): https://soccermatics.readthedocs.io
- *Football Analytics with Python & R*, Eric Eager & Richard Erickson (O'Reilly, 2023)
- **PySport** community and package directory: https://opensource.pysport.org

---

## 7. Microsoft and the hackathon

- Hackathon rules: https://github.com/microsoft/insidethegamehackathon
- AI Skills Navigator playlists: https://aka.ms/asn/foundry · https://aka.ms/asn/foundryagents · https://aka.ms/asn/agenticworkflows · https://aka.ms/asn/ghcopilot · https://aka.ms/asn/data
- Reference demo (Bryan Benz, Microsoft Reactor): https://github.com/bbenz/football-results-dashboard
- Microsoft Foundry documentation: https://learn.microsoft.com/en-us/azure/foundry/
- Microsoft Agent Framework: https://learn.microsoft.com/en-us/agent-framework/overview/
- Azure Container Apps: https://learn.microsoft.com/en-us/azure/container-apps/
- Azure Developer CLI (`azd`): https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/
