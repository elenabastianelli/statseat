"""Helpers that reshape the long tracking table into per-half wide arrays."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class HalfArrays:
    half: int
    n0: int  # first frame number in the half (any object)
    n: np.ndarray  # frame numbers, n0..n_last
    t_s: np.ndarray  # seconds since epoch (UTC), NaN where no object has the frame
    object_ids: list[str]  # players only
    team_ids: list[str]
    x: np.ndarray  # shape (frames, players)
    y: np.ndarray
    ball_x: np.ndarray
    ball_y: np.ndarray
    ball_z: np.ndarray
    ball_status: np.ndarray
    ball_possession: np.ndarray

    def frame_index(self, n_values: np.ndarray) -> np.ndarray:
        return np.asarray(n_values, dtype=np.int64) - self.n0


def to_seconds(ts: pd.Series) -> np.ndarray:
    """tz-aware UTC timestamps -> float seconds since epoch."""
    return (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()


def build_half_arrays(frames: pd.DataFrame, half: int, player_teams: set[str] | None = None) -> HalfArrays:
    """Wide per-half arrays. ``player_teams``: team ids whose objects are players.

    Objects of any other team (for example DFL ``TeamId="referee"``) are not players:
    they are left out of the player arrays (referees are tracked on some matches and
    not on others, so this is discovered per match, never assumed).
    """
    h = frames.loc[frames["half"] == half]
    n_min, n_max = int(h["n"].min()), int(h["n"].max())
    n = np.arange(n_min, n_max + 1, dtype=np.int64)
    nf = len(n)
    t_s = np.full(nf, np.nan)
    players = h.loc[~h["is_ball"]]
    if player_teams is not None:
        players = players.loc[players["team_id"].astype(str).isin(player_teams)]
    pids = sorted(players["object_id"].astype(str).unique().tolist())
    team_of = players.drop_duplicates("object_id").set_index(players.drop_duplicates("object_id")["object_id"].astype(str))["team_id"].astype(str)
    x = np.full((nf, len(pids)), np.nan)
    y = np.full((nf, len(pids)), np.nan)
    col = {p: i for i, p in enumerate(pids)}
    fi = (players["n"].to_numpy() - n_min).astype(np.int64)
    ci = players["object_id"].astype(str).map(col).to_numpy()
    x[fi, ci] = players["x"].to_numpy()
    y[fi, ci] = players["y"].to_numpy()
    t_all = to_seconds(h["t_utc"])
    t_s[(h["n"].to_numpy() - n_min).astype(np.int64)] = t_all
    ball = h.loc[h["is_ball"]]
    bfi = (ball["n"].to_numpy() - n_min).astype(np.int64)

    def bcol(c):
        a = np.full(nf, np.nan)
        a[bfi] = ball[c].to_numpy()
        return a

    return HalfArrays(
        half=half,
        n0=n_min,
        n=n,
        t_s=t_s,
        object_ids=pids,
        team_ids=[team_of[p] for p in pids],
        x=x,
        y=y,
        ball_x=bcol("x"),
        ball_y=bcol("y"),
        ball_z=bcol("z"),
        ball_status=bcol("ball_status"),
        ball_possession=bcol("ball_possession"),
    )
