import { describe, expect, it } from "vitest";
import openapi from "../../../app/contract/openapi.json";
import { DICTIONARIES, LANGS, translate } from ".";

const enumOf = (name: string): string[] =>
  (openapi.components.schemas as Record<string, { enum?: string[] }>)[name]?.enum ?? [];

describe("translations", () => {
  it("every language has exactly the keys of English", () => {
    for (const [lang, dict] of Object.entries(DICTIONARIES)) {
      expect(Object.keys(dict).sort(), lang).toEqual(Object.keys(DICTIONARIES.en).sort());
    }
  });

  it("every translation keeps the variables of the English text", () => {
    const vars = (s: string) => [...s.matchAll(/\{\w+\}/g)].map((m) => m[0]).sort();
    const en = DICTIONARIES.en as Record<string, string>;
    for (const [lang, dict] of Object.entries(DICTIONARIES)) {
      for (const [k, v] of Object.entries(dict as Record<string, string>)) expect(vars(v), `${lang} ${k}`).toEqual(vars(en[k]));
    }
  });

  it("has the eight UI languages, English first", () => {
    expect(LANGS).toEqual(["en", "fr", "es", "de", "it", "nl", "zh", "ru"]);
  });

  it("no translation is empty", () => {
    for (const dict of Object.values(DICTIONARIES)) {
      for (const [k, v] of Object.entries(dict)) expect(v, k).not.toBe("");
    }
  });

  it.each([
    ["Phase", "phase"],
    ["Section", "section"],
    ["TicketTier", "tier"],
    ["Mode", "mode"],
    ["EventType", "event"],
  ])("every %s value of the contract has a label", (schema, prefix) => {
    const values = enumOf(schema);
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(DICTIONARIES.en, `${prefix}.${v}`).toHaveProperty(`${prefix}.${v}`);
  });

  it("every phase after kick-off has an 'opens' label", () => {
    for (const p of enumOf("Phase").filter((p) => p !== "pre_match")) {
      expect(DICTIONARIES.en).toHaveProperty(`opens.${p}`);
    }
  });

  it("fills variables and falls back to English, then to the key", () => {
    expect(translate("it", "pre.matchday", { n: 9 })).toBe("Giornata 9");
    expect(translate("it", "no.such.key")).toBe("no.such.key");
  });
});
