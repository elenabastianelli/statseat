# Acceleration window sensitivity - match SYN-001

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 0 / 0 | 0 | 0 / 0 | 0 | -6.01 / 5.55 | -7.11 / 6.87 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 0 / 0 | 0 | 0 / 0 | 0 | -5.76 / 5.37 | -7.11 / 6.9 | 0.066 / 0.195 | 0.39 / 0.426 |
| 25 | 0 / 0 | 0 | 0 / 0 | 0 | -5.47 / 5.17 | -7.07 / 7.46 | 0.156 / 0.453 | 0.892 / 0.97 |
