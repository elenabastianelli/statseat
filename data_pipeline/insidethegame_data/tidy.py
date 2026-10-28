"""Handling rules R-SORT, R-DEDUP, R-RESAMPLE and R-MISSING of section 5.1 (guidelines v4).

Works on the tidy long table produced by any adapter (one row per object per
frame). Every function returns the handled table plus a log entry
``{"rule", "count", "examples"}``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def log_line(rule: str, count: int, examples=None, note: str = "") -> dict:
    return {"rule": rule, "count": int(count), "examples": list(examples or [])[:3], "note": note}


def sort_rows(frames: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """R-SORT: sort by (period, object, frame). Counts the rows that were out of order."""
    key = ["half", "frameset_idx", "n"]
    g = frames["frameset_idx"].to_numpy()
    n = frames["n"].to_numpy()
    same = g[1:] == g[:-1]
    n_bad = int(((np.diff(n) <= 0) & same).sum()) if len(n) > 1 else 0
    ex = []
    if n_bad:
        idx = np.flatnonzero((np.diff(n) <= 0) & same)[:3]
        ex = [f"{frames['object_id'].iloc[i]} N={int(n[i])}->{int(n[i + 1])}" for i in idx]
    out = frames.sort_values(key, kind="mergesort").reset_index(drop=True)
    return out, log_line("R-SORT sort tracking rows and events", n_bad, ex, "rows found out of (object, frame) order in file order")


def deduplicate(frames: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """R-DEDUP: keep the first duplicate; if duplicates differ, keep the one closest to its neighbours.

    Input must be sorted by (half, frameset_idx, n).
    """
    dup_mask = frames.duplicated(["half", "object_id", "n"], keep=False).to_numpy()
    if not dup_mask.any():
        return frames, log_line("R-DEDUP de-duplicate", 0)
    f = frames.copy()
    f["_row"] = np.arange(len(f))
    keep = np.ones(len(f), dtype=bool)
    n_removed, examples = 0, []
    for (_, obj, n), grp in f.loc[dup_mask].groupby(["half", "object_id", "n"], observed=True):
        rows = grp["_row"].to_numpy()
        xy = grp[["x", "y"]].to_numpy()
        if np.allclose(xy, xy[0], equal_nan=True):
            best = rows[0]
        else:
            # Neighbours: previous and next non-duplicate rows of the same object.
            lo, hi = rows.min() - 1, rows.max() + 1
            neigh = [r for r in (lo, hi) if 0 <= r < len(f) and f["object_id"].iat[r] == obj and not dup_mask[r]]
            if neigh:
                ref = f[["x", "y"]].to_numpy()[neigh].mean(axis=0)
                best = rows[int(np.argmin(np.hypot(xy[:, 0] - ref[0], xy[:, 1] - ref[1])))]
            else:
                best = rows[0]
        keep[rows] = False
        keep[best] = True
        n_removed += len(rows) - 1
        if len(examples) < 3:
            examples.append(f"{obj} N={int(n)} x{len(rows)}")
    out = f.loc[keep].drop(columns="_row").reset_index(drop=True)
    return out, log_line("R-DEDUP de-duplicate", n_removed, examples, "duplicate (object, period, frame) rows removed")


def sampling_irregularity(frames: pd.DataFrame, tolerance_frac: float) -> dict[int, dict]:
    """Per period: share of consecutive-sample dT outside +-tolerance of the median dT."""
    out = {}
    t = (frames["t_utc"] - frames["t_utc"].min()).dt.total_seconds().to_numpy()
    g = frames["frameset_idx"].to_numpy()
    h = frames["half"].to_numpy()
    same = g[1:] == g[:-1]
    dt = np.diff(t)[same]
    hh = h[1:][same]
    for half in sorted(np.unique(hh)):
        d = dt[hh == half]
        med = float(np.median(d))
        out[int(half)] = {"dt_median_s": med, "fs_hz": 1.0 / med, "n_steps": int(len(d)),
                          "n_irregular": int((np.abs(d - med) > tolerance_frac * med).sum()),
                          "dt_min_s": float(d.min()), "dt_max_s": float(d.max())}
    return out


def resample_track(t_s: np.ndarray, values: dict[str, np.ndarray], fs_hz: float, t0: float | None = None) -> tuple[np.ndarray, dict[str, np.ndarray], int]:
    """R-RESAMPLE: linear resampling onto the regular grid ``t0 + k / fs``.

    A grid point is interpolated only when it has a measured sample on each side
    within 1.5 sample periods; otherwise it becomes a gap (NaN). Returns the grid,
    the resampled channels and the number of grid points that became gaps.
    """
    t_s = np.asarray(t_s, float)
    t0 = t_s[0] if t0 is None else t0
    k = np.arange(int(np.floor((t_s[-1] - t0) * fs_hz + 1e-6)) + 1)
    grid = t0 + k / fs_hz
    j = np.clip(np.searchsorted(t_s, grid), 1, len(t_s) - 1)
    left, right = t_s[j - 1], t_s[j]
    exact = np.isclose(grid, t_s[np.clip(np.searchsorted(t_s, grid), 0, len(t_s) - 1)], atol=1e-9)
    ok = exact | ((grid - left) <= 1.5 / fs_hz) & ((right - grid) <= 1.5 / fs_hz)
    out = {}
    for name, v in values.items():
        r = np.interp(grid, t_s, np.asarray(v, float))
        r[~ok] = np.nan
        out[name] = r
    return grid, out, int((~ok).sum())


def resample_if_irregular(frames: pd.DataFrame, fs_hz: dict[int, float], tolerance_frac: float) -> tuple[pd.DataFrame, dict]:
    """R-RESAMPLE applied to the whole table only when some period is irregular."""
    irr = sampling_irregularity(frames, tolerance_frac)
    n_irr = sum(v["n_irregular"] for v in irr.values())
    if n_irr == 0:
        return frames, log_line("R-RESAMPLE resample irregular sampling", 0, note="dT never deviates more than 5 % from the median; no resampling")
    parts, n_gaps = [], 0
    for fsi, g in frames.groupby("frameset_idx", sort=True):
        h = int(g["half"].iat[0])
        t = (g["t_utc"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()
        cols = {c: g[c].to_numpy(float) for c in ("x", "y", "z", "d_m", "s_ms", "a_ms2", "ball_status", "ball_possession")}
        grid, res, gaps = resample_track(t, cols, fs_hz[h])
        n_gaps += gaps
        # Status / possession are categorical: take the nearest original sample instead of interpolating.
        near = np.clip(np.searchsorted(t, grid), 0, len(t) - 1)
        for c in ("ball_status", "ball_possession"):
            res[c] = cols[c][near]
        df = pd.DataFrame(res)
        df["t_utc"] = pd.to_datetime(grid, unit="s", utc=True)
        df["n"] = int(g["n"].iat[0]) + np.arange(len(grid))
        for c in ("frameset_idx", "half", "object_id", "team_id", "is_ball", "minute"):
            df[c] = g[c].iat[0]
        parts.append(df)
    out = pd.concat(parts, ignore_index=True)[frames.columns]
    return out, log_line("R-RESAMPLE resample irregular sampling", n_irr, note=f"{n_irr} irregular steps; {n_gaps} grid points became gaps")


def missing_samples(frames: pd.DataFrame) -> dict:
    """R-MISSING: missing frames inside each track and NaN positions (both become gaps)."""
    agg = frames.groupby("frameset_idx").agg(n0=("n", "min"), n1=("n", "max"), cnt=("n", "size"))
    missing_frames = int((agg["n1"] - agg["n0"] + 1 - agg["cnt"]).sum())
    nan_xy = int(frames[["x", "y"]].isna().any(axis=1).sum())
    return log_line("R-MISSING missing positions", missing_frames + nan_xy, note=f"{missing_frames} missing frames inside tracks, {nan_xy} NaN positions; all become gaps (section 6.3)")
