import { useState } from "react";
import { api } from "../api/client";
import { useMatch } from "../app/MatchContext";
import { GuessThePlayer } from "../components/GuessThePlayer";
import { MatchQuiz } from "../components/MatchQuiz";
import { Card, Load } from "../components/Status";
import { TikiTakaToe } from "../components/TikiTakaToe";
import { useI18n } from "../i18n";
import { useLoad } from "../lib/useLoad";

type Playing = "tiki_taka_toe" | "guess_the_player" | "quiz" | null;
const PLAYABLE = new Set(["tiki_taka_toe", "guess_the_player", "quiz"]);

/** Games (half-time only): Tic Tac Goal, Guess the player and the match quiz (M-3), the sponsor's prize and the stadium leaderboard. */
export function Games() {
  const { t, tOr } = useI18n();
  const { state } = useMatch();
  const [playing, setPlaying] = useState<Playing>(null);
  const games = useLoad(api.games, [state.phase]);
  const board = useLoad(api.leaderboard, [state.phase, playing]);

  if (playing === "tiki_taka_toe") return <TikiTakaToe onExit={() => setPlaying(null)} />;
  if (playing === "guess_the_player") return <GuessThePlayer onExit={() => setPlaying(null)} />;
  if (playing === "quiz") return <MatchQuiz onExit={() => setPlaying(null)} />;

  return (
    <>
      <Load {...games}>
        {(g) => (
          <>
            {g.games.map((game) => {
              const playable = PLAYABLE.has(game.game_id);
              return (
                <Card key={game.game_id} title={tOr(`games.title.${game.game_id}`, game.title)}>
                  <p className="small">{tOr(`games.rules.${game.game_id}`, game.rules)}</p>
                  <button
                    className="btn"
                    disabled={!playable || !game.available}
                    title={playable ? undefined : t("games.opening")}
                    onClick={() => setPlaying(game.game_id as Playing)}
                  >
                    {t("games.play")}
                  </button>
                  {!playable && <p className="small muted">{t("games.opening")}</p>}
                </Card>
              );
            })}
            {g.prize && (
              <Card title={t("games.prize")}>
                <p><strong>{g.prize.name}</strong></p>
                <p className="small">{g.prize.description}</p>
                <p className="small muted">{t("games.prize_by", { sponsor: g.prize.sponsor_name })} · {g.prize.how_to_claim}</p>
              </Card>
            )}
          </>
        )}
      </Load>
      <Load {...board}>
        {(b) => (
          <Card title={t("games.leaderboard")}>
            <ol className="board">
              {b.entries.map((e) => (
                <li key={`${e.rank}-${e.display_name}`} className={e.is_me ? "me" : ""}>
                  <span className="rank">{e.rank}</span>
                  <span className="grow">
                    {e.display_name}
                    {e.is_me && <span className="badge">{t("games.you")}</span>}
                    {e.is_bot && <span className="badge muted">{t("games.bot")}</span>}
                  </span>
                  <span>{t("games.points", { n: e.points })}</span>
                </li>
              ))}
            </ol>
          </Card>
        )}
      </Load>
    </>
  );
}
