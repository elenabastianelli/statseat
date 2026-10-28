"""Tests of The Ref in the app (AG-4): one call per phase and language, the server's checks, the English fallback."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.agents import the_ref
from app.contract import make_fake
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app
from app.engine.sources.fake import load_fake

DEMO = make_fake.FAKE_TICKET_CODES["demo"]

QUESTIONS = [
    {"round_id": "QZ-1", "metric": "distance_covered", "question": "Who ran the furthest in the first half?",
     "options": [{"option_id": f"CAL-0{n}", "label": f"Player {n} (CAL)", "player_id": f"CAL-0{n}"} for n in range(1, 5)],
     "right": ["CAL-01"], "fact": "Player 1, 6.1 km"},
    {"round_id": "QZ-2", "metric": "xg", "question": "Brackwater Athletic scored 1 goal: how much xG had they created?",
     "options": [{"option_id": f"B{n}", "label": f"{n / 2:.1f}–{n / 2 + 0.5:.1f} xG", "player_id": None}
                 for n in range(4)], "right": ["B1"], "fact": "0.66 xG"},
]
ITALIAN = {"QZ-1": "Chi ha corso di più nel primo tempo?",
           "QZ-2": "Il Brackwater Athletic ha segnato 1 gol: quanti xG aveva creato?"}


class FakeRef:
    """Answers like The Ref: the Italian question, options with the Italian decimal comma; `edit` breaks one item."""

    def __init__(self, edit=None, fail: bool = False, prefix: bool = False) -> None:
        self.edit, self.fail, self.prefix, self.payloads = edit, fail, prefix, []

    def __call__(self, payload: dict) -> str:
        self.payloads.append(payload)
        if self.fail:
            raise TimeoutError("Foundry is down (fake)")
        lang = payload["language"]
        items = [{"id": i["id"], "question": f"[{lang}] {i['question']}" if self.prefix else ITALIAN.get(i["id"], i["question"]),
                  "options": [{"id": o["id"], "label": o["label"].replace(".", ",")} for o in i["options"]]}
                 for i in payload["items"]]
        if self.edit:
            self.edit(items)
        return "```json\n" + json.dumps({"language": payload["language"], "items": items}) + "\n```"


def localize(ref: the_ref.TheRef, lang: str = "it") -> list[dict]:
    return ref.localize(("SYN-001", "half_time"), lang, QUESTIONS, ["Brackwater Athletic"])


def test_rewrites_text_and_keeps_answers() -> None:
    fake = FakeRef()
    out = localize(the_ref.TheRef(fake))
    assert [q["question"] for q in out] == list(ITALIAN.values())
    assert out[1]["options"][1]["label"] == "0,5–1,0 xG"  # the decimal separator may change
    assert [q["right"] for q in out] == [q["right"] for q in QUESTIONS]
    assert QUESTIONS[0]["question"].startswith("Who"), "the code's questions are not touched"
    sent = fake.payloads[0]
    assert sent["language"] == "it" and [i["format"] for i in sent["items"]] == ["who", "how_much"]
    assert "right" not in json.dumps(sent) and "6.1 km" not in json.dumps(sent), "never the answer"


def test_one_call_per_phase_and_language() -> None:
    fake = FakeRef()
    ref = the_ref.TheRef(fake)
    for lang in ("it", "it-IT", "es", "it"):
        localize(ref, lang)
    ref.localize(("SYN-001", "full_time"), "it", QUESTIONS, [])
    assert [p["language"] for p in fake.payloads] == ["it", "es", "it"]


def test_failed_checks_keep_the_english_of_that_item() -> None:
    def change_number(items):
        items[1]["question"] = "Il Brackwater Athletic ha segnato 2 gol: quanti xG aveva creato?"

    def rename_player(items):
        items[0]["options"][0]["label"] = "Giocatore 1 (CAL)"

    def drop_club(items):
        items[1]["question"] = "La squadra di casa ha segnato 1 gol: quanti xG aveva creato?"

    for edit, english in ((change_number, "QZ-2"), (rename_player, "QZ-1"), (drop_club, "QZ-2")):
        out = {q["round_id"]: q for q in localize(the_ref.TheRef(FakeRef(edit)))}
        assert out[english]["question"] == next(q["question"] for q in QUESTIONS if q["round_id"] == english)
        other = "QZ-1" if english == "QZ-2" else "QZ-2"
        assert out[other]["question"] == ITALIAN[other]


def test_check_rules() -> None:
    given = the_ref.items(QUESTIONS)[1]
    ok = {"id": "QZ-2", "question": ITALIAN["QZ-2"], "options": [{"id": o["id"], "label": o["label"]}
                                                                for o in given["options"]]}
    assert the_ref.check(given, ok, [], set()) is None
    assert the_ref.check(given, {**ok, "question": "x" * 121}, [], set()) == "empty or long question"
    assert the_ref.check(given, {**ok, "options": ok["options"][::-1]}, [], set()) == "different options"
    assert the_ref.check(given, {**ok, "question": ITALIAN["QZ-2"] + " 0.66"}, [], set()) == \
        "numbers changed in the question"


def test_foundry_down_or_not_json_falls_back_once() -> None:
    for fake in (FakeRef(fail=True), lambda p: "Certo! Ecco le domande."):
        ref = the_ref.TheRef(fake)
        assert localize(ref) == QUESTIONS and localize(ref) == QUESTIONS
        assert ref.calls == 1, "no second call when Foundry is down"


def test_without_foundry_the_quiz_stays_in_english() -> None:
    ref = the_ref.TheRef(None)
    assert localize(ref) is QUESTIONS and ref.calls == 0


def test_quiz_route_in_the_fans_language() -> None:
    fake = FakeRef(prefix=True)  # the real questions of the fake match, marked with the language
    client = TestClient(create_app(Engine(load_fake(), EngineConfig(speed=10.0)), the_ref=the_ref.TheRef(fake)))
    assert client.get("/api/health").json()["the_ref"] == "foundry"
    for n, lang in enumerate(("it", "it", "en")):
        h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={
            "ticket_code": DEMO[n], "device_id": f"device-ref-{n}"}).json()["token"]}
        prefs = client.get("/api/session", headers=h).json()["preferences"]
        client.put("/api/session/preferences", headers=h, json={**prefs, "language": lang})
        client.post("/api/demo/phase", headers=h, json={"phase": "half_time"})
        rnd = client.post("/api/games/quiz", headers=h).json()
        sent = next(i for i in fake.payloads[-1]["items"] if i["id"] == rnd["round_id"])
        assert rnd["question"] == f"[{lang}] {sent['question']}"
        res = client.post(f"/api/games/quiz/{rnd['round_id']}/answer", headers=h, json={"option_id": "nope"})
        assert res.status_code == 200 and not res.json()["correct"], "answers still checked by option_id"
    assert [p["language"] for p in fake.payloads] == ["it", "en"], "the second Italian fan reuses the text"
