"""Acceleration window sensitivity test (guidelines v4, ``R-ACC-WINDOW-TEST``, section 7.3).

On a prepared match, player acceleration is recomputed with ``w_a`` = odd(round(fs * f_a)) for
every ``f_a`` of ``acc_window_test_fractions`` (13, 19 and 25 samples at 25 Hz), **always on the
same cleaned positions** (the kinematic positions of ``prepare_half``, gap samples removed,
segments broken at the same jumps), with speed at the ``R-SG-SPEED`` window. For each window:

1. band exceedances that remain (``R-ACC-BAND`` on the same validity mask): runs and samples,
   ``noise`` (<= 0.2 s) and ``approx`` (> 0.2 s), by sign;
2. valid tangential acceleration: p0.1 / p99.9 and min / max (all valid samples with an
   acceleration, noise included: the raw effect of the window);
3. **diagnostic only**: speed and distance recomputed with the same window ``w_a``; per player,
   the change in maximum speed (median and maximum absolute change, km/h) and in total distance
   (median and maximum absolute relative change, %) against the speed window. Never used in results.

The band never excludes a sample, so the window cannot change the mask, the maximum speeds or the
distances used in results (tested). Outputs: ``qc_results.json`` (``acc_window_sensitivity``), and
``acc_window_sensitivity.csv`` / ``.md`` in the match's report folder.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .config import PrepConfig, odd_window
from .prepare import acc_band_runs, kinematics_on_segments


def _per_player(pairs, speed_by_half) -> dict[str, dict]:
    """Max valid speed (m/s) and total distance (m) per player with the given per-half speed arrays."""
    out: dict[str, dict] = {}
    for (hd, hr), sp in zip(pairs, speed_by_half):
        dt = 1.0 / hd.fs
        dist = np.where(hr.valid, np.nan_to_num(sp) * dt, 0.0) + np.where(hr.valid, 0.0, hr.dist)  # estimated path kept as it is
        for c, pid in enumerate(hd.pids):
            if not hr.present[:, c].any():
                continue
            a = out.setdefault(pid, {"max_ms": np.nan, "dist_m": 0.0})
            v = np.where(hr.valid[:, c], sp[:, c], np.nan)
            if np.isfinite(v).any():
                a["max_ms"] = float(np.nanmax([a["max_ms"], np.nanmax(v)]))
            a["dist_m"] += float(dist[:, c].sum())
    return out


def acc_window_sensitivity(pairs, cfg: PrepConfig) -> dict:
    """``R-ACC-WINDOW-TEST`` for one match. ``pairs``: (HalfData, HalfResult) per period from ``prepare_half``."""
    if not pairs or pairs[0][1].kin_x is None:
        return {"available": False}
    fs = float(pairs[0][0].fs)
    w_speed = cfg.savgol_window(fs)
    base_speed = [hr.speed for _, hr in pairs]
    base = _per_player(pairs, base_speed)
    windows = []
    for f_a in cfg.acc_window_test_fractions:
        w_a = odd_window(fs, f_a, cfg.savgol_polyorder)
        c_a = replace(cfg, acc_window_fraction_of_fs=f_a)
        counts = {cls: {sign: {"runs": 0, "samples": 0} for sign in ("acceleration", "deceleration")} for cls in ("noise", "approx")}
        accs, diag_speed = [], []
        for hd, hr in pairs:
            # Acceleration with w_a, speed at the R-SG-SPEED window, on the same cleaned positions.
            _, acc = kinematics_on_segments(hr.kin_x, hr.kin_y, hr.kin_jumps, hd.fs, w_speed, w_a, cfg.savgol_polyorder)
            _, runs = acc_band_runs(acc, hr.valid, c_a, hd.fs)
            for r in runs:
                counts[r["class"]][r["sign"]]["runs"] += 1
                counts[r["class"]][r["sign"]]["samples"] += r["samples"]
            v = acc[hr.valid & np.isfinite(acc)]
            accs.append(v)
            # Diagnostic only: speed (and distance) with the same window w_a.
            sp_a, _ = kinematics_on_segments(hr.kin_x, hr.kin_y, hr.kin_jumps, hd.fs, w_a, w_a, cfg.savgol_polyorder)
            diag_speed.append(sp_a)
        a = np.concatenate(accs) if accs else np.array([])
        alt = _per_player(pairs, diag_speed)
        dv, dd, rows = [], [], []
        for pid in sorted(base):
            b, x = base[pid], alt.get(pid, {})
            ch_v = (x.get("max_ms", np.nan) - b["max_ms"]) * 3.6 if np.isfinite(b["max_ms"]) else np.nan
            ch_d = 100.0 * (x.get("dist_m", np.nan) - b["dist_m"]) / b["dist_m"] if b["dist_m"] > 0 else np.nan
            dv.append(ch_v)
            dd.append(ch_d)
            rows.append({"object_id": pid, "max_speed_change_kmh": None if not np.isfinite(ch_v) else round(float(ch_v), 4),
                         "distance_change_pct": None if not np.isfinite(ch_d) else round(float(ch_d), 4)})
        dv_a, dd_a = np.abs(np.array(dv, float)), np.abs(np.array(dd, float))
        windows.append({
            "fraction_of_fs": f_a, "window_samples": int(w_a), "exceedances": counts,
            "noise_runs": sum(counts["noise"][s]["runs"] for s in counts["noise"]), "noise_samples": sum(counts["noise"][s]["samples"] for s in counts["noise"]),
            "approx_runs": sum(counts["approx"][s]["runs"] for s in counts["approx"]), "approx_samples": sum(counts["approx"][s]["samples"] for s in counts["approx"]),
            "valid_acc_samples": int(len(a)),
            "acc_p0_1_ms2": float(np.quantile(a, 0.001)) if len(a) else None, "acc_p99_9_ms2": float(np.quantile(a, 0.999)) if len(a) else None,
            "acc_min_ms2": float(a.min()) if len(a) else None, "acc_max_ms2": float(a.max()) if len(a) else None,
            "diagnostic_max_speed_change_kmh": {"median_abs": float(np.nanmedian(dv_a)) if np.isfinite(dv_a).any() else None,
                                                "max_abs": float(np.nanmax(dv_a)) if np.isfinite(dv_a).any() else None},
            "diagnostic_distance_change_pct": {"median_abs": float(np.nanmedian(dd_a)) if np.isfinite(dd_a).any() else None,
                                               "max_abs": float(np.nanmax(dd_a)) if np.isfinite(dd_a).any() else None},
            "per_player": rows,
        })
    return {"available": True, "speed_window_samples": int(w_speed), "fs_hz": fs, "current_fraction_of_fs": cfg.acc_window_fraction_of_fs,
            "note": "Diagnostic speed and distance changes are never used in results; the mask, maximum speeds and distances do not depend on the acceleration window.",
            "windows": windows}


def sensitivity_table(res: dict) -> pd.DataFrame:
    """One row per window (the small CSV / Markdown table of the match's report folder)."""
    rows = []
    for w in res.get("windows", []):
        e = w["exceedances"]
        rows.append({
            "window_samples": w["window_samples"], "fraction_of_fs": w["fraction_of_fs"],
            "noise_runs_acc": e["noise"]["acceleration"]["runs"], "noise_runs_dec": e["noise"]["deceleration"]["runs"],
            "noise_samples": w["noise_samples"],
            "approx_runs_acc": e["approx"]["acceleration"]["runs"], "approx_runs_dec": e["approx"]["deceleration"]["runs"],
            "approx_samples": w["approx_samples"],
            "acc_p0_1_ms2": _r(w["acc_p0_1_ms2"]), "acc_p99_9_ms2": _r(w["acc_p99_9_ms2"]), "acc_min_ms2": _r(w["acc_min_ms2"]), "acc_max_ms2": _r(w["acc_max_ms2"]),
            "diag_max_speed_change_kmh_median": _r(w["diagnostic_max_speed_change_kmh"]["median_abs"], 3),
            "diag_max_speed_change_kmh_max": _r(w["diagnostic_max_speed_change_kmh"]["max_abs"], 3),
            "diag_distance_change_pct_median": _r(w["diagnostic_distance_change_pct"]["median_abs"], 3),
            "diag_distance_change_pct_max": _r(w["diagnostic_distance_change_pct"]["max_abs"], 3),
        })
    return pd.DataFrame(rows)


def _r(v, nd: int = 2):
    return None if v is None else round(float(v), nd)


def sensitivity_markdown(res: dict, match_id: str) -> str:
    from .report import _table

    t = sensitivity_table(res)
    head = (f"# Acceleration window sensitivity - match {match_id}\n\n"
            f"`R-ACC-WINDOW-TEST` (guidelines v4, section 7.3). Acceleration recomputed on the same cleaned positions with "
            f"windows of {', '.join(str(int(w)) for w in t['window_samples'])} samples; speed at {res['speed_window_samples']} samples. "
            f"Current choice: {res['current_fraction_of_fs']:g} x fs (provisional; the team chooses after the v4 run). "
            "Exceedances of the band -8.5..+7.5 m/s2: noise <= 0.2 s, approx > 0.2 s (never excluded). "
            "The last four columns are a **diagnostic only** (speed and distance recomputed with the same window; never used in results).\n\n")
    cols = ["Window (samples)", "Noise runs acc / dec", "Noise samples", "Approx runs acc / dec", "Approx samples", "p0.1 / p99.9 (m/s2)",
            "min / max (m/s2)", "Max speed change median / max (km/h)", "Distance change median / max (%)"]
    rows = [[int(r.window_samples), f"{r.noise_runs_acc} / {r.noise_runs_dec}", r.noise_samples, f"{r.approx_runs_acc} / {r.approx_runs_dec}", r.approx_samples,
             f"{r.acc_p0_1_ms2} / {r.acc_p99_9_ms2}", f"{r.acc_min_ms2} / {r.acc_max_ms2}",
             f"{r.diag_max_speed_change_kmh_median} / {r.diag_max_speed_change_kmh_max}", f"{r.diag_distance_change_pct_median} / {r.diag_distance_change_pct_max}"]
            for r in t.itertuples(index=False)]
    return head + _table(rows, cols) + "\n"
