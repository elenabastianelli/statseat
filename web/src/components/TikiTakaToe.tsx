import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Criterion, PickerPlayer, TttGame, TttMoveResult } from "../api/types";
import { useI18n } from "../i18n";
import { useCountdown } from "../lib/useCountdown";
import { Flag } from "./Flag";
import { Card, ErrorBox, Loading } from "./Status";

/** Tic Tac Goal against the fan-bot (M-3): pick a free cell, then a player who meets its row and column criteria. */
export function TikiTakaToe({ onExit }: { onExit: () => void }) {
  const { t } = useI18n();
  const [game, setGame] = useState<TttGame | null>(null);
  const [players, setPlayers] = useState<PickerPlayer[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [cell, setCell] = useState<{ row: number; col: number } | null>(null);
  const [query, setQuery] = useState("");
  const [last, setLast] = useState<TttMoveResult | null>(null);
  const [earned, setEarned] = useState(0);
  const [busy, setBusy] = useState(false);
  const seconds = useCountdown(game?.turn_seconds, game?.status === "playing" ? game.turn_deadline : null);

  const fail = (e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, {}));

  const started = useRef(false); // React's StrictMode runs effects twice in development: one game only
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    Promise.all([api.tttStart(), api.gamePlayers()]).then(([g, p]) => {
      setGame(g);
      setPlayers(p.players);
    }, fail);
  }, []);

  // Time over for this turn: the server gives the turn to the bot (asked once per deadline).
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (!game || game.status !== "playing" || seconds > 0 || asked.current === game.turn_deadline) return;
    asked.current = game.turn_deadline;
    setCell(null);
    api.tttGet(game.game_session_id).then((g) => {
      setLast({ correct: false, reason: "too_late", elapsed_ms: 0, game: g, points_awarded: 0 });
      setGame(g);
    }, fail);
  }, [seconds, game]);

  const names = useMemo(() => new Map(players.map((p) => [p.player_id, p])), [players]);
  const found = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? players.filter((p) => p.name.toLowerCase().includes(q) || String(p.shirt_number) === q) : players;
  }, [players, query]);

  if (error) return <ErrorBox error={error} />;
  if (!game) return <Loading />;

  const play = (playerId: string) => {
    if (!cell || busy) return;
    setBusy(true);
    api.tttMove(game.game_session_id, cell.row, cell.col, playerId).then((r) => {
      setLast(r);
      setGame(r.game);
      setEarned((n) => n + r.points_awarded);
      setCell(null);
      setQuery("");
    }, fail).finally(() => setBusy(false));
  };

  const header = (c: Criterion) => (
    <div className="ttt-crit">
      {c.kind === "club" ? <img src={api.crestUrl(c.id)} alt="" width={28} height={28} /> : <Flag country={c.id} />}
      <span>{c.label}</span>
    </div>
  );
  const over = game.status !== "playing";

  return (
    <Card title={t("games.title.tiki_taka_toe")} aside={<button className="btn ghost small" onClick={onExit}>{t("games.back")}</button>}>
      <p className="small muted">{t("games.vs", { name: game.opponent.display_name })}</p>
      {!over && (
        <p className="ttt-turn">
          <strong>{t("games.turn_me")}</strong> <span className={seconds <= 5 ? "warn" : ""}>{t("games.seconds", { n: seconds })}</span>
        </p>
      )}
      <div className="ttt-grid" role="grid">
        <div />
        {game.cols.map((c) => <div key={c.id}>{header(c)}</div>)}
        {game.rows.map((r, ri) => (
          <div className="ttt-row" role="row" key={r.id}>
            {header(r)}
            {game.cols.map((_, ci) => {
              const b = game.board[ri * 3 + ci];
              const picked = cell?.row === ri && cell?.col === ci;
              return (
                <button
                  key={ci}
                  role="gridcell"
                  className={`ttt-cell ${b.owner ?? "free"} ${picked ? "picked" : ""}`}
                  disabled={over || b.owner !== null}
                  onClick={() => setCell({ row: ri, col: ci })}
                >
                  {b.owner ? (
                    <>
                      <span className="ttt-mark">{b.owner === "me" ? game.my_symbol : game.my_symbol === "X" ? "O" : "X"}</span>
                      <span className="ttt-name">{names.get(b.player_id ?? "")?.name ?? ""}</span>
                    </>
                  ) : "+"}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {last && !over && (
        <p className="small" role="status">
          {last.correct ? t("games.right", { n: last.points_awarded }) : t(`games.${last.reason ?? "wrong_answer"}`)}
        </p>
      )}
      {!over && !cell && <p className="small muted">{t("games.pick_cell")}</p>}
      {!over && cell && (
        <div className="picker">
          <p className="small">
            <strong>{game.rows[cell.row].label}</strong> + <strong>{game.cols[cell.col].label}</strong>
          </p>
          <input
            className="picker-search"
            placeholder={t("games.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <ul className="picker-list">
            {found.map((p) => (
              <li key={p.player_id}>
                <button className="picker-item" disabled={busy} onClick={() => play(p.player_id)}>
                  <span className="grow">{p.name}</span>
                  <span className="muted small">{p.team_id} #{p.shirt_number}</span>
                </button>
              </li>
            ))}
          </ul>
          <button className="btn ghost small" onClick={() => setCell(null)}>{t("games.cancel")}</button>
        </div>
      )}
      {over && (
        <div className="game-end" role="status">
          <p className="big-result">{t(`games.${game.status}`)}</p>
          <p>{t("games.earned", { n: earned })}</p>
          <button className="btn" onClick={onExit}>{t("games.back")}</button>
        </div>
      )}
    </Card>
  );
}
