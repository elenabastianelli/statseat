"""The MATCH CONTEXT block (AG-1b): what Archie knows about the fan's ticket before calling any tool.

The server builds it for every question from the session: the match on the ticket (never chosen by the model), the
current minute and phase of the fan's clock, the score and scorers, the people of the Pre-match overview, the
fan's version (Fan or Analyst), language and favourites, and which app sections are open. For a Fan ticket it also
lists the tables and charts of the Analyst version only (AG-5), so Archie knows what the fan sees locked. Its numbers
are grounded like a tool result (`tool="match_context"`), so the validator accepts them in Archie's answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.contract import mock
from app.contract.models import ANALYZER_TABLES, Phase, Section
from app.contract.phases import is_unlocked, next_unlock
from app.engine.sources.base import TEAM_ID
from app.engine.stats import CHARTS

from . import analyst_tools as at
from .grounding import numbers_in  # the same reader as Archie's answer

TOOL = "match_context"
SECTION_NAMES = {Section.pre_match: "Pre-match", Section.live: "Live", Section.data_stats: "Data & Statistics",
                 Section.games: "Games", Section.recap: "Recap", Section.ask_archie: "Ask Archie"}
OPENS = {Phase.first_half: "at kick-off", Phase.half_time: "at half-time", Phase.second_half: "in the second half",
         Phase.full_time: "after the final whistle"}
LANGUAGES = {"en": "English", "it": "Italian", "es": "Spanish", "fr": "French", "de": "German", "nl": "Dutch",
             "ru": "Russian", "zh": "Chinese"}
TABLE_TITLES = {"players_table": "All players"}  # as the app titles them (web/src/i18n/en.json)
# The engine still computes acceleration load, but the app does not show it (unreliable data): Archie never sees it.
NOT_IN_APP = {"acceleration_load"}
ARCHIE_CHARTS = [c for c in CHARTS if c[0] not in NOT_IN_APP]


@dataclass(frozen=True)
class MatchContext:
    text: str  # the block sent with the fan's message
    numbers: dict[str, list[float]]  # line label -> the numbers written on that line (grounding source)
    shirts: dict[str, int]  # player id -> shirt number, from the engine's line-ups (grounding source)
    phase: Phase
    language: str


def _overview(match_id: str) -> dict | None:
    """The Pre-match overview (coaches, captains, officials) when it describes the engine's match."""
    m = json.loads((mock.FAKE / "match.json").read_text(encoding="utf-8"))
    return m if m["match_id"] == match_id else None


def _phase_line(phase: Phase, minute: int, added: int, kickoff: str | None) -> str:
    clock = f"{minute}+{added}'" if added else f"{minute}'"
    return {Phase.pre_match: "PRE-MATCH, not kicked off yet" + (f" (kick-off {kickoff})" if kickoff else ""),
            Phase.first_half: f"LIVE, first half, {clock}", Phase.half_time: "HALF-TIME",
            Phase.second_half: f"LIVE, second half, {clock}", Phase.full_time: "FULL TIME"}[phase]


def build(session: at.AnalystSession, favourites: list[str], language: str) -> MatchContext:
    """The MATCH CONTEXT of this session at this instant (the same window as the tools)."""
    score = at.match_score(session)  # the scoreboard of /api/match/state, same instant as the tools
    phase, snap = Phase(score["phase"]), session.engine.snapshot(session.token)
    lineup = {p.player_id: p for p in snap.lineup}
    shirts = {pid: p.shirt_number for pid, p in lineup.items()}
    teams = score["teams"]  # BRA -> Brackwater Athletic
    home, away = (teams[t] for t in at.TEAMS)
    ov = _overview(session.match_id)
    lines: list[tuple[str, str]] = []
    kickoff = None
    if ov:
        ko = datetime.fromisoformat(ov["kickoff"])
        kickoff = ko.strftime("%H:%M")
        lines += [("Match", f'{home} v {away}, "{ov["title"]}". {ov["competition"]} {ov["season"]}, matchday '
                            f'{ov["matchday"]}.'),
                  ("Stadium", ov["stadium_name"] + "."),
                  ("Kick-off", f"{ko.strftime('%A')} {ko.day} {ko.strftime('%B %Y, %H:%M')}.")]
    else:
        lines.append(("Match", f"{home} v {away} ({session.match_id})."))
    lines.append(("Phase", _phase_line(phase, score["minute"], score["added_time"], kickoff) + "."))
    s = score["score"]
    lines.append(("Score", f"{home} {s['home']}–{s['away']} {away}."))
    if score["goals"]:
        goals = [f"{g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}' "
                 f"#{shirts.get(g['player_id'], '?')} {g['player']} ({teams[g['team']]})"
                 + (" (penalty)" if g["penalty"] else "") for g in score["goals"]]
        lines.append(("Goals", "; ".join(goals) + "."))
    if ov:
        by_id = {t["team_id"]: t for t in ov["teams"]}
        lines.append(("Head coaches", ", ".join(f"{by_id[t]['head_coach']} ({teams[t]})" for t in at.TEAMS) + "."))
        caps = [(t, p) for t in at.TEAMS for p in by_id[t]["lineup"] if p.get("captain")]
        if caps:
            lines.append(("Captains today", ", ".join(f"#{p['shirt_number']} {p['name']} ({teams[t]})"
                                                      for t, p in caps) + "."))
        ref = next((o["name"] for o in ov["officials"] if o["role"] == "referee"), None)
        if ref:
            lines.append(("Referee", ref + "."))
    favs = [f"#{lineup[p].shirt_number} {session.player_names.get(p, p)} ({teams[TEAM_ID[lineup[p].team_side]]})"
            for p in favourites if p in lineup]
    lang = LANGUAGES.get(language.split("-")[0].lower(), language)
    lines.append(("Fan", f"{'Analyst' if session.analyzer else 'Fan'} version; app language {lang}; "
                         f"favourite players: {', '.join(favs) if favs else 'none'}."))
    if not session.analyzer:
        locked = [f"{TABLE_TITLES[t]} table ({t})" for t in ANALYZER_TABLES] + [
            f"{title} chart ({c})" for c, title, modes, _ in ARCHIE_CHARTS if "casual" not in modes]
        lines.append(("Analyst only", "locked on this Fan ticket: " + ", ".join(locked)
                      + '; and every number that The Analyst marks "Analyst only".'))
    open_now = [SECTION_NAMES[x] for x in Section if is_unlocked(x, phase)]
    later = [f"{SECTION_NAMES[x]} opens {OPENS[n]}" for x in Section if (n := next_unlock(x, phase))]
    closed = [SECTION_NAMES[x] for x in Section if not is_unlocked(x, phase) and not next_unlock(x, phase)]
    lines.append(("Sections", "open now: " + ", ".join(open_now) + "".join(f"; {x}" for x in later)
                              + (f"; closed for today: {', '.join(closed)}" if closed else "") + "."))
    text = "# MATCH CONTEXT\n" + "\n".join(f"- {k}: {v}" for k, v in lines)
    return MatchContext(text, {k: [n.value for n in numbers_in(v)] for k, v in lines}, shirts, phase,
                        language)
