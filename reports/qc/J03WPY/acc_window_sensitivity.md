# Acceleration window sensitivity - match J03WPY

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 17 / 11 | 94 | 1 / 2 | 25 | -5.53 / 4.81 | -9.85 / 12.38 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 8 / 3 | 35 | 1 / 2 | 19 | -5.25 / 4.59 | -9.42 / 8.67 | 0.074 / 0.217 | 0.34 / 0.741 |
| 25 | 6 / 1 | 22 | 0 / 1 | 6 | -4.94 / 4.34 | -8.91 / 8.17 | 0.142 / 0.507 | 0.764 / 1.605 |
