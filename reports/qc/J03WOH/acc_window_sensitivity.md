# Acceleration window sensitivity - match J03WOH

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 12 / 3 | 50 | 0 / 8 | 50 | -5.65 / 4.75 | -10.12 / 9.48 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 7 / 7 | 49 | 0 / 0 | 0 | -5.37 / 4.53 | -9.51 / 8.99 | 0.112 / 0.28 | 0.342 / 0.614 |
| 25 | 3 / 1 | 14 | 0 / 0 | 0 | -5.04 / 4.3 | -8.76 / 10.11 | 0.237 / 0.582 | 0.764 / 1.337 |
