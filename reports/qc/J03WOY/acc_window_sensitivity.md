# Acceleration window sensitivity - match J03WOY

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 14 / 12 | 78 | 2 / 2 | 27 | -5.64 / 4.89 | -12.28 / 10.11 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 4 / 3 | 24 | 0 / 1 | 7 | -5.36 / 4.67 | -12.64 / 9.28 | 0.08 / 0.342 | 0.342 / 0.531 |
| 25 | 2 / 1 | 7 | 0 / 1 | 7 | -5.04 / 4.43 | -11.67 / 8.29 | 0.157 / 0.752 | 0.77 / 1.162 |
