"""The quiz's two routes on the real backend (M-3 part 2): `POST /api/games/quiz` and `POST /api/games/quiz/{round_id}/answer`.

Registered by `app/engine/server.py` in place of the mock's fake round, because the questions need the engine's
statistics. Same contract models (QuizRound, AnswerResult) and the same scoring as Guess the player: 100 points for a
right answer within 20 s plus up to 50 for speed, nothing taken away for a wrong one; the points go to the session
(leaderboard). Each session plays the match's 5 questions once; a 6th answers 409 `no_more_rounds`.

The text of the questions and options is The Ref's in the fan's language (AG-4, `app/agents/the_ref.py`): one call
per match, phase and language, checked, with the code's English when The Ref is not there or a check fails.
"""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import Depends, FastAPI

from app.contract import mock
from app.contract.models import AnswerRequest, AnswerResult, ApiError, Mode, QuizRound, Section

from . import quiz
from .universe import club_names

ERR = {c: {"model": ApiError} for c in (401, 404, 409, 423)}


def register(app: FastAPI, engine, session, require, the_ref=None) -> None:
    """`session` and `require` are the engine server's dependency and phase check (the token's own clock);
    `the_ref`: an `app.agents.the_ref.TheRef`, or None for the code's English."""
    from app.engine import stats as stats_module

    snapshots: dict[tuple[str, str], list[dict]] = {}  # (match, phase) -> the session's questions: frozen at the whistle
    played: dict[str, int] = {}  # token -> questions started

    def questions(snap) -> list[dict]:
        key = (snap.match_id, snap.phase.value)
        if key not in snapshots:
            stats = stats_module.match_stats(engine.core(snap), Mode.analyst, False, mock.now())
            snapshots[key] = quiz.session(stats, snap.match_id, snap.phase.value)
        return snapshots[key]

    @app.post("/api/games/quiz", response_model=QuizRound, responses=ERR, tags=["games"])
    def quiz_start(auth=Depends(session)) -> dict:
        """The next of the session's 5 questions on the first half (GIOCHI.md section 3)."""
        token, s = auth
        snap = engine.snapshot(token)
        require(snap, Section.games)
        qs = questions(snap)
        if the_ref is not None:
            qs = the_ref.localize((snap.match_id, snap.phase.value), s.preferences.language, qs,
                                  list(club_names().values()))
        n = played.get(token, 0)
        if n >= len(qs):
            raise mock.ApiException(409, "no_more_rounds", f"You have played all {len(qs)} questions.")
        played[token] = n + 1
        q = qs[n]
        s.rounds[q["round_id"]] = time.monotonic()
        return {"round_id": q["round_id"], "question": q["question"], "metric": q["metric"], "scope": "first_half",
                "options": q["options"], "answer_seconds": quiz.ANSWER_SECONDS,
                "deadline": (mock.now() + timedelta(seconds=quiz.ANSWER_SECONDS)).isoformat()}

    @app.post("/api/games/quiz/{round_id}/answer", response_model=AnswerResult, responses=ERR, tags=["games"])
    def quiz_answer(round_id: str, body: AnswerRequest, auth=Depends(session)) -> AnswerResult:
        token, s = auth
        snap = engine.snapshot(token)
        require(snap, Section.games)
        q = next((q for q in questions(snap) if q["round_id"] == round_id), None)
        if q is None or round_id not in s.rounds:
            raise mock.ApiException(404, "round_not_found", "Start the round first.")
        elapsed_ms = int((time.monotonic() - s.rounds.pop(round_id)) * 1000)
        correct = body.option_id in q["right"] and elapsed_ms <= quiz.ANSWER_SECONDS * 1000
        bonus = round(mock.MAX_SPEED_BONUS * (1 - elapsed_ms / (quiz.ANSWER_SECONDS * 1000))) if correct else 0
        points = mock.POINTS_CORRECT + bonus if correct else 0
        s.points += points
        return AnswerResult(correct=correct, correct_option_id=body.option_id if correct else q["right"][0],
                            elapsed_ms=elapsed_ms, points_awarded=points, speed_bonus=bonus, fact=q["fact"])
