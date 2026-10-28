"""The Analyst's tools (task AG-1a, first half of S-2): the deterministic functions behind every number Archie says.

Each tool is a pure function `tool(session, **arguments) -> dict` that reads the match only through the engine's
no-look-ahead window (`engine.snapshot(token)`, then `Engine.core` / `Engine.live_core`, the same choice the server
makes) and builds its result from the payloads of `app/engine/stats.py`: the numbers are the ones the app shows at the
same instant, computed by the same code. Rules shared by every tool:

- **no future**: only data up to the session's instant (`as_of_s` in every result); an argument cannot ask for more;
- **ticket version**: with a Fan ticket (Casual mode) a number that the app shows only in Analyst is null with the
  reason `ANALYZER_ONLY`, so Archie cannot give it away; the version comes from the session, never from an argument;
- **match scope**: the `match_id` comes from the session (the ticket), never from an argument;
- **no invented numbers**: a number that does not exist is null, and `unavailable` lists its path and the reason
  (as `unavailable` in the contract);
- **the referee's call stands**: `offside_check` gives the official decision next to the tracking check;
- **stable paths**: every number has a fixed path in the result (e.g. `distance.top_runner.km`), which AG-1b uses as
  `GroundedValue.source_field`; `numeric_fields` lists them.

`run_tool(session, name, arguments)` validates the arguments against the tool's JSON schema, runs it and returns the
result with a short summary for `ToolCall.result_summary`; a wrong argument gives an `error` with a clear message,
never an exception. `function_tools()` gives the definitions for function calling on Foundry. No model or network call
happens here.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.contract import tickets as tk
from app.contract.models import Mode, Phase
from app.engine import frame_stats, offside, stats
from app.engine.engine import Engine
from app.engine.sources.base import AWAY, HOME, TEAM_ID, Event
from app.engine.window import Snapshot

AGENT = "The Analyst"
PLAY = (Phase.first_half, Phase.second_half)
TEAMS = tuple(TEAM_ID[s] for s in (HOME, AWAY))  # ("BRA", "CAL")
ANALYZER_ONLY = "Analyst only: not in a Fan ticket (Unlock with Gold)"
PLAYER_ANALYZER_ONLY = "Analyst only for this player: a Fan ticket shows it for the fan's favourites and the leaders"
NOT_STARTED = "the match has not kicked off yet"
NOT_COMPUTED = "not computed by the engine yet"
EVENT_KINDS = ("all", "goal", "shot", "penalty", "card", "substitution", "corner", "foul", "var")
SESSION_ONLY = {"match_id": "The match is the one on the fan's ticket; other matches are out of scope.",
                "mode": "The version (Fan or Analyst) comes from the fan's ticket.",
                "as_of_s": "Data is always up to the current minute of the match, never later."}


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class AnalystSession:
    """What the tools know about the fan: the engine, the session token (its clock), the version and favourites."""

    engine: Engine
    token: str
    match_id: str
    mode: Mode  # casual = Fan, analyst = Analyst
    favourites: tuple[str, ...] = ()
    player_names: Mapping[str, str] = field(default_factory=dict)
    team_names: Mapping[str, str] = field(default_factory=dict)  # side -> display name

    @classmethod
    def from_state(cls, engine: Engine, token: str, state, player_names: Mapping[str, str] | None = None,
                   team_names: Mapping[str, str] | None = None) -> "AnalystSession":
        """From the server's session (`app.contract.mock.SessionState`): mode as the app shows it, never beyond the
        ticket tier. The ticket's match must be the engine's (one match per deployment)."""
        if player_names is None or team_names is None:
            from app.engine.server import _names  # lazy: the server will import this module (AG-1b)
            names, teams = _names()
            player_names, team_names = player_names or names, team_names or teams
        rules = tk.TIER_RULES[state.tier]
        mode = state.preferences.mode if state.preferences.mode in rules.allowed_modes else rules.default_mode
        match_id = engine.snapshot(token).match_id
        if state.match_id != match_id:
            raise ValueError(f"ticket for {state.match_id}, engine on {match_id}")
        return cls(engine, token, match_id, mode, tuple(state.preferences.favourite_player_ids), player_names,
                   team_names)

    @property
    def analyzer(self) -> bool:
        return self.mode is Mode.analyst


# ------------------------------------------------------------------ reading the engine (same choices as server.py)


def _snap(s: AnalystSession) -> Snapshot:
    return s.engine.snapshot(s.token)


def _core(s: AnalystSession, snap: Snapshot) -> stats.Core:
    """During play the numbers come from the live tick, as for /api/stats and /api/stats/live."""
    return s.engine.live_core(snap) if snap.phase in PLAY else s.engine.core(snap)


def _stats(s: AnalystSession, snap: Snapshot) -> tuple[stats.Core, dict] | None:
    """The Data & Statistics payload of the session's version (None before kick-off, when the section is closed)."""
    if snap.phase is Phase.pre_match:
        return None
    core = _core(s, snap)
    return core, stats.match_stats(core, s.mode, False, datetime.now(timezone.utc), list(s.favourites))


def _header(s: AnalystSession, snap: Snapshot) -> dict:
    return {"phase": snap.phase.value, "minute": stats.scoreboard(snap)[0], "as_of_s": round(snap.as_of_s, 1),
            "version": "Analyst" if s.analyzer else "Fan"}


class _Out:
    """A result under construction: the header, the body and the `unavailable` list (path, reason)."""

    def __init__(self, s: AnalystSession, snap: Snapshot) -> None:
        self.d = _header(s, snap)
        self.unavailable: list[dict] = []

    def null(self, path: str, reason: str) -> None:
        if not any(u["field"] == path for u in self.unavailable):
            self.unavailable.append({"field": path, "reason": reason})

    def put(self, obj: dict, key: str, value: Any, path: str, reason: str | None) -> None:
        obj[key] = value
        if value is None:
            self.null(path, reason or NOT_COMPUTED)

    def done(self) -> dict:
        self.d["unavailable"] = self.unavailable
        return self.d


def _why(payload: dict) -> dict[str, str]:
    return {u["metric"]: u["reason"] for u in payload["unavailable"]}


def _by_team(items: list[dict], key: str = "team_id") -> dict[str, dict]:
    return {x[key]: x for x in items}


# ------------------------------------------------------------------ players and teams


def _team(s: AnalystSession, ref: str) -> str:
    low = ref.strip().lower()
    for side, tid in TEAM_ID.items():
        name = s.team_names.get(side, "").lower()
        if low in (tid.lower(), side, name) or (name and low in name.split()):
            return tid
    raise ToolError("team_not_found", f"No team '{ref}' in this match: use {TEAMS[0]} or {TEAMS[1]}.")


def _roster(s: AnalystSession, snap: Snapshot) -> list[dict]:
    return [{"player_id": p.player_id, "player": s.player_names.get(p.player_id, p.player_id),
             "team": TEAM_ID[p.team_side], "shirt_number": p.shirt_number, "position": p.position,
             "started": p.starting} for p in snap.lineup]


def _player(s: AnalystSession, snap: Snapshot, ref: str) -> dict:
    """A player of this match from an id (BRA-09), a team and shirt number ('BRA #9') or a name or surname."""
    roster, low = _roster(s, snap), " ".join(ref.strip().lower().split())
    hits = [p for p in roster if p["player_id"].lower() == low]
    if not hits:
        m = re.fullmatch(r"(?P<team>.*?)\s*(?:#|no\.?|number)?\s*(?P<num>\d{1,2})", low)
        if m:
            team = _team(s, m["team"]) if m["team"].strip() else None
            hits = [p for p in roster if p["shirt_number"] == int(m["num"]) and team in (None, p["team"])]
    if not hits:
        hits = [p for p in roster if p["player"].lower() == low]
    if not hits and low:
        hits = [p for p in roster if all(w in p["player"].lower().split() for w in low.split())]
    if not hits:
        raise ToolError("player_not_found", f"No player '{ref}' in this match. Use a name, an id like BRA-09 or a "
                                            "team and shirt number like 'BRA #9'.")
    if len(hits) > 1:
        raise ToolError("ambiguous_player", f"'{ref}' matches several players: "
                        + ", ".join(f"{p['player']} ({p['player_id']})" for p in hits) + ".")
    return hits[0]


def _name(s: AnalystSession, pid: str | None) -> str | None:
    return s.player_names.get(pid, pid) if pid else None


# ------------------------------------------------------------------ tools


def match_score(s: AnalystSession) -> dict:
    """Score, phase, minute and scorers: the scoreboard of /api/match/state and the goals of the event feed."""
    snap = _snap(s)
    st = stats.match_state(snap, datetime.now(timezone.utc))
    o = _Out(s, snap)
    o.d.update(minute=st["minute"], added_time=st["added_time"], score=st["score"],
               teams={TEAM_ID[side]: s.team_names.get(side, TEAM_ID[side]) for side in (HOME, AWAY)})
    o.d["goals"] = [{"minute": e["minute"], "added_time": int(e["qualifiers"].get("added_time", 0)),
                     "team": e["team_id"], "player_id": e["player_id"], "player": _name(s, e["player_id"]),
                     "penalty": bool(e["qualifiers"].get("penalty", False))}
                    for e in stats.event_feed(snap)["events"]
                    if e["type"] == "ShotAtGoal" and e["qualifiers"].get("outcome") == "goal"]
    return o.done()


def _kind(e: dict) -> str | None:
    q = e["qualifiers"]
    return {"ShotAtGoal": "penalty" if q.get("penalty") else "goal" if q.get("outcome") == "goal" else "shot",
            "Caution": "card", "Substitution": "substitution", "CornerKick": "corner", "Foul": "foul",
            "VideoAssistantAction": "var"}.get(e["type"])


def match_events(s: AnalystSession, kind: str = "all", team: str | None = None, player: str | None = None,
                 from_minute: int | None = None, until_minute: int | None = None, last: int = 10) -> dict:
    """Key events of the Live timeline (/api/match/events) released so far, filtered; the latest `last`."""
    snap = _snap(s)
    o = _Out(s, snap)
    tid = _team(s, team) if team else None
    pid = _player(s, snap, player)["player_id"] if player else None
    if until_minute is not None and until_minute > o.d["minute"]:
        o.d["note"] = f"minute {until_minute} has not been played yet: events up to now only"
    items = []
    for e in stats.event_feed(snap)["events"]:
        k = _kind(e)
        q = e["qualifiers"]
        if k is None:
            continue
        if kind != "all" and not (k == kind or (kind == "shot" and k in ("goal", "penalty"))
                                  or (kind == "goal" and k == "penalty" and q.get("outcome") == "goal")):
            continue
        if (tid and e["team_id"] != tid) or (pid and pid not in (e["player_id"], e["secondary_player_id"])):
            continue
        if (from_minute is not None and e["minute"] < from_minute) or (until_minute is not None and e["minute"] > until_minute):
            continue
        it = {"minute": e["minute"], "added_time": int(q.get("added_time", 0)), "kind": k, "team": e["team_id"],
              "player_id": e["player_id"], "player": _name(s, e["player_id"]), "event_id": e["event_id"]}
        if e["type"] == "ShotAtGoal":
            it["outcome"] = q.get("outcome", "wide")
        elif k == "card":
            it["card"] = q.get("card", "yellow")
        elif k == "substitution":
            it.update(player_in_id=e["secondary_player_id"], player_in=_name(s, e["secondary_player_id"]))
        elif k == "foul" and e["secondary_player_id"]:
            it.update(fouled_id=e["secondary_player_id"], fouled=_name(s, e["secondary_player_id"]))
        items.append(it)
    o.d.update(total=len(items), events=items[-last:] if last else [])
    return o.done()


def team_stats(s: AnalystSession) -> dict:
    """The two teams side by side, from Data & Statistics (/api/stats) of the session's version."""
    snap = _snap(s)
    got = _stats(s, snap)
    if got is None:
        o = _Out(s, snap)
        o.d["teams"] = None
        o.null("teams", NOT_STARTED)
        return o.done()
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    shots, counts, passes = _by_team(p["shots"]), _by_team(p["counts"]), _by_team(p["passes"])
    poss = _by_team(p["possession"]["teams"]) if p["possession"] else {}
    dist = _by_team(p["distance_covered"]["teams"]) if p["distance_covered"] else {}
    circ = _by_team(p["ball_circulation"]["teams"]) if p["ball_circulation"] else {}
    counter = _by_team(p["counter_attacks"]["teams"]) if p["counter_attacks"] else {}
    ppda = _by_team(p["ppda"] or [])
    teams = {}
    for t in TEAMS:
        row, c, sh = {}, counts[t], shots[t]
        for k in ("shots", "on_target", "goals"):
            row[k] = sh[k]
        for k in ("penalties", "woodwork", "corners", "fouls", "offsides", "yellow_cards", "red_cards"):
            row[k] = c[k]
        o.put(row, "sprints", c["sprints"], "teams.*.sprints", why.get("counts.sprints"))
        o.put(row, "tackles", c["tackles"], "teams.*.tackles", why.get("counts.tackles"))
        o.put(row, "ball_recoveries", c["ball_recoveries"], "teams.*.ball_recoveries", why.get("counts.tackles"))
        o.put(row, "possession_pct", poss[t]["value"] if poss else None, "teams.*.possession_pct", why.get("possession"))
        o.put(row, "passes", passes[t]["passes"] if passes else None, "teams.*.passes", why.get("passes"))
        o.put(row, "pass_accuracy_pct", passes[t]["accuracy_pct"] if passes else None, "teams.*.pass_accuracy_pct",
              why.get("passes"))
        o.put(row, "distance_km", dist[t]["distance"]["km"] if dist else None, "teams.*.distance_km",
              why.get("distance_covered"))
        row["distance_approx"] = dist[t]["distance"]["approx"] if dist else None
        o.put(row, "ball_circulation_kmh", circ[t]["value"] if circ else None, "teams.*.ball_circulation_kmh",
              why.get("ball_circulation"))
        o.put(row, "counter_attacks", counter[t]["count"] if counter else None, "teams.*.counter_attacks",
              why.get("counter_attacks"))
        if not s.analyzer:
            o.put(row, "ppda", None, "teams.*.ppda", ANALYZER_ONLY)
        elif f"ppda.{t}" in why:  # only this team has no defensive action in its pressing zone yet
            o.put(row, "ppda", None, f"teams.{t}.ppda", why[f"ppda.{t}"])
        else:
            o.put(row, "ppda", ppda[t]["value"] if t in ppda else None, "teams.*.ppda", why.get("ppda"))
        teams[t] = row
    o.d["teams"] = teams
    return o.done()


def _not_started(s: AnalystSession, snap: Snapshot, key: str) -> dict:
    o = _Out(s, snap)
    o.put(o.d, key, None, key, NOT_STARTED)
    return o.done()


def _leader(s: AnalystSession, p: dict, key: str, keep: list[str]) -> dict | None:
    """One leader per team from `leaders` (the Fan tables), e.g. the top runner of each team."""
    out = {x["team_id"]: {"player_id": x[key]["player_id"], "player": _name(s, x[key]["player_id"]),
                          "value": x[key]["value"]} for x in p["leaders"] if x["team_id"] in keep and x[key]}
    return out or None


def shots(s: AnalystSession, team: str | None = None) -> dict:
    """Shots, on target, goals, penalties and woodwork per team, every shot so far and the hardest shot per team."""
    snap = _snap(s)
    tid = _team(s, team) if team else None
    got = _stats(s, snap)
    if got is None:
        return _not_started(s, snap, "shots")
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    counts = _by_team(p["counts"])
    keep = [t for t in TEAMS if tid in (None, t)]
    body: dict = {"teams": {t["team_id"]: {"shots": t["shots"], "on_target": t["on_target"], "goals": t["goals"],
                                           "penalties": counts[t["team_id"]]["penalties"],
                                           "woodwork": counts[t["team_id"]]["woodwork"]}
                            for t in p["shots"] if t["team_id"] in keep},
                  "list": [{"minute": x["minute"], "team": x["team_id"], "player_id": x["player_id"],
                            "player": _name(s, x["player_id"]), "outcome": x["outcome"], "penalty": x["is_penalty"],
                            "header": x["is_header"], "speed_kmh": x["speed_kmh"], "event_id": x["event_id"],
                            **(_shot_xg(x) if s.analyzer else {})}
                           for x in p["shot_map"]["shots"] if x["team_id"] in keep]}
    if s.analyzer and any(x["xg"] is None for x in body["list"]):
        o.null("shots.list[*].xg", why.get("xg") or NOT_COMPUTED)
    hardest = {x["team_id"]: {"player_id": x["player_id"], "player": _name(s, x["player_id"]), "kmh": x["kmh"],
                              "minute": x["minute"]} for x in p["shot_speed"] if x["team_id"] in keep}
    o.put(body, "hardest", hardest or None, "shots.hardest", why.get("shot_speed"))
    gx = p["goals_vs_xg"]
    o.put(body, "xg", {x["team_id"]: x["xg"] for x in gx if x["team_id"] in keep} if gx else None, "shots.xg",
          why.get("goals_vs_xg"))
    o.d["shots"] = body
    return o.done()


def _shot_xg(x: dict) -> dict:
    """A shot's xG and its chance word (METRICS.md decision 11: big, decent or half chance), Analyst only."""
    xg = x["xg"]
    return {"xg": xg, "chance": None if xg is None else frame_stats.metrics.chance_word(xg, frame_stats.CFG)}


def possession(s: AnalystSession) -> dict:
    """Possession per team, ball circulation speed in possession (km/h) and effective playing time."""
    snap = _snap(s)
    got = _stats(s, snap)
    if got is None:
        return _not_started(s, snap, "possession")
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    poss = p["possession"]
    o.put(o.d, "possession", {t["team_id"]: t["value"] for t in poss["teams"]} if poss else None, "possession",
          why.get("possession"))
    c = p["ball_circulation"]
    o.put(o.d, "ball_circulation_kmh", {t["team_id"]: t["value"] for t in c["teams"]} if c else None,
          "ball_circulation_kmh", why.get("ball_circulation"))
    eff = p["effective_playing_time"]
    o.put(o.d, "effective_playing_time", eff and {k: eff[k] for k in ("first_half_min", "second_half_min", "total_min",
                                                                       "played_min", "in_play_pct")},
          "effective_playing_time", why.get("effective_playing_time"))
    return o.done()


def passes(s: AnalystSession) -> dict:
    """Passes and pass accuracy per team, the top passer and the players with most touches of each team."""
    snap = _snap(s)
    got = _stats(s, snap)
    if got is None:
        return _not_started(s, snap, "passes")
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    o.put(o.d, "passes", {t["team_id"]: {"passes": t["passes"], "accuracy_pct": t["accuracy_pct"]} for t in p["passes"]}
          or None, "passes", why.get("passes"))
    o.put(o.d, "top_passer", _leader(s, p, "top_passer", list(TEAMS)), "top_passer", why.get("passes"))
    touches = {x["team_id"]: [{"player_id": y["player_id"], "player": _name(s, y["player_id"]), "touches": y["value"]}
                              for y in x["most_touches"]] for x in p["leaders"] if x["most_touches"]}
    o.put(o.d, "most_touches", touches or None, "most_touches", why.get("counts.tackles"))
    for k in ("avg_length_m", "long_balls"):
        if not s.analyzer:
            o.put(o.d, k, None, k, ANALYZER_ONLY)
            continue
        vals = {t["team_id"]: t[k] for t in p["passes"]}
        reason = why.get(f"passes.{k}") or why.get("passes")
        o.put(o.d, k, vals if any(v is not None for v in vals.values()) else None, k, reason)
        for t, v in vals.items():
            if v is None and o.d[k] is not None:
                o.null(f"{k}.{t}", reason)
    return o.done()


def _dist(d: dict) -> dict:
    return {"km": d["km"], "approx": d["approx"], "unmeasured_pct": d["unmeasured_pct"]}


def distance(s: AnalystSession, team: str | None = None, top: int = 5) -> dict:
    """Distance covered: per team, the top runner (of the match and of each team), the players who ran the most
    (Analyst)."""
    snap = _snap(s)
    tid = _team(s, team) if team else None
    got = _stats(s, snap)
    if got is None:
        return _not_started(s, snap, "distance")
    core, p = got
    o = _Out(s, core.snap)
    d = p["distance_covered"]
    if d is None:
        o.put(o.d, "distance", None, "distance", _why(p).get("distance_covered"))
        return o.done()
    keep = [t for t in TEAMS if tid in (None, t)]
    team_of = {x.player_id: TEAM_ID[x.team_side] for x in core.snap.lineup}
    body: dict = {"teams": {}}
    for t in d["teams"]:
        if t["team_id"] in keep:
            row = _dist(t["distance"])
            o.put(row, "splits", t["splits"], "distance.teams.*.splits", ANALYZER_ONLY)
            body["teams"][t["team_id"]] = row
    tr = d["top_runner"]
    body["top_runner"] = {"player_id": tr["player_id"], "player": _name(s, tr["player_id"]),
                          "team": team_of.get(tr["player_id"]), **_dist(tr["distance"])}
    o.put(body, "team_top_runners_km", _leader(s, p, "top_runner", keep), "distance.team_top_runners_km", NOT_COMPUTED)
    players = None
    if d["players"] is not None:
        players = [{"player_id": x["player_id"], "player": _name(s, x["player_id"]), "team": team_of.get(x["player_id"]),
                    **_dist(x["distance"])} for x in d["players"] if team_of.get(x["player_id"]) in keep][:top]
    o.put(body, "players", players, "distance.players", ANALYZER_ONLY)
    o.d["distance"] = body
    return o.done()


def speeds(s: AnalystSession, team: str | None = None) -> dict:
    """Top speeds (the fastest of each team; Analyst: 5 per team), sprints, top sprinters, speed zones (Analyst),
    running intensity."""
    snap = _snap(s)
    tid = _team(s, team) if team else None
    got = _stats(s, snap)
    if got is None:
        return _not_started(s, snap, "top_speed")
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    keep = [t for t in TEAMS if tid in (None, t)]
    ts = {t: [{"player_id": x["player_id"], "player": _name(s, x["player_id"]), "kmh": x["kmh"], "minute": x["minute"]}
              for x in p["top_speed"] if x["team_id"] == t] for t in keep}
    o.put(o.d, "top_speed", ts if any(ts.values()) else None, "top_speed", why.get("top_speed"))
    counts = _by_team(p["counts"])
    o.put(o.d, "sprints", {t: counts[t]["sprints"] for t in keep} if counts[TEAMS[0]]["sprints"] is not None else None,
          "sprints", why.get("counts.sprints"))
    o.put(o.d, "top_sprinter", _leader(s, p, "top_sprinter", keep), "top_sprinter", why.get("counts.sprints"))
    zones = p["speed_zones"]
    o.put(o.d, "speed_zones", {z["team_id"]: {k: v for k, v in z.items() if k.startswith("km_")} for z in zones
                               if z["team_id"] in keep} if zones else None, "speed_zones",
          why.get("speed_zones") if s.analyzer else ANALYZER_ONLY)
    peak = p["peak_intensity"]
    peaks = {x["team_id"]: {k: v for k, v in x.items() if k != "team_id"} for x in (peak or {}).get("teams", [])
             if x["team_id"] in keep}
    o.put(o.d, "peak_intensity", peaks or None, "peak_intensity", why.get("peak_intensity"))
    al = p["acceleration_load"]
    o.put(o.d, "acceleration_load", {x["team_id"]: {"total_kmh": x["total_kmh"], "per_minute_kmh": x["per_minute_kmh"]}
                                     for x in al["teams"] if x["team_id"] in keep} if al else None,
          "acceleration_load", why.get("acceleration_load"))
    return o.done()


def _player_body(s: AnalystSession, o: _Out, core: stats.Core, p: dict, who: dict, prefix: str) -> dict:
    """One player's numbers: the Player Focus card of the engine (`stats.player_focus`). In Fan, the numbers the Fan
    app shows only for favourites and team leaders are null for the other players."""
    pid = who["player_id"]
    card = stats.player_focus(core, pid, s.analyzer)
    body = dict(who)
    for k in ("minutes_played", "shots", "goals", "yellow_cards", "red_cards", "fouls_committed", "fouls_won"):
        body[k] = card[k]  # also in the Live timeline (shots, cards, fouls, substitutions)
    leaders = {x["team_id"]: x for x in p["leaders"]}.get(who["team"], {})

    def leads(key: str) -> bool:
        x = leaders.get(key)
        return bool(x) and x["player_id"] == pid

    full = s.analyzer or pid in s.favourites
    d = p["distance_covered"]
    see = {"distance": full or (d is not None and d["top_runner"]["player_id"] == pid),
           "top_speed_kmh": full or any(x["player_id"] == pid for x in p["top_speed"]),
           "sprints": full or leads("top_sprinter"), "passes": full or leads("top_passer"),
           "pass_accuracy_pct": full,
           "touches": full or any(y["player_id"] == pid for y in leaders.get("most_touches", [])),
           "tackles": full, "ball_recoveries": full}
    see["top_speed_minute"] = see["top_speed_kmh"]
    nf = stats.no_frames_reason(core.snap) if not core.fr else "no valid tracking samples for this player yet"
    no_passes = f"no pass events in source {core.snap.source} (key events only)"
    no_duels = f"no duel or recovery events in source {core.snap.source}"
    reasons = {"distance": nf, "top_speed_kmh": nf, "top_speed_minute": nf, "sprints": nf, "passes": no_passes,
               "pass_accuracy_pct": no_passes, "touches": no_duels, "tackles": no_duels, "ball_recoveries": no_duels}
    for k, visible in see.items():
        v = card[k] if visible else None
        o.put(body, k, _dist(v) if k == "distance" and v else v, f"{prefix}{k}",
              reasons[k] if visible else PLAYER_ANALYZER_ONLY)
    o.put(body, "acceleration_load_kmh", card["acceleration_load_kmh"] if full else None,
          f"{prefix}acceleration_load_kmh", nf if full else PLAYER_ANALYZER_ONLY)
    o.put(body, "xg", card["xg"] if s.analyzer else None, f"{prefix}xg", nf if s.analyzer else ANALYZER_ONLY)
    return body


def player_stats(s: AnalystSession, player: str) -> dict:
    """One player so far: minutes, distance, top speed, shots, goals, passes, cards and fouls."""
    snap = _snap(s)
    who = _player(s, snap, player)
    got = _stats(s, snap)
    if got is None:
        o = _Out(s, snap)
        o.d["player"] = who
        o.null("player.*", NOT_STARTED)
        return o.done()
    core, p = got
    o = _Out(s, core.snap)
    o.d["player"] = _player_body(s, o, core, p, who, "player.")
    return o.done()


def compare_players(s: AnalystSession, player_a: str, player_b: str) -> dict:
    """Two players side by side, with the same numbers as player_stats."""
    snap = _snap(s)
    a, b = _player(s, snap, player_a), _player(s, snap, player_b)
    if a["player_id"] == b["player_id"]:
        raise ToolError("same_player", "player_a and player_b are the same player.")
    got = _stats(s, snap)
    if got is None:
        o = _Out(s, snap)
        o.d.update(a=a, b=b)
        o.null("a.*", NOT_STARTED)
        o.null("b.*", NOT_STARTED)
        return o.done()
    core, p = got
    o = _Out(s, core.snap)
    o.d["a"] = _player_body(s, o, core, p, a, "a.")
    o.d["b"] = _player_body(s, o, core, p, b, "b.")
    return o.done()


def expected_goals(s: AnalystSession) -> dict:
    """Goals and xG per team (the "Goals vs xG" row of both versions); Analyst: each shot's xG and chance word."""
    snap = _snap(s)
    got = _stats(s, snap)
    if got is None:
        o = _Out(s, snap)
        o.d["goals"] = {t: 0 for t in TEAMS}
        o.put(o.d, "xg", None, "xg", NOT_STARTED)
        return o.done()
    core, p = got
    o = _Out(s, core.snap)
    why = _why(p)
    gx = p["goals_vs_xg"]
    if gx:
        o.d["goals"] = {x["team_id"]: x["goals"] for x in gx}
        o.d["xg"] = {x["team_id"]: x["xg"] for x in gx}
    else:
        sc = stats.match_state(core.snap, datetime.now(timezone.utc))["score"]
        o.d["goals"] = {TEAMS[0]: sc["home"], TEAMS[1]: sc["away"]}
        o.put(o.d, "xg", None, "xg", why.get("goals_vs_xg"))
    if s.analyzer:
        o.d["shots"] = [{"minute": x["minute"], "team": x["team_id"], "player_id": x["player_id"],
                         "player": _name(s, x["player_id"]), "outcome": x["outcome"], "penalty": x["is_penalty"],
                         "event_id": x["event_id"], **_shot_xg(x)} for x in p["shot_map"]["shots"]]
        if any(x["xg"] is None for x in o.d["shots"]):
            o.null("shots[*].xg", why.get("xg") or NOT_COMPUTED)
    else:
        o.put(o.d, "shots", None, "shots", ANALYZER_ONLY)
    return o.done()


OFFICIAL = {"offside": "offside given by the referee", "goal": "the goal stood"}
NOT_CONFIRMED = "our data cannot confirm the call: the referee's decision stands (never contradict it)"


def _checkable(snap: Snapshot) -> list[tuple[int, Event]]:
    """The released official offsides and goals with their minute (the Live feed has no `Offside` events)."""
    return [(Snapshot.minute(e.period, snap.clock_s(e.period, e.t))[0], e) for e in snap.events
            if e.type == "Offside" or (e.type == "ShotAtGoal" and e.attrs.get("outcome") == "goal")]


def offside_check(s: AnalystSession, event_id: str | None = None, minute: int | None = None) -> dict:
    """Tracking check of an official offside or of the pass before a goal (2 m band, METRICS.md section 26), with
    `app.engine.offside`. Without `event_id`: the offside call (else the goal) of `minute`, or the latest so far."""
    snap = _snap(s)
    o = _Out(s, snap)
    if event_id is not None and not any(e.event_id == event_id for e in snap.events):
        raise ToolError("event_not_found", f"No event '{event_id}' so far in this match.")
    if minute is not None and minute > o.d["minute"]:
        raise ToolError("future_minute", f"Minute {minute} has not been played yet (now {o.d['minute']}').")
    if event_id is None:
        pool = [e for m, e in _checkable(snap) if minute in (None, m)]
        pick = next((e for e in reversed(pool) if e.type == "Offside"), None) if minute is not None else None
        pick = pick or (pool[-1] if pool else None)
        event_id = pick.event_id if pick else None
    if event_id is None:
        o.d["moment"] = {"event_id": None, "minute": minute}
        why = "no offside call or goal " + (f"at minute {minute}" if minute is not None else "so far")
        o.put(o.d, "verdict", None, "verdict", why)
        o.put(o.d, "margin_m", None, "margin_m", why)
        return o.done()
    r = offside.offside_check(snap, event_id, s.engine._prep)
    o.d["moment"] = {"event_id": event_id, "case": r["case"], "minute": r["minute"], "attacker_id": r["attacker_id"],
                     "attacker": _name(s, r["attacker_id"]), "pass_event_id": r["pass_event_id"]}
    o.d["official"] = OFFICIAL.get(r["case"])
    o.d["band_m"] = offside.BAND_M
    o.put(o.d, "verdict", r["verdict"], "verdict", r["reason"])
    o.put(o.d, "margin_m", r["margin_m"], "margin_m", r["reason"])
    # the distance from the line without its sign, as Archie says it ("onside by about 3.6 m")
    o.put(o.d, "distance_m", None if r["margin_m"] is None else abs(r["margin_m"]), "distance_m", r["reason"])
    o.d["reason"] = r["reason"]
    if r["case"] == "offside" and r["verdict"] == "onside":
        o.d["note"] = NOT_CONFIRMED
    return o.done()


# ------------------------------------------------------------------ definitions for function calling


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[..., dict]
    description: str
    parameters: dict
    summary: Callable[[dict], str]


def _schema(props: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


TEAM_ARG = {"type": "string", "description": "Team id (BRA, CAL), home/away or club name"}
PLAYER_ARG = {"type": "string", "description": "Name, surname, id (BRA-09) or team and shirt number ('BRA #9')"}


def _n(x: int, word: str) -> str:
    return f"{x} {word}{'' if x == 1 else 's'}"


def _sc(r: dict) -> str:
    t = list(r["teams"])
    return f"{t[0]} {r['score']['home']}-{r['score']['away']} {t[1]}, {r['phase']} {r['minute']}'"


def _team_line(r: dict, key: str, fmt: str = "{}") -> str:
    v = r.get(key)
    return ", ".join(f"{t} {fmt.format(x)}" for t, x in v.items()) if isinstance(v, dict) else "n/a"


def _sum_player(b: dict) -> str:
    d = b.get("distance")
    return (f"{b['player']}: {b['minutes_played']}', {_n(b['goals'], 'goal')}, {_n(b['shots'], 'shot')}"
            + (f", {'≈ ' if d['approx'] else ''}{d['km']} km" if d else ""))


def _sum_dist(r: dict) -> str:
    d = r.get("distance")
    if not d:
        return "distance n/a"
    tr = d["top_runner"]
    return f"top runner {tr['player']} {'≈ ' if tr['approx'] else ''}{tr['km']} km"


def _sum_speed(r: dict) -> str:
    teams = r.get("top_speed")
    return "top speed n/a" if not teams else "; ".join(f"{t} {x[0]['player']} {x[0]['kmh']} km/h"
                                                        for t, x in teams.items() if x)


def _sum_offside(r: dict) -> str:
    m = r["moment"]
    if r["verdict"] is None:
        return "offside check n/a: " + r["unavailable"][0]["reason"]
    how = r["reason"] if r["margin_m"] is None else f"{r['margin_m']:+.1f} m, band {r['band_m']:g} m"
    return f"{m['case']} {m['minute']}': {r['verdict']} ({how})"


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("match_score", match_score, "Current score, phase, minute and goal scorers.", _schema(), _sc),
    Tool("match_events", match_events, "Key events so far (goals, shots, penalties, cards, substitutions, corners, "
         "fouls, VAR), filtered, latest first cut.",
         _schema({"kind": {"type": "string", "enum": list(EVENT_KINDS), "description": "Event kind; default all"},
                  "team": TEAM_ARG, "player": PLAYER_ARG,
                  "from_minute": {"type": "integer", "minimum": 0, "maximum": 130},
                  "until_minute": {"type": "integer", "minimum": 0, "maximum": 130},
                  "last": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Max events; default 10"}}),
         lambda r: _n(r["total"], "event") + (f", last {r['events'][-1]['kind']} {r['events'][-1]['minute']}'"
                                              if r["events"] else "")),
    Tool("team_stats", team_stats, "Both teams side by side: shots, possession, passes, distance, cards, corners, "
         "fouls, offsides, woodwork, tackles, sprints, circulation, counter-attacks, PPDA.", _schema(),
         lambda r: "n/a: " + NOT_STARTED if r["teams"] is None else
         "; ".join(f"{t} {_n(x['goals'], 'goal')}, {_n(x['shots'], 'shot')}" for t, x in r["teams"].items())),
    Tool("shots", shots, "Shots, on target, goals, penalties, woodwork, xG per team; each shot (xG: Analyst "
         "version); hardest shot.",
         _schema({"team": TEAM_ARG}),
         lambda r: "shots n/a" if not r.get("shots") else
         "; ".join(f"{t} {_n(x['shots'], 'shot')} ({x['on_target']} on target)" for t, x in r["shots"]["teams"].items())),
    Tool("possession", possession, "Possession %, ball circulation speed in possession (km/h), effective playing "
         "time.", _schema(),
         lambda r: "possession " + _team_line(r, "possession", "{} %")),
    Tool("passes", passes, "Passes and accuracy per team, top passer, most touches; average length and long balls "
         "(Analyst version).", _schema(),
         lambda r: "passes " + ("n/a" if not r.get("passes") else
                                ", ".join(f"{t} {x['passes']} ({x['accuracy_pct']} %)" for t, x in r["passes"].items()))),
    Tool("distance", distance, "Distance covered: per team, top runners, top players (Analyst version).",
         _schema({"team": TEAM_ARG, "top": {"type": "integer", "minimum": 1, "maximum": 30,
                                            "description": "Players listed; default 5"}}), _sum_dist),
    Tool("speeds", speeds, "Top speeds, sprints, top sprinters, speed zones (Analyst version), running intensity, "
         "acceleration load.",
         _schema({"team": TEAM_ARG}), _sum_speed),
    Tool("player_stats", player_stats, "One player so far: minutes, distance, top speed, sprints, shots, goals, "
         "passes, touches, tackles, cards, fouls.", _schema({"player": PLAYER_ARG}, ("player",)),
         lambda r: _sum_player(r["player"]) if "minutes_played" in r["player"] else f"{r['player']['player']}: n/a"),
    Tool("compare_players", compare_players, "Two players side by side (same numbers as player_stats).",
         _schema({"player_a": PLAYER_ARG, "player_b": PLAYER_ARG}, ("player_a", "player_b")),
         lambda r: " vs ".join(_sum_player(r[k]) if "minutes_played" in r[k] else r[k]["player"] for k in "ab")),
    Tool("expected_goals", expected_goals, "Goals and xG per team; each shot's xG and chance word (Analyst "
         "version).", _schema(), lambda r: "goals " + _team_line(r, "goals") + "; xG " + _team_line(r, "xg")),
    Tool("offside_check", offside_check, "Was it offside? Tracking check of an offside call or the pass before a "
         "goal.", _schema({"event_id": {"type": "string"}, "minute": {"type": "integer", "minimum": 0,
                                                                         "maximum": 130}}),
         _sum_offside),
)}


def function_tools() -> list[dict]:
    """Definitions for function calling (Foundry Agent Service / Responses API format)."""
    return [{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
            for t in TOOLS.values()]


# ------------------------------------------------------------------ running a tool


@dataclass
class ToolRun:
    name: str
    arguments: dict
    result: dict  # the tool's result, or {"error": {"code", "message"}}
    summary: str
    latency_ms: int

    @property
    def ok(self) -> bool:
        return "error" not in self.result

    def tool_call(self) -> dict:
        """A contract `ToolCall` for the Inspector."""
        args = {k: v for k, v in self.arguments.items() if isinstance(v, (str, int, float))}
        return {"agent": AGENT, "tool": self.name, "arguments": args, "result_summary": self.summary,
                "latency_ms": self.latency_ms}


def _check(schema: dict, args: dict) -> None:
    props = schema["properties"]
    for k in args:
        if k in SESSION_ONLY and k not in props:
            raise ToolError("out_of_scope", SESSION_ONLY[k])
        if k not in props:
            raise ToolError("bad_argument", f"Unknown argument '{k}'. Allowed: {', '.join(props) or 'none'}.")
    for k in schema["required"]:
        if args.get(k) in (None, ""):
            raise ToolError("bad_argument", f"Missing argument '{k}'.")
    for k, v in args.items():
        spec = props[k]
        if v is None:
            continue
        if spec["type"] == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
            raise ToolError("bad_argument", f"'{k}' must be a whole number.")
        if spec["type"] == "string" and not isinstance(v, str):
            raise ToolError("bad_argument", f"'{k}' must be a string.")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError("bad_argument", f"'{k}' must be one of: {', '.join(spec['enum'])}.")
        if "minimum" in spec and not spec["minimum"] <= v <= spec["maximum"]:
            raise ToolError("bad_argument", f"'{k}' must be between {spec['minimum']} and {spec['maximum']}.")


def run_tool(session: AnalystSession, name: str, arguments: Mapping[str, Any] | None = None) -> ToolRun:
    """Validate the arguments, run the tool, summarise the result. Never raises."""
    t0 = time.perf_counter()
    args = dict(arguments or {})
    tool = TOOLS.get(name)
    try:
        if tool is None:
            raise ToolError("unknown_tool", f"No tool '{name}'. Tools: {', '.join(TOOLS)}.")
        _check(tool.parameters, args)
        result = tool.fn(session, **{k: v for k, v in args.items() if v is not None})
        summary = tool.summary(result)
    except ToolError as exc:
        result, summary = {"error": {"code": exc.code, "message": exc.message}}, f"error: {exc.message}"
    except Exception as exc:  # a bug must not break Archie's answer: report it as a tool error
        result = {"error": {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}}
        summary = "error: internal"
    return ToolRun(name, args, result, summary[:200], int((time.perf_counter() - t0) * 1000))


def numeric_fields(result: Any, prefix: str = "") -> dict[str, float]:
    """Every number of a result by its stable path (`teams.BRA.shots`, `distance.top_runner.km`): the
    `GroundedValue.source_field` values the grounding validator of AG-1b accepts."""
    out: dict[str, float] = {}
    if isinstance(result, dict):
        for k, v in result.items():
            if k != "unavailable":
                out.update(numeric_fields(v, f"{prefix}{k}."))
    elif isinstance(result, list):
        for i, v in enumerate(result):
            out.update(numeric_fields(v, f"{prefix.rstrip('.')}[{i}]."))
    elif isinstance(result, (int, float)) and not isinstance(result, bool):
        out[prefix.rstrip(".")] = result
    return out
