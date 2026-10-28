"""Ask Archie on Foundry (task AG-1b, second half of S-2): `POST /api/archie/ask` with grounding and the Inspector.

One question, step by step:

1. the server builds the MATCH CONTEXT from the fan's session (`context.py`): the match on the ticket, the fan's
   minute and phase, version, language and open sections. The model never chooses the match;
2. Archie (Foundry prompt agent, Microsoft Agent Framework) reads the context and the fan's words. For a statistic
   it calls `ask_the_analyst`; the server runs The Analyst (a second prompt agent), whose 12 tools run here with the
   fan's session: no future, the ticket's version, the ticket's match;
3. the grounding validator (`grounding.py`) checks every number of Archie's text against the tool results and the
   MATCH CONTEXT. If one is not justified, Archie writes the answer once more with the list of the wrong numbers and
   the data results he already has; if it is still not justified, the answer is discarded and Archie says he will
   check with the team (no number at all);
4. if Foundry fails (network, timeout, token budget), the fan gets the deterministic card: the scoreboard from the
   engine and a pointer to Data & Statistics, never a raw error.

The model round trips, tool calls and tokens per question are capped (`settings.py`). Kit Man (AG-2) is the second
specialist tool next to `ask_the_analyst` (see `foundry.ARCHIE_TOOLS` and `_archie_tools`).

AG-5 adds two things around these steps:
- The Analyst starts in parallel with Archie, on the fan's own words, so its data result is ready, or nearly, when
  Archie asks for it: one model call less to wait for on a question with numbers (`ARCHIE_PREFETCH_ANALYST`, on by
  default). A run that Archie does not use is stopped or discarded, and its tokens still count;
- when Archie gives The Analyst's numbers without a `chart`, the server points to the Data & Statistics table that
  holds them (`table_for`), never to one that the fan's version does not show.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.contract import tickets as tk
from app.contract.models import ArchieAnswer, ArchieRequest, Mode, Phase, Section
from app.contract.phases import is_unlocked, next_unlock

from . import analyst_tools as at
from . import context as ctx_module
from . import foundry
from . import grounding
from . import kitman as km
from .settings import FoundrySettings, cost_usd

ROLES = {"Archie": "orchestrator and voice", "The Analyst": "statistics via tools",
         "Kit Man": "matchday documents (file search)"}
MAX_ANALYST_CALLS = 2
MAX_KITMAN_CALLS = 1  # Kit Man reads about 10,000 tokens per question (credit rules)
# AG-5 (path B): the Data & Statistics table that holds each tool's numbers, for an answer without a `chart`.
# expected_goals and the player tools depend on the fan's version (table_for); the other tools are in the timeline.
TOOL_TABLE = {"distance": "running_table", "speeds": "running_table", "passes": "ball_table",
              "shots": "match_table", "possession": "match_table", "team_stats": "match_table"}

SAFE_LINE = {  # the answer when a number could not be grounded twice: no number at all
    "en": "A fine question, and I won't guess at it. Let me get the lads in the data room on that one.",
    "it": "Bella domanda, e non tiro a indovinare. Lo chiedo ai ragazzi della sala dati.",
    "es": "Buena pregunta, y no voy a adivinar. Se lo consulto a los chicos de la sala de datos.",
    "fr": "Belle question, et je ne vais pas deviner. Je demande aux gars de la salle des données.",
    "de": "Gute Frage, und raten werde ich nicht. Ich frage die Jungs im Datenraum.",
    "nl": "Mooie vraag, en ik ga niet gokken. Ik vraag het even na bij de jongens van de datakamer.",
    "ru": "Хороший вопрос, и гадать я не буду. Уточню у ребят из аналитического отдела.",
    "zh": "好问题，我不会乱猜。我去问问数据室的伙计们。",
}
LINE_DOWN = {  # the deterministic card when Foundry fails: the engine's scoreboard
    "en": "My line to the commentary box has dropped for a moment. The scoreboard: {score}.",
    "it": "La linea con la cabina di commento è caduta per un momento. Il punteggio: {score}.",
    "es": "Se me ha cortado un momento la línea con la cabina. El marcador: {score}.",
    "fr": "Ma ligne avec la cabine de commentaire a sauté un instant. Le score : {score}.",
    "de": "Meine Leitung in die Sprecherkabine ist kurz weg. Der Spielstand: {score}.",
    "nl": "Mijn lijn naar de commentaarcabine is even weg. De stand: {score}.",
    "ru": "Связь с комментаторской кабиной ненадолго пропала. Счёт: {score}.",
    "zh": "解说席的线路暂时断了。比分：{score}。",
}
SEE_DATA = {
    "en": " Every number is in Data & Statistics.", "it": " Tutti i numeri sono in Data & Statistics.",
    "es": " Todos los números están en Data & Statistics.", "fr": " Tous les chiffres sont dans Data & Statistics.",
    "de": " Alle Zahlen findest du unter Data & Statistics.", "nl": " Alle cijfers staan in Data & Statistics.",
    "ru": " Все цифры в разделе Data & Statistics.", "zh": "所有数据都在 Data & Statistics。",
}


def _lang(language: str, table: dict[str, str]) -> str:
    return table.get(language.split("-")[0].lower(), table["en"])


@dataclass
class _Step:
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


@dataclass
class Trace:
    """Everything one question did, for grounding and the Inspector."""

    steps: dict[str, _Step] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)  # contract ToolCall, in order
    sources: list[grounding.Source] = field(default_factory=list)  # numbers of the tool results
    data_results: list[str] = field(default_factory=list)  # The Analyst's replies to Archie
    documents: list[str] = field(default_factory=list)  # Kit Man's documents (evidence in the Inspector)
    notes: list[str] = field(default_factory=list)  # sentences for the Inspector (AG-5)
    runs: list[at.ToolRun] = field(default_factory=list)  # The Analyst's tool runs, for the server's table pointer
    early: asyncio.Task | None = None  # The Analyst started in parallel (AG-5), until Archie's first call takes it
    side: Trace | None = None  # ... its steps and tool calls, kept apart until then
    unused: Trace | None = None  # ... or never taken: spent all the same, so in the total and the cost

    @property
    def tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.steps.values())

    def spent(self) -> int:
        """The tokens of the question so far, The Analyst's parallel run included: one budget for both."""
        return self.tokens + (self.side.tokens if self.side else 0)

    def usage(self, agent: str, model: str, budget: int, spent: Callable[[], int] | None = None) -> foundry.Usage:
        step = self.steps.setdefault(agent, _Step(model))

        def record(tin: int, tout: int, ms: int) -> None:
            step.tokens_in += tin
            step.tokens_out += tout
            step.latency_ms += ms

        return foundry.Usage(record, spent or self.spent, budget)

    def take(self, side: Trace) -> None:
        """Archie used The Analyst's parallel run: its steps, tool calls, sources and runs become this question's."""
        for name, st in side.steps.items():
            mine = self.steps.setdefault(name, _Step(st.model))
            mine.tokens_in += st.tokens_in
            mine.tokens_out += st.tokens_out
            mine.latency_ms += st.latency_ms
        self.calls += side.calls
        self.sources += side.sources
        self.runs += side.runs
        self.side = None


@dataclass
class Reply:
    text: str
    language: str | None
    in_scope: bool
    chart: str | None


def parse_reply(raw: str) -> Reply:
    """Archie's JSON reply (response format of the agent); plain text is accepted as the answer itself."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        d = json.loads(s)
        if isinstance(d, dict) and isinstance(d.get("text"), str):
            chart = d.get("chart") if d.get("chart") in foundry.CHART_IDS else None
            return Reply(d["text"].strip(), d.get("language") or None, d.get("in_scope") is not False, chart)
    except json.JSONDecodeError:
        pass
    return Reply(raw.strip(), None, True, None)


def _pointer(chart: str | None, phase: Phase, mode: Mode) -> dict | None:
    if chart is None:
        return None
    analyzer_only = chart in foundry.ANALYZER_TARGETS and mode is not Mode.analyst
    nxt = next_unlock(Section.data_stats, phase)
    return {"chart_id": chart, "section": Section.data_stats.value,
            "locked": analyzer_only or not is_unlocked(Section.data_stats, phase),
            "unlocks_at_phase": None if analyzer_only or nxt is None else nxt.value}


def table_for(runs: list[at.ToolRun], session: at.AnalystSession) -> str | None:
    """AG-5, path B: the Data & Statistics table that holds the numbers of The Analyst's first tool that has one, for
    an answer without a `chart`. Never a table or chart that the fan's version does not show."""
    for r in runs:
        if not r.ok:
            continue
        if r.name == "expected_goals":
            return "xg_cumulative" if session.analyzer else "match_table"  # Fan: the "Goals vs xG" row
        if r.name in ("player_stats", "compare_players"):
            ids = {r.result[k]["player_id"] for k in ("player", "a", "b") if isinstance(r.result.get(k), dict)}
            if ids and ids <= set(session.favourites):
                return "player_focus"
            if session.analyzer:
                return "players_table"
            continue
        if r.name in TOOL_TABLE:
            return TOOL_TABLE[r.name]
    return None


def _message(match_context: str, fan: str, correction: str = "") -> str:
    fan = fan.replace("</fan>", "")  # the fan's words cannot close their own block
    return f"{match_context}\n\n<fan>\n{fan}\n</fan>" + (f"\n\n{correction}" if correction else "")


def _correction(unmatched: list[str], data_results: list[str]) -> str:
    data = "\n".join(f"⟨The Analyst: {d}⟩" for d in data_results) or "(none)"
    return ("# CHECK BY THE SERVER\n"
            f"Your last answer said numbers that are not in your data results or in the MATCH CONTEXT: "
            f"{grounding.explain(unmatched)}.\nData results you already have:\n{data}\n"
            "Answer the fan again using only numbers from these data results or the MATCH CONTEXT. "
            "If a number is missing, say you'll check with the team.")


class ArchieService:
    """Ask Archie on Foundry. `projects` gives the (async) AIProjectClient; the tests pass a fake one."""

    def __init__(self, settings: FoundrySettings, projects: Callable[[], Any] | None = None) -> None:
        self.settings = settings
        self.projects = projects or foundry.Projects(settings)

    async def close(self) -> None:
        close = getattr(self.projects, "close", None)
        if close is not None:
            await close()

    # -------------------------------------------------------------- the two agents
    def _analyst_tools(self, session: at.AnalystSession, trace: Trace) -> list:
        def bind(name: str):
            def run(**arguments: Any) -> str:
                r = at.run_tool(session, name, arguments)  # the fan's session: no future, ticket version and match
                trace.runs.append(r)
                trace.calls.append(r.tool_call())
                trace.sources += grounding.tool_sources(name, r.arguments, r.result)
                return foundry.dumps(r.result)
            return run
        return [foundry.local_tool(t.name, t.description, bind(t.name), list(t.parameters["properties"]))
                for t in at.TOOLS.values()]

    def _archie_tools(self, session: at.AnalystSession, trace: Trace, match_context: str) -> list:
        s = self.settings

        async def ask_the_analyst(question: str = "", **_: Any) -> str:
            t0 = time.perf_counter()
            asked = sum(c["tool"] == "ask_the_analyst" for c in trace.calls)
            early, trace.early = trace.early, None  # the parallel run answers Archie's first call only
            reply = await self._take_early(early, trace) if early else None
            if reply is None and (asked >= MAX_ANALYST_CALLS or trace.spent() >= s.max_tokens):
                reply = "No more data for this question (limit reached): answer with what you have."
            elif reply is None:
                reply = await self._run_analyst(session, trace, match_context, question)
                trace.data_results.append(reply)
            trace.calls.append({"agent": "Archie", "tool": "ask_the_analyst", "arguments": {"question": question[:200]},
                                "result_summary": reply[:200], "latency_ms": int((time.perf_counter() - t0) * 1000)})
            return reply

        kit_calls = [0]  # counted before the call: Archie can ask for two tools in parallel

        async def ask_kit_man(question: str = "", **_: Any) -> str:
            kit_calls[0] += 1
            if kit_calls[0] > MAX_KITMAN_CALLS or trace.spent() >= s.max_tokens:
                a = km.KitManAnswer(text="No more Kit Man for this question (limit reached): answer with what you "
                                         "have.", ok=False)
            else:
                a = await km.ask_kitman(self.projects(), s, question)
            if a.ok:
                step = trace.steps.setdefault("Kit Man", _Step(s.model))
                step.tokens_in += a.tokens_in
                step.tokens_out += a.tokens_out
                step.latency_ms += a.latency_ms
                trace.sources += kitman_sources(a)
                trace.documents += [d for d in a.citations if d not in trace.documents]
            trace.calls.append({"agent": "Archie", "tool": "ask_kit_man", "arguments": {"question": question[:200]},
                                "result_summary": (a.text if not a.citations else
                                                   f"{a.text[:150]} [{', '.join(a.citations)}]")[:200],
                                "latency_ms": a.latency_ms})
            return a.text + (f"\n(Documents: {', '.join(a.citations)})" if a.citations else "")

        return [foundry.local_tool("ask_the_analyst", foundry.ASK_THE_ANALYST["description"], ask_the_analyst,
                                   ["question"]),
                foundry.local_tool("ask_kit_man", foundry.ASK_KIT_MAN["description"], ask_kit_man, ["question"])]

    async def _archie(self, session: at.AnalystSession, trace: Trace, match_context: str, message: str) -> Reply:
        s = self.settings
        archie = foundry.agent(s, self.projects(), s.archie_agent, s.archie_version,
                               self._archie_tools(session, trace, match_context),
                               trace.usage("Archie", s.model, s.max_tokens))
        result = await archie.run(message)
        return parse_reply(result.text or "")

    async def _run_analyst(self, session: at.AnalystSession, trace: Trace, match_context: str, question: str,
                           spent: Callable[[], int] | None = None) -> str:
        """One run of The Analyst, recorded in `trace`; its tools run here with the fan's session."""
        s = self.settings
        analyst = foundry.agent(s, self.projects(), s.analyst_agent, s.analyst_version,
                                self._analyst_tools(session, trace),
                                trace.usage("The Analyst", s.analyst_deployment, s.max_tokens, spent))
        result = await analyst.run(f"{match_context}\n\nArchie asks: {question.strip()[:500]}")
        return (result.text or "").strip() or "No data for this question."

    # -------------------------------------------------------------- The Analyst in parallel (AG-5)
    def _start_analyst(self, session: at.AnalystSession, trace: Trace, match_context: str, fan: str) -> None:
        """The Analyst starts on the fan's own words while Archie reads the question, in a trace of its own until
        Archie's first `ask_the_analyst` takes it (the budget of the question counts both)."""
        trace.side = Trace()
        trace.early = asyncio.create_task(self._run_analyst(session, trace.side, match_context, fan, trace.spent))

    @staticmethod
    async def _take_early(early: asyncio.Task, trace: Trace) -> str | None:
        """The parallel run's data result for Archie's first call, or None if it failed (the call then runs)."""
        try:
            reply = await early
        except Exception:  # noqa: BLE001 - Foundry error or token budget: Archie's own call runs instead
            reply = None
        if trace.side is not None:
            trace.take(trace.side)
        if reply is not None:
            trace.data_results.append(reply)
            trace.notes.append("The Analyst had started in parallel with Archie, so its data result was ready "
                               "sooner.")
        return reply

    @staticmethod
    async def _settle(trace: Trace) -> None:
        """After the answer: a parallel run that Archie did not use is stopped if still running; its tokens were
        spent, so they stay in the total and the cost, with a note."""
        early, trace.early = trace.early, None
        if early is None:
            return
        stopped = not early.done()
        if stopped:
            early.cancel()
        await asyncio.gather(early, return_exceptions=True)
        side, trace.side = trace.side, None
        if side is not None and side.tokens:
            trace.unused = side
        if side is not None and (stopped or side.tokens):
            trace.notes.append("The Analyst had started in parallel with Archie to save time; this question did not "
                               + ("need it, so it was stopped." if stopped else
                                  f"need it (its {side.tokens} tokens are in the total)."))

    # -------------------------------------------------------------- one question
    async def ask(self, session: at.AnalystSession, state, body: ArchieRequest, inspector: bool) -> dict:
        t0 = time.perf_counter()
        language = body.language or state.preferences.language
        mc = ctx_module.build(session, list(state.preferences.favourite_player_ids), language)
        ctx_sources = grounding.context_sources(mc.numbers) + grounding.lineup_sources(mc.shirts)
        trace = Trace()
        if self.settings.prefetch_analyst:
            self._start_analyst(session, trace, mc.text, body.message)
        method: list[str] = []
        try:
            reply = await self._archie(session, trace, mc.text, _message(mc.text, body.message))
            check = grounding.check(reply.text, trace.sources + ctx_sources)
            if not check.passed:
                method.append(f"the grounding validator rejected {grounding.explain(check.unmatched)}, so Archie "
                              "wrote the answer again with the data results")
                reply = await self._archie(session, trace, mc.text, _message(
                    mc.text, body.message, _correction(check.unmatched, trace.data_results)))
                check = grounding.check(reply.text, trace.sources + ctx_sources)
                if not check.passed:
                    method.append(f"still not grounded ({grounding.explain(check.unmatched)}): the answer was "
                                  "discarded and Archie said he would check with the team")
                    reply = Reply(_lang(reply.language or language, SAFE_LINE), reply.language or language, True,
                                  None)
                    check = grounding.Check(False, [], check.unmatched, check.checked)
            fallback = False
        except Exception as exc:  # Foundry down, timeout, token budget: the deterministic card, never a raw error
            fallback = True
            reply, check = self._card(session, mc, language, ctx_sources)
            method.append(f"Foundry did not answer ({type(exc).__name__}), so the app showed the deterministic "
                          "scoreboard card")
        await self._settle(trace)
        chart = reply.chart
        if (chart is None and not fallback and reply.in_scope and check.passed
                and is_unlocked(Section.data_stats, mc.phase)):
            chart = table_for(trace.runs, session)
            if chart:
                trace.notes.append(f"The server pointed to {chart} in Data & Statistics, where The Analyst's "
                                   "numbers are.")
        answer = {
            "answer_id": f"ANS-{uuid.uuid4().hex[:12]}", "text": reply.text, "language": reply.language or language,
            "in_scope": reply.in_scope, "grounding": check.grounding,
            "points_to": _pointer(chart, mc.phase, session.mode),
            "inspector": self._inspector(trace, check, method, fallback, session.analyzer, t0) if inspector else None,
        }
        return ArchieAnswer.model_validate(answer).model_dump(mode="json")

    def _card(self, session: at.AnalystSession, mc: ctx_module.MatchContext, language: str,
              sources: list[grounding.Source]) -> tuple[Reply, grounding.Check]:
        score = at.match_score(session)
        t = list(score["teams"].values())
        line = f"{t[0]} {score['score']['home']}–{score['score']['away']} {t[1]}"
        if mc.phase in (Phase.first_half, Phase.second_half):
            line += f", {score['minute']}'"
        text = _lang(language, LINE_DOWN).format(score=line)
        open_data = is_unlocked(Section.data_stats, mc.phase)
        if open_data:
            text += _lang(language, SEE_DATA)
        reply = Reply(text, language, True, "match_table" if open_data else None)
        return reply, grounding.check(text, sources)

    def _inspector(self, trace: Trace, check: grounding.Check, method: list[str], fallback: bool, analyzer: bool,
                   t0: float) -> dict:
        agents = [{"agent": name, "model": st.model, "role": ROLES[name], "tokens_in": st.tokens_in,
                   "tokens_out": st.tokens_out, "latency_ms": st.latency_ms} for name, st in trace.steps.items()]
        analyst_runs = [c for c in trace.calls if c["agent"] == at.AGENT]
        if fallback:
            what = "Deterministic card from the engine (no model)."
        elif "The Analyst" in trace.steps:
            what = (f"Archie routed the question to The Analyst, which called {len(analyst_runs)} deterministic "
                    f"tool{'s' if len(analyst_runs) != 1 else ''} with the fan's session (no future, "
                    f"{'Analyst' if analyzer else 'Fan'} version).")
        elif "Kit Man" in trace.steps:
            what = "Archie asked Kit Man, who searched the matchday documents."
        else:
            what = "Archie answered from the server's MATCH CONTEXT, without tools."
        if "Kit Man" in trace.steps and "The Analyst" in trace.steps:
            what += " Archie also asked Kit Man, who searched the matchday documents."
        if trace.notes:
            what += " " + " ".join(trace.notes)
        what += " " + ("Grounding: every number matched a tool result or the MATCH CONTEXT." if check.passed and
                       not method else "")
        if method:
            what += " Then " + "; ".join(method) + "."
        unused = list(trace.unused.steps.values()) if trace.unused else []
        cost = sum(cost_usd(st.model, st.tokens_in, st.tokens_out) for st in [*trace.steps.values(), *unused])
        return {"trace_id": f"archie-{uuid.uuid4().hex[:16]}", "method": what.strip(), "agents": agents,
                "tool_calls": trace.calls, "evidence": ["match_context"] + [f"tool:{c['tool']}" for c in analyst_runs]
                + [f"kitman:{d}" for d in trace.documents],
                "grounding": check.contract(),
                "total_tokens": trace.tokens + (trace.unused.tokens if trace.unused else 0),
                "latency_ms": int((time.perf_counter() - t0) * 1000), "cost_usd": round(cost, 6)}


def session_for(engine, token: str, state) -> at.AnalystSession:
    return at.AnalystSession.from_state(engine, token, state)


def inspector_for(state) -> bool:
    return tk.TIER_RULES[state.tier].inspector


def kitman_sources(a: km.KitManAnswer) -> list[grounding.Source]:
    """The numbers of Kit Man's answer (founding years, capacity, shirt numbers): Archie may repeat them."""
    return [grounding.Source("ask_kit_man", {}, f"kitman.{i}", v) for i, n in enumerate(grounding.numbers_in(a.text))
            for v, _ in n.readings]  # every reading: "60,400" is 60400 or 60.4
