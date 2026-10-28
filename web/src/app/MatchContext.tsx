import { createContext, useContext } from "react";
import type { LineupPlayer, MatchOverview, MatchState, Mode, Preferences, Section, Session, TeamId, TeamSheet } from "../api/types";
import { teamMarks } from "../lib/colours";

/** What every section needs to know about the ticket and the match, provided once by the Shell. */
export interface MatchCtx {
  session: Session;
  state: MatchState;
  overview: MatchOverview | null;
  mode: Mode;
  inspectorOn: boolean;
  /** Chart that Archie pointed to: Data & Statistics scrolls to it. */
  focusChart: string | null;
  openSection: (s: Section, chartId?: string) => void;
  /** Star of a player: follows or unfollows, at most 3 and never after the confirmation (a toast says why). */
  toggleFavourite: (playerId: string) => void;
  updatePrefs: (patch: Partial<Preferences>) => Promise<void>;
}

export const Ctx = createContext<MatchCtx | null>(null);

export function useMatch(): MatchCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useMatch outside the Shell");
  return v;
}

export function team(overview: MatchOverview | null, id: TeamId | null | undefined): TeamSheet | undefined {
  return overview?.teams.find((t) => t.team_id === id);
}

/** Chart and marker colour of a team, chosen together with the opponent's (falls back before the overview loads). */
export function teamColour(overview: MatchOverview | null, id: TeamId | null | undefined): string {
  const t = team(overview, id);
  const home = overview?.teams.find((x) => x.role === "home");
  const away = overview?.teams.find((x) => x.role === "away");
  if (t && home && away) {
    const marks = teamMarks(home.colours, away.colours);
    return t.role === "home" ? marks.home : marks.away;
  }
  return id === "BRA" ? "#F2A900" : "#8FC1E3";
}

export function teamShort(overview: MatchOverview | null, id: TeamId | null | undefined): string {
  return team(overview, id)?.short_name ?? id ?? "";
}

export function player(overview: MatchOverview | null, id: string | null | undefined): LineupPlayer | undefined {
  if (!id || !overview) return undefined;
  for (const t of overview.teams) {
    const p = t.lineup.find((p) => p.player_id === id);
    if (p) return p;
  }
  return undefined;
}

export function teamOfPlayer(overview: MatchOverview | null, id: string): TeamId | undefined {
  return overview?.teams.find((t) => t.lineup.some((p) => p.player_id === id))?.team_id;
}

export function playerName(overview: MatchOverview | null, id: string | null | undefined): string {
  return player(overview, id)?.short_name ?? id ?? "";
}
