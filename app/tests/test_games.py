"""Tests of the half-time games (M-3): the rules of Tic Tac Goal and Guess the player."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.contract import make_fake
from app.contract.mock import create_app
from app.games import guess, ttt
from app.games.universe import squads

NOW = datetime(2026, 10, 24, 15, 52, tzinfo=timezone(timedelta(hours=1)))
PLAYERS = {p["id"]: p for p in squads("BRA", "CAL")}


def meets(p: dict, c: tuple[str, str]) -> bool:
    if c[0] == "club":
        return any(sp["club_id"] == c[1] for sp in p["career"])
    return p["nationality"] == c[1]


# ------------------------------------------------------------------ Tic Tac Goal


def test_grid_uses_only_career_clubs_and_nationalities_and_is_solvable() -> None:
    rows, cols, sol = ttt.grid("BRA", "CAL")
    for c in rows + cols:
        assert c[0] in ("club", "nationality") and c[1] not in ("BRA", "CAL")
    for r, row in enumerate(rows):
        for k, col in enumerate(cols):
            expected = sorted(pid for pid, p in PLAYERS.items() if meets(p, row) and meets(p, col))
            assert sol[f"{r},{k}"] == expected
            assert len(expected) >= 2, "every cell has at least 2 answers"
    assert sum(len(v) <= 3 for v in sol.values()) >= 6, "a grid with mostly 2-3 answers per cell, not the easiest one"


def new_game() -> ttt.Game:
    return ttt.start("BRA", "CAL", "TTT-TEST", 0.0, NOW)


def free_cells(g: ttt.Game) -> int:
    return sum(c["owner"] is None for c in g.view["board"])


def test_right_answer_takes_the_cell_then_the_bot_plays() -> None:
    g = new_game()
    res = ttt.move(g, 0, 0, g.solutions["0,0"][0], 5.0, NOW)
    assert res["correct"] and res["points_awarded"] == ttt.CELL_POINTS
    assert g.view["board"][0]["owner"] == "me"
    assert g.view["turn"] == "me" and res["elapsed_ms"] == 5000


def test_wrong_answer_loses_the_turn() -> None:
    g = new_game()
    wrong = next(pid for pid in PLAYERS if pid not in g.solutions["0,0"])
    res = ttt.move(g, 0, 0, wrong, 3.0, NOW)
    assert not res["correct"] and res["reason"] == "wrong_answer" and res["points_awarded"] == 0
    assert g.view["board"][0]["owner"] != "me"


def test_taken_cell_does_not_lose_the_turn() -> None:
    g = new_game()
    ttt.move(g, 0, 0, g.solutions["0,0"][0], 1.0, NOW)
    before = free_cells(g)
    res = ttt.move(g, 0, 0, g.solutions["0,0"][0], 2.0, NOW)
    assert res["reason"] == "cell_taken" and free_cells(g) == before


def test_after_20_seconds_the_turn_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ttt, "BOT_ACCURACY", 1.0)
    g = new_game()
    assert ttt.refresh(g, 20.0, NOW) == 0 and free_cells(g) == 9  # still in time
    ttt.refresh(g, 20.5, NOW)
    assert free_cells(g) == 8 and g.view["turn"] == "me"
    late = ttt.move(g, 2, 2, g.solutions["2,2"][0], 45.0, NOW)
    assert late["reason"] == "too_late"


def test_three_in_a_row_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ttt, "BOT_ACCURACY", 0.0)  # the bot always answers wrong
    g = new_game()
    for t, c in enumerate(range(3), start=1):
        res = ttt.move(g, 0, c, g.solutions[f"0,{c}"][0], float(t), NOW)
    assert g.view["status"] == "won"
    assert res["points_awarded"] == ttt.CELL_POINTS + ttt.WIN_POINTS
    with pytest.raises(ttt.GameOver):
        ttt.move(g, 1, 1, g.solutions["1,1"][0], 4.0, NOW)


def test_ten_minutes_without_three_in_a_row_is_a_draw() -> None:
    g = new_game()
    assert ttt.refresh(g, ttt.GAME_SECONDS, NOW) == ttt.DRAW_POINTS
    assert g.view["status"] == "draw"


def test_same_grid_for_every_fan() -> None:
    a, b = ttt.start("BRA", "CAL", "TTT-A", 0.0, NOW), ttt.start("BRA", "CAL", "TTT-B", 0.0, NOW)
    assert (a.view["rows"], a.view["cols"]) == (b.view["rows"], b.view["cols"])


# ------------------------------------------------------------------ Guess the player


def test_five_rounds_with_eligible_players_and_four_options() -> None:
    rounds = guess.rounds("SYN-001", "BRA", "CAL")
    assert len(rounds) == guess.ROUNDS and len({r["answer"] for r in rounds}) == guess.ROUNDS
    for r in rounds:
        p = PLAYERS[r["answer"]]
        assert guess.clubs_in_career(p) >= 3
        ids = [o["player_id"] for o in r["options"]]
        assert len(set(ids)) == 4 and p["id"] in ids
        unit = guess.UNIT[p["position_group"]]
        eligible_same_unit = [q for q in PLAYERS.values() if q["id"] != p["id"] and guess.clubs_in_career(q) >= 3
                              and guess.UNIT[q["position_group"]] == unit]
        same_unit = sum(guess.UNIT[PLAYERS[i]["position_group"]] == unit for i in ids if i != p["id"])
        assert same_unit == min(3, len(eligible_same_unit)), "wrong options from the same unit when there are enough"


def test_career_hides_the_current_club_and_shows_only_paid_fees() -> None:
    names = {c["id"]: c["name"] for c in make_fake.U["clubs"]}
    for r in guess.rounds("SYN-001", "BRA", "CAL"):
        p = PLAYERS[r["answer"]]
        assert len(r["career"]) == len(p["career"])
        for clue, sp in zip(r["career"], p["career"]):
            if sp["club_id"] == p["club_id"]:
                assert clue["hidden"] and clue["club_name"] == "?"
            else:
                assert not clue["hidden"] and clue["club_name"] == names[sp["club_id"]]
            assert clue["fee_eur"] == (sp["fee_eur"] if sp["move"] == "transfer" else None)


# ------------------------------------------------------------------ through the API (half-time)


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict]:
    monkeypatch.setenv("MOCK_PHASE", "half_time")
    client = TestClient(create_app())
    r = client.post("/api/auth/login", json={"ticket_code": make_fake.FAKE_TICKET_CODES["gold"][0], "device_id": "device-games-1"})
    return client, {"Authorization": f"Bearer {r.json()['token']}"}


def test_guess_the_player_session_of_five_questions(demo) -> None:
    client, h = demo
    total = 0
    for n in range(1, guess.ROUNDS + 1):
        rnd = client.post("/api/games/guess-the-player", headers=h).json()
        assert rnd["round_number"] == n and rnd["rounds_total"] == guess.ROUNDS
        answer = guess.answer_of("SYN-001", "BRA", "CAL", rnd["round_id"])["correct"]
        res = client.post(f"/api/games/guess-the-player/{rnd['round_id']}/answer", headers=h,
                          json={"option_id": answer}).json()
        assert res["correct"] and res["points_awarded"] >= 100
        total += res["points_awarded"]
    assert client.post("/api/games/guess-the-player", headers=h).status_code == 409
    assert client.get("/api/leaderboard", headers=h).json()["my_points"] >= total


def test_tiki_taka_toe_through_the_api(demo) -> None:
    client, h = demo
    g = client.post("/api/games/tiki-taka-toe", headers=h).json()
    assert g["opponent"]["is_bot"] and g["status"] == "playing" and g["ends_at"]
    sol = ttt.grid("BRA", "CAL")[2]
    mv = client.post(f"/api/games/tiki-taka-toe/{g['game_session_id']}/moves", headers=h,
                     json={"row": 1, "col": 1, "player_id": sol["1,1"][0]}).json()
    assert mv["correct"] and mv["game"]["board"][4]["owner"] == "me"
    bad = client.post(f"/api/games/tiki-taka-toe/{g['game_session_id']}/moves", headers=h,
                      json={"row": 0, "col": 0, "player_id": "XYZ-99"})
    assert bad.status_code == 422
    assert client.get("/api/games/tiki-taka-toe/TTT-NOPE", headers=h).status_code == 404


def test_crests_are_served_without_a_token(demo) -> None:
    client, _ = demo
    r = client.get("/api/crests/ALD")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/crests/xx").status_code == 404
    assert client.get("/api/crests/..%2Fx").status_code == 404
