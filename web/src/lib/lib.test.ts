import { describe, expect, it } from "vitest";
import session from "../../../app/contract/fake/session.json";
import silver from "../../../app/contract/fake/session_silver.json";
import halfTime from "../../../app/contract/fake/match_state_half_time.json";
import preMatch from "../../../app/contract/fake/match_state_pre_match.json";
import fullTime from "../../../app/contract/fake/match_state_full_time.json";
import match from "../../../app/contract/fake/match.json";
import events from "../../../app/contract/fake/events.json";
import { toApiError } from "../api/client";
import type { MatchEvent, MatchState, Session, TeamColours } from "../api/types";
import universe from "../../../synthetic/universe/universe.json";
import { FLAG_URIS } from "../components/flags.generated";
import { BG, colourDistance, contrast, MIN_TEAM_DISTANCE, teamMark, teamMarks } from "./colours";
import { eventLabelKey } from "./format";
import en from "../i18n/en.json";
import { canConfirmFavourites, MAX_FAVOURITES, toggleFavourite } from "./favourites";
import { canSwitchMode, defaultSection, effectiveMode, lockedPointerNote } from "./phases";

const gold = session as unknown as Session;
const silverS = silver as unknown as Session;
const sections = (s: unknown) => (s as MatchState).sections;

describe("ticket tiers", () => {
  it("silver has no mode switch and is kept in Casual", () => {
    expect(silverS.tier).toBe("silver");
    expect(canSwitchMode(silverS)).toBe(false);
    expect(effectiveMode(silverS, "analyst")).toBe("casual");
  });

  it("gold can switch between Analyst and Casual", () => {
    expect(canSwitchMode(gold)).toBe(true);
    expect(effectiveMode(gold, "casual")).toBe("casual");
    expect(effectiveMode(gold, "analyst")).toBe("analyst");
  });
});

describe("Archie's pointer to something locked (AG-5)", () => {
  it("names the phase that opens it", () => {
    expect(lockedPointerNote("first_half", silverS)).toEqual({ key: "archie.chart_locked", phase: "first_half" });
  });

  it("an Analyst-only chart gets the note of Data & Statistics", () => {
    expect(lockedPointerNote(null, gold)).toEqual({ key: "stats.switch_analyst" });
    expect(lockedPointerNote(null, silverS)).toEqual({ key: "stats.unlock_gold" });
  });
});

describe("sections follow the phase", () => {
  it("opens the section of the phase when it is unlocked", () => {
    expect(defaultSection("pre_match", sections(preMatch))).toBe("pre_match");
    expect(defaultSection("half_time", sections(halfTime))).toBe("data_stats");
    expect(defaultSection("full_time", sections(fullTime))).toBe("recap");
  });

  it("falls back to Pre-match when the section of the phase is locked", () => {
    expect(defaultSection("full_time", sections(preMatch))).toBe("pre_match");
  });
});

describe("team colours", () => {
  it("every team of the fake match gets a mark that reads on the background", () => {
    for (const t of match.teams) expect(contrast(teamMark(t.colours as TeamColours), BG)).toBeGreaterThanOrEqual(3);
  });

  it("SYN-001 keeps both home colours: amber and sky blue are different enough", () => {
    const [home, away] = match.teams.map((t) => t.colours as TeamColours);
    const m = teamMarks(home, away);
    expect(m).toEqual({ home: "#F2A900", away: "#8FC1E3" });
    expect(colourDistance(m.home, m.away)).toBeGreaterThanOrEqual(MIN_TEAM_DISTANCE);
  });

  it("switches the away team to its second kit when the two marks are too similar", () => {
    const home: TeamColours = { primary: "#F2A900", secondary: "#1B2A4A", number: "#FFFFFF" };
    const away: TeamColours = { primary: "#E0A800", secondary: "#222222", number: "#FFFFFF",
      away_kit: { shirt: "#1E88E5", shorts: "#FFFFFF", socks: "#1E88E5" } };
    const m = teamMarks(home, away);
    expect(m.home).toBe("#F2A900");
    expect(m.away).toBe("#1E88E5");
    expect(contrast(m.away, BG)).toBeGreaterThanOrEqual(3);
  });

  it("without a second kit, the away team keeps its own mark", () => {
    const c: TeamColours = { primary: "#F2A900", secondary: "#1B2A4A", number: "#FFFFFF" };
    expect(teamMarks(c, c).away).toBe("#F2A900");
  });
});

describe("API errors", () => {
  it("reads the contract's ApiError", () => {
    const e = toApiError(409, { code: "ticket_in_use", message: "In use", section: null, unlocks_at_phase: null });
    expect([e.status, e.code]).toEqual([409, "ticket_in_use"]);
  });

  it("maps FastAPI validation errors", () => {
    expect(toApiError(422, { detail: [] }).code).toBe("validation_error");
  });

  it("keeps the phase of a locked section", () => {
    const e = toApiError(423, { code: "section_locked", message: "", section: "recap", unlocks_at_phase: "full_time" });
    expect(e.unlocksAtPhase).toBe("full_time");
  });
});

describe("line-up pitch", () => {
  it("places every starter of the fake match inside the pitch, each team in its own half", async () => {
    const { POSITION_SPOT, spotOnPitch, PITCH_L, PITCH_W } = await import("./formation");
    for (const tm of match.teams) {
      for (const p of tm.lineup.filter((x) => x.starting)) {
        expect(POSITION_SPOT, p.position).toHaveProperty(p.position);
        const { x, y } = spotOnPitch(p.position, tm.role as "home" | "away");
        expect(x).toBeGreaterThan(0);
        expect(x).toBeLessThan(PITCH_W);
        if (tm.role === "home") expect(y).toBeGreaterThan(PITCH_L / 2);
        else expect(y).toBeLessThan(PITCH_L / 2);
      }
    }
  });

  it("mirrors left and right for the away team", async () => {
    const { spotOnPitch } = await import("./formation");
    expect(spotOnPitch("RB", "home").x).toBeGreaterThan(34);
    expect(spotOnPitch("RB", "away").x).toBeLessThan(34);
  });
});

describe("3 favourite players (contract v0.2.6)", () => {
  const prefs = (ids: string[], locked = false) => ({ favourite_player_ids: ids, favourites_locked: locked });
  it("adds and removes up to 3, then refuses a 4th", () => {
    expect(toggleFavourite(prefs([]), "BRA-09")).toEqual({ favourites: ["BRA-09"] });
    expect(toggleFavourite(prefs(["BRA-09", "BRA-06"]), "BRA-09")).toEqual({ favourites: ["BRA-06"] });
    expect(toggleFavourite(prefs(["BRA-09", "BRA-06", "CAL-09"]), "CAL-01")).toEqual({ refused: "full" });
  });
  it("can be confirmed only with exactly 3, and never changes after", () => {
    expect(canConfirmFavourites(prefs(["BRA-09", "BRA-06"]))).toBe(false);
    expect(canConfirmFavourites(prefs(["BRA-09", "BRA-06", "CAL-09"]))).toBe(true);
    expect(canConfirmFavourites(prefs(["BRA-09", "BRA-06", "CAL-09"], true))).toBe(false);
    expect(toggleFavourite(prefs(["BRA-09", "BRA-06", "CAL-09"], true), "BRA-09")).toEqual({ refused: "locked" });
  });
  it("matches the fake preferences of the contract", () => {
    expect(gold.preferences.favourites_locked).toBe(false);
    expect(gold.preferences.favourite_player_ids?.length).toBeLessThanOrEqual(MAX_FAVOURITES);
  });
});

describe("Live feed labels", () => {
  const whistles = (events.events as unknown as MatchEvent[]).filter((e) => e.type === "FinalWhistle");
  const label = (k: string) => (en as Record<string, string>)[k];

  it("the first-half FinalWhistle reads Half-time, the second-half one Final whistle", () => {
    expect(whistles.map((e) => e.period)).toEqual([1, 2]);
    expect(label(eventLabelKey(whistles[0]))).toBe("Half-time");
    expect(label(eventLabelKey(whistles[1]))).toBe("Final whistle");
  });

  it("a goal keeps its own label", () => {
    const goal = (events.events as unknown as MatchEvent[]).find((e) => e.type === "ShotAtGoal" && e.qualifiers?.outcome === "goal");
    expect(goal && eventLabelKey(goal)).toBe("event.goal");
  });
});

describe("country flags", () => {
  it("has a flag for every real nationality of the universe (Meridia is fictional: globe)", () => {
    const players = [...universe.players, ...universe.league_players] as { nationality: string }[];
    const countries = new Set(players.map((p) => p.nationality).filter((n) => n !== universe.meta.country));
    for (const n of countries) expect(FLAG_URIS[n], n).toMatch(/^data:image\/svg\+xml,%3Csvg/);
    expect(FLAG_URIS[universe.meta.country]).toBeUndefined();
  });
});
