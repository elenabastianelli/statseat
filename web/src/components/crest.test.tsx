import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "../api/client";
import { Crest } from "./Crest";
import scoreboardSource from "./Scoreboard.tsx?raw";
import preMatchSource from "../sections/PreMatch.tsx?raw";
import crestSource from "./Crest.tsx?raw";

describe("club crests", () => {
  it("the crest is the image from crestUrl, with the team name as alt text", () => {
    const html = renderToStaticMarkup(<Crest teamId="BRA" name="Brackwater Athletic" />);
    expect(html).toContain(`src="${api.crestUrl("BRA")}"`);
    expect(html).toContain('alt="Brackwater Athletic"');
  });

  it("Crest takes its image from api.crestUrl", () => {
    expect(crestSource).toContain("api.crestUrl(");
  });

  it("the scoreboard shows both crests, the pre-match one per team card", () => {
    expect(scoreboardSource.match(/<Crest /g)).toHaveLength(2);
    expect(preMatchSource).toMatch(/<Crest teamId=\{team\.team_id\}/);
  });
});
