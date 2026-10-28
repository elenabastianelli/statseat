"""Which app sections are unlocked in each match phase.

Pre-match always; Live only during play; Data & Statistics from kick-off to the end (v0.2.5); Games only at
half-time (v0.2.3, with the sponsor's prize); Recap after the final whistle; Ask Archie always. No Polls section
since v0.2.3 (decision of 25/10). The real engine (S-7) must reuse this table.
"""

from __future__ import annotations

from .models import Phase, Section, SectionState

PHASE_ORDER: list[Phase] = [Phase.pre_match, Phase.first_half, Phase.half_time, Phase.second_half, Phase.full_time]

SECTION_PHASES: dict[Section, set[Phase]] = {
    Section.pre_match: set(PHASE_ORDER),
    Section.live: {Phase.first_half, Phase.second_half},
    Section.data_stats: {Phase.first_half, Phase.half_time, Phase.second_half, Phase.full_time},
    Section.games: {Phase.half_time},
    Section.recap: {Phase.full_time},
    Section.ask_archie: set(PHASE_ORDER),
}


def is_unlocked(section: Section, phase: Phase) -> bool:
    return phase in SECTION_PHASES[section]


def next_unlock(section: Section, phase: Phase) -> Phase | None:
    """The next phase (after `phase`) in which `section` opens, or None if it is open or never opens again."""
    if is_unlocked(section, phase):
        return None
    for p in PHASE_ORDER[PHASE_ORDER.index(phase) + 1 :]:
        if p in SECTION_PHASES[section]:
            return p
    return None


def sections_for(phase: Phase) -> list[SectionState]:
    return [
        SectionState(section=s, unlocked=is_unlocked(s, phase), unlocks_at_phase=next_unlock(s, phase))
        for s in Section
    ]
