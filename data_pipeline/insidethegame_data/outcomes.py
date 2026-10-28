"""What happens after a kick: shots, passes and clearances (guidelines v4.2, sections 9.7-9.9).

Shots
* Free flight: from the kick k to the first of the next touch by any player (from 0.08 s,
  ``R-SHOT-SPLIT``), a goal crossing (``R-GOAL``), a woodwork contact (``R-WOODWORK``) or the
  last live frame before the ball goes dead; at most ``next_touch_max_s``. A touch mid-flight ends
  the flight, so the direction is always measured on the stretch before it (v4.1).
* ``R-SHOT-DIRECTION``: x(t), y(t) fitted linearly (attacking-normalised) on the live ball
  samples of the whole free flight; at least ``shot_flight_min_samples``; the line must cross the
  goal line within ``shot_wide_margin_m`` (6 m) beyond a post, or within
  ``shot_wide_margin_long_m`` (8 m, v4.1) for kicks more than ``shot_long_range_m`` from the goal
  line and for shots above the power threshold.
* ``R-SHOT-NOT-PASS`` (v4.1): not a shot when the ball is directed at a team-mate (ahead of the
  ball, before the goal line, within ``shot_teammate_line_m`` of the fitted line) **and that
  team-mate really receives it** (the next touch is his). Overridden by power evidence.
* ``R-SHOT-POWER`` (v4.1): peak valid live ball speed in the 0.5 s after the kick, stopped before
  the next touch. Above the 99th percentile of DFL pass speeds, and not a header, it **confirms**
  the shot (basis ``shot_power``).
* ``R-SHOT-OUTCOME`` and ``R-SHOT-CHECK``: the observed outcome and the decision table. A flight
  that heads away from the goal with an opponent within ``shot_block_at_kick_m`` of the ball at the
  kick is a shot blocked at the foot (v4.2): ``blocked`` (``saved`` when that opponent is the
  goalkeeper).

Passes: ``R-PASS-KICK`` (a located pass is confirmed by its kick) and ``R-PASS-OUTCOME``.
Clearances: ``R-CLEARANCE`` (at least 4 of 7 conditions, live play only; C4 v4.1: an opponent
within ``clear_opponent_radius_m`` of the ball at the kick).

Every check uses live play only (``R-LIVE-ONLY``). In live mode (``sp.complete`` False) a check
whose frames have not arrived yet is ``pending`` (``R-LIVE-BUFFER``).
"""

from __future__ import annotations

import numpy as np

from .config import PrepConfig
from .touches import TouchTable, next_touch

CLEARANCE_CONDITIONS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")


def _incomplete(sp) -> bool:
    return not getattr(sp, "complete", True)


# ---------------------------------------------------------------------------
# Free flight
# ---------------------------------------------------------------------------

def free_flight(sp, tt: TouchTable, k: int, kicker: int | None, cfg: PrepConfig, crossing_frames=(), woodwork=(), shot: bool = True) -> dict:
    """The free flight of a kick at ``k``: ``{"end", "end_kind", "touch", "woodwork", "crossing"}``.

    ``end_kind``: ``touch`` (next touch by any player), ``goal_crossing``, ``woodwork``, ``out_of_play`` (the end is
    the last live frame before the ball goes dead), ``max_time`` or ``pending`` (live mode, frames not arrived)."""
    sep = cfg.frames(cfg.shot_touch_min_separation_s if shot else cfg.kick_min_separation_s, sp.fs)
    nt = next_touch(sp, tt, k, kicker, cfg, sep, shot=shot)
    ends = []
    if nt["kind"] == "touch":
        ends.append((nt["frame"], 1, "touch"))
    elif nt["kind"] == "out_of_play":
        ends.append((nt["frame"], 3, "out_of_play"))  # the first dead frame; the flight ends on the frame before
    elif nt["kind"] == "pending":
        ends.append((nt["frame"], 4, "pending"))
    else:
        ends.append((nt["frame"], 4, "max_time"))
    horizon = ends[0][0]
    cr = [f for f in crossing_frames if k < f <= horizon]
    if cr:
        ends.append((min(cr), 0, "goal_crossing"))  # a crossing wins a tie with the ball going dead on the same frame
    ww = [w for w in woodwork if k < w <= horizon]
    if ww:
        ends.append((min(ww), 2, "woodwork"))
    f, _, kind = min(ends)
    end = f - 1 if kind == "out_of_play" else f
    return {"end": int(max(end, k)), "end_kind": kind, "touch": nt, "woodwork": min(ww) if ww else None, "crossing": min(cr) if cr else None}


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------

def wide_margin(sp, k: int, attack_sign: float, L: float, power_kmh: float | None, p99_kmh: float | None, cfg: PrepConfig) -> float:
    """``R-SHOT-DIRECTION`` (v4.1): 8 m beyond a post for a kick more than ``shot_long_range_m`` from the goal line (ball
    at the kick, attacking-normalised) or above the power threshold; 6 m otherwise."""
    long_range = np.isfinite(sp.bx[k]) and (L / 2 - attack_sign * float(sp.bx[k])) > cfg.shot_long_range_m
    powerful = power_kmh is not None and p99_kmh is not None and power_kmh > p99_kmh
    return cfg.shot_wide_margin_long_m if (long_range or powerful) else cfg.shot_wide_margin_m


def shot_direction(sp, k: int, e: int, attack_sign: float, L: float, cfg: PrepConfig, crossing_y: float | None = None,
                   margin_m: float | None = None) -> dict:
    """``R-SHOT-DIRECTION``: linear fit of the whole free flight (attacking-normalised coordinates). ``margin_m``: the
    margin beyond a post (``wide_margin``; default ``shot_wide_margin_m``); a miss gives ``direction_outside_<margin>m``."""
    margin = cfg.shot_wide_margin_m if margin_m is None else float(margin_m)
    idx = np.arange(k, e + 1)
    ok = (sp.bstatus[idx] == 1) & np.isfinite(sp.bx[idx]) & np.isfinite(sp.by[idx])
    if getattr(sp, "ball_pos_ok", None) is not None:
        ok &= sp.ball_pos_ok[idx]
    idx = idx[ok]
    out = {"direction": None, "y_line_m": None, "n_samples": int(len(idx)), "fit": None, "margin_m": margin}
    if len(idx) < cfg.shot_flight_min_samples:
        out["direction"] = "direction_undetermined"
        return out
    t = sp.t_s[idx] - sp.t_s[k]
    xa, ya = attack_sign * sp.bx[idx], attack_sign * sp.by[idx]
    vx, x0 = np.polyfit(t, xa, 1)
    vy, y0 = np.polyfit(t, ya, 1)
    out["fit"] = (float(x0), float(y0), float(vx), float(vy))
    if crossing_y is not None:
        y_line = attack_sign * crossing_y
    elif vx > 0:
        y_line = y0 + vy * (L / 2 - x0) / vx
    else:
        out["direction"] = "away_from_goal"
        return out
    out["y_line_m"] = round(float(y_line), 3)
    out["direction"] = "ok" if abs(y_line) <= cfg.goal_width_m / 2 + margin else f"direction_outside_{margin:g}m"
    return out


def directed_at_teammate(sp, k: int, shooter: int, fit, attack_sign: float, L: float, cfg: PrepConfig, receiver: int | None = None) -> dict:
    """``R-SHOT-NOT-PASS`` (v4.1): the ball is directed at a team-mate of the shooter who **really receives it**: the
    receiver (the player of the next touch) is a team-mate who, at the kick frame, is ahead of the ball (positive
    projection on the fitted direction, before the goal line) and within ``shot_teammate_line_m`` of the fitted line.
    ``geometric`` keeps the v4 test (any team-mate on the line), for diagnostics."""
    none = {"holds": False, "geometric": False, "teammate": None, "distance_m": None}
    if fit is None:
        return none
    x0, y0, vx, vy = fit
    sp_ = float(np.hypot(vx, vy))
    if sp_ <= 0:
        return none
    ux, uy = vx / sp_, vy / sp_
    side = sp.team_side[shooter]
    on_line = {}
    for c, s in enumerate(sp.team_side):
        if c == shooter or s != side or not (np.isfinite(sp.px[k, c]) and np.isfinite(sp.py[k, c])):
            continue
        qx, qy = attack_sign * sp.px[k, c] - x0, attack_sign * sp.py[k, c] - y0
        along = qx * ux + qy * uy
        perp = abs(qx * uy - qy * ux)
        if along <= 0 or perp > cfg.shot_teammate_line_m:
            continue
        if ux > 0 and x0 + along * ux > L / 2:  # beyond the goal line
            continue
        on_line[c] = perp
    if receiver is not None and receiver in on_line:
        return {"holds": True, "geometric": True, "teammate": receiver, "distance_m": round(on_line[receiver], 3)}
    return none | {"geometric": bool(on_line)}


def opponent_at_kick(sp, k: int, shooter: int) -> tuple[int | None, float]:
    """(column, distance) of the opponent nearest to the ball at the kick frame ``k`` (horizontal), or (None, inf)."""
    opp = [c for c, s in enumerate(sp.team_side) if s != sp.team_side[shooter]]
    if not opp:
        return None, np.inf
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[k, opp] - sp.bx[k], sp.py[k, opp] - sp.by[k])
    d = np.where(np.isfinite(d), d, np.inf)
    j = int(np.argmin(d))
    return (opp[j], float(d[j])) if np.isfinite(d[j]) else (None, np.inf)


def kick_power_kmh(sp, k: int, stop_before: int | None, window_s: float, cfg: PrepConfig) -> float | None:
    """Peak valid live ball speed (km/h) in the ``window_s`` after the kick, stopped before the next touch."""
    if getattr(sp, "ball_speed", None) is None:
        return None
    hi = min(len(sp.n) - 1, k + cfg.frames(window_s, sp.fs))
    if stop_before is not None:
        hi = min(hi, stop_before - 1)
    seg = np.arange(k, max(hi, k) + 1)
    ok = sp.ball_valid[seg] & (sp.bstatus[seg] == 1) & np.isfinite(sp.ball_speed[seg])
    return round(float(np.max(sp.ball_speed[seg][ok])) * 3.6, 2) if ok.any() else None


def shot_outcome(sp, k: int, flight: dict, shooter: int, gk_ids: set, cfg: PrepConfig, goal_result: str | None = None) -> str:
    """``R-SHOT-OUTCOME``, in order: goal / disallowed_goal (the goal shot of ``R-GOAL``); otherwise the first of
    woodwork, saved (opposing goalkeeper touch), blocked (opposing outfield touch within 2 s), wide (the flight ends
    with the ball out of play); otherwise none. ``pending`` in live mode when the flight has not ended yet."""
    if goal_result in ("goal", "disallowed_goal"):
        return goal_result
    kind = flight["end_kind"]
    if kind == "pending" or goal_result == "pending":
        return "pending"
    if kind == "woodwork":
        return "woodwork"
    if kind == "touch":
        c = flight["touch"]["col"]
        if sp.team_side[c] != sp.team_side[shooter]:
            if sp.pids[c] in gk_ids:
                return "saved"
            if flight["touch"]["frame"] - k <= cfg.frames(cfg.shot_block_max_s, sp.fs):
                return "blocked"
        return "none"
    if kind == "out_of_play":
        return "wide"
    return "none"


def shot_check(located: bool, outcome: str | None, not_pass: bool, power_evidence: bool, direction: str | None,
               blocked_at_kick: bool = False) -> tuple[str, str | None, str | None]:
    """``R-SHOT-CHECK`` (v4.2): (type_check, basis, reason) from the decision table of section 9.7.

    0 not located; 1 goal / disallowed goal; 2 blocked at the kick (away from the goal with an opponent within
    ``shot_block_at_kick_m``: the outcome is already ``blocked`` or ``saved``); 3 directed at a team-mate who receives it,
    without power evidence; 4 power evidence (above the pass p99, not a header): ``shot_power``; 5 direction fails;
    6 an observed outcome (woodwork, saved, blocked, wide); 7 otherwise ``no_coherent_outcome``."""
    if not located:
        return "not_confirmed", None, "event not located"
    if outcome in ("goal", "disallowed_goal"):
        return "confirmed", outcome, None
    if outcome == "pending":
        return "pending", None, None
    if blocked_at_kick and outcome in ("blocked", "saved"):
        return "confirmed", outcome, None
    if not_pass and not power_evidence:
        return "not_confirmed", None, "directed_at_teammate"
    if power_evidence:
        return "confirmed", "shot_power", None
    if direction != "ok":
        return "not_confirmed", None, direction or "direction_undetermined"
    if outcome in ("woodwork", "saved", "blocked", "wide"):
        return "confirmed", outcome, None
    return "not_confirmed", None, "no_coherent_outcome"


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

def pass_outcome(sp, k: int, passer: int, nt: dict, cfg: PrepConfig) -> str:
    """``R-PASS-OUTCOME`` from the next touch after the kick (the pass itself is confirmed by its kick)."""
    if nt["kind"] == "pending":
        return "pending"
    if nt["kind"] == "touch":
        c = nt["col"]
        if c == passer:
            return "unresolved"
        return "teammate" if sp.team_side[c] == sp.team_side[passer] else "opponent"
    if nt["kind"] == "out_of_play":
        return "out_of_play"
    travel = float(np.hypot(sp.bx[nt["frame"]] - sp.bx[k], sp.by[nt["frame"]] - sp.by[k]))
    if nt["kind"] == "none" and travel >= cfg.pass_open_space_min_m:
        return "open_space"
    return "unresolved"


# ---------------------------------------------------------------------------
# Clearances
# ---------------------------------------------------------------------------

def _att(sp, side: str, attack_sign_home: float) -> float:
    return attack_sign_home * (1.0 if side == "home" else -1.0)


def clearance_conditions(sp, tt: TouchTable, k: int, player: int, flight: dict, aerial_kick: bool, role_group: str | None, L: float,
                         cfg: PrepConfig) -> dict:
    """``R-CLEARANCE``: the seven conditions (True / False, or None while pending in live mode)."""
    fs = sp.fs
    side = sp.team_side[player]
    s = _att(sp, side, sp.home_attack_sign)
    others = [c for c in range(len(sp.pids)) if c != player]
    mates = [c for c in others if sp.team_side[c] == side]
    opps = [c for c in others if sp.team_side[c] != side]
    out: dict[str, bool | None] = {}
    # C1: no other touch of the clearing player in the 1 s before the kick (touches < 0.2 s apart are one touch).
    sep = cfg.frames(cfg.kick_min_separation_s, fs)
    lo = k - cfg.frames(cfg.clear_short_possession_s, fs)
    prev = (tt.frames >= lo) & (tt.frames <= k - sep) & (tt.col == player)
    out["C1"] = not bool(prev.any())
    # C2: powerful kick (peak valid ball speed in 0.5 s, stopped before the next touch) or a high ball in the free flight.
    nt = flight["touch"]
    power = kick_power_kmh(sp, k, nt["frame"] if nt["kind"] == "touch" else None, cfg.shot_power_window_s, cfg)
    e = flight["end"]
    z = sp.bz[k:e + 1][(sp.bstatus[k:e + 1] == 1)]
    high = bool(np.isfinite(z).any() and np.nanmax(z) >= cfg.clear_high_ball_m)
    out["C2"] = bool((power is not None and power >= cfg.clear_power_min_kmh) or high)
    # C3: towards a zone without team-mates, away from the own goal (end of the free flight).
    if flight["end_kind"] == "pending":
        out["C3"] = None
    else:
        further = s * sp.bx[e] > s * sp.bx[k]
        with np.errstate(invalid="ignore"):
            dm = np.hypot(sp.px[e, mates] - sp.bx[e], sp.py[e, mates] - sp.by[e]) if mates else np.array([])
        out["C3"] = bool(further and not (np.nan_to_num(dm, nan=np.inf) <= cfg.clear_free_radius_m).any())
    # C4 (v4.1): at least clear_min_opponents opponents within clear_opponent_radius_m of the ball at the kick.
    with np.errstate(invalid="ignore"):
        d_opp = np.hypot(sp.px[k, opps] - sp.bx[k], sp.py[k, opps] - sp.by[k]) if opps else np.array([])
    out["C4"] = int((np.nan_to_num(d_opp, nan=np.inf) <= cfg.clear_opponent_radius_m).sum()) >= cfg.clear_min_opponents
    # C5: header by a centre back or full back in his own crowded penalty area.
    xa, ya = s * sp.bx[k], s * sp.by[k]
    in_box = lambda xx, yy: (xx <= -L / 2 + cfg.penalty_area_depth_m) & (np.abs(yy) <= cfg.penalty_area_width_m / 2)  # noqa: E731
    n_opp_box = int(np.nansum(in_box(s * sp.px[k, opps], s * sp.py[k, opps]))) if opps else 0
    out["C5"] = bool(aerial_kick and role_group in cfg.clear_defender_groups and in_box(xa, ya) and n_opp_box >= cfg.clear_box_min_opponents)
    # C6: start from the own half.
    out["C6"] = bool(xa < 0)
    # C7: possession change or ball out soon after (within clear_after_s of the kick).
    lim = cfg.frames(cfg.clear_after_s, fs)
    nt7 = next_touch(sp, tt, k, player, cfg)
    if nt7["kind"] == "touch":
        out["C7"] = bool(sp.team_side[nt7["col"]] != side and nt7["frame"] - k <= lim)
    elif nt7["kind"] == "out_of_play":
        out["C7"] = bool(nt7["frame"] - k <= lim)
    elif nt7["kind"] == "pending" and k + lim > len(sp.n) - 1:
        out["C7"] = None
    else:
        out["C7"] = False
    return out


def clearance_check(conditions: dict, cfg: PrepConfig) -> tuple[str, int]:
    """(type_check, score): confirmed with at least ``clearance_min_conditions`` of the seven; pending in live mode
    when a missing condition could still change the answer."""
    score = sum(1 for v in conditions.values() if v is True)
    if score >= cfg.clearance_min_conditions:
        return "confirmed", score
    if any(v is None for v in conditions.values()) and score + sum(1 for v in conditions.values() if v is None) >= cfg.clearance_min_conditions:
        return "pending", score
    return "not_confirmed", score
