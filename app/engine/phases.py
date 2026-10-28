"""Phase state machine, driven only by the KickOff and FinalWhistle events released by the window.

pre_match --KickOff(1)--> first_half --FinalWhistle(1)--> half_time --KickOff(2)--> second_half --FinalWhistle(2)-->
full_time. Any other KickOff (after a goal) or an out-of-order whistle is ignored. Extra time is not supported.
Which sections are unlocked in each phase comes from the contract (`app/contract/phases.py`), not from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.contract.models import Phase
from app.contract.phases import PHASE_ORDER, is_unlocked, next_unlock, sections_for

from .sources.base import Event

TRANSITIONS: dict[tuple[Phase, str, int], Phase] = {
    (Phase.pre_match, "KickOff", 1): Phase.first_half,
    (Phase.first_half, "FinalWhistle", 1): Phase.half_time,
    (Phase.half_time, "KickOff", 2): Phase.second_half,
    (Phase.second_half, "FinalWhistle", 2): Phase.full_time,
}


@dataclass(frozen=True)
class PhaseState:
    phase: Phase = Phase.pre_match
    kickoff: dict[int, float] = field(default_factory=dict)  # period -> feed time of its KickOff
    final_whistle: dict[int, float] = field(default_factory=dict)  # period -> feed time of its FinalWhistle


def run_phase_machine(events: Iterable[Event]) -> PhaseState:
    """Replay the released events in time order and return the phase they lead to."""
    phase, ko, fw = Phase.pre_match, {}, {}
    for e in events:
        nxt = TRANSITIONS.get((phase, e.type, e.period))
        if nxt is None:
            continue
        (ko if e.type == "KickOff" else fw)[e.period] = e.t
        phase = nxt
    return PhaseState(phase, ko, fw)


__all__ = ["PHASE_ORDER", "PhaseState", "run_phase_machine", "sections_for", "is_unlocked", "next_unlock"]
