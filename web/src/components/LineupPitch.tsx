import type { TeamSheet } from "../api/types";
import { teamColour, useMatch } from "../app/MatchContext";
import { useI18n } from "../i18n";
import { PITCH_L, PITCH_W, spotOnPitch } from "../lib/formation";

/** Both starting line-ups on one vertical pitch: home at the bottom attacking up, away at the top (L-2e). */
export function LineupPitch({ teams }: { teams: TeamSheet[] }) {
  const { t } = useI18n();
  const { overview, session } = useMatch();
  const favs = new Set(session.preferences.favourite_player_ids ?? []);
  const label = teams.map((tm) => `${tm.name} ${tm.formation}`).join(" · ");
  return (
    <figure className="lineup-pitch">
      <figcaption className="row small">
        {[...teams.filter((x) => x.role === "away"), ...teams.filter((x) => x.role === "home")].map((tm) => (
          <span key={tm.team_id} className="lp-team">
            <i style={{ background: teamColour(overview, tm.team_id) }} />
            {tm.short_name} · {tm.formation}
          </span>
        ))}
      </figcaption>
      <svg viewBox={`-2 -2 ${PITCH_W + 4} ${PITCH_L + 4}`} role="img" aria-label={`${t("pre.lineups")}: ${label}`}>
        <rect x={0} y={0} width={PITCH_W} height={PITCH_L} className="pitch-grass" rx={1} />
        <g className="pitch-lines">
          <rect x={0} y={0} width={PITCH_W} height={PITCH_L} />
          <line x1={0} y1={PITCH_L / 2} x2={PITCH_W} y2={PITCH_L / 2} />
          <circle cx={PITCH_W / 2} cy={PITCH_L / 2} r={9.15} />
          <rect x={13.84} y={0} width={40.32} height={16.5} />
          <rect x={24.84} y={0} width={18.32} height={5.5} />
          <rect x={13.84} y={PITCH_L - 16.5} width={40.32} height={16.5} />
          <rect x={24.84} y={PITCH_L - 5.5} width={18.32} height={5.5} />
        </g>
        {teams.map((tm) =>
          tm.lineup.filter((p) => p.starting).map((p) => {
            const { x, y } = spotOnPitch(p.position, tm.role);
            const fav = favs.has(p.player_id);
            return (
              <g key={p.player_id} transform={`translate(${x} ${y})`}>
                <title>{`${p.shirt_number} ${p.name} (${p.position})`}</title>
                {fav && <circle r={3.9} className="lp-fav" />}
                <circle r={3} fill={tm.colours.primary} stroke={teamColour(overview, tm.team_id)} strokeWidth={0.6} />
                <text className="lp-num" fill={tm.colours.number} y={1.05}>{p.shirt_number}</text>
                {p.captain && (
                  <g transform="translate(2.6 -2.6)">
                    <circle r={1.3} className="lp-capt" />
                    <text className="lp-capt-c" y={0.55}>C</text>
                  </g>
                )}
                <text className="lp-name" y={5.6}>{p.short_name}</text>
              </g>
            );
          }),
        )}
      </svg>
    </figure>
  );
}
