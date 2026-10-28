"""A tiny synthetic match in the generator's format (synthetic/SYN-001/README.md), with its match profile.

Two periods of 6 minutes at 25 Hz with the 22 starters of synthetic/SYN-001/match_info.json, a 5-minute break, smooth
player paths (one winger above 25 km/h), a ball that follows waypoints, possession by blocks of 30 s, a dead ball
(corner), a 25 m/s shot and a substitution. Events use `team_side`, DFL attribute names and `sub_type`, with exact
times (no annotation delay), so the hand-written profile has a zero event clock offset. Deterministic (fixed seed);
written to a temporary repo root (`synthetic/SYN-001/` and `reports/qc/SYN-001/`), never committed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MATCH_INFO = ROOT / "synthetic" / "SYN-001" / "match_info.json"
FS = 25.0
PERIOD_S = 360.0
BREAK_S = 300.0
KO1 = pd.Timestamp("2026-10-24T14:00:00Z")
SHOT_T = 200.0  # period 1, home shot towards +x
SUB_T = 120.0  # period 2


def period_start(period: int) -> pd.Timestamp:
    return KO1 + pd.Timedelta(seconds=0.0 if period == 1 else PERIOD_S + BREAK_S)


def _lineup() -> tuple[list[dict], list[dict], dict]:
    info = json.loads(MATCH_INFO.read_text())
    starters, bench = [], []
    for team in info["teams"]:
        for p in team["lineup"]:
            (starters if p["starting"] else bench).append({**p, "team_id": team["team_id"], "role": team["role"],
                                                          "side": "home" if team["role"] == "home" else "away"})
    return starters, bench, info


def match_dir(root: Path) -> Path:
    return Path(root) / "synthetic" / "SYN-001"


def write_profile(root: Path) -> None:
    """The keys of reports/qc/<id>/match_profile.json that the engine reads (written by the pipeline for SYN-001)."""
    ko = {str(p): {"kickoff_utc": period_start(p).isoformat()} for p in (1, 2)}
    last1 = period_start(1) + pd.Timedelta(seconds=PERIOD_S - 1 / FS)
    profile = {"pitch_length_m": 105.0, "pitch_width_m": 68.0, "fs_hz": {"1": FS, "2": FS},
               "direction_per_half": {"1": {"home_attacks_positive_x": True}, "2": {"home_attacks_positive_x": False}},
               "centre_spot_in_tracking": [0.0, 0.0], "centre_spot_in_events": [0.0, 0.0], "kickoff_per_period": ko,
               "event_clock_offset": {"1": {"event_clock_offset_s": 0.0}, "2": {"event_clock_offset_s": 0.0}},
               "frame_ranges": {"per_half": {"1": {"t_last_utc": last1.isoformat()}}}}
    out = Path(root) / "reports" / "qc" / "SYN-001"
    out.mkdir(parents=True, exist_ok=True)
    (out / "match_profile.json").write_text(json.dumps(profile, indent=1))


def build(root: Path) -> Path:
    """Writes the fixture under `root` and returns `root` (use it as `load_synthetic("SYN-001", root)`)."""
    folder = match_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    write_profile(root)
    shutil.copy(MATCH_INFO, folder / "match_info.json")
    starters, bench, _ = _lineup()
    rng = np.random.default_rng(7)
    sub_out = next(p for p in starters if p["team_id"] == "BRA" and p["playing_position"] == "ST")
    sub_in = next(p for p in bench if p["team_id"] == "BRA")
    winger = next(p for p in starters if p["team_id"] == "BRA" and p["playing_position"] in ("RW", "LW"))
    n = int(PERIOD_S * FS)
    tt = np.arange(n) / FS
    rows = []
    for period in (1, 2):
        t_utc = period_start(period) + pd.to_timedelta(tt, unit="s")
        frame0 = 10000 if period == 1 else 100000
        frames = frame0 + np.arange(n)
        # Ball: waypoints every 2 s, linear in between; a shot at SHOT_T in period 1 (25 m/s for 0.5 s).
        wp_t = np.arange(0.0, PERIOD_S + 2.0, 2.0)
        wp = np.c_[rng.uniform(-40, 40, len(wp_t)), rng.uniform(-28, 28, len(wp_t))]
        bx, by = np.interp(tt, wp_t, wp[:, 0]), np.interp(tt, wp_t, wp[:, 1])
        status = np.where((tt >= 100.0) & (tt < 110.0), "dead", "live")
        if period == 1:
            run = (tt >= SHOT_T - 2.0) & (tt < SHOT_T)  # carried to the shooting spot
            x0, y0 = np.interp(SHOT_T - 2.0, wp_t, wp[:, 0]), np.interp(SHOT_T - 2.0, wp_t, wp[:, 1])
            f = (tt[run] - (SHOT_T - 2.0)) / 2.0
            bx[run], by[run] = x0 + f * (40.0 - x0), y0 + f * (0.0 - y0)
            k = (tt >= SHOT_T) & (tt <= SHOT_T + 0.5)
            bx[k] = 40.0 + 25.0 * (tt[k] - SHOT_T)
            by[k] = 0.0
            hold = (tt > SHOT_T + 0.5) & (tt < SHOT_T + 2.0)
            bx[hold], by[hold] = 52.5, 0.0
            status = np.where((tt > SHOT_T + 0.6) & (tt < SHOT_T + 30.0), "dead", status)  # goal, then kick-off
        poss = np.where((tt // 30) % 2 == 0, "home", "away")
        rows.append(pd.DataFrame({"period": period, "frame": frames, "time_utc": t_utc, "object_id": "BALL",
                                  "object_type": "ball", "team_side": "none", "x_m": bx, "y_m": by,
                                  "z_m": np.where(status == "live", 0.11, 0.0), "ball_status": status,
                                  "ball_possession": poss}))
        for i, p in enumerate(starters + [sub_in]):
            side = 1.0 if p["role"] == "home" else -1.0
            sgn = side if period == 1 else -side  # home attacks +x in period 1
            base_x = sgn * (-40.0 + 6.0 * (i % 11))
            base_y = -28.0 + 5.5 * (i % 11)
            amp, om = (8.0, 0.9) if p is winger else (4.0, 0.35 + 0.02 * (i % 7))
            x = base_x + amp * np.sin(om * tt + i) + rng.normal(0, 0.02, n)
            y = base_y + 0.5 * amp * np.cos(0.7 * om * tt + i) + rng.normal(0, 0.02, n)
            on = np.ones(n, dtype=bool)
            if p is sub_in:
                on = (period == 2) & (tt >= SUB_T)
            elif p is sub_out and period == 2:
                on = tt < SUB_T
            if p is sub_in:  # comes on at the touchline near the halfway line, then joins the play
                x = np.where(tt < SUB_T + 10, (tt - SUB_T) * 0.5, x)
                y = np.where(tt < SUB_T + 10, -34.0 + (tt - SUB_T) * 1.0, y)
            rows.append(pd.DataFrame({"period": period, "frame": frames[on], "time_utc": t_utc[on],
                                      "object_id": p["player_id"], "object_type": "player", "team_side": p["side"],
                                      "x_m": x[on], "y_m": y[on], "z_m": np.nan, "ball_status": status[on],
                                      "ball_possession": poss[on]}))
    pos = pd.concat(rows, ignore_index=True)
    pos.to_parquet(folder / "positions.parquet", index=False)

    side = {"BRA": "home", "CAL": "away", None: "none"}

    def ev(i, typ, period, t, team=None, player=None, x=None, y=None, sub=None, **attrs):
        return {"event_id": f"FX-{i:03d}", "event_type": typ, "sub_type": sub, "period": period,
                "time_utc": (period_start(period) + pd.Timedelta(seconds=t)).isoformat(),
                "team_side": side[team], "player_id": player, "x_m": x, "y_m": y, "attributes": attrs}

    home_ids = [p["player_id"] for p in starters if p["team_id"] == "BRA"]
    away_ids = [p["player_id"] for p in starters if p["team_id"] == "CAL"]
    events = [ev(1, "KickOff", 1, 0.0, "BRA", sub_out["player_id"], x=0.0, y=0.0)]
    i = 2
    for k, t in enumerate(np.arange(8.0, PERIOD_S - 10, 9.0)):
        for period in (1, 2):
            team, ids = ("BRA", home_ids) if (t // 30) % 2 == 0 else ("CAL", away_ids)
            events.append(ev(i, "Play", period, float(t), team, ids[k % 11], 0.0, 0.0, "Pass",
                             Evaluation="successfullyCompleted" if k % 5 else "unsuccessful",
                             Recipient=ids[(k + 3) % 11]))
            i += 1
    events += [
        ev(i, "CornerKick", 1, 110.0, "BRA", home_ids[7], x=52.5, y=34.0),
        ev(i + 1, "Foul", 1, 150.0, "CAL", away_ids[3], Fouler=away_ids[3], Fouled=home_ids[9], TeamFouler="CAL"),
        ev(i + 2, "Caution", 1, 160.0, "CAL", away_ids[3], sub="yellow", CardColor="yellow"),
        ev(i + 3, "ShotAtGoal", 1, SHOT_T, "BRA", winger["player_id"], x=40.0, y=0.0, sub="SuccessfulShot"),
        ev(i + 4, "KickOff", 1, SHOT_T + 30.0, "CAL", away_ids[9], x=0.0, y=0.0),
        ev(i + 5, "TacklingGame", 1, 250.0, "CAL", away_ids[4], sub="ground", Winner=away_ids[4], Loser=home_ids[8],
           WinnerTeam="CAL"),
        ev(i + 6, "FinalWhistle", 1, PERIOD_S),
        ev(i + 7, "KickOff", 2, 0.0, "CAL", away_ids[9], x=0.0, y=0.0),
        ev(i + 8, "Substitution", 2, SUB_T, "BRA", sub_out["player_id"], PlayerIn=sub_in["player_id"],
           PlayerOut=sub_out["player_id"]),
        ev(i + 9, "ShotAtGoal", 2, 200.0, "CAL", away_ids[10], x=30.0, y=5.0, sub="SavedShot"),
        ev(i + 10, "BallClaiming", 2, 260.0, "BRA", home_ids[5], sub="BallClaimed"),
        ev(i + 11, "FinalWhistle", 2, PERIOD_S),
    ]
    events.sort(key=lambda e: e["time_utc"])
    doc = {"match_id": "SYN-001", "universe_id": "MERIDIA-1.0", "generator_version": "fixture", "seed": 7,
           "events": events}
    (folder / "events.json").write_text(json.dumps(doc, indent=1))
    return Path(root)


def cut(src_root: Path, dst_root: Path, until_utc: pd.Timestamp) -> Path:
    """Copy of the fixture with every frame and event after `until_utc` removed (the 'file cut at minute t'). The match
    profile is the full match's, as for the committed SYN-001 profile."""
    src, dst = match_dir(src_root), match_dir(dst_root)
    dst.mkdir(parents=True, exist_ok=True)
    write_profile(dst_root)
    shutil.copy(src / "match_info.json", dst / "match_info.json")
    pos = pd.read_parquet(src / "positions.parquet")
    pos.loc[pd.to_datetime(pos["time_utc"], utc=True) <= until_utc].to_parquet(dst / "positions.parquet", index=False)
    doc = json.loads((src / "events.json").read_text())
    doc["events"] = [e for e in doc["events"] if pd.Timestamp(e["time_utc"]) <= until_utc]
    (dst / "events.json").write_text(json.dumps(doc, indent=1))
    return Path(dst_root)
