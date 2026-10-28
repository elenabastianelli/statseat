"""Tests of the API contract: fake data vs models, consistency with synthetic/, and the mock server."""

from __future__ import annotations

import fnmatch
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contract import CONTRACT_VERSION, make_fake
from app.contract import mock as mock_module
from app.contract import tickets as tk
from app.contract.export_openapi import OUT as OPENAPI, openapi_text
from app.contract.mock import PHASE_ELAPSED, create_app
from app.contract.models import DATA_STATS_TABLES, Reliability
from app.contract.registry import FAKE_MODELS

FAKE = make_fake.FAKE
ROOT = make_fake.ROOT
UNIVERSE = json.loads((ROOT / "synthetic/universe/universe.json").read_text(encoding="utf-8"))
MATCH = json.loads((ROOT / "synthetic/SYN-001/match_info.json").read_text(encoding="utf-8"))
PLAYERS = {p["id"]: p for p in UNIVERSE["players"] if p["club_id"] in ("BRA", "CAL")}
MATCH_PLAYERS = {x["player_id"] for t in MATCH["teams"] for x in t["lineup"]}
PHASES = ["pre_match", "first_half", "half_time", "second_half", "full_time"]
CODES = make_fake.FAKE_TICKET_CODES  # public fake demo codes (also listed in app/contract/README.md)
SILVER, GOLD, DEMO = (CODES[t] for t in ("silver", "gold", "demo"))


def load(name: str) -> dict:
    return json.loads((FAKE / name).read_text(encoding="utf-8"))


def served_files() -> list[Path]:
    return sorted(p for p in FAKE.glob("*.json"))


# ------------------------------------------------------------------ fake data vs models


@pytest.mark.parametrize("path", served_files(), ids=lambda p: p.name)
def test_fake_file_matches_model(path: Path) -> None:
    matches = [m for pat, m in FAKE_MODELS.items() if fnmatch.fnmatch(path.name, pat)]
    assert len(matches) == 1, f"{path.name} must map to exactly one model in registry.py"
    matches[0].model_validate_json(path.read_text(encoding="utf-8"))


def test_fake_files_up_to_date() -> None:
    built = make_fake.build()
    on_disk = {p.relative_to(FAKE).as_posix(): json.loads(p.read_text(encoding="utf-8")) for p in FAKE.rglob("*.json")}
    assert set(built) == set(on_disk), "run: python -m app.contract.make_fake"
    for name, data in built.items():
        assert json.loads(json.dumps(data, ensure_ascii=False)) == on_disk[name], f"{name} is stale: run make_fake"


def test_openapi_up_to_date_and_versioned() -> None:
    assert OPENAPI.read_text(encoding="utf-8") == openapi_text(), "run: python -m app.contract.export_openapi"
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert spec["info"]["version"] == CONTRACT_VERSION
    for path, ops in spec["paths"].items():
        assert "match_id" not in path
        for op in ops.values():
            assert all(p["name"] != "match_id" for p in op.get("parameters", [])), "match_id never comes from the client"


def test_phase_table_shared_with_builder() -> None:
    for ph, (_, _, el, _) in make_fake.PHASES.items():
        assert PHASE_ELAPSED[ph] == (el if ph != "pre_match" else -1.0)


# ------------------------------------------------------------------ consistency with synthetic/ and inside the fake data


def all_player_ids(obj) -> set[str]:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("player_id", "secondary_player_id", "from_player_id", "to_player_id") and v:
                out.add(v)
            elif k in ("player_ids", "favourite_player_ids"):
                out.update(v)
            else:
                out |= all_player_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= all_player_ids(v)
    return out


def test_player_ids_exist() -> None:
    for path in served_files():
        ids = all_player_ids(json.loads(path.read_text(encoding="utf-8")))
        allowed = set(PLAYERS) if path.name.startswith(("players", "guess", "ttt")) else MATCH_PLAYERS
        assert ids <= allowed, f"{path.name}: unknown ids {ids - allowed}"


def test_names_numbers_positions_from_synthetic() -> None:
    for t in load("match.json")["teams"]:
        lineup = {x["player_id"]: x for mt in MATCH["teams"] if mt["team_id"] == t["team_id"] for x in mt["lineup"]}
        assert {p["player_id"] for p in t["lineup"]} == set(lineup)
        for p in t["lineup"]:
            u = PLAYERS[p["player_id"]]
            assert p["name"] == f"{u['first_name']} {u['last_name']}"
            assert p["shirt_number"] == lineup[p["player_id"]]["shirt_number"] == u["shirt_number"]
            assert p["position"] == lineup[p["player_id"]]["playing_position"]
    for p in load("players.json")["players"]:
        u = PLAYERS[p["player_id"]]
        assert (p["name"], p["team_id"], p["shirt_number"]) == (f"{u['first_name']} {u['last_name']}", u["club_id"], u["shirt_number"])


def goals(events: list[dict], until: float) -> dict:
    el = lambda e: e["match_time_s"] / 60 + (47.0 if e["period"] == 2 else 0.0)  # noqa: E731
    g = [e for e in events if e["type"] == "ShotAtGoal" and e["qualifiers"]["outcome"] == "goal" and el(e) <= until]
    return {"home": sum(e["team_id"] == "BRA" for e in g), "away": sum(e["team_id"] == "CAL" for e in g)}


def test_score_matches_goal_events() -> None:
    events = load("events.json")["events"]
    assert all(e["type"] != "Delete" for e in events)
    for ph in PHASES:
        st = load(f"match_state_{ph}.json")
        assert st["score"] == goals(events, PHASE_ELAPSED[ph]), ph
    assert load("stats_casual_first_half.json")["score"] == load("match_state_half_time.json")["score"]
    assert load("stats_analyst_full_match.json")["score"] == load("match_state_full_time.json")["score"]
    assert load("recap_casual.json")["final_score"] == load("match_state_full_time.json")["score"]
    assert load("stats_live_second_half.json")["score"] == load("match_state_second_half.json")["score"]


def test_stats_match_events() -> None:
    events = load("events.json")["events"]
    for scope, until in (("first_half", 47.0), ("full_match", 96.0)):
        st = load(f"stats_analyst_{scope}.json")
        sel = [e for e in events if e["match_time_s"] / 60 + (47.0 if e["period"] == 2 else 0.0) <= until]
        for t in st["shots"]:
            shots = [e for e in sel if e["type"] == "ShotAtGoal" and e["team_id"] == t["team_id"]]
            assert t["shots"] == len(shots)
            assert t["goals"] == sum(e["qualifiers"]["outcome"] == "goal" for e in shots)
        assert len(st["shot_map"]["shots"]) == sum(e["type"] == "ShotAtGoal" for e in sel)
        for c in st["counts"]:
            assert c["yellow_cards"] == sum(e["type"] == "Caution" and e["team_id"] == c["team_id"] for e in sel)
        assert sum(t["value"] for t in st["possession"]["teams"]) == pytest.approx(100)


def test_casual_hides_analyst_fields() -> None:
    st = load("stats_casual_first_half.json")
    for f in ("speed_zones", "xg", "zone_map", "defensive_shape", "ppda", "players"):
        assert st[f] is None
    assert st["distance_covered"]["players"] is None
    assert all(s["xg"] is None for s in st["shot_map"]["shots"])
    assert st["ball_circulation"]["series"] is None and st["acceleration_load"]["series"] is None
    assert st["peak_intensity"]["players"] is None and st["counter_attacks"]["items"] is None
    assert all(f[k] is None for f in st["player_focus"] for k in ("xg", "speed_zones", "zone_map", "acceleration_load_series"))
    assert all(len([t for t in st["top_speed"] if t["team_id"] == team]) == 1 for team in ("BRA", "CAL"))


def test_charts_follow_the_metric_decisions() -> None:
    """v0.3.0 (E-3): Fan has momentum, shot map and running intensity; every other chart is Analyst only and shown
    blurred in Fan. Distance, possession and pass lengths are not charts any more."""
    casual, analyst = load("stats_casual_full_match.json"), load("stats_analyst_full_match.json")
    fan = [c["chart_id"] for c in casual["charts"]]
    assert fan == ["momentum", "shot_map", "peak_intensity"]
    every = [c["chart_id"] for c in analyst["charts"]]
    assert sorted(fan + [c["chart_id"] for c in casual["locked_charts"]]) == sorted(every)
    assert analyst["locked_charts"] == []
    assert not {"distance_covered", "possession", "pass_lengths", "pass_network", "crowd_noise"} & set(every)


def test_reliability_symbol() -> None:
    assert [Reliability.of(v).level.value for v in (100, 95, 94.9, 85, 84.9, 0)] == \
        ["high", "high", "medium", "medium", "low", "low"]
    st = load("stats_analyst_full_match.json")
    for d in [p["distance"] for p in st["distance_covered"]["players"]] + [t["distance"] for t in st["distance_covered"]["teams"]]:
        assert d["reliability"]["measured_pct"] == pytest.approx(100 - d["unmeasured_pct"], abs=0.11)


def test_new_metrics_agree_with_the_events() -> None:
    """Woodwork, penalties, goals vs xG and the hardest shot (headers excluded) come from the same events."""
    events = load("events.json")["events"]
    shots = [e for e in events if e["type"] == "ShotAtGoal"]
    st = load("stats_analyst_full_match.json")
    for c in st["counts"]:
        own = [e for e in shots if e["team_id"] == c["team_id"]]
        assert c["woodwork"] == sum(e["qualifiers"]["outcome"] == "woodwork" for e in own)
        assert c["penalties"] == sum(bool(e["qualifiers"].get("penalty")) for e in own)
    goals = {t["team_id"]: t["goals"] for t in st["shots"]}
    assert {g["team_id"]: g["goals"] for g in st["goals_vs_xg"]} == goals
    assert sorted(g["minute"] for g in st["goal_marks"]) == sorted(e["minute"] for e in shots if e["qualifiers"]["outcome"] == "goal")
    headers = {e["event_id"] for e in shots if e["qualifiers"].get("header")}
    assert headers and not headers & {s["event_id"] for s in st["shot_speed"]}
    assert all(s["speed_kmh"] is None for s in st["shot_map"]["shots"] if s["is_header"])
    assert sum(s["is_penalty"] for s in st["shot_map"]["shots"]) == sum(c["penalties"] for c in st["counts"])
    assert st["effective_playing_time"]["reference_world_cup_2022_min"] == 59.0
    ids = {r["player_id"] for r in st["players"]}
    for lead in st["leaders"]:
        assert len(lead["most_touches"]) == 3 and {p["player_id"] for p in lead["most_touches"]} <= ids


def test_highlights_reference_events() -> None:
    ev = {e["event_id"] for e in load("events.json")["events"]}
    for h in load("highlights.json")["highlights"]:
        assert set(h["event_ids"]) <= ev
        assert "replay" not in h  # the 2D replay is a roadmap item (25 October)


# ------------------------------------------------------------------ games


def test_picker_has_only_name_team_number() -> None:
    players = load("players.json")["players"]
    assert len(players) == 50
    assert all(set(p) == {"player_id", "name", "team_id", "shirt_number"} for p in players)


def test_tiki_taka_toe_grid_is_real_and_solvable() -> None:
    g = load("ttt_game.json")
    sol = load("server/answers.json")["tiki_taka_toe"][g["game_session_id"]]

    def meets(p: dict, c: dict) -> bool:
        if c["kind"] == "club":
            return any(sp["club_id"] == c["id"] for sp in p["career"])
        return p["nationality"] == c["id"]

    for c in g["rows"] + g["cols"]:
        assert c["kind"] in ("club", "nationality")
        assert c["id"] not in ("BRA", "CAL")
    for r, row in enumerate(g["rows"]):
        for k, col in enumerate(g["cols"]):
            expected = sorted(pid for pid, p in PLAYERS.items() if meets(p, row) and meets(p, col))
            assert sol[f"{r},{k}"] == expected
            assert len(expected) >= 2


def test_guess_the_player_rounds() -> None:
    answers = load("server/answers.json")["guess_the_player"]
    car = load("guess_career.json")
    p = PLAYERS[answers[car["round_id"]]["correct"]]
    clubs = {c["id"]: c["name"] for c in UNIVERSE["clubs"]}
    assert [c["club_name"] for c in car["career"]] == [clubs[s["club_id"]] for s in p["career"] if s["club_id"] not in ("BRA", "CAL")]
    tr = load("guess_transfers.json")
    pid = answers[tr["round_id"]]["correct"]
    assert len(tr["transfers"]) == 3
    names = {c["club_name"] for c in tr["transfers"]}
    holders = [q for q, pl in PLAYERS.items() if names <= {clubs[s["club_id"]] for s in pl["career"]}]
    assert holders == [pid], "the 3 clubs must identify one player"
    fees = {(clubs[s["club_id"]], s["from_season"]): s["fee_eur"] for s in PLAYERS[pid]["career"]}
    assert all(fees[(c["club_name"], c["season"])] == c["fee_eur"] for c in tr["transfers"])
    for rnd in (car, tr):
        ids = [o["player_id"] for o in rnd["options"]]
        assert answers[rnd["round_id"]]["correct"] in ids and len(set(ids)) == 4


def test_quiz_answer_comes_from_stats() -> None:
    q = load("quiz_round.json")
    st = load(f"stats_analyst_{q['scope']}.json")
    top = st["distance_covered"]["top_runner"]["player_id"]
    correct = load("server/answers.json")["quiz"][q["round_id"]]["correct"]
    assert next(o for o in q["options"] if o["option_id"] == correct)["player_id"] == top


def test_archie_numbers_are_grounded() -> None:
    a = load("archie_answer.json")
    st = load("stats_analyst_first_half.json")
    for g in a["grounding"]:
        assert g["text"] in a["text"]
        assert g["value"] == st["distance_covered"]["top_runner"]["distance"]["km"]
    assert a["points_to"]["chart_id"] in {c["chart_id"] for c in st["charts"]} | set(DATA_STATS_TABLES)


# ------------------------------------------------------------------ mock server


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def login(client: TestClient, code: str, device: str = "device-0001") -> dict:
    r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": device})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_every_endpoint_returns_valid_data(client: TestClient) -> None:
    assert client.get("/api/health").json()["contract_version"] == CONTRACT_VERSION
    h = login(client, DEMO[0])
    for path in ("/api/session", "/api/match", "/api/ticket"):
        assert client.get(path, headers=h).status_code == 200
    assert client.put("/api/session/preferences", headers=h,
                      json={"mode": "analyst", "favourite_player_ids": ["CAL-09"], "language": "it"}).status_code == 200
    for ph in PHASES:
        st = client.post("/api/demo/phase", headers=h, json={"phase": ph})
        assert st.status_code == 200 and st.json()["phase"] == ph
        for path in ("/api/match/state", "/api/match/events", "/api/highlights", "/api/games", "/api/games/players"):
            assert client.get(path, headers=h).status_code == 200, (ph, path)
        assert client.post("/api/archie/ask", headers=h, json={"message": "Who ran the most?"}).status_code == 200
        live = client.get("/api/stats/live", headers=h)
        assert live.status_code == (200 if ph in ("first_half", "second_half") else 423)
        for mode in ("casual", "analyst"):
            r = client.get(f"/api/stats?mode={mode}", headers=h)
            assert r.status_code == (423 if ph == "pre_match" else 200), (ph, mode)
            if r.status_code == 200:
                assert r.json()["scope"] == {"first_half": "live", "half_time": "first_half", "second_half": "live",
                                             "full_time": "full_match"}[ph]
            r = client.get(f"/api/recap?mode={mode}", headers=h)
            assert r.status_code == (200 if ph == "full_time" else 423)
        games = client.get("/api/games", headers=h).json()
        assert games["prize"]["sponsor_name"] == "Quayspoke Cycles"
        # v0.2.3: games only at half-time, never again after it
        assert all(g["available"] is (ph == "half_time") for g in games["games"]), ph
        assert all(g["unlocks_at_phase"] == ("half_time" if ph in ("pre_match", "first_half") else None)
                   for g in games["games"]), ph
        if ph == "half_time":
            g = client.post("/api/games/tiki-taka-toe", headers=h).json()
            sol = mock_module.ttt.grid("BRA", "CAL")[2]  # the grid of the match (app/games/ttt.py)
            mv = client.post(f"/api/games/tiki-taka-toe/{g['game_session_id']}/moves", headers=h,
                             json={"row": 1, "col": 1, "player_id": sol["1,1"][0]})
            assert mv.status_code == 200 and mv.json()["correct"]
            assert client.get(f"/api/games/tiki-taka-toe/{g['game_session_id']}", headers=h).status_code == 200
            for version in ("career", "transfers"):  # the real game always answers 'career' (v0.3.1)
                rnd = client.post(f"/api/games/guess-the-player?version={version}", headers=h).json()
                assert rnd["version"] == "career"
                ans = client.post(f"/api/games/guess-the-player/{rnd['round_id']}/answer", headers=h,
                                  json={"option_id": rnd["options"][0]["player_id"]})
                assert ans.status_code == 200
            q = client.post("/api/games/quiz", headers=h).json()
            correct = load("server/answers.json")["quiz"][q["round_id"]]["correct"]
            ans = client.post(f"/api/games/quiz/{q['round_id']}/answer", headers=h, json={"option_id": correct}).json()
            assert ans["correct"] and ans["points_awarded"] > 100 - 1
            assert client.get("/api/leaderboard", headers=h).status_code == 200
        else:
            assert client.post("/api/games/quiz", headers=h).status_code == 423


def test_inspector_only_for_demo(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_PHASE", "full_time")
    for code, visible in ((DEMO[1], True), (GOLD[0], False), (SILVER[0], False)):
        h = login(client, code)
        assert client.get("/api/session", headers=h).json()["inspector_enabled"] is visible
        a = client.post("/api/archie/ask", headers=h, json={"message": "Who ran the most?"}).json()
        assert (a["inspector"] is not None) is visible
        assert (client.get("/api/stats", headers=h).json()["inspector"] is not None) is visible
        assert (client.get("/api/recap", headers=h).json()["inspector"] is not None) is visible
        hls = client.get("/api/highlights", headers=h).json()["highlights"]
        assert all((x["inspector"] is not None) is visible for x in hls)


def test_demo_panel_only_for_demo(client: TestClient) -> None:
    for code in (GOLD[1], SILVER[1]):
        h = login(client, code)
        assert client.post("/api/demo/phase", headers=h, json={"phase": "full_time"}).status_code == 403


def test_one_device_per_ticket_and_bad_tickets(client: TestClient) -> None:
    for code in (SILVER[2], GOLD[2]):
        login(client, code, "device-aaaa")
        login(client, code, "device-aaaa")
        r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": "device-bbbb"})
        assert r.status_code == 409 and r.json()["code"] == "ticket_in_use"
    bad = ("NOPE-1", "DEMO-0001", "FAN-0001", "GNT-SYN001-G-AAAA-AAAA", SILVER[0].replace("-S-", "-G-"),
           "GNT-SYN001-J-M2M3-Z5E3")  # a fake judge code of v0.2.1: the tier is gone (L-1)
    for code in bad:
        r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": "device-cccc"})
        assert r.status_code == 401 and r.json()["code"] == "invalid_ticket", code
    assert client.get("/api/match/state").status_code == 401


def test_demo_works_on_several_devices(client: TestClient) -> None:
    for code in (DEMO[2],):
        tokens = {login(client, code, f"device-{i:04d}")["Authorization"] for i in range(3)}
        assert len(tokens) == 3
        for h in tokens:
            s = client.get("/api/session", headers={"Authorization": h}).json()
            assert s["multi_device"] is True


def test_locked_section_error_says_when(client: TestClient) -> None:
    h = login(client, DEMO[0])
    client.post("/api/demo/phase", headers=h, json={"phase": "pre_match"})
    r = client.get("/api/stats", headers=h).json()
    assert r == {"code": "section_locked", "message": r["message"], "section": "data_stats", "unlocks_at_phase": "first_half"}
    a = client.post("/api/archie/ask", headers=h, json={"message": "Show me a chart"}).json()
    assert a["points_to"]["locked"] and a["points_to"]["unlocks_at_phase"] == "first_half"


def test_favourites_must_be_in_the_match(client: TestClient) -> None:
    h = login(client, DEMO[1])
    r = client.put("/api/session/preferences", headers=h, json={"favourite_player_ids": ["BRA-07"]})
    assert r.status_code == 422  # BRA-07 is in the squad but not in the matchday squad


def test_three_favourites_locked_after_confirmation(client: TestClient) -> None:
    """v0.2.6 (L-2g): at most 3 favourites, no repeats; confirming needs exactly 3, then they cannot change."""
    h = login(client, DEMO[1])
    assert client.get("/api/session", headers=h).json()["preferences"]["favourite_player_ids"] == []  # the fan chooses
    put = lambda favs, locked=False: client.put("/api/session/preferences", headers=h, json={  # noqa: E731
        "mode": "analyst", "favourite_player_ids": favs, "favourites_locked": locked, "language": "en"})
    assert put(["BRA-09", "BRA-06", "CAL-09", "CAL-01"]).status_code == 422
    assert put(["BRA-09", "BRA-09"]).json()["code"] == "duplicate_player"
    assert put(["BRA-09", "BRA-06"], locked=True).json()["code"] == "favourites_incomplete"
    assert put(["BRA-09", "BRA-06", "CAL-09"]).status_code == 200  # still free to change
    assert put(["BRA-09", "BRA-06", "CAL-09"], locked=True).json()["favourites_locked"] is True
    for favs, locked in ((["BRA-09", "BRA-06", "CAL-01"], True), (["BRA-09", "BRA-06", "CAL-09"], False)):
        r = put(favs, locked)
        assert r.status_code == 409 and r.json()["code"] == "favourites_locked"
    r = client.put("/api/session/preferences", headers=h, json={  # mode and language still change
        "mode": "casual", "favourite_player_ids": ["BRA-09", "BRA-06", "CAL-09"], "favourites_locked": True, "language": "it"})
    assert r.status_code == 200
    s = client.get("/api/session", headers=h).json()["preferences"]
    assert s["favourite_player_ids"] == ["BRA-09", "BRA-06", "CAL-09"] and s["favourites_locked"] and s["language"] == "it"


def test_player_focus_serves_the_favourites_only(client: TestClient) -> None:
    """v0.3.0: the Player Focus cards of Data & Statistics and Recap are the session's favourites, in their order."""
    h = login(client, DEMO[1])
    client.post("/api/demo/phase", headers=h, json={"phase": "full_time"})
    assert client.get("/api/stats?mode=analyst", headers=h).json()["player_focus"] == []  # no favourites yet
    client.put("/api/session/preferences", headers=h, json={
        "mode": "analyst", "favourite_player_ids": ["CAL-09", "BRA-09", "BRA-06"], "favourites_locked": True, "language": "en"})
    for path, key in (("/api/stats", "player_focus"), ("/api/recap", "favourite_players")):
        for mode in ("casual", "analyst"):
            cards = client.get(f"{path}?mode={mode}", headers=h).json()[key]
            assert [c["player_id"] for c in cards] == ["CAL-09", "BRA-09", "BRA-06"], (path, mode)
            assert all((c["xg"] is None) is (mode == "casual") for c in cards)


def test_recap_pdf_after_the_final_whistle(client: TestClient) -> None:
    """L-2g: the server builds the final report in PDF (contents in test_report.py); locked (423) before full time."""
    h = login(client, DEMO[1])
    client.post("/api/demo/phase", headers=h, json={"phase": "second_half"})
    r = client.get("/api/recap/pdf", headers=h)
    assert r.status_code == 423 and r.json()["unlocks_at_phase"] == "full_time"
    client.post("/api/demo/phase", headers=h, json={"phase": "full_time"})
    for mode in ("casual", "analyst"):
        r = client.get(f"/api/recap/pdf?mode={mode}", headers=h)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF") and f"statseat-report-SYN-001-{mode}.pdf" in r.headers["content-disposition"]
    h = login(client, SILVER[1], "device-0042")
    assert client.get("/api/recap/pdf?mode=analyst", headers=h).status_code in (403, 423)


# ------------------------------------------------------------------ tickets (v0.2.0)


def test_ticket_codes_format_and_tier_letter() -> None:
    all_codes = [c for codes in CODES.values() for c in codes]
    assert len(set(all_codes)) == len(all_codes) == 9  # 3 silver, 3 gold, 3 demo
    for tier, codes in CODES.items():
        for c in codes:
            assert tk.CODE_RE.match(c), c
            assert c.startswith(f"GNT-SYN001-{tk.TIER_LETTER[tk.TicketTier(tier)]}-")
            assert set(c.replace("-", "")[10:]) <= set(tk.CODE_ALPHABET)
            assert tk.parse_code(c) == ("SYN001", tk.TicketTier(tier))
    assert tk.parse_code("  gnt-syn001-g-" + GOLD[0][-9:].lower()) == ("SYN001", tk.TicketTier.gold)
    assert tk.parse_code("DEMO-0001") is None
    assert tk.parse_code("GNT-SYN001-J-M2M3-Z5E3") is None  # no judge tier since v0.2.2


def test_server_stores_only_hashes() -> None:
    raw = (FAKE / "server/tickets.json").read_text(encoding="utf-8")
    reg = tk.Registry.model_validate_json(raw)
    assert reg.salt_label == tk.DEV_SALT_LABEL
    assert all("code" not in t for t in json.loads(raw)["tickets"])
    expected = {tk.hash_code(c, tk.DEV_SALT): tier for tier, codes in CODES.items() for c in codes}
    assert {t.code_hash: t.tier.value for t in reg.tickets} == expected
    everything = "".join(p.read_text(encoding="utf-8") for p in FAKE.rglob("*.json")) + OPENAPI.read_text(encoding="utf-8")
    for codes in CODES.values():
        for c in codes:
            assert c not in everything and c[-9:] not in everything, "plain codes must never be stored or served"


def test_manifest_rejects_wrong_tier_letter() -> None:
    m = make_fake.ticket_manifest()
    m["tickets"][0]["tier"] = "gold"  # a silver code declared as gold
    with pytest.raises(ValueError):
        tk.registry_from_manifest(m, key=tk.DEV_SALT)


def test_other_salt_rejects_dev_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tk.SALT_ENV, "another-deployment-salt")
    c = TestClient(create_app())
    r = c.post("/api/auth/login", json={"ticket_code": GOLD[0], "device_id": "device-0001"})
    assert r.status_code == 401
    assert tk.registry_from_manifest(make_fake.ticket_manifest()).salt_label == "deployment"


def test_tier_rules_in_session(client: TestClient) -> None:
    expected = {"silver": (["casual"], "casual", False, False, False), "gold": (["analyst", "casual"], "analyst", False, False, False),
                "demo": (["analyst", "casual"], "analyst", True, True, True)}
    for tier, (modes, default, insp, panel, multi) in expected.items():
        s = client.post("/api/auth/login", json={"ticket_code": CODES[tier][0], "device_id": "device-tier"}).json()["session"]
        assert s["tier"] == tier
        assert (s["allowed_modes"], s["preferences"]["mode"], s["inspector_enabled"], s["demo_panel_enabled"],
                s["multi_device"]) == (modes, default, insp, panel, multi)


def test_silver_cannot_use_analyst(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_PHASE", "full_time")
    h = login(client, SILVER[0])
    r = client.put("/api/session/preferences", headers=h, json={"mode": "analyst"})
    assert r.status_code == 403 and r.json()["code"] == "mode_not_allowed"
    assert client.get("/api/session", headers=h).json()["preferences"]["mode"] == "casual"
    for path in ("/api/stats?mode=analyst", "/api/recap?mode=analyst"):
        assert client.get(path, headers=h).status_code == 403
    st = client.get("/api/stats", headers=h).json()
    assert st["mode"] == "casual" and st["xg"] is None
    assert client.get("/api/recap", headers=h).json()["mode"] == "casual"
    assert client.put("/api/session/preferences", headers=h, json={"mode": "casual"}).status_code == 200


def test_gold_switches_between_analyst_and_casual(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_PHASE", "half_time")
    h = login(client, GOLD[0])
    assert client.get("/api/stats", headers=h).json()["mode"] == "analyst"
    assert client.put("/api/session/preferences", headers=h, json={"mode": "casual"}).status_code == 200
    assert client.get("/api/stats", headers=h).json()["mode"] == "casual"
    assert client.get("/api/stats?mode=analyst", headers=h).json()["inspector"] is None


def test_my_ticket(client: TestClient) -> None:
    for tier, codes in CODES.items():
        h = login(client, codes[0], "device-ticket")
        t = client.get("/api/ticket", headers=h)
        assert t.status_code == 200
        t = t.json()
        assert t["tier"] == tier and t["match"]["match_id"] == "SYN-001" and t["valid_now"]
        assert t["features"]["allowed_modes"] == client.get("/api/session", headers=h).json()["allowed_modes"]
        assert (t["seat"] is None) is (tier == "demo")
        if t["seat"]:
            stadium = next(s for s in UNIVERSE["stadiums"] if s["id"] == MATCH["environment"]["stadium_id"])
            stand = next(s for s in stadium["stands"] if s["id"] == t["seat"]["stand_id"])
            assert t["seat"]["stand_name"] == stand["name"]
            assert t["seat"]["sector_id"] in {x["id"] for x in stand["sectors"]}
            assert t["seat"]["sector_id"] in next(g for g in stadium["gates"] if g["id"] == t["seat"]["gate_id"])["serves"]
        assert [i["format"] for i in t["images"]] == ["svg", "pdf", "png"]
        assert all(i["path"].startswith("synthetic/SYN-001/tickets/") for i in t["images"])
        text = json.dumps(t).lower()
        assert codes[0].lower() not in text and not {"holder", "name", "email"} & set(t)
        for img in t["images"]:
            r = client.get(img["url"], headers=h)
            assert r.status_code == (200 if img["available"] else 404)
    assert client.get("/api/ticket").status_code == 401


def test_ticket_validity_window(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    late = tk.load_registry().tickets[0].valid_until + timedelta(minutes=1)
    monkeypatch.setattr(mock_module, "now", lambda: late)
    r = client.post("/api/auth/login", json={"ticket_code": GOLD[0], "device_id": "device-late"})
    assert r.status_code == 403 and r.json()["code"] == "ticket_not_valid"


def test_no_look_ahead_as_of(client: TestClient) -> None:
    h = login(client, DEMO[0])
    for ph in PHASES:
        client.post("/api/demo/phase", headers=h, json={"phase": ph})
        cut = max(PHASE_ELAPSED[ph], 0.0) * 60
        st = client.get("/api/match/state", headers=h).json()
        ev = client.get("/api/match/events", headers=h).json()
        hl = client.get("/api/highlights", headers=h).json()
        assert st["as_of_s"] == ev["as_of_s"] == hl["as_of_s"] == cut
        assert all(e["match_time_s"] + (47 * 60 if e["period"] == 2 else 0) <= cut for e in ev["events"])
        if ph in ("first_half", "second_half"):
            assert client.get("/api/stats/live", headers=h).json()["as_of_s"] == cut
        if ph in ("half_time", "full_time"):
            assert client.get("/api/stats", headers=h).json()["as_of_s"] == cut
