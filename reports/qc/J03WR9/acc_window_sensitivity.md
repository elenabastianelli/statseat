# Acceleration window sensitivity - match J03WR9

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 12 / 9 | 69 | 2 / 4 | 44 | -5.56 / 4.79 | -11.63 / 9.31 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 6 / 7 | 44 | 0 / 1 | 7 | -5.3 / 4.58 | -10.75 / 8.63 | 0.111 / 0.402 | 0.371 / 2.069 |
| 25 | 1 / 0 | 5 | 1 / 0 | 6 | -5.01 / 4.34 | -8.43 / 10.98 | 0.227 / 0.921 | 0.833 / 4.34 |
