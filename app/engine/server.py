"""The real statseat backend: the match engine plus the contract's mock for everything the engine does not own yet.

Run from the repo root:  python -m app.engine   (then open http://127.0.0.1:8000/docs)

Owned by the engine (S-7), built from the no-look-ahead window: `GET /api/match/state`, `GET /api/match/events`,
`GET /api/stats/live`, `GET /api/stats`, `GET /api/recap` and its PDF (`GET /api/recap/pdf`, L-2g),
`POST /api/demo/phase`, `GET /api/health`, plus two engine extensions: `POST /api/demo/clock` (demo tickets pause or
resume their own replay) and `GET /api/engine/status`.

Ask Archie (`POST /api/archie/ask`, AG-1b) is Archie on Foundry with The Analyst's tools and the grounding validator
(`app/agents/archie.py`) when Foundry is configured (`FOUNDRY_PROJECT_ENDPOINT`, `ARCHIE_AGENT_ID`,
`ANALYST_AGENT_ID`); otherwise it stays on the contract mock.

Delegated to the contract mock (`app/contract/mock.py`) until their tasks replace them: login, session, preferences,
My ticket, match overview, highlights (D-1), games and leaderboard (S-8). They follow the
engine's phase: the mock's stadium phase is the engine's stadium clock, and a demo session's phase is its own clock.
The app (S-5) talks to the same paths and models, so it switches from the mock to this server without changes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.contract import CONTRACT_VERSION
from app.contract import mock
from app.contract import tickets as tk
from app.contract.models import (
    ApiError, ArchieAnswer, ArchieRequest, DemoPhaseRequest, EventFeed, HighlightList, LiveStats, MatchState, MatchStats, Mode, Phase, Recap,
    Section,
)
from app.contract.phases import is_unlocked, next_unlock
from app.games import quiz_routes

from . import highlights as var
from . import stats as stats_module
from .config import EngineConfig
from .engine import Engine
from .sources.base import AWAY, HOME

OWNED = {("GET", "/api/health"), ("GET", "/api/match/state"), ("GET", "/api/match/events"), ("GET", "/api/stats/live"),
         ("GET", "/api/stats"), ("GET", "/api/recap"), ("GET", "/api/recap/pdf"), ("POST", "/api/demo/phase"),
         ("POST", "/api/games/quiz"), ("POST", "/api/games/quiz/{round_id}/answer")}  # the quiz needs the stats (M-3)
# The mock's highlights tell the fake story (app/contract/fake/): with a real match they would show moments that never
# happened, so the engine answers them itself (no highlights until VAR chooses them, task D-1).
HIGHLIGHT_ROUTES = {("GET", "/api/highlights")}
ERR = {c: {"model": ApiError} for c in (401, 403, 404, 409, 422, 423)}


class DemoClockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "resume"]


def now() -> datetime:
    return datetime.now(timezone(timedelta(hours=1)))


def _names() -> tuple[dict[str, str], dict[str, str]]:
    """Display names of the SYN-001 universe (the Pre-match overview); other sources fall back to ids."""
    m = json.loads((mock.FAKE / "match.json").read_text(encoding="utf-8"))
    players = {p["player_id"]: p["name"] for t in m["teams"] for p in t["lineup"]}
    teams = {HOME if t["role"] == "home" else AWAY: t["name"] for t in m["teams"]}
    return players, teams


def _archie_from_env():
    """Archie on Foundry when it is configured, else None (the mock answers). Imported lazily, so the mock path does
    not need the Foundry packages."""
    from app.agents.settings import FoundrySettings

    settings = FoundrySettings.from_env()
    if not settings.configured:
        return None
    from app.agents.archie import ArchieService

    return ArchieService(settings)


def create_app(engine: Engine | None = None, archie=None, the_ref=None) -> FastAPI:
    """`archie`: an `app.agents.archie.ArchieService` (the tests pass one with a fake Foundry); by default from the
    environment, or the contract mock when Foundry is not configured. `the_ref`: an `app.agents.the_ref.TheRef` for
    the quiz text (AG-4); by default from the environment, the code's English without Foundry."""
    engine = engine or Engine.from_config(EngineConfig.from_env())
    archie = archie if archie is not None else _archie_from_env()
    app = mock.create_app()
    app.title = "statseat API"
    app.description = (f"statseat backend: match engine (S-7, MATCH_SOURCE={engine.source}, {engine.cfg.speed:g}x) with "
                       "the no-look-ahead window; other endpoints served by the contract mock until their tasks land. "
                       + app.description)
    app.state.engine = engine
    app.state.archie = archie
    if the_ref is None:
        from app.agents import the_ref as the_ref_module

        the_ref = the_ref_module.from_env()
    app.state.the_ref = the_ref
    store: mock.Store = app.state.store
    if engine.source.startswith("synthetic:") and not os.environ.get(tk.REGISTRY_ENV):
        # The printed tickets of the synthetic match (S-4); the fake codes of app/contract/README.md stay with `fake`.
        generated = tk.ROOT / "synthetic" / engine.source.partition(":")[2] / "tickets" / "tickets.json"
        if generated.is_file():
            store.registry = tk.load_registry(generated)
    store.stadium_phase = lambda: engine.snapshot().phase  # mock endpoints follow the stadium clock
    fake_story = engine.source == "fake"
    owned = OWNED if fake_story else OWNED | HIGHLIGHT_ROUTES
    if archie is not None:
        owned = owned | {("POST", "/api/archie/ask")}
    mock_highlights = next(r.endpoint for r in app.router.routes
                           if isinstance(r, APIRoute) and r.path == "/api/highlights")  # the fake story's
    app.router.routes = [r for r in app.router.routes
                         if not (isinstance(r, APIRoute) and any((m, r.path) in owned for m in r.methods))]
    app.openapi_schema = None
    bearer = HTTPBearer(auto_error=False)
    player_names, team_names = _names()

    @app.middleware("http")
    async def _demo_phase_sync(request: Request, call_next):
        """Mock endpoints read SessionState.demo_phase: keep it equal to the demo session's own clock."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            s = store.sessions.get(token)
            if s is not None and token in engine.demo:
                s.demo_phase = engine.snapshot(token).phase
        return await call_next(request)

    def session(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> tuple[str, mock.SessionState]:
        if cred is None or cred.credentials not in store.sessions:
            raise mock.ApiException(401, "invalid_token", "Log in with your ticket code first.")
        return cred.credentials, store.sessions[cred.credentials]

    def require(snap, section: Section) -> None:
        if not is_unlocked(section, snap.phase):
            nxt = next_unlock(section, snap.phase)
            raise mock.ApiException(423, "section_locked", f"{section.value} is not available in phase {snap.phase.value}.",
                                    section.value, nxt.value if nxt else None)

    def mode_for(s: mock.SessionState, mode: Mode | None) -> Mode:
        m = mode or s.preferences.mode
        if m not in tk.TIER_RULES[s.tier].allowed_modes:
            raise mock.ApiException(403, "mode_not_allowed",
                                    f"{m.value.capitalize()} mode is not included in a {s.tier.value} ticket.")
        return m

    def state_payload(token: str) -> dict:
        return stats_module.match_state(engine.snapshot(token), now())

    def var_highlights(token: str, s: mock.SessionState) -> dict:
        """VAR's moments for this session, from the engine's located shots and xG."""
        snap = engine.snapshot(token)
        core = engine.live_core(snap) if snap.phase in (Phase.first_half, Phase.second_half) else engine.core(snap)
        shots = core.fr["shots"] if core.fr else None
        return {"match_id": snap.match_id, "as_of_s": round(snap.as_of_s, 1),
                "highlights": var.payload(core.snap, shots, player_names, team_names, tk.TIER_RULES[s.tier].inspector)}

    def shown_highlights(token: str, s: mock.SessionState) -> list[dict]:
        """The moments /api/highlights gives this session: VAR's, or the mock's fake story with MATCH_SOURCE=fake."""
        return (mock_highlights(s) if fake_story else var_highlights(token, s))["highlights"]

    quiz_routes.register(app, engine, session, require, the_ref)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "contract_version": CONTRACT_VERSION, "mock": False,
                "archie": "foundry" if archie is not None else "mock",
                "the_ref": "foundry" if the_ref.call is not None else "code",
                "engine": {"match_source": engine.source, "speed": engine.cfg.speed,
                           "event_buffer_s": engine.cfg.event_buffer_s, "stats_tick_s": engine.tick_s}}

    @app.get("/api/engine/status", tags=["meta"])
    def engine_status() -> dict:
        """Stadium clock (no login): phase, scoreboard minute and the window instant. Handy to watch the replay."""
        snap = engine.snapshot()
        minute, added = stats_module.scoreboard(snap)
        return {"match_source": engine.source, "phase": snap.phase.value, "minute": minute, "added_time": added,
                "as_of_s": round(snap.as_of_s, 1), "cursor_s": round(snap.cursor_t, 1),
                "running": engine.stadium.running, "speed": engine.cfg.speed,
                "events_released": len(snap.events), "frames_released": {p: len(f) for p, f in snap.frames.items()}}

    @app.get("/api/match/state", response_model=MatchState, responses=ERR, tags=["match"])
    def match_state(auth=Depends(session)) -> dict:
        return state_payload(auth[0])

    if not fake_story:
        @app.get("/api/highlights", response_model=HighlightList, responses=ERR, tags=["highlights"])
        def highlights(auth=Depends(session)) -> dict:
            return var_highlights(*auth)

    @app.get("/api/match/events", response_model=EventFeed, responses=ERR, tags=["match"])
    def events(auth=Depends(session)) -> dict:
        return stats_module.event_feed(engine.snapshot(auth[0]))

    @app.get("/api/stats/live", response_model=LiveStats, responses=ERR, tags=["stats"])
    def stats_live(auth=Depends(session)) -> dict:
        snap = engine.snapshot(auth[0])
        require(snap, Section.live)
        return stats_module.live_stats(engine.live_core(snap))

    @app.get("/api/stats", response_model=MatchStats, responses=ERR, tags=["stats"])
    def get_stats(mode: Mode | None = Query(None, description="Defaults to the session mode"), auth=Depends(session)) -> dict:
        token, s = auth
        snap = engine.snapshot(token)
        require(snap, Section.data_stats)
        m = mode_for(s, mode)
        # during play the numbers come from the live tick (an earlier, complete instant), as for /api/stats/live
        core = engine.live_core(snap) if snap.phase in (Phase.first_half, Phase.second_half) else engine.core(snap)
        return stats_module.match_stats(core, m, tk.TIER_RULES[s.tier].inspector, now(),
                                        list(s.preferences.favourite_player_ids))

    @app.get("/api/recap", response_model=Recap, responses=ERR, tags=["stats"])
    def get_recap(mode: Mode | None = Query(None), auth=Depends(session)) -> dict:
        token, s = auth
        snap = engine.snapshot(token)
        require(snap, Section.recap)
        m = mode_for(s, mode)
        core = engine.core(snap)
        p = stats_module.recap(core, m, list(s.preferences.favourite_player_ids), player_names, team_names,
                               tk.TIER_RULES[s.tier].inspector)
        # VAR (AG-3): every goal, then the other moments by score, up to 10
        p["highlight_ids"] = var.recap_ids(var.moments(snap, core.fr["shots"] if core.fr else None))
        p["unavailable"] = [u for u in p["unavailable"] if u["metric"] != "highlight_ids"]
        return p

    @app.get("/api/recap/pdf", response_class=Response, tags=["stats"],
             responses={**ERR, 200: {"content": {"application/pdf": {}}, "description": "The final report (A4 PDF)"}})
    def get_recap_pdf(mode: Mode | None = Query(None), auth=Depends(session)) -> Response:
        """The final report in PDF (L-2g), built by the server from what the app shows this ticket in this version:
        the Recap (/api/recap), the Data & Statistics (/api/stats) and VAR's key moments of the Recap. Registered
        before the PWA's catch-all route (C-1), which would otherwise answer this path."""
        recap = get_recap(mode, auth)
        return mock.report_response(recap, get_stats(mode, auth), shown_highlights(*auth))

    @app.post("/api/demo/phase", response_model=MatchState, responses=ERR, tags=["demo"])
    def demo_phase(body: DemoPhaseRequest, auth=Depends(session)) -> dict:
        token, s = auth
        if not tk.TIER_RULES[s.tier].demo_panel:
            raise mock.ApiException(403, "demo_only", "The demo panel is only for demo tickets.")
        try:
            engine.jump(token, body.phase)
        except ValueError as exc:
            raise mock.ApiException(404, "phase_not_in_match", str(exc)) from None
        snap = engine.snapshot(token)
        s.demo_phase = snap.phase
        engine.prewarm(snap)
        return state_payload(token)

    @app.post("/api/demo/clock", response_model=MatchState, responses=ERR, tags=["demo"])
    def demo_clock(body: DemoClockRequest, auth=Depends(session)) -> dict:
        """Engine extension (not in the contract): pause or resume this demo session's replay."""
        token, s = auth
        if not tk.TIER_RULES[s.tier].demo_panel:
            raise mock.ApiException(403, "demo_only", "The demo panel is only for demo tickets.")
        engine.set_running(token, body.action == "resume")
        s.demo_phase = engine.snapshot(token).phase
        return state_payload(token)

    if archie is not None:
        from app.agents.analyst_tools import AnalystSession

        @app.post("/api/archie/ask", response_model=ArchieAnswer, responses=ERR, tags=["archie"])
        async def ask(body: ArchieRequest, auth=Depends(session)) -> dict:
            """Archie on Foundry (AG-1b): MATCH CONTEXT from the session, The Analyst's tools with the fan's session,
            grounding on every number, the Inspector for demo tickets; the deterministic card if Foundry fails."""
            token, s = auth
            analyst = AnalystSession.from_state(engine, token, s, player_names, team_names)
            return await archie.ask(analyst, s, body, tk.TIER_RULES[s.tier].inspector)

        app.router.on_shutdown.append(archie.close)  # the Foundry client's HTTP sessions

    _serve_pwa(app)
    return app


def _serve_pwa(app: FastAPI) -> None:
    """In the container (task C-1) the same server also serves the built PWA (`npm run build` in web/), so app and API
    share one origin. STATSEAT_WEB_DIST points to the build (default web/dist); without a build nothing is mounted."""
    dist = Path(os.environ.get("STATSEAT_WEB_DIST", tk.ROOT / "web" / "dist")).resolve()
    if not (dist / "index.html").is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    def pwa(path: str):
        if path == "api" or path.startswith("api/"):
            raise mock.ApiException(404, "not_found", "No such API endpoint.")
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist):
            # hashed assets never change; the service worker, the manifest and index.html must never be stale
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})  # client-side routes


def main() -> None:
    import uvicorn

    from app.agents.settings import load_dotenv

    load_dotenv()  # local runs: the repo's .env (Foundry settings); in Azure the variables come from infra/
    port = int(os.environ.get("PORT", "8000"))
    app = create_app()
    eng: Engine = app.state.engine
    archie = "Archie on Foundry" if app.state.archie is not None else "Archie on the mock"
    ref = "The Ref on Foundry" if app.state.the_ref.call is not None else "quiz in English"
    print(f"statseat backend {CONTRACT_VERSION}, engine on {eng.source} at {eng.cfg.speed:g}x, {archie}, {ref}: "
          f"http://127.0.0.1:{port}/docs")
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=port)
