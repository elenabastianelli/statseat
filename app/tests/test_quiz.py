"""Tests of the match quiz (M-3 part 2): the rules of the quiz."""

from __future__ import annotations

import random

from fastapi.testclient import TestClient

from app.contract import make_fake
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app
from app.engine.sources.fake import load_fake
from app.games import quiz

DEMO = make_fake.FAKE_TICKET_CODES["demo"]


def player(pid: str, minutes: int = 46, **values) -> dict:
    return {"player_id": pid, "team_id": pid[:3], "minutes_played": minutes, "distance": None, **values}


def stats(players: list[dict], **extra) -> dict:
    base = {"players": players, "shots": [{"team_id": "BRA"}, {"team_id": "CAL"}],
            "counts": [{"team_id": "BRA", "woodwork": 0, "offsides": 0, "penalties": 0},
                       {"team_id": "CAL", "woodwork": 0, "offsides": 0, "penalties": 0}]}
    return {**base, **extra}


SPRINTS = next(q for q in quiz.WHO if q.qid == "Q19")
PLAYERS = [player(f"BRA-{n:02d}", sprints=v) for n, v in zip(range(1, 12), [9, 8, 7, 7, 6, 5, 4, 3, 2, 1, 1])]


# ------------------------------------------------------------------ "Who?"


def test_who_options_first_closest_lower_and_random() -> None:
    q = quiz._who(SPRINTS, stats(PLAYERS), random.Random(0))
    ids = [o["option_id"] for o in q["options"]]
    assert q["right"] == ["BRA-01"] and len(set(ids)) == 4
    assert "BRA-02" in ids, "the closest: the 2nd"
    assert any(i in ids for i in ("BRA-04", "BRA-05", "BRA-06", "BRA-07", "BRA-08")), "one from 4th to 8th place"
    assert q["fact"].endswith(", 9 sprints")


def test_who_ties_first_are_all_right_and_more_than_three_discard() -> None:
    tied = [player("BRA-01", sprints=9), player("BRA-02", sprints=9)] + PLAYERS[2:]
    q = quiz._who(SPRINTS, stats(tied), random.Random(0))
    assert sorted(q["right"]) == ["BRA-01", "BRA-02"] and q["fact"].endswith("9 sprints each")
    assert {"BRA-01", "BRA-02"} <= {o["option_id"] for o in q["options"]}
    four = [player(f"BRA-0{n}", sprints=9) for n in range(1, 5)] + PLAYERS[4:]
    assert quiz._who(SPRINTS, stats(four), random.Random(0)) is None


def test_who_needs_four_candidates_and_never_counts_zero() -> None:
    few = [player("BRA-01", sprints=3), player("BRA-02", sprints=2), player("BRA-03", sprints=1), player("BRA-04", sprints=0)]
    assert quiz._who(SPRINTS, stats(few), random.Random(0)) is None


def test_accuracy_only_players_with_20_minutes() -> None:
    acc = next(q for q in quiz.WHO if q.qid == "Q13")
    rows = [player("BRA-01", minutes=5, passes=1, pass_accuracy_pct=100.0)] + \
           [player(f"BRA-{n:02d}", passes=10, pass_accuracy_pct=90.0 - n) for n in range(2, 8)]
    q = quiz._who(acc, stats(rows), random.Random(0))
    assert "BRA-01" not in {o["option_id"] for o in q["options"]} and q["right"] == ["BRA-02"]


# ------------------------------------------------------------------ "How much?" and "When?"


def test_bands_are_contiguous_and_contain_the_value() -> None:
    q = next(q for q in quiz.HOW_MUCH if q.qid == "Q8")
    for seed in range(20):
        built = quiz._how_much(q, stats([], possession={"teams": [{"team_id": "BRA", "value": 57.0},
                                                                 {"team_id": "CAL", "value": 43.0}]}), random.Random(seed))
        labels = [o["label"] for o in built["options"]]
        right = next(o["label"] for o in built["options"] if o["option_id"] == built["right"][0])
        lo, hi = (float(x) for x in right.rstrip("%").split("–"))
        value = 57.0 if "Brackwater" in built["question"] else 43.0
        assert lo <= value < hi and hi - lo == 5
        starts = [float(lab.split("–")[0]) for lab in labels]
        assert starts == [starts[0] + 5 * i for i in range(4)] and starts[0] >= 0


def test_rare_counts_off_when_zero_for_both_teams() -> None:
    wood = next(q for q in quiz.HOW_MUCH if q.qid == "Q22")
    assert quiz._how_much(wood, stats([]), random.Random(0)) is None
    s = stats([])
    s["counts"][1]["woodwork"] = 4
    built = quiz._how_much(wood, s, random.Random(0))
    assert [o["label"] for o in built["options"]] == ["0", "1", "2", "3 or more"]


def test_when_uses_five_minute_windows_of_the_team() -> None:
    series = [{"minute": m, "value": 0.9 if 16 <= m <= 20 else -0.9 if 31 <= m <= 35 else 0.0} for m in range(1, 46)]
    s = stats([], momentum={"series": series})
    for seed in range(10):
        built = quiz._when(quiz.When(), s, random.Random(seed))
        best = "15'–20'" if "Brackwater" in built["question"] else "30'–35'"
        assert next(o["label"] for o in built["options"] if o["option_id"] in built["right"]) == best


# ------------------------------------------------------------------ the session


def test_session_same_for_everyone_distinct_metrics_and_at_most_two_how_much() -> None:
    s = stats(PLAYERS + [player(f"CAL-{n:02d}", passes=n, touches=2 * n, fouls_committed=n % 3) for n in range(1, 12)],
              possession={"teams": [{"team_id": "BRA", "value": 57.0}, {"team_id": "CAL", "value": 43.0}]},
              effective_playing_time={"total_min": 26.0}, goals_vs_xg=[{"team_id": "BRA", "goals": 1, "xg": 0.4},
                                                                     {"team_id": "CAL", "goals": 0, "xg": 0.2}])
    a, b = quiz.session(s, "SYN-001", "half_time"), quiz.session(s, "SYN-001", "half_time")
    assert a == b and len(a) == quiz.ROUNDS
    assert [r["round_id"] for r in a] == [f"QZ-{n}" for n in range(1, 6)]
    assert len({r["metric"] for r in a}) == len(a)
    assert sum(r["options"][0]["player_id"] is None and not r["options"][0]["option_id"].startswith("W") for r in a) <= 2


# ------------------------------------------------------------------ through the real backend (half-time, DEMO)


def test_quiz_session_through_the_engine() -> None:
    client = TestClient(create_app(Engine(load_fake(), EngineConfig(speed=10.0))))
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"ticket_code": DEMO[0],
                                                                         "device_id": "device-quiz-01"}).json()["token"]}
    assert client.post("/api/games/quiz", headers=h).status_code == 423  # Games opens only at half-time
    client.post("/api/demo/phase", headers=h, json={"phase": "half_time"})
    seen, right = [], {}
    while (r := client.post("/api/games/quiz", headers=h)).status_code == 200:
        rnd = r.json()
        assert len(rnd["options"]) == 4 and rnd["scope"] == "first_half" and rnd["answer_seconds"] == 20
        seen.append(rnd)
        wrong = client.post(f"/api/games/quiz/{rnd['round_id']}/answer", headers=h, json={"option_id": "nope"}).json()
        assert not wrong["correct"] and wrong["points_awarded"] == 0 and wrong["fact"]
        right[rnd["round_id"]] = wrong["correct_option_id"]  # revealed after the answer
        assert client.post(f"/api/games/quiz/{rnd['round_id']}/answer", headers=h,
                           json={"option_id": "nope"}).status_code == 404  # one answer per question
    assert r.status_code == 409 and r.json()["code"] == "no_more_rounds"
    assert 1 <= len(seen) <= quiz.ROUNDS
    # another fan gets the same questions in the same order; a right answer earns 100 plus the speed bonus
    h2 = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"ticket_code": DEMO[1],
                                                                          "device_id": "device-quiz-02"}).json()["token"]}
    client.post("/api/demo/phase", headers=h2, json={"phase": "half_time"})
    before = client.get("/api/leaderboard", headers=h2).json()["my_points"]
    for first in seen:
        rnd = client.post("/api/games/quiz", headers=h2).json()
        assert (rnd["question"], rnd["options"]) == (first["question"], first["options"])
        res = client.post(f"/api/games/quiz/{rnd['round_id']}/answer", headers=h2,
                          json={"option_id": right[rnd["round_id"]]}).json()
        assert res["correct"] and 100 <= res["points_awarded"] <= 150
    assert client.get("/api/leaderboard", headers=h2).json()["my_points"] >= before + 100 * len(seen)
