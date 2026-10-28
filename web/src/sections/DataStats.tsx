import { useEffect, type ReactNode } from "react";
import { api } from "../api/client";
import type { ChartRef, Distance, MatchStats, TeamId, TimePoint } from "../api/types";
import { playerName, teamColour, teamShort, useMatch } from "../app/MatchContext";
import { GroupedBars, MirrorChart, PairRow, ShotMapChart, SplitBar, ZoneGrid } from "../components/charts";
import { Icon } from "../components/Icon";
import { MethodsView } from "../components/Inspector";
import { Card, Load } from "../components/Status";
import { useI18n } from "../i18n";
import { km, pct } from "../lib/format";
import { canSwitchMode } from "../lib/phases";
import {
  availability, DATA_STATS_TABLES, fasterTeam, playersInOrder, RELIABILITY_SYMBOL, splitSigned, type TableId,
} from "../lib/stats";
import { useLoad } from "../lib/useLoad";

type Stats = MatchStats;
type Focus = NonNullable<Stats["player_focus"]>[number];
type Reliability = Distance["reliability"];

/** Charts whose data field has another name in the payload (and in `unavailable`). */
const CHART_METRIC: Record<string, string> = { xg_cumulative: "xg" };
/** Metrics the backend still serves but the app does not show, as charts or in the Inspector: acceleration load is
 * not reliable enough (S-10 review). */
const HIDDEN_CHARTS = new Set(["acceleration_load"]);

/**
 * Data & Statistics, from kick-off to the end (L-4, contract v0.3.0): the six tables of `DATA_STATS_TABLES` first, in
 * that order, then the charts the backend lists for this version, in its order. Every table and chart has the id
 * `chart-<id>`, so Archie points to a table as to a chart. In Fan the Analyst charts follow, blurred.
 */
export function DataStats() {
  const { t, tOr } = useI18n();
  const { mode, state, inspectorOn, focusChart } = useMatch();
  const stats = useLoad(() => api.stats(mode), [mode, state.phase]);

  useEffect(() => {
    if (stats.data && focusChart) document.getElementById(`chart-${focusChart}`)?.scrollIntoView({ behavior: "smooth" });
  }, [stats.data, focusChart]);

  return (
    <Load {...stats}>
      {(s) => (
        <>
          <p className="eyebrow pad-x">
            {t(`stats.scope.${s.scope}`)} · {t("stats.up_to", { m: s.up_to_minute })} · {t(`mode.${s.mode}`)}
          </p>
          {DATA_STATS_TABLES.map((id) => <Table key={id} id={id} s={s} />)}
          {s.charts.filter((c) => c.modes.includes(s.mode)).map((c) => <Chart key={c.chart_id} c={c} s={s} />)}
          {(s.locked_charts ?? []).filter((c) => !HIDDEN_CHARTS.has(c.chart_id)).map((c) => <Locked key={c.chart_id} id={c.chart_id} title={tOr(`chart.${c.chart_id}`, c.title)} />)}
          {inspectorOn && s.inspector && <MethodsView inspector={{
            ...s.inspector, methods: s.inspector.methods.filter((m) => !HIDDEN_CHARTS.has(m.metric)),
          }} />}
        </>
      )}
    </Load>
  );
}

function useTeams() {
  const { session, overview } = useMatch();
  const h = session.match.home_team_id;
  const a = session.match.away_team_id;
  return { h, a, ch: teamColour(overview, h), ca: teamColour(overview, a), overview };
}

const of = <T extends { team_id: string }>(list: T[] | null | undefined, id: TeamId) => list?.find((x) => x.team_id === id);

/** ● ◐ ○ next to a tracking-based number; the share measured is in the tooltip (the Inspector explains it). */
function Rel({ r }: { r: Reliability | null | undefined }) {
  const { t } = useI18n();
  if (!r) return null;
  const label = t("stats.reliability", { n: r.measured_pct.toFixed(1) });
  return <span className={`rel rel-${r.level}`} title={label} aria-label={label}>{RELIABILITY_SYMBOL[r.level]}</span>;
}

function distanceText(d: Distance): ReactNode {
  return <>{km(d)} <Rel r={d.reliability} /></>;
}

/** A team's distance in a team-against-team row: the unit is in the label, so the value fits with its symbol. */
function teamKm(d: Distance | undefined): ReactNode {
  return d ? <>{d.approx ? "≈" : ""}{d.km.toFixed(1)} <Rel r={d.reliability} /></> : null;
}

/** "Available from 5'" for a metric that is coming; nothing for one that is not available for this source. */
function Pending({ s, metrics }: { s: Stats; metrics: string[] }) {
  const { t } = useI18n();
  const a = availability(s.unavailable, ...metrics);
  return a && a !== "hidden" ? <p className="row small muted"><span>{t("stats.available_from", { m: a.from })}</span></p> : null;
}

/** A row of the team-against-team tables; nothing when a value is missing. */
function useRow() {
  const { ch, ca } = useTeams();
  return (label: string, hv: number | null | undefined, av: number | null | undefined, format?: (v: number) => string,
    texts?: { home?: ReactNode; away?: ReactNode }, unit?: string) =>
    hv == null || av == null ? null : (
      <PairRow key={label} label={unit ? <>{label}<br />{unit}</> : label} home={hv} away={av} homeColour={ch}
        awayColour={ca} format={format} homeText={texts?.home} awayText={texts?.away} />
    );
}

/** A line with one player per team (top runner, top passer...), in the team colours. */
function Leaders({ label, items }: { label: string; items: { team_id: TeamId; text: string }[] }) {
  const { overview } = useTeams();
  if (items.length === 0) return null;
  return (
    <div className="leaders small">
      <span className="muted">{label}</span>
      {items.map((x) => <span key={x.team_id} style={{ color: teamColour(overview, x.team_id) }}>{x.text}</span>)}
    </div>
  );
}

function Table({ id, s }: { id: TableId; s: Stats }) {
  const { t } = useI18n();
  const body = {
    match_table: () => <MatchTable s={s} />,
    player_focus: () => <FocusTable s={s} />,
    running_table: () => <RunningTable s={s} />,
    ball_table: () => <BallTable s={s} />,
    defence_table: () => <DefenceTable s={s} />,
    players_table: () => (s.mode === "analyst" ? <PlayersTable s={s} /> : null),
  }[id]();
  if (id === "players_table" && s.mode !== "analyst") return <Locked id={id} title={t(`table.${id}`)} />;
  return <Card title={t(`table.${id}`)} id={`chart-${id}`}>{body}</Card>;
}

/** Goals, shots, possession, set pieces and discipline, effective playing time, goals vs xG (METRICS.md §7-12, 20, 23, 26). */
function MatchTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, a, ch, ca } = useTeams();
  const row = useRow();
  const ch_ = of(s.counts, h);
  const ca_ = of(s.counts, a);
  const poss = (id: TeamId) => of(s.possession?.teams, id)?.value ?? 0;
  const e = s.effective_playing_time;
  const gx = s.goals_vs_xg ?? [];
  return (
    <>
      {row(t("recap.goals"), of(s.shots, h)?.goals, of(s.shots, a)?.goals)}
      {row(t("live.shots"), of(s.shots, h)?.shots, of(s.shots, a)?.shots)}
      {row(t("live.on_target_label"), of(s.shots, h)?.on_target, of(s.shots, a)?.on_target)}
      {s.possession ? (
        <div className="pad-y">
          <p className="small muted center">{t("live.possession")}</p>
          <SplitBar home={{ value: poss(h), label: pct(poss(h)), colour: ch }} away={{ value: poss(a), label: pct(poss(a)), colour: ca }} />
        </div>
      ) : <Pending s={s} metrics={["possession"]} />}
      {ch_ && ca_ && MATCH_COUNTS.map((k) => row(t(`count.${k}`), ch_[k], ca_[k]))}
      {gx.length === 2 ? (
        <div className="pad-y">
          <p className="small muted center">{t("stats.goals_vs_xg")}</p>
          <Leaders label="" items={[h, a].map((id) => {
            const g = of(gx, id)!;
            return { team_id: id, text: t("stats.goals_xg_row", { g: g.goals, x: g.xg.toFixed(2) }) };
          })} />
        </div>
      ) : <Pending s={s} metrics={["goals_vs_xg"]} />}
      {e ? (
        <div className="pad-y small">
          <p className="row"><span className="muted">{t("stats.effective")}</span>
            <span>{t("stats.in_play", { n: e.total_min.toFixed(0), total: e.played_min.toFixed(0) })}</span></p>
          {e.first_half_min != null && (
            <p className="row muted"><span />
              <span>{e.second_half_min != null
                ? t("stats.in_play_halves", { a: e.first_half_min.toFixed(0), b: e.second_half_min.toFixed(0) })
                : t("stats.in_play_first", { a: e.first_half_min.toFixed(0) })}</span></p>
          )}
        </div>
      ) : <Pending s={s} metrics={["effective_playing_time"]} />}
    </>
  );
}

const MATCH_COUNTS = ["corners", "fouls", "yellow_cards", "red_cards", "penalties", "woodwork", "offsides"] as const;

/** The Player Focus card of each favourite (METRICS.md, "Favourite players"): Fan numbers, plus the Analyst extras. */
function FocusTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const focus = s.player_focus ?? [];
  if (focus.length === 0) return <p className="small muted">{t("stats.focus_empty")}</p>;
  return <>{focus.map((f) => <FocusCard key={f.player_id} f={f} analyzer={s.mode === "analyst"} />)}</>;
}

function FocusCard({ f, analyzer }: { f: Focus; analyzer: boolean }) {
  const { t } = useI18n();
  const { overview } = useTeams();
  const colour = teamColour(overview, f.team_id);
  const tile = (label: string, value: ReactNode) => (
    <div className="focus-item" key={label}><span className="muted">{label}</span><strong>{value}</strong></div>
  );
  const n = (v: number | null | undefined, digits = 0) => (v == null ? "—" : v.toFixed(digits));
  const zones = f.speed_zones;
  return (
    <div className="fav">
      <p className="row">
        <strong style={{ color: colour }}>{playerName(overview, f.player_id)}</strong>
        <span className="small muted">{teamShort(overview, f.team_id)} · {t("recap.played", { n: f.minutes_played })}</span>
      </p>
      <div className="focus-grid small">
        {tile(t("live.distance"), f.distance ? distanceText(f.distance) : "—")}
        {tile(t("live.top_speed"), f.top_speed_kmh != null
          ? `${f.top_speed_kmh.toFixed(1)} km/h${f.top_speed_minute != null ? ` · ${f.top_speed_minute}'` : ""}` : "—")}
        {tile(t("count.sprints"), n(f.sprints))}
        {tile(t("stats.passes"), f.passes != null
          ? `${f.passes}${f.pass_accuracy_pct != null ? ` · ${pct(f.pass_accuracy_pct)}` : ""}` : "—")}
        {tile(`${t("recap.shots")} · ${t("recap.goals")}`, `${f.shots ?? 0} · ${f.goals ?? 0}`)}
        {tile(t("stats.touches"), n(f.touches))}
        {tile(`${t("count.tackles")} · ${t("count.ball_recoveries")}`, `${n(f.tackles)} · ${n(f.ball_recoveries)}`)}
        {tile(t("stats.fouls_cw"), `${f.fouls_committed ?? 0} · ${f.fouls_won ?? 0}`)}
        {tile(t("stats.cards"), `${f.yellow_cards ?? 0} · ${f.red_cards ?? 0}`)}
        {analyzer && tile("xG", n(f.xg, 2))}
      </div>
      {analyzer && zones && (
        <>
          <GroupedBars bins={ZONE_BINS} series={[{ values: zoneValues(zones), colour }]} />
          <p className="small muted center">{t("stats.speed_zones")}</p>
        </>
      )}
      {analyzer && f.zone_map && (
        <><ZoneGrid zone={f.zone_map} colour={colour} /><p className="small muted center">{t("stats.attacks_right")}</p></>
      )}
    </div>
  );
}

const ZONE_BINS = ["0–7", "7–15", "15–20", "20–25", ">25"];
type Zones = NonNullable<Focus["speed_zones"]>;
const zoneValues = (z: Zones) => [z.km_0_7, z.km_7_15, z.km_15_20, z.km_20_25, z.km_over_25];

/** Distance, top speed, sprints, peak intensity (METRICS.md §1, 3, 4, 19); acceleration load (§22) is not shown. */
function RunningTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, a, overview } = useTeams();
  const row = useRow();
  const d = s.distance_covered;
  const lead = (key: "top_runner" | "top_sprinter", fmt: (v: number) => string) =>
    (s.leaders ?? []).filter((x) => x[key]).map((x) => ({
      team_id: x.team_id, text: `${playerName(overview, x[key]!.player_id)} · ${fmt(x[key]!.value)}`,
    }));
  const speeds = (id: TeamId) => s.top_speed.filter((x) => x.team_id === id);
  const fastest = (id: TeamId) => speeds(id)[0];
  const sp = (id: TeamId) => of(d?.teams, id)?.splits;
  return (
    <>
      {d ? (
        <>
          {row(`${t("live.distance")} (km)`, of(d.teams, h)?.distance.km, of(d.teams, a)?.distance.km, undefined, {
            home: teamKm(of(d.teams, h)?.distance), away: teamKm(of(d.teams, a)?.distance),
          })}
          <Leaders label={t("live.top_runner")} items={lead("top_runner", (v) => `${v.toFixed(2)} km`)} />
          {sp(h) && sp(a) && (
            <>
              {row(t("stats.ball_in_play_km"), sp(h)!.ball_in_play_km, sp(a)!.ball_in_play_km, (v) => `${v.toFixed(1)} km`)}
              {row(t("stats.ball_stopped_km"), sp(h)!.ball_stopped_km, sp(a)!.ball_stopped_km, (v) => `${v.toFixed(1)} km`)}
              {row(t("stats.in_possession"), sp(h)!.in_possession_km, sp(a)!.in_possession_km, (v) => `${v.toFixed(1)} km`)}
              {row(t("stats.out_of_possession"), sp(h)!.out_of_possession_km, sp(a)!.out_of_possession_km, (v) => `${v.toFixed(1)} km`)}
            </>
          )}
        </>
      ) : <Pending s={s} metrics={["distance_covered"]} />}
      {s.top_speed.length > 0 && (
        <>
          {row(t("live.top_speed"), fastest(h)?.kmh, fastest(a)?.kmh, (v) => `${v.toFixed(1)} km/h`)}
          {s.mode === "analyst"
            ? [h, a].map((id) => (
              <ol key={id} className="ranks small" style={{ color: teamColour(overview, id) }}>
                {speeds(id).map((x) => <li key={x.player_id}>{playerName(overview, x.player_id)} · {x.kmh.toFixed(1)} km/h · {x.minute}'</li>)}
              </ol>
            ))
            : <Leaders label={t("stats.fastest")} items={[h, a].filter(fastest).map((id) => ({
              team_id: id, text: `${playerName(overview, fastest(id)!.player_id)} · ${fastest(id)!.minute}'`,
            }))} />}
        </>
      )}
      {row(t("count.sprints"), of(s.counts, h)?.sprints, of(s.counts, a)?.sprints)}
      <Leaders label={t("stats.top_sprinter")} items={lead("top_sprinter", (v) => v.toFixed(0))} />
      {s.peak_intensity ? (
        <Leaders label={t("stats.peak")} items={s.peak_intensity.teams.map((p) => ({
          team_id: p.team_id, text: `${p.start_minute}'–${p.end_minute}' · ${p.metres_per_minute.toFixed(1)} m/min`,
        }))} />
      ) : <Pending s={s} metrics={["peak_intensity"]} />}
    </>
  );
}

/** Passes, ball circulation, hardest shots, counter-attacks, most touches (METRICS.md §5, 6, 11, 24, 25). */
function BallTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, a, overview } = useTeams();
  const row = useRow();
  const p = s.passes ?? [];
  const circ = s.ball_circulation;
  const faster = circ ? fasterTeam(circ.teams) : null;
  const ca = s.counter_attacks;
  const touches = (s.leaders ?? []).filter((x) => (x.most_touches ?? []).length > 0);
  return (
    <>
      {p.length > 0 ? (
        <>
          {row(t("stats.passes"), of(p, h)?.passes, of(p, a)?.passes)}
          {row(t("stats.pass_accuracy"), of(p, h)?.accuracy_pct, of(p, a)?.accuracy_pct, (v) => pct(v, 1))}
          <Leaders label={t("stats.top_passer")} items={(s.leaders ?? []).filter((x) => x.top_passer).map((x) => ({
            team_id: x.team_id, text: `${playerName(overview, x.top_passer!.player_id)} · ${x.top_passer!.value.toFixed(0)}`,
          }))} />
          {row(t("stats.avg_length_label"), of(p, h)?.avg_length_m, of(p, a)?.avg_length_m, (v) => `${v.toFixed(1)} m`)}
          {row(t("stats.long_balls"), of(p, h)?.long_balls, of(p, a)?.long_balls)}
        </>
      ) : <Pending s={s} metrics={["passes"]} />}
      {circ ? (
        <>
          {row(`${t("live.ball_circulation")} (km/h)`, of(circ.teams, h)?.value, of(circ.teams, a)?.value, undefined, {
            home: <>{of(circ.teams, h)?.value.toFixed(1)} <Rel r={of(circ.teams, h)?.reliability} /></>,
            away: <>{of(circ.teams, a)?.value.toFixed(1)} <Rel r={of(circ.teams, a)?.reliability} /></>,
          })}
          {faster && <p className="small muted center">{t("stats.faster", { team: teamShort(overview, faster) })}</p>}
        </>
      ) : <Pending s={s} metrics={["ball_circulation"]} />}
      <Leaders label={t("live.shot_speed")} items={(s.shot_speed ?? []).map((x) => ({
        team_id: x.team_id, text: `${playerName(overview, x.player_id)} · ${x.kmh.toFixed(0)} km/h · ${x.minute}'`,
      }))} />
      {ca ? (
        <>
          {row(t("stats.counter_attacks"), of(ca.teams, h)?.count, of(ca.teams, a)?.count)}
          {(ca.items ?? []).length > 0 && (
            <p className="small muted center">
              {ca.items!.map((x, i) => (
                <span key={x.shot_event_id} style={{ color: teamColour(overview, x.team_id) }}>{i ? " · " : ""}{x.minute}'</span>
              ))}
            </p>
          )}
        </>
      ) : <Pending s={s} metrics={["counter_attacks"]} />}
      {touches.length > 0 && (
        <div className="leaders small">
          <span className="muted">{t("stats.most_touches")}</span>
          {touches.map((x) => (
            <span key={x.team_id} style={{ color: teamColour(overview, x.team_id) }}>
              {x.most_touches!.map((y, i) => <span key={y.player_id}>{i ? <br /> : null}{playerName(overview, y.player_id)} · {y.value.toFixed(0)}</span>)}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

/** Tackles, ball recoveries and PPDA (METRICS.md §13, 21): PPDA blurred in Fan. */
function DefenceTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { session } = useMatch();
  const { h, a } = useTeams();
  const row = useRow();
  const ppda = s.ppda;
  return (
    <>
      {row(t("count.tackles"), of(s.counts, h)?.tackles, of(s.counts, a)?.tackles)}
      {row(t("count.ball_recoveries"), of(s.counts, h)?.ball_recoveries, of(s.counts, a)?.ball_recoveries)}
      {s.mode === "analyst" ? (
        ppda && of(ppda, h) && of(ppda, a) ? (
          <>
            {row(t("stats.ppda"), of(ppda, h)!.value, of(ppda, a)!.value, (v) => v.toFixed(1))}
            <p className="small muted center">{t("stats.ppda_hint")}</p>
          </>
        ) : <Pending s={s} metrics={["ppda", `ppda.${h}`, `ppda.${a}`]} />
      ) : (
        <p className="locked-row small">
          <span className="muted">{t("stats.ppda")}</span>
          <span className="locked-note"><Icon name="lock" size={16} />
            {canSwitchMode(session) ? t("stats.switch_analyst") : t("stats.unlock_gold")}</span>
        </p>
      )}
    </>
  );
}

/** The Analyst table of all players (METRICS.md §8, 11, 19, 21, 22, 23). */
function PlayersTable({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, overview } = useTeams();
  const rows = playersInOrder(s, h);
  if (rows.length === 0) return <Pending s={s} metrics={["players"]} />;
  const n = (v: number | null | undefined, digits = 0) => (v == null ? "—" : v.toFixed(digits));
  return (
    <div className="table-scroll">
      <table className="stats-table small">
        <thead>
          <tr>{PLAYER_COLS.map((c) => <th key={c} scope="col">{t(`col.${c}`)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.player_id}>
              <th scope="row" style={{ color: teamColour(overview, p.team_id) }}>{playerName(overview, p.player_id)}</th>
              <td>{p.minutes_played}</td>
              <td>{p.distance ? <>{p.distance.approx ? "≈" : ""}{p.distance.km.toFixed(2)} <Rel r={p.distance.reliability} /></> : "—"}</td>
              <td>{n(p.top_speed_kmh, 1)}</td>
              <td>{n(p.sprints)}</td>
              <td>{n(p.peak_m_per_min, 1)}</td>
              <td>{n(p.passes)}</td>
              <td>{n(p.pass_accuracy_pct)}</td>
              <td>{n(p.long_balls)}</td>
              <td>{n(p.touches)}</td>
              <td>{p.shots ?? 0}</td>
              <td>{p.goals ?? 0}</td>
              <td>{n(p.xg, 2)}</td>
              <td>{n(p.tackles)}</td>
              <td>{n(p.ball_recoveries)}</td>
              <td>{p.fouls_committed ?? 0}</td>
              <td>{p.fouls_won ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PLAYER_COLS = ["player", "min", "km", "top", "sprints", "peak", "passes", "acc", "long", "touches", "shots",
  "goals", "xg", "tackles", "recoveries", "fouls", "fouls_won"] as const;

/**
 * A chart or a table of the Analyst version, blurred as a preview in Fan: gold tickets can switch, silver ones are
 * invited to upgrade.
 */
function Locked({ id, title }: { id: string; title: string }) {
  const { t } = useI18n();
  const { session } = useMatch();
  return (
    <Card title={title} id={`chart-${id}`}>
      <div className="locked-preview" aria-hidden>
        <div className="locked-art">{[38, 62, 45, 80, 55, 70, 30, 66].map((v, i) => <i key={i} style={{ height: `${v}%` }} />)}</div>
      </div>
      <p className="locked-note">
        <Icon name="lock" size={18} />
        {canSwitchMode(session) ? t("stats.switch_analyst") : t("stats.unlock_gold")}
      </p>
    </Card>
  );
}

/** A chart: "Available from 5'" while it is coming, hidden when the metric is not available for this source. */
function Chart({ c, s }: { c: ChartRef; s: Stats }) {
  const { t, tOr } = useI18n();
  const title = tOr(`chart.${c.chart_id}`, c.title);
  const body = renderChart(c.chart_id, s);
  if (body === undefined) return null;
  if (body === null) {
    const a = availability(s.unavailable, c.chart_id, CHART_METRIC[c.chart_id] ?? c.chart_id);
    if (!a || a === "hidden") return null;
    return <Card title={title} id={`chart-${c.chart_id}`}><p className="muted small">{t("stats.available_from", { m: a.from })}</p></Card>;
  }
  return <Card title={title} id={`chart-${c.chart_id}`}>{body}</Card>;
}

/** undefined = no renderer for this chart; null = the metric is not available in this payload. */
function renderChart(id: string, s: Stats): ReactNode | null | undefined {
  switch (id) {
    case "momentum": return s.momentum?.series?.length ? <MomentumChart s={s} /> : null;
    case "shot_map": return s.shot_map ? <ShotsChart s={s} /> : null;
    case "xg_cumulative": return s.xg ? <XgChart s={s} /> : null;
    case "peak_intensity": return s.peak_intensity?.series?.length ? <PeakChart s={s} /> : null;
    case "ball_circulation": return s.ball_circulation?.series?.length ? <CirculationChart s={s} /> : null;
    case "zone_map": return s.zone_map ? <ZoneChart s={s} /> : null;
    case "speed_zones": return s.speed_zones ? <SpeedZonesChart s={s} /> : null;
    case "defensive_shape": return s.defensive_shape ? <ShapeChart s={s} /> : null;
    default: return undefined;
  }
}

/** The mirror chart of two team series, with the goals of `goal_marks` as vertical lines. */
function Mirror({ s, series, label, step, area, windows, zeroBased = true }: {
  s: Stats; series: { team_id: TeamId; points: TimePoint[] }[] | null | undefined; label: string; step?: boolean;
  area?: boolean; windows?: Parameters<typeof MirrorChart>[0]["windows"]; zeroBased?: boolean;
}) {
  const { t } = useI18n();
  const { h, a, ch, ca, overview } = useTeams();
  return (
    <MirrorChart label={label} home={of(series, h)?.points ?? []} away={of(series, a)?.points ?? []} homeColour={ch}
      awayColour={ca} step={step} area={area} windows={windows} to={s.up_to_minute} zeroBased={zeroBased}
      floorLabel={(v) => t("stats.axis_from", { v: String(v) })}
      goals={(s.goal_marks ?? []).map((g) => ({ minute: g.minute, colour: teamColour(overview, g.team_id) }))} />
  );
}

function TeamKey() {
  const { h, a, ch, ca, overview } = useTeams();
  return (
    <div className="row small">
      <span style={{ color: ch }}>▲ {teamShort(overview, h)}</span>
      <span style={{ color: ca }}>{teamShort(overview, a)} ▼</span>
    </div>
  );
}

/** Momentum: one signed value, an area above the axis when the home team pushes and below when the away team does. */
function MomentumChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, a } = useTeams();
  const { home, away } = splitSigned(s.momentum!.series ?? []);
  return (
    <>
      <TeamKey />
      <Mirror s={s} label={t("live.momentum")} series={[{ team_id: h, points: home }, { team_id: a, points: away }]} />
    </>
  );
}

function ShotsChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { mode, overview, session } = useMatch();
  return (
    <>
      <ShotMapChart shots={s.shot_map.shots} homeId={session.match.home_team_id}
        colourOf={(id) => teamColour(overview, id as TeamId)} sized={mode === "analyst"} />
      <p className="small muted center">{t(mode === "analyst" ? "stats.shot_key_xg" : "stats.shot_key")}</p>
    </>
  );
}

function XgChart({ s }: { s: Stats }) {
  const { h, a, ch, ca } = useTeams();
  const x = s.xg!;
  return (
    <>
      <PairRow label="xG" home={of(x.teams, h)?.value ?? 0} away={of(x.teams, a)?.value ?? 0} homeColour={ch} awayColour={ca}
        format={(v) => v.toFixed(2)} />
      <TeamKey />
      <Mirror s={s} label="xG" series={x.cumulative} step area={false} />
    </>
  );
}

/** Running intensity: metres per minute of each team, with its most intense 5 minutes shaded. */
function PeakChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, overview } = useTeams();
  const p = s.peak_intensity!;
  return (
    <>
      <TeamKey />
      <Mirror s={s} label={t("stats.peak")} series={p.series} zeroBased={false} windows={p.teams.map((x) => ({
        from: x.start_minute, to: x.end_minute, above: x.team_id === h, colour: teamColour(overview, x.team_id),
      }))} />
      <Leaders label={t("stats.peak")} items={p.teams.map((x) => ({
        team_id: x.team_id, text: `${x.start_minute}'–${x.end_minute}' · ${x.metres_per_minute.toFixed(1)} m/min`,
      }))} />
    </>
  );
}

function CirculationChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  return (
    <>
      <TeamKey />
      <Mirror s={s} label={t("live.ball_circulation")} series={s.ball_circulation!.series} zeroBased={false} />
      <p className="small muted center">{t("live.ball_circulation")} (km/h)</p>
    </>
  );
}

function ZoneChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { overview } = useTeams();
  return (
    <>
      {s.zone_map!.filter((z) => !z.player_id).map((z) => (
        <div key={z.team_id}>
          <p className="small row"><span style={{ color: teamColour(overview, z.team_id) }}>{teamShort(overview, z.team_id)}</span>
            <span className="muted">{t("stats.attacks_right")}</span></p>
          <ZoneGrid zone={z} colour={teamColour(overview, z.team_id)} />
        </div>
      ))}
    </>
  );
}

function SpeedZonesChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const { h, a, ch, ca } = useTeams();
  const z = (id: TeamId) => {
    const r = s.speed_zones!.find((x) => x.team_id === id && !x.player_id);
    return r ? zoneValues(r) : [0, 0, 0, 0, 0];
  };
  return (
    <>
      <GroupedBars bins={ZONE_BINS} series={[{ values: z(h), colour: ch }, { values: z(a), colour: ca }]} />
      <p className="small muted center">{t("stats.speed_zones")}</p>
    </>
  );
}

function ShapeChart({ s }: { s: Stats }) {
  const { t } = useI18n();
  const shapes = s.defensive_shape!;
  return (
    <>
      <TeamKey />
      <p className="small muted">{t("stats.defensive_line")} (m)</p>
      <Mirror s={s} label={t("stats.defensive_line")} series={shapes.map((d) => ({ team_id: d.team_id, points: d.defensive_line_m }))} zeroBased={false} />
      <p className="small muted">{t("stats.team_length")} (m)</p>
      <Mirror s={s} label={t("stats.team_length")} series={shapes.map((d) => ({ team_id: d.team_id, points: d.team_length_m }))} zeroBased={false} />
    </>
  );
}
