# Acceleration window sensitivity - match J03WMX

`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with windows of 13, 19, 25 samples; speed at 13 samples. Current choice: 0.75 x fs (provisional; the team chooses after the v4 run). Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).

| Window (samples) | Noise runs acc / dec | Noise samples | Approx runs acc / dec | Approx samples | p0.1 / p99.9 (m/s2) | min / max (m/s2) | Max speed change median / max (km/h) | Distance change median / max (%) |
|---|---|---|---|---|---|---|---|---|
| 13 | 32 / 27 | 188 | 1 / 10 | 78 | -5.6 / 4.93 | -14.39 / 9.68 | 0.0 / 0.0 | 0.0 / 0.0 |
| 19 | 6 / 11 | 53 | 0 / 3 | 20 | -5.32 / 4.68 | -10.8 / 8.43 | 0.106 / 1.749 | 0.355 / 0.816 |
| 25 | 2 / 1 | 11 | 0 / 0 | 0 | -5.02 / 4.43 | -10.64 / 7.93 | 0.229 / 2.791 | 0.788 / 1.779 |
