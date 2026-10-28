"""Build the fake data of the contract (app/contract/fake/) from the synthetic universe.

Run: python -m app.contract.make_fake

Names, ids, shirt numbers, positions and careers come from synthetic/universe/universe.json and
synthetic/SYN-001/match_info.json (universe MERIDIA-1.0). The match story below (goals, shots, cards) is FAKE:
it is not the output of the SYN-001 generator (task S-4), only a consistent example to build against.
Everything is derived from one event list, so score, shots, cards, highlights and stats always agree.
Edit this script, not the JSON files: the test suite checks that the JSON files are up to date.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from itertools import combinations
from pathlib import Path

from . import CONTRACT_VERSION
from . import tickets as tk
from .models import Reliability

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).resolve().parent / "fake"
SERVER = FAKE / "server"
MATCH_ID = "SYN-001"
HOME, AWAY = "BRA", "CAL"
KICKOFF = "2026-10-24T15:00:00+01:00"
P1_LEN, P2_LEN = 47.0, 49.0  # minutes played in each half (45+2, 45+4)


def load():
    u = json.loads((ROOT / "synthetic/universe/universe.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / f"synthetic/{MATCH_ID}/match_info.json").read_text(encoding="utf-8"))
    return u, m


U, M = load()
PLAYERS = {p["id"]: p for p in U["players"]}
CLUBS = {c["id"]: c for c in U["clubs"]}
LINEUP = {t["team_id"]: t["lineup"] for t in M["teams"]}
SLOT = {e["player_id"]: e["playing_position"] for t in M["teams"] for e in t["lineup"]}
IN_MATCH = set(SLOT)


def name(pid: str) -> str:
    p = PLAYERS[pid]
    return f"{p['first_name']} {p['last_name']}"


def team_of(pid: str) -> str:
    return PLAYERS[pid]["club_id"]


def colours(club_id: str) -> dict:
    """Club colours plus the second kit, from the universe (contract v0.2.4)."""
    away = next(d for d in U["club_details"] if d["club_id"] == club_id)["kits"]["away"]
    return {**CLUBS[club_id]["colours"], "away_kit": {k: away[k] for k in ("shirt", "shorts", "socks")}}


def prize() -> dict:
    """The sponsor's prize for the half-time games, from the universe (task L-0)."""
    sp = U["match_preview"]["sponsor"]
    pr = sp["prize"]
    return {"sponsor_name": sp["name"], "sponsor_sector": sp["sector"], "name": pr["name"],
            "description": pr["description"], "value_eur": pr["value_eur"], "how_to_claim": pr["how_to_claim"]}


def picker(pid: str) -> dict:
    p = PLAYERS[pid]
    return {"player_id": pid, "name": name(pid), "team_id": p["club_id"], "shirt_number": p["shirt_number"]}


# ------------------------------------------------------------------ the fake match story (one source of truth)
# (event_id, type, period, minute, added, second, team, player, secondary, qualifiers, extra)
# extra for shots: (x, y, xg, speed_kmh); coordinates normalised: the shooting team attacks +x.
EV = [
    ("E001", "KickOff", 1, 1, 0, 0, HOME, "BRA-09", None, {}, None),
    ("E002", "ShotAtGoal", 1, 6, 0, 12, HOME, "BRA-11", None, {"outcome": "wide"}, (34.0, -12.5, 0.05, 88.2)),
    ("E003", "ShotAtGoal", 1, 14, 0, 40, HOME, "BRA-10", None, {"outcome": "saved"}, (36.5, 4.0, 0.09, 96.7)),
    ("E004", "ShotAtGoal", 1, 18, 0, 5, AWAY, "CAL-09", None, {"outcome": "wide"}, (40.5, 6.0, 0.08, 91.3)),
    ("E005", "ShotAtGoal", 1, 23, 0, 14, HOME, "BRA-09", "BRA-10", {"outcome": "goal"}, (43.0, -2.0, 0.31, 108.4)),
    ("E006", "CornerKick", 1, 27, 0, 30, HOME, "BRA-11", None, {}, None),
    ("E007", "ShotAtGoal", 1, 29, 0, 2, HOME, "BRA-27", None, {"outcome": "blocked"}, (37.0, 9.5, 0.06, 84.0)),
    ("E008", "ShotAtGoal", 1, 33, 0, 50, AWAY, "CAL-10", None, {"outcome": "saved"}, (41.0, -5.5, 0.14, 99.1)),
    ("E009", "Foul", 1, 37, 0, 8, AWAY, "CAL-04", "BRA-10", {}, None),
    ("E010", "Caution", 1, 37, 0, 40, AWAY, "CAL-04", None, {"card": "yellow"}, None),
    ("E011", "ShotAtGoal", 1, 38, 0, 15, HOME, "BRA-08", None, {"outcome": "wide"}, (30.5, 1.5, 0.03, 101.5)),
    ("E012", "ShotAtGoal", 1, 39, 0, 22, AWAY, "CAL-07", None, {"outcome": "blocked"}, (38.0, 12.0, 0.05, 86.4)),
    ("E013", "ShotAtGoal", 1, 41, 0, 3, HOME, "BRA-09", None, {"outcome": "saved"}, (44.5, 3.0, 0.22, 94.8)),
    ("E014", "ShotAtGoal", 1, 44, 0, 31, HOME, "BRA-06", None, {"outcome": "woodwork"}, (33.0, -3.0, 0.07, 104.9)),
    ("E015", "ShotAtGoal", 1, 45, 1, 10, AWAY, "CAL-08", None, {"outcome": "wide"}, (32.0, -8.0, 0.03, 89.5)),
    ("E016", "FinalWhistle", 1, 45, 2, 0, None, None, None, {}, None),
    ("E017", "KickOff", 2, 46, 0, 0, AWAY, "CAL-09", None, {}, None),
    ("E018", "ShotAtGoal", 2, 49, 0, 20, AWAY, "CAL-09", None, {"outcome": "saved"}, (42.0, 2.5, 0.17, 97.6)),
    ("E019", "ShotAtGoal", 2, 52, 0, 45, HOME, "BRA-11", None, {"outcome": "wide"}, (35.5, -10.0, 0.04, 92.0)),
    ("E020", "ShotAtGoal", 2, 58, 0, 11, AWAY, "CAL-09", "CAL-07", {"outcome": "goal", "header": True}, (46.5, 1.0, 0.38, 79.5)),
    ("E021", "ShotAtGoal", 2, 61, 0, 37, HOME, "BRA-10", None, {"outcome": "blocked"}, (36.0, 6.5, 0.07, 90.3)),
    ("E022", "Substitution", 2, 64, 0, 0, AWAY, "CAL-10", "CAL-19", {}, None),
    ("E023", "ShotAtGoal", 2, 66, 0, 48, AWAY, "CAL-19", None, {"outcome": "wide"}, (39.0, -9.0, 0.06, 93.2)),
    ("E024", "ShotAtGoal", 2, 70, 0, 25, HOME, "BRA-27", None, {"outcome": "saved"}, (40.0, 11.0, 0.11, 98.0)),
    ("E025", "Substitution", 2, 71, 0, 0, HOME, "BRA-09", "BRA-46", {}, None),
    ("E026", "Foul", 2, 77, 0, 12, AWAY, "CAL-05", "BRA-46", {"in_penalty_area": True}, None),
    ("E027", "Caution", 2, 77, 0, 50, AWAY, "CAL-05", None, {"card": "yellow"}, None),
    ("E028", "VideoAssistantAction", 2, 78, 0, 30, None, None, None, {"decision": "penalty_confirmed"}, None),
    ("E029", "ShotAtGoal", 2, 79, 0, 40, HOME, "BRA-11", None, {"outcome": "goal", "penalty": True}, (41.5, 0.0, 0.76, 101.2)),
    ("E030", "Substitution", 2, 80, 0, 0, AWAY, "CAL-08", "CAL-22", {}, None),
    ("E031", "ShotAtGoal", 2, 86, 0, 5, AWAY, "CAL-22", None, {"outcome": "saved"}, (44.0, -4.0, 0.24, 103.6)),
    ("E032", "ShotAtGoal", 2, 88, 0, 33, HOME, "BRA-46", None, {"outcome": "wide"}, (37.5, 7.0, 0.05, 87.1)),
    ("E033", "ShotAtGoal", 2, 90, 3, 15, AWAY, "CAL-06", None, {"outcome": "blocked"}, (34.5, 2.0, 0.04, 82.6)),
    ("E034", "FinalWhistle", 2, 90, 4, 0, None, None, None, {}, None),
]


def period_seconds(period: int, minute: int, added: int, second: int) -> float:
    """Minute m covers [(m-1)*60, m*60) s of the first half ([(m-46)*60, ...) of the second); 45+a and 90+a follow."""
    if added:
        return float((44 + added) * 60 + second)
    return float((minute - (1 if period == 1 else 46)) * 60 + second)


def elapsed(period: int, t_s: float) -> float:
    """Minutes since kick-off including the added time of the first half."""
    return t_s / 60 + (P1_LEN if period == 2 else 0.0)


EVENTS = []
for eid, typ, per, minute, added, sec, team, pl, sec_pl, q, extra in EV:
    t = period_seconds(per, minute, added, sec)
    e = {"event_id": eid, "type": typ, "period": per, "minute": minute, "match_time_s": t, "team_id": team,
         "player_id": pl, "secondary_player_id": sec_pl, "x": None, "y": None, "qualifiers": dict(q)}
    if added:
        e["qualifiers"]["added_time"] = float(added)
    if extra:
        e["x"], e["y"] = extra[0], extra[1]
        e["qualifiers"]["xg"] = extra[2]
        e["qualifiers"]["speed_kmh"] = extra[3]
    e["_el"] = elapsed(per, t)
    EVENTS.append(e)
EVENTS.sort(key=lambda e: e["_el"])

# Snapshots served by the mock in each phase: (phase, minute, added, elapsed minutes, clock running)
PHASES = {
    "pre_match": (0, 0, 0.0, False),
    "first_half": (31, 0, 30.5, True),
    "half_time": (45, 0, P1_LEN, False),
    "second_half": (67, 0, P1_LEN + 21.5, True),
    "full_time": (90, 0, P1_LEN + P2_LEN, False),
}


def as_of(el: float) -> float:
    """Playback time in seconds since kick-off (the no-look-ahead cut of a payload)."""
    return round(max(el, 0.0) * 60, 1)


def events_until(el: float) -> list[dict]:
    return [e for e in EVENTS if e["_el"] <= el]


def shots_until(el: float) -> list[dict]:
    return [e for e in events_until(el) if e["type"] == "ShotAtGoal"]


def score_until(el: float) -> dict:
    goals = [e for e in shots_until(el) if e["qualifiers"]["outcome"] == "goal"]
    return {"home": sum(e["team_id"] == HOME for e in goals), "away": sum(e["team_id"] == AWAY for e in goals)}


def public_event(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def on_off(pid: str) -> tuple[float | None, float | None]:
    starting = next(x["starting"] for t in LINEUP.values() for x in t if x["player_id"] == pid)
    on, off = (0.0 if starting else None), None
    for e in EVENTS:
        if e["type"] == "Substitution":
            if e["secondary_player_id"] == pid:
                on = e["_el"]
            if e["player_id"] == pid:
                off = e["_el"]
    return on, off


def minutes_played(pid: str, el: float) -> float:
    starting = next(x["starting"] for t in LINEUP.values() for x in t if x["player_id"] == pid)
    on, off = (0.0 if starting else None), None
    for e in EVENTS:
        if e["type"] == "Substitution" and e["_el"] <= el:
            if e["secondary_player_id"] == pid:
                on = e["_el"]
            if e["player_id"] == pid:
                off = e["_el"]
    if on is None:
        return 0.0
    return max(0.0, min(el, off if off is not None else el) - on)


def label_minute(e: dict) -> str:
    added = int(e["qualifiers"].get("added_time", 0))
    return f"{e['minute']}+{added}'" if added else f"{e['minute']}'"


# ------------------------------------------------------------------ per-scope hand-set aggregates (fake)
SCOPES = {
    # elapsed minutes, possession home %, circulation m/s, passes, accuracy, effective time, corners, fouls, sprints,
    # tackles, recoveries, peak intensity (start, end, m/min), offsides, PPDA
    "live_first_half": dict(el=30.5, poss=57.0, circ=(8.7, 7.0), passes=((168, 87.0, 17.6), (119, 79.6, 21.2)), eff=19.1,
                            counts=((3, 3, 27, 5, 17), (1, 4, 24, 9, 15)), peak=((19, 24, 131.5), (14, 19, 128.0)),
                            offsides=(0, 1), ppda=(9.1, 14.2)),
    "first_half": dict(el=P1_LEN, poss=56.0, circ=(8.6, 7.2), passes=((251, 87.3, 17.8), (178, 79.2, 21.4)), eff=28.4,
                       counts=((4, 5, 41, 8, 26), (2, 7, 38, 13, 22)),
                       peak=((19, 24, 131.5), (14, 19, 128.0)), offsides=(1, 1), ppda=(9.4, 13.9)),
    "live_second_half": dict(el=P1_LEN + 21.5, poss=55.0, circ=(8.4, 7.3), passes=((356, 86.5, 18.1), (272, 78.8, 21.6)),
                             eff=42.0, counts=((5, 7, 60, 12, 37), (4, 10, 58, 19, 34)),
                             peak=((19, 24, 131.5), (55, 60, 132.8)), offsides=(1, 2), ppda=(9.8, 13.1)),
    "full_match": dict(el=P1_LEN + P2_LEN, poss=54.0, circ=(8.3, 7.4), passes=((472, 86.1, 18.3), (371, 78.4, 21.9)),
                       eff=57.9, counts=((7, 11, 83, 17, 49), (5, 14, 81, 25, 47)),
                       peak=((74, 79, 134.2), (55, 60, 132.8)), offsides=(2, 3), ppda=(10.2, 12.7)),
}

RATE = {"GK": 0.055, "CB": 0.105, "FB": 0.113, "DM": 0.118, "CM": 0.121, "AM": 0.116, "W": 0.112, "ST": 0.106}
TOP_SPEEDS = [("BRA-27", 34.6, 1, 29), ("CAL-07", 33.9, 1, 18), ("CAL-19", 34.9, 2, 66), ("BRA-46", 35.1, 2, 77)]
# Per-player shares of the team counts (fake): sprints, passes, defensive work, fouls won; acceleration load in km/h
# per minute played and long balls as a share of the passes.
SPRINT_W = {"GK": 0.0, "CB": 0.6, "FB": 1.3, "DM": 0.8, "CM": 1.0, "AM": 1.1, "W": 1.5, "ST": 1.3}
PASS_W = {"GK": 0.5, "CB": 1.1, "FB": 1.0, "DM": 1.4, "CM": 1.4, "AM": 1.1, "W": 0.8, "ST": 0.6}
DEF_W = {"GK": 0.2, "CB": 1.3, "FB": 1.2, "DM": 1.5, "CM": 1.1, "AM": 0.6, "W": 0.5, "ST": 0.3}
WON_W = {"GK": 0.1, "CB": 0.4, "FB": 0.8, "DM": 0.7, "CM": 0.9, "AM": 1.2, "W": 1.4, "ST": 1.3}
LOAD_PER_MIN = {"GK": 60.0, "CB": 105.0, "FB": 125.0, "DM": 120.0, "CM": 125.0, "AM": 128.0, "W": 130.0, "ST": 122.0}
LONG_BALL = {"GK": 0.35, "CB": 0.12, "FB": 0.06, "DM": 0.05, "CM": 0.04, "AM": 0.02, "W": 0.02, "ST": 0.01}
# Fake counter-attacks: shots within 15 s of winning the ball in the team's own half (no recovery events in the story)
COUNTER_SHOTS = ("E013", "E018", "E031")


def group(pid: str) -> str:
    return PLAYERS[pid]["position_group"]


def is_gk(pid: str) -> bool:
    return group(pid) == "GK"


def reliability(unmeasured_pct: float) -> dict:
    return Reliability.of(100.0 - unmeasured_pct).model_dump(mode="json")


def distance(km: float, un: float) -> dict:
    return {"km": km, "approx": un >= 1.0, "unmeasured_pct": un, "reliability": reliability(un)}


def unmeasured(pid: str) -> float:
    return round(0.2 + (sum(map(ord, pid)) % 17) / 10, 1)  # 0.2 .. 1.8 %


def player_distance(pid: str, el: float, poss: float, analyst: bool) -> dict | None:
    mins = minutes_played(pid, el)
    if mins <= 0:
        return None
    p = PLAYERS[pid]
    km = round(mins * (RATE[p["position_group"]] + (p["shirt_number"] % 5) * 0.001), 2)
    out = {"player_id": pid, "distance": distance(km, unmeasured(pid)), "splits": None}
    if analyst:
        out["splits"] = splits(km, poss)
    return out


def splits(km: float, poss_pct: float) -> dict:
    in_play = round(km * 0.62, 2)
    in_pos = round(in_play * (0.35 + poss_pct / 200), 2)
    return {"ball_in_play_km": in_play, "ball_stopped_km": round(km - in_play, 2),
            "in_possession_km": in_pos, "out_of_possession_km": round(in_play - in_pos, 2)}


def match_minute(el: float) -> int:
    """Clock minute of `el` elapsed playing minutes (the second half starts at 45')."""
    return int(el) if el <= P1_LEN else 45 + int(el - P1_LEN)


def buckets(el: float) -> list[int]:
    """5-minute bucket ends (match minutes) already played at `el`."""
    end = min(45 if el <= P1_LEN else 90, match_minute(el))
    return list(range(5, int(end) + 1, 5))


def minutes(el: float) -> list[int]:
    """Match minutes already played at `el`, one point per minute."""
    end = min(45 if el <= P1_LEN else 90, match_minute(el))
    return list(range(1, int(end) + 1))


def wave(seed: int, i: int, amp: float) -> float:
    return amp * math.sin(seed * 1.7 + i * 0.9)


def team_series(base: tuple[float, float], el: float, amp: float, nd: int = 1) -> list[dict]:
    return [{"team_id": t, "points": [{"minute": m, "value": round(base[k] + wave(k + 1, i, amp), nd)}
                                      for i, m in enumerate(buckets(el))]} for k, t in enumerate((HOME, AWAY))]


def distribute(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Split an integer total by weights, largest remainder first (deterministic)."""
    tw = sum(weights.values())
    if tw <= 0 or total <= 0:
        return {k: 0 for k in weights}
    raw = {k: total * w / tw for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[:total - sum(out.values())]:
        out[k] += 1
    return out


def shot_dict(e: dict, analyst: bool) -> dict:
    header = bool(e["qualifiers"].get("header"))
    return {"event_id": e["event_id"], "team_id": e["team_id"], "player_id": e["player_id"], "minute": e["minute"],
            "x": e["x"], "y": e["y"], "xg": e["qualifiers"]["xg"] if analyst else None,
            "outcome": e["qualifiers"]["outcome"], "is_penalty": bool(e["qualifiers"].get("penalty")),
            "is_header": header, "speed_kmh": None if header else e["qualifiers"]["speed_kmh"]}


def team_shots(el: float) -> list[dict]:
    out = []
    for t in (HOME, AWAY):
        s = [e for e in shots_until(el) if e["team_id"] == t]
        out.append({"team_id": t, "shots": len(s),
                    "on_target": sum(e["qualifiers"]["outcome"] in ("goal", "saved") for e in s),
                    "goals": sum(e["qualifiers"]["outcome"] == "goal" for e in s)})
    return out


def xg_by_team(el: float) -> dict[str, float]:
    return {t: round(sum(e["qualifiers"]["xg"] for e in shots_until(el) if e["team_id"] == t), 2) for t in (HOME, AWAY)}


def fake_top_speed(pid: str, el: float) -> tuple[float, int] | None:
    """(km/h, match minute) of a player's top speed up to `el`; the four scripted peaks, the rest derived."""
    for p, kmh, per, m in TOP_SPEEDS:
        if p == pid and elapsed(per, (m - (1 if per == 1 else 46)) * 60) <= el:
            return kmh, m
    on, _ = on_off(pid)
    if on is None or on > el:
        return None
    base = {"GK": 24.0, "CB": 30.2, "FB": 31.8, "DM": 30.0, "CM": 30.6, "AM": 31.2, "W": 32.4, "ST": 31.6}[group(pid)]
    kmh = round(base + (PLAYERS[pid]["shirt_number"] % 7) * 0.25, 1)
    minute = min(match_minute(on) + 3 + PLAYERS[pid]["shirt_number"] % 20, match_minute(el))
    return kmh, max(minute, 1)


def player_table(scope: str) -> dict[str, dict]:
    """Per-player numbers of a scope (fake), consistent with the team counts and the events."""
    sc = SCOPES[scope]
    el = sc["el"]
    played = [x["player_id"] for t in (HOME, AWAY) for x in LINEUP[t] if minutes_played(x["player_id"], el) > 0]
    evs = events_until(el)
    out: dict[str, dict] = {}
    for k, t in enumerate((HOME, AWAY)):
        ids = [pid for pid in played if team_of(pid) == t]
        mins = {pid: minutes_played(pid, el) for pid in ids}
        c = sc["counts"][k]
        opp_fouls = sc["counts"][1 - k][1]
        n_pass, acc, _ = sc["passes"][k]
        sprints = distribute(c[2], {pid: mins[pid] * SPRINT_W[group(pid)] for pid in ids})
        passes = distribute(n_pass, {pid: mins[pid] * PASS_W[group(pid)] for pid in ids})
        tackles = distribute(c[3], {pid: mins[pid] * DEF_W[group(pid)] for pid in ids})
        recov = distribute(c[4], {pid: mins[pid] * DEF_W[group(pid)] for pid in ids})
        fouls = distribute(c[1], {pid: mins[pid] * DEF_W[group(pid)] for pid in ids})
        won = distribute(opp_fouls, {pid: mins[pid] * WON_W[group(pid)] for pid in ids})
        for pid in ids:
            sh = [e for e in evs if e["type"] == "ShotAtGoal" and e["player_id"] == pid]
            top = fake_top_speed(pid, el)
            shirt = PLAYERS[pid]["shirt_number"]
            out[pid] = {
                "team_id": t, "minutes": mins[pid],
                "distance": player_distance(pid, el, sc["poss"] if t == HOME else 100 - sc["poss"], False)["distance"],
                "top": top, "sprints": sprints[pid], "passes": passes[pid],
                "accuracy": round(min(100.0, max(55.0, acc + (shirt % 5) - 2)), 1) if passes[pid] else 0.0,
                "long_balls": round(passes[pid] * LONG_BALL[group(pid)]),
                "touches": passes[pid] + len(sh) + tackles[pid] + recov[pid] + round(passes[pid] * 0.25),
                "shots": len(sh), "goals": sum(e["qualifiers"]["outcome"] == "goal" for e in sh),
                "xg": round(sum(e["qualifiers"]["xg"] for e in sh), 2),
                "tackles": tackles[pid], "recoveries": recov[pid], "fouls": fouls[pid], "fouls_won": won[pid],
                "yellow": sum(e["type"] == "Caution" and e["player_id"] == pid for e in evs),
                "load": round(mins[pid] * (LOAD_PER_MIN[group(pid)] + (shirt % 5) * 1.5), 1),
                "peak": round(sc["peak"][k][2] + (shirt % 9) - 4 + (8.0 if group(pid) in ("W", "FB", "CM") else 0.0)
                              - (60.0 if is_gk(pid) else 0.0), 1),
            }
    return out


def top_speeds(table: dict[str, dict], per_team: int) -> list[dict]:
    out = []
    for t in (HOME, AWAY):
        best = sorted(((v["top"][0], pid, v["top"][1]) for pid, v in table.items() if v["team_id"] == t and v["top"]),
                      key=lambda b: (-b[0], b[1]))
        out += [{"player_id": pid, "team_id": t, "kmh": kmh, "minute": m} for kmh, pid, m in best[:per_team]]
    return out


def shot_speeds(el: float) -> list[dict]:
    """The hardest shot of each team, headers excluded."""
    out = []
    for t in (HOME, AWAY):
        s = [e for e in shots_until(el) if e["team_id"] == t and not e["qualifiers"].get("header")]
        if s:
            e = max(s, key=lambda e: e["qualifiers"]["speed_kmh"])
            out.append({"player_id": e["player_id"], "team_id": t, "kmh": e["qualifiers"]["speed_kmh"],
                        "minute": e["minute"], "event_id": e["event_id"]})
    return out


def cards(el: float, team: str) -> int:
    return sum(e["type"] == "Caution" and e["team_id"] == team for e in events_until(el))


def counts(scope: str) -> list[dict]:
    sc = SCOPES[scope]
    out = []
    for k, t in enumerate((HOME, AWAY)):
        c = sc["counts"][k]
        own = [e for e in shots_until(sc["el"]) if e["team_id"] == t]
        out.append({"team_id": t, "corners": c[0], "fouls": c[1], "yellow_cards": cards(sc["el"], t), "red_cards": 0,
                    "penalties": sum(bool(e["qualifiers"].get("penalty")) for e in own),
                    "woodwork": sum(e["qualifiers"]["outcome"] == "woodwork" for e in own),
                    "offsides": sc["offsides"][k], "sprints": c[2], "tackles": c[3], "ball_recoveries": c[4]})
    return out


def distance_block(scope: str, analyst: bool) -> dict:
    sc = SCOPES[scope]
    el, poss = sc["el"], sc["poss"]
    teams, players = [], []
    for t in (HOME, AWAY):
        ps = [d for x in LINEUP[t] if (d := player_distance(x["player_id"], el, poss if t == HOME else 100 - poss, analyst))]
        players += ps
        outfield = [d for d in ps if not is_gk(d["player_id"])]  # goalkeepers appear only as players
        km = round(sum(d["distance"]["km"] for d in outfield), 2)
        un = round(sum(d["distance"]["km"] * d["distance"]["unmeasured_pct"] for d in outfield) / km, 1)
        teams.append({"team_id": t, "distance": distance(km, un),
                      "splits": splits(km, poss if t == HOME else 100 - poss) if analyst else None})
    top = dict(max(players, key=lambda d: d["distance"]["km"]))
    if not analyst:
        top["splits"] = None
    return {"teams": teams, "top_runner": top, "players": players if analyst else None}


def zones_of(team: str, km: float, player_id: str | None = None) -> dict:
    z = [round(km * f, 2) for f in (0.40, 0.37, 0.12, 0.07)]
    return {"team_id": team, "player_id": player_id, "km_0_7": z[0], "km_7_15": z[1], "km_15_20": z[2],
            "km_20_25": z[3], "km_over_25": round(km - sum(z), 2)}


def speed_zones(dist: dict) -> list[dict]:
    return [zones_of(td["team_id"], td["distance"]["km"]) for td in dist["teams"]]


def possession(scope: str) -> dict:
    sc = SCOPES[scope]
    return {"teams": [{"team_id": HOME, "value": sc["poss"]}, {"team_id": AWAY, "value": round(100 - sc["poss"], 1)}]}


def circulation(scope: str, analyst: bool) -> dict:
    sc = SCOPES[scope]
    kmh = (round(sc["circ"][0] * 3.6, 1), round(sc["circ"][1] * 3.6, 1))
    return {"teams": [{"team_id": t, "value": kmh[k], "reliability": Reliability.of((97.4, 96.1)[k]).model_dump(mode="json")}
                      for k, t in enumerate((HOME, AWAY))],
            "unit": "km/h", "series": team_series(kmh, sc["el"], 2.2) if analyst else None, "chart_id": "ball_circulation"}


def momentum(el: float) -> dict:
    pts = [{"minute": m, "value": round(max(-1.0, min(1.0, 0.15 + wave(5, i / 5, 0.45))), 2)}
           for i, m in enumerate(minutes(el))]
    return {"current": pts[-1]["value"] if pts else 0.0, "series": pts, "chart_id": "momentum"}


def peak_block(scope: str, table: dict[str, dict], analyst: bool) -> dict:
    sc = SCOPES[scope]
    teams, series = [], []
    for k, t in enumerate((HOME, AWAY)):
        start, end, mpm = sc["peak"][k]
        teams.append({"team_id": t, "start_minute": start, "end_minute": end, "metres_per_minute": mpm})
        pts = []
        for i, m in enumerate(minutes(sc["el"])):
            v = mpm if start < m <= end else 108.0 + wave(k + 7, i / 4, 9.0)
            pts.append({"minute": m, "value": round(v, 1)})
        series.append({"team_id": t, "points": pts})
    players = None
    if analyst:
        players = [{"player_id": pid, "team_id": v["team_id"],
                    "start_minute": sc["peak"][0 if v["team_id"] == HOME else 1][0] + PLAYERS[pid]["shirt_number"] % 3,
                    "end_minute": sc["peak"][0 if v["team_id"] == HOME else 1][0] + PLAYERS[pid]["shirt_number"] % 3 + 5,
                    "metres_per_minute": v["peak"]} for pid, v in sorted(table.items()) if v["minutes"] >= 5]
    return {"teams": teams, "series": series, "players": players, "chart_id": "peak_intensity"}


def acceleration_load(scope: str, table: dict[str, dict], analyst: bool) -> dict:
    sc = SCOPES[scope]
    teams = []
    for t in (HOME, AWAY):
        out = {pid: v for pid, v in table.items() if v["team_id"] == t and not is_gk(pid)}
        total = round(sum(v["load"] for v in out.values()), 1)
        un = round(sum(v["distance"]["unmeasured_pct"] for v in out.values()) / max(len(out), 1), 1)
        teams.append({"team_id": t, "total_kmh": total, "per_minute_kmh": round(total / sc["el"], 1),
                      "reliability": reliability(un)})
    series = team_series(tuple(x["per_minute_kmh"] for x in teams), sc["el"], 60.0) if analyst else None
    return {"teams": teams, "series": series, "chart_id": "acceleration_load"}


def counter_attacks(el: float, analyst: bool) -> dict:
    shots = [e for e in shots_until(el) if e["event_id"] in COUNTER_SHOTS]
    return {"teams": [{"team_id": t, "count": sum(e["team_id"] == t for e in shots)} for t in (HOME, AWAY)],
            "items": [{"team_id": e["team_id"], "minute": e["minute"], "recovery_event_id": None,
                       "shot_event_id": e["event_id"]} for e in shots] if analyst else None}


def effective_time(scope: str) -> dict:
    sc = SCOPES[scope]
    el, eff = sc["el"], sc["eff"]
    first = SCOPES["first_half"]["eff"] if el > P1_LEN else eff
    return {"first_half_min": first, "second_half_min": round(eff - first, 1) if el > P1_LEN else None,
            "total_min": eff, "played_min": round(el, 1), "in_play_pct": round(100 * eff / el, 1),
            "reference_world_cup_2022_min": 59.0, "reference_bundesliga_pct": 62.0}


def leaders(table: dict[str, dict]) -> list[dict]:
    out = []
    for t in (HOME, AWAY):
        rows = {pid: v for pid, v in table.items() if v["team_id"] == t}

        def top(key):
            pid = min(rows, key=lambda p: (-key(rows[p]), p))
            return {"player_id": pid, "value": float(key(rows[pid]))}

        touches = sorted(rows, key=lambda p: (-rows[p]["touches"], p))[:3]
        out.append({"team_id": t, "top_runner": top(lambda v: v["distance"]["km"]), "top_sprinter": top(lambda v: v["sprints"]),
                    "top_passer": top(lambda v: v["passes"]),
                    "most_touches": [{"player_id": p, "value": float(rows[p]["touches"])} for p in touches]})
    return out


def player_row(pid: str, v: dict) -> dict:
    return {"player_id": pid, "team_id": v["team_id"], "minutes_played": round(v["minutes"]), "distance": v["distance"],
            "top_speed_kmh": v["top"][0] if v["top"] else None, "sprints": v["sprints"], "peak_m_per_min": v["peak"],
            "acceleration_load_kmh": v["load"], "passes": v["passes"], "pass_accuracy_pct": v["accuracy"],
            "long_balls": v["long_balls"], "touches": v["touches"], "shots": v["shots"], "goals": v["goals"],
            "xg": v["xg"], "tackles": v["tackles"], "ball_recoveries": v["recoveries"], "fouls_committed": v["fouls"],
            "fouls_won": v["fouls_won"]}


def focus(pid: str, v: dict, el: float, analyst: bool) -> dict:
    """Player Focus card (fake). The files carry a card for every player who played; the mock serves the session's
    favourites only."""
    out = {"player_id": pid, "team_id": v["team_id"], "minutes_played": round(v["minutes"]), "distance": v["distance"],
           "top_speed_kmh": v["top"][0] if v["top"] else None, "top_speed_minute": v["top"][1] if v["top"] else None,
           "sprints": v["sprints"], "acceleration_load_kmh": v["load"], "passes": v["passes"],
           "pass_accuracy_pct": v["accuracy"], "shots": v["shots"], "goals": v["goals"], "touches": v["touches"],
           "tackles": v["tackles"], "ball_recoveries": v["recoveries"], "fouls_committed": v["fouls"],
           "fouls_won": v["fouls_won"], "yellow_cards": v["yellow"], "red_cards": 0,
           "highlight_ids": [h["highlight_id"] for h in HIGHLIGHTS if pid in h["player_ids"] and h["_el"] <= el],
           "xg": None, "speed_zones": None, "zone_map": None, "acceleration_load_series": None}
    if analyst:
        per_min = round(v["load"] / max(v["minutes"], 1.0), 1)
        out.update(xg=v["xg"], speed_zones=zones_of(v["team_id"], v["distance"]["km"], pid),
                   zone_map={**zone_map(v["team_id"], 50.0 + PLAYERS[pid]["shirt_number"] % 10), "player_id": pid},
                   acceleration_load_series=[{"minute": m, "value": round(per_min + wave(PLAYERS[pid]["shirt_number"], i, 12.0), 1)}
                                             for i, m in enumerate(buckets(el)) if m > match_minute(on_off(pid)[0] or 0.0)])
    return out


def starters(team: str) -> list[str]:
    return [x["player_id"] for x in LINEUP[team] if x["starting"]]


def zone_map(team: str, poss: float) -> dict:
    raw = [[1 + ((r * 3 + c * 5 + (7 if team == HOME else 2)) % 9) + (c * poss / 60) for c in range(6)] for r in range(4)]
    total = sum(map(sum, raw))
    cells = [[round(v * 100 / total, 1) for v in row] for row in raw]
    cells[0][0] = round(cells[0][0] + 100 - sum(map(sum, cells)), 1)
    return {"team_id": team, "player_id": None, "rows": 4, "cols": 6, "cells_pct": cells, "chart_id": "zone_map"}


def defensive_shape(team: str, el: float) -> dict:
    k = 0 if team == HOME else 1
    return {"team_id": team,
            "defensive_line_m": [{"minute": m, "value": round((38.0 if k == 0 else 33.5) + wave(k + 4, i, 3), 1)} for i, m in enumerate(buckets(el))],
            "team_length_m": [{"minute": m, "value": round((34.0 if k == 0 else 37.5) + wave(k + 6, i, 2.5), 1)} for i, m in enumerate(buckets(el))],
            "chart_id": "defensive_shape"}


CHARTS = [  # the same list as the engine (app/engine/stats.py), in display order
    ("momentum", "Who is pushing", ["casual", "analyst"]),
    ("shot_map", "Shot map", ["casual", "analyst"]),
    ("xg_cumulative", "Expected goals over time", ["analyst"]),
    ("peak_intensity", "Running intensity", ["casual", "analyst"]),
    ("acceleration_load", "Acceleration load", ["analyst"]),
    ("ball_circulation", "Ball circulation speed", ["analyst"]),
    ("zone_map", "Zones of play", ["analyst"]),
    ("speed_zones", "Distance by speed zone", ["analyst"]),
    ("defensive_shape", "Defensive line and team length", ["analyst"]),
]

METHODS = [
    ("distance_covered", "Sum of distances between cleaned positions (Savitzky-Golay), valid samples only; '≈' when unmeasured time >= 1 %; team = outfield players", "engine.physical.distance_covered", {"filter": "savitzky_golay", "window_s": 0.5}, 0.9),
    ("top_speed", "Maximum of our own speed from cleaned positions, outside the ±1 s validity windows", "engine.physical.top_speed", {"max_kmh": 38}, 0.9),
    ("speed_zones", "Distance split by FIFA speed zones", "engine.physical.speed_zones", {"zones_kmh": "0-7,7-15,15-20,20-25,25+"}, 0.9),
    ("peak_intensity", "Rolling five-minute window, step 1 s, metres per minute per player; team = mean of its outfield players", "engine.physical.peak_intensity", {"window_min": 5}, 0.9),
    ("acceleration_load", "Sum of the absolute changes of speed (|dv/dt| dt), measured samples only", "engine.physical.acceleration_load", {"unit": "km/h"}, 0.9),
    ("ball_circulation", "Median ball speed while the team is in possession and the ball is in play, without the 2 s after shots and clearances", "engine.ball.circulation_speed", {"unit": "km/h"}, 1.2),
    ("possession", "Share of ball-in-play time by team in possession", "engine.events.possession", {}, 0.0),
    ("xg", "Visible goal angle (fixed slope 2.84) and opponents in the shot triangle, calibrated on real match data; penalties 0.79", "engine.shots.xg", {"angle_slope": 2.84, "penalty_xg": 0.79}, 0.0),
    ("momentum", "Territory and danger in the last 5 minutes, weights 0.5 and 0.5", "engine.momentum", {"window_min": 5}, 0.0),
    ("shot_speed", "Peak ball speed in the 0.5 s after the shot, ball in play, headers excluded", "engine.shots.shot_speed", {"max_kmh": 211}, 0.0),
]


COMPUTED_AT = {"live_first_half": "2026-10-24T15:31:00+01:00", "first_half": "2026-10-24T15:47:05+01:00",
               "live_second_half": "2026-10-24T16:24:00+01:00", "full_match": "2026-10-24T16:53:40+01:00"}


def build_stats(scope: str, mode: str) -> dict:
    analyst = mode == "analyst"
    sc = SCOPES[scope]
    el = sc["el"]
    dist = distance_block(scope, analyst)
    table = player_table(scope)
    xg = xg_by_team(el)
    shots = shots_until(el)
    ts = team_shots(el)
    cum = []
    for t in (HOME, AWAY):
        run, pts = 0.0, [{"minute": 0, "value": 0.0}]
        for e in shots:
            if e["team_id"] == t:
                run += e["qualifiers"]["xg"]
                pts.append({"minute": e["minute"], "value": round(run, 2)})
        cum.append({"team_id": t, "points": pts})
    passes = []
    for k, t in enumerate((HOME, AWAY)):
        n, acc, length = sc["passes"][k]
        passes.append({"team_id": t, "passes": n, "accuracy_pct": acc, "avg_length_m": length if analyst else None,
                       "long_balls": sum(v["long_balls"] for v in table.values() if v["team_id"] == t) if analyst else None})
    charts = [{"chart_id": c, "title": ti, "section": "data_stats", "modes": mo} for c, ti, mo in CHARTS if mode in mo]
    locked = [{"chart_id": c, "title": ti, "section": "data_stats", "modes": mo} for c, ti, mo in CHARTS if mode not in mo]
    return {
        "match_id": MATCH_ID, "mode": mode, "scope": "live" if scope.startswith("live") else scope,
        "up_to_minute": 45 if scope == "first_half" else 90 if scope == "full_match" else match_minute(el),
        "as_of_s": as_of(el),
        "score": score_until(el), "charts": charts, "locked_charts": locked,
        "goal_marks": [{"team_id": e["team_id"], "minute": e["minute"]} for e in shots if e["qualifiers"]["outcome"] == "goal"],
        "shots": ts, "counts": counts(scope), "possession": possession(scope),
        "goals_vs_xg": [{"team_id": s["team_id"], "goals": s["goals"], "xg": xg[s["team_id"]]} for s in ts],
        "effective_playing_time": effective_time(scope),
        "player_focus": [focus(pid, v, el, analyst) for pid, v in sorted(table.items())],
        "distance_covered": dist, "top_speed": top_speeds(table, 5 if analyst else 1),
        "peak_intensity": peak_block(scope, table, analyst), "acceleration_load": acceleration_load(scope, table, analyst),
        "speed_zones": speed_zones(dist) if analyst else None,
        "passes": passes, "ball_circulation": circulation(scope, analyst), "shot_speed": shot_speeds(el),
        "counter_attacks": counter_attacks(el, analyst),
        "ppda": [{"team_id": t, "value": sc["ppda"][k]} for k, t in enumerate((HOME, AWAY))] if analyst else None,
        "leaders": leaders(table),
        "players": [player_row(pid, v) for pid, v in sorted(table.items())] if analyst else None,
        "xg": {"teams": [{"team_id": t, "value": xg[t]} for t in (HOME, AWAY)], "cumulative": cum,
               "method": "visible angle and opponents in the shot triangle (Materials/METRICS.md section 9)",
               "chart_id": "xg_cumulative"} if analyst else None,
        "momentum": momentum(el),
        "shot_map": {"shots": [shot_dict(e, analyst) for e in shots], "chart_id": "shot_map"},
        "zone_map": [zone_map(t, sc["poss"] if t == HOME else 100 - sc["poss"]) for t in (HOME, AWAY)] if analyst else None,
        "defensive_shape": [defensive_shape(t, el) for t in (HOME, AWAY)] if analyst else None,
        "inspector": {"methods": [{"metric": a, "method": b, "tool": c, "parameters": d, "data_excluded_pct": e}
                                  for a, b, c, d, e in METHODS],
                      "computed_at": COMPUTED_AT[scope],
                      "source": "fake"},
    }


def build_live(phase: str) -> dict:
    scope = "live_first_half" if phase == "first_half" else "live_second_half"
    minute, added, el, _ = PHASES[phase]
    return {"match_id": MATCH_ID, "phase": phase, "minute": minute, "as_of_s": as_of(el), "score": score_until(el),
            "shots": team_shots(el), "counts": counts(scope)}


# ------------------------------------------------------------------ highlights
# (highlight_id, type, main event, extra event ids, importance, why it mattered)
HIGHLIGHTS_SPEC = [
    ("HL-01", "goal", "E005", [], 92.0, "Ulvestad finished Shimazaki's through ball first time: the derby's opening goal, and the loudest moment of the first half."),
    ("HL-02", "speed_peak", None, [], 41.0, "Peyrol hit 34.6 km/h chasing a long switch, the fastest sprint of the first half."),
    ("HL-03", "card", "E010", ["E009"], 38.0, "Wilstead was booked for stopping a Brackwater counter-attack, so he played the rest of the match on a yellow."),
    ("HL-04", "woodwork", "E014", [], 67.0, "Meulenkamp's shot from outside the box hit the crossbar just before half-time: it could have been 2-0."),
    ("HL-05", "goal", "E020", [], 90.0, "Brightwell headed in van Dalfsen's cross to equalise, after Calderholm's best spell of pressure."),
    ("HL-06", "var_review", "E028", ["E026", "E027"], 74.0, "The VAR confirmed the foul by Sowerby on Netherby inside the box: penalty to Brackwater."),
    ("HL-07", "goal", "E029", [], 95.0, "Kinsella-Ward scored the penalty to put Brackwater back in front with eleven minutes left."),
    ("HL-08", "save", "E031", [], 70.0, "Kirkhurst saved Olatunde's close-range shot to keep the lead in the final minutes."),
]
SPEED_PEAK_EL = elapsed(1, 28 * 60 + 20)


def ev(eid: str) -> dict:
    return next(e for e in EVENTS if e["event_id"] == eid)


def highlight(spec) -> dict:
    hid, typ, main, extra, imp, why = spec
    if main is None:  # speed peak of BRA-27 at 29'
        per, minute, el, team, pids, eids, lab = 1, 29, SPEED_PEAK_EL, HOME, ["BRA-27"], [], "29' – Top speed: Clément Peyrol, 34.6 km/h"
    else:
        e = ev(main)
        per, minute, el, team = e["period"], e["minute"], e["_el"], e["team_id"]
        eids = extra + [main]
        pids = [p for p in (e["player_id"], e["secondary_player_id"]) if p]
        if typ == "goal":
            pen = " (penalty)" if e["qualifiers"].get("penalty") else ""
            lab = f"{label_minute(e)} – Goal by {name(e['player_id'])}{pen} ({CLUBS[team]['name']})"
        elif typ == "woodwork":
            lab = f"{label_minute(e)} – Off the crossbar by {name(e['player_id'])}"
        elif typ == "card":
            lab = f"{label_minute(e)} – Yellow card for {name(e['player_id'])} ({CLUBS[team]['name']})"
        elif typ == "var_review":
            foul = ev("E026")
            team, pids = HOME, [foul["secondary_player_id"], foul["player_id"]]
            lab = f"{label_minute(e)} – VAR review: penalty to {CLUBS[HOME]['name']}"
        else:  # save by the goalkeeper of the other team
            gk = "BRA-01" if team == AWAY else "CAL-01"
            pids = [gk, e["player_id"]]
            team = team_of(gk)
            lab = f"{label_minute(e)} – Save by {name(gk)} from {name(e['player_id'])}"
    main_ev = ev(main) if main else None
    return {
        "schema_version": "v0", "highlight_id": hid, "type": typ, "period": per, "minute": minute, "team_id": team,
        "player_ids": pids, "event_ids": eids, "label": lab, "why_it_mattered": why, "importance": imp,
        "score_after": score_until(el),
        "xg": main_ev["qualifiers"].get("xg") if main_ev else None,
        "inspector": {"importance_factors": {"type": round(imp * 0.5, 1), "minute": round(imp * 0.15, 1),
                                             "game_state": round(imp * 0.3, 1),
                                             "xg": round(imp - imp * 0.95, 1)},
                      "candidate_rank": 0, "labelled_by": "VAR", "model": "gpt-6-sol",
                      "trace_id": f"trace-var-{hid.lower()}"},
        "_el": el,
    }


HIGHLIGHTS = sorted((highlight(s) for s in HIGHLIGHTS_SPEC), key=lambda h: h["_el"])
for rank, h in enumerate(sorted(HIGHLIGHTS, key=lambda h: -h["importance"]), start=1):
    h["inspector"]["candidate_rank"] = rank


# ------------------------------------------------------------------ games
def ttt_grid() -> tuple[list[dict], list[dict], dict]:
    """A deterministic grid from real crossings of the universe (logic of generator/insidethegame_gen/games.py,
    re-implemented read-only): criteria are career clubs (never BRA/CAL) and nationalities; answers are the
    50 squad players; every cell has >= 2 answers. Among the grids with one nationality column, pick the one
    with the most cells of 2-3 answers (harder than the 'richest' example of the spec)."""
    crit: dict[tuple[str, str], set[str]] = {}
    for p in U["players"]:
        for sp in p["career"]:
            if sp["club_id"] not in (HOME, AWAY):
                crit.setdefault(("club", sp["club_id"]), set()).add(p["id"])
        crit.setdefault(("nationality", p["nationality"]), set()).add(p["id"])

    def ans(a, b):
        return set() if a[0] == b[0] == "nationality" else crit[a] & crit[b]

    keys = sorted(crit)
    adj = {k: {o for o in keys if o != k and len(ans(k, o)) >= 2} for k in keys}
    best = None
    for nat in sorted(k for k in keys if k[0] == "nationality" and k[1] != "Meridia"):
        rows_pool = sorted(c for c in adj[nat] if c[0] == "club")
        for rows in combinations(rows_pool, 3):
            common = sorted(c for c in adj[rows[0]] & adj[rows[1]] & adj[rows[2]] if c[0] == "club" and c not in rows)
            for cols in combinations(common, 2):
                cells = [len(ans(r, c)) for r in rows for c in (*cols, nat)]
                key = (sum(2 <= n <= 3 for n in cells), -max(cells), rows, cols)
                if best is None or key[:2] > best[0][:2]:
                    best = (key, list(rows), [*cols, nat])
    _, rows, cols = best

    def crit_dict(c):
        return {"kind": c[0], "id": c[1], "label": CLUBS[c[1]]["name"] if c[0] == "club" else c[1]}

    sol = {f"{r},{c}": sorted(ans(rows[r], cols[c])) for r in range(3) for c in range(3)}
    return [crit_dict(c) for c in rows], [crit_dict(c) for c in cols], sol


def former_clubs(pid: str) -> list[dict]:
    return [sp for sp in PLAYERS[pid]["career"] if sp["club_id"] not in (HOME, AWAY)]


def guess_transfers_pick() -> tuple[str, list[dict]]:
    """First player (by id) with >= 3 paid transfers at clubs other than BRA/CAL whose 3 clubs identify only them."""
    for pid in sorted(PLAYERS):
        paid = [sp for sp in former_clubs(pid) if sp["move"] == "transfer" and sp["fee_eur"] > 0]
        seen, three = set(), []
        for sp in paid:
            if sp["club_id"] not in seen:
                seen.add(sp["club_id"])
                three.append(sp)
        if len(three) < 3:
            continue
        three = three[:3]
        clubs = {sp["club_id"] for sp in three}
        holders = [q for q in PLAYERS if clubs <= {sp["club_id"] for sp in PLAYERS[q]["career"]}]
        same_group = [q for q in PLAYERS if q != pid and PLAYERS[q]["position_group"] == PLAYERS[pid]["position_group"]]
        if holders == [pid] and len(same_group) >= 3:
            return pid, three
    raise RuntimeError("no player for the transfers version")


def options_for(pid: str, seed: int) -> list[dict]:
    group = PLAYERS[pid]["position_group"]
    others = sorted(q for q in PLAYERS if q != pid and PLAYERS[q]["position_group"] == group)
    picks = [others[(seed + 3 * i) % len(others)] for i in range(3)]
    picks = list(dict.fromkeys(picks))
    while len(picks) < 3:
        picks.append(next(q for q in others if q not in picks))
    opts = sorted([pid, *picks], key=lambda q: (sum(map(ord, q)) * (seed + 7)) % 101)
    return [picker(q) for q in opts]


# ------------------------------------------------------------------ tickets (fake, public demo codes)
TICKETS_PER_TIER = 3
TICKET_VALID = ("2026-10-20T00:00:00+01:00", "2026-11-10T23:59:59+01:00")  # the whole demo and judging window
# (stand, sector, row, first seat) per tier; demo passes have no seat
TICKET_SEATS = {"silver": ("N", "N2", "14", 21), "gold": ("W", "W3", "8", 11), "demo": None}


def fake_code(tier: str, i: int) -> str:
    """Deterministic fake code, e.g. GNT-SYN001-G-XXXX-XXXX. Public demo codes: listed in app/contract/README.md."""
    letter = tk.TIER_LETTER[tk.TicketTier(tier)]
    digest = hashlib.sha256(f"gantry-fake-ticket/{MATCH_ID}/{tier}/{i}".encode()).digest()
    chars = "".join(tk.CODE_ALPHABET[b % len(tk.CODE_ALPHABET)] for b in digest[:8])
    return f"GNT-{tk.match_part(MATCH_ID)}-{letter}-{chars[:4]}-{chars[4:]}"


def ticket_manifest() -> dict:
    """Plain manifest in the shape the ticket generator (S-4) will write to synthetic/SYN-001/tickets/manifest.json."""
    out = []
    for tier, seat in TICKET_SEATS.items():
        letter = tk.TIER_LETTER[tk.TicketTier(tier)]
        for i in range(1, TICKETS_PER_TIER + 1):
            tid = f"TKT-{tk.match_part(MATCH_ID)}-{letter}-{i:04d}"
            out.append({"ticket_id": tid, "code": fake_code(tier, i), "tier": tier,
                        "seat": None if seat is None else {"stand_id": seat[0], "sector_id": seat[1], "row": seat[2],
                                                           "seat": str(seat[3] + i - 1)},
                        "files": {fmt: f"{tid}.{fmt}" for fmt in ("svg", "pdf", "png")}})
    return {"match_id": MATCH_ID, "valid_from": TICKET_VALID[0], "valid_until": TICKET_VALID[1], "tickets": out}


FAKE_TICKET_CODES: dict[str, list[str]] = {
    tier: [fake_code(tier, i) for i in range(1, TICKETS_PER_TIER + 1)] for tier in TICKET_SEATS}


def session_view(tier: str, header: dict, prefs: dict) -> dict:
    f = tk.features(tk.TicketTier(tier))
    return {"tier": tier, "allowed_modes": f["allowed_modes"], "inspector_enabled": f["inspector"],
            "demo_panel_enabled": f["demo_panel"], "multi_device": f["multi_device"], "match": header, "preferences": prefs}


# ------------------------------------------------------------------ write everything
def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build() -> dict[str, object]:
    out: dict[str, object] = {}
    home, away = CLUBS[HOME], CLUBS[AWAY]
    stadium = U["stadiums"][0]
    header = {"match_id": MATCH_ID, "home_team_id": HOME, "away_team_id": AWAY, "home_name": home["name"],
              "away_name": away["name"], "title": "The Estuary Derby", "kickoff": KICKOFF, "stadium_name": stadium["name"]}
    prefs = {"mode": "analyst", "favourite_player_ids": ["BRA-09", "BRA-06"], "favourites_locked": False, "language": "en"}
    out["login.json"] = {"token": "fake-token-demo-0001", "token_type": "bearer", "session": session_view("demo", header, prefs)}
    out["session.json"] = out["login.json"]["session"]
    out["session_silver.json"] = session_view("silver", header, {**prefs, "mode": "casual"})
    out["preferences.json"] = prefs

    staff = {s["id"]: s for s in U["staff"]}
    offs = {o["id"]: o for o in U["officials"]}
    teams = []
    for t in M["teams"]:
        c = CLUBS[t["team_id"]]
        coach = next(staff[s["staff_id"]] for s in t["staff"] if s["role"] == "head_coach")
        teams.append({"team_id": t["team_id"], "role": t["role"] if t["role"] == "home" else "away", "name": c["name"],
                      "short_name": c["short_name"], "nickname": c["nickname"], "colours": colours(t["team_id"]),
                      "formation": t["formation"], "head_coach": f"{coach['first_name']} {coach['last_name']}",
                      "lineup": [{"player_id": x["player_id"], "name": name(x["player_id"]),
                                  "short_name": PLAYERS[x["player_id"]]["short_name"], "shirt_number": x["shirt_number"],
                                  "position": x["playing_position"], "starting": x["starting"], "captain": x["captain"]}
                                 for x in t["lineup"]]})
    comp = next(c for c in U["competitions"] if c["id"] == M["competition_id"])
    out["match.json"] = {"match_id": MATCH_ID, "competition": comp["name"], "season": M["season"],
                         "matchday": M["matchday"], "title": "The Estuary Derby", "kickoff": KICKOFF,
                         "stadium_name": stadium["name"], "stadium_capacity": stadium["capacity"], "teams": teams,
                         "officials": [{"role": o["role"], "name": f"{offs[o['official_id']]['first_name']} {offs[o['official_id']]['last_name']}"}
                                       for o in M["officials"]]}

    from .phases import sections_for
    from .models import Phase
    server_times = {"pre_match": "14:40:00", "first_half": "15:30:30", "half_time": "15:52:00",
                    "second_half": "16:24:30", "full_time": "16:53:00"}
    for ph, (minute, added, el, running) in PHASES.items():
        out[f"match_state_{ph}.json"] = {
            "match_id": MATCH_ID, "phase": ph, "minute": minute, "added_time": added, "clock_running": running,
            "score": score_until(el),
            "sections": [s.model_dump(mode="json") for s in sections_for(Phase(ph))], "as_of_s": as_of(el),
            "server_time": f"2026-10-24T{server_times[ph]}+01:00"}
    out["events.json"] = {"match_id": MATCH_ID, "as_of_s": as_of(P1_LEN + P2_LEN), "events": [public_event(e) for e in EVENTS]}

    out["stats_live_first_half.json"] = build_live("first_half")
    out["stats_live_second_half.json"] = build_live("second_half")
    for scope in ("live_first_half", "first_half", "live_second_half", "full_match"):
        for mode in ("casual", "analyst"):
            out[f"stats_{mode}_{scope}.json"] = build_stats(scope, mode)

    out["highlights.json"] = {"match_id": MATCH_ID, "as_of_s": as_of(P1_LEN + P2_LEN), "highlights": [{k: v for k, v in h.items() if not k.startswith("_")} for h in HIGHLIGHTS]}

    out["games.json"] = {"games": [
        {"game_id": "tiki_taka_toe", "title": "Tic Tac Goal", "available": True, "unlocks_at_phase": None,
         "rules": "Fill a cell with a player of the two squads who meets both its row and column criteria. A wrong answer "
                  "loses the turn; 20 s per turn, 10 minutes per game. Three in a row wins."},
        {"game_id": "guess_the_player", "title": "Guess the player", "available": True, "unlocks_at_phase": None,
         "rules": "A player's whole career, with the transfer fees and the current club hidden: pick the right name "
                  "among four. 5 questions, 20 s each."},
        {"game_id": "quiz", "title": "Match quiz", "available": True, "unlocks_at_phase": None,
         "rules": "Questions on this match's first half, at half-time."}],
        "prize": prize()}
    squad = sorted((p for p in U["players"] if p["club_id"] in (HOME, AWAY)), key=lambda p: (p["club_id"] != HOME, p["shirt_number"]))
    out["players.json"] = {"players": [picker(p["id"]) for p in squad]}

    rows, cols, sol = ttt_grid()
    out["ttt_game.json"] = {
        "game_session_id": "TTT-0001", "rows": rows, "cols": cols,
        "board": [{"row": r, "col": c, "owner": None, "player_id": None} for r in range(3) for c in range(3)],
        "opponent": {"display_name": "Fan 0482 (Foundry End)", "is_bot": True}, "my_symbol": "X", "turn": "me",
        "turn_seconds": 20, "turn_deadline": "2026-10-24T15:52:30+01:00", "status": "playing"}
    first_answer = sol["0,0"][0]
    played = json.loads(json.dumps(out["ttt_game.json"]))
    played["board"][0].update(owner="me", player_id=first_answer)
    played.update(turn="opponent", turn_deadline="2026-10-24T15:52:58+01:00")
    out["ttt_move.json"] = {"correct": True, "reason": None, "elapsed_ms": 8240, "game": played, "points_awarded": 10}

    career_pid = "BRA-05"
    out["guess_career.json"] = {
        "round_id": "GTP-0001", "version": "career",
        "career": [{"club_name": CLUBS[sp["club_id"]]["name"], "from_season": sp["from_season"], "to_season": sp["to_season"]}
                   for sp in former_clubs(career_pid)],
        "transfers": None, "options": options_for(career_pid, 1), "answer_seconds": 30,
        "deadline": "2026-10-24T15:53:30+01:00"}
    tr_pid, three = guess_transfers_pick()
    out["guess_transfers.json"] = {
        "round_id": "GTP-0002", "version": "transfers", "career": None,
        "transfers": [{"club_name": CLUBS[sp["club_id"]]["name"], "season": sp["from_season"], "fee_eur": sp["fee_eur"],
                       "move": sp["move"]} for sp in three],
        "options": options_for(tr_pid, 2), "answer_seconds": 30, "deadline": "2026-10-24T15:54:30+01:00"}
    out["guess_answer.json"] = {"correct": True, "correct_option_id": career_pid, "elapsed_ms": 11400,
                                "points_awarded": 100, "speed_bonus": 31,
                                "fact": f"{name(career_pid)} played for " + ", ".join(CLUBS[sp['club_id']]['name'] for sp in former_clubs(career_pid)) + "."}

    st = out["stats_analyst_first_half.json"]
    runners = sorted(st["distance_covered"]["players"], key=lambda d: -d["distance"]["km"])
    quiz_opts = sorted(runners[:4], key=lambda d: d["player_id"])
    letters = "ABCD"
    out["quiz_round.json"] = {
        "round_id": "QZ-0001", "question": "Who covered the most distance in the first half?",
        "metric": "distance_covered", "scope": "first_half",
        "options": [{"option_id": letters[i], "label": name(d["player_id"]), "player_id": d["player_id"]} for i, d in enumerate(quiz_opts)],
        "answer_seconds": 20, "deadline": "2026-10-24T15:55:20+01:00"}
    top = runners[0]
    quiz_correct = letters[[d["player_id"] for d in quiz_opts].index(top["player_id"])]
    approx = "≈" if top["distance"]["approx"] else ""
    quiz_fact = f"{name(top['player_id'])} covered {approx}{top['distance']['km']} km in the first half."
    out["quiz_answer.json"] = {"correct": True, "correct_option_id": quiz_correct, "elapsed_ms": 6800,
                               "points_awarded": 100, "speed_bonus": 33, "fact": quiz_fact}
    out["leaderboard.json"] = {"match_id": MATCH_ID, "my_rank": 3, "my_points": 241, "entries": [
        {"rank": 1, "display_name": "Fan 1907 (Lamplighters Stand)", "points": 388, "is_me": False, "is_bot": False},
        {"rank": 2, "display_name": "Fan 0482 (Foundry End)", "points": 305, "is_me": False, "is_bot": True},
        {"rank": 3, "display_name": "You", "points": 241, "is_me": True, "is_bot": False},
        {"rank": 4, "display_name": "Fan 2231 (Harbour Stand)", "points": 198, "is_me": False, "is_bot": False},
        {"rank": 5, "display_name": "Fan 0045 (Main Stand)", "points": 120, "is_me": False, "is_bot": True}]}

    km = top["distance"]["km"]
    km_text = f"{approx}{km} km"
    out["archie_answer.json"] = {
        "answer_id": "ANS-0001",
        "text": (f"What a shift from {name(top['player_id'])}! He covered {km_text} in the first half, more than anyone "
                 "on the pitch. You'll find every player's distance in Data & Statistics, in the Running table."),
        "language": "en", "in_scope": True,
        "grounding": [{"text": km_text, "value": km, "unit": "km", "tool": "get_distance_covered",
                       "arguments": {"scope": "first_half"}, "source_field": "distance_covered.top_runner.distance.km"}],
        "points_to": {"chart_id": "running_table", "section": "data_stats", "locked": False, "unlocks_at_phase": None},
        "inspector": {
            "trace_id": "trace-archie-0001",
            "method": "Archie routed the question to The Analyst, which called one deterministic tool; the grounding validator matched every number in the answer.",
            "agents": [
                {"agent": "Archie", "model": "gpt-6-luna", "role": "orchestrator and voice", "tokens_in": 1840, "tokens_out": 96, "latency_ms": 910},
                {"agent": "The Analyst", "model": "gpt-6-sol", "role": "statistics via tools", "tokens_in": 1210, "tokens_out": 64, "latency_ms": 1180}],
            "tool_calls": [{"agent": "The Analyst", "tool": "get_distance_covered", "arguments": {"scope": "first_half"},
                            "result_summary": f"top_runner={top['player_id']} {km} km (unmeasured {top['distance']['unmeasured_pct']} %)", "latency_ms": 12}],
            "evidence": ["stats:first_half"],
            "grounding": {"passed": True, "numbers_checked": 1, "numbers_unmatched": []},
            "total_tokens": 3210, "latency_ms": 2140, "cost_usd": 0.0012}}

    full = {m: out[f"stats_{m}_full_match.json"] for m in ("casual", "analyst")}
    for mode in ("casual", "analyst"):
        fs = full[mode]
        favs = fs["player_focus"]  # every player who played; the mock serves the session's favourites only
        sc = score_until(P1_LEN + P2_LEN)
        gx = {g["team_id"]: g["xg"] for g in fs["goals_vs_xg"]}
        narrative = (
            "Ulvestad opened the scoring in the 23rd minute, Brightwell headed Calderholm level just before the hour, "
            "and Kinsella-Ward's penalty, confirmed by the VAR, won the Estuary Derby for Brackwater."
            if mode == "casual" else
            f"Brackwater had {fs['possession']['teams'][0]['value']} % of the ball and created {gx[HOME]} xG "
            f"against {gx[AWAY]}; Calderholm's direct play produced fewer, but closer, chances.")
        hardest = max(fs["shot_speed"], key=lambda x: x["kmh"])
        idx = fs["shot_speed"].index(hardest)
        key = [{"text": f"{fs['possession']['teams'][0]['value']} %", "value": fs["possession"]["teams"][0]["value"], "unit": "%",
                "tool": "get_possession", "arguments": {"scope": "full_match"}, "source_field": "possession.teams[0].value"},
               {"text": f"{hardest['kmh']} km/h", "value": hardest["kmh"], "unit": "km/h",
                "tool": "get_shot_speed", "arguments": {"scope": "full_match"}, "source_field": f"shot_speed[{idx}].kmh"}]
        if mode == "analyst":
            key.append({"text": str(gx[HOME]), "value": gx[HOME], "unit": "xG",
                        "tool": "get_xg", "arguments": {"scope": "full_match", "team_id": HOME}, "source_field": "xg.teams[0].value"})
        out[f"recap_{mode}.json"] = {
            "match_id": MATCH_ID, "mode": mode, "final_score": sc,
            "headline": f"{home['name']} {sc['home']}-{sc['away']} {away['name']}: the derby stays amber",
            "narrative": narrative, "highlight_ids": [h["highlight_id"] for h in HIGHLIGHTS if h["type"] == "goal" or h["importance"] >= 70],
            "key_numbers": key, "shots": fs["shots"], "possession": fs["possession"]["teams"], "favourite_players": favs,
            "inspector": {"trace_id": f"trace-recap-{mode}", "method": "Archie wrote the recap from deterministic tool results; every number was checked.",
                          "agents": [{"agent": "Archie", "model": "gpt-6-luna", "role": "orchestrator and voice", "tokens_in": 3020, "tokens_out": 210, "latency_ms": 1650},
                                     {"agent": "The Analyst", "model": "gpt-6-sol", "role": "statistics via tools", "tokens_in": 1500, "tokens_out": 90, "latency_ms": 1240}],
                          "tool_calls": [{"agent": "The Analyst", "tool": k["tool"], "arguments": k["arguments"], "result_summary": k["text"], "latency_ms": 9} for k in key],
                          "evidence": ["stats:full_match", "highlights"],
                          "grounding": {"passed": True, "numbers_checked": len(key), "numbers_unmatched": []},
                          "total_tokens": 4820, "latency_ms": 2950, "cost_usd": 0.0019}}

    # My ticket: one example per tier (first ticket of each tier, at kick-off)
    reg = tk.registry_from_manifest(ticket_manifest(), key=tk.DEV_SALT)
    stadium_sv = tk.stadium_of(MATCH_ID)
    for tier in tk.TIER_LETTER:
        t = next(x for x in reg.tickets if x.tier == tier)
        out[f"ticket_{tier.value}.json"] = tk.ticket_info(t, header, stadium_sv, datetime.fromisoformat(KICKOFF))

    # server-only data (never served as such): hashes and tiers, never the codes
    out["server/tickets.json"] = reg.model_dump(mode="json")
    out["server/answers.json"] = {
        "tiki_taka_toe": {"TTT-0001": sol},
        "guess_the_player": {"GTP-0001": {"correct": career_pid, "fact": out["guess_answer.json"]["fact"]},
                             "GTP-0002": {"correct": tr_pid, "fact": f"{name(tr_pid)}: " + "; ".join(
                                 f"{CLUBS[sp['club_id']]['name']} {sp['from_season']} for €{sp['fee_eur']:,}" for sp in three) + "."}},
        "quiz": {"QZ-0001": {"correct": quiz_correct, "fact": quiz_fact}},
        "contract_version": CONTRACT_VERSION}
    return out


def main() -> None:
    files = build()
    for old in FAKE.rglob("*.json"):
        old.unlink()
    for rel, data in files.items():
        dump(FAKE / rel, data)
    print(f"wrote {len(files)} files to {FAKE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
