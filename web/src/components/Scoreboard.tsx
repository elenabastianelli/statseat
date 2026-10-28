import { team, teamColour, useMatch } from "../app/MatchContext";
import { useI18n } from "../i18n";
import { Crest } from "./Crest";
import { minuteLabel } from "../lib/format";

export function Scoreboard() {
  const { t } = useI18n();
  const { session, state, overview } = useMatch();
  const m = session.match;
  const home = team(overview, m.home_team_id);
  const away = team(overview, m.away_team_id);
  const live = state.phase === "first_half" || state.phase === "second_half";
  return (
    <div className="scoreboard">
      <div className="sb-team">
        <Crest teamId={m.home_team_id} name={home?.name ?? m.home_name} team={home} />
        <span className="sb-name">{home?.short_name ?? m.home_name}</span>
      </div>
      <div className="sb-mid">
        <div className="sb-score">
          <span style={{ color: teamColour(overview, m.home_team_id) }}>{state.score.home}</span>
          <span className="muted">–</span>
          <span style={{ color: teamColour(overview, m.away_team_id) }}>{state.score.away}</span>
        </div>
        <div className="sb-clock">
          {live && <span className="live-dot" aria-hidden />}
          {t(`phase.${state.phase}`)}
          {live && ` · ${minuteLabel(state.minute, state.added_time ?? 0)}`}
        </div>
      </div>
      <div className="sb-team">
        <Crest teamId={m.away_team_id} name={away?.name ?? m.away_name} team={away} />
        <span className="sb-name">{away?.short_name ?? m.away_name}</span>
      </div>
    </div>
  );
}
