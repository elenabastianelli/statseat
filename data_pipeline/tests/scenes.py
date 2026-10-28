"""Tiny event scenes for the trajectory rules of section 9 (no real data).

Pitch 100 x 60 m (L/2 = 50: the home team attacks the goal at x = +50), 25 Hz, one period of
``N`` frames. Players stand still unless a test moves them; the ball follows a piecewise-linear
path through (frame, x, y) knots, so every knot is a velocity change (an |a| peak after the
Savitzky-Golay filter, about 2.4 x |dv| m/s2 for an instant change).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.events_sync import SyncPeriod, locate_play_events
from insidethegame_data.touches import ball_kinematics

FS = 25.0
L = 100.0
T0 = 1_000_000.0
N = 600
N0 = 1000
SIDES = {"T-H": "home", "T-A": "away"}
GK = {"G"}
OFF = {1: {"event_clock_offset_s": 2.0, "search_window_s": 5.0}}


def ball(knots, n=N):
    f = [k[0] for k in knots]
    return np.interp(np.arange(n), f, [k[1] for k in knots]), np.interp(np.arange(n), f, [k[2] for k in knots])


def period(ball_knots, players, bz=None, status=None, n=N, with_speed=True, complete=True, groups=None) -> SyncPeriod:
    """``players``: id -> (x, y, side). ``bz``: ball height (NaN = not supplied). ``status``: 1 live, 0 dead."""
    pids = list(players)
    px = np.column_stack([np.full(n, players[p][0], float) for p in pids])
    py = np.column_stack([np.full(n, players[p][1], float) for p in pids])
    bx, by = ball(ball_knots, n)
    sp = SyncPeriod(half=1, fs=FS, n=N0 + np.arange(n), t_s=T0 + np.arange(n) / FS, pids=pids, team_side=[players[p][2] for p in pids],
                    px=px, py=py, bx=bx, by=by, bz=np.full(n, np.nan) if bz is None else np.asarray(bz, float),
                    bstatus=np.ones(n) if status is None else np.asarray(status, float), home_attack_sign=1.0, complete=complete,
                    role_group=[(groups or {}).get(p) for p in pids])
    if with_speed:
        bk = ball_kinematics(sp, CFG)
        speed = np.hypot(bk["vx"], bk["vy"])
        sp.ball_valid = np.isfinite(speed) & (sp.bstatus == 1)
        sp.ball_speed = np.where(sp.ball_valid, speed, np.nan)
    return sp


def events(rows):
    """``rows``: (event_type, attrs, frame of the annotated time, optional dict of extra columns)."""
    out = []
    for i, row in enumerate(rows):
        etype, attrs, frame_time = row[:3]
        extra = row[3] if len(row) > 3 else {}
        r = {"event_id": f"E{i}", "half": 1, "event_type": etype, "attrs_json": json.dumps(attrs), "sub_types": "",
             "time_utc": pd.Timestamp(T0 + frame_time / FS, unit="s", tz="UTC"), "sync_time_utc": pd.NaT, "sync_frame": None,
             "sync_certainty": None, "sync_method": "not_located", "sync_residual_s": None, "type_check": "not_applicable"}
        r.update(extra)
        out.append(r)
    return pd.DataFrame(out)


def restart(etype: str, team: str, frame: int, sub_types: str = "", **attrs):
    """A restart already located at its dead->live transition (as ``timealign.locate_events`` does).
    ``sub_types``: e.g. ``ShotAtGoal/SuccessfulShot`` for a penalty or direct free kick that is a shot (DFL)."""
    return (etype, {"Team": team, **attrs}, frame - 2 * FS, {"sync_frame": N0 + frame, "sync_method": "dead_live_transition", "sync_certainty": 1.0,
                                                             "sync_time_utc": pd.Timestamp(T0 + frame / FS, unit="s", tz="UTC"), "sync_residual_s": 0.0,
                                                             "sub_types": sub_types})


def shot(player: str, team: str, frame: float, outcome: str = "", **attrs):
    return ("ShotAtGoal", {"Player": player, "Team": team, **attrs}, frame - 2 * FS, {"sub_types": outcome})


def locate(sp, rows, **kw):
    return locate_play_events(events(rows), {1: sp}, OFF, GK, SIDES, L, CFG, **kw)
