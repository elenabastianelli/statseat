import { api } from "../api/client";
import { reasonFor, type GroundedValue, type Recap as RecapData } from "../api/types";
import { player, playerName, teamColour, teamOfPlayer, teamShort, useMatch } from "../app/MatchContext";
import { PairRow, SplitBar } from "../components/charts";
import { TraceView } from "../components/Inspector";
import { Card, Load, Unavailable } from "../components/Status";
import { Timeline } from "../components/Timeline";
import { useI18n } from "../i18n";
import { km, pct } from "../lib/format";
import { keyMetric, keyOwner, keyValue } from "../lib/stats";
import { useLoad } from "../lib/useLoad";

export function Recap() {
  const { mode, state } = useMatch();
  const recap = useLoad(() => api.recap(mode), [mode, state.phase]);
  const highlights = useLoad(api.highlights, [state.phase]);
  return (
    <Load {...recap}>
      {(r) => (
        <RecapView r={r} highlights={(highlights.data?.highlights ?? []).filter((h) => r.highlight_ids.includes(h.highlight_id))} />
      )}
    </Load>
  );
}

function RecapView({ r, highlights }: { r: RecapData; highlights: Parameters<typeof Timeline>[0]["highlights"] }) {
  const { t } = useI18n();
  const { overview, session, inspectorOn } = useMatch();
  const h = session.match.home_team_id;
  const a = session.match.away_team_id;
  const ch = teamColour(overview, h);
  const ca = teamColour(overview, a);
  const poss = (id: string) => r.possession.find((p) => p.team_id === id)?.value ?? 0;
  const shots = (id: string) => r.shots.find((s) => s.team_id === id);
  const physical = reasonFor(r.unavailable, "favourite_players.distance", "favourite_players.top_speed_kmh");
  /** What a key number is and whose: "Possession · Brackwater", "Top speed · <player>". */
  const keyLabel = (k: GroundedValue) => {
    const key = KEY_METRICS[keyMetric(k)];
    const metric = key ? t(key) : keyMetric(k) === "xg" ? "xG" : keyMetric(k).replaceAll("_", " ");
    const owner = keyOwner(k, h, a);
    const who = owner.player_id ? playerName(overview, owner.player_id) : owner.team_id ? teamShort(overview, owner.team_id) : "";
    return who ? `${metric} · ${who}` : metric;
  };
  return (
    <>
      <Card>
        <h2>{r.headline}</h2>
        <p>{r.narrative}</p>
        {inspectorOn && r.inspector && <TraceView trace={r.inspector} />}
      </Card>
      {r.key_numbers.length > 0 && (
        <Card title={t("recap.key_numbers")}>
          <p className="small muted">{t("recap.key_numbers_hint")}</p>
          <div className="tiles">
            {r.key_numbers.map((k) => (
              <div className="tile" key={k.source_field + k.text}>
                <span className="tile-value">{keyValue(k)}</span>
                <span className="small muted">{keyLabel(k)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card>
        {r.possession.length > 0
          ? <SplitBar home={{ value: poss(h), label: pct(poss(h)), colour: ch }} away={{ value: poss(a), label: pct(poss(a)), colour: ca }} />
          : <Unavailable reason={reasonFor(r.unavailable, "possession")} />}
        <PairRow label={t("recap.shots")} home={shots(h)?.shots ?? 0} away={shots(a)?.shots ?? 0} homeColour={ch} awayColour={ca} />
        <PairRow label={t("live.on_target_label")} home={shots(h)?.on_target ?? 0} away={shots(a)?.on_target ?? 0} homeColour={ch} awayColour={ca} />
      </Card>
      {highlights.length > 0 && (
        <>
          <h3 className="pad-x">{t("recap.highlights")}</h3>
          <Timeline highlights={highlights} />
        </>
      )}
      {r.favourite_players.length > 0 && (
        <Card title={t("recap.favourites")}>
          {physical && <Unavailable reason={physical} />}
          {r.favourite_players.map((f) => {
            const p = player(overview, f.player_id);
            return (
              <div key={f.player_id} className="fav">
                <p className="row">
                  <strong>{p?.name ?? f.player_id}</strong>
                  <span className="small muted">{teamShort(overview, teamOfPlayer(overview, f.player_id))} · {t("recap.played", { n: f.minutes_played })}</span>
                </p>
                <div className="tiles small-tiles">
                  <div className="tile"><span className="tile-value">{f.goals}</span><span className="small muted">{t("recap.goals")}</span></div>
                  <div className="tile"><span className="tile-value">{f.shots}</span><span className="small muted">{t("recap.shots")}</span></div>
                  <div className="tile" title={f.distance ? undefined : physical}>
                    <span className="tile-value">{f.distance ? km(f.distance) : "—"}</span><span className="small muted">{t("live.distance")}</span>
                  </div>
                  <div className="tile" title={f.top_speed_kmh != null ? undefined : physical}>
                    <span className="tile-value">{f.top_speed_kmh != null ? f.top_speed_kmh.toFixed(1) : "—"}</span>
                    <span className="small muted">{t("recap.top_speed")} km/h</span>
                  </div>
                  <div className="tile"><span className="tile-value">{f.passes ?? "—"}</span>
                    <span className="small muted">{t("recap.passes")}{f.pass_accuracy_pct != null ? ` · ${pct(f.pass_accuracy_pct)}` : ""}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </Card>
      )}
    </>
  );
}

/** The translated label of each key number, by the first field of its `source_field`. */
const KEY_METRICS: Record<string, string> = {
  possession: "live.possession", shots: "recap.shots", top_speed: "recap.top_speed", shot_speed: "live.shot_speed",
};
