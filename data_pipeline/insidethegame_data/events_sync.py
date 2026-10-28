"""Locate goals, passes, shots, clearances and duels from trajectories (guidelines v4.2, section 9.3 steps 3-7).

The annotated event time is only a hint; trajectories decide, and the annotated event is
never changed (our checks add ``sync_*``, ``type_check`` and outcome columns next to it).
Per period, in this order:

3. **Goals** (``R-GOAL``, ``goals.py``): goal-line crossings (entry rule, ``R-GOAL-FREEZE``)
   confirmed looking forward by the next restart or, with no restart and no blocking event
   before the FinalWhistle, by the end of the period (v4.1); the goal shot searched backwards
   (also for unconfirmed crossings, v4.2) and attributed with ``attribute_goal``; a disallowed
   goal takes ``disallowed_reason`` from a ``GoalDisallowed`` event. Matched goal shots are fixed
   anchors.
4. **Candidates** (``touches.py``): for each pass, shot or clearance, the kicks of the annotated
   player inside the search window around the corrected time: ground touches (``R-TOUCH``),
   aerial touches (``R-AERIAL``; >= 1.6 m for shots, ``R-SHOT-HEADER``) and, for shots, soft
   kicks (``R-SHOT-SOFT``); a kick needs the player to own the touch (``R-TOUCH-NEAREST``).
   When the window is mostly dead ball the search extends backwards (``R-LATE-ANNOTATION``).
   Duels: the duel signature (``R-DUEL``). At most 8 per event, score
   1 - 0.5 |t - t_c| / window - 0.5 d / 2 m.
5. **Order-preserving alignment** (``R-ALIGN``): a Needleman-Wunsch style dynamic programme picks
   at most one candidate per event so that the located frames keep the event order and two
   kick-type events are at least 0.2 s apart (0.08 s after a shot, ``R-SHOT-SPLIT``); located
   restarts and goal shots are fixed anchors (weight 2). An aerial duel won with
   ``WinnerAction = shot`` by the player of the next shot may share the shot's frame (v4.2).
6. **Location** (``R-SYNC-LOCATION``): zone and distance from the ball at ``sync_frame``.
7. **Type checks and outcomes** (``outcomes.py``): shots (``R-SHOT-*``, ``R-WOODWORK``), passes
   (``R-PASS-KICK``, ``R-PASS-OUTCOME``), clearances (``R-CLEARANCE``); duels are ``not_applicable``.

Every check uses live play only (``R-LIVE-ONLY``). In live mode (``SyncPeriod.complete`` False:
the arrays hold only the frames released so far) an event whose corrected time plus the search
window has not been reached is not processed, and checks that look forward stay ``pending``
until their frames arrive (``R-LIVE-BUFFER``).

Nothing here reads provider speed or acceleration. The provider's own synchronised frame (DFL
``CalculatedFrame``) is compared afterwards, for tests only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import PrepConfig
from .goals import attribute_goal, ball_goes_dead, goal_crossings, goal_shot, last_restart_index, next_restart
from .mapping import map_shot_outcome
from .outcomes import (CLEARANCE_CONDITIONS, clearance_check, clearance_conditions, directed_at_teammate, free_flight,
                       kick_power_kmh, opponent_at_kick, pass_outcome, shot_check, shot_direction, shot_outcome, wide_margin)
from .touches import (TouchTable, ball_kinematics, incoming_height, kick_frames, nearest_player, next_touch, soft_kicks, touch_peaks,  # noqa: F401
                      touch_table, woodwork_frames)

KICK_KINDS = ("pass", "shot", "clearance")
LOCATED_METHODS = ("kick_signature", "aerial_signature", "soft_kick_signature", "goal_backwards", "restart_transition", "duel_min_distance")
KICK_METHODS = ("kick_signature", "aerial_signature", "soft_kick_signature", "goal_backwards", "restart_transition")


@dataclass
class SyncPeriod:
    """One period in the canonical reference system (centre origin, metres)."""

    half: int
    fs: float
    n: np.ndarray  # provider frame numbers
    t_s: np.ndarray  # UTC seconds
    pids: list[str]
    team_side: list[str]  # "home" / "away" per column
    px: np.ndarray  # (frames, players) positions usable for locating (NaN where not a real measurement)
    py: np.ndarray
    bx: np.ndarray
    by: np.ndarray
    bz: np.ndarray  # NaN when the provider has no usable ball height (R-AERIAL and the crossbar tests are skipped)
    bstatus: np.ndarray  # provider ball status: 1 live, 0 dead
    home_attack_sign: float  # +1 if home attacks +x in this period (canonical), else -1
    # v4 (optional): our valid ball speed (R-SHOT-POWER, R-CLEARANCE C2), ball positions usable for the shot fit,
    # role group per column (R-CLEARANCE C5), and whether the period is complete (False in live mode, R-LIVE-BUFFER).
    ball_speed: np.ndarray | None = None
    ball_valid: np.ndarray | None = None
    ball_pos_ok: np.ndarray | None = None
    role_group: list[str] | None = None
    complete: bool = True


# ---------------------------------------------------------------------------
# Candidates (step 4)
# ---------------------------------------------------------------------------

def _window_idx(sp: SyncPeriod, t_c: float, win_s: float) -> tuple[int, int]:
    lo = int(np.searchsorted(sp.t_s, t_c - win_s, side="left"))
    hi = int(np.searchsorted(sp.t_s, t_c + win_s, side="right")) - 1
    return max(lo, 0), min(hi, len(sp.n) - 1)


def _score(sp: SyncPeriod, i: int, d: float, reach: float, t_c: float, win_s: float) -> float:
    return round(1.0 - 0.5 * abs(float(sp.t_s[i]) - t_c) / win_s - 0.5 * d / reach, 6)


def search_window(sp: SyncPeriod, t_c: float, win_s: float, cfg: PrepConfig) -> tuple[int, int, float, bool]:
    """(first frame, last frame, half-width used for the score, extended?) of the search for a kick.

    ``R-LATE-ANNOTATION`` (v4.2): when more than ``late_annotation_dead_share`` of the samples in [t_c - win, t_c + win]
    are dead ball, the window extends backwards to ``late_annotation_max_s`` before the corrected time (the previous live
    phase); the score then uses the extended half-width, so a candidate far from the corrected time still scores >= 0."""
    lo, hi = _window_idx(sp, t_c, win_s)
    if hi < lo:
        return lo, hi, win_s, False
    dead_share = float(np.mean(sp.bstatus[lo:hi + 1] != 1))
    if dead_share <= cfg.late_annotation_dead_share:
        return lo, hi, win_s, False
    lo_ext = max(0, int(np.searchsorted(sp.t_s, t_c - cfg.late_annotation_max_s, side="left")))
    if lo_ext >= lo:
        return lo, hi, win_s, False
    return lo_ext, hi, max(win_s, t_c - float(sp.t_s[lo_ext])), True


def kick_candidates(sp: SyncPeriod, tt: TouchTable, col: int, t_c: float, win_s: float, cfg: PrepConfig, kind: str = "pass",
                    extended: list | None = None) -> list[tuple[int, float, str]]:
    """(frame index, score, method) of the kicks of player ``col`` inside the search window (best first). ``extended``:
    when a list is given, ``True`` is appended if ``R-LATE-ANNOTATION`` extended the window, else ``False``."""
    lo, hi, w, ext = search_window(sp, t_c, win_s, cfg)
    if extended is not None:
        extended.append(ext)
    if hi < lo:
        return []
    shot = kind == "shot"
    found = kick_frames(sp, tt, col, lo, hi, cfg, shot=shot)
    if shot:
        found += soft_kicks(sp, tt, col, lo, hi, cfg)
    out = [(i, _score(sp, i, d, cfg.touch_reach_m, t_c, w), m) for i, d, m in found]
    out.sort(key=lambda c: (-c[1], c[0]))
    return out[: cfg.sync_max_candidates]


def _dist_player_ball(sp: SyncPeriod, col: int, lo: int, hi: int) -> np.ndarray:
    lo, hi = max(0, lo), min(len(sp.n) - 1, hi)
    with np.errstate(invalid="ignore"):
        return np.hypot(sp.px[lo:hi + 1, col] - sp.bx[lo:hi + 1], sp.py[lo:hi + 1, col] - sp.by[lo:hi + 1])


def duel_candidates(sp: SyncPeriod, col_a: int, col_b: int, t_c: float, win_s: float, cfg: PrepConfig) -> list[tuple[int, float]]:
    """``R-DUEL``: (frame index, score) at local minima of max(d(a, ball), d(b, ball)) inside the window, live play only."""
    idx = np.flatnonzero(np.abs(sp.t_s - t_c) <= win_s)
    if len(idx) == 0:
        return []
    lo, hi = int(idx[0]), int(idx[-1])
    with np.errstate(invalid="ignore"):
        f = np.fmax(_dist_player_ball(sp, col_a, lo, hi), _dist_player_ball(sp, col_b, lo, hi))
    f = np.where(np.isfinite(f) & (sp.bstatus[lo:hi + 1] == 1), f, np.inf)
    left, right = np.r_[np.inf, f[:-1]], np.r_[f[1:], np.inf]
    loc = np.flatnonzero((f <= cfg.duel_reach_m) & (f < left) & (f <= right))
    out = []
    for j in loc:
        i = lo + int(j)
        score = 1.0 - 0.5 * abs(float(sp.t_s[i]) - t_c) / win_s - 0.5 * float(f[j]) / cfg.duel_reach_m
        out.append((i, round(score, 6)))
    out.sort(key=lambda c: (-c[1], c[0]))
    return out[: cfg.sync_max_candidates]


# ---------------------------------------------------------------------------
# Order-preserving alignment (R-ALIGN)
# ---------------------------------------------------------------------------

def align(items: list[dict], sep: int) -> list[int | None]:
    """Pick at most one candidate per item, maximising the total score, so that the chosen frames are
    non-decreasing in item order and two kick-type items are at least ``sep`` frames apart (``sep_after`` of
    the earlier item when given: 2 frames after a shot, ``R-SHOT-SPLIT``).

    ``items``: in event order, each ``{"kick": bool, "cands": [(frame, score), ...], "sep_after": int?,
    "order_free_next": bool?}``. An item may be skipped (a gap of the alignment, score 0). Returns the chosen candidate
    index per item (or None). Ties are broken by the first state in insertion order, so the result is deterministic.

    ``order_free_next`` (v4.2, ``R-DUEL``): the item must still follow the items before it, but its own frame does not
    constrain the items after it (an aerial duel won with a shot may share the shot's frame, or come just after it).
    """
    F = [-(10 ** 12)]
    SA = [0]
    S = [0.0]
    P = [-1]
    IT = [-1]
    CI = [-1]
    for it, item in enumerate(items):
        if not item["cands"]:
            continue
        f_arr, sa_arr, s_arr = np.array(F), np.array(SA), np.array(S)
        sa_new = int(item.get("sep_after", sep)) if item["kick"] else 0
        new = []
        for ci, (f, sc) in enumerate(item["cands"]):
            ok = (f_arr + sa_arr <= f) if item["kick"] else (f_arr <= f)
            if not ok.any():
                continue
            masked = np.where(ok, s_arr, -np.inf)
            b = int(np.argmax(masked))
            if item.get("order_free_next"):
                new.append((F[b], SA[b], float(masked[b]) + sc, b, it, ci))  # later items see the predecessor's frame
                continue
            new.append((f, sa_new, float(masked[b]) + sc, b, it, ci))
        for f, sa, s, b, i_, c_ in new:
            F.append(f); SA.append(sa); S.append(s); P.append(b); IT.append(i_); CI.append(c_)  # noqa: E702
    chosen: list[int | None] = [None] * len(items)
    st = int(np.argmax(np.array(S)))
    while st > 0:
        chosen[IT[st]] = CI[st]
        st = P[st]
    return chosen


def mark_duel_shot_pairs(items: list[dict], rows: list) -> list[dict]:
    """``R-DUEL`` / ``R-ALIGN`` (v4.2): DFL annotates an aerial duel won with ``WinnerAction = shot`` just before the
    winner's header, but ``R-DUEL`` places it at the header contact, one frame before or after it, so the order
    constraint could drop the shot. Such a duel (item key ``shot_duel_winner``) gets ``order_free_next`` when the next
    kick-type item is a shot by the duel winner: the pair may then share a frame, in either order. The order constraint
    is relaxed for that pair only (the duel is the last item before the shot). ``rows``: (k, kind, player, t_c, methods)
    per item, in the same order. Returns the items (the same dicts, marked)."""
    for d, item in enumerate(items):
        w = item.get("shot_duel_winner")
        if w is None:
            continue
        nxt = next((j for j in range(d + 1, len(items)) if items[j]["kick"]), None)
        if nxt is not None and rows[nxt][1] == "shot" and rows[nxt][2] == w:
            item["order_free_next"] = True
    return items


# ---------------------------------------------------------------------------
# Event location (step 6)
# ---------------------------------------------------------------------------

def location_fields(sp: SyncPeriod, i: int, team_side: str | None, col: int | None, L: float, cfg: PrepConfig) -> dict:
    """``R-SYNC-LOCATION``: ball position, zone and distance to the attacked goal at the synchronised frame."""
    bx, by = float(sp.bx[i]), float(sp.by[i])
    out = {"sync_x_m": round(bx, 3), "sync_y_m": round(by, 3)}
    if team_side in ("home", "away"):
        s = sp.home_attack_sign * (1.0 if team_side == "home" else -1.0)
        xa, ya = s * bx, s * by
        third = "attacking_third" if xa > L / 6 else ("defensive_third" if xa < -L / 6 else "middle_third")
        box = abs(ya) <= cfg.penalty_area_width_m / 2
        pa = "attacking" if box and xa >= L / 2 - cfg.penalty_area_depth_m else ("defensive" if box and xa <= -L / 2 + cfg.penalty_area_depth_m else "none")
        out.update(sync_x_att_m=round(xa, 3), sync_y_att_m=round(ya, 3), sync_zone=third, sync_penalty_area=pa,
                   sync_goal_distance_m=round(float(np.hypot(L / 2 - xa, ya)), 3))
    if col is not None:
        d = _dist_player_ball(sp, col, i, i)
        out["sync_player_ball_m"] = round(float(d[0]), 3) if np.isfinite(d[0]) else None
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

# R-GOAL: a set piece that is itself a shot. DFL nests the shot inside the restart (``Penalty`` or a direct
# ``FreeKick`` with sub-types ``ShotAtGoal/<outcome>``). It stays a restart for locating (its dead->live
# transition, R-RESTART-SUB) and is never an open-play shot (no R-SHOT-* checks, no xG), but a goal scored from
# it is an annotated goal, matched to its crossing like any goal shot.
SET_PIECE_SHOT_SUBTYPE = "ShotAtGoal"
# Annotated taker of a set-piece shot (for ``shooter_mismatch``): the event's player, else the DFL penalty taker.
# DFL keeps the taker of a free-kick shot on the nested element only, so it is usually unknown (mismatch not judged).
SET_PIECE_TAKER_ATTRIBUTES = ("Player", "ProspectiveTaker")


def set_piece_shot_label(event_type, sub_types, cfg: PrepConfig) -> str | None:
    """English outcome of a set-piece shot (a restart whose first sub-type is a shot, section 16.3), else None."""
    if event_type not in cfg.restart_event_types or not isinstance(sub_types, str):
        return None
    parts = sub_types.split("/")
    if parts[0] != SET_PIECE_SHOT_SUBTYPE:
        return None
    return map_shot_outcome("/".join(parts[1:]) or None)["outcome"]


def set_piece_taker(attrs: dict) -> str | None:
    return next((str(attrs[a]) for a in SET_PIECE_TAKER_ATTRIBUTES if attrs.get(a)), None)


def event_kind(event_type: str, attrs: dict, cfg: PrepConfig) -> str | None:
    if event_type == "Play":
        return "pass"
    if event_type == "ShotAtGoal":
        return "shot"
    if event_type == "OtherBallAction" and str(attrs.get(cfg.clearance_attribute, "")).lower() == "true":
        return "clearance"
    if event_type == "TacklingGame":
        return "duel"
    return None


NEW_COLS = ("event_kind", "acting_team_side", "type_check_basis", "type_check_reason", "kick_method", "aerial", "sync_x_m", "sync_y_m",
            "sync_x_att_m", "sync_y_att_m", "sync_zone", "sync_penalty_area", "sync_goal_distance_m", "sync_player_ball_m",
            "next_touch_frame", "next_touch_object_id", "next_touch_team_side", "next_touch_kind", "next_touch_after_s", "pass_length_m", "pass_outcome",
            "flight_end_kind", "flight_end_frame", "shot_outcome_label", "shot_outcome_observed", "shot_goal_line_y_m", "shot_direction",
            "shot_directed_at_teammate", "shot_power_kmh", "shot_power_evidence", "goal_check", "shooter_mismatch",
            "clearance_conditions", "clearance_score", "n_candidates",
            # v4.2 (added, never renamed): R-LATE-ANNOTATION, R-SHOT-DIRECTION margin, block at the kick, header for
            # R-SHOT-POWER, how a goal was confirmed, own goal and attributed scorer (R-GOAL attribution).
            "sync_window_extended", "shot_wide_margin_m", "shot_blocked_at_kick", "shot_header", "goal_confirmed_by", "own_goal",
            "goal_scorer_id")


def _shot_rows_of_period(sub: pd.DataFrame, pos: dict, side_of_team: dict, off_s: float, restart_t_eff: dict[int, float], cfg: PrepConfig) -> list[dict]:
    """The annotated shots that a goal crossing can be matched to (``R-GOAL`` step 5): open-play ``ShotAtGoal`` events
    and set-piece shots (penalties, direct free kicks). A set-piece shot's time is its located dead->live transition
    (``restart_t_eff``: event row -> time), where ``R-GOAL`` places the kick, else its corrected time."""
    rows = []
    for idx, r in sub.loc[(sub["event_type"] == "ShotAtGoal") | sub["event_type"].isin(cfg.restart_event_types)].iterrows():
        attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
        k = pos[idx]
        if r["event_type"] == "ShotAtGoal":
            rows.append({"k": k, "side": side_of_team.get(attrs.get("Team")), "player": attrs.get("Player"), "set_piece": False,
                         "label": map_shot_outcome(r["sub_types"])["outcome"], "t_c": r["time_utc"].timestamp() + off_s})
            continue
        label = set_piece_shot_label(r["event_type"], r["sub_types"], cfg)
        if label is not None:
            rows.append({"k": k, "side": side_of_team.get(attrs.get("Team")), "player": set_piece_taker(attrs), "set_piece": True,
                         "label": label, "t_c": restart_t_eff.get(k, r["time_utc"].timestamp() + off_s)})
    return rows


def locate_play_events(ev: pd.DataFrame, periods: dict[int, SyncPeriod], offsets: dict[int, dict], gk_ids: set[str], side_of_team: dict[str, str],
                       L: float, cfg: PrepConfig, pass_p99_kmh: float | None = None) -> pd.DataFrame:
    """Fill ``sync_*``, ``type_check`` and the v4 outcome columns for goals, passes, shots, clearances and duels.

    ``ev`` must already carry the restart / substitution location of ``timealign.locate_events`` (restarts are
    fixed anchors and the forward look of ``R-GOAL``). ``pass_p99_kmh``: the 99th percentile of DFL pass speeds for
    ``R-SHOT-POWER`` (default ``cfg.pass_speed_p99_kmh``). Rows are returned in the same order; the goal crossings
    are in ``ev.attrs["goal_crossings"]`` and the counts in ``ev.attrs["sync_log"]``.
    """
    p99 = cfg.pass_speed_p99_kmh if pass_p99_kmh is None else float(pass_p99_kmh)
    ev = ev.copy()
    new_cols = {c: [None] * len(ev) for c in NEW_COLS}
    sync_time = list(ev["sync_time_utc"])
    sync_frame = list(ev["sync_frame"])
    sync_cert = list(ev["sync_certainty"])
    sync_method = list(ev["sync_method"])
    sync_res = list(ev["sync_residual_s"])
    type_check = list(ev["type_check"])
    pos = {idx: k for k, idx in enumerate(ev.index)}
    log = {"ground_touches": 0, "aerial_touches": 0, "woodwork_contacts": 0, "ball_height_used": False,
           "shot_power_threshold_kmh": round(p99, 2), "pending_events": 0, "late_annotation_windows": 0, "woodwork": []}
    crossings_out: list[dict] = []
    shot_sep = cfg.frames(cfg.shot_touch_min_separation_s, next(iter(periods.values())).fs) if periods else 2
    for h, sp in periods.items():
        n0 = int(sp.n[0])
        bk = ball_kinematics(sp, cfg, L)  # v4.2: live segments ending in a goal continue through the in-goal samples
        tt = touch_table(sp, bk, cfg)
        crossings = goal_crossings(sp, L, cfg, bk)  # R-GOAL step 1 (also tells R-WOODWORK where the ball entered the net)
        in_net = [c["index"] for c in crossings if ball_goes_dead(sp, c["index"], cfg) != "play_continued"]
        ww = woodwork_frames(sp, tt, L, cfg, bk, crossing_frames=in_net)
        ww_frames = [w["frame"] for w in ww]
        if ww_frames:  # R-WOODWORK (v4.2): a woodwork contact is not a player's touch
            tt.col[np.isin(tt.frames, ww_frames)] = -1
        log["ground_touches"] += int((tt.ground & tt.touch).sum())
        log["aerial_touches"] += int((tt.aerial & tt.touch).sum())
        log["woodwork_contacts"] += len(ww)
        log["woodwork"] += [{"period": h, "frame": int(sp.n[w["frame"]]), **{k: v for k, v in w.items() if k not in ("frame", "contact_frame")}} for w in ww]
        log["ball_height_used"] = log["ball_height_used"] or bool(np.isfinite(sp.bz).any())
        col_of = {p: i for i, p in enumerate(sp.pids)}
        off = offsets.get(h, {})
        off_s = float(off.get("event_clock_offset_s", 0.0) or 0.0)
        win = float(off.get("search_window_s", cfg.event_search_min_s))
        sub = ev.loc[ev["half"] == h]
        t_last = float(sp.t_s[-1])
        # R-GOAL (v4.1, v4.2): the period's FinalWhistle, the events that block a goal confirmed by the end of the period,
        # and the GoalDisallowed events (their Reason is the disallowed_reason), all at their corrected times.
        t_final = None
        blocking, disallowed_events = [], []
        for idx, r in sub.loc[sub["event_type"].isin((cfg.final_whistle_event, cfg.goal_disallowed_event) + tuple(cfg.goal_end_blocking_events))].iterrows():
            t_e = r["time_utc"].timestamp() + off_s
            if r["event_type"] == cfg.final_whistle_event:
                t_final = t_e if t_final is None else min(t_final, t_e)
                continue
            attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
            if r["event_type"] in cfg.goal_end_blocking_events:
                blocking.append((t_e, r["event_type"]))
            if r["event_type"] == cfg.goal_disallowed_event:
                disallowed_events.append({"t": t_e, "event_id": r["event_id"], "side": side_of_team.get(attrs.get("Team")), "reason": attrs.get("Reason")})

        # --- restarts of the period (located ones at their dead->live frame) ---
        restarts = []
        for idx, r in sub.loc[sub["event_type"].isin(cfg.restart_event_types)].iterrows():
            attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
            located = r["sync_method"] == "dead_live_transition" and pd.notna(r["sync_frame"])
            i = int(r["sync_frame"]) - n0 if located else None
            restarts.append({"k": pos[idx], "type": r["event_type"], "side": side_of_team.get(attrs.get("Team")), "index": i,
                             "t_eff": float(sp.t_s[i]) if located else r["time_utc"].timestamp() + off_s, "order": pos[idx]})
        restart_side_at = {r["index"]: r["side"] for r in restarts if r["index"] is not None}

        # --- step 3: goals (R-GOAL) ---
        shots = _shot_rows_of_period(sub, pos, side_of_team, off_s, {r["k"]: r["t_eff"] for r in restarts}, cfg)
        anchors: dict[int, dict] = {}
        for cr in crossings:
            f = cr["index"]
            t_cross = float(sp.t_s[f])
            attack_side = "home" if cr["goal_x_sign"] == sp.home_attack_sign else "away"
            dead = ball_goes_dead(sp, f, cfg)
            nr = None
            confirmed_by = None
            if dead != "dead":
                result = dead  # play_continued, or pending in live mode
            else:
                nr = next_restart(restarts, t_cross)
                if nr is None:
                    # R-GOAL (v4.1): no restart before the end of the period. A goal confirmed by the end of the period when
                    # the FinalWhistle comes after the crossing and no blocking event (VAR, GoalDisallowed, Foul, Offside)
                    # lies between the crossing (minus the search window, the tolerance of annotated times) and the whistle.
                    blocked = [e for t_e, e in blocking if t_cross - win <= t_e <= (t_final if t_final is not None else np.inf)]
                    if not sp.complete:
                        result = "pending"
                    elif t_final is not None and t_final > t_cross and not blocked:
                        result, confirmed_by = "goal", "end_of_period"
                    else:
                        result = "unconfirmed"
                else:
                    result = "goal" if nr["type"] == "KickOff" else "disallowed_goal"
                    confirmed_by = "kickoff" if result == "goal" else None
            row = {"period": h, "frame": int(sp.n[f]), "index": f, "time_utc": pd.Timestamp(t_cross, unit="s", tz="UTC").isoformat(),
                   "attacking_side": attack_side, "y_line_m": cr["y_line_m"], "z_line_m": cr["z_line_m"], "status_at_crossing": cr["status_at_f"],
                   "goal_check": result, "next_restart_type": nr["type"] if nr else None, "shot_event_id": None, "shooter_mismatch": None,
                   "kick_frame": None, "kicker_id": None, "shot_method": None, "annotated_label": None,
                   # v4.2 fields (added, never renamed)
                   "crossing_method": cr.get("crossing_method", "observed"), "extrapolated_s": cr.get("extrapolated_s"), "confirmed_by": confirmed_by,
                   "disallowed_reason": None, "goal_disallowed_event_id": None, "scorer_id": None, "own_goal": None, "deflected_by_id": None,
                   "pre_deflection_on_target": None}
            if result in ("goal", "disallowed_goal", "unconfirmed"):
                if result in ("disallowed_goal", "unconfirmed"):
                    # R-GOAL (v4.2): the reason of a GoalDisallowed of the attacking team between the crossing (minus the
                    # search window) and the next restart (the end of the period without one).
                    t_end = nr["t_eff"] if nr is not None else np.inf
                    gd = [d for d in disallowed_events if t_cross - win <= d["t"] <= t_end and d["side"] in (attack_side, None)]
                    if gd:
                        d0 = min(gd, key=lambda d: (abs(d["t"] - t_cross), d["event_id"]))
                        row.update(disallowed_reason=d0["reason"], goal_disallowed_event_id=d0["event_id"])
                g = goal_shot(sp, tt, f, attack_side, restart_side_at.get(last_restart_index(sp, f)), cfg)
                row.update(kick_frame=int(sp.n[g["index"]]) if g["index"] is not None else None,
                           kicker_id=sp.pids[g["col"]] if g["col"] is not None else None, shot_method=g["method"])
                scorer_col = g["col"]
                if g["index"] is not None and g["col"] is not None:
                    att = attribute_goal(sp, tt, g["index"], g["col"], f, attack_side, L, cfg)
                    scorer_col = att["scorer_col"]
                    row.update(scorer_id=sp.pids[scorer_col], own_goal=att["own_goal"],
                               deflected_by_id=sp.pids[att["deflected_by_col"]] if att["deflected_by_col"] is not None else None,
                               pre_deflection_on_target=att["pre_deflection_on_target"])
                t_kick = float(sp.t_s[g["index"]]) if g["index"] is not None else t_cross
                cands = [s for s in shots if s["side"] == attack_side and s["k"] not in anchors and t_kick - win <= s["t_c"] <= t_cross + win
                         and (s["label"] == "goal" or result in ("disallowed_goal", "unconfirmed"))]
                if cands:
                    best = min(cands, key=lambda s: (s["label"] != "goal", abs(s["t_c"] - t_kick), s["k"]))
                    row["shot_event_id"] = ev.iloc[best["k"]]["event_id"]
                    row["annotated_label"] = best["label"]
                    if scorer_col is not None and best["player"] is not None:  # no annotated scorer: the mismatch cannot be judged
                        row["shooter_mismatch"] = bool(best["player"] != sp.pids[scorer_col])
                    if g["index"] is not None:
                        anchors[best["k"]] = {"index": g["index"], "method": g["method"], "kick_method": g["kick_method"], "crossing": row}
            crossings_out.append(row)
        set_piece_label = {s["k"]: s["label"] for s in shots if s["set_piece"]}
        goal_of_shot = {r["shot_event_id"]: r for r in crossings_out if r["period"] == h and r["shot_event_id"]}
        goal_kicks = [(r["index"], r["kicker_id"], r) for r in crossings_out if r["period"] == h and r["kick_frame"] is not None]
        cross_result = {r["index"]: r["goal_check"] for r in crossings_out if r["period"] == h}
        flight_crossings = [r["index"] for r in crossings_out if r["period"] == h and r["goal_check"] in ("goal", "disallowed_goal", "unconfirmed", "pending")]

        # --- step 4 and 5: candidates and alignment ---
        items, rows = [], []
        for idx, r in sub.iterrows():
            k = pos[idx]
            attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
            kind = event_kind(r["event_type"], attrs, cfg)
            if k in set_piece_label:
                # R-GOAL: a set-piece shot keeps type_check not_applicable (no open-play shot checks); its annotated
                # outcome and, when matched to a crossing, the goal check and shooter mismatch are recorded.
                new_cols["shot_outcome_label"][k] = set_piece_label[k]
                grow = goal_of_shot.get(r["event_id"])
                if grow is not None:
                    new_cols["goal_check"][k], new_cols["shooter_mismatch"][k] = grow["goal_check"], grow["shooter_mismatch"]
                    new_cols["goal_confirmed_by"][k], new_cols["own_goal"][k] = grow.get("confirmed_by"), grow.get("own_goal")
                    new_cols["goal_scorer_id"][k] = grow.get("scorer_id")
            if r["sync_method"] == "dead_live_transition" and pd.notna(r["sync_frame"]):
                items.append({"kick": True, "cands": [(int(r["sync_frame"]) - n0, cfg.sync_anchor_weight)]})
                rows.append((k, None, None, None, []))
                i = int(r["sync_frame"]) - n0
                team = side_of_team.get(attrs.get("Team"))
                new_cols["acting_team_side"][k] = team
                for c, v in location_fields(sp, i, team, None, L, cfg).items():
                    new_cols[c][k] = v
                continue
            if kind is None:
                continue
            new_cols["event_kind"][k] = kind
            new_cols["acting_team_side"][k] = side_of_team.get(attrs.get("WinnerTeam" if kind == "duel" else "Team"))
            if kind == "shot":
                new_cols["shot_outcome_label"][k] = map_shot_outcome(r["sub_types"])["outcome"]
            t_c = r["time_utc"].timestamp() + off_s
            if not sp.complete and t_c + win > t_last:
                # R-LIVE-BUFFER: the frames of this event have not all arrived yet; it waits in the buffer.
                sync_method[k], type_check[k] = "pending", "pending"
                log["pending_events"] += 1
                continue
            methods: list[str] = []
            if k in anchors:
                a = anchors[k]
                cands = [(a["index"], cfg.sync_anchor_weight)]
                methods = [a["method"]]
                player = col_of.get(attrs.get("Player"))
            elif kind == "duel":
                a_, b_ = col_of.get(attrs.get("Winner")), col_of.get(attrs.get("Loser"))
                cands = duel_candidates(sp, a_, b_, t_c, win, cfg) if a_ is not None and b_ is not None else []
                methods = ["duel_min_distance"] * len(cands)
                player = a_
            else:
                player = col_of.get(attrs.get("Player"))
                ext: list[bool] = []
                found = kick_candidates(sp, tt, player, t_c, win, cfg, kind, extended=ext) if player is not None else []
                cands = [(i, s) for i, s, _ in found]
                methods = [m for _, _, m in found]
                if ext and ext[0]:  # R-LATE-ANNOTATION
                    new_cols["sync_window_extended"][k] = True
                    log["late_annotation_windows"] += 1
            new_cols["n_candidates"][k] = len(cands)
            item = {"kick": kind in KICK_KINDS, "cands": cands}
            if kind == "shot":
                item["sep_after"] = shot_sep
            if kind == "duel" and str(attrs.get("Type", "")) == cfg.duel_aerial_type and str(attrs.get("WinnerAction", "")) in cfg.duel_shot_winner_actions:
                item["shot_duel_winner"] = player  # R-DUEL / R-ALIGN (v4.2): may share the frame of the winner's next shot
            items.append(item)
            rows.append((k, kind, player, t_c, methods))
        items = mark_duel_shot_pairs(items, rows)
        chosen = align(items, cfg.frames(cfg.kick_min_separation_s, sp.fs))

        # --- step 6 and 7: location, type checks and outcomes ---
        for item, (k, kind, player, t_c, methods), ci in zip(items, rows, chosen):
            if kind is None:
                continue
            if ci is None:
                sync_method[k] = "no_signature_in_window" if not item["cands"] else "dropped_by_order"
                sync_cert[k] = 0.0
                type_check[k] = "not_confirmed" if kind in KICK_KINDS else "not_applicable"
                new_cols["type_check_reason"][k] = "event not located" if kind in KICK_KINDS else None
                continue
            i, score = item["cands"][ci]
            method = methods[ci]
            sync_time[k] = pd.Timestamp(float(sp.t_s[i]), unit="s", tz="UTC")
            sync_frame[k] = int(sp.n[i])
            sync_cert[k] = round(float(min(score, 1.0)), 3)
            sync_method[k] = method
            sync_res[k] = round(float(sp.t_s[i]) - t_c, 3)
            side = sp.team_side[player] if player is not None else new_cols["acting_team_side"][k]
            for c, v in location_fields(sp, i, side, player, L, cfg).items():
                new_cols[c][k] = v
            j = np.flatnonzero(tt.frames == i)
            new_cols["kick_method"][k] = anchors[k]["kick_method"] if k in anchors else method
            new_cols["aerial"][k] = bool(len(j) and tt.aerial[j[0]] and np.isfinite(tt.height(int(j[0]))) and tt.height(int(j[0])) > cfg.aerial_min_height_m)
            if kind == "duel":
                type_check[k] = "not_applicable"
                continue
            if player is None:  # a goal shot whose kicker is not the annotated player's column (anchor without a player)
                player = col_of.get(anchors[k]["crossing"]["kicker_id"]) if k in anchors else None
            if player is None:
                type_check[k], new_cols["type_check_reason"][k] = "not_confirmed", "annotated player not tracked"
                continue
            s_att = sp.home_attack_sign * (1.0 if sp.team_side[player] == "home" else -1.0)
            if kind == "pass":
                nt = next_touch(sp, tt, i, player, cfg)
                _next_touch_cols(new_cols, k, sp, nt, i)
                new_cols["pass_length_m"][k] = round(float(np.hypot(sp.bx[nt["frame"]] - sp.bx[i], sp.by[nt["frame"]] - sp.by[i])), 3)
                new_cols["pass_outcome"][k] = pass_outcome(sp, i, player, nt, cfg)
                type_check[k], new_cols["type_check_basis"][k] = "confirmed", "kick"  # R-PASS-KICK
            elif kind == "shot":
                goal_row = anchors[k]["crossing"] if k in anchors else None
                if goal_row is None:
                    for gi, gk_id, grow in goal_kicks:
                        if abs(gi - i) <= shot_sep and gk_id == sp.pids[player]:
                            goal_row = grow
                goal_result = goal_row["goal_check"] if goal_row is not None else None
                fl = free_flight(sp, tt, i, player, cfg, flight_crossings, ww_frames, shot=True)
                if goal_result is None and fl["end_kind"] == "goal_crossing" and cross_result.get(fl["crossing"]) == "pending":
                    goal_result = "pending"  # R-LIVE-BUFFER: the goal check waits for the next restart
                _next_touch_cols(new_cols, k, sp, fl["touch"], i)
                cy = goal_row["y_line_m"] if goal_result in ("goal", "disallowed_goal") else None
                nt = fl["touch"]
                power = kick_power_kmh(sp, i, nt["frame"] if nt["kind"] == "touch" else None, cfg.shot_power_window_s, cfg)
                margin = wide_margin(sp, i, s_att, L, power, p99, cfg)  # R-SHOT-DIRECTION (v4.1): 8 m or 6 m
                dr = shot_direction(sp, i, fl["end"], s_att, L, cfg, crossing_y=cy, margin_m=margin)
                receiver = nt["col"] if nt["kind"] == "touch" else None  # R-SHOT-NOT-PASS (v4.1): the team-mate must receive it
                npass = (directed_at_teammate(sp, i, player, dr["fit"], s_att, L, cfg, receiver=receiver)
                         if dr["direction"] != "direction_undetermined" else {"holds": False})
                # R-SHOT-POWER (v4.1): power confirms a shot except for headers (ball above aerial_min_height_m at the kick,
                # the same height that separates ground touches; the annotated body part only without ball height).
                z_kick = float(sp.bz[i])
                if np.isfinite(z_kick):
                    header = bool(z_kick > cfg.aerial_min_height_m)
                else:
                    a_k = json.loads(ev.iloc[k]["attrs_json"]) if isinstance(ev.iloc[k]["attrs_json"], str) else {}
                    header = str(a_k.get("TypeOfShot", "")).lower() == "head"
                evidence = bool(power is not None and power > p99 and not header)
                outcome = shot_outcome(sp, i, fl, player, gk_ids, cfg, goal_result)
                # R-SHOT-OUTCOME (v4.2): a flight away from the goal with an opponent within shot_block_at_kick_m of the ball
                # at the kick is a shot blocked at the foot (kick and block merged in one |a| peak): blocked, or saved
                # when that opponent is the goalkeeper (the R-SHOT-OUTCOME meaning of a goalkeeper touch).
                blocked_at_kick = False
                if outcome not in ("goal", "disallowed_goal", "pending") and dr["direction"] == "away_from_goal":
                    oc, od = opponent_at_kick(sp, i, player)
                    if oc is not None and od <= cfg.shot_block_at_kick_m:
                        outcome, blocked_at_kick = ("saved" if sp.pids[oc] in gk_ids else "blocked"), True
                tc, basis, reason = shot_check(True, outcome, bool(npass["holds"]), evidence, dr["direction"], blocked_at_kick)
                type_check[k], new_cols["type_check_basis"][k], new_cols["type_check_reason"][k] = tc, basis, reason
                new_cols["flight_end_kind"][k], new_cols["flight_end_frame"][k] = fl["end_kind"], int(sp.n[fl["end"]])
                new_cols["shot_outcome_observed"][k] = outcome
                new_cols["shot_goal_line_y_m"][k] = dr["y_line_m"]
                new_cols["shot_direction"][k] = dr["direction"]
                new_cols["shot_directed_at_teammate"][k] = bool(npass["holds"])
                new_cols["shot_power_kmh"][k] = power
                new_cols["shot_power_evidence"][k] = evidence
                new_cols["shot_wide_margin_m"][k] = margin
                new_cols["shot_blocked_at_kick"][k] = blocked_at_kick
                new_cols["shot_header"][k] = header
                if goal_row is not None:
                    new_cols["goal_check"][k] = goal_result
                    new_cols["shooter_mismatch"][k] = goal_row["shooter_mismatch"]
                    new_cols["goal_confirmed_by"][k] = goal_row.get("confirmed_by")
                    new_cols["own_goal"][k] = goal_row.get("own_goal")
                    new_cols["goal_scorer_id"][k] = goal_row.get("scorer_id")
            else:  # clearance
                fl = free_flight(sp, tt, i, player, cfg, (), ww_frames, shot=False)
                _next_touch_cols(new_cols, k, sp, fl["touch"], i)
                new_cols["flight_end_kind"][k], new_cols["flight_end_frame"][k] = fl["end_kind"], int(sp.n[fl["end"]])
                rg = sp.role_group[player] if sp.role_group is not None else None
                conds = clearance_conditions(sp, tt, i, player, fl, bool(new_cols["aerial"][k]), rg, L, cfg)
                tc, score_c = clearance_check(conds, cfg)
                met = [c for c in CLEARANCE_CONDITIONS if conds[c] is True]
                type_check[k] = tc
                new_cols["clearance_conditions"][k] = json.dumps(conds, sort_keys=True)
                new_cols["clearance_score"][k] = score_c
                new_cols["type_check_basis" if tc == "confirmed" else "type_check_reason"][k] = f"{score_c} of 7 conditions ({', '.join(met) or 'none'})"
    ev["sync_time_utc"], ev["sync_frame"], ev["sync_certainty"], ev["sync_method"], ev["sync_residual_s"], ev["type_check"] = (
        sync_time, sync_frame, sync_cert, sync_method, sync_res, type_check)
    for c, v in new_cols.items():
        ev[c] = v
    ev.attrs["sync_log"] = log
    ev.attrs["goal_crossings"] = crossings_out
    return ev


def _next_touch_cols(new_cols: dict, k: int, sp: SyncPeriod, nt: dict, i: int) -> None:
    new_cols["next_touch_kind"][k] = nt["kind"]
    new_cols["next_touch_frame"][k] = int(sp.n[nt["frame"]])
    new_cols["next_touch_after_s"][k] = round((nt["frame"] - i) / sp.fs, 3)
    if nt.get("col") is not None:
        new_cols["next_touch_object_id"][k] = sp.pids[nt["col"]]
        new_cols["next_touch_team_side"][k] = sp.team_side[nt["col"]]


# ---------------------------------------------------------------------------
# Aggregates for the report (no frame-level data)
# ---------------------------------------------------------------------------

def _nn(v):
    """NaN -> None so that reports and JSON never show 'nan'."""
    try:
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def _mad(s: pd.Series) -> float | None:
    v = s.dropna().to_numpy()
    return round(float(np.median(np.abs(v - np.median(v)))), 3) if len(v) else None


def _bands(c: pd.Series) -> dict:
    v = c.dropna().to_numpy()
    return {"high (>= 0.75)": int((v >= 0.75).sum()), "medium (0.5-0.75)": int(((v >= 0.5) & (v < 0.75)).sum()), "low (< 0.5)": int((v < 0.5).sum())}


def _counts(s: pd.Series) -> dict:
    return {str(k): int(v) for k, v in s.dropna().value_counts().sort_index().items()}


def _crosstab(a: pd.Series, b: pd.Series, missing_b: str = "none") -> dict:
    if not len(a):
        return {}
    t = pd.crosstab(a.fillna("none").astype(str), b.fillna(missing_b).astype(str))
    return {str(r): {str(c): int(t.loc[r, c]) for c in t.columns if t.loc[r, c]} for r in t.index}


def summarise(ev: pd.DataFrame, cfg: PrepConfig) -> dict:
    """Aggregate numbers for the report and the summary (no frame-level data)."""
    k = ev.loc[ev["event_kind"].notna()]
    loc = k.loc[k["sync_method"].isin(LOCATED_METHODS)]
    out: dict = {"by_kind_and_method": k.groupby(["event_kind", "sync_method"]).size().rename("n").reset_index().to_dict(orient="records")}
    out["located"] = {}
    for kind in ("pass", "shot", "clearance", "duel"):
        lk = loc.loc[loc["event_kind"] == kind]
        out["located"][kind] = {"events": int((k["event_kind"] == kind).sum()), "located": int(len(lk)), "by_method": _counts(lk["sync_method"]),
                                "certainty_p10_p50_p90": [round(float(q), 3) for q in lk["sync_certainty"].astype(float).quantile([0.1, 0.5, 0.9])] if len(lk) else None,
                                "residual_median_s": round(float(lk["sync_residual_s"].astype(float).median()), 3) if len(lk) else None,
                                "residual_mad_s": _mad(lk["sync_residual_s"].astype(float)) if len(lk) else None,
                                # R-LATE-ANNOTATION (v4.2): located after the search window was extended backwards
                                "located_in_extended_window": int(lk["sync_window_extended"].map(lambda v: v is True).sum()) if "sync_window_extended" in lk else 0}
    out["certainty_bands"] = {kind: _bands(loc.loc[loc["event_kind"] == kind, "sync_certainty"].astype(float)) for kind in ("pass", "shot", "clearance", "duel")}
    out["type_check"] = {kind: _counts(k.loc[k["event_kind"] == kind, "type_check"]) for kind in ("pass", "shot", "clearance")}
    out["type_check_basis"] = {kind: _counts(k.loc[k["event_kind"] == kind, "type_check_basis"]) for kind in ("pass", "shot")}
    out["not_confirmed_reasons"] = {kind: _counts(k.loc[(k["event_kind"] == kind) & (k["type_check"] == "not_confirmed"), "type_check_reason"])
                                    for kind in ("pass", "shot")}

    # Goals (R-GOAL). Annotated goals: open-play shots labelled goal plus set-piece shots (penalties, direct free
    # kicks) labelled goal; the set pieces are counted here but never enter the open-play shot statistics below.
    gc = ev.attrs.get("goal_crossings", [])
    labels = k.loc[k["event_kind"] == "shot", "shot_outcome_label"]
    shots_all = k.loc[k["event_kind"] == "shot"]
    goal_type: dict[str, str] = {str(e): "ShotAtGoal" for e in shots_all.loc[labels == "goal", "event_id"]}
    for e, t, st in zip(ev["event_id"], ev["event_type"], ev["sub_types"] if "sub_types" in ev else [None] * len(ev)):
        if set_piece_shot_label(t, st, cfg) == "goal":
            goal_type[str(e)] = str(t)
    goal_ids = set(goal_type)
    type_of = {str(e): str(t) for e, t in zip(ev["event_id"], ev["event_type"])}
    matched_goal_ids = {str(r["shot_event_id"]) for r in gc if r["goal_check"] == "goal" and r["shot_event_id"]}
    out["goals"] = {"crossings_by_result": _counts(pd.Series([r["goal_check"] for r in gc], dtype=object)),
                    "annotated_goals": len(goal_ids),
                    "annotated_goals_by_event_type": dict(sorted(Counter(goal_type.values()).items())),
                    "annotated_goals_without_goal_crossing": sorted(goal_ids - matched_goal_ids),
                    "goal_crossings_without_annotated_goal": sum(1 for r in gc if r["goal_check"] == "goal" and (not r["shot_event_id"] or r["annotated_label"] != "goal")),
                    "shooter_mismatches": sum(1 for r in gc if r["shooter_mismatch"]),
                    "goals_without_located_shot": sum(1 for r in gc if r["goal_check"] in ("goal", "disallowed_goal") and r["kick_frame"] is None),
                    # v4.2: how goals were confirmed (kickoff / end_of_period), crossings found over a provider freeze
                    # (R-GOAL-FREEZE), own goals (attribution with a deflection) and the reasons of disallowed goals.
                    "goals_by_confirmation": _counts(pd.Series([r.get("confirmed_by") for r in gc if r["goal_check"] == "goal"], dtype=object)),
                    "crossings_by_method": _counts(pd.Series([r.get("crossing_method", "observed") for r in gc], dtype=object)),
                    "own_goals": sum(1 for r in gc if r["goal_check"] == "goal" and r.get("own_goal")),
                    "disallowed_reasons": _counts(pd.Series([r.get("disallowed_reason") for r in gc if r["goal_check"] == "disallowed_goal"], dtype=object)),
                    "crossings": [{**{c: _nn(v) for c, v in r.items() if c != "index"},
                                   "shot_event_type": type_of.get(str(r["shot_event_id"])) if r["shot_event_id"] else None} for r in gc]}

    # Shots (R-SHOT-*): observed outcome vs annotated label, headers, soft kicks, kick-block pairs.
    s = shots_all
    head = s["attrs_json"].map(lambda a: str(json.loads(a).get("TypeOfShot", "")).lower() == "head" if isinstance(a, str) else False).astype(bool)
    sloc = s["sync_method"].isin(KICK_METHODS)
    aerial = s["aerial"].map(lambda v: v is True or v == True).astype(bool)  # noqa: E712
    block_fast = (s["shot_outcome_observed"] == "blocked") & (pd.to_numeric(s["next_touch_after_s"], errors="coerce") < cfg.kick_min_separation_s)
    out["shots"] = {
        "annotated": int(len(s)), "located": int(sloc.sum()), "by_method": _counts(s.loc[sloc, "sync_method"]),
        "located_by_kick_method": _counts(s.loc[sloc, "kick_method"]),
        "confirmed": int((s["type_check"] == "confirmed").sum()), "not_confirmed": int((s["type_check"] == "not_confirmed").sum()),
        "observed_outcome": _counts(s["shot_outcome_observed"]),
        "observed_vs_label": _crosstab(s["shot_outcome_label"], s["shot_outcome_observed"], "not located"),
        "headers": {"annotated": int(head.sum()), "located": int((head & sloc).sum()),
                    "located_by_aerial_signature": int((head & (s["kick_method"] == "aerial_signature")).sum()),
                    "located_aerial_frame": int((head & sloc & aerial).sum())},
        "soft_kicks_located": int((s["kick_method"] == "soft_kick_signature").sum()),
        "blocked_within_0_2_s": int(block_fast.sum()),
        "power_evidence": int(s["shot_power_evidence"].map(lambda v: v is True or v == True).astype(bool).sum()),  # noqa: E712
        "directed_at_teammate": int(s["shot_directed_at_teammate"].map(lambda v: v is True or v == True).astype(bool).sum()),  # noqa: E712
        "blocked_at_kick": int(s["shot_blocked_at_kick"].map(lambda v: v is True).sum()) if "shot_blocked_at_kick" in s else 0,
        "wide_margin_m": _counts(s["shot_wide_margin_m"]) if "shot_wide_margin_m" in s else {},
        "direction": _counts(s["shot_direction"]),
        "rows": [{c: _nn(v) for c, v in d.items()} for d in _shot_rows(s)],
    }

    # Passes (R-PASS-KICK, R-PASS-OUTCOME) vs the provider's own evaluation (diagnostic only).
    p = k.loc[k["event_kind"] == "pass"]
    ev_eval = p["attrs_json"].map(lambda a: json.loads(a).get("Evaluation") if isinstance(a, str) else None)
    out["passes"] = {"annotated": int(len(p)), "confirmed": int((p["type_check"] == "confirmed").sum()),
                     "by_method": _counts(p.loc[p["sync_method"].isin(KICK_METHODS), "sync_method"]),
                     "pass_outcome": _counts(p["pass_outcome"]), "outcome_vs_evaluation": _crosstab(ev_eval, p["pass_outcome"], "not located")}

    # Clearances (R-CLEARANCE): score distribution and how often each condition holds.
    c = k.loc[k["event_kind"] == "clearance"]
    conds = c["clearance_conditions"].dropna().map(json.loads)
    out["clearances"] = {"annotated": int(len(c)), "located": int(c["sync_method"].isin(KICK_METHODS).sum()),
                         "confirmed": int((c["type_check"] == "confirmed").sum()), "not_confirmed": int((c["type_check"] == "not_confirmed").sum()),
                         "score_distribution": _counts(c["clearance_score"].dropna().astype(int)),
                         "condition_met": {cc: int(sum(1 for d in conds if d.get(cc) is True)) for cc in CLEARANCE_CONDITIONS},
                         "not_confirmed_examples": [{"event_id": r["event_id"], "half": int(r["half"]), "reason": r["type_check_reason"]}
                                                    for _, r in c.loc[c["type_check"] == "not_confirmed"].sort_values(["half", "time_utc"], kind="mergesort").head(10).iterrows()]}
    out["sync_log"] = ev.attrs.get("sync_log", {})
    return out


def _shot_rows(shots: pd.DataFrame) -> list[dict]:
    return [{"event_id": r["event_id"], "half": int(r["half"]), "annotated_outcome": r["shot_outcome_label"], "type_check": r["type_check"],
             "basis": r["type_check_basis"], "reason": r["type_check_reason"], "sync_method": r["sync_method"], "kick_method": r["kick_method"],
             "observed_outcome": r["shot_outcome_observed"], "goal_line_y_m": r["shot_goal_line_y_m"], "power_kmh": r["shot_power_kmh"],
             "sync_certainty": r["sync_certainty"], "sync_residual_s": r["sync_residual_s"], "goal_distance_m": r["sync_goal_distance_m"],
             "zone": r["sync_zone"], "penalty_area": r["sync_penalty_area"], "aerial": r["aerial"],
             "header": str(json.loads(r["attrs_json"]).get("TypeOfShot", "")).lower() == "head" if isinstance(r["attrs_json"], str) else False}
            for _, r in shots.iterrows()]


def provider_sync_comparison(ev: pd.DataFrame, fs: float) -> dict:
    """Our synchronised frame vs the provider's ``CalculatedFrame`` (tests only; it decides nothing)."""
    if "provider_calculated_frame" not in ev:
        return {"supplied": False}
    m = ev["provider_calculated_frame"].notna() & ev["sync_frame"].notna()
    if not m.any():
        return {"supplied": bool(ev["provider_calculated_frame"].notna().any()), "n": 0}
    d = (ev.loc[m, "sync_frame"].astype(float) - ev.loc[m, "provider_calculated_frame"].astype(float)) / fs
    a = d.abs()
    out = {"supplied": True, "n": int(m.sum()), "by_type": ev.loc[m, "event_type"].value_counts().to_dict(),
           "ours_minus_provider_median_s": round(float(d.median()), 3), "abs_median_s": round(float(a.median()), 3),
           "within_0_2_s": int((a <= 0.2).sum()), "within_1_s": int((a <= 1.0).sum()), "abs_max_s": round(float(a.max()), 3)}
    # Header shots (tests only): how close the aerial location is to the provider's frame (section 19, point 5).
    if "kick_method" in ev:
        hm = m & (ev["event_type"] == "ShotAtGoal") & (ev["kick_method"] == "aerial_signature")
        if hm.any():
            out["aerial_shots"] = {"n": int(hm.sum()), "abs_median_s": round(float(a[hm].median()), 3), "within_0_2_s": int((a[hm] <= 0.2).sum())}
    return out
