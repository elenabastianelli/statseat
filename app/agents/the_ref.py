"""The Ref in the app (task AG-4): the quizmaster who rewrites the quiz questions in the fan's language.

The code chooses, The Ref only writes (prompts/the_ref/README.md): the questions, the options and the right answers come
from `app/games/quiz.py`; The Ref (prompt agent `the-ref` on Foundry, instructions of M-5) gets one JSON object with
every question of the phase and returns the same object with the text rewritten. The questions are the same for every
fan, so the server calls The Ref **once per match, phase and language** and keeps the text in memory; a second fan
with the same language never waits.

Before any text is shown it is checked (README "Checks for the server"): same items and options in the same order,
the same numbers (the decimal separator may change), player labels and club names unchanged, no empty text, questions
of at most 120 characters. An item that fails keeps the code's English text; if Foundry does not answer, every item
does, and that result is kept too (at most one call per phase and language, even when Foundry is down). The right
answer is never sent, and scoring never depends on The Ref: the answer is still checked by `option_id`.

The agent and its version come from `THE_REF_AGENT_ID` and `THE_REF_AGENT_VERSION` (defaults: `the-ref` version 1).
Without `FOUNDRY_PROJECT_ENDPOINT` the quiz stays in the code's English. Keyless like the other agents.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from collections import Counter
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 120
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

Call = Callable[[dict], str]
"""Sends The Ref's input JSON to Foundry and returns the reply text; raises on any error."""


def items(questions: list[dict]) -> list[dict]:
    """The quiz questions as The Ref's input items: no right answer, no fact."""
    return [{"id": q["round_id"], "game": "quiz", "format": _format(q), "metric": q["metric"],
             "question": q["question"],
             "options": [{"id": o["option_id"], "label": o["label"]} for o in q["options"]]} for q in questions]


def _format(q: dict) -> str:
    if any(o.get("player_id") for o in q["options"]):
        return "who"
    return "when" if all(o["label"].endswith("'") for o in q["options"]) else "how_much"


def _numbers(text: str) -> Counter:
    return Counter(n.replace(",", ".") for n in NUMBER.findall(text))


def _parse(raw: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    d = json.loads(s)
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise ValueError("no items")
    return d


def check(given: dict, got: Any, keep: list[str], keep_labels: set[str]) -> str | None:
    """Why The Ref's item `got` cannot replace the input item `given`, or None when it can. `keep`: names (clubs)
    that must stay when they are in the question; `keep_labels`: option ids whose label must not change (players)."""
    if not isinstance(got, dict) or got.get("id") != given["id"]:
        return "different item"
    q, opts = got.get("question"), got.get("options")
    if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUESTION_CHARS:
        return "empty or long question"
    if not isinstance(opts, list) or [o.get("id") if isinstance(o, dict) else None for o in opts] != \
            [o["id"] for o in given["options"]]:
        return "different options"
    if any(not isinstance(o.get("label"), str) or not o["label"].strip() for o in opts):
        return "empty option"
    if _numbers(q) != _numbers(given["question"]):
        return "numbers changed in the question"
    for a, b in zip(given["options"], opts):
        if _numbers(b["label"]) != _numbers(a["label"]):
            return f"numbers changed in option {a['id']}"
        if a["id"] in keep_labels and b["label"] != a["label"]:
            return f"name changed in option {a['id']}"
    if any(name in given["question"] and name not in q for name in keep):
        return "club name changed"
    return None


class TheRef:
    """The rewritten quiz per (match, phase, language), filled by one call to The Ref the first time it is needed."""

    def __init__(self, call: Call | None) -> None:
        self.call = call  # None: Foundry is not configured, the quiz stays in English
        self.calls = 0
        self._cache: dict[tuple, dict[str, dict]] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()

    def localize(self, key: tuple, language: str, questions: list[dict], keep: list[str]) -> list[dict]:
        """`questions` (quiz.session) with the text of The Ref for `language`; a copy, the originals are untouched."""
        lang = (language or "en").split("-")[0].lower()
        if self.call is None:
            return questions
        full = (*key, lang)
        with self._guard:
            lock = self._locks.setdefault(full, threading.Lock())
        with lock:  # the first fan of a language waits for the call, the others for its result
            if full not in self._cache:
                self._cache[full] = self._rewrite(lang, questions, keep)
        texts = self._cache[full]
        out = copy.deepcopy(questions)
        for q in out:
            t = texts.get(q["round_id"])
            if t:
                q["question"] = t["question"]
                for o in q["options"]:
                    o["label"] = t["options"].get(o["option_id"], o["label"])
        return out

    def _rewrite(self, lang: str, questions: list[dict], keep: list[str]) -> dict[str, dict]:
        given = items(questions)
        self.calls += 1
        try:
            reply = _parse(self.call({"language": lang, "items": given}))
        except Exception as exc:  # network, timeout, missing agent, not JSON: the code's English for every item
            log.warning("The Ref (%s): no rewrite, %s", lang, exc)
            return {}
        got = {i.get("id"): i for i in reply["items"] if isinstance(i, dict)}
        out = {}
        for g in given:
            players = {o["option_id"] for q in questions if q["round_id"] == g["id"] for o in q["options"]
                       if o.get("player_id")}
            why = check(g, got.get(g["id"]), keep, players)
            if why:
                log.warning("The Ref (%s) %s: English kept, %s", lang, g["id"], why)
                continue
            r = got[g["id"]]
            out[g["id"]] = {"question": r["question"].strip(),
                            "options": {o["id"]: o["label"].strip() for o in r["options"]}}
        return out


def foundry_call(settings) -> Call:
    """The real call: the prompt agent by name and version through the project's Responses API (sync, as the quiz
    routes are). The client is made at the first call, so a server without the Azure packages still starts."""
    state: dict[str, Any] = {}

    def call(payload: dict) -> str:
        if "client" not in state:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            project = AIProjectClient(endpoint=settings.endpoint, credential=DefaultAzureCredential())
            state["client"] = project.get_openai_client()
        ref = {"name": settings.the_ref_agent, "version": settings.the_ref_version, "type": "agent_reference"}
        r = state["client"].responses.create(input=json.dumps(payload, ensure_ascii=False),
                                             timeout=settings.timeout_s, extra_body={"agent_reference": ref})
        return r.output_text

    return call


def from_env() -> TheRef:
    from .settings import FoundrySettings

    settings = FoundrySettings.from_env()
    return TheRef(foundry_call(settings) if settings.endpoint else None)
