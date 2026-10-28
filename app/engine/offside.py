"""Offside check for Archie (Materials/METRICS.md section 26, decision 27): a deterministic tool, never a statistic.

The statistics count only the official `Offside` events. When a fan asks "was it offside?", The Analyst can check two
cases only, on the released frames and events of a `Snapshot` (no look-ahead):

1. an official `Offside` event: the attacker is its player; the pass is the last time a team-mate played the ball (a
   `Play`, a set piece, an `OtherBallAction` such as a flick-on, or a shot) within 15 s before the call, never the
   attacker's own touch (Law 11: the offside position counts when a team-mate plays or touches the ball; on the six DFL
   matches two calls came after a team-mate's `OtherBallAction` that followed the opponent's possession);
2. a goal (a `ShotAtGoal` with outcome goal): the pass is the scoring team's last ball played by a team-mate of the
   scorer before the shot's located kick, within 15 s; the attacker is the pass's recipient (the scorer when the
   recipient is unknown).

At the pass's located frame (the pipeline's kick candidates, as for the pass length; else the contact frame of
`frame_stats.kick_index`), in the attacking team's coordinates (goal at +x):
`margin = attacker x - max(second-last opponent x (goalkeeper included), ball x)`; "offside" when margin > 2 m, "onside"
when margin < -2 m, otherwise "too close to call with our data". An attacker in his own half is onside, and a throw-in,
a corner or a goal kick gives no offside offence (Law 11). Not checkable (verdict None, with the reason): the event is
not released or is neither an offside nor a goal, a set-piece shot (no pass), no pass in the 15 s before, the pass not
located on the tracking, the attacker's or any opponent's position not valid at that frame (an invalid position is
never used, and a missing defender could be the one who sets the line), fewer than two opponents on the pitch.
"""

from __future__ import annotations

import numpy as np

from . import frame_stats
from .sources.base import AWAY, HOME
from .window import Snapshot

CFG = frame_stats.CFG
BAND_M = CFG.offside_band_m
LOOKBACK_S = CFG.offside_pass_lookback_s
NO_OFFENCE = ("ThrowIn", "CornerKick", "GoalKick")  # Law 11: no offside offence directly from these restarts
PASSES = ("Play", "OtherBallAction", "ShotAtGoal", "FreeKick", "KickOff") + NO_OFFENCE  # a team-mate plays the ball
TOO_CLOSE = "too close to call with our data"


def verdict(attacker_x: float, opponents_x, ball_x: float, band_m: float = BAND_M) -> tuple[str | None, float | None, str]:
    """Law 11 at one frame, in the attacking team's coordinates (goal at +x). Returns (verdict, margin m, reason)."""
    xs = np.sort(np.asarray(opponents_x, float))[::-1]
    if len(xs) < 2:
        return None, None, "fewer than two opponents on the pitch"
    margin = float(attacker_x) - max(float(xs[1]), float(ball_x))
    if attacker_x <= 0.0:
        return "onside", margin, "in his own half (Law 11)"
    if margin > band_m:
        return "offside", margin, f"{margin:.1f} m beyond the second-last opponent or the ball"
    if margin < -band_m:
        return "onside", margin, f"{-margin:.1f} m behind the second-last opponent or the ball"
    return TOO_CLOSE, margin, f"within {band_m:g} m of the line: one tracked point per player, 25 times a second"


def offside_check(snap: Snapshot, event_id: str, cache: frame_stats.PrepCache | None = None) -> dict:
    """Check an official offside or a goal (module docstring). Returns `event_id`, `case` (offside or goal), `verdict`
    ("offside", "onside", "too close to call with our data" or None), `margin_m` (0.1 m), `pass_event_id`, `frame`
    (provider frame number of the pass), `minute`, `attacker_id` and `reason`."""
    out = {"event_id": event_id, "case": None, "verdict": None, "margin_m": None, "pass_event_id": None, "frame": None,
           "minute": None, "attacker_id": None, "reason": None}

    def stop(reason: str, **kw) -> dict:
        return {**out, **kw, "reason": reason}

    e = next((x for x in snap.events if x.event_id == event_id), None)
    if e is None:
        return stop("not among the released events (unknown, or its frames have not arrived yet)")
    by_period = {p.pf.period: p for p in frame_stats.prepare(snap, cache)}
    if e.type == "Offside":
        out["case"] = "offside"
    elif e.type == "ShotAtGoal" and e.attrs.get("outcome") == "goal":
        out["case"] = "goal"
        if e.attrs.get("penalty") or e.event_id.endswith("-shot"):
            return stop("a set-piece shot: no pass to check")
    else:
        return stop("only official offsides and goals are checked")
    if e.period not in by_period or e.team_side not in (HOME, AWAY):
        return stop("no tracking frames for this period")
    p = by_period[e.period]
    pf, hr = p.pf, p.hr
    t_ref = e.t
    if out["case"] == "goal":  # before the shot's located kick, as in the statistics (frame_stats._shots)
        sp, tt = frame_stats.touches(snap, p, cache)
        i = frame_stats.metrics.shot_kick(sp, tt, pf.pids.index(e.player_id), e.t, frame_stats.PASS_SEARCH_S, CFG) \
            if e.player_id in pf.pids else None
        t_ref = float(pf.t[i if i is not None else frame_stats.kick_index(pf, e)[0]])
    passes = [x for x in snap.events if x.period == e.period and x.team_side == e.team_side and x.type in PASSES
              and t_ref - LOOKBACK_S <= x.t <= t_ref and x is not e and x.player_id != e.player_id
              and f"{x.event_id}-shot" != e.event_id]
    if not passes:
        return stop(f"no pass of the attacking team in the {LOOKBACK_S:g} s before")
    ps = passes[-1]
    attacker = e.player_id if out["case"] == "offside" else (
        ps.secondary_player_id if ps.secondary_player_id in pf.pids and ps.secondary_player_id != ps.player_id
        else e.player_id)
    out.update(pass_event_id=ps.event_id, attacker_id=attacker,
               minute=snap.minute(ps.period, snap.clock_s(ps.period, ps.t))[0])
    if ps.type in NO_OFFENCE:
        return stop("no offside offence directly from a throw-in, a corner or a goal kick (Law 11)", verdict="onside")
    i = None
    if ps.player_id in pf.pids:
        sp, tt = frame_stats.touches(snap, p, cache)
        r = frame_stats.metrics.pass_reception(sp, tt, pf.pids.index(ps.player_id), ps.t, frame_stats.PASS_SEARCH_S, CFG)
        i = r["kick"] if r else None
        if i is None:
            j, located = frame_stats.kick_index(pf, ps)
            i = j if located else None
    if i is None:
        return stop("the pass is not located on the tracking")
    out.update(frame=int(pf.frame[i]), minute=snap.minute(ps.period, snap.clock_s(ps.period, float(pf.t[i])))[0])
    s = frame_stats.attack_sign(e.team_side, e.period)
    if attacker not in pf.pids:
        return stop("the attacker is not tracked")
    a = pf.pids.index(attacker)
    if not (hr.valid[i, a] and np.isfinite(hr.x[i, a])):
        return stop("the attacker's position is not valid at the pass frame")
    opp = [c for c, side in enumerate(pf.team_side) if side != e.team_side and hr.present[i, c]]
    if any(not (hr.valid[i, c] and np.isfinite(hr.x[i, c])) for c in opp):
        return stop("an opponent's position is not valid at the pass frame")
    v, margin, why = verdict(s * float(hr.x[i, a]), [s * float(hr.x[i, c]) for c in opp], s * float(pf.bx[i]))
    return {**out, "verdict": v, "margin_m": round(margin, 1) if margin is not None else None, "reason": why}
