"""Tests of The Analyst's tools (AG-1a): same numbers as the app at the same instant, no future, Fan vs Analyst,
clear errors. Everything runs locally on the small synthetic fixture with frames (and the fake match): no model call."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.agents import analyst_tools as at
from app.agents import grounding as gr
from app.contract import make_fake
from app.contract import tickets as tk
from app.contract.models import Mode, Phase, ToolCall
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import _names, create_app
from app.engine.sources.fake import load_fake
from app.engine.sources.synthetic import load_synthetic

from . import engine_fixture as fx

CODES = make_fake.FAKE_TICKET_CODES
FAN, ANALYZER = CODES["silver"][0], CODES["gold"][0]
NAMES, TEAM_NAMES = _names()
NO_ARGS = {"match_score": {}, "match_events": {}, "team_stats": {}, "shots": {}, "possession": {}, "passes": {},
           "distance": {}, "speeds": {}, "player_stats": {"player": "BRA-09"},
           "compare_players": {"player_a": "BRA-09", "player_b": "CAL #9"}, "expected_goals": {}, "offside_check": {}}


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory) -> Path:
    return fx.build(tmp_path_factory.mktemp("syn_tools"))


@pytest.fixture()
def app_engine(fixture_dir, monkeypatch):
    """The real server on the fixture match with frames, the fake ticket codes, a paused stadium clock."""
    monkeypatch.setenv(tk.REGISTRY_ENV, str(tk.DEFAULT_REGISTRY))
    engine = Engine(load_synthetic("SYN-001", fixture_dir), EngineConfig(autostart=False))
    app = create_app(engine)
    return TestClient(app), app, engine


def login(client: TestClient, code: str, device: str) -> tuple[str, dict]:
    r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": device})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return token, {"Authorization": f"Bearer {token}"}


def run_all(session: at.AnalystSession) -> dict[str, dict]:
    out = {}
    for name, args in NO_ARGS.items():
        run = at.run_tool(session, name, args)
        assert run.ok, (name, run.result)
        ToolCall.model_validate(run.tool_call())
        out[name] = run.result
    return out


def instants(engine: Engine) -> dict[str, float]:
    s = engine.starts
    return {"pre_match": s[Phase.pre_match] + 10.0, "first_half": s[Phase.first_half] + 300.0,
            "half_time": s[Phase.half_time] + 20.0, "second_half": s[Phase.second_half] + 200.0,
            "full_time": s[Phase.full_time] + 1.0}


def by_team(items: list[dict]) -> dict[str, dict]:
    return {x["team_id"]: x for x in items}


def numbers(obj) -> list[float]:
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in numbers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in numbers(v)]
    return [obj] if isinstance(obj, (int, float)) and not isinstance(obj, bool) else []


# ------------------------------------------------------------------ same numbers as the app


@pytest.mark.parametrize("code", [FAN, ANALYZER])
def test_tools_match_the_app_at_every_phase(app_engine, code) -> None:
    client, app, engine = app_engine
    token, h = login(client, code, "device-tools-1")
    fav = "BRA-09"
    prefs = client.get("/api/session", headers=h).json()["preferences"]
    client.put("/api/session/preferences", headers=h, json={**prefs, "favourite_player_ids": [fav]})
    store = app.state.store
    for phase, cursor in instants(engine).items():
        engine.stadium.seek(cursor, False)
        snap = engine.snapshot(token)
        assert snap.phase.value == phase
        engine.core(engine.stats_snapshot(snap))  # the live tick is ready, as in the app after its first poll
        session = at.AnalystSession.from_state(engine, token, store.sessions[token])
        assert session.mode is (Mode.casual if code == FAN else Mode.analyst)
        r = run_all(session)

        state = client.get("/api/match/state", headers=h).json()
        assert r["match_score"]["score"] == state["score"] and r["match_score"]["minute"] == state["minute"]
        assert r["match_score"]["as_of_s"] == state["as_of_s"]
        feed = client.get("/api/match/events", headers=h).json()["events"]
        goals = [e for e in feed if e["type"] == "ShotAtGoal" and e["qualifiers"].get("outcome") == "goal"]
        assert [g["player_id"] for g in r["match_score"]["goals"]] == [g["player_id"] for g in goals]
        assert r["match_events"]["total"] == sum(e["type"] not in ("KickOff", "FinalWhistle") for e in feed)

        if phase == "pre_match":
            assert client.get("/api/stats", headers=h).status_code == 423
            assert r["team_stats"]["teams"] is None and r["distance"]["distance"] is None
            assert r["team_stats"]["unavailable"][0]["reason"] == at.NOT_STARTED
            continue

        ms = client.get("/api/stats", headers=h).json()
        assert ms["mode"] == session.mode.value
        for name in ("team_stats", "shots", "possession", "passes", "distance", "speeds", "player_stats"):
            assert r[name]["as_of_s"] == ms["as_of_s"], name
        teams = r["team_stats"]["teams"]
        sh, counts = by_team(ms["shots"]), by_team(ms["counts"])
        for t in ("BRA", "CAL"):
            assert {k: teams[t][k] for k in ("shots", "on_target", "goals")} == {k: sh[t][k] for k in ("shots", "on_target", "goals")}
            for k in ("penalties", "woodwork", "corners", "fouls", "offsides", "yellow_cards", "red_cards", "tackles",
                      "ball_recoveries"):
                assert teams[t][k] == counts[t][k], k
            assert teams[t]["sprints"] == counts[t]["sprints"] is not None
            assert teams[t]["possession_pct"] == by_team(ms["possession"]["teams"])[t]["value"]
            assert teams[t]["distance_km"] == by_team(ms["distance_covered"]["teams"])[t]["distance"]["km"]
            assert teams[t]["ball_circulation_kmh"] == by_team(ms["ball_circulation"]["teams"])[t]["value"]
            assert r["possession"]["possession"][t] == teams[t]["possession_pct"]
            assert [x["kmh"] for x in r["speeds"]["top_speed"][t]] == [x["kmh"] for x in ms["top_speed"] if x["team_id"] == t]
            assert r["shots"]["shots"]["teams"][t]["shots"] == sh[t]["shots"]
            # E-4: goals vs xG (both versions), counter-attacks, acceleration load
            gx = by_team(ms["goals_vs_xg"])[t]
            assert r["expected_goals"]["xg"][t] == r["shots"]["shots"]["xg"][t] == gx["xg"]
            assert r["expected_goals"]["goals"][t] == gx["goals"]
            assert teams[t]["counter_attacks"] == by_team(ms["counter_attacks"]["teams"])[t]["count"]
            al = by_team(ms["acceleration_load"]["teams"])[t]
            assert r["speeds"]["acceleration_load"][t] == {k: al[k] for k in ("total_kmh", "per_minute_kmh")}
            if code == ANALYZER:
                assert r["passes"]["long_balls"][t] == by_team(ms["passes"])[t]["long_balls"]
                assert teams[t]["ppda"] == by_team(ms["ppda"] or []).get(t, {}).get("value")
        for tool, key, lead in (("distance", "team_top_runners_km", "top_runner"), ("speeds", "top_sprinter", "top_sprinter"),
                                ("passes", "top_passer", "top_passer")):
            got = r[tool]["distance"][key] if tool == "distance" else r[tool][key]
            want = {x["team_id"]: x[lead]["value"] for x in ms["leaders"] if x[lead]}
            assert {t: v["value"] for t, v in (got or {}).items()} == want, key
        eff = r["possession"]["effective_playing_time"]
        assert eff == {k: ms["effective_playing_time"][k] for k in eff}
        tr = r["distance"]["distance"]["top_runner"]
        assert (tr["player_id"], tr["km"]) == (ms["distance_covered"]["top_runner"]["player_id"],
                                               ms["distance_covered"]["top_runner"]["distance"]["km"])
        assert {t: x["kmh"] for t, x in (r["shots"]["shots"]["hardest"] or {}).items()} == \
               {x["team_id"]: x["kmh"] for x in ms["shot_speed"]}
        assert [x["speed_kmh"] for x in r["shots"]["shots"]["list"]] == [x["speed_kmh"] for x in ms["shot_map"]["shots"]]
        focus = ms["player_focus"][0]  # the favourite's Player Focus card, in both versions
        p = r["player_stats"]["player"]
        for k in ("minutes_played", "shots", "goals", "sprints", "passes", "pass_accuracy_pct", "top_speed_kmh",
                  "yellow_cards", "fouls_committed", "fouls_won"):
            assert p[k] == focus[k], k
        assert p["distance"]["km"] == focus["distance"]["km"]
        assert p["acceleration_load_kmh"] == focus["acceleration_load_kmh"] is not None
        assert p["xg"] == focus["xg"]  # null in Fan
        if code == ANALYZER:
            assert [x["xg"] for x in r["shots"]["shots"]["list"]] == [x["xg"] for x in ms["shot_map"]["shots"]]
            assert [x["xg"] for x in r["expected_goals"]["shots"]] == [x["xg"] for x in ms["shot_map"]["shots"]]
            assert r["distance"]["distance"]["players"][0]["km"] == ms["distance_covered"]["players"][0]["distance"]["km"]
            assert r["speeds"]["speed_zones"]["BRA"]["km_over_25"] == by_team(ms["speed_zones"])["BRA"]["km_over_25"]
            other = r["compare_players"]["b"]  # not a favourite: the row of the Analyst players table
            row = next(x for x in ms["players"] if x["player_id"] == other["player_id"])
            assert (other["distance"]["km"], other["top_speed_kmh"], other["sprints"]) == \
                   (row["distance"]["km"], row["top_speed_kmh"], row["sprints"])

        if phase in ("first_half", "second_half"):
            live = client.get("/api/stats/live", headers=h).json()
            assert live["as_of_s"] == r["team_stats"]["as_of_s"]
            for t, x in by_team(live["shots"]).items():
                assert (teams[t]["shots"], teams[t]["on_target"], teams[t]["goals"]) == (x["shots"], x["on_target"], x["goals"])
            for t, x in by_team(live["counts"]).items():
                assert (teams[t]["penalties"], teams[t]["yellow_cards"], teams[t]["red_cards"]) == \
                       (x["penalties"], x["yellow_cards"], x["red_cards"])

        if phase == "full_time":
            rec = client.get("/api/recap", headers=h).json()
            assert rec["final_score"] == r["match_score"]["score"]
            assert {x["team_id"]: x["value"] for x in rec["possession"]} == r["possession"]["possession"]
            f = rec["favourite_players"][0]
            p = r["player_stats"]["player"]
            assert f["player_id"] == fav == p["player_id"]
            assert (p["minutes_played"], p["shots"], p["goals"], p["top_speed_kmh"], p["distance"]["km"]) == \
                   (f["minutes_played"], f["shots"], f["goals"], f["top_speed_kmh"], f["distance"]["km"])
            assert p["passes"] == f["passes"]  # a favourite: shown in the Fan app too


# ------------------------------------------------------------------ Fan never gets Analyst numbers


def test_fan_gets_no_analyzer_numbers(app_engine) -> None:
    client, app, engine = app_engine
    token, h = login(client, FAN, "device-tools-2")  # no favourites
    for phase in ("first_half", "half_time", "full_time"):
        engine.stadium.seek(instants(engine)[phase], False)
        engine.core(engine.stats_snapshot(engine.snapshot(token)))
        session = at.AnalystSession.from_state(engine, token, app.state.store.sessions[token])
        r = run_all(session)
        assert r["distance"]["distance"]["players"] is None
        assert all(t["splits"] is None for t in r["distance"]["distance"]["teams"].values())
        assert r["speeds"]["speed_zones"] is None and all(len(x) == 1 for x in r["speeds"]["top_speed"].values())
        assert r["passes"]["avg_length_m"] is r["passes"]["long_balls"] is None
        assert all(t["ppda"] is None for t in r["team_stats"]["teams"].values())
        assert all("xg" not in x for x in r["shots"]["shots"]["list"]) and r["expected_goals"]["shots"] is None
        assert r["player_stats"]["player"]["xg"] is None
        for name in ("distance", "speeds", "team_stats", "passes", "expected_goals"):
            assert any(u["reason"] == at.ANALYZER_ONLY for u in r[name]["unavailable"]), name
        # a player who is neither a favourite nor a leader: no per-player tracking numbers in Fan
        ms = client.get("/api/stats", headers=h).json()
        leaders = {r["distance"]["distance"]["top_runner"]["player_id"]} | {x["player_id"] for x in ms["top_speed"]} | \
                  {x[k]["player_id"] for x in ms["leaders"] for k in ("top_runner", "top_sprinter", "top_passer") if x[k]}
        other = next(p.player_id for p in engine.snapshot(token).lineup if p.player_id not in leaders and p.starting)
        p = at.run_tool(session, "player_stats", {"player": other}).result["player"]
        assert p["distance"] is p["top_speed_kmh"] is p["sprints"] is p["passes"] is p["pass_accuracy_pct"] is None
        assert p["acceleration_load_kmh"] is None
        assert p["shots"] is not None and p["minutes_played"] > 0  # the timeline's numbers stay
        # every measured (non-integer) number a Fan tool gives is a number of the Fan app at the same instant
        app_numbers = set(numbers(ms))
        if phase == "full_time":
            app_numbers |= set(numbers(client.get("/api/recap", headers=h).json()))
        tool_numbers = {v for name, res in r.items() if name != "offside_check"  # Archie only, not in the app
                        for k, v in at.numeric_fields(res).items()
                        if isinstance(v, float) and k != "as_of_s"}  # the scoreboard's as_of_s is checked above
        assert tool_numbers <= app_numbers, tool_numbers - app_numbers
        # the same tools with an Analyst session give those numbers
        full = run_all(at.AnalystSession(engine, token, session.match_id, Mode.analyst, (), NAMES, TEAM_NAMES))
        assert full["distance"]["distance"]["players"] and full["speeds"]["speed_zones"]
        assert at.run_tool(full_session := at.AnalystSession(engine, token, session.match_id, Mode.analyst, (), NAMES,
                                                             TEAM_NAMES), "player_stats", {"player": other}).result["player"]["distance"]


# ------------------------------------------------------------------ no future


@pytest.mark.parametrize("phase", ["first_half", "half_time", "second_half", "full_time"])
def test_no_future_tool_results_identical_on_a_cut_match(fixture_dir, tmp_path, phase) -> None:
    """At instant t, the tools give the same results on the whole match and on a file cut at t."""
    full = Engine(load_synthetic("SYN-001", fixture_dir), EngineConfig(autostart=False))
    cursor = instants(full)[phase]
    cut_dir = fx.cut(fixture_dir, tmp_path / "cut", fx.KO1 + pd.Timedelta(seconds=cursor))
    cut = Engine(load_synthetic("SYN-001", cut_dir), EngineConfig(autostart=False))
    results = []
    for e in (full, cut):
        e.stadium.seek(cursor, False)
        e.core(e.stats_snapshot(e.snapshot()))
        for mode in Mode:
            results.append(run_all(at.AnalystSession(e, "stadium", "SYN-001", mode, ("BRA-09",), NAMES, TEAM_NAMES)))
    assert results[:2] == results[2:]
    snap = full.snapshot()
    for res in results[0].values():
        assert res["as_of_s"] <= round(snap.as_of_s, 1)
    assert all(ev["minute"] <= results[0]["match_score"]["minute"] for ev in results[0]["match_events"]["events"])


def test_no_future_even_when_an_argument_asks_for_it(fixture_dir) -> None:
    e = Engine(load_synthetic("SYN-001", fixture_dir), EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.first_half] + 150.0, False)  # minute 3: the fixture's shot (200 s) is the future
    s = at.AnalystSession(e, "stadium", "SYN-001", Mode.analyst, (), NAMES, TEAM_NAMES)
    r = at.run_tool(s, "match_events", {"kind": "shot", "until_minute": 90}).result
    assert r["total"] == 0 and "not been played yet" in r["note"]
    assert at.run_tool(s, "shots").result["shots"]["teams"]["BRA"]["shots"] == 0
    bad = at.run_tool(s, "offside_check", {"minute": 80})
    assert not bad.ok and bad.result["error"]["code"] == "future_minute"
    for k in ("as_of_s", "match_id"):
        run = at.run_tool(s, "team_stats", {k: 5000})
        assert run.result["error"]["code"] == "out_of_scope"


# ------------------------------------------------------------------ placeholders, missing data, errors


def test_events_only_source_says_why() -> None:
    e = Engine(load_fake(), EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.half_time] + 10.0, False)
    s = at.AnalystSession(e, "stadium", "SYN-001", Mode.analyst, (), NAMES, TEAM_NAMES)
    r = run_all(s)
    assert r["expected_goals"]["xg"] is None and r["expected_goals"]["goals"] == {"BRA": 1, "CAL": 0}
    assert "needs tracking frames" in {u["field"]: u["reason"] for u in r["expected_goals"]["unavailable"]}["xg"]
    assert r["offside_check"]["verdict"] is r["offside_check"]["margin_m"] is None
    assert r["offside_check"]["unavailable"][0]["reason"]
    assert r["shots"]["shots"]["xg"] is None
    assert r["speeds"]["acceleration_load"] is None and r["team_stats"]["teams"]["BRA"]["counter_attacks"] is None
    assert r["distance"]["distance"] is None and "needs tracking frames" in r["distance"]["unavailable"][0]["reason"]
    assert r["team_stats"]["teams"]["BRA"]["possession_pct"] is None
    reasons = {u["field"]: u["reason"] for u in r["team_stats"]["unavailable"]}
    assert "needs tracking frames" in reasons["teams.*.possession_pct"] and "no pass events" in reasons["teams.*.passes"]
    assert r["team_stats"]["teams"]["BRA"]["goals"] == 1  # the fake story: 1-0 at half-time, real counts stay
    assert r["match_score"]["score"] == {"home": 1, "away": 0}
    assert all(x["xg"] is None for x in r["shots"]["shots"]["list"] + r["expected_goals"]["shots"])  # never the fake
    # feed's own xG


@pytest.mark.parametrize("x, want", [(23.0, "offside"), (21.0, "too close to call with our data"), (17.0, "onside")])
def test_offside_check_gives_the_engine_verdict_next_to_the_referee_call(fixture_dir, x, want) -> None:
    """The engine's offside check (E-4) on an official offside at minute 1, by event id or by minute; the referee's
    call is always in the result, and an "onside" never contradicts it."""
    from .test_engine_metrics import offside_scene

    md, ps, attacker = offside_scene(load_synthetic("SYN-001", fixture_dir), x)
    e = Engine(md, EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.half_time] + 1.0, False)
    for mode in Mode:  # Archie's tool in both versions: the app never shows it
        s = at.AnalystSession(e, "stadium", "SYN-001", mode, (), NAMES, TEAM_NAMES)
        for args in ({"event_id": "FX-OFF"}, {"minute": 1}):
            run = at.run_tool(s, "offside_check", args)
            r = run.result
            assert run.ok and r["verdict"] == want, (args, r)
            assert r["moment"] == {"event_id": "FX-OFF", "case": "offside", "minute": 1, "attacker_id": attacker,
                                   "attacker": NAMES[attacker], "pass_event_id": ps.event_id}
            assert r["margin_m"] == pytest.approx(x - 20.0, abs=0.15) and r["band_m"] == 2.0
            assert r["distance_m"] == abs(r["margin_m"])
            assert r["official"] == at.OFFICIAL["offside"] and r["unavailable"] == []
            assert (r.get("note") == at.NOT_CONFIRMED) is (want == "onside")
            assert run.summary.startswith(f"offside 1': {want} (")
            assert at.numeric_fields(r)["margin_m"] == r["margin_m"]  # a grounded number
            said = f"{'Onside' if want == 'onside' else 'Beyond the line'} by {abs(x - 20.0):.1f} m."
            assert gr.check(said, gr.tool_sources("offside_check", args, r)).passed, said
    none = at.run_tool(s, "offside_check", {"minute": 2}).result
    assert none["verdict"] is None and none["unavailable"][0]["reason"] == "no offside call or goal at minute 2"


def test_offside_check_after_a_throw_in_is_onside_by_law_11(fixture_dir) -> None:
    """No offside offence directly from a throw-in, a corner or a goal kick: "onside" with no margin (the 41' goal of
    SYN-001 comes from a corner)."""
    from app.engine.sources.base import Event

    from .test_engine_metrics import offside_scene

    throw = Event(event_id="FX-THROW", type="ThrowIn", period=1, t=18.0, team_side="home", player_id="BRA-02")
    md, _, _ = offside_scene(load_synthetic("SYN-001", fixture_dir), 23.0, extra=(throw,))
    e = Engine(md, EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.half_time] + 1.0, False)
    run = at.run_tool(at.AnalystSession(e, "stadium", "SYN-001", Mode.analyst, (), NAMES, TEAM_NAMES),
                      "offside_check", {"event_id": "FX-OFF"})
    r = run.result
    assert run.ok and r["verdict"] == "onside" and r["margin_m"] is None and "throw-in" in r["reason"]
    assert r["note"] == at.NOT_CONFIRMED and "throw-in" in run.summary


def test_wrong_arguments_give_clear_errors_not_exceptions() -> None:
    e = Engine(load_fake(), EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.full_time] + 1.0, False)
    s = at.AnalystSession(e, "stadium", "SYN-001", Mode.casual, (), NAMES, TEAM_NAMES)
    cases = [("player_stats", {"player": "Lionel Nobody"}, "player_not_found"),
             ("player_stats", {}, "bad_argument"),
             ("player_stats", {"player": "9"}, "ambiguous_player"),  # BRA #9 and CAL #9
             ("compare_players", {"player_a": "BRA-09", "player_b": "bra 9"}, "same_player"),
             ("shots", {"team": "Real Madrid"}, "team_not_found"),
             ("team_stats", {"match_id": "SYN-002"}, "out_of_scope"),
             ("match_events", {"kind": "goals"}, "bad_argument"),
             ("match_events", {"last": "ten"}, "bad_argument"),
             ("distance", {"top": 500}, "bad_argument"),
             ("offside_check", {"event_id": "nope"}, "event_not_found"),
             ("league_table", {}, "unknown_tool")]
    for name, args, code in cases:
        run = at.run_tool(s, name, args)
        assert not run.ok and run.result["error"]["code"] == code, (name, args, run.result)
        assert run.summary.startswith("error:") and run.result["error"]["message"]
        ToolCall.model_validate(run.tool_call())


def test_players_found_by_id_number_or_name() -> None:
    e = Engine(load_fake(), EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.full_time] + 1.0, False)
    s = at.AnalystSession(e, "stadium", "SYN-001", Mode.analyst, (), NAMES, TEAM_NAMES)
    name = NAMES["BRA-09"]
    for ref in ("BRA-09", "bra-09", "BRA #9", "Brackwater 9", name, name.split()[-1].upper()):
        r = at.run_tool(s, "player_stats", {"player": ref})
        assert r.ok and r.result["player"]["player_id"] == "BRA-09", ref
    assert r.result["player"]["goals"] >= 1 and "Ulvestad" in name


def test_definitions_ready_for_function_calling() -> None:
    defs = at.function_tools()
    assert [d["name"] for d in defs] == list(NO_ARGS)
    for d in defs:
        assert d["type"] == "function" and len(d["description"]) < 160
        assert d["parameters"]["type"] == "object" and d["parameters"]["additionalProperties"] is False
        assert "match_id" not in d["parameters"]["properties"]


def test_numeric_fields_are_stable_paths() -> None:
    e = Engine(load_fake(), EngineConfig(autostart=False))
    e.stadium.seek(e.starts[Phase.full_time] + 1.0, False)
    s = at.AnalystSession(e, "stadium", "SYN-001", Mode.analyst, (), NAMES, TEAM_NAMES)
    f = at.numeric_fields(at.run_tool(s, "team_stats").result)
    assert f["teams.BRA.goals"] == 2 and f["teams.CAL.goals"] == 1 and "as_of_s" in f
    ev = at.numeric_fields(at.run_tool(s, "match_score").result)
    assert ev["score.home"] == 2 and "goals[0].minute" in ev
