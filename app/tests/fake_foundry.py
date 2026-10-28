"""A fake Foundry for the tests of Ask Archie (AG-1b): no network, no model, no token.

It stands in for the project's OpenAI client: Microsoft Agent Framework's `FoundryAgent` runs for real (agent
reference, function-call loop, middleware), and every request goes to a scripted Responses API in this process.
Each agent has a *brain*: given the user text of the conversation and the tool outputs so far, it either calls tools
or writes the final text, as a model would.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Callable

import httpx
from openai import AsyncOpenAI

Brain = Callable[["Turn"], "list[tuple[str, dict]] | str | dict"]
"""A brain returns tool calls, a final text, or (Kit Man, AG-2) {"text": ..., "files": [...]}: a file search whose
results are those files, then the text with citation markers around the first file's name."""


@dataclass
class Turn:
    agent: str
    user: str  # the text of the user messages of the conversation
    outputs: list[tuple[str, dict, str]]  # (tool, arguments, output) so far

    def output(self, tool: str) -> dict:
        return json.loads(next(o for t, _, o in reversed(self.outputs) if t == tool))


@dataclass
class FakeFoundry:
    brains: dict[str, Brain]
    fail: bool = False  # every request fails, as when Foundry is down
    requests: list[dict] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=itertools.count)
    _conv: dict[str, list[dict]] = field(default_factory=dict)  # response id -> conversation items

    def __call__(self) -> "FakeFoundry":  # the `projects` factory of ArchieService
        return self

    def get_openai_client(self, **_: object) -> AsyncOpenAI:
        return AsyncOpenAI(base_url="https://fake-foundry.local/openai/v1", api_key="not-a-key", max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(self._handle)))

    async def close(self) -> None:
        pass

    # ------------------------------------------------------------------ the scripted Responses API
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(503, json={"error": {"message": "Foundry is down (fake)", "type": "server_error"}})
        body = json.loads(request.content)
        self.requests.append(body)
        agent = body["agent_reference"]["name"]
        given = body.get("input") or []
        if isinstance(given, str):  # Kit Man is called with a plain string (app/agents/kitman.py)
            given = [{"type": "message", "role": "user", "content": given}]
        conv = list(self._conv.get(body.get("previous_response_id") or "", [])) + list(given)
        turn = self._turn(agent, conv)
        decision = self.brains[agent](turn)
        n = next(self._ids)
        if isinstance(decision, dict):
            files = decision.get("files", [])
            output = [{"type": "file_search_call", "id": f"fs_{n}", "status": "completed", "queries": [],
                       "results": [{"file_id": f"file_{i}", "filename": f, "score": 0.9, "text": ""}
                                   for i, f in enumerate(files)]},
                      {"type": "message", "id": f"msg_{n}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "annotations": [],
                                    "text": decision["text"] + (f" \ue200cite:{files[0]}\ue201" if files else "")}]}]
        elif isinstance(decision, str):
            output = [{"type": "message", "id": f"msg_{n}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": decision, "annotations": []}]}]
        else:
            output = [{"type": "function_call", "id": f"fc_{n}_{i}", "call_id": f"call_{n}_{i}", "name": name,
                       "arguments": json.dumps(args), "status": "completed"} for i, (name, args) in enumerate(decision)]
        rid = f"resp_{n}"
        self._conv[rid] = conv + output
        return httpx.Response(200, json={
            "id": rid, "object": "response", "created_at": 1761400000, "status": "completed", "model": "gpt-6-luna",
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "error": None,
            "incomplete_details": None, "instructions": None, "metadata": {}, "temperature": None, "top_p": None,
            "text": {"format": {"type": "text"}},
            "usage": {"input_tokens": 1000, "output_tokens": 40, "total_tokens": 1040,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}})

    @staticmethod
    def _turn(agent: str, conv: list[dict]) -> Turn:
        texts, calls, outputs = [], {}, []
        for item in conv:
            kind = item.get("type", "message")
            if kind == "message" and item.get("role") == "user":
                content = item["content"]
                texts += [content] if isinstance(content, str) else [c.get("text", "") for c in content]
            elif kind == "function_call":
                calls[item["call_id"]] = (item["name"], json.loads(item["arguments"] or "{}"))
            elif kind == "function_call_output":
                name, args = calls[item["call_id"]]
                out = item["output"]
                if isinstance(out, list):
                    out = "".join(c.get("text", "") for c in out)
                outputs.append((name, args, out))
        return Turn(agent, "\n".join(texts), outputs)
