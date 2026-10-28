"""Tiny array fixtures for the preparation rules (no real data)."""

from __future__ import annotations

import numpy as np

from insidethegame_data.prepare import HalfData

FS = 25.0


def make_half(xs: list[np.ndarray], ys: list[np.ndarray], n_frames: int | None = None, bstatus=None, bx=None, by=None,
              pids=None, sides=None, prov=None, half: int = 1) -> HalfData:
    """Build a HalfData from per-player position arrays (NaN outside the track)."""
    nf = n_frames or len(xs[0])
    x = np.column_stack(xs).astype(float)
    y = np.column_stack(ys).astype(float)
    npl = x.shape[1]
    t = 1_000_000.0 + np.arange(nf) / FS
    st = np.ones(nf) if bstatus is None else np.asarray(bstatus, float)
    # Default ball: moving slowly so that it is never frozen.
    bx = np.linspace(0, 10, nf) if bx is None else np.asarray(bx, float)
    by = np.linspace(0, 5, nf) if by is None else np.asarray(by, float)
    p = prov if prov is not None else np.full((nf, npl), np.nan)
    return HalfData(half=half, fs=FS, n=1000 + np.arange(nf), t_s=t, pids=pids or [f"P{i}" for i in range(npl)],
                    team_side=sides or ["home"] * npl, x=x, y=y, prov_s=p.copy(), prov_d=p.copy(), prov_a=p.copy(),
                    bx=bx, by=by, bz=np.zeros(nf), bstatus=st, bposs=np.ones(nf), ball_prov_s=np.full(nf, np.nan))


def run_x(n: int, v: float, x0: float = 0.0) -> np.ndarray:
    return x0 + v * np.arange(n) / FS
