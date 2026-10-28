"""Tests of Kit Man in the app (AG-2): the module on its own, then as Archie's second tool, with a fake Foundry. No
network, no model, no token: the answers are scripted like the saved ones of M-4 (prompts/kitman/foundry_test_M4.json)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agents import foundry
from app.agents import kitman as km
from app.agents.settings import FoundrySettings

from .fake_foundry import Turn
from .test_archie import ask, engine, fixture_dir, reply, start  # noqa: F401  (fixtures)

M4 = json.loads((Path(__file__).resolve().parents[2] / "prompts/kitman/foundry_test_M4.json").read_text(encoding="utf-8"))
SETTINGS = FoundrySettings(endpoint="https://fake", archie_agent="archie", archie_version="5", analyst_agent="a",
                           analyst_version="1")


# ------------------------------------------------------------------ the module


def test_clean_strips_the_citation_markers() -> None:
    raw = "Brackwater's number 10 is Riku Shimazaki cite:turn0file0. He's from Sendai x."
    assert km.clean(raw) == "Brackwater's number 10 is Riku Shimazaki. He's from Sendai."
    v2 = next(r for r in M4["runs"] if r["version"] == "2")
    assert all(km.clean(t["answer"]) == t["answer"].strip() for t in v2["turns"])  # saved answers are already clean


def test_documents_from_citations_else_from_the_file_search() -> None:
    cited = {"output": [{"type": "file_search_call", "results": [{"filename": "matchday.md"}, {"filename": "squad-BRA.md"}]},
                        {"type": "message", "content": [{"annotations": [{"type": "file_citation",
                                                                          "filename": "squad-BRA.md"}]}]}]}
    assert km.documents(cited) == ["squad-BRA.md"]
    found_only = {"output": [{"type": "file_search_call", "results": [{"filename": "league.md"}, {"filename": "club-CAL.md"}]},
                             {"type": "message", "content": [{"annotations": []}]}]}
    assert km.documents(found_only) == ["club-CAL.md", "league.md"]  # M-4: 3 answers had no annotations
    assert km.documents({}) == []


class _Responses:
    def __init__(self, fail: bool) -> None:
        self.fail, self.requests = fail, []

    async def create(self, **kw):
        self.requests.append(kw)
        if self.fail:
            raise TimeoutError("Foundry did not answer")
        return {"output_text": "Riku Shimazaki, from Sendai c.", "usage": {"input_tokens": 9881, "output_tokens": 95},
                "output": [{"type": "file_search_call", "results": [{"filename": "squad-BRA.md"}]}]}


class _Project:
    def __init__(self, fail: bool = False) -> None:
        self.responses = _Responses(fail)

    def get_openai_client(self):
        return self


def test_ask_kitman_calls_the_agent_by_name_and_version() -> None:
    p = _Project()
    a = asyncio.run(km.ask_kitman(p, SETTINGS, "Who is Brackwater's number 10?"))
    assert a.ok and a.text == "Riku Shimazaki, from Sendai." and a.citations == ["squad-BRA.md"]
    assert (a.tokens_in, a.tokens_out) == (9881, 95)
    req = p.responses.requests[0]
    assert req["extra_body"]["agent_reference"] == {"name": "kitman", "version": "2", "type": "agent_reference"}
    assert req["include"] == ["file_search_call.results"] and req["input"] == "Who is Brackwater's number 10?"


def test_ask_kitman_never_raises() -> None:
    a = asyncio.run(km.ask_kitman(_Project(fail=True), SETTINGS, "Stadium history?"))
    assert not a.ok and a.text == km.UNAVAILABLE and a.citations == []


def test_kitman_agent_from_the_environment(monkeypatch) -> None:
    assert (FoundrySettings.from_env().kitman_agent, FoundrySettings.from_env().kitman_version) == ("kitman", "2")
    monkeypatch.setenv("KITMAN_AGENT_ID", "kitman-test")
    monkeypatch.setenv("KITMAN_AGENT_VERSION", "3")
    s = FoundrySettings.from_env()
    assert (s.kitman_agent, s.kitman_version) == ("kitman-test", "3")


def test_archie_declares_kit_man_with_the_credit_rule() -> None:
    assert [t["name"] for t in foundry.ARCHIE_TOOLS] == ["ask_the_analyst", "ask_kit_man"]
    d = foundry.ASK_KIT_MAN["description"]
    assert "Never for the numbers of today's match" in d and "at most once per question" in d
    assert "ask_kit_man" in foundry.archie_instructions()
    defs = foundry.definitions(SETTINGS)
    assert [t["name"] for t in defs["archie"]["tools"]] == ["ask_the_analyst", "ask_kit_man"]


# ------------------------------------------------------------------ Kit Man as Archie's second tool


def via_kitman(question: str, say):
    def brain(t: Turn):
        if not t.outputs:
            return [("ask_kit_man", {"question": question})]
        return say(t.outputs[-1][2])
    return brain


def test_archie_asks_kit_man_and_the_inspector_shows_it(engine) -> None:  # noqa: F811
    brains = {"archie": via_kitman("When was Brackwater founded?",
                                   lambda doc: reply("Brackwater go back to 1887, a proper old club.")),
              "the-analyst": lambda t: "unused",
              "kitman": lambda t: {"text": "Brackwater Athletic were founded in 1887.", "files": ["club-BRA.md"]}}
    client, h, fake, _, _ = start(engine, brains)
    a = ask(client, h, "How old is Brackwater?")
    assert a["text"].startswith("Brackwater go back to 1887")
    g = [x for x in a["grounding"] if x["value"] == 1887]
    assert g and g[0]["tool"] == "ask_kit_man"  # the year comes from Kit Man's answer
    ins = a["inspector"]
    assert ins["grounding"]["passed"]
    assert [s["agent"] for s in ins["agents"]] == ["Archie", "Kit Man"]
    kit = ins["agents"][1]
    assert kit["role"] == "matchday documents (file search)" and kit["tokens_in"] == 1000
    assert "kitman:club-BRA.md" in ins["evidence"] and "Kit Man" in ins["method"]
    call = next(c for c in ins["tool_calls"] if c["tool"] == "ask_kit_man")
    assert call["agent"] == "Archie" and "club-BRA.md" in call["result_summary"]
    kit_req = next(r for r in fake.requests if r["agent_reference"]["name"] == "kitman")
    assert kit_req["agent_reference"]["version"] == "2" and kit_req["input"] == "When was Brackwater founded?"


def test_kit_man_at_most_once_per_question(engine) -> None:  # noqa: F811
    def archie(t: Turn):
        if not t.outputs:
            return [("ask_kit_man", {"question": "Stadium history?"}), ("ask_kit_man", {"question": "And the roof?"})]
        return reply("A grand old ground.")
    brains = {"archie": archie, "the-analyst": lambda t: "unused",
              "kitman": lambda t: {"text": "Lanternworks Stadium opened long ago.", "files": ["stadium.md"]}}
    client, h, fake, _, _ = start(engine, brains)
    a = ask(client, h, "Tell me about the stadium")
    assert sum(r["agent_reference"]["name"] == "kitman" for r in fake.requests) == 1
    calls = [c for c in a["inspector"]["tool_calls"] if c["tool"] == "ask_kit_man"]
    assert len(calls) == 2 and sum("limit reached" in c["result_summary"] for c in calls) == 1  # parallel calls too


def test_archie_goes_on_when_kit_man_is_down(engine) -> None:  # noqa: F811
    seen = []

    def archie(t: Turn):
        if not t.outputs:
            return [("ask_kit_man", {"question": "Who is the referee's assistant?"})]
        seen.append(t.outputs[-1][2])
        return reply("I'll have to check that one with the lads.")

    def kitman(t: Turn):
        raise RuntimeError("Kit Man's vector store is gone")

    client, h, *_ = start(engine, {"archie": archie, "the-analyst": lambda t: "unused", "kitman": kitman})
    a = ask(client, h, "Who are the assistants?")
    assert a["text"] == "I'll have to check that one with the lads."
    assert seen == [km.UNAVAILABLE]
    assert [s["agent"] for s in a["inspector"]["agents"]] == ["Archie"]  # no Kit Man step when he did not answer
