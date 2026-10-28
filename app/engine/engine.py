"""The match engine: one loaded match, one window, the stadium clock, the demo clocks and a cache of statistics.

Everything it returns is built from a `Snapshot` of the window (`window.py`); the loaded `MatchData` never leaves
this object.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.contract.models import Phase

from . import frame_stats, stats
from .clock import ReplayClock, phase_starts
from .config import EngineConfig
from .sources import load_match
from .sources.base import MatchData
from .window import MatchWindow, Snapshot

PLAY = (Phase.first_half, Phase.second_half)
HOLDS = (Phase.half_time, Phase.full_time)  # a demo clock stops here on its own


class Engine:
    def __init__(self, match: MatchData, cfg: EngineConfig = EngineConfig(),
                 now: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.source = match.source
        self.window = MatchWindow(match, cfg.event_buffer_s)
        self.starts = phase_starts(match, cfg.event_buffer_s, cfg.pre_match_s)
        self.tick_s = cfg.stats_tick_s or (60.0 if match.frames else 10.0)
        self._now = now
        start = self.starts.get(cfg.start_phase, self.starts[Phase.pre_match])
        self.stadium = ReplayClock(cfg.speed, start, cfg.autostart, now=now)
        self.demo: dict[str, ReplayClock] = {}  # session token -> its own replay clock (demo tickets only)
        self._prep = frame_stats.PrepCache()
        self._cores: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._compute_lock = threading.Lock()
        self._pending: set = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engine-stats")

    @classmethod
    def from_config(cls, cfg: EngineConfig) -> "Engine":
        return cls(load_match(cfg.match_source), cfg)

    # ------------------------------------------------------------ clocks
    def clock(self, token: str | None = None) -> ReplayClock:
        return self.demo.get(token, self.stadium) if token else self.stadium

    def snapshot(self, token: str | None = None) -> Snapshot:
        return self.window.at(self.clock(token).position())

    def jump(self, token: str, phase: Phase) -> None:
        """Demo panel: move this session's cursor to the start of `phase`. It runs during play and holds at the
        next half-time or full-time."""
        if phase not in self.starts:
            raise ValueError(f"the match of source {self.source} has no {phase.value}")
        pos = self.starts[phase]
        running = phase in PLAY
        hold = min((self.starts[h] for h in HOLDS if h in self.starts and self.starts[h] > pos), default=None)
        clock = self.demo.get(token)
        if clock is None:
            clock = self.demo[token] = ReplayClock(self.cfg.speed, pos, running, now=self._now)
        clock.seek(pos, running, hold if running else None)

    def set_running(self, token: str, running: bool) -> None:
        clock = self.demo.get(token)
        if clock is None:  # first use: start from where the stadium is
            self.demo[token] = clock = ReplayClock(self.cfg.speed, self.stadium.position(), False, now=self._now)
        if running:
            pos = clock.position()
            hold = min((self.starts[h] for h in HOLDS if h in self.starts and self.starts[h] > pos), default=None)
            clock.seek(pos, True, hold)
        else:
            clock.pause()

    # ------------------------------------------------------------ statistics
    def stats_snapshot(self, snap: Snapshot) -> Snapshot:
        """During play, live statistics are computed every `tick_s` of playing time (an earlier, complete instant,
        never a later one); in the other phases the snapshot does not change."""
        if snap.phase not in PLAY:
            return snap
        target = math.floor(snap.as_of_s / self.tick_s) * self.tick_s
        p = 1 if snap.phase is Phase.first_half else 2
        ko = snap.state.kickoff[p]
        offset = target - (snap.period_length_s(1) if p == 2 else 0.0)
        cursor = max(ko + offset, ko + snap.buffer_s + 1e-3)
        if cursor >= snap.cursor_t:
            return snap
        earlier = self.window.at(cursor)
        return earlier if earlier.phase is snap.phase else snap

    @staticmethod
    def _key(snap: Snapshot) -> tuple:
        """Two snapshots with the same key hold the same data, hence the same statistics."""
        return (snap.phase, len(snap.events), tuple((p, len(f)) for p, f in sorted(snap.frames.items())),
                round(snap.as_of_s, 3))

    def core(self, snap: Snapshot) -> stats.Core:
        """Statistics of exactly this snapshot (computed once, then cached)."""
        key = self._key(snap)
        with self._lock:
            if key in self._cores:
                self._cores.move_to_end(key)
                return self._cores[key]
        with self._compute_lock:  # one computation at a time; a second caller waits for the first
            with self._lock:
                if key in self._cores:
                    return self._cores[key]
            core = stats.compute_core(snap, self._prep)
            with self._lock:
                self._cores[key] = core
                while len(self._cores) > 32:
                    self._cores.popitem(last=False)
            return core

    def live_core(self, snap: Snapshot) -> stats.Core:
        """Live statistics without making the fan wait: the tick snapshot if it is ready, otherwise the latest
        ready one of the same phase that is not later than `snap` (never a later one), while the tick is computed in
        the background. With events only, computing is instant and this is just `core`."""
        target = self.stats_snapshot(snap)
        key = self._key(target)
        with self._lock:
            ready = self._cores.get(key)
            if ready is None:
                earlier = [c for c in self._cores.values()
                           if c.snap.phase is snap.phase and c.snap.as_of_s <= snap.as_of_s]
                fallback = max(earlier, key=lambda c: c.snap.as_of_s, default=None)
        if ready is not None:
            return ready
        if fallback is None or not target.has_tracking:
            return self.core(target)
        if key not in self._pending:
            self._pending.add(key)

            def work():
                try:
                    self.core(target)
                finally:
                    self._pending.discard(key)

            self._pool.submit(work)
        return fallback

    def prewarm(self, snap: Snapshot) -> None:
        """Compute the statistics of `snap` in the background (used after a demo jump; never for a later instant)."""
        if snap.has_tracking and self._key(snap) not in self._cores:
            self._pool.submit(self.core, snap)
