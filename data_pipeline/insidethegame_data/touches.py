"""Ball touches located from trajectories (guidelines v4.2, section 9.5).

Every check here uses **live play only** (``R-LIVE-ONLY``): ball kinematics are computed
on live segments (split at dead frames and at live-play ball jumps), reach tests use
live frames, and every forward search ends at the first dead frame. The one exception
(v4.2): a live segment that ends with the ball going into a goal continues through the
in-goal samples (beyond the goal line, between the posts, under the bar), so that a touch
just before the line is not lost at the filter edge (``R-GOAL`` step 4).

* ``R-TOUCH`` ground touch: a local maximum of the ball's horizontal |a| (Savitzky-Golay,
  13 samples) of at least ``touch_acc_min_ms2`` in live play, with the ball at or below
  ``ground_touch_max_height_m`` at the peak (v4.2) and a player within ``touch_reach_m``
  within +-``touch_peak_tolerance_s``.
* ``R-TOUCH-NEAREST`` (v4.2): a touch is a kick of a player only if his closest approach to the
  ball within +-``touch_peak_tolerance_s`` of the peak (the tolerance of ``R-TOUCH``) is the
  smallest of all players or within the margin of it: ``touch_nearest_margin_m`` (0.3 m) for a
  ground touch, ``touch_nearest_margin_aerial_m`` (0.5 m) for an aerial touch (tracking measures
  the torso, not the head). It applies to every kick (passes, shots, clearances, goal shots) and,
  at the kick frame itself, to soft kicks (ground margin). The touch table also records the
  **contact frame** (the frame of that smallest distance), for diagnostics.
* ``R-AERIAL`` aerial touch: the ball above ``aerial_min_height_m`` (``header_shot_min_height_m``
  for shots, ``R-SHOT-HEADER``) on its **incoming path** (median height of the live samples from
  half a filter window before the frame to the frame, v4.1), a change of direction of at least
  ``aerial_dir_change_min_deg`` between the horizontal velocity half a filter window before and
  after the frame (both at least ``aerial_min_speed_ms``), a local |a| maximum of at least
  ``aerial_acc_min_ms2`` and a player within reach. Skipped without ball height.
* ``R-SHOT-SOFT`` soft kick (shots only): a local minimum of the ball-shooter distance
  (<= 2 m) after which the distance grows by more than ``soft_kick_away_m`` within
  ``soft_kick_window_s``, the next touch is not the shooter's and no ground or aerial touch
  of the shooter that is itself a kick (the ball moves away after it) lies within +-0.2 s.
* ``R-SHOT-SPLIT``: after a shot, touches from ``shot_touch_min_separation_s`` (0.08 s) are
  searched; everywhere else touches closer than 0.2 s are one touch.
* ``R-WOODWORK`` (v4.2): an |a| peak in live play with the ball's **path** (the segments between
  consecutive samples within +-0.2 s of the peak) within ``woodwork_distance_m`` of a post or of
  the crossbar of either goal; it is woodwork when the path is within ``woodwork_close_m`` of the
  frame and every player is at least ``woodwork_player_min_m`` away, or when the ball is closer to
  the frame than to any player by more than ``woodwork_margin_m`` (3-D player distance from a point
  ``player_reach_height_m`` above the player for balls higher than that). A candidate is the net,
  not woodwork, when the ball enters the goal between the posts within +-0.2 s of the peak.

A touch is a **kick** of a player (pass, shot, clearance) when he is within reach and owns the
touch (``R-TOUCH-NEAREST``), the ball then moves away from him by at least ``kick_away_min_m``
within ``kick_away_s``, and the next touch is not his own (otherwise it is a dribbling touch).

Nothing here reads provider speed or acceleration.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import PrepConfig
from .kinematics import savgol_kinematics


@dataclass
class TouchTable:
    """Touch frames of one period, in frame order (indices into the period arrays)."""

    frames: np.ndarray  # int
    ground: np.ndarray  # bool: R-TOUCH peak (|a| >= touch_acc_min_ms2, ball at or below ground_touch_max_height_m)
    aerial: np.ndarray  # bool: R-AERIAL touch (incoming-path height above aerial_min_height_m)
    z: np.ndarray  # ball height at the frame (NaN without height)
    col: np.ndarray  # nearest player within reach (-1: nobody within reach -> not a touch)
    dist: np.ndarray  # distance of that player (m): the smallest player-ball distance within +-0.2 s
    contact: np.ndarray | None = None  # R-TOUCH-NEAREST (v4.2): frame of that smallest distance (-1: no live frame)
    z_in: np.ndarray | None = None  # R-AERIAL (v4.1): ball height on the incoming path (NaN without height)

    @property
    def touch(self) -> np.ndarray:
        return self.col >= 0

    def height(self, j: int) -> float:
        """Incoming-path height of touch ``j`` (the single-sample height when the table was built without it)."""
        return float(self.z_in[j]) if self.z_in is not None else float(self.z[j])


def in_goal_mask(sp, L: float, cfg: PrepConfig) -> np.ndarray:
    """Ball samples inside either goal: beyond the goal line, between the posts and under the bar (ball radius
    included; only y without ball height)."""
    half_goal = cfg.goal_width_m / 2 + cfg.ball_radius_m
    bar = cfg.crossbar_height_m + cfg.ball_radius_m
    with np.errstate(invalid="ignore"):
        m = np.isfinite(sp.bx) & np.isfinite(sp.by) & (np.abs(sp.bx) >= L / 2) & (np.abs(sp.by) <= half_goal)
        if np.isfinite(sp.bz).any():
            m &= ~(np.isfinite(sp.bz) & (sp.bz > bar))
    return m


def goal_extension(sp, live: np.ndarray, L: float, cfg: PrepConfig) -> np.ndarray:
    """``R-LIVE-ONLY`` exception (v4.2, ``R-GOAL`` step 4): the in-goal samples that directly follow a live segment,
    up to the first sample that leaves the goal or is frozen (horizontal position held by the provider)."""
    ext = np.zeros(len(sp.n), dtype=bool)
    ing = in_goal_mask(sp, L, cfg)
    for i in np.flatnonzero(live[:-1] & ~live[1:]) + 1:  # first non-live sample after a live one
        j = int(i)
        while j < len(sp.n) and ing[j] and not live[j] and not (sp.bx[j] == sp.bx[j - 1] and sp.by[j] == sp.by[j - 1]):
            ext[j] = True
            j += 1
    return ext


def ball_kinematics(sp, cfg: PrepConfig, L: float | None = None) -> dict[str, np.ndarray]:
    """Ball velocity and horizontal |a| (13-sample Savitzky-Golay) on live segments, split at dead frames and at
    live-play position jumps (``R-LIVE-ONLY``: NaN outside live play). With the pitch length ``L``, a live segment
    that ends with the ball going into a goal continues through the in-goal samples (``goal_extension``, v4.2)."""
    nf = len(sp.n)
    out = {k: np.full(nf, np.nan) for k in ("vx", "vy", "acc")}
    live = (sp.bstatus == 1) & np.isfinite(sp.bx) & np.isfinite(sp.by)
    usable = live | goal_extension(sp, live, L, cfg) if L is not None else live
    step = np.r_[0.0, np.hypot(np.diff(sp.bx), np.diff(sp.by))]
    jump = np.nan_to_num(step) > cfg.ball_speed_max_ms / sp.fs
    brk = ~usable | jump | np.r_[True, ~usable[:-1]]
    w = cfg.savgol_window(sp.fs)
    starts = np.flatnonzero(usable & brk)
    stops = np.r_[np.flatnonzero(brk | ~usable), nf]  # a segment ends just before the next break or unusable frame
    for s in starts:
        e = int(stops[np.searchsorted(stops, s, side="right")])  # end-exclusive
        k = savgol_kinematics(sp.bx[s:e], sp.by[s:e], sp.fs, w, cfg.savgol_polyorder)
        out["vx"][s:e], out["vy"][s:e], out["acc"][s:e] = k["vx"], k["vy"], k["acc"]
    out["live"] = live
    out["in_goal_extension"] = usable & ~live
    return out


def incoming_height(sp, i: int, cfg: PrepConfig) -> float:
    """``R-AERIAL`` (v4.1): the ball height on the incoming path to frame ``i``: the median of the finite heights of the
    live samples from half a filter window before ``i`` to ``i`` (7 samples at 25 Hz). NaN without height. A single
    wrong sample (for example a height reset to 0 between airborne samples) cannot decide the height."""
    half = (cfg.savgol_window(sp.fs) - 1) // 2
    lo = max(0, i - half)
    z = sp.bz[lo:i + 1][(sp.bstatus[lo:i + 1] == 1)]
    z = z[np.isfinite(z)]
    return float(np.median(z)) if len(z) else float("nan")


def local_maxima(acc: np.ndarray, threshold: float) -> np.ndarray:
    """Indices of local maxima of ``acc`` at or above ``threshold`` (plateaus give their first frame)."""
    a = np.nan_to_num(acc, nan=-np.inf)
    left = np.r_[-np.inf, a[:-1]]
    right = np.r_[a[1:], -np.inf]
    return np.flatnonzero((a >= threshold) & (a > left) & (a >= right))


def touch_peaks(acc: np.ndarray, cfg: PrepConfig) -> np.ndarray:
    """``R-TOUCH``: indices of local maxima of the ball's |a| at or above ``touch_acc_min_ms2``."""
    return local_maxima(acc, cfg.touch_acc_min_ms2)


def _window(sp, i: int, cfg: PrepConfig, lo_bound: int = 0) -> tuple[int, int, np.ndarray]:
    tol = cfg.frames(cfg.touch_peak_tolerance_s, sp.fs)
    lo, hi = max(0, lo_bound, i - tol), min(len(sp.n) - 1, i + tol)
    return lo, hi, sp.bstatus[lo:hi + 1] == 1


def player_reach(sp, col: int, i: int, cfg: PrepConfig) -> float:
    """Smallest player-ball distance of ``col`` over the live frames within +-0.2 s of ``i`` (inf if none)."""
    lo, hi, live = _window(sp, i, cfg)
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[lo:hi + 1, col] - sp.bx[lo:hi + 1], sp.py[lo:hi + 1, col] - sp.by[lo:hi + 1])
    d = np.where(live & np.isfinite(d), d, np.inf)
    return float(d.min()) if len(d) else np.inf


def _window_distances(sp, i: int, cfg: PrepConfig, cols=None, lo_bound: int = 0) -> tuple[int, np.ndarray]:
    """(first frame, distances) of every player to the ball over the live frames within +-0.2 s of ``i`` (not before
    ``lo_bound``); inf where not live, not finite, or outside ``cols``."""
    lo, hi, live = _window(sp, i, cfg, lo_bound)
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[lo:hi + 1] - sp.bx[lo:hi + 1, None], sp.py[lo:hi + 1] - sp.by[lo:hi + 1, None])
    d = np.where(live[:, None] & np.isfinite(d), d, np.inf)
    if cols is not None:
        keep = np.zeros(d.shape[1], dtype=bool)
        keep[list(cols)] = True
        d[:, ~keep] = np.inf
    return lo, d


def nearest_contact(sp, i: int, cfg: PrepConfig, cols=None, lo_bound: int = 0) -> tuple[int | None, float, int]:
    """(player, distance, contact frame): the player closest to the ball around frame ``i`` (minimum over the live
    frames within +-0.2 s, not before ``lo_bound``), whether or not within reach, and the frame of that minimum
    (``R-TOUCH-NEAREST``: the contact frame; the earliest at equal distances). (None, inf, -1) without a live frame."""
    lo, d = _window_distances(sp, i, cfg, cols, lo_bound)
    if not np.isfinite(d).any():
        return None, np.inf, -1
    dmin = d.min(axis=0)
    c = int(np.argmin(dmin))
    return c, float(dmin[c]), lo + int(np.flatnonzero(d[:, c] == dmin[c])[0])


def nearest_player(sp, i: int, cfg: PrepConfig, cols=None, lo_bound: int = 0) -> tuple[int | None, float]:
    """Player closest to the ball around frame ``i`` (min over +-0.2 s, live frames, not before ``lo_bound``), if within
    reach. ``cols``: restrict the search to these columns."""
    c, d, _ = nearest_contact(sp, i, cfg, cols, lo_bound)
    if c is None:
        return None, np.inf
    return (c, d) if d <= cfg.touch_reach_m else (None, d)


def owns_touch(sp, tt: "TouchTable", j: int, col: int, cfg: PrepConfig, aerial: bool = False) -> bool:
    """``R-TOUCH-NEAREST`` (v4.2): the player ``col`` owns touch ``j`` when his closest approach to the ball within +-0.2 s
    of the peak (the tolerance of ``R-TOUCH``) is the smallest of all players (``tt.dist``) or within the margin of it:
    ``touch_nearest_margin_m`` (0.3 m) for a ground touch, ``touch_nearest_margin_aerial_m`` (0.5 m) for an aerial touch
    (``aerial``: tracking measures the torso, not the head). Comparing at a single contact frame instead was tested on
    the six DFL matches: it lost 3.2 points of located passes and found no extra shot (guidelines section 9.5)."""
    margin = cfg.touch_nearest_margin_aerial_m if aerial else cfg.touch_nearest_margin_m
    return bool(player_reach(sp, col, int(tt.frames[j]), cfg) <= float(tt.dist[j]) + margin)


def nearest_at_frame(sp, i: int, col: int, cfg: PrepConfig) -> bool:
    """``R-TOUCH-NEAREST`` at a single frame (soft kicks): ``col`` is the nearest player to the ball at ``i`` or within
    ``touch_nearest_margin_m`` of the nearest."""
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[i] - sp.bx[i], sp.py[i] - sp.by[i])
    d = np.where(np.isfinite(d), d, np.inf)
    return bool(np.isfinite(d[col]) and d[col] <= float(d.min()) + cfg.touch_nearest_margin_m)


def aerial_frames(sp, bk: dict, cfg: PrepConfig, min_height: float | None = None) -> np.ndarray:
    """``R-AERIAL``: aerial touch frames (before the reach test). Empty without ball height."""
    min_height = cfg.aerial_min_height_m if min_height is None else min_height
    if not np.isfinite(sp.bz).any():
        return np.array([], dtype=int)
    cand = local_maxima(bk["acc"], cfg.aerial_acc_min_ms2)
    half = (cfg.savgol_window(sp.fs) - 1) // 2  # the two velocity estimates must not share the impulse
    nf = len(sp.n)
    out = []
    for i in cand:
        if sp.bstatus[i] != 1 or i - half < 0 or i + half >= nf:
            continue
        z_in = incoming_height(sp, int(i), cfg)  # v4.1: the incoming path, not a single sample
        if not np.isfinite(z_in) or z_in <= min_height:
            continue
        v0 = np.array([bk["vx"][i - half], bk["vy"][i - half]])
        v1 = np.array([bk["vx"][i + half], bk["vy"][i + half]])
        if not (np.isfinite(v0).all() and np.isfinite(v1).all()):
            continue
        s0, s1 = float(np.hypot(*v0)), float(np.hypot(*v1))
        if s0 < cfg.aerial_min_speed_ms or s1 < cfg.aerial_min_speed_ms:
            continue
        ang = np.degrees(np.arccos(np.clip(float(v0 @ v1) / (s0 * s1), -1.0, 1.0)))
        if ang >= cfg.aerial_dir_change_min_deg:
            out.append(int(i))
    return np.array(out, dtype=int)


def touch_table(sp, bk: dict, cfg: PrepConfig) -> TouchTable:
    """Touch frames (live |a| peaks and aerial touches) with the nearest player within reach (``-1`` when nobody is), the
    contact frame (``R-TOUCH-NEAREST``) and the incoming-path height. ``ground``: the peaks with the ball at or below
    ``ground_touch_max_height_m`` at the peak (v4.2; every live peak without ball height). The incoming-path height is
    for aerial touches only: a volley of a dropping ball is struck low although the ball arrives from above. A higher
    peak stays a touch (a goalkeeper catching a high ball, a chest control: it ends a flight and can be a next touch) but
    is never a ground kick; it can be a kick only as an aerial touch (``R-AERIAL``)."""
    peaks = touch_peaks(bk["acc"], cfg)
    peaks = peaks[sp.bstatus[peaks] == 1] if len(peaks) else peaks  # R-LIVE-ONLY (the in-goal samples carry kinematics)
    z_peak = sp.bz[peaks] if len(peaks) else np.array([])
    g = peaks[~(np.isfinite(z_peak) & (z_peak > cfg.ground_touch_max_height_m))] if len(peaks) else peaks
    a = aerial_frames(sp, bk, cfg)
    frames = np.union1d(peaks, a).astype(int)
    col = np.full(len(frames), -1, dtype=int)
    dist = np.full(len(frames), np.inf)
    contact = np.full(len(frames), -1, dtype=int)
    z_in = np.array([incoming_height(sp, int(i), cfg) for i in frames]) if len(frames) else np.array([])
    for j, i in enumerate(frames):
        c, d, jc = nearest_contact(sp, int(i), cfg)
        dist[j], contact[j] = d, jc
        if c is not None and d <= cfg.touch_reach_m:
            col[j] = c
    return TouchTable(frames=frames, ground=np.isin(frames, g), aerial=np.isin(frames, a),
                      z=sp.bz[frames] if len(frames) else np.array([]), col=col, dist=dist, contact=contact, z_in=z_in)


def _first_dead_after(sp, k: int, end: int) -> int | None:
    dead = np.flatnonzero(sp.bstatus[k + 1:end + 1] != 1)
    return k + 1 + int(dead[0]) if len(dead) else None


def next_touch(sp, tt: TouchTable, k: int, kicker: int | None, cfg: PrepConfig, sep: int | None = None, shot: bool = False) -> dict:
    """First touch after the kick at frame ``k`` (``R-LIVE-ONLY``): a ground or aerial touch with a player within
    reach, from ``sep`` frames after the kick (0.2 s; 0.08 s after a shot, ``R-SHOT-SPLIT``), skipping the kicker's
    own touches within ``kick_away_s``. Stops at the first dead frame (``out_of_play``) or after
    ``next_touch_max_s`` (``none``). In live mode (``sp.complete`` False) a search that runs into the last released
    frame without an answer is ``pending`` (``R-LIVE-BUFFER``).

    ``shot`` (``R-SHOT-SPLIT``): a block 1-2 m from the shooter comes about 0.1 s after the kick, while the shooter
    is still within reach of the ball in the +-0.2 s reach window. Within ``kick_away_s`` the shooter cannot be the
    toucher, so such a touch goes to the nearest **other** player within reach (frames after the kick only)."""
    sep = cfg.frames(cfg.kick_min_separation_s, sp.fs) if sep is None else sep
    away = cfg.frames(cfg.kick_away_s, sp.fs)
    last = len(sp.n) - 1
    end = min(last, k + cfg.frames(cfg.next_touch_max_s, sp.fs))
    dead_i = _first_dead_after(sp, k, end)
    stop = dead_i if dead_i is not None else end
    sel = np.flatnonzero((tt.frames >= k + sep) & (tt.frames <= stop) & tt.touch)
    for j in sel:
        p, c, d = int(tt.frames[j]), int(tt.col[j]), float(tt.dist[j])
        if kicker is not None and c == kicker and p - k <= away:
            if not shot:
                continue
            c, d = nearest_player(sp, p, cfg, cols=[x for x in range(len(sp.pids)) if x != kicker], lo_bound=k + 1)
            if c is None:
                continue
        return {"kind": "touch", "frame": p, "col": c, "dist_m": d}
    if dead_i is not None:
        return {"kind": "out_of_play", "frame": dead_i, "col": None}
    if not getattr(sp, "complete", True) and end >= last and k + cfg.frames(cfg.next_touch_max_s, sp.fs) > last:
        return {"kind": "pending", "frame": last, "col": None}
    return {"kind": "none", "frame": end, "col": None}


def moves_away(sp, col: int, i: int, dmin: float, cfg: PrepConfig) -> bool:
    """The ball moves away from player ``col`` by at least ``kick_away_min_m`` within ``kick_away_s`` (live frames,
    stopping at the first dead frame)."""
    away = cfg.frames(cfg.kick_away_s, sp.fs)
    hi = min(len(sp.n) - 1, i + away)
    dead_i = _first_dead_after(sp, i, hi)
    if dead_i is not None:
        hi = dead_i - 1
    if hi <= i:
        return False
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[i + 1:hi + 1, col] - sp.bx[i + 1:hi + 1], sp.py[i + 1:hi + 1, col] - sp.by[i + 1:hi + 1])
    return bool(np.isfinite(d).any() and float(np.nanmax(d)) - dmin >= cfg.kick_away_min_m)


def kick_frames(sp, tt: TouchTable, col: int, lo: int, hi: int, cfg: PrepConfig, shot: bool = False, kinds=("ground", "aerial")) -> list[tuple[int, float, str]]:
    """Kicks of player ``col`` at ground or aerial touch frames in [lo, hi]: (frame, distance, method).

    Aerial touches need ``aerial_min_height_m`` (passes, clearances) or ``header_shot_min_height_m`` (shots) on the
    incoming path. A frame that is a ground touch is reported as ``kick_signature``; an aerial-only one as
    ``aerial_signature``. ``R-TOUCH-NEAREST``: the player must own the touch at its contact frame.
    """
    min_h = cfg.header_shot_min_height_m if shot else cfg.aerial_min_height_m
    sep = cfg.frames(cfg.shot_touch_min_separation_s if shot else cfg.kick_min_separation_s, sp.fs)
    out = []
    for j in np.flatnonzero((tt.frames >= lo) & (tt.frames <= hi)):
        i = int(tt.frames[j])
        is_ground = bool(tt.ground[j]) and "ground" in kinds
        h = tt.height(j)
        is_aerial = bool(tt.aerial[j]) and "aerial" in kinds and np.isfinite(h) and h > min_h
        if not (is_ground or is_aerial) or sp.bstatus[i] != 1:
            continue
        dmin = player_reach(sp, col, i, cfg)
        if dmin > cfg.touch_reach_m or not owns_touch(sp, tt, int(j), col, cfg, aerial=not is_ground) or not moves_away(sp, col, i, dmin, cfg):
            continue
        if next_touch(sp, tt, i, col, cfg, sep, shot=shot)["col"] == col:
            continue  # a dribbling touch: the kick comes later
        out.append((i, dmin, "kick_signature" if is_ground else "aerial_signature"))
    return out


def soft_kicks(sp, tt: TouchTable, col: int, lo: int, hi: int, cfg: PrepConfig) -> list[tuple[int, float, str]]:
    """``R-SHOT-SOFT``: soft kicks of the shooter ``col`` in [lo, hi]: (frame, distance, ``soft_kick_signature``)."""
    nf = len(sp.n)
    grow = cfg.frames(cfg.soft_kick_window_s, sp.fs)
    tol = cfg.frames(cfg.touch_peak_tolerance_s, sp.fs)
    sep = cfg.frames(cfg.shot_touch_min_separation_s, sp.fs)
    a, b = max(1, lo), min(nf - 2, hi)
    if b < a:
        return []
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[a - 1:b + 2 + grow, col] - sp.bx[a - 1:b + 2 + grow], sp.py[a - 1:b + 2 + grow, col] - sp.by[a - 1:b + 2 + grow])
    live = sp.bstatus[a - 1:b + 2 + grow] == 1
    d = np.where(live & np.isfinite(d), d, np.nan)
    out = []
    own_touch = [int(f) for f, c in zip(tt.frames, tt.col) if c >= 0 and lo - tol <= f <= hi + tol]
    for i in range(a, b + 1):
        j = i - (a - 1)
        di = d[j]
        if not np.isfinite(di) or di > cfg.touch_reach_m:
            continue
        left, right = d[j - 1], d[j + 1] if j + 1 < len(d) else np.nan
        if not (np.isfinite(left) and di < left and (not np.isfinite(right) or di <= right)):
            continue
        if not nearest_at_frame(sp, i, col, cfg):  # R-TOUCH-NEAREST (v4.2)
            continue
        # The distance grows by more than soft_kick_away_m within soft_kick_window_s (live frames only).
        nxt = d[j + 1:j + 1 + grow]
        stop = np.flatnonzero(~np.isfinite(nxt))
        nxt = nxt[: stop[0]] if len(stop) else nxt
        if not len(nxt) or float(np.max(nxt)) - di <= cfg.soft_kick_away_m:
            continue
        # No ground or aerial touch of the shooter within +-0.2 s that is itself a kick (a regular kick already explains
        # it). v4.2: a touch after which the ball cannot move away from him (for example a filter-edge peak on the frame
        # where the ball crosses the goal line and the provider freezes it) explains nothing and does not veto.
        if any(abs(f - i) <= tol and (rf := player_reach(sp, col, f, cfg)) <= cfg.touch_reach_m and moves_away(sp, col, f, rf, cfg) for f in own_touch):
            continue
        if next_touch(sp, tt, i, col, cfg, sep, shot=True)["col"] == col:
            continue
        out.append((i, float(di), "soft_kick_signature"))
    return out


def segment_distance(p1, q1, p2, q2) -> tuple[float, float]:
    """Smallest distance between the segments p1-q1 and p2-q2 (any dimension) and the parameter s in [0, 1] of the
    closest point on the first segment (closest points of two segments, Ericson 2005, section 5.1.9)."""
    p1, q1, p2, q2 = (np.asarray(v, float) for v in (p1, q1, p2, q2))
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = float(d1 @ d1), float(d2 @ d2), float(d2 @ r)
    eps = 1e-12
    if a <= eps and e <= eps:
        return float(np.linalg.norm(r)), 0.0
    if a <= eps:
        s, t = 0.0, min(max(f / e, 0.0), 1.0)
    else:
        c = float(d1 @ r)
        if e <= eps:
            s, t = min(max(-c / a, 0.0), 1.0), 0.0
        else:
            b = float(d1 @ d2)
            den = a * e - b * b
            s = min(max((b * f - c * e) / den, 0.0), 1.0) if den > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                s, t = min(max(-c / a, 0.0), 1.0), 0.0
            elif t > 1.0:
                s, t = min(max((b - c) / a, 0.0), 1.0), 1.0
    return float(np.linalg.norm((p1 + d1 * s) - (p2 + d2 * t))), float(s)


def _frame_distance(p, q, L: float, cfg: PrepConfig, use_z: bool) -> tuple[float, str | None, float, float]:
    """(distance, part, goal x sign, s) from the ball path p-q to the nearest post or crossbar of either goal. Posts are
    vertical segments 0 <= z <= crossbar; the crossbar joins the posts at the crossbar height. Without height the posts
    are points (2-D) and the crossbar is not tested."""
    gw, cb = cfg.goal_width_m / 2, cfg.crossbar_height_m
    best = (np.inf, None, 0.0, 0.0)
    for gx in (-L / 2, L / 2):
        for py in (-gw, gw):
            if use_z:
                d, s = segment_distance(p, q, (gx, py, 0.0), (gx, py, cb))
            else:
                d, s = segment_distance(p[:2], q[:2], (gx, py), (gx, py))
            if d < best[0]:
                best = (d, "post", 1.0 if gx > 0 else -1.0, s)
        if use_z:
            d, s = segment_distance(p, q, (gx, -gw, cb), (gx, gw, cb))
            if d < best[0]:
                best = (d, "crossbar", 1.0 if gx > 0 else -1.0, s)
    return best


def bounce_point(sp, j: int, use_z: bool, max_steps: float = 2.5) -> np.ndarray | None:
    """``R-WOODWORK`` (v4.2): where the ball changed direction between samples j and j + 1. The incoming line runs through
    samples j - 1 and j, the outgoing line through j + 2 and j + 1; their horizontal intersection is the contact when it
    lies ahead of j on the incoming line and behind j + 1 on the outgoing line, within ``max_steps`` steps in all (at
    80 km/h the ball moves almost 1 m between two samples, so the post itself is rarely sampled). z: on the incoming
    line. None when the lines are (nearly) parallel or the point is not between the samples."""
    if j < 1 or j + 2 >= len(sp.n):
        return None
    idx = [j - 1, j, j + 1, j + 2]
    if not (np.isfinite(sp.bx[idx]).all() and np.isfinite(sp.by[idx]).all()):
        return None
    a = np.array([sp.bx[j], sp.by[j]])
    u = a - np.array([sp.bx[j - 1], sp.by[j - 1]])
    b = np.array([sp.bx[j + 1], sp.by[j + 1]])
    w = b - np.array([sp.bx[j + 2], sp.by[j + 2]])
    nu, nw = float(np.hypot(*u)), float(np.hypot(*w))
    det = -u[0] * w[1] + u[1] * w[0]  # = -|u||w| sin(angle between the two lines)
    if nu < 1e-6 or nw < 1e-6 or abs(det) < 1e-6 * nu * nw:
        return None
    rhs = b - a
    s = (-rhs[0] * w[1] + rhs[1] * w[0]) / det
    t = (u[0] * rhs[1] - u[1] * rhs[0]) / det
    if s < 0 or t < 0 or s + t > max_steps:
        return None
    c = a + s * u
    z = float(sp.bz[j] + s * (sp.bz[j] - sp.bz[j - 1])) if use_z and np.isfinite(sp.bz[[j - 1, j]]).all() else 0.0
    return np.array([c[0], c[1], max(z, 0.0)])


def player_distance_3d(sp, f: int, cfg: PrepConfig) -> np.ndarray:
    """``R-WOODWORK`` (v4.2): distance of every player to the ball at frame ``f``; for a ball higher than
    ``player_reach_height_m``, 3-D from a point at that height above the player's position (a standing player's reach)."""
    with np.errstate(invalid="ignore"):
        d = np.hypot(sp.px[f] - sp.bx[f], sp.py[f] - sp.by[f])
    z = sp.bz[f]
    if np.isfinite(z) and z > cfg.player_reach_height_m:
        d = np.sqrt(d ** 2 + (z - cfg.player_reach_height_m) ** 2)
    return np.where(np.isfinite(d), d, np.inf)


def woodwork_frames(sp, tt: TouchTable, L: float, cfg: PrepConfig, bk: dict | None = None, crossing_frames=None) -> list[dict]:
    """``R-WOODWORK`` (v4.2). Candidates: |a| peaks (>= ``touch_acc_min_ms2``, any height) in live play whose ball **path**
    (the segments between consecutive samples within +-0.2 s of the peak, starting in live play, without position
    jumps) comes within ``woodwork_distance_m`` of a post or the crossbar. A candidate is woodwork when (i) the path is
    within ``woodwork_close_m`` of the frame and every player is at least ``woodwork_player_min_m`` from the ball, or
    (ii) the ball is closer to the frame than to any player by more than ``woodwork_margin_m``. Player distances: the
    minimum over the live frames within +-0.2 s of the contact sample (the path sample nearest to the frame), 3-D above
    ``player_reach_height_m``. ``tt`` is kept for the call signature; the peaks come from ``bk`` (computed if None).

    **The net is not the woodwork (v4.2):** a candidate is dropped when the ball enters the net within +-0.2 s of the peak:
    a crossing of ``R-GOAL`` step 1 (between the posts, observed or extrapolated) after which the ball goes dead within
    5 s (step 2, not ``play_continued``; ``crossing_frames``, computed when None). The |a| peak is then the ball stopping
    in the net, spread by the filter, or a contact on the way in, which is a goal and never a woodwork hit
    (``R-WOODWORK-STAT``). A ball that grazes the line within the ball radius and comes back into play is not in the net."""
    if bk is None:
        bk = ball_kinematics(sp, cfg, L)
    if crossing_frames is None:
        from .goals import ball_goes_dead, goal_crossings  # goals imports this module: imported here

        crossing_frames = [c["index"] for c in goal_crossings(sp, L, cfg, bk) if ball_goes_dead(sp, c["index"], cfg) != "play_continued"]
    crossing_frames = np.asarray(sorted(crossing_frames), dtype=int)
    nf = len(sp.n)
    tol = cfg.frames(cfg.touch_peak_tolerance_s, sp.fs)
    jump = cfg.ball_speed_max_ms / sp.fs
    has_z = bool(np.isfinite(sp.bz).any())
    out = []
    for i in touch_peaks(bk["acc"], cfg):
        i = int(i)
        if sp.bstatus[i] != 1:
            continue
        if len(crossing_frames) and (np.abs(crossing_frames - i) <= tol).any():
            continue  # the net, not the woodwork
        lo_w, hi_w = max(0, i - tol), min(nf - 1, i + tol)
        with np.errstate(invalid="ignore"):
            # A bounce point can lie up to 2.5 steps beyond the samples (bounce_point): skip only peaks whose samples are all
            # further from both goal lines than that plus the candidate distance.
            steps = np.hypot(np.diff(sp.bx[max(0, lo_w - 1):hi_w + 3]), np.diff(sp.by[max(0, lo_w - 1):hi_w + 3]))
            reach = cfg.woodwork_distance_m + 2.5 * float(np.nanmax(np.minimum(steps, jump))) if np.isfinite(steps).any() else cfg.woodwork_distance_m
            if not (np.abs(sp.bx[lo_w:hi_w + 1]) >= L / 2 - reach).any():
                continue
        best = (np.inf, None, 0.0, -1)
        for j in range(lo_w, hi_w):
            if sp.bstatus[j] != 1 or not (np.isfinite(sp.bx[j:j + 2]).all() and np.isfinite(sp.by[j:j + 2]).all()):
                continue
            if np.hypot(sp.bx[j + 1] - sp.bx[j], sp.by[j + 1] - sp.by[j]) > jump:
                continue
            use_z = has_z and bool(np.isfinite(sp.bz[j:j + 2]).all())
            zz = (sp.bz[j], sp.bz[j + 1]) if use_z else (0.0, 0.0)
            d, part, gs, s = _frame_distance(np.array([sp.bx[j], sp.by[j], zz[0]]), np.array([sp.bx[j + 1], sp.by[j + 1], zz[1]]), L, cfg, use_z)
            if d < best[0]:
                best = (d, part, gs, j if s <= 0.5 else j + 1)
            # A bounce between the two samples: the path goes to the contact point and back (incoming and outgoing lines).
            if 0 < j and j + 2 < nf and (sp.bstatus[[j - 1, j + 1, j + 2]] == 1).all() and \
                    np.hypot(sp.bx[j] - sp.bx[j - 1], sp.by[j] - sp.by[j - 1]) <= jump and np.hypot(sp.bx[j + 2] - sp.bx[j + 1], sp.by[j + 2] - sp.by[j + 1]) <= jump:
                c = bounce_point(sp, j, use_z)
                if c is not None:
                    d, part, gs, _ = _frame_distance(c, c, L, cfg, use_z)
                    if d < best[0]:
                        best = (d, part, gs, j)
        d_frame, part, gs, c = best
        if d_frame > cfg.woodwork_distance_m:
            continue
        lo, hi = max(0, c - tol), min(nf - 1, c + tol)
        d_play = min((float(player_distance_3d(sp, f, cfg).min()) for f in range(lo, hi + 1) if sp.bstatus[f] == 1), default=np.inf)
        clear = d_frame <= cfg.woodwork_close_m and d_play >= cfg.woodwork_player_min_m
        closer = d_play - d_frame > cfg.woodwork_margin_m
        if clear or closer:
            out.append({"frame": i, "contact_frame": int(c), "distance_m": round(float(d_frame), 3), "part": part, "goal_x_sign": gs,
                        "player_distance_m": None if not np.isfinite(d_play) else round(d_play, 3),
                        "decided_by": "clear_of_players" if clear else "closer_to_frame"})
    return out
