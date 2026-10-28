"""Deterministic statistics and contract payloads, built from a window `Snapshot` only (never the whole match).

Metric names and methods follow Materials/METRICS.md and the models of `app/contract/models.py` (v0.3.0). Event-based
numbers (score, shots, corners, fouls, cards, offsides, passes, duels, touches) come from the released events;
frame-based ones from `frame_stats.py` (data pipeline). A metric that cannot be computed is null (or an empty list)
and is listed in `unavailable` with the reason: the engine never fills a number it does not have. Since task E-4 the
metrics follow the decisions of 25 October (Materials/METRICS.md): xG and goals vs xG, the located shot map, momentum,
the rolling peak intensity, sprints, pass lengths, ball circulation, acceleration load, tackles and recoveries,
counter-attacks, zone maps, defensive shape and PPDA; goalkeepers are out of the team totals. The offside check for
Archie is `offside.py` (never a statistic).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.contract.models import Mode, Phase, Reliability

from . import frame_stats
from .phases import sections_for
from .sources.base import AWAY, HOME, TEAM_ID
from .sources.fake import attacks_plus_x
from .window import Snapshot

SIDES = (HOME, AWAY)
FEED_TYPES = {"KickOff", "ShotAtGoal", "Caution", "Substitution", "CornerKick", "Foul", "VideoAssistantAction",
              "FinalWhistle"}  # contract EventType: key events only
ON_TARGET = {"goal", "saved"}
SHOT_OUTCOMES = {"goal", "saved", "wide", "blocked", "woodwork"}
ON_BALL = {"Play", "ShotAtGoal", "OtherBallAction", "BallClaiming", "FreeKick", "ThrowIn", "CornerKick", "GoalKick",
           "KickOff"}
TOUCH_TYPES = ON_BALL | {"TacklingGame", "Penalty"}  # a duel's player is its winner (Materials/METRICS.md section 24)
TOP_SPEEDS_ANALYST = 5

E4 = "computed from task E-4"
# Kept for app/agents/analyst_tools.py (AG-1a imports it); the engine fills the acceleration load since E-4 block 4.
LOAD_PENDING = f"acceleration load {E4} (Materials/METRICS.md section 22)"

XG_METHOD = ("visible goal angle (fixed slope 2.84) and opponents in the shot triangle at the located kick frame, "
             "calibrated on real match data; penalties 0.79 (Materials/METRICS.md section 9)")

CHARTS = [  # chart_id, title, modes, payload path that must be filled (contract v0.3.0, display order)
    ("momentum", "Who is pushing", ("casual", "analyst"), "momentum"),
    ("shot_map", "Shot map", ("casual", "analyst"), "shot_map"),
    ("xg_cumulative", "Expected goals over time", ("analyst",), "xg"),
    ("peak_intensity", "Running intensity", ("casual", "analyst"), "peak_intensity.series"),
    ("acceleration_load", "Acceleration load", ("analyst",), "acceleration_load.series"),
    ("ball_circulation", "Ball circulation speed", ("analyst",), "ball_circulation.series"),
    ("zone_map", "Zones of play", ("analyst",), "zone_map"),
    ("speed_zones", "Distance by speed zone", ("analyst",), "speed_zones"),
    ("defensive_shape", "Defensive line and team length", ("analyst",), "defensive_shape"),
]


@dataclass
class Core:
    """Mode-independent numbers of one snapshot (event-based in `ev`, frame-based in `fr`, None without frames)."""

    snap: Snapshot
    ev: dict
    fr: dict | None


def no_frames_reason(snap: Snapshot) -> str:
    if not snap.has_tracking:
        return f"needs tracking frames: source {snap.source} has events only"
    return "not enough tracking frames released yet"


# ------------------------------------------------------------------ event-based metrics


def _new_player() -> dict:
    return {"shots": 0, "goals": 0, "passes": 0, "complete": 0, "touches": 0, "tackles": 0, "recoveries": 0,
            "fouls_committed": 0, "fouls_won": 0, "yellow": 0, "red": 0}


def event_metrics(snap: Snapshot) -> dict:
    t = {s: {"shots": 0, "on_target": 0, "goals": 0, "penalties": 0, "woodwork": 0, "corners": 0, "fouls": 0,
             "offsides": 0, "yellow": 0, "red": 0, "passes": 0, "complete": 0, "tackles": 0, "recoveries": 0}
         for s in SIDES}
    per_player: dict[str, dict] = {}
    shots, goals = [], []

    def pl(pid: str | None) -> dict:
        return per_player.setdefault(pid, _new_player()) if pid else {}

    for e in snap.events:
        s = e.team_side
        if s not in t:
            continue
        pp = pl(e.player_id)
        if pp and e.type in TOUCH_TYPES and not e.event_id.endswith("-shot"):  # a set-piece shot is one touch
            pp["touches"] += 1
        if e.type == "ShotAtGoal":
            out = e.attrs.get("outcome", "wide")
            out = out if out in SHOT_OUTCOMES else "wide"
            t[s]["shots"] += 1
            t[s]["on_target"] += out in ON_TARGET
            t[s]["goals"] += out == "goal"
            t[s]["penalties"] += bool(e.attrs.get("penalty"))
            t[s]["woodwork"] += out == "woodwork"
            if pp:
                pp["shots"] += 1
                pp["goals"] += out == "goal"
            shots.append((e, out))
            if out == "goal":
                goals.append(e)
        elif e.type == "CornerKick":
            t[s]["corners"] += 1
        elif e.type == "Foul":
            t[s]["fouls"] += 1
            if pp:
                pp["fouls_committed"] += 1
            if e.secondary_player_id:
                pl(e.secondary_player_id)["fouls_won"] += 1
        elif e.type == "Offside":
            t[s]["offsides"] += 1
        elif e.type == "Caution":
            card = e.attrs.get("card", "yellow")
            yellow, red = card in ("yellow", "second_yellow"), card in ("red", "second_yellow")
            t[s]["yellow"] += yellow
            t[s]["red"] += red
            if pp:
                pp["yellow"] += yellow
                pp["red"] += red
        elif e.type == "Play":
            ok = e.attrs.get("outcome") == "complete"
            t[s]["passes"] += 1
            t[s]["complete"] += ok
            if pp:
                pp["passes"] += 1
                pp["complete"] += ok
        elif e.type == "TacklingGame":
            # A tackle won is a duel won by the defending player (METRICS.md section 21); without the role, any duel won.
            won = e.attrs.get("winner_role") != "with_ball"
            t[s]["tackles"] += won
            t[s]["recoveries"] += won
            if pp:
                pp["tackles"] += won
                pp["recoveries"] += won
        elif e.type == "BallClaiming":
            t[s]["recoveries"] += 1
            if pp:
                pp["recoveries"] += 1
    has_passes = snap.full_event_stream or any(e.type == "Play" for e in snap.events)
    return {"teams": t, "players": per_player, "shots": shots, "goals": goals, "has_passes": has_passes,
            "possession": _event_possession(snap) if snap.full_event_stream else None}


def _event_possession(snap: Snapshot) -> dict | None:
    """Fallback without tracking, only for full event streams: the time to the next on-ball event (max 30 s) goes to
    the team of the event."""
    evs = [e for e in snap.events if e.type in ON_BALL and e.team_side in SIDES]
    acc = {HOME: 0.0, AWAY: 0.0}
    for a, b in zip(evs, evs[1:]):
        if a.period == b.period:
            acc[a.team_side] += min(b.t - a.t, 30.0)
    tot = acc[HOME] + acc[AWAY]
    if tot <= 0:
        return None
    h = round(100.0 * acc[HOME] / tot, 1)
    return {"home_pct": h, "away_pct": round(100.0 - h, 1)}


def compute_core(snap: Snapshot, cache: frame_stats.PrepCache | None = None) -> Core:
    return Core(snap, event_metrics(snap), frame_stats.compute(snap, cache) if snap.frames else None)


# ------------------------------------------------------------------ small payload pieces


def score(core: Core) -> dict:
    return {"home": core.ev["teams"][HOME]["goals"], "away": core.ev["teams"][AWAY]["goals"]}


def scoreboard(snap: Snapshot) -> tuple[int, int]:
    ph = snap.phase
    if ph is Phase.pre_match:
        return 0, 0
    if ph is Phase.first_half:
        return snap.minute(1, snap.as_of_s)
    if ph is Phase.half_time:
        return 45, 0
    if ph is Phase.second_half:
        return snap.minute(2, snap.as_of_s - (snap.period_length_s(1) or 0.0))
    return 90, 0


def normalised_xy(e) -> tuple[float | None, float | None]:
    """Contract convention: the team of the item attacks towards +x."""
    if e.x is None or e.y is None:
        return None, None
    flip = e.team_side in SIDES and not attacks_plus_x(e.team_side, e.period)
    return (round(-e.x if flip else e.x, 2), round(-e.y if flip else e.y, 2))


def _event_minute(core: Core, e) -> int:
    return Snapshot.minute(e.period, core.snap.clock_s(e.period, e.t))[0]


def _team_shots(core: Core) -> list[dict]:
    return [{"team_id": TEAM_ID[s], **{k: core.ev["teams"][s][k] for k in ("shots", "on_target", "goals")}} for s in SIDES]


def _recoveries(core: Core, side: str | None = None, pid: str | None = None) -> int:
    """Ball recoveries (METRICS.md section 21): with frames, the candidate events confirmed by the possession column;
    without frames, the candidate events (balls claimed, duels won by the defending player)."""
    if core.fr:
        return sum(1 for r in core.fr["recoveries"] if (side is None or r["side"] == side)
                   and (pid is None or r["event"].player_id == pid))
    return core.ev["players"].get(pid, _new_player())["recoveries"] if pid else core.ev["teams"][side]["recoveries"]


def _counts(core: Core) -> list[dict]:
    full = core.snap.full_event_stream
    out = []
    for s in SIDES:
        t = core.ev["teams"][s]
        out.append({"team_id": TEAM_ID[s], "corners": t["corners"], "fouls": t["fouls"], "yellow_cards": t["yellow"],
                    "red_cards": t["red"], "penalties": t["penalties"], "woodwork": t["woodwork"],
                    "offsides": t["offsides"], "sprints": core.fr["teams"][s]["sprints"] if core.fr else None,
                    "tackles": t["tackles"] if full else None, "ball_recoveries": _recoveries(core, side=s) if full else None})
    return out


def _possession(core: Core) -> dict | None:
    p = core.fr["possession"] if core.fr else core.ev["possession"]
    if p is None:
        return None
    return {"teams": [{"team_id": "BRA", "value": p["home_pct"]}, {"team_id": "CAL", "value": p["away_pct"]}]}


def _distance(core: Core, analyst: bool) -> dict | None:
    if not core.fr or not core.fr["players"]:
        return None
    fr = core.fr

    def splits(m):
        return {k: round(v / 1000.0, 2) for k, v in zip(("ball_in_play_km", "ball_stopped_km", "in_possession_km",
                                                           "out_of_possession_km"), m)}

    players = sorted(fr["players"].items(), key=lambda kv: (-kv[1]["dist_m"], kv[0]))
    pid, top = players[0]

    def player(pid, v):
        return {"player_id": pid, "distance": frame_stats.distance_payload(v["dist_m"], v["unmeasured"]),
                "splits": splits(v["splits_m"]) if analyst else None}

    return {"teams": [{"team_id": TEAM_ID[s], "distance": frame_stats.distance_payload(fr["teams"][s]["dist_m"],
                                                                                         fr["teams"][s]["unmeasured"]),
                       "splits": splits(fr["teams"][s]["splits_m"]) if analyst else None} for s in SIDES],
            "top_runner": player(pid, top),
            "players": [player(p, v) for p, v in players if v["samples"] > 0] if analyst else None}


def _top_speeds(core: Core, per_team: int = 1) -> list[dict]:
    """The fastest `per_team` players of each team (Fan 1, Analyst 5)."""
    if not core.fr:
        return []
    out = []
    for s in SIDES:
        best = sorted(((v["top"]["kmh"], pid, v["top"]["minute"]) for pid, v in core.fr["players"].items()
                       if v["side"] == s and v["top"]), key=lambda b: (-b[0], b[1]))
        out += [{"player_id": pid, "team_id": TEAM_ID[s], "kmh": kmh, "minute": minute}
                for kmh, pid, minute in best[:per_team]]
    return out


def _shot_speeds(core: Core) -> list[dict]:
    """The hardest shot of each team, headers excluded (Materials/METRICS.md section 5)."""
    best: dict[str, dict] = {}
    for v in (core.fr["shot_speeds"] if core.fr else {}).values():
        e = v["event"]
        if e.attrs.get("header") or e.team_side not in SIDES or not e.player_id:
            continue
        if e.team_side not in best or v["kmh"] > best[e.team_side]["kmh"]:
            best[e.team_side] = v
    return [{"player_id": b["event"].player_id, "team_id": TEAM_ID[s], "kmh": b["kmh"], "minute": b["minute"],
             "event_id": b["event"].event_id} for s in SIDES if (b := best.get(s))]


def _circulation(core: Core, analyst: bool) -> dict | None:
    c = core.fr["circulation"] if core.fr else None
    if not c:
        return None
    return {"teams": [{"team_id": TEAM_ID[s], "value": c["values"][s],
                       "reliability": Reliability.of(c["measured_pct"][s]).model_dump(mode="json")} for s in SIDES],
            "unit": "km/h",
            "series": [{"team_id": TEAM_ID[x["side"]], "points": x["points"]} for x in c["series"]] if analyst else None,
            "chart_id": "ball_circulation"}


def _pass_lengths(core: Core, side: str | None = None, pid: str | None = None) -> list[float] | None:
    """Lengths (m) of the measured successful passes of a team or a player (METRICS.md section 11); None without
    frames."""
    if not core.fr:
        return None
    return [v["length_m"] for v in core.fr["passes"]["lengths"].values()
            if (side is None or v["side"] == side) and (pid is None or v["player_id"] == pid)]


def _passes(core: Core, analyst: bool) -> list[dict]:
    """Passes and accuracy (both versions); Analyst: the average length of the successful passes and the long balls
    (successful passes of 32 m or more)."""
    if not core.ev["has_passes"]:
        return []
    out = []
    for s in SIDES:
        t = core.ev["teams"][s]
        acc = round(100.0 * t["complete"] / t["passes"], 1) if t["passes"] else 0.0
        lengths = _pass_lengths(core, side=s) if analyst else None
        out.append({"team_id": TEAM_ID[s], "passes": t["passes"], "accuracy_pct": acc,
                    "avg_length_m": round(float(np.mean(lengths)), 1) if lengths else None,
                    "long_balls": frame_stats.metrics.long_balls(lengths, frame_stats.CFG) if lengths is not None else None})
    return out


def _shot_xg(core: Core, e) -> float | None:
    """xG of one released shot (2 decimals, as shown), None without frames or position."""
    v = core.fr["shots"].get(e.event_id) if core.fr else None
    return round(v["xg"], 2) if v and v["xg"] is not None else None


def _shot_map(core: Core, analyst: bool) -> dict:
    """Shot map (Materials/METRICS.md section 14): the ball at the located kick frame, attack-normalised (the frame
    of the xG and of the shot speed); the annotated position when the shot is not located or without frames."""
    located = core.fr["shots"] if core.fr else {}
    shots = []
    for e, out in core.ev["shots"]:
        v = located.get(e.event_id)
        x, y = (round(v["x"], 2), round(v["y"], 2)) if v and v["x"] is not None else normalised_xy(e)
        if x is None:
            continue
        header = bool(e.attrs.get("header"))
        speed = v["kmh"] if v else None
        shots.append({"event_id": e.event_id, "team_id": TEAM_ID[e.team_side], "player_id": e.player_id or "",
                      "minute": _event_minute(core, e), "x": x, "y": y,
                      "xg": _shot_xg(core, e) if analyst else None, "outcome": out,
                      "is_penalty": bool(e.attrs.get("penalty")), "is_header": header,
                      "speed_kmh": speed if speed and not header else None})
    return {"shots": shots, "chart_id": "shot_map"}


def _team_xg(core: Core) -> dict[str, float]:
    """Team xG = the sum of its shots' xG as shown (2 decimals), penalties included."""
    out = {s: 0.0 for s in SIDES}
    for e, _ in core.ev["shots"]:
        x = _shot_xg(core, e)
        if x is not None:
            out[e.team_side] = round(out[e.team_side] + x, 2)
    return out


def _xg(core: Core, analyst: bool) -> dict | None:
    """xG per team and the cumulative step series (Materials/METRICS.md section 9): one point per shot, from 0 at 0'."""
    if not analyst or not core.fr:
        return None
    run = {s: 0.0 for s in SIDES}
    cum = {s: [{"minute": 0, "value": 0.0}] for s in SIDES}
    for e, _ in core.ev["shots"]:
        x = _shot_xg(core, e)
        if x is not None:
            run[e.team_side] = round(run[e.team_side] + x, 2)
            cum[e.team_side].append({"minute": _event_minute(core, e), "value": run[e.team_side]})
    return {"teams": [{"team_id": TEAM_ID[s], "value": run[s]} for s in SIDES],
            "cumulative": [{"team_id": TEAM_ID[s], "points": cum[s]} for s in SIDES], "method": XG_METHOD,
            "chart_id": "xg_cumulative"}


def _goals_vs_xg(core: Core) -> list[dict]:
    """Goals (own goals are never a shot of the team, so they are out) next to the team's xG (METRICS.md section 10):
    numbers only, the same in both versions."""
    if not core.fr:
        return []
    xg = _team_xg(core)
    return [{"team_id": TEAM_ID[s], "goals": core.ev["teams"][s]["goals"], "xg": xg[s]} for s in SIDES]


def _momentum(core: Core) -> dict | None:
    """Who is pushing (METRICS.md section 13): the value now and one point per played minute (both versions)."""
    m = core.fr["momentum"] if core.fr else None
    return {"current": m["current"], "series": m["series"], "chart_id": "momentum"} if m else None


def _player_xg(core: Core, pid: str) -> float | None:
    if not core.fr:
        return None
    return round(float(sum(x for e, _ in core.ev["shots"] if e.player_id == pid and (x := _shot_xg(core, e)) is not None)), 2)


def _peak(core: Core, analyst: bool) -> dict | None:
    """Peak intensity (METRICS.md section 4): each team's most intense five minutes, one point per minute for the
    mirror chart (both versions) and, for Analyst, each player's peak."""
    pk = core.fr["peaks"] if core.fr else None
    if not pk or not pk["teams"]:
        return None
    side_of = {p.player_id: p.team_side for p in core.snap.lineup}
    return {"teams": [{"team_id": TEAM_ID[s], **pk["teams"][s]} for s in SIDES if s in pk["teams"]],
            "series": [{"team_id": TEAM_ID[s], "points": pk["series"][s]} for s in SIDES],
            "players": [{"player_id": pid, "team_id": TEAM_ID[side_of[pid]], **v}
                        for pid, v in sorted(pk["players"].items()) if pid in side_of] if analyst else None,
            "chart_id": "peak_intensity"}


def _effective(core: Core) -> dict | None:
    fr = core.fr
    if not fr:
        return None
    played = round(core.snap.as_of_s / 60.0, 1)
    by = fr["effective_min_by_period"]
    return {"first_half_min": by.get(1), "second_half_min": by.get(2), "total_min": fr["effective_min"],
            "played_min": played,
            "in_play_pct": min(100.0, round(100.0 * fr["effective_min"] / played, 1)) if played else 0.0}


def _goal_marks(core: Core) -> list[dict]:
    return [{"team_id": TEAM_ID[e.team_side], "minute": _event_minute(core, e)} for e in core.ev["goals"]]


# ------------------------------------------------------------------ players: minutes, rows, leaders, Player Focus


def _minutes_played(snap: Snapshot, pid: str) -> int:
    starter = next((p.starting for p in snap.lineup if p.player_id == pid), False)
    on = 0.0 if starter else None
    played = 0.0
    for e in snap.events:
        if e.type != "Substitution":
            continue
        if e.secondary_player_id == pid:
            on = snap.playing_s(e.period, e.t)
        elif e.player_id == pid and on is not None:
            played += snap.playing_s(e.period, e.t) - on
            on = None
    if on is not None:
        played += snap.as_of_s - on
    return int(round(max(played, 0.0) / 60.0))


def _player_numbers(core: Core, pid: str) -> dict:
    """The numbers of one player shared by the table of all players and the Player Focus card."""
    full = core.snap.full_event_stream
    has_passes = core.ev["has_passes"]
    pl = core.ev["players"].get(pid, _new_player())
    fr = core.fr["players"].get(pid) if core.fr else None
    load = core.fr["load"]["players"].get(pid) if core.fr else None
    return {"distance": frame_stats.distance_payload(fr["dist_m"], fr["unmeasured"]) if fr else None,
            "top_speed_kmh": fr["top"]["kmh"] if fr and fr["top"] else None,
            "sprints": fr["sprints"] if fr else None,
            "acceleration_load_kmh": round(load["kmh"], 1) if load else None,
            "passes": pl["passes"] if has_passes else None,
            "pass_accuracy_pct": (round(100.0 * pl["complete"] / pl["passes"], 1) if pl["passes"] else 0.0)
            if has_passes else None,
            "touches": pl["touches"] if full else None, "shots": pl["shots"], "goals": pl["goals"],
            "tackles": pl["tackles"] if full else None, "ball_recoveries": _recoveries(core, pid=pid) if full else None,
            "fouls_committed": pl["fouls_committed"], "fouls_won": pl["fouls_won"]}


def _played(snap: Snapshot) -> list:
    """Line-up entries of the players who have been on the pitch so far."""
    return [p for p in snap.lineup if _minutes_played(snap, p.player_id) > 0 or p.starting]


def _players_table(core: Core) -> list[dict]:
    rows = []
    for p in _played(core.snap):
        n = _player_numbers(core, p.player_id)
        rows.append({"player_id": p.player_id, "team_id": TEAM_ID[p.team_side],
                     "minutes_played": _minutes_played(core.snap, p.player_id),
                     **{k: n[k] for k in ("distance", "top_speed_kmh", "sprints", "acceleration_load_kmh", "passes",
                                          "pass_accuracy_pct", "touches", "shots", "goals", "tackles",
                                          "ball_recoveries", "fouls_committed", "fouls_won")},
                     "peak_m_per_min": _player_peak(core, p.player_id), "long_balls": _player_long_balls(core, p.player_id),
                     "xg": _player_xg(core, p.player_id)})
    return rows


def _player_peak(core: Core, pid: str) -> float | None:
    v = core.fr["peaks"]["players"].get(pid) if core.fr else None
    return v["metres_per_minute"] if v else None


def _player_long_balls(core: Core, pid: str) -> int | None:
    lengths = _pass_lengths(core, pid=pid)
    return frame_stats.metrics.long_balls(lengths, frame_stats.CFG) if lengths is not None else None


def _leaders(core: Core) -> list[dict]:
    full = core.snap.full_event_stream
    out = []
    for s in SIDES:
        ids = [p.player_id for p in _played(core.snap) if p.team_side == s]
        ev = {pid: core.ev["players"].get(pid, _new_player()) for pid in ids}
        fr = {pid: core.fr["players"][pid] for pid in ids if core.fr and pid in core.fr["players"]}

        def top(values: dict[str, float]) -> dict | None:
            if not values or max(values.values()) <= 0:
                return None
            pid = min(values, key=lambda k: (-values[k], k))
            return {"player_id": pid, "value": values[pid]}

        touches = sorted(((v["touches"], pid) for pid, v in ev.items() if v["touches"] > 0), key=lambda x: (-x[0], x[1]))
        out.append({"team_id": TEAM_ID[s],
                    "top_runner": top({pid: round(v["dist_m"] / 1000.0, 2) for pid, v in fr.items()}),
                    "top_sprinter": top({pid: float(v["sprints"]) for pid, v in fr.items()}),
                    "top_passer": top({pid: float(v["passes"]) for pid, v in ev.items()}) if core.ev["has_passes"] else None,
                    "most_touches": [{"player_id": pid, "value": float(n)} for n, pid in touches[:3]] if full else []})
    return out


def player_focus(core: Core, pid: str, analyst: bool) -> dict:
    """The Player Focus card of a favourite player (contract v0.3.0), the same in Data & Statistics and Recap."""
    side = next((p.team_side for p in core.snap.lineup if p.player_id == pid), HOME)
    n = _player_numbers(core, pid)
    pl = core.ev["players"].get(pid, _new_player())
    fr = core.fr["players"].get(pid) if core.fr else None
    load = core.fr["load"]["players"].get(pid) if core.fr else None
    return {"player_id": pid, "team_id": TEAM_ID[side], "minutes_played": _minutes_played(core.snap, pid),
            **{k: n[k] for k in ("distance", "top_speed_kmh", "sprints", "acceleration_load_kmh", "passes",
                                 "pass_accuracy_pct", "shots", "goals", "touches", "tackles", "ball_recoveries",
                                 "fouls_committed", "fouls_won")},
            "top_speed_minute": fr["top"]["minute"] if fr and fr["top"] else None,
            "yellow_cards": pl["yellow"], "red_cards": pl["red"], "highlight_ids": [],
            "xg": _player_xg(core, pid) if analyst else None,
            "speed_zones": _zones(side, fr["zones_m"], pid) if analyst and fr else None,
            "zone_map": _zone_map(side, core.fr["zone_maps"]["players"][pid], pid)
            if analyst and core.fr and pid in core.fr["zone_maps"]["players"] else None,
            "acceleration_load_series": [{"minute": m, "value": round(v, 1)} for m, v in sorted(load["series"].items())]
            if analyst and load else None}


def _zones(side: str, zones_m, pid: str | None = None) -> dict:
    """Contract `SpeedZones` (km per FIFA zone) of a team (outfield players) or of one player."""
    return {"team_id": TEAM_ID[side], "player_id": pid,
            **{k: round(v / 1000.0, 2) for k, v in zip(("km_0_7", "km_7_15", "km_15_20", "km_20_25", "km_over_25"),
                                                       zones_m)}}


def _acceleration_load(core: Core, analyst: bool) -> dict | None:
    """Acceleration load (METRICS.md section 22): per team (outfield players) the total change of speed in km/h, per
    minute played, with the reliability symbol of its distance; Analyst: km/h per minute in 5-minute buckets."""
    if not core.fr:
        return None
    ld, played = core.fr["load"]["teams"], core.snap.as_of_s / 60.0
    return {"teams": [{"team_id": TEAM_ID[s], "total_kmh": round(ld[s]["kmh"], 1),
                       "per_minute_kmh": round(ld[s]["kmh"] / played, 1) if played > 0 else 0.0,
                       "reliability": Reliability.of(100.0 * (1.0 - core.fr["teams"][s]["unmeasured"])).model_dump(mode="json")}
                      for s in SIDES],
            "series": [{"team_id": TEAM_ID[s], "points": [{"minute": m, "value": round(v, 1)}
                                                          for m, v in sorted(ld[s]["series"].items())]}
                       for s in SIDES] if analyst else None,
            "chart_id": "acceleration_load"}


def _zone_map(side: str, cells, pid: str | None = None) -> dict:
    """Contract `ZoneMap`: share of time (%) per display cell; row 0 at the team's left touchline (y = +W/2), column
    0 at its own goal line (attacking left to right)."""
    return {"team_id": TEAM_ID[side], "player_id": pid, "rows": int(cells.shape[0]), "cols": int(cells.shape[1]),
            "cells_pct": [[round(float(v), 2) for v in row] for row in cells], "chart_id": "zone_map"}


def _zone_maps(core: Core, analyst: bool) -> list[dict] | None:
    zm = core.fr["zone_maps"]["teams"] if analyst and core.fr else None
    return [_zone_map(s, zm[s]) for s in SIDES if s in zm] if zm else None


def _defensive_shape(core: Core, analyst: bool) -> list[dict] | None:
    """Defensive line height and team length per 5-minute bucket (METRICS.md section 17), Analyst."""
    if not analyst or not core.fr:
        return None
    sh = core.fr["shapes"]
    out = [{"team_id": TEAM_ID[s],
            "defensive_line_m": [{"minute": m, "value": round(v, 1)} for m, v in sorted(sh[s]["line"].items())],
            "team_length_m": [{"minute": m, "value": round(v, 1)} for m, v in sorted(sh[s]["length"].items())],
            "chart_id": "defensive_shape"} for s in SIDES]
    return out if any(x["defensive_line_m"] for x in out) else None


def _ppda(core: Core, analyst: bool) -> list[dict] | None:
    """PPDA per team (METRICS.md section 13), Analyst; a team without a defensive action in its pressing zone yet has
    no value (it is listed in `unavailable`)."""
    if not analyst or not core.fr:
        return None
    out = [{"team_id": TEAM_ID[s], "value": round(v["value"], 1)} for s, v in core.fr["ppda"].items()
           if v["value"] is not None]
    return out or None


def _counter_attacks(core: Core, analyst: bool) -> dict | None:
    """Counter-attacks (METRICS.md section 25): counts per team; Analyst: the list, linked to the timeline."""
    if not core.fr:
        return None
    items = core.fr["counter_attacks"]
    return {"teams": [{"team_id": TEAM_ID[s], "count": sum(x["side"] == s for x in items)} for s in SIDES],
            "items": [{"team_id": TEAM_ID[x["side"]], "minute": x["minute"], "recovery_event_id": x["recovery_event_id"],
                       "shot_event_id": x["shot_event_id"]} for x in items] if analyst else None}


def _favourites(core: Core, favourites: list[str]) -> list[str]:
    ids = {p.player_id for p in core.snap.lineup}
    return [pid for pid in favourites if pid in ids]


def _path(payload: dict, path: str):
    v = payload
    for k in path.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    return v


def _unavailable(core: Core, payload: dict, analyst: bool) -> list[dict]:
    nf = no_frames_reason(core.snap)
    has_frames = core.fr is not None
    reasons = {  # metric: (reason, available_from_minute)
        "possession": (nf if core.snap.has_tracking or not core.snap.full_event_stream else "no on-ball events yet", None),
        "distance_covered": (nf, None), "top_speed": (nf, None), "ball_circulation": (nf, None),
        "effective_playing_time": (nf, None), "speed_zones": (nf, None),
        "shot_speed": (nf if not has_frames else "no shot with valid ball samples yet", None),
        "peak_intensity": (nf, None) if not has_frames else ("needs five complete minutes of play", 5),
        "passes": (f"no pass events in source {core.snap.source} (key events only)", None),
        "xg": (nf, None), "goals_vs_xg": (nf, None), "momentum": (nf, None),
        "acceleration_load": (nf, None), "counter_attacks": (nf, None),
        "ppda": (nf if not has_frames else "no defensive action in a pressing zone yet", None),
        "zone_map": (nf if not has_frames else "no valid sample in live play yet", None),
        "defensive_shape": (nf if not has_frames else "needs five complete minutes of play", 5),
    }
    if not analyst:
        for k in ("speed_zones", "xg", "zone_map", "defensive_shape", "ppda"):
            reasons.pop(k)  # Analyst-only fields are null in Casual by design, not unavailable
    out = []
    for k, (r, from_minute) in reasons.items():
        if k in payload and payload[k] in (None, []):
            out.append({"metric": k, "reason": r, "available_from_minute": from_minute})
    got = {x["team_id"] for x in payload.get("ppda") or []}
    if analyst and payload.get("ppda") and len(got) < len(SIDES):
        out += [{"metric": f"ppda.{TEAM_ID[s]}", "reason": "no defensive action in its pressing zone yet",
                 "available_from_minute": None} for s in SIDES if TEAM_ID[s] not in got]
    passes = payload.get("passes") or []
    if analyst and passes and passes[0].get("long_balls") is None:
        out.append({"metric": "passes.long_balls", "reason": nf, "available_from_minute": None})
    elif analyst and any(p.get("avg_length_m") is None for p in passes):
        out.append({"metric": "passes.avg_length_m", "reason": "no successful pass with its reception released yet",
                    "available_from_minute": None})
    counts = payload.get("counts") or []
    if counts and counts[0].get("sprints") is None:
        out.append({"metric": "counts.sprints", "reason": nf, "available_from_minute": None})
    if counts and counts[0].get("tackles") is None:
        out.append({"metric": "counts.tackles", "reason": f"no duel or recovery events in source {core.snap.source}",
                    "available_from_minute": None})
    return out


# ------------------------------------------------------------------ contract payloads


def match_state(snap: Snapshot, server_time: datetime) -> dict:
    """Scoreboard of the snapshot (events only: cheap enough for every poll of the app)."""
    minute, added = scoreboard(snap)
    goals = event_metrics(snap)["teams"]
    return {"match_id": snap.match_id, "phase": snap.phase.value, "minute": minute, "added_time": added,
            "clock_running": snap.phase in (Phase.first_half, Phase.second_half),
            "score": {"home": goals[HOME]["goals"], "away": goals[AWAY]["goals"]},
            "sections": [s.model_dump(mode="json") for s in sections_for(snap.phase)],
            "as_of_s": round(snap.as_of_s, 1), "server_time": server_time.isoformat()}


def event_feed(snap: Snapshot) -> dict:
    out = []
    for e in snap.events:
        if e.type not in FEED_TYPES:
            continue
        clock = snap.clock_s(e.period, e.t)
        minute, added = Snapshot.minute(e.period, clock)
        x, y = normalised_xy(e)
        q = {k: v for k, v in e.attrs.items() if isinstance(v, (str, float, int, bool))}
        if added:
            q["added_time"] = float(added)
        out.append({"event_id": e.event_id, "type": e.type, "period": e.period, "minute": minute,
                    "match_time_s": round(clock, 2), "team_id": TEAM_ID.get(e.team_side),
                    "player_id": e.player_id, "secondary_player_id": e.secondary_player_id, "x": x, "y": y,
                    "qualifiers": q})
    return {"match_id": snap.match_id, "as_of_s": round(snap.as_of_s, 1), "events": out}


def live_stats(core: Core) -> dict:
    """Live (contract v0.2.5): only the key numbers per team; every other statistic is in Data & Statistics."""
    snap = core.snap
    minute, _ = scoreboard(snap)
    p = {"match_id": snap.match_id, "phase": snap.phase.value, "minute": minute, "as_of_s": round(snap.as_of_s, 1),
         "score": score(core), "shots": _team_shots(core), "counts": _counts(core)}
    p["unavailable"] = _unavailable(core, p, False)
    return p


def match_stats(core: Core, mode: Mode, inspector: bool, computed_at: datetime,
                favourites: list[str] | None = None) -> dict:
    """Data & Statistics (contract v0.3.0). `favourites`: the session's favourite players, for the Player Focus."""
    snap = core.snap
    analyst = mode is Mode.analyst
    full = snap.phase is Phase.full_time
    live = snap.phase in (Phase.first_half, Phase.second_half)
    fr = core.fr
    zones = [_zones(s, fr["teams"][s]["zones_m"]) for s in SIDES] if analyst and fr else None
    scope = "live" if live else "full_match" if full else "first_half"
    p = {"match_id": snap.match_id, "mode": mode.value, "scope": scope,
         "up_to_minute": scoreboard(snap)[0] if live else 90 if full else 45, "as_of_s": round(snap.as_of_s, 1),
         "score": score(core), "charts": [], "goal_marks": _goal_marks(core),
         "shots": _team_shots(core), "counts": _counts(core), "possession": _possession(core),
         "goals_vs_xg": _goals_vs_xg(core),
         "effective_playing_time": _effective(core),
         "player_focus": [player_focus(core, pid, analyst) for pid in _favourites(core, favourites or [])],
         "distance_covered": _distance(core, analyst),
         "top_speed": _top_speeds(core, TOP_SPEEDS_ANALYST if analyst else 1), "peak_intensity": _peak(core, analyst),
         "acceleration_load": _acceleration_load(core, analyst), "speed_zones": zones,
         "passes": _passes(core, analyst), "ball_circulation": _circulation(core, analyst),
         "shot_speed": _shot_speeds(core),
         "counter_attacks": _counter_attacks(core, analyst), "ppda": _ppda(core, analyst), "leaders": _leaders(core),
         "players": _players_table(core) if analyst else None,
         "xg": _xg(core, analyst), "momentum": _momentum(core), "shot_map": _shot_map(core, analyst),
         "zone_map": _zone_maps(core, analyst), "defensive_shape": _defensive_shape(core, analyst), "inspector": None}
    p["charts"] = [{"chart_id": c, "title": title, "section": "data_stats", "modes": list(modes)}
                   for c, title, modes, path in CHARTS if mode.value in modes and _path(p, path) not in (None, [])]
    p["locked_charts"] = [{"chart_id": c, "title": title, "section": "data_stats", "modes": list(modes)}
                          for c, title, modes, _ in CHARTS if mode.value not in modes]
    p["unavailable"] = _unavailable(core, p, analyst)
    if inspector:
        p["inspector"] = {"methods": methods(core), "computed_at": computed_at.isoformat(), "source": snap.source}
    return p


def methods(core: Core) -> list[dict]:
    """Inspector 'How it's computed': the deterministic function and parameters behind each number."""
    snap, cfg = core.snap, frame_stats.CFG
    excl = core.fr["excluded_pct"] if core.fr else 0.0
    out = [{"metric": "as_of_s", "method": "No look-ahead: computed on the window snapshot at this instant: frames up "
            "to the replay cursor, events only after their next frames have arrived",
            "tool": "app.engine.window.MatchWindow.at",
            "parameters": {"cursor_t": round(snap.cursor_t, 2), "event_buffer_s": snap.buffer_s}, "data_excluded_pct": 0.0},
           {"metric": "shots", "method": "Count of released ShotAtGoal events, penalties included; on target = goal or "
            "saved; woodwork counted apart", "tool": "app.engine.stats.event_metrics", "parameters": {},
            "data_excluded_pct": 0.0},
           {"metric": "counts", "method": "Corners, fouls (fouler's team), offsides and cards from released events; a "
            "second yellow counts as a yellow and a red", "tool": "app.engine.stats.event_metrics", "parameters": {},
            "data_excluded_pct": 0.0},
           {"metric": "leaders.most_touches", "method": "Events in which the player plays the ball: passes, shots, duels "
            "won, recoveries, other ball actions and set pieces taken", "tool": "app.engine.stats.event_metrics",
            "parameters": {}, "data_excluded_pct": 0.0}]
    if core.ev["has_passes"]:
        out.append({"metric": "passes", "method": "Play events; accuracy = complete / all", "tool":
                    "app.engine.stats.event_metrics", "parameters": {}, "data_excluded_pct": 0.0})
    if core.fr:
        sg = {"filter": "savitzky_golay", "polyorder": cfg.savgol_polyorder, "window_fraction_of_fs":
              cfg.savgol_window_fraction_of_fs}
        pc = core.fr["passes"]["counts"]
        out += [
            {"metric": "distance_covered", "method": "Sum of valid speed x dT plus estimated gap paths, whole time on "
             "the pitch, valid samples only; team = its outfield players, goalkeepers shown as players; '≈' when 1 % "
             "or more is not measured (guidelines 10.2); reliability symbol from the measured share (95 % and 85 %)",
             "tool": "insidethegame_data.metrics.player_summary", "parameters": sg, "data_excluded_pct": excl},
            {"metric": "top_speed", "method": "Maximum of our own speed over valid, measured samples",
             "tool": "insidethegame_data.metrics.player_summary", "parameters": {"max_kmh": cfg.player_speed_max_kmh},
             "data_excluded_pct": excl},
            {"metric": "speed_zones", "method": "Distance of valid samples by FIFA speed zone; team = outfield players",
             "tool": "app.engine.frame_stats.compute", "parameters": {"zones_kmh": "0-7,7-15,15-20,20-25,25+"},
             "data_excluded_pct": excl},
            {"metric": "counts.sprints", "method": "Runs of valid, measured samples at or above 25 km/h lasting at "
             "least 1 s; two sprints less than 1 s apart count as one; an invalid or estimated sample ends a sprint "
             "and is never bridged; team = outfield players", "tool": "insidethegame_data.metrics.sprint_runs",
             "parameters": {"kmh": frame_stats.SPRINT_KMH, "min_s": frame_stats.SPRINT_MIN_S,
                            "merge_gap_s": cfg.sprint_merge_gap_s}, "data_excluded_pct": excl},
            {"metric": "peak_intensity", "method": "Each player's metres per minute over a rolling five-minute window "
             "(1 s step, tracking clock, ball out of play included, never across half-time); a team is the mean of its "
             "outfield players on the pitch for the whole window (substitutes count once they have played a whole "
             "window); the chart shows the window ending at each minute", "tool":
             "insidethegame_data.metrics.rolling_intensity", "parameters": {"window_s": cfg.peak_window_s,
                                                                             "step_s": cfg.peak_step_s},
             "data_excluded_pct": excl},
            {"metric": "ball_circulation", "method": "Median ball speed while live and in the team's possession, valid "
             f"ball samples, without the {cfg.circulation_exclude_after_s:g} s after each shot and clearance "
             f"({core.fr['circulation']['excluded_kicks'] if core.fr['circulation'] else 0} kicks); 5-minute buckets, "
             "the last one of a half with its added time", "tool": "insidethegame_data.metrics.after_kicks_mask",
             "parameters": {"unit": "km/h", "exclude_after_s": cfg.circulation_exclude_after_s},
             "data_excluded_pct": excl},
            {"metric": "passes.avg_length_m", "method": "Successful passes only (annotated complete): from the ball at "
             "the kick, located with the pipeline's touches (best kick candidate of the passer in the search window), "
             "to the ball at the team-mate's reception (next touch); long ball = 32 m or more. "
             f"{pc['measured']} of {pc['complete']} complete passes measured; {pc['not_located']} kicks not located, "
             f"{pc['not_received']} without a team-mate's next touch, {pc['waiting']} waiting for their frames",
             "tool": "insidethegame_data.metrics.pass_reception",
             "parameters": {"long_ball_m": cfg.long_ball_min_m, "search_s": frame_stats.PASS_SEARCH_S, **pc},
             "data_excluded_pct": 0.0},
            {"metric": "possession", "method": "Share of live-ball samples by the provider's ball possession",
             "tool": "app.engine.frame_stats.compute", "parameters": {}, "data_excluded_pct": 0.0},
            {"metric": "shot_speed", "method": "Peak valid ball speed in the 0.5 s after the located kick (the frame "
             "of the shot map and the xG), headers excluded", "tool": "app.engine.frame_stats.compute",
             "parameters": {"window_s": frame_stats.SHOT_SPEED_WINDOW_S}, "data_excluded_pct": excl},
            {"metric": "acceleration_load", "method": "Sum of |dv/dt| x dT (the total change of speed, tangential "
             "acceleration of guidelines 7.2) over valid samples whose acceleration is measured or approximate (never "
             "noise; estimated gaps have none), in km/h; team = its outfield players; per minute played; Analyst: km/h "
             "per minute in 5-minute buckets (the last one of a half with its added time)",
             "tool": "insidethegame_data.metrics.acceleration_load_samples",
             "parameters": {"acc_window_fraction_of_fs": cfg.acc_window_fraction_of_fs, "unit": "km/h"},
             "data_excluded_pct": excl},
            {"metric": "counts.ball_recoveries", "method": "Balls claimed or intercepted and duels won by the defending "
             "player, each confirmed by the provider possession column changing from the opponent to the team within "
             f"{cfg.recovery_check_s:g} s of the event (live play); that change is where the recovery is located; "
             "tackles won = duels won by the defending player (from the events)",
             "tool": "insidethegame_data.metrics.possession_gain_frame",
             "parameters": {"check_s": cfg.recovery_check_s, "recoveries": len(core.fr["recoveries"])},
             "data_excluded_pct": 0.0},
            {"metric": "counter_attacks", "method": "A ball recovery in the team's own half (ball at its located "
             f"frame), in live play, then a shot of the same team within {cfg.counter_attack_max_s:g} s with the ball "
             "live and never in the opponent's possession in between (provider column)",
             "tool": "insidethegame_data.metrics.counter_attacks", "parameters": {"max_s": cfg.counter_attack_max_s},
             "data_excluded_pct": 0.0},
            {"metric": "zone_map", "method": "Time on a 1 m grid over valid samples in live play, attacking left to "
             f"right, Gaussian smoothing sigma {cfg.zone_map_sigma_m:g} m (reflected at the pitch lines), shown in "
             f"{cfg.zone_map_cell_m[0]:g} x "
             f"{cfg.zone_map_cell_m[1]:g} m cells, normalised to 100 %; team = its outfield players",
             "tool": "insidethegame_data.metrics.zone_map",
             "parameters": {"grid_m": cfg.zone_map_grid_m, "sigma_m": cfg.zone_map_sigma_m,
                            "cell_m": f"{cfg.zone_map_cell_m[0]:g}x{cfg.zone_map_cell_m[1]:g}"}, "data_excluded_pct": excl},
            {"metric": "defensive_shape", "method": "Per live frame where every outfield player on the pitch has a "
             "valid position: defensive line = mean distance from the own goal line of the 2 deepest outfield "
             "players; team length = deepest to most advanced after dropping one player at each end; mean per "
             "5-minute bucket", "tool": "insidethegame_data.metrics.team_shape", "parameters": {"bucket_s": 300},
             "data_excluded_pct": excl},
            {"metric": "ppda", "method": "Opponent passes (Play events at their located kick) in the team's pressing "
             f"zone (the {round(100 * cfg.ppda_zone_share)} % of the pitch nearest the opponent's goal) per defensive "
             "action there: duels in which its player defended (won or lost), fouls committed, balls claimed (at their "
             "located frame, else the annotated position); "
             + "; ".join(f"{TEAM_ID[s]} {v['passes']} passes / {v['actions']} actions" for s, v in core.fr["ppda"].items()),
             "tool": "insidethegame_data.metrics.ppda", "parameters": {"zone_share": cfg.ppda_zone_share},
             "data_excluded_pct": 0.0},
        ]
        out += _shot_methods(core)
    return out


def _shot_methods(core: Core) -> list[dict]:
    """Inspector entries of the shot map, xG and goals vs xG, with how many shots were located on the tracking."""
    shots = list(core.fr["shots"].values())
    n, loc = len(shots), sum(v["located"] for v in shots)
    cand = sum(v["method"] == "kick_candidate" for v in shots)
    fallback = sum(not v["located"] and v["x"] is not None for v in shots)
    missing = sum(v["x"] is None for v in shots)
    m = frame_stats.metrics.xg_model()
    where = (f"{loc} of {n} shots located on the tracking: {cand} at the shooter's best kick candidate of the "
             f"pipeline within {frame_stats.PASS_SEARCH_S:g} s of the event (ground, header or soft kick he owns), "
             f"{loc - cand} at the last frame within {frame_stats.SHOT_CONTACT_M:g} m of the shooter in "
             f"[t - {frame_stats.SHOT_SEARCH_BEFORE_S:g} s, t + {frame_stats.SHOT_SEARCH_AFTER_S:g} s]; {fallback} at "
             f"their annotated position (fallback){f', {missing} without any position' if missing else ''}")
    counts = {"shots": n, "shots_located": loc, "shots_kick_candidate": cand, "shots_annotated_fallback": fallback}
    return [
        {"metric": "shot_map", "method": f"Ball at the located kick frame, attack-normalised; {where}",
         "tool": "insidethegame_data.metrics.shot_kick", "parameters": counts, "data_excluded_pct": 0.0},
        {"metric": "xg", "method": "logit(xG) = a + 2.84 * visible goal angle + b * opposing outfield players with "
         "valid positions in the triangle ball-posts, at the located kick frame (calibration/xg_model.json, fixed "
         f"angle slope from the Soccermatics fit, a and b fitted on real match data); penalties {m['penalty_xg']:g}; "
         f"direct free kicks use the model; {where}", "tool": "insidethegame_data.metrics.shot_xg",
         "parameters": {"intercept": m["intercept"], "angle_slope": m["coef"][1], "opponents_slope": m["coef"][2],
                        "penalty_xg": m["penalty_xg"], **counts}, "data_excluded_pct": 0.0},
        {"metric": "goals_vs_xg", "method": "Goals (own goals excluded) next to the sum of the team's xG, penalties "
         "included; numbers only, no judgement words", "tool": "app.engine.stats.match_stats", "parameters": {},
         "data_excluded_pct": 0.0},
        {"metric": "momentum", "method": "Over the last five minutes of the current period (from its kick-off at the "
         "start of a period, never across half-time): tilt = each team's possession time with the ball in the "
         "opponent's final third (attacking x > L/6, live play, valid ball samples, provider possession) as a share "
         "of both; danger = each team's share of the xG of the shots in the window; momentum = 0.5 x (tilt home - "
         "tilt away) + 0.5 x (danger home - danger away), from -1 to +1, positive = home pushing. No shot in the "
         "window: tilt only; no final-third possession: danger only; neither: 0. One point per minute",
         "tool": "insidethegame_data.metrics.momentum_value",
         "parameters": {"window_s": frame_stats.CFG.momentum_window_s, "tilt_weight": frame_stats.CFG.momentum_tilt_weight},
         "data_excluded_pct": 0.0},
    ]


def recap(core: Core, mode: Mode, favourites: list[str], names: dict[str, str], team_names: dict[str, str],
          inspector: bool) -> dict:
    snap = core.snap
    sc = score(core)
    home, away = team_names.get(HOME, "Home"), team_names.get(AWAY, "Away")
    scorers = []
    for e in core.ev["goals"]:
        minute, added = Snapshot.minute(e.period, snap.clock_s(e.period, e.t))
        pen = " (pen.)" if e.attrs.get("penalty") else ""
        scorers.append(f"{names.get(e.player_id, e.player_id)} {minute}'{f'+{added}' if added else ''}{pen}")
    narrative = f"Full time: {home} {sc['home']}-{sc['away']} {away}."
    if scorers:
        narrative += " Goals: " + ", ".join(scorers) + "."
    poss = _possession(core)
    sh = _team_shots(core)
    if mode is Mode.analyst:
        narrative += f" Shots {sh[0]['shots']}-{sh[1]['shots']} (on target {sh[0]['on_target']}-{sh[1]['on_target']})."
        if poss:
            narrative += f" Possession {poss['teams'][0]['value']:g}-{poss['teams'][1]['value']:g} %."
    key = [{"text": f"{sh[0]['shots']} shot{'' if sh[0]['shots'] == 1 else 's'}", "value": float(sh[0]["shots"]), "unit": "shots",
            "tool": "get_match_stats", "arguments": {"scope": "full_match"}, "source_field": "shots[0].shots"}]
    if poss:
        key.insert(0, {"text": f"{poss['teams'][0]['value']:g} %", "value": poss["teams"][0]["value"], "unit": "%",
                       "tool": "get_match_stats", "arguments": {"scope": "full_match"},
                       "source_field": "possession.teams[0].value"})
    speeds = _top_speeds(core, TOP_SPEEDS_ANALYST if mode is Mode.analyst else 1)  # as /api/stats in this mode
    top = max(speeds, key=lambda x: (x["kmh"], x["player_id"]), default=None)
    if top:  # the player goes with it, so the app can say whose top speed it is
        key.append({"text": f"{top['kmh']:g} km/h", "value": top["kmh"], "unit": "km/h", "tool": "get_match_stats",
                    "arguments": {"scope": "full_match", "player_id": top["player_id"]},
                    "source_field": f"top_speed[{speeds.index(top)}].kmh"})
    favs = [player_focus(core, pid, mode is Mode.analyst) for pid in _favourites(core, favourites)]
    p = {"match_id": snap.match_id, "mode": mode.value, "final_score": sc,
         "headline": f"{home} {sc['home']}-{sc['away']} {away}", "narrative": narrative, "highlight_ids": [],
         "key_numbers": key, "shots": sh, "possession": poss["teams"] if poss else [], "favourite_players": favs,
         "inspector": None}
    un = []
    if not poss:
        un.append({"metric": "possession", "reason": no_frames_reason(snap)})
    if favs and favs[0]["distance"] is None:
        un.append({"metric": "favourite_players.distance", "reason": no_frames_reason(snap)})
    un.append({"metric": "highlight_ids", "reason": "highlights are chosen by VAR (task D-1)"})
    p["unavailable"] = un
    if inspector:
        p["inspector"] = {"trace_id": f"engine-recap-{round(snap.as_of_s)}", "method": "Deterministic recap from the "
                          "engine's numbers: the summary is written by the code (no model call)", "agents": [],
                          "tool_calls": [{"agent": "engine", "tool": "app.engine.stats.recap",
                                          "arguments": {"as_of_s": round(snap.as_of_s, 1)},
                                          "result_summary": p["headline"], "latency_ms": 0}],
                          "evidence": [snap.source], "grounding": {"passed": True, "numbers_checked": len(key),
                                                                    "numbers_unmatched": []},
                          "total_tokens": 0, "latency_ms": 0, "cost_usd": 0.0}
    return p
