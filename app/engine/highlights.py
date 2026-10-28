"""VAR: the key moments of the match (task AG-3), deterministic and in background.

VAR never talks to the fans. From the released events and frames of a `Snapshot` (no look-ahead) it finds the
candidate moments of VAR.md section 1, merges the events of one action, scores each moment with section 2 and writes
label and "why it mattered" from the English templates of section 3 (no model call: every word and number comes from
the code). `/api/highlights` lists the moments with a score of at least `LIVE_MIN`; the Recap keeps every goal, then
the other moments by score, up to `RECAP_MAX`.

Score = base x minute factor x game-state factor + xG bonus, from what is known at the instant of the moment (a goal
at 88' that puts a team ahead is a lead goal, never "the winner"). The contract's `importance` is 0-100: it is the
score as a share of the highest score the formula can give (`MAX_SCORE`, 156 with the weights below), so it never
changes after the fact and is the same scale in every match; the raw score is in the Inspector.

Coordinates as in the events: in the attacking team's direction the goal is at x = +52.5 m, posts at y = +-3.66 m,
crossbar at z = 2.44 m. A rule that needs the ball's tracking (a shot that just missed, a save near a post or high, a
goal-line clearance, a goal ruled out for offside, a foul that stops a dangerous break) is skipped without frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .frame_stats import attack_sign, gk_ids
from .sources.base import AWAY, HOME, TEAM_ID
from .window import Snapshot

# thresholds (VAR.md section 1, M-6 of 26/10: chosen on the new xG of E-1)
BIG_SAVE_XG = 0.12
BIG_CHANCE_XG = 0.20
LIVE_MIN = 45.0
RECAP_MAX = 10
XG_BONUS = 20.0

BASE = {"goal": 100, "penalty_missed": 90, "woodwork": 90, "big_save": 80, "near_miss": 80, "big_chance": 80,
        "goal_line_clearance": 80, "red_card": 70, "var_review": 70, "offside_goal": 60, "skill": 50,
        "dangerous_foul": 40}
XG_BONUS_TYPES = {"big_save", "big_chance", "penalty_missed", "near_miss"}
CONTRACT_TYPE = {"goal": "goal", "penalty_missed": "save", "woodwork": "woodwork", "big_save": "save",
                 "near_miss": "big_chance", "big_chance": "big_chance", "goal_line_clearance": "save",
                 "red_card": "card", "var_review": "var_review", "offside_goal": "other",
                 "skill": "spectacular_play", "dangerous_foul": "other"}
SHOT_TYPES = {"goal", "penalty_missed", "woodwork", "big_save", "near_miss", "big_chance", "goal_line_clearance"}


def minute_factor(minute: int) -> float:
    """x1.0 up to 59', x1.1 from 60' to 74', x1.2 from 75' (added time included)."""
    return 1.0 if minute < 60 else 1.1 if minute < 75 else 1.2


def goal_factor(diff_before: int) -> float:
    """Goals, on the goal difference before the goal from the scorer's side: level (the goal gives the lead) x1.3,
    one behind (an equaliser) x1.2, one ahead (to +2) x1.0, two or more ahead or a consolation goal x0.8."""
    if diff_before == 0:
        return 1.3
    if diff_before == -1:
        return 1.2
    if diff_before == 1:
        return 1.0
    return 0.8


def state_factor(diff: int) -> float:
    """Other moments, on the goal difference at that instant: 0 or 1 x1.2, 2 x1.0, 3 or more x0.8."""
    d = abs(diff)
    return 1.2 if d <= 1 else 1.0 if d == 2 else 0.8


MAX_SCORE = max(BASE[k] * minute_factor(90) * (goal_factor(0) if k == "goal" else state_factor(0))
                + (XG_BONUS if k in XG_BONUS_TYPES else 0.0) for k in BASE)

# geometry (m)
HALF_LENGTH = 52.5
POST_Y = 3.66
BAR_Z = 2.44
NEAR_M = 1.0  # a shot "just wide" or "just over": within 1 m of a post or of the bar
SAVE_NEAR_POST_Y = POST_Y - NEAR_M  # a save from outside the box with the ball within 1 m of a post
SAVE_HIGH_Z = 1.8
BOX_DEPTH, BOX_HALF_WIDTH = 16.5, 20.16
LINE_M = 1.0  # a goal-line clearance: blocked within 1 m of the goal line, between the posts
FOUL_GOAL_M = 35.0  # a dangerous break: the ball within 35 m of the goal ...
FOUL_APPROACH_S = 2.0  # ... and closer to it than 2 s before
OFFSIDE_LOOKBACK_S = 15.0
VAR_DECISION_S = 180.0
SHOT_MERGE_S = 5.0  # shot and rebound of one team
GOAL_ABSORB_S = 10.0  # a goal absorbs the team's shots of the 10 s before
BALL_WINDOW_S = 3.0  # ball tracking after the kick for the crossing of the goal line and the save or block

VAR_DECISIONS = {"PenaltyNotAwarded": "no penalty", "Penalty": "penalty given"}


@dataclass
class Moment:
    kind: str
    period: int
    t: float
    side: str | None
    player_ids: list[str]
    event_ids: list[str]
    facts: dict = field(default_factory=dict)
    xg: float | None = None
    score: float = 0.0
    factors: dict = field(default_factory=dict)
    score_after: tuple[int, int] = (0, 0)


def _key(period: int, t: float) -> tuple[int, float]:
    return period, t


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


def _surname(names: dict[str, str], pid: str | None) -> str:
    name = names.get(pid or "", pid or "")
    return name.split(" ", 1)[1] if " " in name else name


def _minute_label(snap: Snapshot, period: int, t: float) -> tuple[int, str]:
    m, added = Snapshot.minute(period, snap.clock_s(period, t))
    return m, f"{m}+{added}'" if added else f"{m}'"


def _ball(snap: Snapshot, period: int, t0: float, side: str, secs: float = BALL_WINDOW_S):
    """Ball after `t0` in the attacking coordinates of `side`: (x, y, z) arrays of the valid frames, or None."""
    pf = snap.frames.get(period)
    if pf is None or not len(pf):
        return None
    sel = (pf.t >= t0) & (pf.t <= t0 + secs)
    s = attack_sign(side, period)
    x, y, z = s * pf.bx[sel], s * pf.by[sel], pf.bz[sel]
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    return (x[ok], y[ok], z[ok]) if ok.any() else None


def _crossing(ball) -> tuple[float, float] | None:
    """(y, z) at the first frame with the ball over the goal line (x >= 52.5)."""
    x, y, z = ball
    over = np.flatnonzero(x >= HALF_LENGTH)
    return (float(y[over[0]]), float(z[over[0]])) if len(over) else None


def _stop(ball) -> tuple[float, float, float]:
    """The ball where it stops approaching the goal (first frame after which x decreases): the save or the block."""
    x, y, z = ball
    back = np.flatnonzero(np.diff(x) < 0)
    k = int(back[0]) if len(back) else len(x) - 1
    return float(x[k]), float(y[k]), float(z[k])


def _goalkeeper(snap: Snapshot, side: str, key: tuple[int, float]) -> str | None:
    """The goalkeeper of `side` on the pitch at that instant (the starter, unless substituted before)."""
    gks = gk_ids(snap)
    on = next((p.player_id for p in snap.lineup if p.team_side == side and p.starting and p.player_id in gks), None)
    for e in snap.events:
        if e.type == "Substitution" and e.team_side == side and _key(e.period, e.t) < key and e.player_id == on:
            on = e.secondary_player_id or on
    return on


def _shot_moment(snap: Snapshot, e, xg: float | None, kick: tuple[float, float, float] | None) -> Moment | None:
    """The candidate of one shot (VAR.md section 1), None when the shot is not a moment. `kick`: (t, x, y) of the
    located kick in the attacking coordinates, None when not located."""
    out = e.attrs.get("outcome", "wide")
    penalty = bool(e.attrs.get("penalty"))
    m = Moment("", e.period, e.t, e.team_side, [e.player_id] if e.player_id else [], [e.event_id], xg=xg,
               facts={"header": bool(e.attrs.get("header")), "penalty": penalty, "outcome": out})
    if kick is not None:
        m.facts["distance_m"] = round(float(np.hypot(HALF_LENGTH - kick[1], kick[2])))
    if out == "goal":
        m.kind = "goal"
        return m
    if penalty:
        m.kind = "penalty_missed"
        return m
    if out == "woodwork":
        m.kind = "woodwork"
        return m
    ball = _ball(snap, e.period, kick[0], e.team_side) if kick is not None else None
    if out == "saved":
        outside = kick is not None and not (kick[1] >= HALF_LENGTH - BOX_DEPTH and abs(kick[2]) <= BOX_HALF_WIDTH)
        if xg is not None and xg >= BIG_SAVE_XG:
            m.kind = "big_save"
        elif outside and ball is not None:
            _, y, z = _stop(ball)
            if abs(y) >= SAVE_NEAR_POST_Y or z >= SAVE_HIGH_Z:
                m.kind = "big_save"
    elif out == "wide":
        if xg is not None and xg >= BIG_CHANCE_XG:
            m.kind = "big_chance"
        elif ball is not None and (c := _crossing(ball)) is not None:
            y, z = abs(c[0]), c[1]
            if POST_Y < y <= POST_Y + NEAR_M and z < BAR_Z + NEAR_M:
                m.kind, m.facts["miss"] = "near_miss", "wide"
            elif y <= POST_Y + NEAR_M and BAR_Z < z <= BAR_Z + NEAR_M:
                m.kind, m.facts["miss"] = "near_miss", "over"
    elif out == "blocked":
        if ball is not None:
            x, y, _ = _stop(ball)
            if x >= HALF_LENGTH - LINE_M and abs(y) < POST_Y:
                m.kind = "goal_line_clearance"
        if not m.kind and xg is not None and xg >= BIG_CHANCE_XG:
            m.kind = "big_chance"
    if m.kind in ("big_save", "goal_line_clearance"):
        keeper = _goalkeeper(snap, _other(e.team_side), _key(e.period, e.t))
        m.facts["goalkeeper"] = keeper
    return m if m.kind else None


def _dangerous_foul(snap: Snapshot, e, gks: set[str]) -> Moment | None:
    """A foul that stops a dangerous break (VAR.md section 1): at the foul's frame the fouled team has the ball, within
    35 m of the goal and closer than 2 s before, and its attackers ahead of the ball plus the carrier are at least as
    many as the outfield defenders ahead of the ball."""
    pf = snap.frames.get(e.period)
    if pf is None or not len(pf) or e.team_side not in (HOME, AWAY):
        return None
    fouled = _other(e.team_side)
    i = min(int(np.searchsorted(pf.t, e.t)), len(pf) - 1)
    j = min(int(np.searchsorted(pf.t, e.t - FOUL_APPROACH_S)), len(pf) - 1)
    if pf.bposs[i] != (1 if fouled == HOME else 2):
        return None
    s = attack_sign(fouled, e.period)
    bx, by, bx0, by0 = s * pf.bx[i], s * pf.by[i], s * pf.bx[j], s * pf.by[j]
    if not np.isfinite([bx, by, bx0, by0]).all():
        return None
    dist, dist0 = np.hypot(HALF_LENGTH - bx, by), np.hypot(HALF_LENGTH - bx0, by0)
    if dist >= FOUL_GOAL_M or dist >= dist0:
        return None
    ahead = {HOME: 0, AWAY: 0}
    for c, side in enumerate(pf.team_side):
        px = s * pf.x[i, c]
        if np.isfinite(px) and px > bx and not (side != fouled and pf.pids[c] in gks):
            ahead[side] += 1
    n_att, n_def = ahead[fouled] + 1, ahead[e.team_side]
    if n_att < n_def:
        return None
    return Moment("dangerous_foul", e.period, e.t, e.team_side, [p for p in (e.player_id, e.secondary_player_id) if p],
                  [e.event_id], facts={"n_att": n_att, "n_def": n_def, "distance_m": round(float(dist))})


def _offside_goal(snap: Snapshot, e) -> Moment | None:
    """A goal ruled out for offside: in the 15 s before an `Offside` of the attacking team the ball is in the goal
    (x > 52.5, inside the posts, under the bar) with no goal or kick-off of that team in between."""
    if e.team_side not in (HOME, AWAY):
        return None
    ball = _ball(snap, e.period, e.t - OFFSIDE_LOOKBACK_S, e.team_side, OFFSIDE_LOOKBACK_S)
    if ball is None:
        return None
    x, y, z = ball
    if not ((x > HALF_LENGTH) & (np.abs(y) < POST_Y) & (z < BAR_Z)).any():
        return None
    for o in snap.events:
        if o.period == e.period and e.t - OFFSIDE_LOOKBACK_S <= o.t < e.t and (
                o.type == "KickOff" or (o.type == "ShotAtGoal" and o.attrs.get("outcome") == "goal")):
            return None
    return Moment("offside_goal", e.period, e.t, e.team_side, [e.player_id] if e.player_id else [], [e.event_id])


def candidates(snap: Snapshot, shots: dict | None) -> list[Moment]:
    """Every candidate moment of the released events, in time order. `shots`: the engine's located shots
    (`core.fr["shots"]`: xG, kick time and attacking position), None without frames."""
    shots = shots or {}
    gks = gk_ids(snap)
    out: list[Moment] = []
    events = sorted(snap.events, key=lambda e: _key(e.period, e.t))
    for n, e in enumerate(events):
        if e.type == "ShotAtGoal" and e.team_side in (HOME, AWAY):
            v = shots.get(e.event_id)
            xg = v["xg"] if v and v["xg"] is not None else None
            kick = (v["t_kick"], v["x"], v["y"]) if v and v["located"] and v["x"] is not None else None
            m = _shot_moment(snap, e, xg, kick)
        elif e.type == "SendingOff" or (e.type == "Caution" and e.attrs.get("card") in ("red", "yellowRed")):
            m = Moment("red_card", e.period, e.t, e.team_side, [e.player_id] if e.player_id else [], [e.event_id],
                       facts={"second_yellow": e.attrs.get("card") == "yellowRed"})
        elif e.type == "VideoAssistantAction":
            m = Moment("var_review", e.period, e.t, e.team_side, [], [e.event_id], facts={"decision": "check under way"})
            for d in events[n + 1:]:
                if _key(d.period, d.t) > _key(e.period, e.t + VAR_DECISION_S):
                    break
                if d.type in VAR_DECISIONS:
                    m.facts["decision"] = VAR_DECISIONS[d.type]
                    m.event_ids.append(d.event_id)
                    m.player_ids += [d.player_id] if d.player_id else []
                    m.side = m.side or d.team_side
                    break
        elif e.type in ("Nutmeg", "SpectacularPlay"):
            m = Moment("skill", e.period, e.t, e.team_side, [p for p in (e.player_id, e.secondary_player_id) if p],
                       [e.event_id], facts={"nutmeg": e.type == "Nutmeg"})
        elif e.type == "Foul":
            m = _dangerous_foul(snap, e, gks)
        elif e.type == "Offside":
            m = _offside_goal(snap, e)
        else:
            m = None
        if m is not None:
            out.append(m)
    return out


def merge(moments: list[Moment]) -> list[Moment]:
    """A goal absorbs its team's shot moments of the 10 s before; shots of one team less than 5 s apart (shot and
    rebound) are one moment, of the type with the higher base."""
    goals = [m for m in moments if m.kind == "goal"]
    kept = [m for m in moments if not (m.kind in SHOT_TYPES and m.kind != "goal" and any(
        g.side == m.side and g.period == m.period and 0 < g.t - m.t <= GOAL_ABSORB_S for g in goals))]
    out: list[Moment] = []
    for m in kept:
        prev = out[-1] if out else None
        if (prev is not None and m.kind in SHOT_TYPES and prev.kind in SHOT_TYPES and prev.side == m.side
                and prev.period == m.period and m.t - prev.t < SHOT_MERGE_S):
            first, second = (prev, m) if BASE[prev.kind] >= BASE[m.kind] else (m, prev)
            first.event_ids = sorted(set(prev.event_ids + m.event_ids), key=(prev.event_ids + m.event_ids).index)
            out[-1] = first
            continue
        out.append(m)
    return out


def score_moments(snap: Snapshot, moments: list[Moment]) -> None:
    """Score of every moment (VAR.md section 2) on the score known at its instant."""
    goals = sorted(((m.period, m.t, m.side) for m in moments if m.kind == "goal"))
    for m in moments:
        before = [g for g in goals if (g[0], g[1]) < (m.period, m.t)]
        home = sum(g[2] == HOME for g in before)
        away = len(before) - home
        minute, _ = Snapshot.minute(m.period, snap.clock_s(m.period, m.t))
        f_min = minute_factor(minute)
        if m.kind == "goal":
            diff = (home - away) if m.side == HOME else (away - home)
            f_state = goal_factor(diff)
            m.score_after = (home + (m.side == HOME), away + (m.side == AWAY))
        else:
            f_state = state_factor(home - away)
            m.score_after = (home, away)
        bonus = XG_BONUS * m.xg if m.kind in XG_BONUS_TYPES and m.xg is not None else 0.0
        m.score = round(BASE[m.kind] * f_min * f_state + bonus)
        m.factors = {"type": float(BASE[m.kind]), "minute": f_min, "game_state": f_state, "xg": round(bonus, 1),
                     "score": float(m.score)}
        m.facts["score_before"] = (home, away)


def _score_text(score: tuple[int, int]) -> str:
    return f"{score[0]}-{score[1]}"


def label_and_why(snap: Snapshot, m: Moment, names: dict[str, str], teams: dict[str, str]) -> tuple[str, str]:
    """English label and "why it mattered" from the templates of VAR.md section 3, with the moment's own fields only."""
    _, mins = _minute_label(snap, m.period, m.t)
    player = _surname(names, m.player_ids[0]) if m.player_ids else "the team"
    team = teams.get(m.side, TEAM_ID.get(m.side, "")) if m.side else ""
    score = _score_text(m.facts.get("score_before", (0, 0)))
    dist = m.facts.get("distance_m")
    from_m = f" from {dist} m" if dist is not None else ""
    xg = f" (xG {m.xg:.2f})" if m.xg is not None else ""
    if m.kind == "goal":
        variant = "Penalty" if m.facts["penalty"] else "Header" if m.facts["header"] else ""
        label = f"{mins} – Goal! {player} ({team})" + (f" · {variant}" if variant else "")
        h, a = m.score_after
        lead = (h - a) if m.side == HOME else (a - h)
        effect = "levels it at" if lead == 0 else f"puts {team} ahead at" if lead == 1 else "makes it"
        part = "penalty" if m.facts["penalty"] else "header" if m.facts["header"] else "finish"
        return label, f"{player}'s {part} {effect} {_score_text(m.score_after)}."
    if m.kind == "penalty_missed":
        keeper = _surname(names, _goalkeeper(snap, _other(m.side), _key(m.period, m.t))) if m.side else ""
        saved = m.facts.get("outcome") == "saved" and keeper
        label = f"{mins} – Penalty saved by {keeper}" if saved else f"{mins} – Penalty missed by {player}"
        h, a = m.facts["score_before"]
        lead = (h - a) if m.side == HOME else (a - h)
        chance = "level" if lead < 0 else "lead" if lead == 0 else "extend the lead"
        return label, f"A chance to {chance} from the spot, kept out at {score}."
    if m.kind == "woodwork":
        return f"{mins} – Off the woodwork by {player}", f"Inches away at {score}: {player}'s shot{from_m} hits the woodwork."
    if m.kind == "big_save":
        keeper = _surname(names, m.facts.get("goalkeeper")) or "the goalkeeper"
        return f"{mins} – Big save by {keeper}", f"{keeper} denies {player}{from_m}{xg} to keep it {score}."
    if m.kind == "near_miss":
        word = "over" if m.facts.get("miss") == "over" else "wide"
        return f"{mins} – Just {word} from {player}", f"{player}'s effort{from_m} misses by less than a metre at {score}."
    if m.kind == "big_chance":
        return f"{mins} – Big chance for {player}", f"{player} misses{from_m}{xg} with the score {score}."
    if m.kind == "goal_line_clearance":
        return f"{mins} – Off the line!", f"{player}'s shot is kept out on the line at {score}."
    if m.kind == "red_card":
        word = "Second yellow" if m.facts.get("second_yellow") else "Red card"
        return f"{mins} – {word} for {player}", f"{team} are down to ten men at {score}."
    if m.kind == "var_review":
        d = m.facts["decision"]
        return f"{mins} – VAR check: {d}", f"VAR reviews a possible incident: {d}, at {score}."
    if m.kind == "offside_goal":
        return (f"{mins} – Goal ruled out for offside ({player})",
                f"{player} has the ball in the net but is offside; it stays {score}.")
    if m.kind == "skill":
        what = "Nutmeg" if m.facts.get("nutmeg") else "Piece of skill"
        return f"{mins} – {what} by {player}", f"{player} beats an opponent with a {what.lower()}."
    fouled = _surname(names, m.player_ids[1]) if len(m.player_ids) > 1 else "the attacker"
    n_att, n_def = m.facts["n_att"], m.facts["n_def"]
    return (f"{mins} – {player} stops a {n_att}-on-{n_def} break",
            f"The break was {n_att} against {n_def} towards goal until {player}'s foul on {fouled}.")


def importance(score: float) -> float:
    """The score as a share (0-100) of the highest score the formula can give."""
    return round(min(100.0, 100.0 * score / MAX_SCORE), 1)


def moments(snap: Snapshot, shots: dict | None) -> list[Moment]:
    """Candidates, merged and scored, in time order."""
    ms = merge(candidates(snap, shots))
    score_moments(snap, ms)
    return ms


def recap_ids(ms: list[Moment]) -> list[str]:
    """Every goal, then the other moments of the timeline (score >= LIVE_MIN) by score (ties: the more recent first),
    up to RECAP_MAX in all; in time order."""
    goals = [m for m in ms if m.kind == "goal"]
    others = sorted((m for m in ms if m.kind != "goal" and m.score >= LIVE_MIN), key=lambda m: (-m.score, -m.period, -m.t))
    chosen = goals + others[:max(0, RECAP_MAX - len(goals))]
    chosen.sort(key=lambda m: (m.period, m.t))
    return [highlight_id(m) for m in chosen]


def highlight_id(m: Moment) -> str:
    return f"HL-{m.event_ids[0]}"


def payload(snap: Snapshot, shots: dict | None, names: dict[str, str], teams: dict[str, str],
            inspector: bool) -> list[dict]:
    """The contract's `Highlight` list: the moments with a score of at least LIVE_MIN, in time order."""
    ms = [m for m in moments(snap, shots) if m.score >= LIVE_MIN]
    rank = {id(m): r for r, m in enumerate(sorted(ms, key=lambda m: (-m.score, -m.period, -m.t)), start=1)}
    out = []
    for m in ms:
        minute, _ = Snapshot.minute(m.period, snap.clock_s(m.period, m.t))
        label, why = label_and_why(snap, m, names, teams)
        out.append({"schema_version": "v0", "highlight_id": highlight_id(m), "type": CONTRACT_TYPE[m.kind],
                    "period": m.period, "minute": minute, "team_id": TEAM_ID.get(m.side) if m.side else None,
                    "player_ids": m.player_ids, "event_ids": m.event_ids, "label": label, "why_it_mattered": why,
                    "importance": importance(m.score),
                    "score_after": {"home": m.score_after[0], "away": m.score_after[1]},
                    "xg": round(m.xg, 2) if m.xg is not None else None,
                    "inspector": {"importance_factors": m.factors, "candidate_rank": rank[id(m)],
                                  "labelled_by": "VAR (code templates, VAR.md section 3)", "model": "none",
                                  "trace_id": f"var-{highlight_id(m)}"} if inspector else None})
    return out
