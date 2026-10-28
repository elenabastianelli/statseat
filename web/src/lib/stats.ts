// Pure helpers of Data & Statistics (L-4): no React here, so the rules are tested in lib.test.ts.
import type { GroundedValue, MatchStats, MetricUnavailable, Shot, TeamId, TimePoint } from "../api/types";

/** The tables of Data & Statistics in display order: `DATA_STATS_TABLES` of app/contract/models.py (v0.3.0). */
export const DATA_STATS_TABLES = [
  "match_table", "player_focus", "running_table", "ball_table", "defence_table", "players_table",
] as const;
export type TableId = (typeof DATA_STATS_TABLES)[number];

/** Symbol of the share of time measured by the tracking (contract `Reliability`): high ●, medium ◐, low ○. */
export const RELIABILITY_SYMBOL = { high: "●", medium: "◐", low: "○" } as const;

/**
 * A null metric (contract v0.3.0): with `available_from_minute` the app shows "Available from 5'", otherwise the card or
 * the row is hidden. `undefined` = the metric is not listed as unavailable.
 */
export type Availability = { from: number } | "hidden";

export function availability(list: MetricUnavailable[] | null | undefined, ...metrics: string[]): Availability | undefined {
  const u = list?.find((x) => metrics.includes(x.metric));
  if (!u) return undefined;
  return u.available_from_minute != null ? { from: u.available_from_minute } : "hidden";
}

/** Shot map symbols (METRICS.md §14): goal filled, woodwork ×, every other shot hollow. */
export function shotSymbol(outcome: Shot["outcome"]): "filled" | "cross" | "hollow" {
  return outcome === "goal" ? "filled" : outcome === "woodwork" ? "cross" : "hollow";
}

/** Radius of a shot dot in pitch metres: the same for every shot in Fan, the area proportional to xG in Analyst. */
export function shotRadius(xg: number | null | undefined, sized: boolean): number {
  return sized && xg != null ? 0.6 + Math.sqrt(xg) * 3.4 : 1.3;
}

/** The team that moves the ball faster (one sentence in Fan, METRICS.md §6); null on a tie. */
export function fasterTeam(teams: { team_id: TeamId; value: number }[]): TeamId | null {
  if (teams.length < 2 || teams[0].value === teams[1].value) return null;
  return teams[0].value > teams[1].value ? teams[0].team_id : teams[1].team_id;
}

/**
 * Momentum is one signed value per minute (positive = home pushing): for the mirror chart it becomes two series, the
 * home part above the axis and the away part below.
 */
export function splitSigned(points: TimePoint[]): { home: TimePoint[]; away: TimePoint[] } {
  return {
    home: points.map((p) => ({ minute: p.minute, value: Math.max(p.value, 0) })),
    away: points.map((p) => ({ minute: p.minute, value: Math.max(-p.value, 0) })),
  };
}

export interface MirrorSeries {
  points: TimePoint[];
  /** true = home team: drawn above the axis */
  above: boolean;
}

export interface MirrorGeometry {
  x: (minute: number) => number;
  /** value drawn on the axis: 0, or a floor below the lowest value so that close series stay readable */
  floor: number;
  /** y of a value for the series above (home) or below (away) the axis */
  y: (value: number, above: boolean) => number;
  axisY: number;
  from: number;
  to: number;
  max: number;
}

/**
 * The scale of the mirror chart (METRICS.md, "One chart over time"): one time axis for both teams, the axis in the
 * middle, the same value scale above and below so that the higher team at each minute is the one further from the axis.
 * With `zeroBased` false the axis is a floor below the lowest value (shown to the fan): series such as metres per
 * minute stay between 100 and 140, and from zero both teams would be two full blocks.
 */
export function mirrorGeometry(series: MirrorSeries[], width: number, height: number, max?: number,
  span?: { from?: number; to?: number }, zeroBased = true): MirrorGeometry {
  const pts = series.flatMap((s) => s.points);
  const from = span?.from ?? Math.min(...pts.map((p) => p.minute), 0);
  const to = Math.max(span?.to ?? 0, ...pts.map((p) => p.minute), from + 1);
  const top = max ?? Math.max(0, ...pts.map((p) => Math.abs(p.value)));
  const low = pts.length ? Math.min(...pts.map((p) => Math.abs(p.value))) : 0;
  const floor = zeroBased || top <= low ? 0 : niceFloor(Math.max(0, low - (top - low)));
  const m = top - floor > 0 ? top - floor : 1;
  const axisY = height / 2;
  const half = height / 2 - 3;
  return {
    x: (minute) => ((minute - from) / (to - from)) * width,
    y: (value, above) => axisY + (above ? -1 : 1) * (Math.min(Math.max(Math.abs(value) - floor, 0), m) / m) * half,
    axisY, from, to, max: m + floor, floor,
  };
}

/** A round number at or below v, for the axis label (95, 1100, 0.5). */
export function niceFloor(v: number): number {
  if (v <= 0) return 0;
  const step = 10 ** Math.floor(Math.log10(v)) / 2;
  return Math.floor(v / step) * step;
}

/** SVG path of one series: a line, or a step line (cumulative xG); `area` closes it on the axis. */
export function mirrorPath(s: MirrorSeries, g: MirrorGeometry, step = false, area = false): string {
  if (s.points.length === 0) return "";
  const parts = s.points.map((p, i) => {
    const x = g.x(p.minute).toFixed(1);
    const y = g.y(p.value, s.above).toFixed(1);
    if (i === 0) return `M${x},${y}`;
    return step ? `H${x}V${y}` : `L${x},${y}`;
  });
  if (!area) return parts.join("");
  const first = g.x(s.points[0].minute).toFixed(1);
  const last = g.x(s.points[s.points.length - 1].minute).toFixed(1);
  const axis = g.axisY.toFixed(1);
  return `M${first},${axis}` + parts.join("").replace(/^M/, "L") + `L${last},${axis}Z`;
}

/** The players of the Analyst table, grouped by team (home first) and, inside a team, by minutes played. */
export function playersInOrder(s: Pick<MatchStats, "players">, homeId: TeamId): NonNullable<MatchStats["players"]> {
  return [...(s.players ?? [])].sort((a, b) =>
    a.team_id === b.team_id ? b.minutes_played - a.minutes_played : a.team_id === homeId ? -1 : 1);
}

/** "possession.teams[0].value" → "possession", "top_speed[2].kmh" → "top_speed": what a Recap key number is. */
export function keyMetric(k: GroundedValue): string {
  return k.source_field.split(".")[0].replace(/\[\d+\]$/, "");
}

/** Units shown next to a key number; a count ("9 shots") shows the number only, since the label says what it counts. */
const SHOWN_UNITS = new Set(["%", "km/h"]);

export function keyValue(k: GroundedValue): string {
  return SHOWN_UNITS.has(k.unit ?? "") ? k.text : String(k.value);
}

/**
 * Whose a Recap key number is: the player of its arguments, else its team, from the arguments or from a team-indexed
 * field ("teams[0]", "shots[1]": 0 = home, 1 = away). Nothing for a number of the whole match.
 */
export function keyOwner(k: GroundedValue, home: TeamId, away: TeamId): { player_id?: string; team_id?: TeamId } {
  const pid = k.arguments?.player_id;
  if (typeof pid === "string") return { player_id: pid };
  const tid = k.arguments?.team_id;
  if (typeof tid === "string") return { team_id: tid as TeamId };
  const index = /(?:^shots|teams)\[(\d)\]/.exec(k.source_field)?.[1];
  return index === "0" ? { team_id: home } : index === "1" ? { team_id: away } : {};
}
