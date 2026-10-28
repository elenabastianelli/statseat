"""Mock server of the statseat API: serves the fake data of app/contract/fake/ with the contract models.

Run from the repo root:  python -m app.contract.mock   (then open http://127.0.0.1:8000/docs)
Environment: PORT (default 8000), MOCK_PHASE (default half_time) = the phase of the stadium clock,
STATSEAT_TICKETS_FILE (default fake/server/tickets.json), STATSEAT_TICKET_SALT (default: the public dev salt).

In-memory and single-process. The token is opaque and random; the server maps it to the ticket (tier, match_id,
device). The client never sends a match_id or a tier. Tier rules are in tickets.py: silver = Casual only, gold =
Analyst + Casual, demo = gold + Inspector + phase panel (also for the judges); one device per ticket except demo.
Demo sessions have their own phase (demo panel); the others follow the stadium clock (MOCK_PHASE).
Inspector fields are stripped for silver and gold.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..games import guess as gtp
from ..games import ttt
from ..games.universe import CRESTS
from ..report import recap_pdf, report_filename
from . import CONTRACT_VERSION
from . import tickets as tk
from .models import (
    MAX_FAVOURITES, AnswerRequest, AnswerResult, ApiError, ArchieAnswer, ArchieRequest, DemoPhaseRequest, EventFeed, GameList,
    Highlight, HighlightList, Leaderboard, LiveStats, LoginRequest, LoginResponse, MatchOverview, MatchState,
    MatchStats, Mode, Phase, PlayerPicker, Preferences, Recap, Section, Session, TicketInfo, TicketTier, TttGame,
    TttMoveRequest, TttMoveResult, GuessRound, QuizRound,
)
from .phases import is_unlocked, next_unlock

FAKE = Path(__file__).resolve().parent / "fake"
P1_LEN = 47.0
# Elapsed minutes of each phase snapshot (same table as make_fake.PHASES; a test keeps them equal).
PHASE_ELAPSED = {"pre_match": -1.0, "first_half": 30.5, "half_time": 47.0, "second_half": 68.5, "full_time": 96.0}
STATS_SCOPE = {Phase.first_half: "live_first_half", Phase.half_time: "first_half",
               Phase.second_half: "live_second_half", Phase.full_time: "full_match"}
POINTS_CORRECT, MAX_SPEED_BONUS = 100, 50


@lru_cache(maxsize=None)
def fake(name: str) -> dict:
    return json.loads((FAKE / name).read_text(encoding="utf-8"))


class ApiException(Exception):
    def __init__(self, status: int, code: str, message: str, section: str | None = None, unlocks: str | None = None):
        self.status = status
        self.error = ApiError(code=code, message=message, section=section, unlocks_at_phase=unlocks)


@dataclass
class SessionState:
    ticket: tk.RegistryTicket
    tier: TicketTier
    match_id: str
    device_id: str
    preferences: Preferences
    demo_phase: Phase | None = None
    rounds: dict[str, float] = field(default_factory=dict)  # round id -> server start time (monotonic)
    ttt: ttt.Game | None = None
    guess_played: int = 0  # Guess the player rounds started in this session (5 at most)
    points: int = 0


class Store:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.devices: dict[str, str] = {}  # ticket_id -> device id (single-device tiers only)
        self.registry = tk.load_registry()

    def stadium_phase(self) -> Phase:
        return Phase(os.environ.get("MOCK_PHASE", Phase.half_time.value))


def now() -> datetime:
    return datetime.now(timezone(timedelta(hours=1)))


def create_app() -> FastAPI:
    store = Store()
    app = FastAPI(
        title="statseat API",
        version=CONTRACT_VERSION,
        description=(
            "Contract between the statseat backend and the PWA (mock implementation with fake data). "
            "match_id and the ticket tier are derived from the ticket token server-side. Tiers: silver = Casual only; "
            "gold = Analyst with switch to Casual; demo = gold + Inspector + phase panel (also for the judges). "
            "One device per ticket except demo. Payloads built from the match carry as_of_s (no look-ahead). "
            "All names are fictional (universe MERIDIA-1.0)."
        ),
    )
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])
    bearer = HTTPBearer(auto_error=False)
    err = {401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}, 409: {"model": ApiError},
           422: {"model": ApiError}, 423: {"model": ApiError}}

    @app.exception_handler(ApiException)
    async def _api_error(_: Request, exc: ApiException):
        return JSONResponse(status_code=exc.status, content=exc.error.model_dump(mode="json"))

    # ------------------------------------------------------------ helpers
    def session(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> SessionState:
        if cred is None or cred.credentials not in store.sessions:
            raise ApiException(401, "invalid_token", "Log in with your ticket code first.")
        return store.sessions[cred.credentials]

    def phase_of(s: SessionState) -> Phase:
        return s.demo_phase or store.stadium_phase()

    def require(s: SessionState, section: Section) -> Phase:
        ph = phase_of(s)
        if not is_unlocked(section, ph):
            nxt = next_unlock(section, ph)
            raise ApiException(423, "section_locked", f"{section.value} is not available in phase {ph.value}.",
                               section.value, nxt.value if nxt else None)
        return ph

    def rules(s: SessionState) -> tk.TierRules:
        return tk.TIER_RULES[s.tier]

    def inspector_ok(s: SessionState) -> bool:
        return rules(s).inspector

    def mode_for(s: SessionState, mode: Mode | None) -> Mode:
        m = mode or s.preferences.mode
        if m not in rules(s).allowed_modes:
            raise ApiException(403, "mode_not_allowed", f"{m.value.capitalize()} mode is not included in a {s.tier.value} ticket.")
        return m

    def as_of(s: SessionState) -> float:
        return round(max(PHASE_ELAPSED[phase_of(s).value], 0.0) * 60, 1)

    def strip(data: dict, s: SessionState) -> dict:
        data = copy.deepcopy(data)
        if not inspector_ok(s):
            if "inspector" in data:
                data["inspector"] = None
            for h in data.get("highlights", []):
                h["inspector"] = None
        return data

    def session_view(s: SessionState) -> Session:
        r = rules(s)
        return Session(tier=s.tier, allowed_modes=list(r.allowed_modes), inspector_enabled=r.inspector,
                       demo_panel_enabled=r.demo_panel, multi_device=r.multi_device,
                       match=fake("session.json")["match"], preferences=s.preferences)

    def event_elapsed(e: dict) -> float:
        return e["match_time_s"] / 60 + (P1_LEN if e["period"] == 2 else 0.0)

    def start_round(s: SessionState, round_id: str, data: dict, seconds_field: str, deadline_field: str) -> dict:
        s.rounds[round_id] = time.monotonic()
        data = copy.deepcopy(data)
        data[deadline_field] = (now() + timedelta(seconds=data[seconds_field])).isoformat()
        return data

    def score_answer(s: SessionState, round_id: str, limit_s: int, correct_id: str, given: str, fact: str) -> AnswerResult:
        if round_id not in s.rounds:
            raise ApiException(404, "round_not_found", "Start the round first.")
        elapsed_ms = int((time.monotonic() - s.rounds.pop(round_id)) * 1000)
        in_time = elapsed_ms <= limit_s * 1000
        correct = given == correct_id and in_time
        bonus = round(MAX_SPEED_BONUS * (1 - elapsed_ms / (limit_s * 1000))) if correct else 0
        pts = POINTS_CORRECT + bonus if correct else 0
        s.points += pts
        return AnswerResult(correct=correct, correct_option_id=correct_id, elapsed_ms=elapsed_ms,
                            points_awarded=pts, speed_bonus=bonus, fact=fact)

    # ------------------------------------------------------------ health, auth, session
    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "contract_version": CONTRACT_VERSION, "mock": True}

    @app.post("/api/auth/login", response_model=LoginResponse, responses=err, tags=["auth"])
    def login(body: LoginRequest) -> LoginResponse:
        ticket = store.registry.find(body.ticket_code) if tk.parse_code(body.ticket_code) else None
        if ticket is None:
            raise ApiException(401, "invalid_ticket", "Unknown ticket code.")
        if not ticket.valid_from <= now() <= ticket.valid_until:
            raise ApiException(403, "ticket_not_valid", "This ticket is not valid at this time.")
        r = tk.TIER_RULES[ticket.tier]
        if not r.multi_device:
            bound = store.devices.get(ticket.ticket_id)
            if bound is not None and bound != body.device_id:
                raise ApiException(409, "ticket_in_use", "This ticket is already in use on another device.")
            store.devices[ticket.ticket_id] = body.device_id
        token = secrets.token_urlsafe(24)
        # a new session has no favourites: the fan chooses them (L-2g); preferences.json is only an example payload
        prefs = Preferences(**{**fake("preferences.json"), "mode": r.default_mode, "favourite_player_ids": []})
        s = SessionState(ticket=ticket, tier=ticket.tier, match_id=ticket.match_id, device_id=body.device_id,
                         preferences=prefs)
        store.sessions[token] = s
        return LoginResponse(token=token, session=session_view(s))

    @app.get("/api/session", response_model=Session, responses=err, tags=["auth"])
    def get_session(s: SessionState = Depends(session)) -> Session:
        return session_view(s)

    @app.put("/api/session/preferences", response_model=Preferences, responses=err, tags=["auth"])
    def put_preferences(body: Preferences, s: SessionState = Depends(session)) -> Preferences:
        ids = {p["player_id"] for t in fake("match.json")["teams"] for p in t["lineup"]}
        favs = body.favourite_player_ids
        unknown = [p for p in favs if p not in ids]
        if unknown:
            raise ApiException(422, "unknown_player", f"Not in this match: {', '.join(unknown)}")
        if len(set(favs)) != len(favs):
            raise ApiException(422, "duplicate_player", "A favourite player can be chosen only once.")
        if s.preferences.favourites_locked and (favs != s.preferences.favourite_player_ids or not body.favourites_locked):
            raise ApiException(409, "favourites_locked", "Your favourite players are confirmed and cannot change.")
        if body.favourites_locked and len(favs) != MAX_FAVOURITES:
            raise ApiException(422, "favourites_incomplete", f"Choose {MAX_FAVOURITES} players before confirming.")
        mode_for(s, body.mode)
        s.preferences = body
        return body

    # ------------------------------------------------------------ My ticket
    @app.get("/api/ticket", response_model=TicketInfo, responses=err, tags=["auth"])
    def my_ticket(s: SessionState = Depends(session)) -> dict:
        return tk.ticket_info(s.ticket, fake("session.json")["match"], tk.stadium_of(s.match_id), now())

    @app.get("/api/ticket/image", responses={**err, 200: {"content": {"image/svg+xml": {}, "image/png": {},
                                                                       "application/pdf": {}}}}, tags=["auth"])
    def my_ticket_image(format: Literal["svg", "pdf", "png"] = "svg", s: SessionState = Depends(session)) -> FileResponse:
        path = s.ticket.files.get(format)
        if path is None or not (tk.ROOT / path).is_file():
            raise ApiException(404, "ticket_image_not_ready", "The ticket image has not been generated yet.")
        media = {"svg": "image/svg+xml", "pdf": "application/pdf", "png": "image/png"}[format]
        return FileResponse(tk.ROOT / path, media_type=media)

    # ------------------------------------------------------------ match
    @app.get("/api/match", response_model=MatchOverview, responses=err, tags=["match"])
    def match(s: SessionState = Depends(session)) -> dict:
        return fake("match.json")

    @app.get("/api/match/state", response_model=MatchState, responses=err, tags=["match"])
    def match_state(s: SessionState = Depends(session)) -> dict:
        data = copy.deepcopy(fake(f"match_state_{phase_of(s).value}.json"))
        data["server_time"] = now().isoformat()
        return data

    @app.get("/api/match/events", response_model=EventFeed, responses=err, tags=["match"])
    def events(s: SessionState = Depends(session)) -> dict:
        cut = PHASE_ELAPSED[phase_of(s).value]
        data = fake("events.json")
        return {"match_id": data["match_id"], "as_of_s": as_of(s),
                "events": [e for e in data["events"] if event_elapsed(e) <= cut]}

    # ------------------------------------------------------------ statistics
    def favourites_only(data: dict, key: str, s: SessionState) -> dict:
        """The fake files carry a Player Focus card for every player who played (v0.3.0): serve the session's
        favourites only, in the order the fan chose them."""
        cards = {c["player_id"]: c for c in data[key]}
        data[key] = [cards[pid] for pid in s.preferences.favourite_player_ids if pid in cards]
        return data

    @app.get("/api/stats/live", response_model=LiveStats, responses=err, tags=["stats"])
    def stats_live(s: SessionState = Depends(session)) -> dict:
        ph = require(s, Section.live)
        return fake(f"stats_live_{ph.value}.json")

    @app.get("/api/stats", response_model=MatchStats, responses=err, tags=["stats"])
    def stats(mode: Mode | None = Query(None, description="Defaults to the session mode"),
              s: SessionState = Depends(session)) -> dict:
        ph = require(s, Section.data_stats)
        m = mode_for(s, mode).value
        return favourites_only(strip(fake(f"stats_{m}_{STATS_SCOPE[ph]}.json"), s), "player_focus", s)

    @app.get("/api/recap", response_model=Recap, responses=err, tags=["stats"])
    def recap(mode: Mode | None = Query(None), s: SessionState = Depends(session)) -> dict:
        require(s, Section.recap)
        return favourites_only(strip(fake(f"recap_{mode_for(s, mode).value}.json"), s), "favourite_players", s)

    @app.get("/api/recap/pdf", response_class=Response, tags=["stats"],
             responses={**err, 200: {"content": {"application/pdf": {}}, "description": "The final report (A4 PDF)"}})
    def recap_report(mode: Mode | None = Query(None), s: SessionState = Depends(session)) -> Response:
        """The final report in PDF, built by the server (L-2g, v0.2.6): the Recap's summary (computed by the code),
        its key moments and the Data & Statistics of the same version, with the fan's favourite players. Open after the
        final whistle (423 before), English only."""
        r = recap(mode, s)
        return report_response(r, stats(mode, s), highlights(s)["highlights"])

    # ------------------------------------------------------------ highlights
    def visible_highlights(s: SessionState) -> list[dict]:
        cut = PHASE_ELAPSED[phase_of(s).value]
        evs = {e["event_id"]: e for e in fake("events.json")["events"]}
        out = []
        for h in fake("highlights.json")["highlights"]:
            el = max((event_elapsed(evs[i]) for i in h["event_ids"]), default=None)
            if el is None:
                el = h["minute"] if h["period"] == 1 else P1_LEN + h["minute"] - 45
            if el <= cut:
                out.append(h)
        return out

    @app.get("/api/highlights", response_model=HighlightList, responses=err, tags=["highlights"])
    def highlights(s: SessionState = Depends(session)) -> dict:
        return strip({"match_id": s.match_id, "as_of_s": as_of(s), "highlights": visible_highlights(s)}, s)

    # ------------------------------------------------------------ games
    @app.get("/api/games", response_model=GameList, responses=err, tags=["games"])
    def games(s: SessionState = Depends(session)) -> dict:
        ph = phase_of(s)
        data = copy.deepcopy(fake("games.json"))
        for g in data["games"]:
            g["available"] = is_unlocked(Section.games, ph)
            nxt = next_unlock(Section.games, ph)
            g["unlocks_at_phase"] = nxt.value if nxt else None
        return data

    @app.get("/api/games/players", response_model=PlayerPicker, responses=err, tags=["games"])
    def players(s: SessionState = Depends(session)) -> dict:
        return fake("players.json")

    @app.get("/api/crests/{club_id}", response_class=FileResponse, tags=["games"],
             responses={404: {"model": ApiError}, 200: {"content": {"image/svg+xml": {}}, "description": "The crest"}})
    def crest(club_id: str) -> FileResponse:
        """A club's crest (M-1, synthetic/crests/). Public, like any image of the app: no token needed."""
        path = CRESTS / f"{club_id}.svg"
        if not (len(club_id) == 3 and club_id.isalpha() and club_id.isupper() and path.is_file()):
            raise ApiException(404, "crest_not_found", "Unknown club.")
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    def match_clubs() -> tuple[str, str]:
        teams = fake("match.json")["teams"]
        return tuple(next(t["team_id"] for t in teams if t["role"] == role) for role in ("home", "away"))

    def my_ttt(s: SessionState, game_session_id: str) -> ttt.Game:
        if s.ttt is None or s.ttt.view["game_session_id"] != game_session_id:
            raise ApiException(404, "game_not_found", "Start a game first.")
        s.points += ttt.refresh(s.ttt, time.monotonic(), now())
        return s.ttt

    @app.post("/api/games/tiki-taka-toe", response_model=TttGame, responses=err, tags=["games"])
    def ttt_start(s: SessionState = Depends(session)) -> dict:
        """A new game against the fan-bot (M-3, app/games/ttt.py): the match's grid, 20 s per turn, 10 minutes."""
        require(s, Section.games)
        s.ttt = ttt.start(*match_clubs(), f"TTT-{secrets.token_hex(4).upper()}", time.monotonic(), now())
        return s.ttt.view

    @app.get("/api/games/tiki-taka-toe/{game_session_id}", response_model=TttGame, responses=err, tags=["games"])
    def ttt_get(game_session_id: str, s: SessionState = Depends(session)) -> dict:
        """The game now (v0.3.1): after the 20 s the fan has lost the turn and the bot has played."""
        require(s, Section.games)
        return my_ttt(s, game_session_id).view

    @app.post("/api/games/tiki-taka-toe/{game_session_id}/moves", response_model=TttMoveResult, responses=err, tags=["games"])
    def ttt_move(game_session_id: str, body: TttMoveRequest, s: SessionState = Depends(session)) -> dict:
        require(s, Section.games)
        if s.ttt is None or s.ttt.view["game_session_id"] != game_session_id:
            raise ApiException(404, "game_not_found", "Start a game first.")
        if body.player_id not in {p["player_id"] for p in fake("players.json")["players"]}:
            raise ApiException(422, "unknown_player", "Pick a player from the list.")
        try:
            result = ttt.move(s.ttt, body.row, body.col, body.player_id, time.monotonic(), now())
        except ttt.GameOver:
            raise ApiException(409, "game_over", "This game is over.") from None
        s.points += result["points_awarded"]
        return result

    @app.post("/api/games/guess-the-player", response_model=GuessRound, responses=err, tags=["games"])
    def guess_start(version: Literal["career", "transfers"] = "career", s: SessionState = Depends(session)) -> dict:
        """The next of the session's 5 questions (M-3, app/games/guess.py): always the 'career' version."""
        require(s, Section.games)
        if s.guess_played >= gtp.ROUNDS:
            raise ApiException(409, "no_more_rounds", f"You have played all {gtp.ROUNDS} questions.")
        s.guess_played += 1
        data = gtp.round_view(s.match_id, *match_clubs(), s.guess_played)
        return start_round(s, data["round_id"], data, "answer_seconds", "deadline")

    @app.post("/api/games/guess-the-player/{round_id}/answer", response_model=AnswerResult, responses=err, tags=["games"])
    def guess_answer(round_id: str, body: AnswerRequest, s: SessionState = Depends(session)) -> AnswerResult:
        require(s, Section.games)
        a = gtp.answer_of(s.match_id, *match_clubs(), round_id)
        if a is None:
            raise ApiException(404, "round_not_found", "Unknown round.")
        return score_answer(s, round_id, gtp.ANSWER_SECONDS, a["correct"], body.option_id, a["fact"])

    @app.post("/api/games/quiz", response_model=QuizRound, responses=err, tags=["games"])
    def quiz_start(s: SessionState = Depends(session)) -> dict:
        require(s, Section.games)
        data = fake("quiz_round.json")
        return start_round(s, data["round_id"], data, "answer_seconds", "deadline")

    @app.post("/api/games/quiz/{round_id}/answer", response_model=AnswerResult, responses=err, tags=["games"])
    def quiz_answer(round_id: str, body: AnswerRequest, s: SessionState = Depends(session)) -> AnswerResult:
        require(s, Section.games)
        a = fake("server/answers.json")["quiz"].get(round_id)
        if a is None:
            raise ApiException(404, "round_not_found", "Unknown round.")
        return score_answer(s, round_id, fake("quiz_round.json")["answer_seconds"], a["correct"], body.option_id, a["fact"])

    @app.get("/api/leaderboard", response_model=Leaderboard, responses=err, tags=["games"])
    def leaderboard(s: SessionState = Depends(session)) -> dict:
        data = copy.deepcopy(fake("leaderboard.json"))
        me = next(e for e in data["entries"] if e["is_me"])
        me["points"] = data["my_points"] = data["my_points"] + s.points
        data["entries"].sort(key=lambda e: -e["points"])
        for i, e in enumerate(data["entries"], start=1):
            e["rank"] = i
        data["my_rank"] = me["rank"]
        return data

    # ------------------------------------------------------------ Ask Archie
    @app.post("/api/archie/ask", response_model=ArchieAnswer, responses=err, tags=["archie"])
    def ask(body: ArchieRequest, s: SessionState = Depends(session)) -> dict:
        data = strip(fake("archie_answer.json"), s)
        data["language"] = body.language or s.preferences.language
        ph = phase_of(s)
        if data["points_to"] and not is_unlocked(Section.data_stats, ph):
            nxt = next_unlock(Section.data_stats, ph)
            data["points_to"].update(locked=True, unlocks_at_phase=nxt.value if nxt else None)
            data["text"] += " That chart opens at half-time."
        return data

    # ------------------------------------------------------------ demo panel
    @app.post("/api/demo/phase", response_model=MatchState, responses=err, tags=["demo"])
    def demo_phase(body: DemoPhaseRequest, s: SessionState = Depends(session)) -> dict:
        if not rules(s).demo_panel:
            raise ApiException(403, "demo_only", "The demo panel is only for demo tickets.")
        s.demo_phase = body.phase
        return match_state(s)

    return app


def report_response(recap: dict, stats: dict, highlights: list[dict]) -> Response:
    """The final report as a downloadable PDF (app/report/), with the names and colours of the Pre-match overview.
    `recap` and `stats` are the payloads of /api/recap and /api/stats for the same session and mode, `highlights` those
    of /api/highlights: the report keeps the Recap's key moments, in its order. All go through the contract models,
    so the PDF reads exactly what the app receives."""
    recap = Recap.model_validate(recap).model_dump(mode="json")
    stats = MatchStats.model_validate(stats).model_dump(mode="json")
    by_id = {h["highlight_id"]: Highlight.model_validate(h).model_dump(mode="json") for h in highlights}
    moments = [by_id[i] for i in recap["highlight_ids"] if i in by_id]
    pdf = recap_pdf(recap, stats, moments, fake("session.json")["match"], fake("match.json"), now())
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{report_filename(recap)}"'})


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"statseat mock API {CONTRACT_VERSION}: http://127.0.0.1:{port}/docs")
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
