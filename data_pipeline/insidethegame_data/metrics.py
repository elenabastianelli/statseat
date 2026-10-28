"""Player metrics, precision rules, shot statistics and the provider comparison (guidelines v4, section 10).

* Distance (sections 6.3, 10.1): sum of valid speed x dT plus estimated gap segments,
  over the whole time on the pitch, with splits for ball in play / out of play
  (provider ball status) and in / out of possession (provider possession).
* ``R-DIST-PRECISION`` (10.2): e = share of the time on the pitch not directly measured
  (estimated + discarded + excluded) sets the "≈" precision of distances.
* ``R-ACC-REPORT`` (10.2): peak acceleration and deceleration over valid samples with
  ``acc_status`` measured or approx, "≈" (to 0.1 m/s2) when the peak sample is approx;
  noise samples are never used.
* ``R-ZONES`` (10.5): distance per FIFA speed zone on valid samples; estimated gap distance
  is "unassigned"; zones plus unassigned add up to the total.
* ``R-SPRINT`` (10.5, METRICS.md decision 21, E-4): runs of consecutive valid, measured samples
  at or above 25 km/h lasting at least 1 s; two sprints less than 1 s apart count as one; any
  invalid or estimated sample ends a sprint and is never bridged.
* ``R-SHOT-COUNTS``, ``R-WOODWORK-STAT`` (10.6): shot statistics from the shot events and
  their outcome **label** (mapped to English, section 16.3); on target = goals + saved;
  woodwork hits are a statistic of their own.
* Provider comparison (section 7.1): tests only; it is computed after every decision has
  been taken and feeds nothing back.

Match metrics of ``Materials/METRICS.md`` (decisions of 25 October 2026, task E-4), pure functions
used by the app engine (``app/engine/frame_stats.py``):

* xG (section 9): ``shot_xg`` = ``calibration.shot_features`` + ``calibration.xg`` (the committed
  ``calibration/xg_model.json``, the same code path as the calibration); ``penalty_xg`` (0.79);
  ``chance_word`` (big / decent / half chance);
* pass length and long balls (section 11): ``pass_reception`` (the pipeline's kick candidates
  and next touch, as ``pass_length_m``) and ``long_balls``;
* peak intensity (section 4): ``rolling_intensity`` and ``team_intensity``;
* ball circulation (section 6): ``after_kicks_mask``;
* momentum (section 13): ``momentum_value``;
* acceleration load (section 22): ``acceleration_load_samples``;
* ball recoveries and counter-attacks (sections 21, 25): ``possession_gain_frame``, ``counter_attacks``;
* zone map, defensive line and team length, PPDA (sections 15, 17, 13): ``zone_map``, ``team_shape``,
  ``in_pressing_zone``, ``ppda``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration
from .config import PrepConfig
from .events_sync import kick_candidates
from .mapping import map_shot_outcome
from .prepare import ACC_CODE, REASONS, HalfData, HalfResult
from .segments import run_lengths
from .touches import next_touch

XG_MODEL_PATH = calibration.REPO / "calibration" / "xg_model.json"


def zone_labels(cfg: PrepConfig) -> list[str]:
    e = [0.0, *cfg.speed_zones_kmh]
    return [f"{e[i]:g}-{e[i + 1]:g}" for i in range(len(e) - 1)] + [f"{e[-1]:g}+"]


def format_acceleration(value: float, approx: bool) -> str:
    """``R-ACC-REPORT``: "≈ value" to 0.1 m/s2 for an approx sample, the value as computed otherwise."""
    if value is None or not np.isfinite(value):
        return "-"
    return f"≈ {value:+.1f} m/s2" if approx else f"{value:+.2f} m/s2"


def sprint_runs(speed: np.ndarray, valid_measured: np.ndarray, fs: float, cfg: PrepConfig) -> list[tuple[int, int]]:
    """``R-SPRINT`` (METRICS.md section 19, decision 21; decided in E-3, implemented in E-4): (first, last) sample of
    each sprint. A sprint is a run of consecutive valid, measured samples at or above ``sprint_min_kmh`` (exactly 25.0
    km/h counts) lasting at least ``sprint_min_s``. Sprints are found first, then two consecutive sprints separated by
    fewer than ``frames(sprint_merge_gap_s)`` samples below the threshold count as one, but only when every sample in
    between is valid and measured: an invalid or estimated sample ends a sprint and is never bridged."""
    vm = np.asarray(valid_measured, bool)
    with np.errstate(invalid="ignore"):
        fast = vm & (np.asarray(speed, float) * 3.6 >= cfg.sprint_min_kmh)
    starts, lengths = run_lengths(fast)
    keep = lengths >= cfg.frames(cfg.sprint_min_s, fs)
    gap_max = cfg.frames(cfg.sprint_merge_gap_s, fs)
    out: list[tuple[int, int]] = []
    for a, n in zip(starts[keep], lengths[keep]):
        a, b = int(a), int(a + n - 1)
        if out and a - out[-1][1] - 1 < gap_max and vm[out[-1][1] + 1:a].all():
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def count_sprints(speed: np.ndarray, valid_measured: np.ndarray, fs: float, cfg: PrepConfig) -> int:
    """``R-SPRINT``: the number of sprints of ``sprint_runs`` (merged sprints count once)."""
    return len(sprint_runs(speed, valid_measured, fs, cfg))


# ---------------------------------------------------------------------------
# Match metrics of Materials/METRICS.md (task E-4), used by the app engine
# ---------------------------------------------------------------------------

def xg_model(model_path: Path | None = None) -> dict:
    """The committed xG model ``calibration/xg_model.json`` (read once, with the calibration's own cache)."""
    return calibration._xg_model(str(model_path or XG_MODEL_PATH))


def shot_xg(ball_x_att: float, ball_y_att: float, opp_x_att, opp_y_att, pitch_length_m: float, goal_width_m: float,
            model_path: Path | None = None) -> dict:
    """xG of an open-play shot or a direct free kick (METRICS.md section 9, decision 9): the features of
    ``calibration.shot_features`` (distance, visible angle, opposing outfield players with valid positions inside the
    triangle ball-posts) in the shooting team's attacking coordinates (goal at x = +L/2), and ``calibration.xg`` of
    the committed model: the same code path as the calibration. Returns the three features and ``xg``."""
    f = calibration.shot_features(float(ball_x_att), float(ball_y_att), np.asarray(opp_x_att, float),
                                  np.asarray(opp_y_att, float), float(pitch_length_m), float(goal_width_m))
    return {**f, "xg": calibration.xg(f["distance_m"], f["visible_angle_rad"], f["opponents_in_triangle"], model_path)}


def penalty_xg(model_path: Path | None = None) -> float:
    """The fixed xG of a penalty (METRICS.md decision 10: 0.79, ``penalty_xg`` of the model file)."""
    return float(xg_model(model_path)["penalty_xg"])


def chance_word(xg: float, cfg: PrepConfig) -> str:
    """Words for Archie and the Recap (METRICS.md decision 11, our thresholds): xG >= 0.30 "big chance", 0.10-0.30
    "decent chance", below 0.10 "half chance"."""
    if xg >= cfg.chance_big_min_xg:
        return "big chance"
    return "decent chance" if xg >= cfg.chance_decent_min_xg else "half chance"


def shot_kick(sp, tt, col: int, t_c: float, win_s: float, cfg: PrepConfig) -> int | None:
    """The kick of a shot of player ``col`` annotated at ``t_c``: the best-scoring kick candidate of the pipeline in the
    search window (``events_sync.kick_candidates`` for a shot: ground touches, aerial touches with the ball above
    ``header_shot_min_height_m`` and soft kicks the shooter owns, ``R-TOUCH-NEAREST``; a window that is mostly dead
    ball extends backwards, ``R-LATE-ANNOTATION``; no ``R-ALIGN``). None when the shooter has no kick there."""
    cands = kick_candidates(sp, tt, col, t_c, win_s, cfg, "shot")
    return int(cands[0][0]) if cands else None


def pass_reception(sp, tt, col: int, t_c: float, win_s: float, cfg: PrepConfig) -> dict | None:
    """A pass of player ``col`` annotated at ``t_c`` (METRICS.md section 11, the pipeline's ``pass_length_m``): its kick
    is the best-scoring kick candidate of the pipeline in the search window (``events_sync.kick_candidates``: ground or
    aerial touches the player owns, ``R-TOUCH-NEAREST``; no ``R-ALIGN`` over the other events), its reception the next
    touch (``touches.next_touch``). ``length_m`` is the distance between the ball at the kick and at the reception, only
    when the next touch is a team-mate's (a successful pass). None when the pass is not located.
    ``sp``: an ``events_sync.SyncPeriod``; ``tt``: its touch table."""
    cands = kick_candidates(sp, tt, col, t_c, win_s, cfg, "pass")
    if not cands:
        return None
    i = int(cands[0][0])
    nt = next_touch(sp, tt, i, col, cfg)
    c = nt.get("col")
    mate = nt["kind"] == "touch" and c is not None and c != col and sp.team_side[c] == sp.team_side[col]
    length = float(np.hypot(sp.bx[nt["frame"]] - sp.bx[i], sp.by[nt["frame"]] - sp.by[i])) if mate else None
    return {"kick": i, "next_touch": nt["kind"], "reception": int(nt["frame"]) if mate else None, "teammate": mate,
            "length_m": length}


def long_balls(lengths_m, cfg: PrepConfig) -> int:
    """Successful passes of ``long_ball_min_m`` (32 m) or more (METRICS.md decision 13)."""
    return int(sum(1 for v in lengths_m if v >= cfg.long_ball_min_m))


def rolling_intensity(dist: np.ndarray, present: np.ndarray, clock_s: np.ndarray, done_s: float,
                      cfg: PrepConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Peak intensity (METRICS.md section 4, decision 4) of one period: each player's metres per minute over a rolling
    window of ``peak_window_s`` with a step of ``peak_step_s``, on the tracking clock (``clock_s``: seconds since the
    period's kick-off, increasing), ball out of play included (``dist``: (frames, players) metres counted at each
    sample, valid speed x dT plus estimated paths). The window ends are W, W + step, ... up to ``done_s`` (and up to
    the end of the last sample, which stands for one sample interval); a window holds the samples with
    end - W < clock <= end, so it never crosses the half-time break. A player counts in a window only when he was on
    the pitch (``present``) for the whole of it.
    Returns (window ends in s, m/min per end and player, on the pitch for the whole window)."""
    w, step, npl = cfg.peak_window_s, cfg.peak_step_s, dist.shape[1]
    dt = float(np.median(np.diff(clock_s[-10:]))) if len(clock_s) > 1 else 0.0
    last = min(float(done_s), float(clock_s[-1]) + dt) if len(clock_s) else 0.0
    if last < w:
        return np.zeros(0), np.zeros((0, npl)), np.zeros((0, npl), dtype=bool)
    ends = w + step * np.arange(int(np.floor((last - w) / step + 1e-9)) + 1)
    hi = np.searchsorted(clock_s, ends, side="right")
    lo = np.searchsorted(clock_s, ends - w, side="right")
    cd = np.vstack([np.zeros((1, npl)), np.cumsum(np.nan_to_num(dist), axis=0)])
    cp = np.vstack([np.zeros((1, npl), dtype=np.int64), np.cumsum(present, axis=0)])
    mpm = (cd[hi] - cd[lo]) / (w / 60.0)
    full = ((cp[hi] - cp[lo]) == (hi - lo)[:, None]) & ((hi - lo) > 0)[:, None]
    return ends, mpm, full


def team_intensity(mpm: np.ndarray, full: np.ndarray, cols: list[int]) -> np.ndarray:
    """A team's intensity per window (METRICS.md decision 4): the mean over the given (outfield) players who were on the
    pitch for the whole window; NaN when there is none."""
    sel = full[:, cols]
    n = sel.sum(axis=1)
    total = np.where(sel, mpm[:, cols], 0.0).sum(axis=1)
    return np.where(n > 0, total / np.maximum(n, 1), np.nan)


def after_kicks_mask(t: np.ndarray, kick_t, after_s: float) -> np.ndarray:
    """Ball circulation (METRICS.md section 6, decision 6): the samples with t_k <= t <= t_k + ``after_s`` of each kick
    time t_k (shots and clearances), which the circulation speed leaves out."""
    m = np.zeros(len(t), dtype=bool)
    for k in kick_t:
        m[int(np.searchsorted(t, k, side="left")):int(np.searchsorted(t, k + after_s, side="right"))] = True
    return m


def momentum_value(tilt_home_s: float, tilt_away_s: float, xg_home: float, xg_away: float, cfg: PrepConfig) -> float:
    """Momentum of one window (METRICS.md section 13, decision 15), in [-1, 1], positive = the home team is pushing:
    ``(w * tilt_home + (1 - w) * danger_home) - (w * tilt_away + (1 - w) * danger_away)`` with w =
    ``momentum_tilt_weight`` (0.5); tilt = each team's possession time with the ball in the opponent's final third as
    a share of both teams' (``tilt_*_s``); danger = each team's share of the xG of the shots in the window.
    Edge cases (E-4): no shot in the window (no xG): tilt only; no final-third possession: danger only; neither: 0."""
    t_all, x_all = tilt_home_s + tilt_away_s, xg_home + xg_away
    tilt = (tilt_home_s - tilt_away_s) / t_all if t_all > 0 else None  # tilt_home - tilt_away
    danger = (xg_home - xg_away) / x_all if x_all > 0 else None
    w = cfg.momentum_tilt_weight
    if tilt is not None and danger is not None:
        v = w * tilt + (1.0 - w) * danger
    else:
        v = tilt if tilt is not None else danger if danger is not None else 0.0
    return float(min(1.0, max(-1.0, v)))


def acceleration_load_samples(acc_tan: np.ndarray, acc_status: np.ndarray | None, valid: np.ndarray, fs: float) -> np.ndarray:
    """Acceleration load (METRICS.md section 22, decision 24) of each sample: |a_tan| x dT (m/s, a change of speed) on
    valid samples whose acceleration is ``measured`` or ``approx`` (``R-ACC-BAND``: ``noise`` is never used; estimated
    gaps have no acceleration), 0 elsewhere. Their sum is the total change of speed."""
    st = np.where(np.asarray(valid, bool) & np.isfinite(acc_tan), ACC_CODE["measured"], ACC_CODE["none"]) \
        if acc_status is None else acc_status
    use = np.asarray(valid, bool) & np.isin(st, (ACC_CODE["measured"], ACC_CODE["approx"])) & np.isfinite(acc_tan)
    return np.where(use, np.abs(np.nan_to_num(acc_tan)), 0.0) / fs


def possession_gain_frame(poss: np.ndarray, live: np.ndarray, i: int, team: float, opponent: float,
                          window: int) -> int | None:
    """Ball recoveries (METRICS.md section 21, decision 23): an event of the team (a ball claimed or intercepted, a tackle
    won by the defending player) at frame ``i`` is a recovery only when the provider possession column, in live play,
    changes from the opponent to the team within ``window`` frames of it. Returns the frame of that change (the
    nearest to ``i``, the earlier one on a tie), where the team wins the ball, or None."""
    lo, hi = max(1, i - window), min(len(poss) - 1, i + window)
    k = np.arange(lo, hi + 1)
    gain = k[live[k - 1] & live[k] & (poss[k - 1] == opponent) & (poss[k] == team)] if len(k) else k
    return int(gain[np.argmin(np.abs(gain - i))]) if len(gain) else None


def counter_attacks(recoveries: list[tuple[int, float, bool]], shots: list[tuple[int, float]], poss: np.ndarray,
                    live: np.ndarray, fs: float, cfg: PrepConfig) -> list[tuple[int, int]]:
    """Counter-attacks (METRICS.md section 25, decision 26) of one period: a recovery (frame, team, in its own half)
    in live play followed by a shot of the same team (frame, team; in frame order) within ``counter_attack_max_s``,
    with the ball live and never in the opponent's possession in between (provider column; team codes 1 and 2). One
    per recovery, with its first such shot. Returns (recovery index, shot index) pairs."""
    horizon = cfg.frames(cfg.counter_attack_max_s, fs)
    out = []
    for a, (i, team, own_half) in enumerate(recoveries):
        if not own_half or not live[i]:
            continue
        nxt = next((b for b, (j, t) in enumerate(shots) if t == team and j > i), None)
        if nxt is None or shots[nxt][0] - i > horizon:
            continue
        j = shots[nxt][0]
        if live[i:j].all() and not (poss[i:j + 1] == 3.0 - team).any():
            out.append((a, nxt))
    return out


def zone_map(x_att: np.ndarray, y_att: np.ndarray, pitch_length_m: float, pitch_width_m: float,
             cfg: PrepConfig) -> np.ndarray | None:
    """Zone map (METRICS.md section 15, decision 19) of the given samples (one per valid live sample, in the attacking
    coordinates of the player's team: own goal at x = -L/2): time per ``zone_map_grid_m`` (1 m) cell, smoothed with a
    Gaussian of ``zone_map_sigma_m`` (3 m), reflected at the pitch lines so that no time leaks off the pitch (a player
    near a touchline keeps his share), summed into display cells of ``zone_map_cell_m`` (5 x 4 m: 21 columns x 17 rows
    on 105 x 68 m) and normalised to 100 %. Row 0 is the left touchline of the attacking team (y = +W/2), column 0
    starts at its own goal line. None without samples."""
    from scipy.ndimage import gaussian_filter

    x, y = np.asarray(x_att, float), np.asarray(y_att, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.any():
        return None
    g = cfg.zone_map_grid_m
    nx, ny = int(round(pitch_length_m / g)), int(round(pitch_width_m / g))
    h, _, _ = np.histogram2d(y[ok], x[ok], bins=[ny, nx], range=[[-pitch_width_m / 2, pitch_width_m / 2],
                                                                 [-pitch_length_m / 2, pitch_length_m / 2]])
    h = gaussian_filter(h, sigma=cfg.zone_map_sigma_m / g, mode="reflect")[::-1]  # row 0 = y = +W/2
    cx, cy = (int(round(c / g)) for c in cfg.zone_map_cell_m)
    cols, rows = -(-nx // cx), -(-ny // cy)
    h = np.pad(h, ((0, rows * cy - ny), (0, cols * cx - nx)))
    cells = h.reshape(rows, cy, cols, cx).sum(axis=(1, 3))
    total = cells.sum()
    return 100.0 * cells / total if total > 0 else None


def team_shape(x_att: np.ndarray, on_pitch: np.ndarray, pitch_length_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Defensive line and team length (METRICS.md section 17, decision 20) per frame, from the outfield players'
    attacking x (frames, players): line = mean distance from the own goal line of the 2 deepest players; length = from
    the deepest to the most advanced after dropping one player at each end. NaN where fewer than 4 outfield players
    are on the pitch or one of them has no valid position (a line from the others could be wrong)."""
    on = np.asarray(on_pitch, bool)
    x = np.where(on, np.asarray(x_att, float), np.inf)
    n = on.sum(axis=1)
    ok = (n >= 4) & ~(on & ~np.isfinite(np.asarray(x_att, float))).any(axis=1)
    s = np.sort(np.where(np.isnan(x), np.inf, x), axis=1)
    rows = np.arange(len(s))
    line = (s[:, 0] + s[:, 1]) / 2.0 + pitch_length_m / 2.0
    length = s[rows, np.clip(n - 2, 0, s.shape[1] - 1)] - s[:, 1]
    with np.errstate(invalid="ignore"):
        return np.where(ok, line, np.nan), np.where(ok, length, np.nan)


def in_pressing_zone(x_att_defending: float, pitch_length_m: float, cfg: PrepConfig) -> bool:
    """PPDA (METRICS.md section 13, decision 17): the pressing zone of a team is the ``ppda_zone_share`` (60 %) of the
    pitch nearest to the opponent's goal, in that team's attacking coordinates."""
    return bool(x_att_defending >= pitch_length_m / 2 - cfg.ppda_zone_share * pitch_length_m)


def ppda(opponent_passes: int, defensive_actions: int) -> float | None:
    """Passes per defensive action; None when the team has no defensive action in its pressing zone (undefined)."""
    return opponent_passes / defensive_actions if defensive_actions > 0 else None


def format_distance(km: float, e: float, cfg: PrepConfig) -> str:
    if not np.isfinite(km):
        return "-"
    if e < cfg.precision_exact_below:
        return f"{km:.2f} km"
    if e < cfg.precision_tenth_below:
        return f"≈ {km:.1f} km"
    return f"≈ {round(km * 2) / 2:g} km"


def player_summary(pairs: list[tuple[HalfData, HalfResult]], lineup: pd.DataFrame, cfg: PrepConfig) -> pd.DataFrame:
    """One row per player over the whole time on the pitch (valid samples only; section 10)."""
    acc: dict[str, dict] = {}
    zones = zone_labels(cfg)
    edges = np.array(cfg.speed_zones_kmh)
    for hd, hr in pairs:
        fs = hd.fs
        live = hd.bstatus == 1
        st = hr.acc_status if hr.acc_status is not None else np.where(hr.valid & np.isfinite(hr.acc_tan), ACC_CODE["measured"], ACC_CODE["none"])
        band = {}
        for r in hr.acc_band:
            band.setdefault(r["col"], []).append(r)
        for c, pid in enumerate(hd.pids):
            m = hr.present[:, c]
            if not m.any():
                continue
            a = acc.setdefault(pid, {"object_id": pid, "team_side": hd.team_side[c], "samples": 0, "valid": 0, "estimated": 0, "discarded": 0,
                                     "dist_m": 0.0, "dist_live_m": 0.0, "dist_dead_m": 0.0, "dist_in_poss_m": 0.0, "dist_out_poss_m": 0.0,
                                     "top_speed_ms": np.nan, "top_speed_period": None, "top_speed_clock_s": None, "fs": fs,
                                     **{f"dist_zone_{z}_m": 0.0 for z in zones}, "dist_unassigned_m": 0.0, "sprints": 0,
                                     "acc_peak_ms2": np.nan, "acc_peak_approx": False, "dec_peak_ms2": np.nan, "dec_peak_approx": False,
                                     "acc_noise_runs": 0, "acc_noise_samples": 0, "acc_approx_runs": 0, "acc_approx_samples": 0,
                                     **{f"r_{r}": 0 for r in REASONS[1:]}})
            a["samples"] += int(m.sum())
            a["valid"] += int(hr.valid[:, c].sum())
            a["estimated"] += int(hr.estimated[:, c].sum())
            a["discarded"] += int(hr.discarded[:, c].sum())
            codes, cnt = np.unique(hr.reason[m, c], return_counts=True)
            for k, n in zip(codes, cnt):
                if k:
                    a[f"r_{REASONS[k]}"] += int(n)
            d = hr.dist[:, c]
            own = 1 if hd.team_side[c] == "home" else 2
            a["dist_m"] += float(d.sum())
            a["dist_live_m"] += float(d[live].sum())
            a["dist_dead_m"] += float(d[~live].sum())
            a["dist_in_poss_m"] += float(d[hd.bposs == own].sum())
            a["dist_out_poss_m"] += float(d[hd.bposs != own].sum())
            sp = np.where(hr.valid[:, c], hr.speed[:, c], np.nan)
            if np.isfinite(sp).any():
                i = int(np.nanargmax(sp))
                if not np.isfinite(a["top_speed_ms"]) or sp[i] > a["top_speed_ms"]:
                    a.update(top_speed_ms=float(sp[i]), top_speed_period=hd.half, top_speed_index=i)
            # R-ZONES: valid samples by speed zone; the estimated gap path is unassigned.
            v = hr.valid[:, c]
            zi = np.digitize(np.nan_to_num(hr.speed[v, c]) * 3.6, edges)
            dv = hr.dist[v, c]
            for zk, z in enumerate(zones):
                a[f"dist_zone_{z}_m"] += float(dv[zi == zk].sum())
            a["dist_unassigned_m"] += float(hr.dist[~v, c].sum())
            # R-SPRINT: valid, measured samples (an estimated or invalid sample ends the run).
            a["sprints"] += count_sprints(hr.speed[:, c], v & ~hr.estimated[:, c], fs, cfg)
            # R-ACC-REPORT: peaks over measured or approx samples; "≈" when the peak sample is approx.
            use = (st[:, c] == ACC_CODE["measured"]) | (st[:, c] == ACC_CODE["approx"])
            if use.any():
                at = np.where(use, hr.acc_tan[:, c], np.nan)
                imax, imin = int(np.nanargmax(at)), int(np.nanargmin(at))
                if not np.isfinite(a["acc_peak_ms2"]) or at[imax] > a["acc_peak_ms2"]:
                    a.update(acc_peak_ms2=float(at[imax]), acc_peak_approx=bool(st[imax, c] == ACC_CODE["approx"]))
                if not np.isfinite(a["dec_peak_ms2"]) or at[imin] < a["dec_peak_ms2"]:
                    a.update(dec_peak_ms2=float(at[imin]), dec_peak_approx=bool(st[imin, c] == ACC_CODE["approx"]))
            for r in band.get(c, []):
                a[f"acc_{r['class']}_runs"] += 1
                a[f"acc_{r['class']}_samples"] += r["samples"]
    df = pd.DataFrame(list(acc.values()))
    if df.empty:
        return df
    roles = lineup.set_index("person_id")[["role", "role_group"]]
    df["role"] = df["object_id"].map(roles["role"]).fillna("UNMAPPED_NONE")
    df["time_on_pitch_s"] = df["samples"] / df["fs"]
    df["valid_share"] = df["valid"] / df["samples"]
    df["not_measured_share"] = 1.0 - df["valid_share"]
    df["estimated_share"] = df["estimated"] / df["samples"]
    df["discarded_share"] = df["discarded"] / df["samples"]
    df["distance_reported"] = [format_distance(dm / 1000, e, cfg) for dm, e in zip(df["dist_m"], df["not_measured_share"])]
    df["top_speed_kmh"] = df["top_speed_ms"] * 3.6
    df["acc_peak_reported"] = [format_acceleration(v, ap) for v, ap in zip(df["acc_peak_ms2"], df["acc_peak_approx"])]
    df["dec_peak_reported"] = [format_acceleration(v, ap) for v, ap in zip(df["dec_peak_ms2"], df["dec_peak_approx"])]
    return df.sort_values(["team_side", "object_id"]).reset_index(drop=True)


SET_PIECE_SHOT_SUBTYPE = "ShotAtGoal"  # DFL nests the shot of a penalty or direct free kick inside the restart event
SET_PIECE_TAKER_ATTRIBUTES = ("Player", "ProspectiveTaker")


def shot_statistics(events: pd.DataFrame, side_of_team: dict[str, str], cfg: PrepConfig | None = None) -> dict:
    """``R-SHOT-COUNTS`` and ``R-WOODWORK-STAT`` per team and per player, from the shot events and their outcome label.

    The trajectory checks never relabel a shot: the label is the provider's annotation (real data) or the
    simulation's (synthetic data), mapped to English. On target = goals + saved; woodwork, blocked and wide are
    not on target; a woodwork hit that goes in is a goal (it carries the goal label).

    v4.1 (as Opta): **penalties count** in shots, shots on target and goals, and so do the other set-piece shots
    (direct free kicks, nested by DFL in the restart event). ``penalties``, ``penalty_goals`` and ``set_piece_shots`` keep
    them visible; their xG is separate (a penalty is a fixed 0.79, ``calibration/xg_model.json``). A set-piece shot whose
    taker is not annotated counts for its team only."""
    cfg = cfg or PrepConfig()
    s = events.loc[(events["event_type"] == "ShotAtGoal") | events["event_type"].isin(cfg.restart_event_types)]
    teams: dict[str, dict] = {}
    players: dict[str, dict] = {}
    unmapped = set()
    for _, r in s.iterrows():
        attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
        sub = r["sub_types"] if isinstance(r.get("sub_types"), str) else None
        set_piece = r["event_type"] != "ShotAtGoal"
        if set_piece:
            parts = (sub or "").split("/")
            if parts[0] != SET_PIECE_SHOT_SUBTYPE:
                continue  # an ordinary restart, not a shot
            sub = "/".join(parts[1:]) or None
            player = next((str(attrs[a]) for a in SET_PIECE_TAKER_ATTRIBUTES if attrs.get(a)), None)
        else:
            player = str(attrs.get("Player"))
        side = side_of_team.get(attrs.get("Team"), "unknown")
        lab = map_shot_outcome(sub)
        if lab["outcome"].startswith("UNMAPPED_"):
            unmapped.add(lab["outcome"])
        targets = [(side, teams)] + ([(player, players)] if player is not None else [])
        for key, d in targets:
            row = d.setdefault(key, {"shots": 0, "on_target": 0, "goals": 0, "saved": 0, "blocked": 0, "wide": 0, "woodwork": 0, "other": 0,
                                     "penalties": 0, "penalty_goals": 0, "set_piece_shots": 0})
            if d is players:
                row.setdefault("team_side", side)
            row["shots"] += 1
            row["on_target"] += int(lab["on_target"])
            row[lab["outcome"] if lab["outcome"] in ("saved", "blocked", "wide", "woodwork") else ("goals" if lab["outcome"] == "goal" else "other")] += 1
            if set_piece:
                row["set_piece_shots"] += 1
                if r["event_type"] == "Penalty":
                    row["penalties"] += 1
                    row["penalty_goals"] += int(lab["outcome"] == "goal")
    return {"teams": dict(sorted(teams.items())), "players": dict(sorted(players.items())), "unmapped_labels": sorted(unmapped),
            "convention": "on target = goals + saved; woodwork, blocked and wide are not on target; woodwork hits counted separately; "
                          "penalties and direct free-kick shots count in shots, on target and goals (v4.1, as Opta), also listed as penalties / set_piece_shots"}


def provider_comparison(pairs: list[tuple[HalfData, HalfResult]]) -> dict:
    """Our kinematics vs the provider's on valid player samples (tests only)."""
    sp, ps, st, pdv, at, pa = [], [], [], [], [], []
    jump_s, jump_d, jump_step = [], [], []
    frozen_s = []
    for hd, hr in pairs:
        v = hr.valid
        step = np.full(hr.x.shape, np.nan)
        step[1:] = np.hypot(np.diff(hr.x, axis=0), np.diff(hr.y, axis=0))
        sp.append(hr.speed[v]); ps.append(hr.prov_s[v]); st.append(step[v]); pdv.append(hr.prov_d[v]); at.append(hr.acc_tan[v]); pa.append(hr.prov_a[v])
        raw_step = np.full(hd.x.shape, np.nan)
        raw_step[1:] = np.hypot(np.diff(hd.x, axis=0), np.diff(hd.y, axis=0))
        j = hr.raw_jumps
        jump_s.append(hd.prov_s[j]); jump_d.append(hd.prov_d[j]); jump_step.append(raw_step[j])
        frozen_s.append(hd.prov_s[hr.frozen])
    cat = lambda a: np.concatenate(a) if a else np.array([])  # noqa: E731
    sp, ps, st, pdv, at, pa = map(cat, (sp, ps, st, pdv, at, pa))
    jump_s, jump_d, jump_step, frozen_s = map(cat, (jump_s, jump_d, jump_step, frozen_s))
    if not np.isfinite(ps).any():
        return {"supplied": False}

    def comp(a, b):
        ok = np.isfinite(a) & np.isfinite(b)
        d = a[ok] - b[ok]
        return {"n": int(ok.sum()), "mean_diff": float(d.mean()), "p95_abs_diff": float(np.quantile(np.abs(d), 0.95)), "r": float(np.corrcoef(a[ok], b[ok])[0, 1])}

    ok = np.isfinite(jump_d) & np.isfinite(jump_step)
    return {
        "supplied": True,
        "speed_vs_S_ms": comp(sp, ps),
        "step_vs_D_m": comp(st, pdv),
        "acc_tan_vs_A_ms2": comp(at, pa),
        "provider_A_range_ms2": [float(np.nanmin(pa)), float(np.nanmax(pa))] if np.isfinite(pa).any() else None,
        "at_raw_jump_frames": {"n": int(len(jump_s)), "provider_S_max_ms": float(np.nanmax(jump_s)) if len(jump_s) else None,
                               "share_D_equal_step": float(np.mean(np.abs(jump_d[ok] - jump_step[ok]) < 0.01)) if ok.any() else None,
                               "max_step_m": float(np.nanmax(jump_step)) if len(jump_step) else None},
        "during_frozen_samples": {"n": int(len(frozen_s)), "provider_S_mean_ms": float(np.nanmean(frozen_s)) if len(frozen_s) else None},
    }
