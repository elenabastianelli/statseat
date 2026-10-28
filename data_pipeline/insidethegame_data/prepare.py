"""Data preparation: handle, don't warn (guidelines v4, sections 5, 6 and 7).

Input: one period as frames x players arrays in the **canonical** reference system
(centre origin, metres, home attacking +x in the first period). Output: cleaned
positions, the validity mask (``valid_kinematics`` + ``exclusion_reason``),
gap flags (``estimated`` / ``discarded``), our Savitzky-Golay kinematics with the
acceleration status (``acc_status``), the distance counted per sample, the per-rule
log and the diagnostic catalogue.

Order of operations (section 6.4, steps 2-13): edges and parking values, swaps
(``R-SWAP``, ``R-SWAP-RESTORE``), gaps (``R-EDGE``, ``R-PARKING``, ``R-FROZEN``,
``R-MISSING``, ``R-EXIT-REENTRY``, ``R-GAP``), substitutions (``R-SUB-INHERIT``),
jumps (``R-JUMP``, ``R-UNKNOWN``), overlaps (``R-OVERLAP-CROWD``), kinematics on
continuous segments (``R-SG-SPEED``, ``R-SG-ACC``), the speed limit
(``R-SPEED-LIMIT``), the mask, the acceleration band (``R-ACC-BAND``, never an
exclusion), distance, then the ball (``R-BALL-DEAD``, ``R-BALL-WINDOW``,
``R-BALL-LIMIT``). Provider speed/acceleration/step are carried along for the
provider comparison only and are never read by any decision in this module.

Exclusion reasons follow the priority list of section 6.1: when several rules
exclude the same sample, the first one in the list is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import PrepConfig
from .edges import discover_parking_values, edge_mask, parking_mask
from .gaps import estimate_gap
from .kinematics import savgol_kinematics
from .segments import run_lengths

# Priority list of section 6.1 (index = code, lower code wins). Ball samples can also be dead_ball or restart.
REASONS = ["valid", "outside_tracking_area", "parked", "frozen", "missing", "jump", "swap", "substitution", "exit", "reentry",
           "crowding", "overlap", "limit", "unknown", "dead_ball", "restart"]
CODE = {r: i for i, r in enumerate(REASONS)}
BALL_ORDER = ["dead_ball", "restart", "frozen", "jump", "limit"]
# R-ACC-BAND (section 7.2): status of each player sample's acceleration.
ACC_STATUS = ["none", "measured", "noise", "approx"]
ACC_CODE = {r: i for i, r in enumerate(ACC_STATUS)}


@dataclass
class HalfData:
    half: int
    fs: float
    n: np.ndarray
    t_s: np.ndarray
    pids: list[str]
    team_side: list[str]
    x: np.ndarray  # (frames, players), canonical metres, NaN outside the track
    y: np.ndarray
    prov_s: np.ndarray  # tests only
    prov_d: np.ndarray
    prov_a: np.ndarray
    bx: np.ndarray
    by: np.ndarray
    bz: np.ndarray
    bstatus: np.ndarray  # 1 live, 0 dead, NaN unknown (provider)
    bposs: np.ndarray  # 1 home, 2 away (provider)
    ball_prov_s: np.ndarray


@dataclass
class HalfResult:
    half: int
    x: np.ndarray  # cleaned (swap-corrected) positions, gap samples as measured
    y: np.ndarray
    x_est: np.ndarray  # positions with estimated gaps filled (display / distance)
    y_est: np.ndarray
    present: np.ndarray
    reason: np.ndarray  # int8 codes into REASONS
    estimated: np.ndarray
    discarded: np.ndarray
    speed: np.ndarray
    acc_tan: np.ndarray
    valid: np.ndarray
    dist: np.ndarray  # metres counted at each sample (valid speed x dT + estimated path)
    ball_reason: np.ndarray
    ball_speed: np.ndarray
    ball_valid: np.ndarray
    prov_s: np.ndarray
    prov_d: np.ndarray
    prov_a: np.ndarray
    raw_jumps: np.ndarray
    frozen: np.ndarray
    log: dict = field(default_factory=dict)
    catalogue: list = field(default_factory=list)
    substitutions: list = field(default_factory=list)
    swaps: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    edge_runs: list = field(default_factory=list)
    # v4 additions (defaults keep the constructor backward compatible)
    acc_status: np.ndarray | None = None  # int8 codes into ACC_STATUS (R-ACC-BAND)
    parked_runs: list = field(default_factory=list)  # R-PARKING runs marked in this period
    parking_values: list = field(default_factory=list)  # the parking values used (discovered here when not given)
    acc_band: list = field(default_factory=list)  # R-ACC-BAND runs: {"col", "lo", "hi", "class", "sign", "peak"}
    kin_x: np.ndarray | None = None  # positions used for kinematics (gap samples removed), for R-ACC-WINDOW-TEST
    kin_y: np.ndarray | None = None
    kin_jumps: np.ndarray | None = None  # position jumps where kinematic segments break


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def track_bounds(x: np.ndarray) -> list[tuple[int, int] | None]:
    """First and last row with a position, per column (None if never tracked)."""
    out = []
    for c in range(x.shape[1]):
        f = np.flatnonzero(np.isfinite(x[:, c]))
        out.append((int(f[0]), int(f[-1])) if len(f) else None)
    return out


def jump_matrix(x: np.ndarray, y: np.ndarray, d_max: float) -> np.ndarray:
    j = np.zeros(x.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        step = np.hypot(np.diff(x, axis=0), np.diff(y, axis=0))
    j[1:] = np.nan_to_num(step, nan=0.0) > d_max
    return j


def still_matrix(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """True where the position equals the previous sample exactly."""
    s = np.zeros(x.shape, dtype=bool)
    s[1:] = (x[1:] == x[:-1]) & (y[1:] == y[:-1])
    return s


def frozen_matrix(x: np.ndarray, y: np.ndarray, min_frames: int) -> np.ndarray:
    """R-FROZEN: repeated samples of runs with an identical position for >= ``min_frames`` repeats.

    The first sample of a run is the last real measurement; the repeats are frozen.
    """
    still = still_matrix(x, y)
    fr = np.zeros_like(still)
    for c in range(x.shape[1]):
        for s, ln in zip(*run_lengths(still[:, c])):
            if ln >= min_frames:
                fr[s:s + ln, c] = True
    return fr


class Marks:
    """Collects (code, slice, column) exclusions and applies them in rule order."""

    def __init__(self, shape):
        self.items: list[tuple[int, int, int, int]] = []
        self.shape = shape

    def add(self, reason: str, lo: int, hi: int, col: int):
        lo, hi = max(0, lo), min(self.shape[0] - 1, hi)
        if hi >= lo:
            self.items.append((CODE[reason], lo, hi, col))

    def apply(self, reason_arr: np.ndarray, present: np.ndarray):
        for code, lo, hi, c in sorted(self.items):
            seg = reason_arr[lo:hi + 1, c]
            seg[(seg == 0) & present[lo:hi + 1, c]] = code


# ---------------------------------------------------------------------------
# Identity swaps (R-SWAP, R-SWAP-RESTORE)
# ---------------------------------------------------------------------------

def detect_mutual_swaps(x, y, jumps, dist):
    """Frames where two players jump onto each other's previous positions (both within ``dist``)."""
    out = []
    for i in np.flatnonzero(jumps.sum(axis=1) >= 2):
        cols = np.flatnonzero(jumps[i])
        for ia, a in enumerate(cols):
            for b in cols[ia + 1:]:
                d1 = np.hypot(x[i, a] - x[i - 1, b], y[i, a] - y[i - 1, b])
                d2 = np.hypot(x[i, b] - x[i - 1, a], y[i, b] - y[i - 1, a])
                if d1 < dist and d2 < dist:
                    out.append((int(i), int(a), int(b)))
    return out


def find_swap_partner(x, y, jumps, i, c, k, dist):
    """One-sided swap: c's new position is where another (also jumping) player was, or vice versa."""
    nf, npl = x.shape
    lo, hi = max(0, i - k), min(nf, i + k + 1)
    p_new = (x[i, c], y[i, c])
    p_old = (x[i - 1, c], y[i - 1, c]) if i > 0 else (np.nan, np.nan)
    best, best_d = None, np.inf
    for b in range(npl):
        if b == c or not jumps[lo:hi, b].any():
            continue
        with np.errstate(invalid="ignore"):
            d1 = np.nanmin(np.hypot(x[lo:i, b] - p_new[0], y[lo:i, b] - p_new[1])) if i > lo and np.isfinite(x[lo:i, b]).any() else np.inf
            d2 = np.nanmin(np.hypot(x[lo:hi, b] - p_old[0], y[lo:hi, b] - p_old[1])) if np.isfinite(x[lo:hi, b]).any() else np.inf
        d = min(d1, d2)
        if d < dist and d < best_d:
            best, best_d = b, d
    return best, float(best_d)


def correct_swaps(hd: HalfData, arrays: list[np.ndarray], cfg: PrepConfig, d_max: float) -> list[dict]:
    """``R-SWAP-RESTORE``: swap labels back between a mutual swap and its mutual correction.

    A mutual swap of the pair (a, b) at frame i followed by another mutual swap of
    the same pair at frame j **within** ``swap_pair_max_s`` (120 s) means the labels
    were wrong on [i, j): the columns of every array are exchanged there. A mutual
    swap without a correction in time is only windowed (we cannot tell which side
    is wrong). In every case the caller excludes +-1 s around both frames.
    """
    x, y = arrays[0], arrays[1]
    jumps = jump_matrix(x, y, d_max)
    mutual = detect_mutual_swaps(x, y, jumps, cfg.swap_distance_m)
    by_pair: dict[tuple[int, int], list[int]] = {}
    for i, a, b in mutual:
        by_pair.setdefault((a, b), []).append(i)
    incidents = []
    max_gap = cfg.frames(cfg.swap_pair_max_s, hd.fs)
    for (a, b), frames in sorted(by_pair.items()):
        frames = sorted(frames)
        k = 0
        while k < len(frames):
            i = frames[k]
            if k + 1 < len(frames) and frames[k + 1] - i <= max_gap:
                j = frames[k + 1]
                for arr in arrays:
                    arr[i:j, [a, b]] = arr[i:j, [b, a]]
                incidents.append({"frame_index": i, "end_index": j, "a": a, "b": b, "corrected": True})
                k += 2
            else:
                incidents.append({"frame_index": i, "end_index": None, "a": a, "b": b, "corrected": False})
                k += 1
    return incidents


# ---------------------------------------------------------------------------
# Main per-period preparation
# ---------------------------------------------------------------------------

def detect_inheritance(x: np.ndarray, y: np.ndarray, bounds: list, co: int | None, ci: int | None, cfg: PrepConfig) -> dict:
    """``R-SUB-INHERIT`` step 2: inheritance is detected, never assumed. It exists only when the incoming track's first
    sample is < ``sub_inherit_max_m`` from the outgoing player's last sample and at most ``sub_inherit_max_frame_gap``
    frames later; an outgoing player not tracked in the period gives no inheritance."""
    if co is None or ci is None or not bounds[co] or not bounds[ci]:
        return {"inheritance": False, "distance_m": None, "frame_gap": None}
    o1, i0 = bounds[co][1], bounds[ci][0]
    d = round(float(np.hypot(x[i0, ci] - x[o1, co], y[i0, ci] - y[o1, co])), 3)
    gap = int(i0 - o1)
    return {"inheritance": bool(d < cfg.sub_inherit_max_m and abs(gap) <= cfg.sub_inherit_max_frame_gap), "distance_m": d, "frame_gap": gap}


def inheritance_links(pids: list[str], x: np.ndarray, y: np.ndarray, subs: list[dict], cfg: PrepConfig) -> dict[str, str]:
    """incoming -> outgoing player ID for the substitutions of one period with identity inheritance."""
    col = {p: i for i, p in enumerate(pids)}
    bounds = track_bounds(x)
    out = {}
    for s in subs:
        r = detect_inheritance(x, y, bounds, col.get(s["player_out"]), col.get(s["player_in"]), cfg)
        if r["inheritance"]:
            out[str(s["player_in"])] = str(s["player_out"])
    return out


def segment_bounds(ok: np.ndarray, breaks: np.ndarray) -> list[tuple[int, int]]:
    """Continuous kinematic segments of one track: runs of ``ok`` samples, split where ``breaks`` is True
    (a position jump starts a new segment). Returns (start, end-exclusive) pairs."""
    out = []
    for s, ln in zip(*run_lengths(ok)):
        s, e = int(s), int(s + ln)
        cuts = [s] + [int(i) for i in np.flatnonzero(breaks[s + 1:e]) + s + 1] + [e]
        out += [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
    return out


def kinematics_on_segments(xk: np.ndarray, yk: np.ndarray, breaks: np.ndarray, fs: float, window: int, acc_window: int,
                           polyorder: int) -> tuple[np.ndarray, np.ndarray]:
    """``R-SG-SPEED`` and ``R-SG-ACC`` on every continuous segment of every track (frames x players).

    Segments break at gaps (NaN positions), track ends and position jumps, so that filter edges never fall
    inside valid data. Returns (speed, tangential acceleration); NaN where a segment is shorter than the window
    (speed) or than ``acc_window`` (acceleration)."""
    nf, npl = xk.shape
    speed = np.full((nf, npl), np.nan)
    acc_tan = np.full((nf, npl), np.nan)
    for c in range(npl):
        ok = np.isfinite(xk[:, c]) & np.isfinite(yk[:, c])
        for a, b in segment_bounds(ok, breaks[:, c]):
            k = savgol_kinematics(xk[a:b, c], yk[a:b, c], fs, window, polyorder, acc_window=acc_window)
            speed[a:b, c], acc_tan[a:b, c] = k["speed"], k["acc_tan"]
    return speed, acc_tan


def acc_band_runs(acc_tan: np.ndarray, valid: np.ndarray, cfg: PrepConfig, fs: float) -> tuple[np.ndarray, list[dict]]:
    """``R-ACC-BAND``: classify the valid samples' tangential acceleration. Never excludes a sample.

    Runs of consecutive valid samples beyond the band (below ``player_acc_min_ms2`` or above
    ``player_acc_max_ms2``, one sign per run) lasting at most ``frames(acc_noise_max_s)`` samples are ``noise``
    (their acceleration is not used); longer runs are ``approx`` (kept, reported with "≈"). Every other valid
    sample with an acceleration is ``measured``; the rest is ``none``.
    """
    has = valid & np.isfinite(acc_tan)
    status = np.where(has, ACC_CODE["measured"], ACC_CODE["none"]).astype(np.int8)
    max_noise = cfg.frames(cfg.acc_noise_max_s, fs)
    runs = []
    with np.errstate(invalid="ignore"):
        beyond = {"deceleration": has & (acc_tan < cfg.player_acc_min_ms2), "acceleration": has & (acc_tan > cfg.player_acc_max_ms2)}
    for sign, m in beyond.items():
        for c in range(acc_tan.shape[1]):
            for s0, ln in zip(*run_lengths(m[:, c])):
                s0, ln = int(s0), int(ln)
                cls = "noise" if ln <= max_noise else "approx"
                status[s0:s0 + ln, c] = ACC_CODE[cls]
                seg = acc_tan[s0:s0 + ln, c]
                runs.append({"col": c, "lo": s0, "hi": s0 + ln - 1, "samples": ln, "class": cls, "sign": sign,
                             "peak": float(np.min(seg) if sign == "deceleration" else np.max(seg))})
    runs.sort(key=lambda r: (r["col"], r["lo"]))
    return status, runs


def prepare_half(hd: HalfData, subs: list[dict], edges: list[dict], pitch: tuple[float, float], cfg: PrepConfig,
                 parking: list[dict] | None = None) -> HalfResult:
    """Prepare one period (guidelines v4, section 6.4 steps 2-13).

    ``edges``: tracking-area edges (``R-EDGE``). ``parking``: parking values (``R-PARKING``) discovered on the
    whole match; when ``None`` they are discovered on this period's frames (what the live engine does on the
    frames released so far). ``subs``: substitutions of the period, each with ``player_out``, ``player_in``,
    ``team`` and ``event_time_s`` (the event time corrected by the period's event clock offset).
    """
    fs = hd.fs
    dt = 1.0 / fs
    L, W = pitch
    nf, npl = hd.x.shape
    win = cfg.frames(cfg.exclusion_window_s, fs)
    d_max = cfg.player_speed_max_ms * dt
    x, y = hd.x.copy(), hd.y.copy()
    ps, pd_, pa = hd.prov_s.copy(), hd.prov_d.copy(), hd.prov_a.copy()
    log: dict[str, dict] = {}
    cat: list[dict] = []

    bounds = track_bounds(x)
    present = np.zeros((nf, npl), dtype=bool)
    for c, bnd in enumerate(bounds):
        if bnd:
            present[bnd[0]:bnd[1] + 1, c] = True

    raw_jumps = jump_matrix(x, y, d_max)

    # --- R-SWAP, R-SWAP-RESTORE: identity swaps (labels restored before anything else) ---
    incidents = correct_swaps(hd, [x, y, ps, pd_, pa], cfg, d_max)
    jumps = jump_matrix(x, y, d_max)

    marks = Marks((nf, npl))
    swap_frames: dict[int, set[int]] = {}
    for inc in incidents:
        for fr in [inc["frame_index"]] + ([inc["end_index"]] if inc["end_index"] is not None else []):
            for c in (inc["a"], inc["b"]):
                marks.add("swap", fr - win, fr + win, c)
                swap_frames.setdefault(c, set()).add(fr)

    # --- R-EDGE, R-PARKING, R-FROZEN: tracking-area edges, parking values and frozen tracks ---
    if parking is None:
        parking = discover_parking_values([x], [y], [list(hd.pids)], L / 2, W / 2, cfg.frames(cfg.frozen_min_s, fs), cfg.parking_min_samples,
                                          cfg.parking_min_tracks, edges, cfg.edge_value_decimals,
                                          same_track=inheritance_links(list(hd.pids), x, y, subs, cfg))["parking_values"]
    still = still_matrix(x, y)
    frozen = frozen_matrix(x, y, cfg.frames(cfg.frozen_min_s, fs))
    on_edge = edge_mask(x, y, edges, cfg.edge_value_decimals) & present
    pinned = np.zeros_like(on_edge)
    edge_runs = []
    for c in range(npl):
        cand = on_edge[:, c] | still[:, c]
        for s, ln in zip(*run_lengths(cand)):
            if on_edge[s:s + ln, c].any():
                pinned[s:s + ln, c] = True
                edge_runs.append({"col": c, "lo": int(s), "hi": int(s + ln - 1)})
    parked = parking_mask(x, y, parking, cfg.edge_value_decimals) & present & ~pinned
    parked_runs = []
    for c in range(npl):
        for s, ln in zip(*run_lengths(parked[:, c])):
            parked_runs.append({"col": c, "lo": int(s), "hi": int(s + ln - 1)})
    missing = present & ~np.isfinite(x)
    gap_mask = (missing | frozen | pinned | parked) & present

    sub_in = {s["player_in"]: s for s in subs}
    estimated = np.zeros((nf, npl), dtype=bool)
    discarded = np.zeros((nf, npl), dtype=bool)
    x_est, y_est = x.copy(), y.copy()
    gaps = []
    for c in range(npl):
        if not bounds[c]:
            continue
        t0, t1 = bounds[c]
        pid = hd.pids[c]
        for s, ln in zip(*run_lengths(gap_mask[:, c])):
            lo, hi = int(s), int(s + ln - 1)
            is_pinned = bool(pinned[lo:hi + 1, c].any())
            is_parked = bool(parked[lo:hi + 1, c].any()) and not is_pinned
            g = {"col": c, "lo": lo, "hi": hi, "frames": ln, "pinned": is_pinned, "parked": is_parked, "resume_m": None}
            if hi >= t1:
                # R-EXIT-REENTRY: a frozen, edge-pinned or parked run that ends the track is an exit.
                g["kind"] = "exit"
                marks.add("exit", lo, hi, c)
                discarded[lo:hi + 1, c] = True
            elif lo <= t0 + 1:  # the first sample is real (frozen) or the run starts the track (edge / parked)
                g["kind"] = "track_head"
                head_reason = "substitution" if pid in sub_in else ("outside_tracking_area" if is_pinned else ("parked" if is_parked else "frozen"))
                marks.add(head_reason, lo, hi + win, c)
                discarded[lo:hi + 1, c] = True
            else:
                d = float(np.hypot(x[hi + 1, c] - x[lo - 1, c], y[hi + 1, c] - y[lo - 1, c]))
                g["resume_m"] = d  # measured from the last sample before the run
                off_pitch = abs(x[lo, c]) > L / 2 or abs(y[lo, c]) > W / 2
                back_on = abs(x[hi + 1, c]) <= L / 2 and abs(y[hi + 1, c]) <= W / 2
                if is_pinned:
                    g["kind"] = "outside_tracking_area"
                    marks.add("outside_tracking_area", lo - win, hi + win, c)
                elif is_parked:
                    g["kind"] = "parked"
                    marks.add("parked", lo - win, hi + win, c)
                elif missing[lo:hi + 1, c].all():
                    g["kind"] = "missing"
                    marks.add("missing", lo, hi + win, c)
                elif off_pitch and back_on:
                    g["kind"] = "reentry"
                    marks.add("reentry", lo, hi + cfg.frames(cfg.reentry_stabilisation_s, fs), c)
                else:
                    g["kind"] = "frozen"
                    marks.add("frozen", lo, hi + win, c)
                if d < cfg.gap_resume_max_m:
                    known = np.isfinite(x[:, c]) & ~gap_mask[:, c]
                    ex, ey, method = estimate_gap(hd.t_s, x[:, c], y[:, c], known, lo, hi, fs, cfg)
                    x_est[lo:hi + 1, c], y_est[lo:hi + 1, c] = ex, ey
                    estimated[lo:hi + 1, c] = True
                    g["handling"], g["method"] = "estimated", method
                else:
                    discarded[lo:hi + 1, c] = True
                    g["handling"] = "discarded"
            g.setdefault("handling", "discarded")
            gaps.append(g)
    # Positions used for kinematics: gap samples removed.
    xk, yk = x.copy(), y.copy()
    xk[gap_mask], yk[gap_mask] = np.nan, np.nan

    # --- R-SUB-INHERIT: substitutions and identity inheritance (section 5.3) ---
    sub_rows = []
    col_of = {p: i for i, p in enumerate(hd.pids)}
    stab = cfg.frames(cfg.sub_stabilisation_s, fs)
    first_s = cfg.frames(cfg.sub_no_jump_invalid_s, fs)
    tail_kind = frozen | pinned | parked
    for s in subs:
        co, ci = col_of.get(s["player_out"]), col_of.get(s["player_in"])
        r = {"player_out": s["player_out"], "player_in": s["player_in"], "team": s.get("team")}
        t_ev = s["event_time_s"]
        out_tracked = bool(co is not None and bounds[co])
        if out_tracked:
            o0, o1 = bounds[co]
            i_ev = int(np.clip(np.searchsorted(hd.t_s, t_ev), o0, o1))
            # Outgoing player: invalid from the event (corrected time) or from the start of a frozen tail,
            # whichever is earlier, to the end of the track.
            tail = o1
            while tail > o0 and tail_kind[tail, co]:
                tail -= 1
            start = min(i_ev, tail + 1 if tail < o1 else i_ev)
            marks.add("substitution", start, o1, co)
            r.update(out_invalid_from_index=start, out_track_end_index=o1, out_frozen_tail_s=round((o1 - tail) / fs, 2),
                     event_to_out_end_s=round((o1 - i_ev) / fs, 2), out_invalid_s=round((o1 - start + 1) / fs, 2),
                     # Information only (it changes nothing): |x| of the outgoing player's last position.
                     halfway_distance_m=round(float(abs(x[o1, co])), 2) if np.isfinite(x[o1, co]) else None)
        if ci is not None and bounds[ci]:
            i0, i1 = bounds[ci]
            r["outgoing_tracked_in_period"] = out_tracked
            det = detect_inheritance(x, y, bounds, co, ci, cfg)
            if out_tracked:
                r["inherit_distance_m"], r["inherit_frame_gap"] = det["distance_m"], det["frame_gap"]
            inherits = det["inheritance"]
            r["inheritance"] = bool(inherits)
            jf = np.array([], dtype=int)
            if inherits:  # only then is the re-acquisition jump searched (within 60 s of the first sample)
                search_hi = min(i1, i0 + cfg.frames(cfg.sub_jump_search_s, fs))
                jf = np.flatnonzero(jumps[i0:search_hi + 1, ci])
            if len(jf):
                jidx = i0 + int(jf[0])
                end = jidx + stab  # invalid from the first sample until the jump + 2 s of stabilisation
                r.update(reacquisition_jump=True, reacquisition_after_s=round((jidx - i0) / fs, 2),
                         reacquisition_jump_m=round(float(np.hypot(x[jidx, ci] - x[jidx - 1, ci], y[jidx, ci] - y[jidx - 1, ci])), 2))
            else:
                end = i0 + first_s - 1  # no jump (or no inheritance): invalid for the first 1 s only
                r.update(reacquisition_jump=False, reacquisition_after_s=None)
            marks.add("substitution", i0, end, ci)
            h = i0 + 1
            while h <= i1 and tail_kind[h, ci]:
                h += 1
            r.update(in_invalid_until_s=round((end - i0 + 1) / fs, 2), in_invalid_s=round((end - i0 + 1) / fs, 2),
                     in_head_frozen_s=round((h - i0 - 1) / fs, 2))
            r["_in_window"] = (i0, end, ci)
        sub_rows.append(r)

    # --- R-JUMP, R-UNKNOWN: position jumps (cause classified for the catalogue; window +-1 s) ---
    k_swap = cfg.frames(cfg.swap_window_s, fs)
    in_sub_window = np.zeros((nf, npl), dtype=bool)
    for r in sub_rows:
        if "_in_window" in r:
            a, b, c = r["_in_window"]
            in_sub_window[a:b + 1, c] = True
        if "out_invalid_from_index" in r:
            in_sub_window[r["out_invalid_from_index"]:r["out_track_end_index"] + 1, col_of[r["player_out"]]] = True
    gap_end = np.zeros((nf, npl), dtype=bool)
    for g in gaps:
        if g["hi"] + 1 < nf:
            gap_end[g["hi"] + 1, g["col"]] = True
    offp = (np.abs(x) > L / 2 + 1.0) | (np.abs(y) > W / 2 + 1.0)
    offp_cum = np.vstack([np.zeros((1, npl)), np.cumsum(np.nan_to_num(offp), axis=0)])
    look = cfg.frames(30.0, fs)
    for c in range(npl):
        jm = jumps[:, c] | (raw_jumps[:, c] & np.isin(np.arange(nf), list(swap_frames.get(c, set()))))
        for s, ln in zip(*run_lengths(jm)):
            s, e = int(s), int(s + ln - 1)
            step = float(np.nanmax(np.hypot(np.diff(hd.x[max(s - 1, 0):e + 1, c]), np.diff(hd.y[max(s - 1, 0):e + 1, c])))) if e >= 1 else np.nan
            if in_sub_window[s, c]:
                cause, detail, reason = "substitution", "re-acquisition after identity inheritance" if hd.pids[c] in sub_in else "outgoing player", "substitution"
            elif s in swap_frames.get(c, set()):
                cause, detail, reason = "identity swap", "mutual swap with a team-mate or opponent", "swap"
            else:
                partner, _ = find_swap_partner(hd.x, hd.y, raw_jumps, s, c, k_swap, cfg.swap_distance_m)
                lo_ = max(0, s - look)
                if partner is not None:
                    cause, detail, reason = "identity swap", "one-sided swap (partner position match + partner jump)", "swap"
                elif gap_end[s, c] or (s >= 1 and gap_end[s - 1, c]):
                    cause, detail, reason = "track loss", "jump when a frozen, edge-pinned or parked track resumes (re-acquisition)", "jump"
                elif (offp_cum[s, c] - offp_cum[lo_, c]) / fs >= 10.0:
                    cause, detail, reason = "re-entry", ">= 10 s clearly off the pitch in the previous 30 s", "reentry"
                else:
                    others = np.r_[0:c, c + 1:npl]
                    crowd = 0
                    for fi in (s - 1, s):
                        with np.errstate(invalid="ignore"):
                            dd = np.hypot(hd.x[fi, others] - hd.x[fi, c], hd.y[fi, others] - hd.y[fi, c])
                        crowd = max(crowd, int(np.nansum(dd < cfg.crowding_radius_m)))
                    if crowd >= cfg.crowding_min_players:
                        cause, detail, reason = "crowding", f">= {cfg.crowding_min_players} players within {cfg.crowding_radius_m:g} m", "crowding"
                    else:
                        cause, detail, reason = "unknown", "no signature matched", "unknown"
            marks.add(reason, s - win, e + win, c)
            cat.append({"half": hd.half, "index": s, "col": c, "anomaly_type": "jump", "value": step, "duration_frames": int(ln),
                        "suspected_cause": cause, "cause_detail": detail, "exclusion_reason": reason})

    # --- R-OVERLAP-CROWD: overlaps (< 0.3 m for >= 0.2 s) ---
    min_ov = max(1, cfg.frames(cfg.overlap_min_s, fs))
    n_overlaps = 0
    for a in range(npl):
        for b in range(a + 1, npl):
            if not (present[:, a] & present[:, b]).any():
                continue
            with np.errstate(invalid="ignore"):
                d = np.hypot(xk[:, a] - xk[:, b], yk[:, a] - yk[:, b])
            for s, ln in zip(*run_lengths(np.nan_to_num(d, nan=np.inf) < cfg.overlap_distance_m)):
                if ln < min_ov:
                    continue
                n_overlaps += 1
                for c in (a, b):
                    marks.add("overlap", s - win, s + ln - 1 + win, c)
                lo_, hi_ = max(0, s - k_swap), min(nf, s + ln + k_swap)
                swapish = raw_jumps[lo_:hi_, a].any() or raw_jumps[lo_:hi_, b].any()
                cat.append({"half": hd.half, "index": int(s), "col": a, "partner_col": b, "anomaly_type": "overlap", "value": float(np.nanmin(d[s:s + ln])),
                            "duration_frames": int(ln), "suspected_cause": "identity swap" if swapish else "unknown",
                            "cause_detail": "overlap next to a jump" if swapish else "overlap without jump (contact or merged tracks)", "exclusion_reason": "overlap"})

    # --- R-SG-SPEED, R-SG-ACC: kinematics on continuous segments (section 7.2) ---
    speed, acc_tan = kinematics_on_segments(xk, yk, jumps, fs, cfg.savgol_window(fs), cfg.acc_window(fs), cfg.savgol_polyorder)

    # --- R-SPEED-LIMIT: player speed above 38 km/h (acceleration is a band, never an exclusion) ---
    with np.errstate(invalid="ignore"):
        viol = speed > cfg.player_speed_max_ms
    pre = np.zeros((nf, npl), dtype=np.int8)
    marks.apply(pre, present)
    n_limit_new = 0
    for c in range(npl):
        for s, ln in zip(*run_lengths(viol[:, c])):
            already = bool((pre[s:s + ln, c] > 0).all())
            marks.add("limit", s - win, s + ln - 1 + win, c)
            if not already:
                n_limit_new += 1
                cat.append({"half": hd.half, "index": int(s), "col": c, "anomaly_type": "limit", "value": float(np.nanmax(speed[s:s + ln, c])),
                            "duration_frames": int(ln), "suspected_cause": "unknown", "cause_detail": "speed above 38 km/h", "exclusion_reason": "limit"})

    # --- the mask (first rule of the priority list wins, section 6.1) ---
    reason = np.zeros((nf, npl), dtype=np.int8)
    marks.apply(reason, present)
    # Samples without kinematics (segments shorter than the filter window) and not otherwise excluded.
    short = present & (reason == 0) & ~np.isfinite(speed)
    reason[short] = CODE["unknown"]
    valid = present & (reason == 0) & np.isfinite(speed)

    # --- R-ACC-BAND: on valid samples, after the mask; positions, speed, distance and the mask are untouched ---
    acc_status, band_runs = acc_band_runs(acc_tan, valid, cfg, fs)

    # --- distance counted per sample: valid speed x dT + estimated path ---
    dist = np.where(valid, np.nan_to_num(speed) * dt, 0.0)
    for g in gaps:
        if g.get("handling") != "estimated":
            continue
        c, lo, hi = g["col"], g["lo"], g["hi"]
        px = np.r_[x[lo - 1, c], x_est[lo:hi + 1, c], x[hi + 1, c]]
        py = np.r_[y[lo - 1, c], y_est[lo:hi + 1, c], y[hi + 1, c]]
        st = np.hypot(np.diff(px), np.diff(py))
        dist[lo:hi + 1, c] += st[:-1]
        dist[hi, c] += st[-1]
        g["estimated_distance_m"] = float(st.sum())

    # --- ball (R-BALL-DEAD, R-BALL-WINDOW, R-JUMP for the ball in live play, R-BALL-LIMIT) ---
    bres = prepare_ball(hd, cfg, cat)

    band = {cls: {sign: {"runs": sum(1 for r in band_runs if r["class"] == cls and r["sign"] == sign),
                         "samples": sum(r["samples"] for r in band_runs if r["class"] == cls and r["sign"] == sign)}
                  for sign in ("acceleration", "deceleration")} for cls in ("noise", "approx")}
    log.update(
        swaps={"incidents": len(incidents), "corrected": sum(i["corrected"] for i in incidents)},
        overlaps=n_overlaps, limit_new_episodes=n_limit_new, short_segment_samples=int(short.sum()),
        acc_band=band, acc_none_valid_samples=int((valid & (acc_status == ACC_CODE["none"])).sum()),
        parking={"values": len(parking), "runs": len(parked_runs), "samples": int(parked.sum())},
    )
    for r in sub_rows:
        r.pop("_in_window", None)
    return HalfResult(
        half=hd.half, x=x, y=y, x_est=x_est, y_est=y_est, present=present, reason=reason, estimated=estimated & present,
        discarded=discarded & present, speed=speed, acc_tan=acc_tan, valid=valid, dist=dist,
        ball_reason=bres["reason"], ball_speed=bres["speed"], ball_valid=bres["valid"], prov_s=ps, prov_d=pd_, prov_a=pa,
        raw_jumps=raw_jumps & present, frozen=frozen & present, log=log | {"ball": bres["log"]}, catalogue=cat, substitutions=sub_rows,
        swaps=incidents, gaps=gaps, edge_runs=edge_runs, acc_status=acc_status, parked_runs=parked_runs, parking_values=list(parking),
        acc_band=band_runs, kin_x=xk, kin_y=yk, kin_jumps=jumps,
    )


def prepare_ball(hd: HalfData, cfg: PrepConfig, cat: list) -> dict:
    """``R-BALL-DEAD``, ``R-BALL-WINDOW``, ``R-JUMP`` (ball, live play only) and ``R-BALL-LIMIT``.

    Positions are kept for display. Ball kinematics are invalid while dead (``dead_ball``), for
    ``ball_restart_s`` (0.25 s) after each dead->live restart (``restart``) and +-``ball_window_s`` (0.25 s)
    around live-play ball jumps and live speeds above 211 km/h. Jumps between dead and live phases are expected
    and ignored. There is no limit on ball acceleration.
    """
    fs = hd.fs
    nf = len(hd.n)
    bx, by, st = hd.bx, hd.by, hd.bstatus
    present = np.isfinite(bx)
    live = st == 1
    dead = st == 0
    reason = np.zeros(nf, dtype=np.int8)

    def mark(name, lo, hi):
        lo, hi = max(0, lo), min(nf - 1, hi)
        seg = reason[lo:hi + 1]
        seg[(seg == 0) & present[lo:hi + 1]] = CODE[name]

    bw = cfg.frames(cfg.ball_window_s, fs)
    rs = cfg.frames(cfg.ball_restart_s, fs)
    # Order of BALL_ORDER: dead ball, restart, frozen, jump, limit (first rule wins).
    reason[dead & present] = CODE["dead_ball"]
    reason[~np.isfinite(st) & present] = CODE["unknown"]
    restarts = np.flatnonzero(dead[:-1] & live[1:]) + 1
    for i in restarts:
        mark("restart", i, i + rs - 1)
    still = np.zeros(nf, dtype=bool)
    still[1:] = (bx[1:] == bx[:-1]) & (by[1:] == by[:-1]) & live[1:] & live[:-1]
    n_frozen = 0
    for s, ln in zip(*run_lengths(still)):
        if ln >= cfg.frames(cfg.frozen_min_s, fs):
            mark("frozen", s, s + ln - 1)
            n_frozen += 1
    step = np.full(nf, np.nan)
    step[1:] = np.hypot(np.diff(bx), np.diff(by))
    live_step = np.r_[False, live[1:] & live[:-1]]
    bj = live_step & (np.nan_to_num(step) > cfg.ball_speed_max_ms / fs)
    n_restart_jumps = int((np.r_[False, dead[:-1] & live[1:]] & (np.nan_to_num(step) > cfg.ball_speed_max_ms / fs)).sum())
    for s, ln in zip(*run_lengths(bj)):
        mark("jump", s - bw, s + ln - 1 + bw)
        cat.append({"half": hd.half, "index": int(s), "col": -1, "anomaly_type": "ball_jump", "value": float(np.nanmax(step[s:s + ln])), "duration_frames": int(ln),
                    "suspected_cause": "ball-specific", "cause_detail": "jump during live play", "exclusion_reason": "jump"})
    # Kinematics on live segments, split at jumps and status changes.
    w = cfg.savgol_window(fs)
    speed = np.full(nf, np.nan)
    ok = live & present
    seg = np.cumsum(~ok | bj | np.r_[True, ~ok[:-1]])
    for sid in np.unique(seg[ok]):
        idx = np.flatnonzero((seg == sid) & ok)
        speed[idx] = savgol_kinematics(bx[idx], by[idx], fs, w, cfg.savgol_polyorder)["speed"]
    with np.errstate(invalid="ignore"):
        viol = live & (speed > cfg.ball_speed_max_ms)
    for s, ln in zip(*run_lengths(viol)):
        if (reason[s:s + ln] == 0).any():
            cat.append({"half": hd.half, "index": int(s), "col": -1, "anomaly_type": "ball_limit", "value": float(np.nanmax(speed[s:s + ln])), "duration_frames": int(ln),
                        "suspected_cause": "ball-specific", "cause_detail": "live ball speed beyond 211 km/h", "exclusion_reason": "limit"})
        mark("limit", s - bw, s + ln - 1 + bw)
    short = present & live & (reason == 0) & ~np.isfinite(speed)
    reason[short] = CODE["unknown"]
    valid = present & live & (reason == 0) & np.isfinite(speed)
    return {"reason": reason, "speed": speed, "valid": valid,
            "log": {"dead_samples": int((dead & present).sum()), "restarts": int(len(restarts)), "restart_jumps_ignored": n_restart_jumps,
                    "live_jump_episodes": int(len(run_lengths(bj)[0])), "live_frozen_runs": n_frozen,
                    "limit_episodes": int(len(run_lengths(viol)[0])), "short_segment_samples": int(short.sum())}}


def reason_names(codes: np.ndarray) -> np.ndarray:
    return np.array(REASONS, dtype=object)[codes]


def summarise_reasons(codes: np.ndarray, present: np.ndarray) -> dict[str, int]:
    vals, cnt = np.unique(codes[present], return_counts=True)
    return {REASONS[v]: int(c) for v, c in zip(vals, cnt)}


def to_frame(hr: HalfResult, hd: HalfData) -> pd.DataFrame:
    """Long per-sample table (players and ball) for the git-ignored frame-level output."""
    nf, npl = hr.x.shape
    acc_names = np.array(ACC_STATUS, dtype=object)[hr.acc_status] if hr.acc_status is not None else None
    rows = []
    for c in range(npl):
        m = hr.present[:, c]
        rows.append(pd.DataFrame({
            "period": hd.half, "frame": hd.n[m], "t_s": hd.t_s[m], "object_id": hd.pids[c], "object_type": "player", "team_side": hd.team_side[c],
            "x_m": hr.x[m, c], "y_m": hr.y[m, c], "x_est_m": hr.x_est[m, c], "y_est_m": hr.y_est[m, c], "z_m": np.nan,
            "ball_status": np.where(hd.bstatus[m] == 1, "live", np.where(hd.bstatus[m] == 0, "dead", "unknown")),
            "ball_possession": np.where(hd.bposs[m] == 1, "home", np.where(hd.bposs[m] == 2, "away", "none")),
            "speed_ms": hr.speed[m, c], "acc_tan_ms2": hr.acc_tan[m, c], "distance_m": hr.dist[m, c],
            "acc_status": acc_names[m, c] if acc_names is not None else "none",
            "valid_kinematics": hr.valid[m, c], "exclusion_reason": reason_names(hr.reason[m, c]),
            "estimated": hr.estimated[m, c], "discarded": hr.discarded[m, c],
            "provider_speed_ms": hr.prov_s[m, c], "provider_accel_ms2": hr.prov_a[m, c], "provider_step_m": hr.prov_d[m, c],
        }))
    m = np.isfinite(hd.bx)
    rows.append(pd.DataFrame({
        "period": hd.half, "frame": hd.n[m], "t_s": hd.t_s[m], "object_id": "BALL", "object_type": "ball", "team_side": "none",
        "x_m": hd.bx[m], "y_m": hd.by[m], "x_est_m": hd.bx[m], "y_est_m": hd.by[m], "z_m": hd.bz[m],
        "ball_status": np.where(hd.bstatus[m] == 1, "live", np.where(hd.bstatus[m] == 0, "dead", "unknown")),
        "ball_possession": np.where(hd.bposs[m] == 1, "home", np.where(hd.bposs[m] == 2, "away", "none")),
        "speed_ms": hr.ball_speed[m], "acc_tan_ms2": np.nan, "distance_m": np.nan, "acc_status": "none",
        "valid_kinematics": hr.ball_valid[m], "exclusion_reason": reason_names(hr.ball_reason[m]),
        "estimated": False, "discarded": False, "provider_speed_ms": hd.ball_prov_s[m], "provider_accel_ms2": np.nan, "provider_step_m": np.nan,
    }))
    return pd.concat(rows, ignore_index=True)
