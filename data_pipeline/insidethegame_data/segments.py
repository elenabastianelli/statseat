"""Segmentation of trajectories (guidelines section 4, step 4).

A segment is a run of consecutive samples of one object that never crosses:
* a FrameSet boundary (half time, substitution: every player/half has its own FrameSet),
* a gap in frame numbering (dN != 1) or an irregular time step,
* a missing (NaN) position,
* the start or end of a long off-pitch excursion (players beyond the out-of-bounds
  margin for at least ``min_offpitch_s``), which is how a player leaving the pitch
  for medical treatment and re-entering shows up in continuous tracking.

Red cards would end a FrameSet early, so they are covered by the FrameSet rule.
"""

from __future__ import annotations

import numpy as np


def run_lengths(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Start indices and lengths of True runs in a boolean array."""
    m = np.concatenate(([False], mask.astype(bool), [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return starts, ends - starts


def long_runs_mask(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Keep only True runs of at least ``min_len`` samples."""
    out = np.zeros(len(mask), dtype=bool)
    for s, ln in zip(*run_lengths(mask)):
        if ln >= min_len:
            out[s : s + ln] = True
    return out


def segment_ids(
    group: np.ndarray,
    n: np.ndarray,
    t_s: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    fs_hz: float,
    offpitch_long: np.ndarray | None = None,
    dt_frac: float = 0.5,
) -> np.ndarray:
    """Assign a segment id to every sample (``-1`` for NaN positions).

    Inputs must be sorted by (group, n). ``group`` identifies the FrameSet.
    ``offpitch_long`` marks samples inside a long off-pitch excursion.
    """
    size = len(n)
    if size == 0:
        return np.zeros(0, dtype=np.int64)
    valid = np.isfinite(x) & np.isfinite(y)
    brk = np.zeros(size, dtype=bool)
    brk[0] = True
    dt_nom = 1.0 / fs_hz
    same_group = group[1:] == group[:-1]
    dn = np.diff(n)
    dt = np.diff(t_s)
    bad_step = (dn != 1) | (np.abs(dt - dt_nom) > dt_frac * dt_nom)
    brk[1:] = ~same_group | bad_step | ~valid[:-1]
    if offpitch_long is not None:
        op = offpitch_long.astype(bool)
        brk[1:] |= op[1:] != op[:-1]
    seg = np.cumsum(brk) - 1
    seg = seg.astype(np.int64)
    seg[~valid] = -1
    # Re-number valid segments densely so ids are 0..k-1.
    uniq, inv = np.unique(seg[valid], return_inverse=True)
    seg[valid] = inv
    return seg
