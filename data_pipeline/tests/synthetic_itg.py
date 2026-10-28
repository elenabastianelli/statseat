"""Tiny hand-made match in **our synthetic format** (``synthetic/<id>/``) for tests. No real data.

Deliberately different from DFL and from SYN-001 so nothing can be hard-coded:
pitch 100 x 64 m, centre origin, 25 Hz, two periods of 20 s (frames from 10000 and
100000), six players and the ball.

* Period 1: home attacks +x (home goalkeeper at x = -45); period 2: sides switched.
* The ball is dead for the first second of each period, then live. In period 1 the
  home centre back passes to the striker at t = 5 s, who shoots into the goal at
  t = 8 s; the ball is dead after it crosses the line, is put back on the centre spot
  and play restarts with a kickoff at t = 14 s (``R-GOAL``: a goal is confirmed by the
  next kickoff). Period 2 mirrors the ball path; its goal crossing has a kickoff after
  it but no annotated shot (a goal crossing without an annotated goal).
* Events carry a +0.3 s annotation delay. The kickoffs have **no** ``TeamLeft`` /
  ``TeamRight`` (the adapter infers them from the goalkeepers), the duel gives its
  winning team as ``"away"`` (mapped to the team ID), and one ``Delete`` event is
  included (dropped at load).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

FS = 25.0
L, W = 100.0, 64.0
N0 = {1: 10000, 2: 100000}
DURATION_S = 20.0
T0 = {1: datetime(2026, 10, 24, 14, 0, 0, tzinfo=timezone.utc), 2: datetime(2026, 10, 24, 15, 0, 0, tzinfo=timezone.utc)}
HOME, AWAY = "HOM", "AWY"
DELAY_S = 0.3
PLAYERS = [  # id, team side, position, starting
    ("HOM-01", "home", "GK", True), ("HOM-04", "home", "CB", True), ("HOM-09", "home", "ST", True),
    ("AWY-01", "away", "GK", True), ("AWY-05", "away", "CB", True), ("AWY-09", "away", "ST", True),
    ("HOM-12", "home", "GK", False),
]
DEAD_S = 1.0
PASS_T, RECEIVE_T, SHOT_T, GOAL_T = 5.0, 6.5, 8.0, 9.2
RESTART_T = 14.0  # kickoff after the goal


@dataclass
class SynthFolder:
    repo: Path
    folder: Path
    match_id: str


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def player_paths(t: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Period-1 positions (home attacks +x); period 2 mirrors them."""
    return {
        "HOM-01": (-45 + 0.5 * np.sin(t), np.zeros_like(t)),
        "HOM-04": (-20 + 2 * np.sin(0.3 * t), np.full_like(t, 5.0)),
        "HOM-09": (10 + 3 * np.sin(0.25 * t), np.full_like(t, -3.0)),
        "AWY-01": (45 - 0.5 * np.sin(t), np.zeros_like(t)),
        "AWY-05": (20 - 2 * np.sin(0.3 * t), np.full_like(t, -5.0)),
        "AWY-09": (-10 - 3 * np.sin(0.25 * t), np.full_like(t, 3.0)),
    }


def ball_path(t: np.ndarray, pp: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Ball x, y, z and status for period 1: kickoff, carry to the centre back, pass, shot, goal."""
    cb, st = pp["HOM-04"], pp["HOM-09"]
    bx, by = np.zeros_like(t), np.zeros_like(t)
    k = lambda s: int(round(s * FS))  # noqa: E731
    i_carry, i_pass, i_recv, i_shot = k(DEAD_S), k(PASS_T), k(RECEIVE_T), k(SHOT_T)
    # Carried from the centre spot towards the centre back, then at his feet (0.5 m in front).
    w = np.clip((t - DEAD_S) / (PASS_T - 1.0 - DEAD_S), 0, 1)
    seg = slice(i_carry, i_pass)
    bx[seg] = (1 - w[seg]) * 0.0 + w[seg] * (cb[0][seg] + 0.5)
    by[seg] = (1 - w[seg]) * 0.0 + w[seg] * cb[1][seg]
    # Pass: straight line to the striker.
    p0 = np.array([cb[0][i_pass - 1] + 0.5, cb[1][i_pass - 1]])
    p1 = np.array([st[0][i_recv] - 0.5, st[1][i_recv]])
    a = (t[i_pass:i_recv] - PASS_T) / (RECEIVE_T - PASS_T)
    bx[i_pass:i_recv], by[i_pass:i_recv] = p0[0] + a * (p1[0] - p0[0]), p0[1] + a * (p1[1] - p0[1])
    # At the striker's feet.
    bx[i_recv:i_shot], by[i_recv:i_shot] = st[0][i_recv:i_shot] + 0.5, st[1][i_recv:i_shot]
    # Shot into the goal (between the posts, low), then beyond the line.
    s0 = np.array([bx[i_shot - 1], by[i_shot - 1]])
    s1 = np.array([L / 2 + 1.0, 1.0])
    a = np.clip((t[i_shot:] - SHOT_T) / (GOAL_T - SHOT_T), 0, None)
    bx[i_shot:], by[i_shot:] = s0[0] + a * (s1[0] - s0[0]), s0[1] + a * (s1[1] - s0[1])
    bx = np.minimum(bx, L / 2 + 1.5)
    z = np.full_like(t, 0.11)
    status = np.where((t >= DEAD_S) & (bx <= L / 2 + 1.2), "live", "dead")  # live until just beyond the goal line
    # Back on the centre spot while dead, then the kickoff after the goal: the ball rolls slowly towards -x.
    back = t >= RESTART_T - 2.0
    bx[back] = np.where(t[back] >= RESTART_T, -3.0 * (t[back] - RESTART_T), 0.0)
    by[back] = 0.0
    status = np.where(t >= RESTART_T, "live", status)
    return bx, by, z, status


def write_synthetic_folder(repo: Path, match_id: str = "SYN-T01", true_frame_shift: int = 0) -> SynthFolder:
    folder = Path(repo) / "synthetic" / match_id
    folder.mkdir(parents=True, exist_ok=True)
    lineup = {"home": [], "away": []}
    for pid, side, pos, start in PLAYERS:
        lineup[side].append({"player_id": pid, "shirt_number": int(pid[-2:]), "starting": start, "playing_position": pos, "captain": False})
    info = {
        "match_id": match_id, "universe_id": "TEST-0", "competition_id": "TST-1", "season": "2026/27", "matchday": 1,
        "planned_kickoff": "2026-10-24T15:00:00+01:00", "home_team_id": HOME, "guest_team_id": AWAY,
        "environment": {"stadium_id": "STA-TEST", "pitch_x_m": L, "pitch_y_m": W},
        "teams": [{"role": "home", "team_id": HOME, "formation": "4-4-2", "kit": "home", "lineup": lineup["home"], "staff": []},
                  {"role": "guest", "team_id": AWAY, "formation": "4-2-3-1", "kit": "away", "lineup": lineup["away"], "staff": []}],
        "officials": [], "result": None,
    }
    (folder / "match_info.json").write_text(json.dumps(info, indent=2))

    n = int(DURATION_S * FS)
    t = np.arange(n) / FS
    rows = []
    for period in (1, 2):
        sgn = 1.0 if period == 1 else -1.0
        pp = player_paths(t)
        bx, by, bz, status = ball_path(t, pp)
        times = pd.to_datetime([T0[period] + timedelta(seconds=float(s)) for s in t], utc=True)
        frames = N0[period] + np.arange(n)
        poss = np.where(status == "live", "home", "none")  # the home team builds up and scores in both periods (mirrored in period 2)
        for pid, (x, y) in pp.items():
            side = "home" if pid.startswith("HOM") else "away"
            rows.append(pd.DataFrame({"period": period, "frame": frames, "time_utc": times, "object_id": pid, "object_type": "player", "team_side": side,
                                      "x_m": (sgn * x).astype(np.float32), "y_m": (sgn * y).astype(np.float32), "z_m": np.float32(np.nan),
                                      "ball_status": status, "ball_possession": poss}))
        rows.append(pd.DataFrame({"period": period, "frame": frames, "time_utc": times, "object_id": "BALL", "object_type": "ball", "team_side": "none",
                                  "x_m": (sgn * bx).astype(np.float32), "y_m": (sgn * by).astype(np.float32), "z_m": bz.astype(np.float32),
                                  "ball_status": status, "ball_possession": poss}))
    pos = pd.concat(rows, ignore_index=True).sort_values(["period", "frame", "object_id"], kind="mergesort").reset_index(drop=True)
    pos["period"] = pos["period"].astype(np.int64)
    pos["frame"] = pos["frame"].astype(np.int64)
    pos.to_parquet(folder / "positions.parquet", index=False)

    def ev(eid, etype, period, t_true, side=None, player=None, x=None, y=None, sub=None, attrs=None):
        frame = N0[period] + int(round(t_true * FS))
        return {"event_id": eid, "event_type": etype, "sub_type": sub, "time_utc": _iso(T0[period] + timedelta(seconds=t_true + DELAY_S)),
                "period": period, "team_side": side, "player_id": player, "x_m": x, "y_m": y, "attributes": attrs or {},
                "true_frame": frame + true_frame_shift}

    pp = player_paths(t)
    i_pass, i_shot = int(PASS_T * FS), int(SHOT_T * FS)
    events = [
        ev("E1", "KickOff", 1, DEAD_S, "home", "HOM-09", 0.0, 0.0),
        ev("E2", "Play", 1, PASS_T, "home", "HOM-04", float(pp["HOM-04"][0][i_pass]), 5.0, "Pass",
           {"Evaluation": "successfullyCompleted", "Height": "flat", "Recipient": "HOM-09"}),
        ev("E3", "TacklingGame", 1, 7.0, "away", "AWY-05", 15.0, -4.0, None, {"WinnerTeam": "away", "Loser": "HOM-09", "LoserTeam": "home"}),
        ev("E4", "ShotAtGoal", 1, SHOT_T, "home", "HOM-09", float(pp["HOM-09"][0][i_shot]), -3.0, "SuccessfulShot", {"TypeOfShot": "rightLeg"}),
        ev("E5", "Caution", 1, 12.0, "away", "AWY-05", None, None, None, {"CardColor": "yellow"}),
        ev("E10", "KickOff", 1, RESTART_T, "away", "AWY-09", 0.0, 0.0),
        ev("E6", "FinalWhistle", 1, 19.5),
        ev("E7", "KickOff", 2, DEAD_S, "away", "AWY-09", 0.0, 0.0),
        ev("E11", "KickOff", 2, RESTART_T, "away", "AWY-09", 0.0, 0.0),
        ev("E8", "Delete", 2, 5.0),
        ev("E9", "FinalWhistle", 2, 19.5),
    ]
    doc = {"match_id": match_id, "universe_id": "TEST-0", "generator_version": "test", "seed": 0, "events": events}
    (folder / "events.json").write_text(json.dumps(doc, indent=2))
    return SynthFolder(repo=Path(repo), folder=folder, match_id=match_id)
