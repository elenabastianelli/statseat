import type { LineupPlayer, TeamSheet } from "../api/types";
import { teamColour, useMatch } from "../app/MatchContext";
import { Card, Loading } from "../components/Status";
import { Crest } from "../components/Crest";
import { useI18n } from "../i18n";
import { date, time } from "../lib/format";
import { Icon } from "../components/Icon";
import { LineupPitch } from "../components/LineupPitch";

export function PreMatch() {
  const { t, lang } = useI18n();
  const { overview } = useMatch();
  if (!overview) return <Loading />;
  return (
    <>
      <Card>
        <p className="eyebrow">{overview.competition} · {t("pre.matchday", { n: overview.matchday })} · {overview.season}</p>
        <h2>{overview.title}</h2>
        <p className="muted">
          {t("pre.kickoff")} {time(overview.kickoff, lang)} · {date(overview.kickoff, lang)}
          <br />
          {overview.stadium_name} · {t("pre.capacity", { n: overview.stadium_capacity.toLocaleString(lang) })}
        </p>
      </Card>
      <Card title={t("pre.lineups")}>
        <LineupPitch teams={overview.teams} />
      </Card>
      <p className="muted small pad-x">{t("pre.favourite_hint")}</p>
      <div className="two-col">
        {overview.teams.map((tm) => <TeamCard key={tm.team_id} team={tm} />)}
      </div>
      <Card title={t("pre.officials")}>
        <ul className="plain">
          {overview.officials.map((o) => (
            <li key={o.role} className="row">
              <span className="muted">{t(`official.${o.role}`)}</span>
              <span>{o.name}</span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}

function TeamCard({ team }: { team: TeamSheet }) {
  const { t } = useI18n();
  const { overview } = useMatch();
  const starters = team.lineup.filter((p) => p.starting);
  const bench = team.lineup.filter((p) => !p.starting);
  return (
    <Card>
      <header className="team-head" style={{ borderColor: teamColour(overview, team.team_id) }}>
        <Crest teamId={team.team_id} name={team.name} team={team} size="lg" />
        <div>
          <h3>{team.name}</h3>
          <p className="small muted">“{team.nickname}” · {team.formation} · {t("pre.coach")}: {team.head_coach}</p>
        </div>
      </header>
      <ul className="lineup">{starters.map((p) => <PlayerRow key={p.player_id} p={p} colour={team.colours} />)}</ul>
      <h4 className="muted small">{t("pre.bench")}</h4>
      <ul className="lineup">{bench.map((p) => <PlayerRow key={p.player_id} p={p} colour={team.colours} />)}</ul>
    </Card>
  );
}

function PlayerRow({ p, colour }: { p: LineupPlayer; colour: TeamSheet["colours"] }) {
  const { t } = useI18n();
  const { session, toggleFavourite } = useMatch();
  const fav = (session.preferences.favourite_player_ids ?? []).includes(p.player_id);
  return (
    <li>
      <span className="shirt" style={{ background: colour.primary, color: colour.number }}>{p.shirt_number}</span>
      <span className="grow">
        {p.name}
        {p.captain && <span className="badge">C</span>}
      </span>
      <span className="muted small">{p.position}</span>
      <button
        className={`star ${fav ? "on" : ""}`}
        aria-pressed={fav}
        aria-label={t(fav ? "pre.unfollow" : "pre.follow", { name: p.name })}
        onClick={() => toggleFavourite(p.player_id)}
      >
        <Icon name={fav ? "starFilled" : "star"} />
      </button>
    </li>
  );
}
