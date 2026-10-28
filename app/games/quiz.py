"""Match quiz (GIOCHI.md section 3): 5 questions on the first half, chosen by the code from the engine's statistics.

Rules (26/10): the quiz is played only at half-time (the Games section opens only then), on a
snapshot of the half-time statistics, the same 5 questions in the same order for every fan (seed = match id + phase);
never two questions on the same metric, at most 2 "How much?" questions; Q7 in bands of 2 km/h.

The input is the engine's Data & Statistics payload in Analyst mode (`app.engine.stats.match_stats`), so every answer
is the number the fan can check in Data & Statistics. Ties are judged on the value as shown (e.g. 6.1 km): every
player tied first is a right answer; more than 3 tied first discards the question. A question whose data is missing
is off (Q3, sprint metres per player, is not computed per player yet). The texts are English; The Ref (M-5) will only
rewrite them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from .universe import club_names, full_name, universe

ROUNDS, ANSWER_SECONDS = 5, 20
MAX_HOW_MUCH = 2
MIN_MINUTES_ACCURACY = 20  # Q13: only players with at least 20 minutes


@dataclass(frozen=True)
class Who:
    """ "Who?": the player first in a per-player metric of the stats table (`players`)."""

    qid: str
    metric: str
    text: str
    value: Callable[[dict], float | None]  # from a row of `players`; None = not a candidate
    digits: int  # decimals as shown to the fan
    unit: str


@dataclass(frozen=True)
class HowMuch:
    """ "How much?": 4 contiguous bands of the same width around a team (or match) value."""

    qid: str
    metric: str
    text: str  # may contain {team} and {goals}
    value: Callable[[dict, str], float | None]  # (stats, team_id) -> value
    width: float
    unit: str
    per_team: bool = True
    count: bool = False  # rare counts: options 0, 1, 2, 3 or more; off when 0 for both teams


@dataclass(frozen=True)
class When:
    """ "When?": the 5-minute window of the first half in which a team pushed most (momentum)."""

    qid: str = "Q15"
    metric: str = "momentum"
    text: str = "When did {team} push the most in the first half?"


def _row(key: str) -> Callable[[dict], float | None]:
    return lambda p: p.get(key) or None  # 0 is never a candidate: nobody "made the most" of nothing


def _team(stats: dict, section: str, team: str, key: str = "value") -> float | None:
    rows = stats.get(section)
    rows = rows.get("teams") if isinstance(rows, dict) else rows
    row = next((r for r in rows or [] if r.get("team_id") == team), None)
    return None if row is None else row.get(key)


def _count(key: str) -> Callable[[dict, str], float | None]:
    return lambda s, t: _team(s, "counts", t, key)


def _shots_by_player(stats: dict, key: str) -> dict[str, float]:
    best: dict[str, float] = {}
    for sh in (stats.get("shot_map") or {}).get("shots", []):
        v = sh.get(key)
        if v is not None and not sh.get("is_penalty"):
            best[sh["player_id"]] = max(best.get(sh["player_id"], 0.0), v)
    return best


def _defensive_line(stats: dict, team: str) -> float | None:
    row = next((r for r in stats.get("defensive_shape") or [] if r["team_id"] == team), None)
    pts = [p["value"] for p in (row or {}).get("defensive_line_m", []) if p.get("value") is not None]
    return sum(pts) / len(pts) if pts else None


def _team_km(stats: dict, team: str) -> float | None:
    d = _team(stats, "distance_covered", team, "distance")
    return d["km"] if d else None


WHO = [
    Who("Q1", "distance_covered", "Who ran the furthest in the first half?",
        lambda p: (p.get("distance") or {}).get("km"), 1, "km"),
    Who("Q4", "top_speed", "Who reached the highest top speed?", _row("top_speed_kmh"), 1, "km/h"),
    Who("Q5", "peak_intensity", "Who had the most intense 5 minutes?", _row("peak_m_per_min"), 0, "m/min"),
    Who("Q9", "shots", "Who took the most shots?", _row("shots"), 0, "shots"),
    Who("Q12", "passes", "Who made the most passes?", _row("passes"), 0, "passes"),
    Who("Q13", "passes", f"Who had the best passing accuracy? (players with at least {MIN_MINUTES_ACCURACY} minutes)",
        lambda p: p.get("pass_accuracy_pct") if p.get("minutes_played", 0) >= MIN_MINUTES_ACCURACY and p.get("passes")
        else None, 0, "%"),
    Who("Q16", "touches", "Who touched the ball the most?", _row("touches"), 0, "touches"),
    Who("Q19", "sprints", "Who made the most sprints?", _row("sprints"), 0, "sprints"),
    Who("Q20", "tackles_recoveries", "Who won the ball back the most?", _row("ball_recoveries"), 0, "recoveries"),
    Who("Q21", "acceleration_load", "Who changed speed the most (highest acceleration load)?",
        _row("acceleration_load_kmh"), 0, "km/h"),
    Who("Q23", "fouls", "Who committed the most fouls?", _row("fouls_committed"), 0, "fouls"),
]
# Q6 and Q10 rank the players from the shot map (open play): built at selection time from the stats
SHOT_WHO = [("Q6", "shot_speed", "Who hit the hardest shot?", "speed_kmh", 1, "km/h"),
            ("Q10", "xg", "Who had the biggest chance (the shot with the highest xG)?", "xg", 2, "xG")]

HOW_MUCH = [
    HowMuch("Q2", "distance_covered", "How many km did {team} run in total in the first half?", _team_km, 2, "km"),
    HowMuch("Q7", "ball_circulation", "How fast did {team} move the ball around?",
            lambda s, t: _team(s, "ball_circulation", t), 2, "km/h"),
    HowMuch("Q8", "possession", "How much possession did {team} have?", lambda s, t: _team(s, "possession", t), 5, "%"),
    HowMuch("Q11", "xg", "{team} scored {goals}: how much xG had they created?",
            lambda s, t: _team(s, "goals_vs_xg", t, "xg"), 0.5, "xG"),
    HowMuch("Q14", "effective_playing_time", "How many minutes was the ball really in play in the first half?",
            lambda s, t: (s.get("effective_playing_time") or {}).get("total_min"), 3, "min", per_team=False),
    HowMuch("Q17", "defensive_shape", "How far from their own goal did {team}'s back line play, on average?",
            _defensive_line, 3, "m"),
    HowMuch("Q22", "woodwork", "How many times did {team} hit the post or the bar?", _count("woodwork"), 1, "", count=True),
    HowMuch("Q24", "counter_attacks", "How many counter-attacks did {team} make?",
            lambda s, t: _team(s, "counter_attacks", t, "count"), 1, "", count=True),
    HowMuch("Q25", "offsides", "How many times was {team} caught offside?", _count("offsides"), 1, "", count=True),
    HowMuch("Q26", "penalties", "How many penalties were awarded?",
            lambda s, t: sum(_team(s, "counts", x, "penalties") or 0 for x in _teams(s)), 1, "", per_team=False, count=True),
]


def _teams(stats: dict) -> list[str]:
    return [r["team_id"] for r in stats.get("shots") or []]


def _names() -> dict[str, str]:
    return {p["id"]: full_name(p) for p in universe()["players"]}


def _fmt(v: float, digits: int) -> str:
    return f"{v:.{digits}f}"


def _pick_options(ranked: list[tuple[str, float]], rng: random.Random) -> tuple[list[str], list[str]] | None:
    """GIOCHI.md section 3: (options, right answers) from candidates ranked best first, with values as shown.
    The first value (all tied); the closest below; one from 4th-8th place; one at random; None = discard."""
    if len(ranked) < 4:
        return None
    top = ranked[0][1]
    firsts = [k for k, v in ranked if v == top]
    if len(firsts) > 3:
        return None
    rest = [(k, v) for k, v in ranked if v != top]
    chosen: list[str] = []
    below = [k for k, v in rest if v == rest[0][1]]
    chosen.append(rng.choice(below))
    lower = [k for k, _ in ranked[3:8] if k not in chosen and k not in firsts]
    if not lower:
        lower = [k for k, _ in ranked[3:] if k not in chosen and k not in firsts][:1]
    if lower:
        chosen.append(rng.choice(lower))
    others = [k for k, _ in rest if k not in chosen]
    if others:
        chosen.append(rng.choice(others))
    options = firsts + chosen[: 4 - len(firsts)]
    if len(options) < 4:
        return None
    rng.shuffle(options)
    return options, firsts


def _who(q: Who | tuple, stats: dict, rng: random.Random) -> dict | None:
    names, clubs = _names(), club_names()
    if isinstance(q, Who):
        qid, metric, text, digits, unit = q.qid, q.metric, q.text, q.digits, q.unit
        values = {p["player_id"]: q.value(p) for p in stats.get("players") or [] if p.get("minutes_played", 0) > 0}
    else:
        qid, metric, text, key, digits, unit = q
        values = _shots_by_player(stats, key)
    shown = {k: round(v, digits) for k, v in values.items() if v}
    ranked = sorted(shown.items(), key=lambda kv: (-kv[1], kv[0]))
    picked = _pick_options(ranked, rng)
    if picked is None:
        return None
    options, right = picked
    team = {p["id"]: p["club_id"] for p in universe()["players"]}
    value = f"{_fmt(shown[right[0]], digits)} {unit}".strip()
    fact = f"{' and '.join(names[r] for r in right)}, {value}" + (" each" if len(right) > 1 else "")
    return {"qid": qid, "metric": metric, "question": text,
            "options": [{"option_id": k, "label": f"{names[k]} ({team[k]})", "player_id": k}
                        for k in options],
            "right": right, "fact": fact}


def _bands(q: HowMuch, value: float, rng: random.Random) -> tuple[list[dict], str]:
    if q.count:
        labels = ["0", "1", "2", "3 or more"]
        right = min(int(value), 3)
        return [{"option_id": f"B{i}", "label": lab, "player_id": None} for i, lab in enumerate(labels)], f"B{right}"
    digits = 1 if q.width < 1 else 0
    lo = math.floor(value / q.width) * q.width
    pos = min(rng.randrange(4), int(round(lo / q.width)))  # never a band below 0
    start = lo - pos * q.width
    unit = q.unit if q.unit == "%" else f" {q.unit}"
    opts = [{"option_id": f"B{i}", "label": f"{_fmt(start + i * q.width, digits)}–{_fmt(start + (i + 1) * q.width, digits)}{unit}",
             "player_id": None} for i in range(4)]
    return opts, f"B{pos}"


def _how_much(q: HowMuch, stats: dict, rng: random.Random) -> dict | None:
    teams = _teams(stats)
    if q.count and q.per_team and all(not (q.value(stats, t) or 0) for t in teams):
        return None  # rare counts: an answer that is always 0 is no fun (E-3)
    team = rng.choice(teams) if q.per_team else None
    value = q.value(stats, team)
    if value is None or (q.count and not q.per_team and not value):
        return None
    clubs = club_names()
    goals = _team(stats, "goals_vs_xg", team, "goals") if team else None
    text = q.text.format(team=clubs.get(team, team) if team else "", goals=f"{goals} goal{'s' if goals != 1 else ''}")
    options, right = _bands(q, value, rng)
    shown = str(int(value)) if q.count else _fmt(value, 2 if q.unit == "xG" else 1)
    unit = q.unit if q.unit == "%" else f" {q.unit}" if q.unit else ""
    fact = f"{clubs.get(team, team)}: {shown}{unit}" if team else f"{shown}{unit}"
    return {"qid": q.qid, "metric": q.metric, "question": text, "options": options, "right": [right], "fact": fact}


def _when(q: When, stats: dict, rng: random.Random) -> dict | None:
    series = (stats.get("momentum") or {}).get("series") or []
    teams = _teams(stats)
    if not series or len(teams) != 2:
        return None
    team = rng.choice(teams)
    sign = 1 if team == teams[0] else -1  # the momentum is home minus away
    windows: dict[str, list[float]] = {}
    for p in series:
        start = min((p["minute"] - 1) // 5 * 5, 40)
        windows.setdefault(f"{start}'–{start + 5}'", []).append(sign * p["value"])
    ranked = sorted(((k, round(sum(v) / len(v), 2)) for k, v in windows.items()), key=lambda kv: (-kv[1], kv[0]))
    picked = _pick_options(ranked, rng)
    if picked is None:
        return None
    options, right = picked
    clubs = club_names()
    return {"qid": q.qid, "metric": q.metric, "question": q.text.format(team=clubs.get(team, team)),
            "options": [{"option_id": f"W{k.split(chr(39))[0]}", "label": k, "player_id": None} for k in options],
            "right": [f"W{r.split(chr(39))[0]}" for r in right], "fact": f"{clubs.get(team, team)}: {' and '.join(right)}"}


def session(stats: dict, match_id: str, phase: str) -> list[dict]:
    """The session's questions (at most 5), the same for every fan: drawn with the seed, the first valid ones kept."""
    rng = random.Random(f"quiz/{match_id}/{phase}")
    pool: list = [*WHO, *SHOT_WHO, *HOW_MUCH, When()]
    rng.shuffle(pool)
    out, metrics, how_much = [], set(), 0
    for q in pool:
        metric = q[1] if isinstance(q, tuple) else q.metric
        if metric in metrics or (isinstance(q, HowMuch) and how_much >= MAX_HOW_MUCH):
            continue
        built = (_how_much(q, stats, rng) if isinstance(q, HowMuch) else _when(q, stats, rng) if isinstance(q, When)
                 else _who(q, stats, rng))
        if built is None:
            continue
        metrics.add(metric)
        how_much += isinstance(q, HowMuch)
        built["round_id"] = f"QZ-{len(out) + 1}"
        out.append(built)
        if len(out) == ROUNDS:
            break
    return out
