import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { AnswerResult, QuizRound } from "../api/types";
import { useI18n } from "../i18n";
import { useCountdown } from "../lib/useCountdown";
import { Card, ErrorBox, Loading } from "./Status";

/** Match quiz (M-3 part 2): the session's questions on the first half, chosen by the server from the engine's
 * statistics; after each answer the true value, the one the fan can check in Data & Statistics. */
export function MatchQuiz({ onExit }: { onExit: () => void }) {
  const { t } = useI18n();
  const [round, setRound] = useState<QuizRound | null>(null);
  const [number, setNumber] = useState(0);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [earned, setEarned] = useState(0);
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const seconds = useCountdown(round?.answer_seconds, round && !result ? round.round_id : null);

  const next = useCallback(() => {
    setResult(null);
    setChosen(null);
    api.quizStart().then((r) => {
      setRound(r);
      setNumber((n) => n + 1);
    }, (e: unknown) => {
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

  const answer = useCallback((optionId: string) => {
    if (!round) return;
    setChosen(optionId);
    api.quizAnswer(round.round_id, optionId).then((r) => {
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

  const exit = <button className="btn ghost small" onClick={onExit}>{t("games.back")}</button>;
  if (error) return <ErrorBox error={error} />;
  if (finished) {
    return (
      <Card title={t("games.title.quiz")} aside={exit}>
        <div className="game-end" role="status">
          <p>{t("games.done", { total: number })}</p>
          {earned > 0 && <p>{t("games.earned", { n: earned })}</p>}
          <button className="btn" onClick={onExit}>{t("games.back")}</button>
        </div>
      </Card>
    );
  }
  if (!round) return <Loading />;

  return (
    <Card title={t("games.title.quiz")} aside={exit}>
      <p className="ttt-turn">
        <strong>{t("games.question_n", { n: number })}</strong>
        {!result && <span className={seconds <= 5 ? "warn" : ""}> {t("games.seconds", { n: seconds })}</span>}
      </p>
      <p className="quiz-question">{round.question}</p>
      <div className="options">
        {round.options.map((o) => {
          const state = !result ? "" : o.option_id === result.correct_option_id ? "right" : o.option_id === chosen ? "wrong" : "";
          return (
            <button key={o.option_id} className={`option ${state}`} disabled={chosen !== null} onClick={() => answer(o.option_id)}>
              <span className="grow">{o.label}</span>
            </button>
          );
        })}
      </div>
      {result && (
        <div role="status">
          <p>{result.correct ? t("games.right", { n: result.points_awarded }) : t("games.answer", { fact: result.fact ?? "" })}</p>
          {result.correct && result.fact && <p className="small muted">{result.fact}</p>}
          <button className="btn" onClick={next}>{t("games.next")}</button>
        </div>
      )}
    </Card>
  );
}
