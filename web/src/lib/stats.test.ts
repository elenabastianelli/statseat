import { describe, expect, it } from "vitest";
import models from "../../../app/contract/models.py?raw";
import casual from "../../../app/contract/fake/stats_casual_full_match.json";
import analyst from "../../../app/contract/fake/stats_analyst_full_match.json";
import dataStats from "../sections/DataStats.tsx?raw";
import type { GroundedValue, MatchStats, MetricUnavailable, Recap } from "../api/types";
import { DICTIONARIES } from "../i18n";
import recapAnalyst from "../../../app/contract/fake/recap_analyst.json";
import {
  availability, DATA_STATS_TABLES, fasterTeam, keyMetric, keyOwner, keyValue, mirrorGeometry, mirrorPath, niceFloor,
  playersInOrder, RELIABILITY_SYMBOL, shotRadius, shotSymbol, splitSigned,
} from "./stats";

const fan = casual as unknown as MatchStats;
const pro = analyst as unknown as MatchStats;

describe("Data & Statistics tables (L-4)", () => {
  it("are the contract's DATA_STATS_TABLES, in its order", () => {
    const m = models.match(/DATA_STATS_TABLES = \(([^)]*)\)/);
    expect(m).not.toBeNull();
    expect([...m![1].matchAll(/"(\w+)"/g)].map((x) => x[1])).toEqual([...DATA_STATS_TABLES]);
  });

  it("every label the section uses exists in English (and so in every language)", () => {
    const en = DICTIONARIES.en as Record<string, string>;
    const keys = [...dataStats.matchAll(/\bt\(\s*"([\w.]+)"/g)].map((x) => x[1]);
    expect(keys.length).toBeGreaterThan(40);
    for (const k of keys) expect(en, k).toHaveProperty(k);
    for (const id of DATA_STATS_TABLES) expect(en, id).toHaveProperty(`table.${id}`);
    const cols = dataStats.match(/PLAYER_COLS = \[([^\]]*)\]/)![1];
    for (const c of cols.matchAll(/"(\w+)"/g)) expect(en, c[1]).toHaveProperty(`col.${c[1]}`);
  });
});

describe("reliability and availability", () => {
  it("has one symbol per contract level", () => {
    expect(RELIABILITY_SYMBOL).toEqual({ high: "●", medium: "◐", low: "○" });
    for (const t of pro.distance_covered!.teams) expect(RELIABILITY_SYMBOL[t.distance.reliability.level]).toBeTruthy();
  });

  it("shows 'Available from' only with a minute, hides the card otherwise", () => {
    const list: MetricUnavailable[] = [
      { metric: "peak_intensity", reason: "needs five complete minutes of play", available_from_minute: 5 },
      { metric: "xg", reason: "needs tracking frames", available_from_minute: null },
    ];
    expect(availability(list, "peak_intensity")).toEqual({ from: 5 });
    expect(availability(list, "xg_cumulative", "xg")).toBe("hidden");
    expect(availability(list, "momentum")).toBeUndefined();
    expect(availability(null, "xg")).toBeUndefined();
  });
});

describe("shot map", () => {
  it("fills goals, crosses woodwork, leaves every other shot hollow", () => {
    expect(shotSymbol("goal")).toBe("filled");
    expect(shotSymbol("woodwork")).toBe("cross");
    for (const o of ["saved", "wide", "blocked"] as const) expect(shotSymbol(o)).toBe("hollow");
  });

  it("sizes dots by xG only in Analyst, with the area growing with xG", () => {
    expect(shotRadius(0.6, false)).toBe(shotRadius(0.05, false));
    expect(shotRadius(0.6, true)).toBeGreaterThan(shotRadius(0.05, true));
    expect(shotRadius(null, true)).toBe(shotRadius(0.6, false));
    expect(fan.shot_map.shots.every((s) => s.xg == null)).toBe(true); // Fan: no xG per shot to size by
  });
});

describe("mirror chart", () => {
  const home = { points: [{ minute: 0, value: 0 }, { minute: 10, value: 2 }], above: true };
  const away = { points: [{ minute: 0, value: 0 }, { minute: 10, value: 1 }], above: false };

  it("puts the home team above the axis and the away team below, on one scale", () => {
    const g = mirrorGeometry([home, away], 300, 120);
    expect(g.axisY).toBe(60);
    expect(g.y(2, true)).toBeLessThan(g.axisY);
    expect(g.y(1, false)).toBeGreaterThan(g.axisY);
    expect(g.axisY - g.y(2, true)).toBeCloseTo(2 * (g.y(1, false) - g.axisY));
    expect(g.x(0)).toBe(0);
    expect(g.x(10)).toBe(300);
  });

  it("runs the time axis to the current minute and draws closed areas and step lines", () => {
    const g = mirrorGeometry([home], 300, 120, undefined, { from: 0, to: 45 });
    expect(g.to).toBe(45);
    expect(mirrorPath(home, g, false, true)).toMatch(/^M0\.0,60\.0L.*Z$/);
    expect(mirrorPath(home, g, true)).toContain("H");
    expect(mirrorPath({ points: [], above: true }, g)).toBe("");
  });

  it("can start the axis from a round floor below the lowest value, never cutting a series", () => {
    const hi = { points: [{ minute: 0, value: 120 }, { minute: 5, value: 134 }], above: true };
    const lo = { points: [{ minute: 0, value: 110 }, { minute: 5, value: 128 }], above: false };
    const g = mirrorGeometry([hi, lo], 300, 120, undefined, undefined, false);
    expect(g.floor).toBe(85);
    expect(g.floor).toBeLessThan(110);
    expect(g.y(110, false)).toBeGreaterThan(g.axisY);
    expect(mirrorGeometry([hi, lo], 300, 120).floor).toBe(0);
    expect([niceFloor(86), niceFloor(1180), niceFloor(0.42), niceFloor(-3)]).toEqual([85, 1000, 0.4, 0]);
  });

  it("splits momentum into the home part above and the away part below", () => {
    const { home: h, away: a } = splitSigned([{ minute: 1, value: 0.4 }, { minute: 2, value: -0.3 }]);
    expect(h.map((p) => p.value)).toEqual([0.4, 0]);
    expect(a.map((p) => p.value)).toEqual([0, 0.3]);
  });
});

describe("tables from the fake data", () => {
  it("names the team that moves the ball faster", () => {
    const teams = pro.ball_circulation!.teams;
    const winner = teams[0].value > teams[1].value ? teams[0].team_id : teams[1].team_id;
    expect(fasterTeam(teams)).toBe(winner);
    expect(fasterTeam([{ team_id: "BRA", value: 9 }, { team_id: "CAL", value: 9 }])).toBeNull();
  });

  it("lists every player of the Analyst table, home team first", () => {
    const rows = playersInOrder(pro, "BRA");
    expect(rows).toHaveLength(pro.players!.length);
    const firstAway = rows.findIndex((r) => r.team_id !== "BRA");
    expect(rows.slice(firstAway).every((r) => r.team_id !== "BRA")).toBe(true);
    expect(playersInOrder(fan, "BRA")).toEqual([]); // Fan: the table is blurred
  });

  it("keeps the Analyst-only numbers out of the Fan payload", () => {
    expect(fan.ppda).toBeNull();
    expect(fan.players).toBeNull();
    expect(fan.passes.every((p) => p.avg_length_m == null && p.long_balls == null)).toBe(true);
    expect(pro.ppda).toHaveLength(2);
  });
});

describe("Recap key numbers", () => {
  const keys = (recapAnalyst as unknown as Recap).key_numbers;
  const top: GroundedValue = {
    text: "34.5 km/h", value: 34.5, unit: "km/h", tool: "get_match_stats",
    arguments: { scope: "full_match", player_id: "BRA-27" }, source_field: "top_speed[0].kmh",
  };
  const shots: GroundedValue = {
    text: "13 shots", value: 13, unit: "shots", tool: "get_match_stats", arguments: { scope: "full_match" },
    source_field: "shots[1].shots",
  };

  it("say whose they are: the player, else the team", () => {
    expect(keyOwner(top, "BRA", "CAL")).toEqual({ player_id: "BRA-27" });
    expect(keyOwner(shots, "BRA", "CAL")).toEqual({ team_id: "CAL" });
    expect(keys.map((k) => keyOwner(k, "BRA", "CAL"))).toEqual([{ team_id: "BRA" }, {}, { team_id: "BRA" }]);
  });

  it("show a count without its word and keep the other units", () => {
    expect(keyValue(shots)).toBe("13");
    expect(keyValue(top)).toBe("34.5 km/h");
    expect(keys.map(keyValue)).toEqual(["54.0 %", "108.4 km/h", "1.86"]);
  });

  it("name the metric of the source field", () => {
    expect([top, shots, ...keys].map(keyMetric)).toEqual(["top_speed", "shots", "possession", "shot_speed", "xg"]);
  });
});
