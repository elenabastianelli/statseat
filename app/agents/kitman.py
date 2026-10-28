"""Kit Man in the app (task AG-2): Archie's second specialist, for the teams, the players' backgrounds, the stadium, the
officials and the Laws of the Game, from the matchday documents of SYN-001 (file search on Foundry, task M-4).

`ask_kitman(project, settings, question)` calls the prompt agent `kitman` by name and version through the project's
Responses API (as `prompts/kitman/foundry_kitman.py` does) and returns a `KitManAnswer`: the text without the citation
markers, the documents it used, tokens and latency. It never raises: on any error the answer says Kit Man is not
available, so Archie answers without the club facts. Kit Man is the most expensive agent (about 10,000 input tokens
per question, PLAN credit rules): Archie may call it once per question, never for the numbers of the match.

The agent and its version come from `KITMAN_AGENT_ID` and `KITMAN_AGENT_VERSION` (defaults: `kitman` version 2, the
M-4 agent on the demo project). Keyless like the other agents (`DefaultAzureCredential`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .settings import FoundrySettings

# Foundry wraps file citations in private-use characters (U+E200 ... U+E201), as foundry_kitman.py strips them.
MARKER = re.compile(".*?", re.S)
UNAVAILABLE = "Kit Man is not available right now: answer without the club, stadium and rules facts."
MAX_QUESTION_CHARS = 500


@dataclass
class KitManAnswer:
    text: str
    citations: list[str] = field(default_factory=list)  # document names, e.g. squad-BRA.md
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    ok: bool = True


def clean(raw: str) -> str:
    """The answer without the citation markers and the spaces they leave."""
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", MARKER.sub("", raw or "")).strip()


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def documents(response: Any) -> list[str]:
    """Documents used: the file citations of the message, else the file search results (M-4 notes: in some answers
    the annotations are empty)."""
    cited, found = [], []
    for item in _get(response, "output", None) or []:
        kind = _get(item, "type")
        if kind == "file_search_call":
            found += [_get(r, "filename") for r in _get(item, "results", None) or []]
        elif kind == "message":
            for part in _get(item, "content", None) or []:
                cited += [_get(a, "filename") for a in _get(part, "annotations", None) or []
                          if _get(a, "type") == "file_citation"]
    return sorted({d for d in (cited or found) if d})


async def ask_kitman(project: Any, settings: FoundrySettings, question: str) -> KitManAnswer:
    """One question to Kit Man (`project` is the async AIProjectClient of the server). Never raises."""
    t0 = time.perf_counter()
    try:
        client = project.get_openai_client()
        r = await client.responses.create(
            input=question.strip()[:MAX_QUESTION_CHARS],
            include=["file_search_call.results"],
            timeout=settings.timeout_s,
            extra_body={"agent_reference": {"name": settings.kitman_agent, "version": settings.kitman_version,
                                            "type": "agent_reference"}},
        )
        usage = _get(r, "usage", None)
        text = clean(_get(r, "output_text", "") or "")
        return KitManAnswer(text=text or "No answer in the matchday documents.", citations=documents(r),
                            tokens_in=int(_get(usage, "input_tokens", 0) or 0),
                            tokens_out=int(_get(usage, "output_tokens", 0) or 0),
                            latency_ms=int((time.perf_counter() - t0) * 1000))
    except Exception:  # network, timeout, missing agent: Archie goes on without Kit Man
        return KitManAnswer(text=UNAVAILABLE, latency_ms=int((time.perf_counter() - t0) * 1000), ok=False)
