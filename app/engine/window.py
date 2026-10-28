"""The no-look-ahead window (S-7): the ONLY way the backend reads the match.

The synthetic match is generated before the demo, whole; the engine replays it. Statistics, agents and the API must
see only the past and the present. `MatchWindow` keeps the loaded match private and hands out `Snapshot`s; a snapshot
at replay instant `cursor_t` (feed seconds, see `sources/base.py`) contains only:

1. **frames** with `t <= cursor_t`;
2. **events** with `t + buffer_s <= cursor_t`, so an event is released only after the frames that follow it for
   `buffer_s` seconds (guidelines 9.3 step 7: "an event is never processed before its frames"). Default 5 s, the
   minimum event search window of the guidelines;
3. **the phase**, from a state machine fed only with those released events (KickOff / FinalWhistle, `phases.py`);
4. frames and events of a period only once its KickOff has been released, and no frames of a period after its
   FinalWhistle;
5. **`as_of_s`**, the playing time (seconds since kick-off, first-half added time included, break excluded) of the
   instant the snapshot stands for, computed only from the released whistles. Every payload carries it.

Everything downstream (`stats.py`, the API, later the agents) receives a `Snapshot` and nothing else, so a statistic
at minute t cannot depend on anything after t. The truncation test (`app/tests/test_engine.py`) checks it: the same
statistics come out of the whole match and of a file cut at minute t. `MatchWindow.check()` re-checks rules 1, 2 and
"frames before events" against the full match for any snapshot (used by the tests).

Metadata known before kick-off (line-ups, pitch size, coordinate transform) is static and is not windowed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from app.contract.models import Phase

from .phases import PhaseState, run_phase_machine
from .sources.base import Event, LineupEntry, MatchData, PeriodFrames

DEFAULT_EVENT_BUFFER_S = 5.0


@dataclass(frozen=True, eq=False)
class Snapshot:
    """What the backend may know at one replay instant. Immutable; built only by `MatchWindow.at`."""

    match_id: str
    source: str
    cursor_t: float
    buffer_s: float
    as_of_s: float
    state: PhaseState
    events: tuple[Event, ...]
    frames: Mapping[int, PeriodFrames]  # empty when the source has no frames or no period has started
    has_tracking: bool  # the source has tracking frames (False for fake)
    full_event_stream: bool
    lineup: tuple[LineupEntry, ...]
    pitch: tuple[float, float]
    notes: tuple[str, ...]

    @property
    def phase(self) -> Phase:
        return self.state.phase

    def period_length_s(self, period: int) -> float | None:
        ko, fw = self.state.kickoff.get(period), self.state.final_whistle.get(period)
        return None if ko is None or fw is None else fw - ko

    def clock_s(self, period: int, t: float) -> float:
        """Seconds since the kick-off of `period` (clamped to the period length once it is over)."""
        ko = self.state.kickoff.get(period)
        if ko is None:
            return 0.0
        c = max(0.0, t - ko)
        length = self.period_length_s(period)
        return min(c, length) if length is not None else c

    def playing_s(self, period: int, t: float) -> float:
        """Playing time of an instant: first-half clock, or first-half length plus second-half clock."""
        return self.clock_s(period, t) + ((self.period_length_s(1) or 0.0) if period == 2 else 0.0)

    @staticmethod
    def minute(period: int, clock_s: float) -> tuple[int, int]:
        """(scoreboard minute, added minutes): 45+2 is (45, 2); the second half starts at 46."""
        m = int(clock_s // 60) + 1 + (45 if period == 2 else 0)
        cap = 45 if period == 1 else 90
        return (cap, m - cap) if m > cap else (m, 0)


class MatchWindow:
    """Releases the match up to a replay instant, never beyond (rules in the module docstring)."""

    def __init__(self, match: MatchData, buffer_s: float = DEFAULT_EVENT_BUFFER_S) -> None:
        self._match = match
        self._buffer_s = float(buffer_s)
        self._event_t = [e.t for e in match.events]  # sorted
        self._frames = MappingProxyType(dict(match.frames or {}))

    @property
    def buffer_s(self) -> float:
        return self._buffer_s

    def at(self, cursor_t: float) -> Snapshot:
        cursor_t = float(cursor_t)
        n = bisect.bisect_right(self._event_t, cursor_t - self._buffer_s)  # rule 2
        released = self._match.events[:n]
        state = run_phase_machine(released)  # rule 3
        events = tuple(e for e in released if e.period in state.kickoff)  # rule 4
        frames = {}
        for p, pf in self._frames.items():
            if p not in state.kickoff:
                continue  # rule 4
            limit = min(cursor_t, state.final_whistle.get(p, np.inf))
            frames[p] = pf.head(int(np.searchsorted(pf.t, limit, side="right")))  # rule 1
        return Snapshot(
            match_id=self._match.match_id, source=self._match.source, cursor_t=cursor_t, buffer_s=self._buffer_s,
            as_of_s=_as_of(state, cursor_t), state=state, events=events, frames=MappingProxyType(frames),
            has_tracking=bool(self._frames), full_event_stream=self._match.full_event_stream,
            lineup=self._match.lineup, pitch=(self._match.pitch_length_m, self._match.pitch_width_m),
            notes=self._match.notes)

    def check(self, snap: Snapshot) -> None:
        """Assert the no-look-ahead rules of `snap` against the full match (tests and debugging)."""
        for p, pf in snap.frames.items():
            assert len(pf) == 0 or pf.t[-1] <= snap.cursor_t, f"period {p}: frame after the cursor"
        for e in snap.events:
            assert e.t + snap.buffer_s <= snap.cursor_t, f"{e.event_id}: event released before its buffer"
            full = self._frames.get(e.period)
            if full is None:
                continue
            end = min(e.t + snap.buffer_s, snap.state.final_whistle.get(e.period, np.inf))
            needed = int(np.searchsorted(full.t, end, side="right"))
            got = len(snap.frames.get(e.period, ()))
            assert got >= needed, f"{e.event_id}: event visible before its frames ({got} < {needed})"


def _as_of(state: PhaseState, cursor_t: float) -> float:
    ko, fw = state.kickoff, state.final_whistle
    if state.phase is Phase.pre_match:
        return 0.0
    p1 = (fw[1] - ko[1]) if 1 in fw else max(0.0, cursor_t - ko[1])
    if state.phase in (Phase.first_half, Phase.half_time):
        return p1
    p2 = (fw[2] - ko[2]) if 2 in fw else max(0.0, cursor_t - ko[2])
    return p1 + p2
