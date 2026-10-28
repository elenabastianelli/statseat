import { api } from "../api/client";
import type { LiveStats, MatchEvent } from "../api/types";
import { playerName, teamColour, teamShort, useMatch } from "../app/MatchContext";
import { PairRow } from "../components/charts";
import { Card, Load } from "../components/Status";
import { Timeline } from "../components/Timeline";
import { LIVE_POLL_MS } from "../config";
import { useI18n } from "../i18n";
import { eventLabelKey } from "../lib/format";
import { useLoad } from "../lib/useLoad";

/** Live, only during play: the key numbers per team, then the highlights and the event timeline (L-2f). */
export function Live() {
  const { state } = useMatch();
  const stats = useLoad(api.liveStats, [state.phase], LIVE_POLL_MS);
  const events = useLoad(api.events, [state.phase], LIVE_POLL_MS);
  const highlights = useLoad(api.highlights, [state.phase], LIVE_POLL_MS);
  return (
    <>
      <Load {...stats}>{(s) => <Headline s={s} />}</Load>
      {highlights.data && highlights.data.highlights.length > 0 && <Timeline highlights={highlights.data.highlights} />}
      <Load {...events}>{(e) => <Feed events={e.events} />}</Load>
    </>
  );
}

/** The key numbers per team, and nothing else (25/10): every other statistic is in Data & Statistics. */
function Headline({ s }: { s: LiveStats }) {
  const { t } = useI18n();
  const { session, overview } = useMatch();
  const h = session.match.home_team_id;
  const a = session.match.away_team_id;
  const ch = teamColour(overview, h);
  const ca = teamColour(overview, a);
  const shots = (id: string) => s.shots.find((x) => x.team_id === id);
  const counts = (id: string) => s.counts.find((x) => x.team_id === id);
  const rows: [string, (id: string) => number][] = [
    [t("recap.goals"), (id) => shots(id)?.goals ?? 0],
    [t("live.shots"), (id) => shots(id)?.shots ?? 0],
    [t("live.on_target_label"), (id) => shots(id)?.on_target ?? 0],
    [t("count.penalties"), (id) => counts(id)?.penalties ?? 0],
    [t("count.yellow_cards"), (id) => counts(id)?.yellow_cards ?? 0],
    [t("count.red_cards"), (id) => counts(id)?.red_cards ?? 0],
  ];
  return (
    <Card title={t("recap.key_numbers")}>
      {rows.map(([label, v]) => <PairRow key={label} label={label} home={v(h)} away={v(a)} homeColour={ch} awayColour={ca} />)}
    </Card>
  );
}

function Feed({ events }: { events: MatchEvent[] }) {
  const { t } = useI18n();
  const { overview } = useMatch();
  const shown = events.filter((e) => e.type !== "Foul").slice().reverse();
  return (
    <Card title={t("live.feed")}>
      {shown.length === 0 ? <p className="muted">{t("live.no_events")}</p> : (
        <ul className="feed">
          {shown.map((e) => {
            const outcome = e.qualifiers?.outcome as string | undefined;
            const isGoal = e.type === "ShotAtGoal" && outcome === "goal";
            return (
              <li key={e.event_id} style={{ borderColor: teamColour(overview, e.team_id) }}>
                <span className="feed-min">{e.minute}'</span>
                <span className="grow">
                  <strong>{t(eventLabelKey(e))}</strong>{" "}
                  {e.player_id && playerName(overview, e.type === "Substitution" ? e.secondary_player_id : e.player_id)}
                  {e.type === "Substitution" && e.player_id && <span className="muted"> {t("event.on_for", { name: playerName(overview, e.player_id) })}</span>}
                  {e.type === "ShotAtGoal" && !isGoal && outcome && <span className="muted"> · {t(`event.${outcome}`)}</span>}
                </span>
                {e.team_id && <span className="small muted">{teamShort(overview, e.team_id)}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
