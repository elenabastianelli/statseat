"""Goals, confirmed looking forward (guidelines v4.2, ``R-GOAL``, section 9.6).

Goals are located **first** (section 9.3, step 3), from the ball alone:

1. **Crossings.** Every frame f where the ball passes a goal line, s * x(f-1) < L/2 <= s * x(f),
   with the ball live at f-1. The sample at f is used **whatever its status** (the ball inside
   the goal is the one exception to ``R-LIVE-ONLY``). y and z are interpolated at the line;
   the crossing is inside the goal when |y| <= 3.66 + 0.11 m and z <= 2.44 + 0.11 m (only y
   without ball height). Only crossings inside the goal are kept.
   * **Entry rule** (v4.2): a goal needs the ball to enter by crossing the goal line between the
     posts. After the ball has gone **wholly over** a goal line (centre beyond it by more than the
     ball radius, Law 9) outside the goal frame, it stays out until it has been back on the pitch
     for at least ``goal_reentry_min_s``; crossings while it is out are ignored.
   * ``R-GOAL-FREEZE`` (v4.2): when the sample after the last live sample is dead and frozen (the
     provider holds the horizontal position), the last live velocity is extrapolated for at most
     ``goal_freeze_extrapolate_s``; a crossing inside the goal found this way has
     ``crossing_method = "extrapolated"``. Only when no player is within ``goal_freeze_player_min_m``
     of the ball at the last live sample.
2. **The ball goes dead** within ``goal_dead_within_s``; otherwise ``play_continued``.
3. **Look forward to the next restart** of the period: ``KickOff`` -> ``goal``; any other
   restart -> ``disallowed_goal``; none before the end of the period -> ``unconfirmed``, or a goal
   confirmed by the end of the period (v4.1, decided in ``events_sync``). In live mode a crossing
   whose next restart has not arrived yet is ``pending`` (``R-LIVE-BUFFER``).
4. **Backwards to the shot**: the last kick (ground touch, aerial touch >= 1.6 m or soft kick)
   by a player of the attacking team **up to and including the crossing frame** (v4.2), back to the
   last dead->live restart; opponent touches in between are allowed. If the action starts with a
   restart taken by the attacking team and no later attacking kick exists, the kick is the
   restart's dead->live frame (``restart_transition``). An own goal without any attacking kick has
   no located shot. **Attribution with a deflection** (v4.1, ``attribute_goal``): a defender's
   touch after the kick takes the goal (own goal) only when the shot was not going on target.
"""

from __future__ import annotations

import numpy as np

from .config import PrepConfig
from .touches import TouchTable, kick_frames, nearest_player, soft_kicks


def _crossing_row(f: int, s: float, y: float, z: float, status_live: bool, method: str, extrapolated_s: float | None = None) -> dict:
    return {"index": int(f), "goal_x_sign": s, "y_line_m": round(y, 3), "z_line_m": None if not np.isfinite(z) else round(z, 3),
            "status_at_f": "live" if status_live else "dead", "crossing_method": method, "extrapolated_s": extrapolated_s}


def _inside_at_line(sp, i0: int, i1: int, L: float, s: float, cfg: PrepConfig, has_z: bool) -> bool:
    """The ball's path from sample i0 (in front of the goal line of side ``s``) to i1 (beyond it) crosses the line between
    the posts and under the bar (y and z interpolated at the line; ball radius included; only y without height)."""
    x0, x1 = s * sp.bx[i0], s * sp.bx[i1]
    w = (L / 2 - x0) / (x1 - x0) if x1 > x0 else 1.0
    y = sp.by[i0] + w * (sp.by[i1] - sp.by[i0])
    z0, z1 = sp.bz[i0], sp.bz[i1]
    z = z0 + w * (z1 - z0) if np.isfinite(z0) and np.isfinite(z1) else z0
    return bool(abs(y) <= cfg.goal_width_m / 2 + cfg.ball_radius_m and (not has_z or not np.isfinite(z) or z <= cfg.crossbar_height_m + cfg.ball_radius_m))


def ball_out_mask(sp, s: float, L: float, cfg: PrepConfig) -> np.ndarray:
    """Entry rule (v4.2): True at frame i when the ball is "out" over the goal line of side ``s`` (+1 for the goal at +x,
    -1 for the goal at -x): it has gone **wholly over** that line (centre beyond it by more than the ball radius, Law 9)
    after crossing it **outside the goal frame** (wide of a post or over the bar), and it has not yet been back on the
    pitch (in front of the line) for ``goal_reentry_min_s``. Where the ball crossed the line decides, not where it is
    afterwards: a ball that went out wide and then appears behind the line between the posts, from the side or from
    behind, is still out. A ball that only grazes the line within the ball radius never goes out."""
    nf = len(sp.n)
    has_z = bool(np.isfinite(sp.bz).any())
    half_goal = cfg.goal_width_m / 2 + cfg.ball_radius_m
    bar = cfg.crossbar_height_m + cfg.ball_radius_m
    need = max(1, cfg.frames(cfg.goal_reentry_min_s, sp.fs))
    xs = s * sp.bx
    out = np.zeros(nf, dtype=bool)
    is_out, run = False, 0
    last_cross_inside = None  # where the ball last crossed the line (None: not seen; then the sample's own position decides)
    prev = None  # last sample with a finite x
    for i in range(nf):
        x = xs[i]
        if not np.isfinite(x):
            out[i] = is_out
            continue
        if x < L / 2:
            run += 1
            if is_out and run >= need:
                is_out = False
        else:
            run = 0
            if prev is not None and xs[prev] < L / 2:
                last_cross_inside = _inside_at_line(sp, prev, i, L, s, cfg, has_z)
            if x > L / 2 + cfg.ball_radius_m:
                inside = last_cross_inside if last_cross_inside is not None else bool(
                    abs(sp.by[i]) <= half_goal and (not has_z or not np.isfinite(sp.bz[i]) or sp.bz[i] <= bar))
                if not inside:
                    is_out = True
        out[i] = is_out
        prev = i
    return out


def goal_crossings(sp, L: float, cfg: PrepConfig, bk: dict | None = None) -> list[dict]:
    """Step 1: crossings of either goal line inside the goal, in frame order: observed crossings that respect the entry
    rule, plus the crossings extrapolated over a provider freeze (``R-GOAL-FREEZE``; ``bk``: ball kinematics with the
    last live velocity, computed when None)."""
    out = []
    has_z = bool(np.isfinite(sp.bz).any())
    half_goal = cfg.goal_width_m / 2 + cfg.ball_radius_m
    bar = cfg.crossbar_height_m + cfg.ball_radius_m
    live = sp.bstatus == 1
    for s in (1.0, -1.0):
        xs = s * sp.bx
        was_out = ball_out_mask(sp, s, L, cfg)
        with np.errstate(invalid="ignore"):
            hit = np.flatnonzero((xs[:-1] < L / 2) & (xs[1:] >= L / 2) & live[:-1]) + 1
        for f in hit:
            if was_out[f - 1]:
                continue  # entry rule: the ball is still out after an exit outside the frame
            x0, x1 = xs[f - 1], xs[f]
            w = (L / 2 - x0) / (x1 - x0) if x1 > x0 else 1.0
            y = float(sp.by[f - 1] + w * (sp.by[f] - sp.by[f - 1]))
            z0, z1 = sp.bz[f - 1], sp.bz[f]
            z = float(z0 + w * (z1 - z0)) if np.isfinite(z0) and np.isfinite(z1) else (float(z0) if np.isfinite(z0) else float("nan"))
            inside = abs(y) <= half_goal and (not has_z or not np.isfinite(z) or z <= bar)
            if inside:
                out.append(_crossing_row(f, s, y, z, bool(live[f]), "observed"))
    out += frozen_crossings(sp, L, cfg, bk)
    out.sort(key=lambda r: r["index"])
    return out


def frozen_crossings(sp, L: float, cfg: PrepConfig, bk: dict | None = None) -> list[dict]:
    """``R-GOAL-FREEZE`` (v4.2): at each live -> dead transition (i live, i + 1 dead) where the provider holds the ball's
    horizontal position (x and y of i + 1 equal those of i), extrapolate the last live velocity of i for at most
    ``goal_freeze_extrapolate_s``. When the line s * x = L/2 is reached inside the goal and no player is within
    ``goal_freeze_player_min_m`` of the ball at i, the crossing is at frame i + 1 with ``crossing_method = extrapolated``.
    z at the line: the last live height plus the vertical velocity of the last two live samples."""
    from .touches import ball_kinematics

    if bk is None:
        bk = ball_kinematics(sp, cfg)
    out = []
    has_z = bool(np.isfinite(sp.bz).any())
    half_goal = cfg.goal_width_m / 2 + cfg.ball_radius_m
    bar = cfg.crossbar_height_m + cfg.ball_radius_m
    t_max = cfg.goal_freeze_extrapolate_s
    live = sp.bstatus == 1
    for i in np.flatnonzero(live[:-1] & ~live[1:]):
        i = int(i)
        if not (sp.bx[i + 1] == sp.bx[i] and sp.by[i + 1] == sp.by[i]):
            continue  # not frozen
        vx, vy = bk["vx"][i], bk["vy"][i]
        if not (np.isfinite(vx) and np.isfinite(vy)):
            continue
        with np.errstate(invalid="ignore"):
            dp = np.hypot(sp.px[i] - sp.bx[i], sp.py[i] - sp.by[i])
        if np.isfinite(dp).any() and float(np.nanmin(dp)) < cfg.goal_freeze_player_min_m:
            continue
        for s in (1.0, -1.0):
            x0, v = s * sp.bx[i], s * vx
            if not (x0 < L / 2 and v > 0):
                continue
            t = (L / 2 - x0) / v
            if t > t_max + 1e-9:
                continue
            y = float(sp.by[i] + vy * t)
            z = float("nan")
            if has_z and np.isfinite(sp.bz[i]):
                vz = (sp.bz[i] - sp.bz[i - 1]) * sp.fs if i > 0 and live[i - 1] and np.isfinite(sp.bz[i - 1]) else 0.0
                z = float(sp.bz[i] + vz * t)
            if abs(y) <= half_goal and (not has_z or not np.isfinite(z) or z <= bar):
                out.append(_crossing_row(i + 1, s, y, z, False, "extrapolated", round(float(t), 3)))
    return out


def ball_goes_dead(sp, f: int, cfg: PrepConfig) -> str:
    """Step 2: ``dead`` when the ball is dead at some frame in [f, f + goal_dead_within_s]; ``play_continued``
    otherwise; ``pending`` in live mode when those frames have not all arrived yet."""
    hi = f + cfg.frames(cfg.goal_dead_within_s, sp.fs)
    last = len(sp.n) - 1
    if (sp.bstatus[f:min(hi, last) + 1] != 1).any():
        return "dead"
    if hi > last and not getattr(sp, "complete", True):
        return "pending"
    return "play_continued"


def next_restart(restarts: list[dict], t_cross: float) -> dict | None:
    """Step 3: the first restart event of the period after the crossing. ``restarts``: dicts with ``t_eff`` (the
    located dead->live time when the restart is located, otherwise its corrected event time), in any order."""
    after = [r for r in restarts if r["t_eff"] > t_cross]
    return min(after, key=lambda r: (r["t_eff"], r["order"])) if after else None


def last_restart_index(sp, f: int) -> int:
    """Index of the last dead->live transition at or before ``f - 1`` (0 when the ball was never dead before)."""
    st = sp.bstatus[:f]
    tr = np.flatnonzero((st[:-1] != 1) & (st[1:] == 1)) + 1
    return int(tr[-1]) if len(tr) else 0


def goal_shot(sp, tt: TouchTable, f: int, attack_side: str, restart_side: str | None, cfg: PrepConfig) -> dict:
    """Step 4: the goal shot of a crossing at ``f``: ``{"index", "col", "method"}`` or ``{"index": None}``.

    The kick is searched up to and including the crossing frame ``f`` (v4.2: a tap-in whose touch peak falls on the
    crossing frame is the goal shot). ``restart_side``: the team side that took the restart opening the action
    (None if unknown)."""
    r0 = last_restart_index(sp, f)
    cols = [c for c, side in enumerate(sp.team_side) if side == attack_side]
    kicks = []
    for c in cols:
        for i, d, m in kick_frames(sp, tt, c, r0, f, cfg, shot=True) + soft_kicks(sp, tt, c, r0, f, cfg):
            kicks.append((i, -d, c, m))
    if kicks:
        i, nd, c, m = max(kicks)  # the latest kick; at equal frames the closest player
        return {"index": int(i), "col": int(c), "method": "goal_backwards", "kick_method": m, "restart_index": r0}
    if restart_side == attack_side and sp.bstatus[r0] == 1:
        c, _ = nearest_player(sp, r0, cfg, cols=cols)
        return {"index": int(r0), "col": c, "method": "restart_transition", "kick_method": "restart_transition", "restart_index": r0}
    return {"index": None, "col": None, "method": None, "kick_method": None, "restart_index": r0}


def attribute_goal(sp, tt: TouchTable, k: int, kicker: int, f: int, attack_side: str, L: float, cfg: PrepConfig) -> dict:
    """``R-GOAL`` attribution with a deflection (v4.1). ``k``: the goal shot's kick frame, ``kicker`` its player, ``f`` the
    crossing frame. A team-mate's deflection is already the goal shot (the last attacking kick). When players of the
    defending team deflect the ball (a ground or an aerial touch) between the kick (from ``shot_touch_min_separation_s``)
    and 0.2 s before the crossing (closer to the crossing a peak can be the net), the direction of the flight **before
    the first of these touches** is fitted (``R-SHOT-DIRECTION``):

    * it was going on target (the fitted line crosses the goal line between the posts, ball radius included): the goal
      stays with the kicker;
    * it was not (wide or away from the goal): an **own goal** of the last defending player who touched the ball;
    * the direction cannot be fitted (fewer than ``shot_flight_min_samples``): the goal stays with the kicker.

    Returns ``{"scorer_col", "own_goal", "deflected_by_col", "pre_deflection_on_target", "pre_deflection_y_line_m"}``."""
    from .outcomes import shot_direction

    out = {"scorer_col": kicker, "own_goal": False, "deflected_by_col": None, "pre_deflection_on_target": None, "pre_deflection_y_line_m": None}
    if kicker is None:
        return out
    sep = cfg.frames(cfg.shot_touch_min_separation_s, sp.fs)
    tol = cfg.frames(cfg.touch_peak_tolerance_s, sp.fs)
    # A deflection is a ground or an aerial touch (R-TOUCH, R-AERIAL): a high |a| bump without the aerial change of
    # direction (for example on a curling shot that a diving goalkeeper misses) is not one. Nor is a peak within +-0.2 s
    # of the crossing (as R-WOODWORK, the net): it can be the ball stopping in the net or a provider freeze at the filter
    # edge (J03WMX, episode 3: the last live sample before the freeze, the goalkeeper 1.13 m away).
    sel = [int(j) for j in np.flatnonzero((tt.frames >= k + sep) & (tt.frames < f - tol) & tt.touch & (tt.ground | tt.aerial))
           if sp.team_side[int(tt.col[j])] != attack_side and sp.bstatus[int(tt.frames[j])] == 1]
    if not sel:
        return out
    first, last_col = int(tt.frames[sel[0]]), int(tt.col[sel[-1]])
    out["deflected_by_col"] = last_col
    s_att = sp.home_attack_sign * (1.0 if attack_side == "home" else -1.0)
    dr = shot_direction(sp, k, first - 1, s_att, L, cfg)
    if dr["direction"] == "direction_undetermined":
        return out
    on_target = dr["direction"] != "away_from_goal" and dr["y_line_m"] is not None and abs(dr["y_line_m"]) <= cfg.goal_width_m / 2 + cfg.ball_radius_m
    out.update(pre_deflection_on_target=bool(on_target), pre_deflection_y_line_m=dr["y_line_m"])
    if not on_target:
        out.update(scorer_col=last_col, own_goal=True)
    return out
