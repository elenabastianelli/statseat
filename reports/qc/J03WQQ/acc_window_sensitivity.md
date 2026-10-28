# Acceleration window sensitivity - match J03WQQ

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 19 / 14 | 102 | 2 / 3 | 33 | -5.78 / 4.98 | -11.3 / 8.45 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 11 / 5 | 43 | 0 / 0 | 0 | -5.51 / 4.75 | -13.3 / 7.96 | 0.09 / 0.282 | 0.368 / 0.631 |
| 25 | 1 / 1 | 2 | 0 / 0 | 0 | -5.19 / 4.5 | -8.6 / 7.5 | 0.195 / 0.612 | 0.828 / 1.408 |
