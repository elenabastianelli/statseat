"""Engine settings, from environment variables (no secrets here)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.contract.models import Phase

from .window import DEFAULT_EVENT_BUFFER_S


@dataclass(frozen=True)
class EngineConfig:
    match_source: str = "synthetic:SYN-001"  # MATCH_SOURCE: synthetic:<id> | dfl:<id> | fake
    speed: float = 10.0  # ENGINE_SPEED: replay speed (10 = ten match seconds per real second)
    event_buffer_s: float = DEFAULT_EVENT_BUFFER_S  # ENGINE_EVENT_BUFFER_S: events wait this long for their frames
    pre_match_s: float = 300.0  # ENGINE_PRE_MATCH_S: match seconds of pre-match before kick-off (30 s at 10x)
    start_phase: Phase = Phase.pre_match  # ENGINE_START_PHASE: where the stadium clock starts
    autostart: bool = True  # ENGINE_AUTOSTART: 0 keeps the stadium clock paused
    stats_tick_s: float | None = None  # ENGINE_STATS_TICK_S: live stats every N match seconds (default 10, 60 with frames)

    @classmethod
    def from_env(cls) -> "EngineConfig":
        env = os.environ
        tick = env.get("ENGINE_STATS_TICK_S")
        return cls(match_source=env.get("MATCH_SOURCE", "synthetic:SYN-001"), speed=float(env.get("ENGINE_SPEED", "10")),
                   event_buffer_s=float(env.get("ENGINE_EVENT_BUFFER_S", str(DEFAULT_EVENT_BUFFER_S))),
                   pre_match_s=float(env.get("ENGINE_PRE_MATCH_S", "300")),
                   start_phase=Phase(env.get("ENGINE_START_PHASE", "pre_match")),
                   autostart=env.get("ENGINE_AUTOSTART", "1") not in ("0", "false", "no"),
                   stats_tick_s=float(tick) if tick else None)
