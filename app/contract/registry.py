"""Which contract model every fake file follows (used by the tests, handy for the real backend)."""

from __future__ import annotations

from pydantic import BaseModel

from . import models as m

FAKE_MODELS: dict[str, type[BaseModel]] = {
    "login.json": m.LoginResponse,
    "session*.json": m.Session,
    "ticket_*.json": m.TicketInfo,
    "preferences.json": m.Preferences,
    "match.json": m.MatchOverview,
    "match_state_*.json": m.MatchState,
    "events.json": m.EventFeed,
    "stats_live_*.json": m.LiveStats,
    "stats_casual_*.json": m.MatchStats,
    "stats_analyst_*.json": m.MatchStats,
    "recap_*.json": m.Recap,
    "highlights.json": m.HighlightList,
    "games.json": m.GameList,
    "players.json": m.PlayerPicker,
    "ttt_game.json": m.TttGame,
    "ttt_move.json": m.TttMoveResult,
    "guess_career.json": m.GuessRound,
    "guess_transfers.json": m.GuessRound,
    "guess_answer.json": m.AnswerResult,
    "quiz_round.json": m.QuizRound,
    "quiz_answer.json": m.AnswerResult,
    "leaderboard.json": m.Leaderboard,
    "archie_answer.json": m.ArchieAnswer,
}
