"""Replay clocks: where the replay cursor is, in feed seconds (see `sources/base.py`).

- One **stadium clock** for silver and gold tickets (everyone in the stadium sees the same instant).
- One clock **per demo session**, moved by the demo panel (`POST /api/demo/phase`); after a jump it runs during
  play and stops on its own at half-time and full-time, so a judge (on a demo ticket) can explore a phase without
  it running away.

The clock only says *where* the cursor is. What may be read at that instant is decided by `MatchWindow`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from app.contract.models import Phase

from .sources.base import MatchData


class ReplayClock:
    """position = start + speed x (wall time since start) while running; optional hold point where it stops."""

    def __init__(self, speed: float, position: float, running: bool, hold_at: float | None = None,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.speed = float(speed)
        self._now = now
        self._lock = threading.Lock()
        self._base_pos = float(position)
        self._base_wall = now()
        self._running = running
        self.hold_at = hold_at

    def position(self) -> float:
        with self._lock:
            if not self._running:
                return self._base_pos
            pos = self._base_pos + self.speed * (self._now() - self._base_wall)
            if self.hold_at is not None and pos >= self.hold_at:
                self._base_pos, self._running, self.hold_at = self.hold_at, False, None
                return self._base_pos
            return pos

    @property
    def running(self) -> bool:
        self.position()  # applies a hold point that has been reached
        return self._running

    def seek(self, position: float, running: bool, hold_at: float | None = None) -> None:
        with self._lock:
            self._base_pos, self._base_wall, self._running, self.hold_at = float(position), self._now(), running, hold_at

    def start(self) -> None:
        if not self._running:
            self.seek(self.position(), True, self.hold_at)

    def pause(self) -> None:
        self.seek(self.position(), False, self.hold_at)


def phase_starts(match: MatchData, buffer_s: float, pre_match_s: float) -> dict[Phase, float]:
    """Replay-controller only: the cursor at which each phase begins (the whistle plus the event buffer).

    This reads the whistles of the whole file because the demo panel and the stadium clock must know where to put
    the cursor; it returns positions only, never match data. What a session can read is still decided by the window.
    """
    ko: dict[int, float] = {}
    fw: dict[int, float] = {}
    for e in match.events:  # sorted by time: the first KickOff / FinalWhistle of each period wins
        target = ko if e.type == "KickOff" else fw if e.type == "FinalWhistle" else None
        if target is not None and e.period in (1, 2):
            target.setdefault(e.period, e.t)
    eps = 1e-3
    out = {Phase.pre_match: ko.get(1, 0.0) - pre_match_s}
    if 1 in ko:
        out[Phase.first_half] = ko[1] + buffer_s + eps
    if 1 in fw:
        out[Phase.half_time] = fw[1] + buffer_s + eps
    if 2 in ko:
        out[Phase.second_half] = ko[2] + buffer_s + eps
    if 2 in fw:
        out[Phase.full_time] = fw[2] + buffer_s + eps
    return out
