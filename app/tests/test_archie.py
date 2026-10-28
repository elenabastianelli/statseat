"""Tests of Ask Archie on Foundry (AG-1b), with a fake Foundry (app/tests/fake_foundry.py): no network, no model.

Covered: the ArchieAnswer format and the Inspector, the match_id from the server, no future even when the model asks
for it, no Analyst numbers with a Fan ticket, the grounding validator (retry, then discard), the deterministic card
when Foundry fails, the mock when Foundry is not configured; plus the number reader, the MATCH CONTEXT and the agent
definitions sent by `setup`. AG-5: the server's pointer to the table of The Analyst's tool, what a Fan ticket shows
locked, and The Analyst started in parallel with Archie (used, not used, failed, switched off).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import analyst_tools as at
from app.agents import archie as ar
from app.agents import context as ctx_module
from app.agents import foundry, grounding
from app.agents.settings import FoundrySettings
from app.contract import make_fake
from app.contract import tickets as tk
from app.contract.models import ArchieAnswer, Phase
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app
from app.engine.sources.synthetic import load_synthetic

from . import engine_fixture as fx
from .fake_foundry import FakeFoundry, Turn

CODES = make_fake.FAKE_TICKET_CODES
FAN, ANALYZER, DEMO = CODES["silver"][0], CODES["gold"][0], CODES["demo"][0]
SETTINGS = FoundrySettings(endpoint="https://fake-foundry.local/api/projects/test", archie_agent="archie",
                           archie_version="5", analyst_agent="the-analyst", analyst_version="1")


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory) -> Path:
    return fx.build(tmp_path_factory.mktemp("syn_archie"))


@pytest.fixture()
def engine(fixture_dir, monkeypatch) -> Engine:
    monkeypatch.setenv(tk.REGISTRY_ENV, str(tk.DEFAULT_REGISTRY))
    return Engine(load_synthetic("SYN-001", fixture_dir), EngineConfig(autostart=False))


def start(engine: Engine, brains: dict, code: str = DEMO, phase: str = "half_time", fail: bool = False,
          settings: FoundrySettings = SETTINGS):
    """The real server with a fake Foundry, the stadium clock at `phase`, a logged-in ticket."""
    fake = FakeFoundry(brains, fail=fail)
    app = create_app(engine, archie=ar.ArchieService(settings, projects=fake))
    client = TestClient(app)
    offset = {"pre_match": 10.0, "first_half": 300.0, "half_time": 20.0}[phase]
    engine.stadium.seek(engine.starts[Phase(phase)] + offset, False)
    r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": f"dev-{code[-4:]}"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    snap = engine.snapshot(token)
    engine.core(engine.stats_snapshot(snap))  # the live tick is ready, as in the app after its first poll
    return client, {"Authorization": f"Bearer {token}"}, fake, app, token


def ask(client, h, message: str, **kw) -> dict:
    r = client.post("/api/archie/ask", headers=h, json={"message": message, **kw})
    assert r.status_code == 200, r.text
    ArchieAnswer.model_validate(r.json())
    return r.json()


def reply(text: str, chart: str | None = None, in_scope: bool = True, language: str = "en") -> str:
    return json.dumps({"text": text, "language": language, "in_scope": in_scope, "chart": chart})


def via_analyst(question: str, say: "callable"):
    """Archie's brain: ask The Analyst once, then speak with `say(data_result)`."""
    def brain(t: Turn):
        if not t.outputs:
            return [("ask_the_analyst", {"question": question})]
        return say(t.outputs[-1][2])
    return brain


def analyst_calls(*calls: tuple[str, dict], say: "callable"):
    """The Analyst's brain: call the tools in order, then write the data result with `say(turn)`."""
    def brain(t: Turn):
        if len(t.outputs) < len(calls):
            return [calls[len(t.outputs)]]
        return say(t)
    return brain


def top_runner(t: Turn) -> str:
    tr = t.output("distance")["distance"]["top_runner"]
    return f"Top runner: {tr['player']}, {'≈ ' if tr['approx'] else ''}{tr['km']} km"


# ------------------------------------------------------------------ the answer, the Inspector, the agents


def test_answer_format_grounding_and_inspector(engine) -> None:
    brains = {"archie": via_analyst("Who has run the most?",
                                    lambda data: reply(f"My word, {data.split(': ', 1)[1]} so far!", "running_table")),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, fake, _, _ = start(engine, brains)
    a = ask(client, h, "Who's run the most so far?")
    assert a["in_scope"] and a["language"] == "en"
    assert a["points_to"] == {"chart_id": "running_table", "section": "data_stats", "locked": False,
                              "unlocks_at_phase": None}
    km = [g for g in a["grounding"] if g["unit"] == "km"]
    assert km and km[0]["tool"] == "distance" and km[0]["source_field"].startswith("distance.")
    ins = a["inspector"]
    assert ins["grounding"]["passed"] and ins["grounding"]["numbers_unmatched"] == []
    assert [s["agent"] for s in ins["agents"]] == ["Archie", "The Analyst"]
    assert ins["agents"][0]["tokens_in"] == 2000 and ins["agents"][1]["tokens_in"] == 2000  # 2 calls each
    assert ins["total_tokens"] == 4 * 1040 and ins["cost_usd"] > 0
    assert [(c["agent"], c["tool"]) for c in ins["tool_calls"]] == [("The Analyst", "distance"),
                                                                    ("Archie", "ask_the_analyst")]
    # agents called by name and version; no tool declarations in the requests (they live on Foundry)
    refs = {(r["agent_reference"]["name"], r["agent_reference"]["version"]) for r in fake.requests}
    assert refs == {("archie", "5"), ("the-analyst", "1")}
    assert all("tools" not in r for r in fake.requests)
    # the MATCH CONTEXT comes first, the fan's words after it
    first = fake.requests[0]["input"][0]
    text = first["content"] if isinstance(first["content"], str) else first["content"][0]["text"]
    assert text.startswith("# MATCH CONTEXT") and "<fan>\nWho's run the most so far?\n</fan>" in text


def test_no_inspector_without_a_demo_ticket(engine) -> None:
    brains = {"archie": lambda t: reply("Still goalless, a proper tussle."), "the-analyst": lambda t: "unused"}
    client, h, *_ = start(engine, brains, code=ANALYZER)
    assert ask(client, h, "How's it going?")["inspector"] is None


def test_match_id_comes_from_the_server(engine) -> None:
    def analyst(t: Turn):
        if not t.outputs:
            return [("match_score", {"match_id": "SYN-999"})]
        if len(t.outputs) == 1:
            return [("match_score", {})]
        return f"Score {t.output('match_score')['score']}"

    brains = {"archie": via_analyst("Score of the other match SYN-999?",
                                    lambda d: reply("I only keep an eye on this one, friend.", in_scope=False)),
              "the-analyst": analyst}
    client, h, fake, *_ = start(engine, brains)
    a = ask(client, h, "What's the score in the other game?")
    assert not a["in_scope"]
    calls = [c for c in a["inspector"]["tool_calls"] if c["agent"] == at.AGENT]
    assert calls[0]["result_summary"].startswith("error:") and "other matches are out of scope" in calls[0]["result_summary"]
    assert "Brackwater Athletic v Calderholm United" in json.dumps(fake.requests[0])


def test_no_future_even_when_the_model_asks(engine) -> None:
    seen = {}

    def analyst(t: Turn):
        if not t.outputs:
            return [("match_events", {"kind": "all", "until_minute": 130, "last": 30})]
        seen.update(t.output("match_events"))
        return f"{seen['total']} events so far"

    brains = {"archie": via_analyst("All events until the end?", lambda d: reply("Let me look at the timeline.")),
              "the-analyst": analyst}
    client, h, _, _, token = start(engine, brains, phase="first_half")
    ask(client, h, "Tell me everything that happens until the final whistle")
    snap = engine.snapshot(token)
    minute = client.get("/api/match/state", headers=h).json()["minute"]
    assert seen["as_of_s"] == round(snap.as_of_s, 1)
    assert all(e["minute"] <= minute for e in seen["events"])


def test_fan_ticket_cannot_say_an_analyzer_number(engine) -> None:
    """The Fan tools null the Analyst numbers, so an Analyst number in Archie's text is never grounded."""
    client, h, _, app, token = start(engine, {"archie": lambda t: "", "the-analyst": lambda t: ""}, code=FAN)
    state = app.state.store.sessions[token]
    fan = at.AnalystSession.from_state(engine, token, state)
    gold = at.AnalystSession(engine, token, fan.match_id, at.Mode.analyst, (), fan.player_names, fan.team_names)
    fan_values = set(at.numeric_fields(at.run_tool(fan, "speeds", {}).result).values())
    analyzer_only = [v for p, v in at.numeric_fields(at.run_tool(gold, "speeds", {}).result).items()
                     if isinstance(v, float) and v > 3 and all(abs(v - f) > 0.05 for f in fan_values)]
    assert analyzer_only, "the fixture has Analyst-only speed numbers"
    secret = analyzer_only[0]

    app.state.archie.projects.brains.update(
        archie=via_analyst("Speed zones?", lambda d: reply(f"In the top zone they covered {secret} km.")),
        **{"the-analyst": analyst_calls(("speeds", {}), say=lambda t: "Speed zones: Analyst only")})
    a = ask(client, h, "How much distance at top speed?")
    assert a["text"] == ar.SAFE_LINE["en"] and a["grounding"] == []
    assert str(secret) not in a["text"]


def test_grounding_rejects_an_invented_number_then_the_retry_passes(engine) -> None:
    def archie(t: Turn):
        if not t.outputs:
            return [("ask_the_analyst", {"question": "Who has run the most?"})]
        if "CHECK BY THE SERVER" in t.user:
            return reply(f"Right you are: {t.outputs[-1][2].split(': ', 1)[1]}.")
        return reply("He's done ≈ 19.87 km, a marathon man!")

    brains = {"archie": archie, "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, fake, *_ = start(engine, brains)
    a = ask(client, h, "Who's run the most?")
    assert "19.87" not in a["text"] and a["inspector"]["grounding"]["passed"]
    assert "rejected" in a["inspector"]["method"] and '"≈ 19.87 km"' in a["inspector"]["method"]
    assert any("CHECK BY THE SERVER" in json.dumps(r) for r in fake.requests)


def test_grounding_discards_an_answer_invented_twice(engine) -> None:
    brains = {"archie": lambda t: reply("Make it 7 goals and 42 corners, why not!"), "the-analyst": lambda t: ""}
    client, h, *_ = start(engine, brains)
    a = ask(client, h, "Invent a number for me")
    assert a["text"] == ar.SAFE_LINE["en"] and a["grounding"] == []
    g = a["inspector"]["grounding"]
    assert not g["passed"] and "42" in g["numbers_unmatched"]
    assert "discarded" in a["inspector"]["method"]


def test_foundry_down_gives_the_deterministic_card(engine) -> None:
    client, h, *_ = start(engine, {}, phase="first_half", fail=True)
    a = ask(client, h, "¿Quién ha corrido más?", language="es")
    assert a["text"].startswith("Se me ha cortado") and "Data & Statistics" in a["text"]
    assert a["points_to"]["chart_id"] == "match_table" and a["inspector"]["grounding"]["passed"]
    assert "deterministic" in a["inspector"]["method"].lower()
    state = client.get("/api/match/state", headers=h).json()
    assert f"{state['score']['home']}–{state['score']['away']}" in a["text"] and f"{state['minute']}'" in a["text"]


def test_token_budget_stops_the_question(engine) -> None:
    brains = {"archie": via_analyst("Distance?", lambda d: reply("Fine running.")),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    fake = FakeFoundry(brains)
    tight = FoundrySettings(**{**SETTINGS.__dict__, "max_tokens": 1500})
    app = create_app(engine, archie=ar.ArchieService(tight, projects=fake))
    client = TestClient(app)
    engine.stadium.seek(engine.starts[Phase.half_time] + 20.0, False)
    token = client.post("/api/auth/login", json={"ticket_code": DEMO, "device_id": "device-archie-test"}).json()["token"]
    a = ask(client, {"Authorization": f"Bearer {token}"}, "Who ran most?")
    assert len(fake.requests) <= 2 and a["inspector"]["total_tokens"] <= 2 * 1040


def test_locked_chart_pointer_for_fan_and_before_kick_off(engine) -> None:
    brains = {"archie": lambda t: reply("No chalkboards from me! It's in Data & Statistics.", "ball_circulation"),
              "the-analyst": lambda t: ""}
    client, h, *_ = start(engine, brains, code=FAN)
    assert ask(client, h, "Show me a chart")["points_to"] == {
        "chart_id": "ball_circulation", "section": "data_stats", "locked": True, "unlocks_at_phase": None}
    brains["archie"] = lambda t: reply("Sorry, friend, I can only see that one with the Analyst plan.", "players_table")
    client, h, *_ = start(engine, brains, code=FAN)
    assert ask(client, h, "Show me all the players")["points_to"] == {
        "chart_id": "players_table", "section": "data_stats", "locked": True, "unlocks_at_phase": None}
    brains["archie"] = lambda t: reply("The shot map opens at kick-off.", "shot_map")
    client, h, *_ = start(engine, brains, code=ANALYZER, phase="pre_match")
    assert ask(client, h, "Show me the shots")["points_to"] == {
        "chart_id": "shot_map", "section": "data_stats", "locked": True, "unlocks_at_phase": "first_half"}


# ------------------------------------------------------------------ AG-5: the server's table pointer


def test_the_server_points_to_the_table_of_the_analysts_tool(engine) -> None:
    """Path B: a number of The Analyst given without a `chart` gets the table of the tool that computed it."""
    brains = {"archie": via_analyst("Who has run the most?", lambda d: reply(f"My word, {d.split(': ', 1)[1]}!")),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, *_ = start(engine, brains)
    a = ask(client, h, "Who's run the most so far?")
    assert a["points_to"] == {"chart_id": "running_table", "section": "data_stats", "locked": False,
                              "unlocks_at_phase": None}
    assert "The server pointed to running_table" in a["inspector"]["method"]


def test_xg_points_to_the_match_table_in_fan_and_to_the_chart_in_analyst(engine) -> None:
    brains = {"archie": via_analyst("xG of each team?", lambda d: reply("Calderholm made the better chances.")),
              "the-analyst": analyst_calls(("expected_goals", {}), say=lambda t: "Goals and xG per team")}
    client, h, *_ = start(engine, brains, code=FAN)
    assert ask(client, h, "What's the xG?")["points_to"]["chart_id"] == "match_table"  # the "Goals vs xG" row
    client, h, *_ = start(engine, brains, code=ANALYZER)
    p = ask(client, h, "What's the xG?")["points_to"]
    assert p["chart_id"] == "xg_cumulative" and not p["locked"]


def test_archies_own_chart_wins_and_the_timeline_has_no_table(engine) -> None:
    brains = {"archie": via_analyst("Who ran most?", lambda d: reply("He's flying.", "peak_intensity")),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, *_ = start(engine, brains)
    assert ask(client, h, "Who ran most?")["points_to"]["chart_id"] == "peak_intensity"
    brains.update(archie=via_analyst("Was it offside?", lambda d: reply("The referee's call stands, friend.")),
                  **{"the-analyst": analyst_calls(("offside_check", {}), say=lambda t: "Checked on the tracking")})
    client, h, *_ = start(engine, brains)
    assert ask(client, h, "Was it offside?")["points_to"] is None
    brains.update(archie=via_analyst("Who ran most?", lambda d: reply("Nobody has run yet, friend.")),
                  **{"the-analyst": analyst_calls(("distance", {}), say=lambda t: "Not started yet")})
    client, h, *_ = start(engine, brains, phase="pre_match")
    assert ask(client, h, "Who ran most?")["points_to"] is None  # Data & Statistics opens at kick-off


def test_table_for() -> None:
    def run(name: str, result: dict | None = None) -> at.ToolRun:
        return at.ToolRun(name, {}, result or {}, "", 0)

    fan = SimpleNamespace(analyzer=False, favourites=("BRA-09",))
    gold = SimpleNamespace(analyzer=True, favourites=("BRA-09",))
    assert ar.table_for([run("match_events"), run("speeds")], fan) == "running_table"
    assert ar.table_for([run("passes")], fan) == "ball_table"
    assert ar.table_for([run("expected_goals")], fan) == "match_table"
    assert ar.table_for([run("expected_goals")], gold) == "xg_cumulative"
    favourite = run("player_stats", {"player": {"player_id": "BRA-09"}})
    pair = run("compare_players", {"a": {"player_id": "BRA-09"}, "b": {"player_id": "CAL-10"}})
    assert ar.table_for([favourite], fan) == "player_focus"
    assert ar.table_for([pair], fan) is None and ar.table_for([pair], gold) == "players_table"
    assert ar.table_for([run("distance", {"error": {"code": "x", "message": "y"}})], gold) is None
    assert ar.table_for([run("offside_check"), run("match_score")], gold) is None


# ------------------------------------------------------------------ AG-5: The Analyst in parallel


def test_the_analyst_starts_in_parallel_and_archie_uses_it(engine) -> None:
    brains = {"archie": via_analyst("Who has run the most?", lambda d: reply(f"My word, {d.split(': ', 1)[1]}!")),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, fake, *_ = start(engine, brains)
    a = ask(client, h, "Chi ha corso di più?")
    runs = [r for r in fake.requests if r["agent_reference"]["name"] == "the-analyst"]
    assert len(runs) == 2  # one run (the tool call, then the data result): Archie's call took it, no second run
    assert "Archie asks: Chi ha corso di più?" in json.dumps(runs[0], ensure_ascii=False)  # the fan's own words
    ins = a["inspector"]
    assert ins["grounding"]["passed"] and "started in parallel" in ins["method"]
    assert [(c["agent"], c["tool"]) for c in ins["tool_calls"]] == [("The Analyst", "distance"),
                                                                    ("Archie", "ask_the_analyst")]
    assert ins["total_tokens"] == 4 * 1040


def test_a_parallel_run_archie_does_not_use_proves_nothing(engine) -> None:
    brains = {"archie": lambda t: reply("Still a proper tussle out there."),
              "the-analyst": analyst_calls(("distance", {}), say=top_runner)}
    client, h, *_ = start(engine, brains)
    a = ask(client, h, "How's it going?")
    ins = a["inspector"]
    assert [s["agent"] for s in ins["agents"]] == ["Archie"] and ins["tool_calls"] == []
    assert a["grounding"] == [] and "did not need it" in ins["method"]
    assert ins["total_tokens"] >= 1040


def test_settle_stops_or_counts_an_unused_run() -> None:
    async def finished() -> ar.Trace:
        trace = ar.Trace(side=ar.Trace())
        trace.side.steps["The Analyst"] = ar._Step("gpt-6-luna", 1000, 40, 5)

        async def done() -> str:
            return "data"
        trace.early = asyncio.ensure_future(done())
        await asyncio.sleep(0)
        await ar.ArchieService._settle(trace)
        return trace

    async def running() -> ar.Trace:
        trace = ar.Trace(side=ar.Trace())
        trace.early = asyncio.ensure_future(asyncio.sleep(10))
        await ar.ArchieService._settle(trace)
        return trace

    t = asyncio.run(finished())
    assert t.unused is not None and t.unused.tokens == 1040 and t.early is None and t.side is None
    assert "its 1040 tokens are in the total" in t.notes[0]
    t = asyncio.run(running())
    assert t.unused is None and t.notes[0].endswith("so it was stopped.")


def test_a_failed_parallel_run_lets_archie_ask_as_usual(engine) -> None:
    runs = {"n": 0}
    inner = analyst_calls(("distance", {}), say=top_runner)

    def analyst(t: Turn):
        if not t.outputs:
            runs["n"] += 1
            if runs["n"] == 1:
                raise RuntimeError("the parallel run fails (fake)")
        return inner(t)

    brains = {"archie": via_analyst("Who has run the most?", lambda d: reply(f"My word, {d.split(': ', 1)[1]}!")),
              "the-analyst": analyst}
    client, h, *_ = start(engine, brains)
    a = ask(client, h, "Who's run the most?")
    assert runs["n"] == 2 and a["inspector"]["grounding"]["passed"]
    assert "started in parallel" not in a["inspector"]["method"]


def test_the_parallel_start_can_be_switched_off(engine, monkeypatch) -> None:
    monkeypatch.setenv("ARCHIE_PREFETCH_ANALYST", "0")
    assert not FoundrySettings.from_env().prefetch_analyst
    monkeypatch.delenv("ARCHIE_PREFETCH_ANALYST")
    assert FoundrySettings.from_env().prefetch_analyst and FoundrySettings.from_env().max_tokens == 30000
    off = FoundrySettings(**{**SETTINGS.__dict__, "prefetch_analyst": False})
    brains = {"archie": lambda t: reply("Still a proper tussle out there."), "the-analyst": lambda t: "unused"}
    client, h, fake, *_ = start(engine, brains, settings=off)
    a = ask(client, h, "How's it going?")
    assert {r["agent_reference"]["name"] for r in fake.requests} == {"archie"}
    assert "parallel" not in a["inspector"]["method"]


def test_mock_when_foundry_is_not_configured(engine) -> None:
    client = TestClient(create_app(engine))
    assert client.get("/api/health").json()["archie"] == "mock"
    token = client.post("/api/auth/login", json={"ticket_code": DEMO, "device_id": "device-archie-test"}).json()["token"]
    r = client.post("/api/archie/ask", headers={"Authorization": f"Bearer {token}"}, json={"message": "Hi"})
    assert r.status_code == 200 and r.json()["answer_id"] == "ANS-0001"


# ------------------------------------------------------------------ pieces


def test_number_reader() -> None:
    def read(t):
        return [(n.text, n.value) for n in grounding.numbers_in(t)]

    assert read("number 8, ≈ 4.1 km, 28.4 km/h") == [("8", 8.0), ("≈ 4.1 km", 4.1), ("28.4 km/h", 28.4)]
    assert read("≈ 1,2 km y 0,6 km") == [("≈ 1,2 km", 1.2), ("0,6 km", 0.6)]
    assert read("E-4, VL4, SYN-001, BRA-09") == []
    assert [n.readings for n in grounding.numbers_in("60,400")] == [((60.4, 3), (60400.0, 0))]
    src = [grounding.Source("distance", {}, "distance.top_runner.km", 4.13),
           grounding.Source("match_context", {}, "Stadium", 60400.0)]
    assert grounding.check("≈ 4.1 km and 4 km, 60,400 fans, 1st half, 90 minutes", src).passed
    bad = grounding.check("4.2 km", src)
    assert not bad.passed and bad.unmatched == ["4.2 km"]



def test_shirt_numbers_only_from_the_line_ups() -> None:
    """'#39' is a shirt: grounded on the line-ups, never on a statistic that happens to round to it."""
    src = [grounding.Source("distance", {}, "distance.top_runner.km", 3.89)] + grounding.lineup_sources(
        {"CAL-04": 4, "CAL-39": 39})
    assert [n.shirt for n in grounding.numbers_in("#4, number 39, el número 4, 4号, 4 km")] == [
        True, True, True, True, False]
    ok = grounding.check("Calderholm's #39, and el número 4 with 3.89 km", src)
    assert ok.passed and [g["tool"] for g in ok.grounding] == ["lineup", "lineup", "distance"]
    assert ok.grounding[0]["source_field"] == "CAL-39.shirt_number"
    assert grounding.check("#11 struck it", src).unmatched == ["11"]  # no player wears 11 here
    assert not grounding.check("number 4 km", [src[0]]).passed  # a shirt never matches 3.89 rounded


def test_match_context(engine, monkeypatch) -> None:
    client, h, _, app, token = start(engine, {}, code=FAN, phase="first_half")
    state = app.state.store.sessions[token]
    mc = ctx_module.build(at.AnalystSession.from_state(engine, token, state), [], "it")
    assert mc.text.startswith("# MATCH CONTEXT") and "LIVE, first half" in mc.text
    assert "- Fan: Fan version; app language Italian" in mc.text
    assert "Games opens at half-time" in mc.text and "Recap opens after the final whistle" in mc.text
    minute = client.get("/api/match/state", headers=h).json()["minute"]
    assert minute in mc.numbers["Phase"]
    # AG-5: a Fan ticket lists what it shows locked; the charts of both versions are not in the list
    locked = next(line for line in mc.text.splitlines() if line.startswith("- Analyst only:"))
    assert "All players table (players_table)" in locked and "Expected goals over time chart (xg_cumulative)" in locked
    assert "(momentum)" not in locked and "(shot_map)" not in locked and mc.numbers["Analyst only"] == []
    # Fix M: the defensive line chart is listed; acceleration load is not in the app, so Archie never sees it
    assert "Defensive line and team length chart (defensive_shape)" in locked and "acceleration" not in mc.text.lower()
    client, h, _, app, token = start(engine, {}, code=ANALYZER, phase="first_half")
    gold = ctx_module.build(at.AnalystSession.from_state(engine, token, app.state.store.sessions[token]), [], "en")
    assert "- Fan: Analyst version;" in gold.text and "Analyst only" not in gold.text


def test_parse_reply() -> None:
    assert ar.parse_reply(reply("Hi", "shot_map", False, "it")) == ar.Reply("Hi", "it", False, "shot_map")
    assert ar.parse_reply("```json\n" + reply("Hi", "not_a_chart") + "\n```").chart is None
    assert ar.parse_reply("Plain words.") == ar.Reply("Plain words.", None, True, None)


def test_agent_definitions() -> None:
    d = foundry.definitions(SETTINGS)
    assert "MATCH CONTEXT (injected" not in d["archie"]["instructions"]
    assert "Brackwater" in d["archie"]["instructions"]  # the illustrative examples stay
    assert "ask_the_analyst" in d["archie"]["instructions"] and [t["name"] for t in d["archie"]["tools"]] == [
        "ask_the_analyst", "ask_kit_man"]  # Kit Man since AG-2
    assert len(d["analyst"]["tools"]) == 12 and d["archie"]["text"]["schema"]["required"] == [
        "text", "language", "in_scope", "chart"]
    assert set(foundry.CHART_IDS) >= {"running_table", "shot_map", "ball_circulation", "defensive_shape"}
    assert "acceleration_load" not in foundry.CHART_IDS and "acceleration_load" not in foundry.ANALYZER_TARGETS
    assert "acceleration_load" not in d["archie"]["text"]["schema"]["properties"]["chart"]["enum"]
    assert "defensive_shape" in d["archie"]["instructions"] and "acceleration_load" not in d["archie"]["instructions"]
