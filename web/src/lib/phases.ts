import type { Mode, Phase, Section, SectionState, Session } from "../api/types";

/**
 * Tabs in display order (L-2e): the match first (Pre-match, Live, Data & Statistics, Recap), then Games, shown as a
 * smaller, secondary tab. Ask Archie is not a tab: its button is always on screen.
 */
export const TAB_SECTIONS: Section[] = ["pre_match", "live", "data_stats", "recap", "games"];
export const SECONDARY_SECTIONS: Section[] = ["games"];

/** The section the app opens when the match enters a phase (the server decides what is unlocked). */
const PHASE_DEFAULT: Record<Phase, Section> = {
  pre_match: "pre_match",
  first_half: "live",
  half_time: "data_stats",
  second_half: "live",
  full_time: "recap",
};

export function defaultSection(phase: Phase, sections: SectionState[]): Section {
  const wanted = PHASE_DEFAULT[phase];
  return isUnlocked(wanted, sections) ? wanted : "pre_match";
}

export function isUnlocked(section: Section, sections: SectionState[]): boolean {
  return sections.find((s) => s.section === section)?.unlocked ?? false;
}

/** The mode switch is shown only when the ticket allows more than one mode (silver: Casual only). */
export function canSwitchMode(session: Session): boolean {
  return session.allowed_modes.length > 1;
}

/**
 * The note under Archie's answer when what he points to is locked (AG-5): the phase that opens it, or, for a table or
 * chart of the Analyst version only, the same note as Data & Statistics (gold can switch to it, silver cannot).
 */
export function lockedPointerNote(
  unlocksAt: Phase | null | undefined,
  session: Session,
): { key: "archie.chart_locked"; phase: Phase } | { key: "stats.switch_analyst" | "stats.unlock_gold" } {
  if (unlocksAt) return { key: "archie.chart_locked", phase: unlocksAt };
  return { key: canSwitchMode(session) ? "stats.switch_analyst" : "stats.unlock_gold" };
}

/** The mode to request: the preferred one if the tier allows it, otherwise the first allowed. */
export function effectiveMode(session: Session, preferred: Mode | undefined): Mode {
  return preferred && session.allowed_modes.includes(preferred) ? preferred : session.allowed_modes[0];
}
