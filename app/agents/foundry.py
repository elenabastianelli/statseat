"""Foundry side of Ask Archie (AG-1b): the two agents' definitions and the Microsoft Agent Framework clients.

- **Archie** (orchestrator and voice) is a prompt agent on Foundry Agent Service with one function tool,
  `ask_the_analyst`, and a JSON response format (text, language, in_scope, chart).
- **The Analyst** is a second prompt agent with the 12 function tools of AG-1a (`function_tools()`).

Prompt agents own their tool declarations on the service; the server only runs the functions (Agent Framework's
`FoundryAgent` with local `FunctionTool`s), with the fan's session. Access is keyless (`DefaultAzureCredential`).
`setup()` creates the agent versions from the files in `prompts/`: it is the only code here that changes Foundry, and
it runs only from the terminal (`python -m app.agents setup`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from agent_framework import ChatContext, ChatMiddleware, FunctionTool
from agent_framework.foundry import FoundryAgent

from app.contract.models import ANALYZER_TABLES, DATA_STATS_TABLES
from . import analyst_tools as at
from .context import ARCHIE_CHARTS  # the engine charts that the app shows
from .settings import ROOT, FoundrySettings

PROMPTS = ROOT / "prompts"
# Expected on every question (the tools are declared on Foundry and only run here): keep it out of the logs.
logging.getLogger("agent_framework.foundry").addFilter(
    lambda r: "tool declarations cannot be sent" not in r.getMessage())
CHART_IDS = DATA_STATS_TABLES + tuple(c[0] for c in ARCHIE_CHARTS)
ANALYZER_CHARTS = {c[0] for c in ARCHIE_CHARTS if "casual" not in c[2]}
ANALYZER_TARGETS = ANALYZER_CHARTS | set(ANALYZER_TABLES)  # what a Fan ticket shows locked (Analyst version only)

ASK_THE_ANALYST = {
    "type": "function", "name": "ask_the_analyst",
    "description": "Ask The Analyst, the data room, for statistics of today's match up to the current minute, "
                   "including xG and the tracking check of an offside call or of a goal (\"was it offside?\"). "
                   "Returns a short data result with exact numbers.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["question"],
                   "properties": {"question": {"type": "string", "description": "The fan's question in English, "
                                                                                "naming the player or team"}}},
}
ASK_KIT_MAN = {
    "type": "function", "name": "ask_kit_man",
    "description": "Ask Kit Man, the club archivist, about the clubs, the players' backgrounds and nicknames, the "
                   "stadium, today's officials and the Laws of the Game, from the matchday documents. Never for the "
                   "numbers of today's match (ask_the_analyst does those). Expensive: at most once per question.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["question"],
                   "properties": {"question": {"type": "string", "description": "The fan's question in English, "
                                                                                "naming the player, club or rule"}}},
}
# AG-2: Kit Man is the second specialist, declared on Archie and run in archie.py (app/agents/kitman.py).
ARCHIE_TOOLS = [ASK_THE_ANALYST, ASK_KIT_MAN]

REPLY_FORMAT = {
    "type": "json_schema", "name": "archie_reply", "strict": True,
    "schema": {"type": "object", "additionalProperties": False, "required": ["text", "language", "in_scope", "chart"],
               "properties": {"text": {"type": "string", "description": "What Archie says to the fan"},
                              "language": {"type": "string", "description": "BCP 47 tag of the language of text"},
                              "in_scope": {"type": "boolean", "description": "false only when Archie declines"},
                              "chart": {"type": ["string", "null"], "enum": [*CHART_IDS, None],
                                        "description": "Data & Statistics table or chart pointed to, or null"}}},
}


# ------------------------------------------------------------------ instructions from prompts/


def block(path: Path) -> str:
    """The text between the two `---8<---` lines, without the sample MATCH CONTEXT (the server writes the real one)."""
    text = re.split(r"^---8<---$", path.read_text(encoding="utf-8"), flags=re.M)[1]
    return text.split("\n# MATCH CONTEXT")[0].strip()


def archie_instructions() -> str:
    return block(PROMPTS / "archie" / "instructions.md") + "\n\n" + block(PROMPTS / "archie" / "app_addendum.md")


def analyst_instructions() -> str:
    return block(PROMPTS / "analyst" / "instructions.md")


def definitions(settings: FoundrySettings) -> dict[str, dict]:
    """The two prompt agents as `create_version` would send them (`python -m app.agents setup --dry-run`)."""
    return {
        "analyst": {"name": settings.analyst_agent, "model": settings.analyst_deployment,
                    "instructions": analyst_instructions(), "tools": at.function_tools(), "text": None},
        "archie": {"name": settings.archie_agent, "model": settings.model, "instructions": archie_instructions(),
                   "tools": ARCHIE_TOOLS, "text": REPLY_FORMAT},
    }


def setup(settings: FoundrySettings, which: tuple[str, ...] = ("analyst", "archie")) -> dict[str, str]:
    """Create a new version of each agent on Foundry (a call to Foundry: run it only with the team's yes)."""
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import FunctionTool as FoundryFunctionTool
    from azure.ai.projects.models import PromptAgentDefinition, PromptAgentDefinitionTextOptions
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=settings.endpoint, credential=DefaultAzureCredential())
    out = {}
    for key in which:
        d = definitions(settings)[key]
        tools = [FoundryFunctionTool(name=t["name"], description=t["description"], parameters=t["parameters"],
                                     strict=False) for t in d["tools"]]
        text = PromptAgentDefinitionTextOptions(format=d["text"]) if d["text"] else None
        agent = project.agents.create_version(
            agent_name=d["name"], description=f"{key} (AG-1b): instructions and tools from the InsideTheGame repo",
            definition=PromptAgentDefinition(model=d["model"], instructions=d["instructions"],
                                             reasoning={"effort": "low"}, tools=tools, text=text))
        out[key] = f"{agent.name} version {agent.version}"
    return out


def show(settings: FoundrySettings) -> dict:
    """The agent versions in use, as Foundry has them (a read call to Foundry)."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=settings.endpoint, credential=DefaultAzureCredential())
    out = {}
    for name, version in ((settings.archie_agent, settings.archie_version),
                          (settings.analyst_agent, settings.analyst_version)):
        if not name:
            continue  # The Analyst is not created yet
        if version:
            v = project.agents.get_version(agent_name=name, agent_version=version)
        else:
            v = project.agents.get(agent_name=name).versions.latest
        d = v.definition
        out[f"{name} {v.version}"] = {"model": d.get("model"), "tools": [t.get("name") for t in d.get("tools") or []],
                                    "text": bool(d.get("text")), "instructions_chars": len(d.get("instructions") or "")}
    return out


# ------------------------------------------------------------------ running the agents (one question)


class BudgetExceeded(RuntimeError):
    pass


class Usage(ChatMiddleware):
    """Counts the tokens and latency of every model call of one agent, and stops the question at the token budget."""

    def __init__(self, record: Callable[[int, int, int], None], spent: Callable[[], int], budget: int) -> None:
        self.record, self.spent, self.budget = record, spent, budget

    async def process(self, context: ChatContext, call_next) -> None:
        if self.spent() >= self.budget:
            raise BudgetExceeded(f"token budget of {self.budget} reached")
        t0 = time.perf_counter()
        await call_next()
        usage = getattr(context.result, "usage_details", None) or {}
        self.record(int(usage.get("input_token_count") or 0), int(usage.get("output_token_count") or 0),
                    int((time.perf_counter() - t0) * 1000))


def local_tool(name: str, description: str, fn: Callable[..., Any], arguments: list[str]) -> FunctionTool:
    """A tool the server runs. The schema the model sees lives on Foundry; locally the arguments keep only their names
    (Agent Framework drops unknown ones), with no type or range, so `fn` checks them (run_tool) and a wrong value
    becomes a clear error in the result and in the Inspector. The session-only names (match_id, mode, as_of_s) are
    accepted here so run_tool can answer them with `out_of_scope`."""
    names = dict.fromkeys([*arguments, *at.SESSION_ONLY])
    return FunctionTool(name=name, description=description, func=fn,
                        input_model={"type": "object", "properties": {k: {} for k in names}})


class Projects:
    """One keyless `AIProjectClient` per event loop (the server has one; the terminal command may make a few)."""

    def __init__(self, settings: FoundrySettings) -> None:
        self.settings = settings
        self._clients: dict[int, Any] = {}

    def __call__(self) -> Any:
        loop = id(asyncio.get_running_loop())
        if loop not in self._clients:
            from azure.ai.projects.aio import AIProjectClient
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()
            self._clients[loop] = (AIProjectClient(endpoint=self.settings.endpoint, credential=credential), credential)
        return self._clients[loop][0]

    async def close(self) -> None:
        """At shutdown: close the client and credential of this event loop (their HTTP sessions)."""
        pair = self._clients.pop(id(asyncio.get_running_loop()), None)
        if pair:
            for c in pair:
                await c.close()


def agent(settings: FoundrySettings, project: Any, name: str, version: str | None, tools: list[FunctionTool],
          usage: Usage) -> FoundryAgent:
    return FoundryAgent(project_client=project, agent_name=name, agent_version=version, tools=tools,
                        middleware=[usage], timeout=settings.timeout_s,
                        function_invocation_configuration={"max_iterations": settings.max_rounds,
                                                           "max_function_calls": settings.max_tool_calls})


def dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
