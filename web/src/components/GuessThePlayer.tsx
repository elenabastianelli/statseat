import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { AnswerResult, CareerClue, GuessRound } from "../api/types";
import { useI18n } from "../i18n";
import { useCountdown } from "../lib/useCountdown";
import { Card, ErrorBox, Loading } from "./Status";

/** Guess the player (M-3): 5 careers with the transfer fees, the current club hidden, 4 names to choose from. */
export function GuessThePlayer({ onExit }: { onExit: () => void }) {
  const { t, lang } = useI18n();
  const [round, setRound] = useState<GuessRound | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [earned, setEarned] = useState(0);
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const seconds = useCountdown(round?.answer_seconds, round && !result ? round.round_id : null);

  const next = useCallback(() => {
    setResult(null);
    setChosen(null);
    api.guessStart().then(setRound, (e: unknown) => {
      if (e instanceof ApiError && e.code === "no_more_rounds") setFinished(true);
      else setError(e instanceof ApiError ? e : new ApiError(0, {}));
    });
  }, []);
  const started = useRef(false); // React's StrictMode runs effects twice in development: start the first round once
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    next();
  }, [next]);

  const answer = useCallback((playerId: string) => {
    if (!round) return;
    setChosen(playerId);
    api.guessAnswer(round.round_id, playerId).then((r) => {
      setResult(r);
      setEarned((n) => n + r.points_awarded);
    }, (e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, {})));
  }, [round]);

  // Time over: an empty answer, so the server reveals the right one (no points).
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (!round || result || chosen || seconds > 0 || asked.current === round.round_id) return;
    asked.current = round.round_id;
    answer("");
  }, [round, result, chosen, seconds, answer]);

  const money = (eur: number) =>
    new Intl.NumberFormat(lang, { style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1 }).format(eur);
  const clue = (c: CareerClue) => (
    <li key={`${c.from_season}-${c.club_name}`} className={c.hidden ? "career-row hidden" : "career-row"}>
      <span className="career-years">{c.from_season}{c.to_season !== c.from_season ? `–${c.to_season ?? t("games.now")}` : ""}</span>
      <span className="grow"><strong>{c.club_name}</strong></span>
      <span className="muted small">{c.move ? t(`games.move.${c.move}`) : ""}{c.fee_eur ? ` · ${money(c.fee_eur)}` : ""}</span>
    </li>
  );

  const exit = <button className="btn ghost small" onClick={onExit}>{t("games.back")}</button>;
  if (error) return <ErrorBox error={error} />;
  if (finished) {
    return (
      <Card title={t("games.title.guess_the_player")} aside={exit}>
        <div className="game-end" role="status">
          <p>{t("games.done", { total: 5 })}</p>
          {earned > 0 && <p>{t("games.earned", { n: earned })}</p>}
          <button className="btn" onClick={onExit}>{t("games.back")}</button>
        </div>
      </Card>
    );
  }
  if (!round) return <Loading />;
  const last = round.round_number === round.rounds_total;

  return (
    <Card title={t("games.title.guess_the_player")} aside={exit}>
      <p className="ttt-turn">
        <strong>{t("games.question", { n: round.round_number ?? 1, total: round.rounds_total ?? 5 })}</strong>
        {!result && <span className={seconds <= 5 ? "warn" : ""}> {t("games.seconds", { n: seconds })}</span>}
      </p>
      <p className="small muted">{t("games.who")}</p>
      <ol className="career">{(round.career ?? []).map(clue)}</ol>
      <div className="options">
        {round.options.map((o) => {
          const state = !result ? "" : o.player_id === result.correct_option_id ? "right" : o.player_id === chosen ? "wrong" : "";
          return (
            <button key={o.player_id} className={`option ${state}`} disabled={chosen !== null} onClick={() => answer(o.player_id)}>
              <span className="grow">{o.name}</span>
              <span className="muted small">{o.team_id} #{o.shirt_number}</span>
            </button>
          );
        })}
      </div>
      {result && (
        <div role="status">
          <p>{result.correct ? t("games.right", { n: result.points_awarded }) : t("games.it_was", { fact: result.fact ?? "" })}</p>
          {last ? (
            <button className="btn" onClick={() => setFinished(true)}>{t("games.see_score")}</button>
          ) : (
            <button className="btn" onClick={next}>{t("games.next")}</button>
          )}
        </div>
      )}
    </Card>
  );
}
