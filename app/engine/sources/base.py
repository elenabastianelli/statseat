"""The match as loaded by an adapter: one canonical shape for every source (guidelines v3, sections 2.2 and 4).

Every adapter (fake, dfl, synthetic) returns a `MatchData`. Nothing outside `app/engine/` reads it: the rest of the
backend sees the match only through `MatchWindow` (`window.py`), which releases frames and events up to the replay
instant.

Time base: every frame and event has a **feed time** `t`, in seconds on one stadium timeline whose zero is the
first-half kick-off; the half-time break is part of it (second-half items have `t` = break end + clock). Coordinates:
metres, origin at the centre spot, raw orientation with the home team attacking +x in the first period (section 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

import numpy as np

HOME, AWAY = "home", "away"
# Payload team ids of the contract (TeamId enum, SYN-001). Other sources map their home/away teams onto them.
TEAM_ID = {HOME: "BRA", AWAY: "CAL"}
SIDE_OF_TEAM_ID = {v: k for k, v in TEAM_ID.items()}


class SourceNotAvailable(RuntimeError):
    """The requested MATCH_SOURCE cannot be loaded on this machine (files missing); the message says why."""


@dataclass(frozen=True)
class Event:
    """One event, DFL taxonomy in English (never `Delete`). `attrs` keys and values are English."""

    event_id: str
    type: str
    period: int
    t: float  # feed time (s)
    team_side: str | None = None  # home / away
    player_id: str | None = None
    secondary_player_id: str | None = None  # assist, player coming on, fouled player, duel loser
    x: float | None = None  # canonical raw orientation (home attacks +x in period 1)
    y: float | None = None
    attrs: Mapping[str, str | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class LineupEntry:
    player_id: str
    team_side: str
    position: str | None
    starting: bool
    shirt_number: int | None = None


@dataclass(frozen=True, eq=False)
class PeriodFrames:
    """Tracking of one period as wide arrays (frames x players); player columns = the line-up, in a fixed order.

    `t` is the feed time of every frame (strictly increasing). Ball status: 1 live, 0 dead, NaN unknown; ball
    possession: 1 home, 2 away, NaN none (the encoding of `data_pipeline.prepare.HalfData`).
    """

    period: int
    fs: float
    t: np.ndarray
    frame: np.ndarray
    pids: tuple[str, ...]
    team_side: tuple[str, ...]
    x: np.ndarray
    y: np.ndarray
    bx: np.ndarray
    by: np.ndarray
    bz: np.ndarray
    bstatus: np.ndarray
    bposs: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def head(self, k: int) -> "PeriodFrames":
        """The first k frames (views, no copy)."""
        k = max(0, min(int(k), len(self.t)))
        return replace(self, t=self.t[:k], frame=self.frame[:k], x=self.x[:k], y=self.y[:k], bx=self.bx[:k],
                       by=self.by[:k], bz=self.bz[:k], bstatus=self.bstatus[:k], bposs=self.bposs[:k])


@dataclass(frozen=True, eq=False)
class MatchData:
    source: str  # MATCH_SOURCE, e.g. fake, dfl:J03WPY, synthetic:SYN-001
    match_id: str  # payload match id (the ticket's match, SYN-001)
    pitch_length_m: float
    pitch_width_m: float
    lineup: tuple[LineupEntry, ...]
    events: tuple[Event, ...]  # sorted by t
    frames: Mapping[int, PeriodFrames] | None  # None when the source has events only
    full_event_stream: bool  # True when every on-ball event is in the feed (passes, duels...), False for key events
    notes: tuple[str, ...] = ()  # what the adapter did, shown in the Inspector


def team_side_of(team_id: str | None) -> str | None:
    if team_id is None:
        return None
    return SIDE_OF_TEAM_ID.get(team_id, team_id if team_id in (HOME, AWAY) else None)


def periods_from_tidy(frames, lineup: tuple[LineupEntry, ...], team_ids: dict[str, str], fs: dict[int, float],
                      t0_s: float, sign: float = 1.0, centre: tuple[float, float] = (0.0, 0.0)) -> dict[int, PeriodFrames]:
    """Long tidy tracking table (columns of `data_pipeline`: half, n, is_ball, object_id, team_id, t_utc, x, y, z,
    ball_status, ball_possession) -> one `PeriodFrames` per period, reusing `data_pipeline.tables.build_half_arrays`.

    team_ids: provider team id -> home/away. fs: frame rate per period. t0_s: UTC seconds of the first-half kick-off
    (feed time zero). sign and centre: canonical transform (section 4), x_c = sign * (x - cx).
    """
    from ..pipeline import build_half_arrays

    pids = tuple(p.player_id for p in lineup)
    sides = tuple(p.team_side for p in lineup)
    out: dict[int, PeriodFrames] = {}
    for h in sorted(int(v) for v in frames["half"].unique()):
        if h not in (1, 2):
            continue  # extra time is not supported by the phase machine
        ha = build_half_arrays(frames, h, set(team_ids))
        col = {p: i for i, p in enumerate(ha.object_ids)}
        nf = len(ha.n)
        x = np.full((nf, len(pids)), np.nan)
        y = np.full((nf, len(pids)), np.nan)
        for j, p in enumerate(pids):
            if p in col:
                x[:, j] = sign * (ha.x[:, col[p]] - centre[0])
                y[:, j] = sign * (ha.y[:, col[p]] - centre[1])
        ok = np.isfinite(ha.t_s)
        first = int(np.flatnonzero(ok)[0])
        # Feed time from the frame number: strictly increasing even where no object has the frame.
        t = (ha.t_s[first] - t0_s) + (ha.n - ha.n[first]) / fs[h]
        out[h] = PeriodFrames(period=h, fs=float(fs[h]), t=t, frame=ha.n.copy(), pids=pids, team_side=sides, x=x, y=y,
                              bx=sign * (ha.ball_x - centre[0]), by=sign * (ha.ball_y - centre[1]), bz=ha.ball_z,
                              bstatus=ha.ball_status, bposs=ha.ball_possession)
    return out
