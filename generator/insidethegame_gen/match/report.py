"""Quick sanity statistics of a generated match, printed by the generator after each match.

Computed only from the written files, the way any reader would: Savitzky-Golay speed
(order 2, window 13 at 25 Hz, guidelines 7.2) per continuous player track, distance =
sum(speed * dt), top speed, sprints (>= 25.2 km/h for >= 1 s; a sanity number only, the
effort thresholds are not used for anything else), tangential acceleration range, event
counts, passes and shots per team, ball-in-play share, possession share.

This is **not** the Linesman realism report: it bypasses the data pipeline (no validity mask,
no event location). The full Linesman report runs the synthetic match and its replicas
through the same preparation pipeline as the DFL matches and compares them with the DFL
reference (KS distance, median rule, traffic light): ``data_pipeline/insidethegame_data/realism.py``
(``python -m insidethegame_data.realism SYN-001 --replicas 6``), results in ``reports/realism/``.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.signal import savgol_filter

from .pitch import FS

SPRINT_MS = 7.0  # 25.2 km/h, Varley et al. (2017); sanity count only
ZONES_KMH = np.array([0.0, 7.0, 15.0, 20.0, 25.0, 1e9])  # FIFA speed zones (reporting only)
PASS_TYPES = ("Play",)
RESTART_TYPES = ("KickOff", "ThrowIn", "GoalKick", "CornerKick", "FreeKick")


def player_kinematics(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Speed and tangential acceleration (Savitzky-Golay, order 2, 13 samples, central)."""
    w = 13
    if x.size < w:
        return np.zeros(x.size), np.zeros(x.size)
    dt = 1.0 / FS
    vx = savgol_filter(x, w, 2, deriv=1, delta=dt, mode="interp")
    vy = savgol_filter(y, w, 2, deriv=1, delta=dt, mode="interp")
    ax = savgol_filter(vx, w, 2, deriv=1, delta=dt, mode="interp")
    ay = savgol_filter(vy, w, 2, deriv=1, delta=dt, mode="interp")
    sp = np.hypot(vx, vy)
    at = (vx * ax + vy * ay) / np.maximum(sp, 1e-6)
    at[sp < 0.5] = 0.0
    return sp, at


def _runs(mask: np.ndarray, min_len: int) -> int:
    if not mask.any():
        return 0
    d = np.diff(np.r_[0, mask.astype(int), 0])
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return int(np.sum((ends - starts) >= min_len))


def realism_report(match_dir) -> dict:
    match_dir = Path(match_dir)
    table = pq.read_table(match_dir / "positions.parquet")
    cols = {c: table.column(c).to_numpy() if c in ("period", "frame", "x_m", "y_m", "z_m") else
            np.asarray(table.column(c).to_pylist()) for c in
            ("period", "frame", "object_id", "team_side", "x_m", "y_m", "z_m", "ball_status", "ball_possession")}
    doc = json.loads((match_dir / "events.json").read_text(encoding="utf-8"))
    events = doc["events"]
    players = {}
    for oid in np.unique(cols["object_id"]):
        if oid == "BALL":
            continue
        m = cols["object_id"] == oid
        dist_m, top, sprints, frames, amin, amax = 0.0, 0.0, 0, 0, 0.0, 0.0
        zones = np.zeros(len(ZONES_KMH) - 1)
        for period in np.unique(cols["period"][m]):
            mm = m & (cols["period"] == period)
            order = np.argsort(cols["frame"][mm], kind="stable")
            x = cols["x_m"][mm][order].astype(float)
            y = cols["y_m"][mm][order].astype(float)
            sp, at = player_kinematics(x, y)
            dist_m += float(np.sum(sp) / FS)
            idx = np.clip(np.searchsorted(ZONES_KMH, sp * 3.6, side="right") - 1, 0, len(ZONES_KMH) - 2)
            zones += np.bincount(idx, weights=sp / FS, minlength=len(ZONES_KMH) - 1)
            top = max(top, float(sp.max()) if sp.size else 0.0)
            sprints += _runs(sp >= SPRINT_MS, FS)
            frames += x.size
            if at.size:
                amin, amax = min(amin, float(at.min())), max(amax, float(at.max()))
        players[str(oid)] = {"team_side": str(cols["team_side"][m][0]), "minutes": round(frames / FS / 60, 1),
                             "distance_km": round(dist_m / 1000, 2), "top_speed_kmh": round(top * 3.6, 1),
                             "sprints": sprints, "acc_min": round(amin, 2), "acc_max": round(amax, 2),
                             "speed_zones_km": [round(z / 1000, 3) for z in zones]}
    ball = cols["object_id"] == "BALL"
    live = cols["ball_status"][ball] == "live"
    poss = cols["ball_possession"][ball]
    bx, by, bz = cols["x_m"][ball], cols["y_m"][ball], cols["z_m"][ball]
    bper = cols["period"][ball]
    ball_speed_max = 0.0
    for period in np.unique(bper):
        mm = bper == period
        v = np.hypot(np.diff(bx[mm]), np.diff(by[mm])) * FS
        lv = live[mm][1:] & live[mm][:-1]
        if v.size:
            ball_speed_max = max(ball_speed_max, float(np.max(np.where(lv, v, 0))))
    types = Counter(e["event_type"] for e in events)
    per_team = {}
    for side in ("home", "away"):
        evs = [e for e in events if e["team_side"] == side]
        passes = [e for e in evs if e["event_type"] == "Play" or (e["event_type"] in RESTART_TYPES and e["sub_type"] == "Play")]
        ok = sum(1 for e in passes if e["attributes"].get("Evaluation") == "successfullyCompleted")
        shots = [e for e in evs if e["event_type"] == "ShotAtGoal"
                 or (e["event_type"] in RESTART_TYPES and (e["sub_type"] or "").startswith("ShotAtGoal"))]
        goals = sum(1 for e in shots if (e["sub_type"] or "").endswith("SuccessfulShot"))
        per_team[side] = {"passes": len(passes), "pass_success_pct": round(100 * ok / max(len(passes), 1), 1),
                          "shots": len(shots), "goals": goals,
                          "possession_pct": round(100 * float(np.mean(poss[live] == side)) if live.any() else 0.0, 1)}
    goals = {side: sum(1 for e in events if (e["sub_type"] or "").endswith("SuccessfulShot") and e["team_side"] == side)
             for side in ("home", "away")}
    gk_ids = _goalkeepers(cols)
    outfield = [p for pid, p in players.items() if p["minutes"] > 85 and pid not in gk_ids]
    gks = [p for pid, p in players.items() if pid in gk_ids]
    full = [p["distance_km"] for p in outfield]
    return {
        "match_id": doc["match_id"],
        "score": goals,
        "frames": int(ball.sum()),
        "duration_min": {int(p): round(float(np.sum(bper == p)) / FS / 60, 2) for p in np.unique(bper)},
        "ball_in_play_pct": round(100 * float(np.mean(live)), 1),
        "ball_speed_max_live_kmh": round(ball_speed_max * 3.6, 1),
        "event_counts": dict(sorted(types.items())),
        "teams": per_team,
        "outfield_full_match_distance_km": {"n": len(full), "median": round(float(np.median(full)), 2) if full else None,
                                            "min": min(full) if full else None, "max": max(full) if full else None},
        "goalkeepers_distance_km": [p["distance_km"] for p in gks],
        "top_speed_kmh": {"max": max(p["top_speed_kmh"] for p in players.values()),
                          "median": round(float(np.median([p["top_speed_kmh"] for p in players.values()])), 1)},
        "sprints_per_full_match_outfield": {"median": float(np.median([p["sprints"] for p in outfield])) if outfield else None},
        "acc_range": [min(p["acc_min"] for p in players.values()), max(p["acc_max"] for p in players.values())],
        "players": players,
    }


def _goalkeepers(cols) -> set[str]:
    """Per team and period, the player whose mean position is deepest (closest to a goal line)."""
    out = set()
    for period in np.unique(cols["period"]):
        for side in ("home", "away"):
            m = (cols["period"] == period) & (cols["team_side"] == side)
            ids = cols["object_id"][m]
            xs = cols["x_m"][m].astype(float)
            best, bx = None, -1.0
            for oid in np.unique(ids):
                mx = abs(float(np.mean(xs[ids == oid])))
                if mx > bx:
                    best, bx = str(oid), mx
            if best is not None:
                out.add(best)
    return out
