"""Derivatives of positions (guidelines v4, sections 7.2 and 7.3).

Savitzky-Golay is the only filter in the data path (order 2, central derivative,
``mode="interp"``), applied per continuous segment:

* ``R-SG-SPEED``: velocity with a window of odd(round(fs * 0.5)) samples (13 at 25 Hz);
* ``R-SG-ACC``: acceleration = the same filter applied to (vx, vy) with its own window
  ``acc_window`` (13, 19 or 25 samples at 25 Hz, chosen with ``R-ACC-WINDOW-TEST``). A
  segment shorter than ``acc_window`` has a speed but no acceleration.

The zero-phase Butterworth function is kept **only** to repeat the filter comparison
of section 7.2 when a new provider is introduced; it is never used in the data path.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt, savgol_filter


def savgol_kinematics(x: np.ndarray, y: np.ndarray, fs_hz: float, window: int, polyorder: int = 2,
                      acc_window: int | None = None) -> dict[str, np.ndarray]:
    """Velocity, speed, acceleration magnitude and tangential acceleration of one segment.

    ``window`` is the velocity window (``R-SG-SPEED``); ``acc_window`` the acceleration window
    (``R-SG-ACC``, default: the same as ``window``). Returns NaN arrays (and ``filtered=False``) if
    the segment is shorter than ``window``; the acceleration channels stay NaN (``acc_filtered=False``)
    if it is shorter than ``acc_window``.
    """
    n = len(x)
    wa = window if acc_window is None else int(acc_window)
    out = {k: np.full(n, np.nan) for k in ("vx", "vy", "speed", "ax", "ay", "acc", "acc_tan")}
    out["filtered"] = np.zeros(n, dtype=bool)
    out["acc_filtered"] = np.zeros(n, dtype=bool)
    if n < window:
        return out
    dt = 1.0 / fs_hz
    vx = savgol_filter(x, window_length=window, polyorder=polyorder, deriv=1, delta=dt, mode="interp")
    vy = savgol_filter(y, window_length=window, polyorder=polyorder, deriv=1, delta=dt, mode="interp")
    speed = np.hypot(vx, vy)
    out.update(vx=vx, vy=vy, speed=speed)
    out["filtered"] = np.ones(n, dtype=bool)
    if n < wa:
        return out
    ax = savgol_filter(vx, window_length=wa, polyorder=polyorder, deriv=1, delta=dt, mode="interp")
    ay = savgol_filter(vy, window_length=wa, polyorder=polyorder, deriv=1, delta=dt, mode="interp")
    with np.errstate(invalid="ignore", divide="ignore"):
        # d|v|/dt = (v . a) / |v| ; undefined at rest, set to 0 there (|v| < 1e-6 m/s).
        acc_tan = np.where(speed > 1e-6, (vx * ax + vy * ay) / speed, 0.0)
    out.update(ax=ax, ay=ay, acc=np.hypot(ax, ay), acc_tan=acc_tan)
    out["acc_filtered"] = np.ones(n, dtype=bool)
    return out


def butterworth_kinematics(x: np.ndarray, y: np.ndarray, fs_hz: float, cutoff_hz: float, order: int = 4) -> dict[str, np.ndarray]:
    """Zero-phase Butterworth low-pass on positions, then central differences."""
    n = len(x)
    b, a = butter(order, cutoff_hz, btype="low", fs=fs_hz)
    padlen = 3 * max(len(a), len(b))
    out = {k: np.full(n, np.nan) for k in ("speed", "acc", "acc_tan")}
    if n <= padlen:
        out["filtered"] = np.zeros(n, dtype=bool)
        return out
    dt = 1.0 / fs_hz
    xf = filtfilt(b, a, x)
    yf = filtfilt(b, a, y)
    vx = np.gradient(xf, dt)
    vy = np.gradient(yf, dt)
    ax = np.gradient(vx, dt)
    ay = np.gradient(vy, dt)
    speed = np.hypot(vx, vy)
    with np.errstate(invalid="ignore", divide="ignore"):
        acc_tan = np.where(speed > 1e-6, (vx * ax + vy * ay) / speed, 0.0)
    out.update(speed=speed, acc=np.hypot(ax, ay), acc_tan=acc_tan)
    out["filtered"] = np.ones(n, dtype=bool)
    return out


def step_displacement(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Backward per-frame displacement ||p_t - p_{t-1}||; first value NaN."""
    d = np.full(len(x), np.nan)
    if len(x) > 1:
        d[1:] = np.hypot(np.diff(x), np.diff(y))
    return d
