"""Tests of VAR (AG-3, app/engine/highlights.py) on small made-up matches: general rules, never SYN-001's numbers."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from app.contract.models import Highlight
from app.engine import highlights as var
from app.engine.sources.base import AWAY, HOME, Event, LineupEntry, PeriodFrames

KICKOFF = {1: 0.0, 2: 3000.0}  # feed time of each kick-off: minute = clock // 60 + 1 (+45 in the second half)
NAMES = {"H9": "Hugo Striker", "A9": "Ada Forward", "H1": "Hal Keeper", "A1": "Ari Keeper"}
TEAMS = {HOME: "Home Town", AWAY: "Away City"}
LINEUP = (LineupEntry("H1", HOME, "GK", True), LineupEntry("H9", HOME, "ST", True),
          LineupEntry("A1", AWAY, "GK", True), LineupEntry("A9", AWAY, "ST", True))


@dataclass
class Snap:
    """The parts of a window Snapshot that VAR reads."""

    events: tuple
    frames: dict = field(default_factory=dict)
    lineup: tuple = LINEUP

    def clock_s(self, period: int, t: float) -> float:
        return max(0.0, t - KICKOFF[period])


def at(minute: int, period: int = 1) -> float:
    """Feed time in the middle of a scoreboard minute."""
    return KICKOFF[period] + (minute - (1 if period == 1 else 46)) * 60 + 30


def shot(eid: str, side: str, minute: int, outcome: str, period: int = 1, dt: float = 0.0, **attrs) -> Event:
    pid = "H9" if side == HOME else "A9"
    return Event(eid, "ShotAtGoal", period, at(minute, period) + dt, side, pid, attrs={"outcome": outcome, **attrs})


def located(xgs: dict[str, float], events, x: float = 40.0, y: float = 0.0) -> dict:
    """The engine's located shots (`core.fr["shots"]`): kick at the event time, at (x, y) in attacking coordinates."""
    by_id = {e.event_id: e for e in events}
    return {eid: {"xg": xg, "t_kick": by_id[eid].t, "x": x, "y": y, "located": True} for eid, xg in xgs.items()}


def ball_frames(t0: float, path: list[tuple[float, float, float]], period: int = 1, fs: float = 25.0) -> PeriodFrames:
    """Ball-only tracking from `t0`, one point per frame (raw coordinates), no players."""
    n = len(path)
    xs, ys, zs = (np.array(c, dtype=float) for c in zip(*path))
    return PeriodFrames(period=period, fs=fs, t=t0 + np.arange(n) / fs, frame=np.arange(n), pids=(), team_side=(),
                        x=np.zeros((n, 0)), y=np.zeros((n, 0)), bx=xs, by=ys, bz=zs, bstatus=np.ones(n),
                        bposs=np.ones(n))


def kinds(ms) -> list[tuple[int, str]]:
    return [(m.period, m.kind) for m in ms]


def test_importance_scale_is_fixed_by_the_formula() -> None:
    assert var.MAX_SCORE == pytest.approx(100 * 1.2 * 1.3)  # a lead goal from 75': the highest score possible
    assert var.importance(var.MAX_SCORE) == 100.0
    assert var.importance(var.MAX_SCORE / 2) == 50.0
    for k in var.BASE:  # no moment type can go past the scale
        top = var.BASE[k] * 1.2 * (1.3 if k == "goal" else 1.2) + (var.XG_BONUS if k in var.XG_BONUS_TYPES else 0)
        assert top <= var.MAX_SCORE


@pytest.mark.parametrize("minute,factor", [(1, 1.0), (59, 1.0), (60, 1.1), (74, 1.1), (75, 1.2), (90, 1.2)])
def test_minute_factor(minute: int, factor: float) -> None:
    assert var.minute_factor(minute) == factor


def test_goal_scores_use_only_the_score_before_the_goal() -> None:
    ev = (shot("g1", AWAY, 20, "goal"), shot("g2", HOME, 40, "goal"), shot("g3", HOME, 88, "goal", period=2))
    ms = var.moments(Snap(ev), None)
    assert [m.score for m in ms] == [130, 120, 156]  # lead x1.3, equaliser x1.2, lead from 75' x1.3 x1.2
    assert [m.score_after for m in ms] == [(0, 1), (1, 1), (2, 1)]
    # the same 88' goal with only the events up to then: the same score (no future)
    assert var.moments(Snap(ev[:3]), None)[-1].score == 156


def test_other_moments_use_the_goal_difference() -> None:
    ev = tuple(shot(f"g{i}", HOME, 10 + i, "goal") for i in range(3)) + (shot("w", AWAY, 30, "woodwork"),)
    wood = var.moments(Snap(ev), None)[-1]
    assert wood.kind == "woodwork" and wood.factors["game_state"] == 0.8 and wood.score == 72  # 90 x 1.0 x 0.8


def test_xg_thresholds_are_parameters(monkeypatch) -> None:
    ev = (shot("s1", HOME, 10, "saved"), shot("s2", HOME, 20, "saved"), shot("w1", AWAY, 30, "wide"),
          shot("w2", AWAY, 40, "wide"), shot("b1", HOME, 50, "blocked", period=2))
    shots = located({"s1": 0.12, "s2": 0.11, "w1": 0.20, "w2": 0.19, "b1": 0.25}, ev)
    ms = var.moments(Snap(ev), shots)
    assert [(m.event_ids[0], m.kind) for m in ms] == [("s1", "big_save"), ("w1", "big_chance"), ("b1", "big_chance")]
    assert ms[0].score == round(80 * 1.2 + 20 * 0.12)  # the xG bonus
    assert ms[0].facts["goalkeeper"] == "A1"
    monkeypatch.setattr(var, "BIG_SAVE_XG", 0.10)
    monkeypatch.setattr(var, "BIG_CHANCE_XG", 0.30)
    ms = var.moments(Snap(ev), shots)
    assert [(m.event_ids[0], m.kind) for m in ms] == [("s1", "big_save"), ("s2", "big_save")]


def test_penalty_missed_and_its_rebound_goal() -> None:
    pen = shot("p", HOME, 30, "saved", penalty=True)
    ms = var.moments(Snap((pen,)), located({"p": 0.79}, (pen,)))
    assert kinds(ms) == [(1, "penalty_missed")] and ms[0].score == round(90 * 1.2 + 20 * 0.79)
    rebound = shot("r", HOME, 30, "goal", dt=6.0)  # a goal within 10 s of the saved penalty: only the goal
    ms = var.moments(Snap((pen, rebound)), None)
    assert kinds(ms) == [(1, "goal")] and ms[0].event_ids == ["r"]


def test_shot_and_rebound_are_one_moment() -> None:
    ev = (shot("a", AWAY, 50, "saved", period=2), shot("b", AWAY, 50, "woodwork", period=2, dt=3.0),
          shot("c", AWAY, 50, "wide", period=2, dt=9.0))
    shots = located({"a": 0.15, "b": 0.05, "c": 0.25}, ev)
    ms = var.moments(Snap(ev), shots)
    assert [(m.kind, m.event_ids) for m in ms] == [("woodwork", ["a", "b"]), ("big_chance", ["c"])]


def test_near_miss_from_the_ball_crossing_the_goal_line() -> None:
    e = shot("n", HOME, 20, "wide")  # home attacks +x in the first half
    path = [(40 + k, 4.2, 1.0) for k in range(15)]  # crosses x = 52.5 at y = 4.2 (within 1 m of the post), low
    ms = var.moments(Snap((e,), {1: ball_frames(e.t, path)}), located({"n": 0.05}, (e,)))
    assert kinds(ms) == [(1, "near_miss")] and ms[0].facts["miss"] == "wide"
    path = [(40 + k, 6.0, 1.0) for k in range(15)]  # 2.3 m wide of the post: not a moment
    assert var.moments(Snap((e,), {1: ball_frames(e.t, path)}), located({"n": 0.05}, (e,))) == []


def test_goal_line_clearance_in_the_second_half() -> None:
    e = shot("c", HOME, 60, "blocked", period=2)  # home attacks -x in the second half
    path = [(-(45 + 0.5 * k), 0.5, 0.3) for k in range(16)] + [(-50.0, 1.0, 0.3)]  # stops at x = -52.5 ... then back
    ms = var.moments(Snap((e,), {2: ball_frames(e.t, path, period=2)}), located({"c": 0.05}, (e,)))
    assert kinds(ms) == [(2, "goal_line_clearance")]


def test_var_review_waits_for_its_decision() -> None:
    check = Event("v", "VideoAssistantAction", 2, at(67, 2))
    ms = var.moments(Snap((check,)), None)
    assert ms[0].facts["decision"] == "check under way"  # the decision is not released yet
    decision = Event("d", "PenaltyNotAwarded", 2, at(67, 2) + 40, HOME, "H9")
    ms = var.moments(Snap((check, decision)), None)
    assert ms[0].facts["decision"] == "no penalty" and ms[0].event_ids == ["v", "d"]
    assert ms[0].score == round(70 * 1.1 * 1.2)


def test_recap_keeps_every_goal_then_the_best_moments() -> None:
    goals = tuple(shot(f"g{i}", HOME if i % 2 else AWAY, 5 + i, "goal") for i in range(4))
    woods = tuple(shot(f"w{i}", HOME, 15 + 2 * i, "woodwork") for i in range(9))
    skill = (Event("k", "Nutmeg", 1, at(44), HOME, "H9"),)  # 60, under every woodwork (108 or 90)
    ms = var.moments(Snap(goals + woods + skill), None)
    ids = var.recap_ids(ms)
    assert len(ids) == var.RECAP_MAX
    assert all(f"HL-g{i}" in ids for i in range(4))
    assert "HL-k" not in ids and ids == sorted(ids, key=[var.highlight_id(m) for m in ms].index)


def test_payload_follows_the_contract() -> None:
    ev = (shot("g", AWAY, 23, "goal"), shot("s", HOME, 81, "saved", period=2),
          Event("k", "Nutmeg", 1, at(30), HOME, "H9"))
    shots = located({"g": 0.3, "s": 0.13}, ev)
    out = var.payload(Snap(ev), shots, NAMES, TEAMS, inspector=True)
    hs = [Highlight.model_validate(h) for h in out]
    assert [h.minute for h in hs] == [23, 30, 81]
    assert hs[0].label == "23' – Goal! Forward (Away City)" and hs[0].importance <= 100
    assert hs[2].label == "81' – Big save by Keeper" and "(xG 0.13)" in hs[2].why_it_mattered
    assert hs[0].inspector.importance_factors["score"] == 130
    assert all(h["inspector"] is None for h in var.payload(Snap(ev), shots, NAMES, TEAMS, inspector=False))


def test_moments_under_the_live_threshold_stay_out() -> None:
    ev = (shot("g1", HOME, 5, "goal"), shot("g2", HOME, 6, "goal"), shot("g3", HOME, 7, "goal"),
          Event("k", "Nutmeg", 1, at(10), HOME, "H9"))  # 50 x 1.0 x 0.8 = 40 at 3-0
    out = var.payload(Snap(ev), None, NAMES, TEAMS, inspector=False)
    assert [h["highlight_id"] for h in out] == ["HL-g1", "HL-g2", "HL-g3"]
