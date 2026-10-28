"""Gap estimation (guidelines v4, section 6.3, `R-GAP`).

A gap is estimated only when the player resumes less than ``gap_resume_max_m``
from the last valid position; otherwise it is discarded by the caller.

* gap <= 0.5 s: cubic spline (k = 3) through the measured samples around the gap
  (equivalent to ``floodlight.transforms.interpolation.interpolate_spline``; scipy
  is used so that floodlight is not a dependency);
* gap <= 2 s: Kalman smoother (forward filter + Rauch-Tung-Striebel), constant
  acceleration model, measurement noise sigma ~ 0.1 m;
* longer: straight line (a lower bound of the distance).

Estimated samples count only for distance, never for speed or peaks.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import make_interp_spline


def _context(t: np.ndarray, v: np.ndarray, known: np.ndarray, lo: int, hi: int, k: int) -> np.ndarray:
    """Indices of up to ``k`` measured samples just before ``lo`` and just after ``hi``."""
    before = np.flatnonzero(known[:lo])[-k:]
    after = hi + 1 + np.flatnonzero(known[hi + 1:])[:k]
    return np.r_[before, after]


def spline_fill(t: np.ndarray, v: np.ndarray, idx: np.ndarray, gap: np.ndarray) -> np.ndarray:
    if len(idx) < 4:
        return np.interp(t[gap], t[idx], v[idx])
    spl = make_interp_spline(t[idx], v[idx], k=3)
    return spl(t[gap])


def kalman_rts_fill(t: np.ndarray, v: np.ndarray, idx: np.ndarray, gap: np.ndarray, sigma: float, q: float) -> np.ndarray:
    """Constant-acceleration Kalman filter + RTS smoother on one coordinate.

    ``idx``: measured samples (context); ``gap``: samples to estimate. Time steps
    are taken from ``t`` (seconds).
    """
    sel = np.union1d(idx, gap)
    ts = t[sel]
    meas = np.isin(sel, idx)
    z = v[sel]
    n = len(sel)
    xs = np.zeros((n, 3)); ps = np.zeros((n, 3, 3))
    xp = np.zeros((n, 3)); pp = np.zeros((n, 3, 3)); fs = np.zeros((n, 3, 3))
    first = np.flatnonzero(meas)[0]
    x = np.array([z[first], 0.0, 0.0])
    p = np.diag([sigma**2, 25.0, 100.0])
    h = np.array([1.0, 0.0, 0.0])
    r = sigma**2
    for i in range(n):
        dt = ts[i] - ts[i - 1] if i else 0.0
        f = np.array([[1, dt, 0.5 * dt * dt], [0, 1, dt], [0, 0, 1]])
        qm = q * np.array([[dt**5 / 20, dt**4 / 8, dt**3 / 6], [dt**4 / 8, dt**3 / 3, dt**2 / 2], [dt**3 / 6, dt**2 / 2, dt]])
        x = f @ x
        p = f @ p @ f.T + qm
        xp[i], pp[i], fs[i] = x, p, f
        if meas[i]:
            s = h @ p @ h + r
            kg = p @ h / s
            x = x + kg * (z[i] - h @ x)
            p = p - np.outer(kg, h @ p)
        xs[i], ps[i] = x, p
    for i in range(n - 2, -1, -1):
        c = ps[i] @ fs[i + 1].T @ np.linalg.inv(pp[i + 1])
        xs[i] = xs[i] + c @ (xs[i + 1] - xp[i + 1])
        ps[i] = ps[i] + c @ (ps[i + 1] - pp[i + 1]) @ c.T
    return xs[np.isin(sel, gap), 0]


def estimate_gap(t: np.ndarray, x: np.ndarray, y: np.ndarray, known: np.ndarray, lo: int, hi: int, fs_hz: float, cfg) -> tuple[np.ndarray, np.ndarray, str]:
    """Estimate samples ``lo..hi`` (inclusive) of one track. ``known`` marks measured samples.

    The caller guarantees a measured sample right before ``lo`` and right after ``hi``.
    """
    gap = np.arange(lo, hi + 1)
    dur = (hi - lo + 1) / fs_hz
    k = max(2, int(round(cfg.gap_context_s * fs_hz)))
    if dur <= cfg.gap_spline_max_s:
        idx = _context(t, x, known, lo, hi, min(k, 5))
        return spline_fill(t, x, idx, gap), spline_fill(t, y, idx, gap), "cubic spline"
    if dur <= cfg.gap_kalman_max_s:
        idx = _context(t, x, known, lo, hi, k)
        return (kalman_rts_fill(t, x, idx, gap, cfg.kalman_meas_sigma_m, cfg.kalman_jerk_psd),
                kalman_rts_fill(t, y, idx, gap, cfg.kalman_meas_sigma_m, cfg.kalman_jerk_psd), "Kalman/RTS")
    a, b = lo - 1, hi + 1
    w = (t[gap] - t[a]) / (t[b] - t[a])
    return x[a] + w * (x[b] - x[a]), y[a] + w * (y[b] - y[a]), "straight line"
