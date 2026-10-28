import type { Distance, MatchEvent } from "../api/types";

/** Distances with part of the time unmeasured are shown with "≈" (data guidelines v3). */
export function km(d: Distance): string {
  return `${d.approx ? "≈" : ""}${d.km.toFixed(2)} km`;
}

export function pct(v: number, digits = 0): string {
  return `${v.toFixed(digits)}%`;
}

export function minuteLabel(minute: number, added = 0): string {
  return added > 0 ? `${minute}+${added}'` : `${minute}'`;
}

export function time(iso: string, lang: string): string {
  return new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
}

export function date(iso: string, lang: string): string {
  return new Date(iso).toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** The i18n key of an event in the Live feed: a goal is its own key, the first-half FinalWhistle is half-time. */
export function eventLabelKey(e: Pick<MatchEvent, "type" | "period" | "qualifiers">): string {
  if (e.type === "ShotAtGoal" && e.qualifiers?.outcome === "goal") return "event.goal";
  if (e.type === "FinalWhistle" && e.period === 1) return "event.half_time";
  return `event.${e.type}`;
}
