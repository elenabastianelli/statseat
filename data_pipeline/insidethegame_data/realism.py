"""Linesman: realism report of a synthetic match against the DFL reference (guidelines v4, section 12).

Usage (from data_pipeline/):
    .venv/bin/python -m insidethegame_data.realism SYN-001 --replicas 6

What it does:

1. prepares the synthetic match (``synthetic/<id>/``) and its replicas (``synthetic/replicas/<id>-r<k>/``,
   made by ``python -m insidethegame_gen.match SYN-001 --replicas 6 --only-replicas`` in ``generator/``)
   with the **same preparation pipeline** as the DFL matches (``qc.run``), writing the frame-level outputs to
   a git-ignored work folder (default ``reports/qc/frames/_realism/``). A match already prepared there is
   reused only while its input files are the ones the preparation hashed (SHA-256 in its ``qc_results.json``):
   a regenerated match is prepared again (``stale_inputs``), and ``--no-qc`` refuses a stale one;
2. computes every metric of ``realism_metrics.py`` (the same code that built the DFL reference);
3. compares each metric with ``calibration/realism_reference.json``:

   * **one-sample Kolmogorov-Smirnov distance D** of the synthetic sample (SYN-001 and its replicas pooled,
     and SYN-001 alone) against the DFL CDF interpolated from its 21 stored quantiles (``ks_distance``). The
     reference stores p2.5, p5, p10, p15, ..., p90, p95, p97.5 and no minimum or maximum (privacy; known limit,
     accepted, guidelines section 19: a quantile at a whole-number position coincides with one player-match value). The CDF is linear between them, 0 below p2.5 and 1 from p97.5. In each tail it differs
     from the full DFL distribution by at most 0.025 (the reference mass there), and between two stored levels
     by at most their spacing (0.05), so D differs from the D against the full distribution by at most 0.025 in
     the tails and 0.05 inside; the traffic-light thresholds are unchanged;
   * the position of the synthetic median relative to the **range of the DFL per-match medians**;
   * the **traffic light** (``traffic_light``): green = D <= 0.15 and the median inside the range; yellow =
     D in (0.15, 0.30] or the median just outside (within 10 % of the range width); red = D > 0.30 or the
     median far outside. Metrics with one value per match or per team-match use the median rule only;

4. writes ``reports/realism/realism.json`` (every number), ``reports/realism/figures/*.png`` and
   ``reports/realism/README.md``.

:func:`realism_report` returns the same dictionary as ``realism.json``, so a Foundry "Linesman" agent can
call it as a tool. Deterministic: no randomness, no wall-clock time, fixed PNG metadata; two runs give
byte-identical files. Only aggregates are written: no frame-level data and no person IDs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from .calibration import DEFAULT_CAL_CONFIG, CalibrationConfig, MatchData, dumps, frames_dir, load_match, sha256
from .config import GUIDELINES_VERSION
from .realism_metrics import (DEFAULT_REALISM_CONFIG, GROUPS, RealismConfig, match_samples, merge_crosstabs, metric_catalogue, role_position_sums,
                              shot_outcome_crosstab)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = REPO / "reports" / "qc" / "frames" / "_realism"  # git-ignored (reports/qc/frames)
DEFAULT_OUT_DIR = REPO / "reports" / "realism"
DEFAULT_REFERENCE = REPO / "calibration" / "realism_reference.json"
DEFAULT_POSITION_MAPS = REPO / "calibration" / "position_maps.json"
LIGHTS = ("green", "yellow", "red")


# ---------------------------------------------------------------------------
# Kolmogorov-Smirnov distance against a CDF given by its quantiles
# ---------------------------------------------------------------------------

def _levels(quantiles, levels) -> np.ndarray:
    """Levels (0..1) of the stored quantiles; evenly spaced 0..1 when not given."""
    if levels is None:
        return np.linspace(0.0, 1.0, len(quantiles))
    lev = np.asarray(levels, float)
    if len(lev) != len(quantiles):
        raise ValueError(f"{len(quantiles)} quantiles but {len(lev)} levels")
    return lev


def reference_cdf(quantiles, levels=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Knots of the reference CDF from quantiles at ``levels`` (fractions 0..1; default evenly spaced 0..1).

    Returns the distinct quantile values ``xs`` and, for each, the lowest and highest level at which it
    occurs (``lo``, ``hi``). A value repeated over several levels is an atom: the CDF jumps there from
    ``lo`` to ``hi``. Between two knots the CDF is linear, from ``hi`` of the left knot to ``lo`` of the
    right one. It is 0 below the first knot and 1 from the last one: with the reference's 21 levels (p2.5 ...
    p97.5) the CDF jumps from 0 to 0.025 at p2.5 and from 0.975 to 1 at p97.5, so each tail's 2.5 % of mass
    sits on that quantile. Against the full distribution this CDF is off by at most 0.025 in the tails and by
    at most the spacing of two stored levels (0.05) between them; D inherits the same bounds.
    """
    q = np.maximum.accumulate(np.asarray(quantiles, float))
    lev = _levels(q, levels)
    xs, first = np.unique(q, return_index=True)
    _, last_rev = np.unique(q[::-1], return_index=True)
    last = len(q) - 1 - last_rev
    lo, hi = lev[first].copy(), lev[last].copy()
    lo[0], hi[-1] = 0.0, 1.0  # 0 below the lowest quantile, 1 from the highest one
    return xs, lo, hi


def cdf_at(x, knots: tuple[np.ndarray, np.ndarray, np.ndarray], left: bool = False) -> np.ndarray:
    """Reference CDF F(x) (right-continuous), or its left limit F(x-) with ``left=True``."""
    xs, lo, hi = knots
    x = np.atleast_1d(np.asarray(x, float))
    out = np.zeros_like(x)
    j = np.searchsorted(xs, x, side="right") - 1  # last knot <= x
    inside = j >= 0
    jj = np.clip(j, 0, len(xs) - 1)
    exact = inside & (xs[jj] == x)
    out[exact] = (lo if left else hi)[jj[exact]]
    between = inside & ~exact & (jj < len(xs) - 1)
    jb = jj[between]
    t = (x[between] - xs[jb]) / (xs[jb + 1] - xs[jb])
    out[between] = hi[jb] + t * (lo[jb + 1] - hi[jb])
    out[inside & ~exact & (jj == len(xs) - 1)] = 1.0
    return out


def ks_distance(sample, quantiles, levels=None) -> float | None:
    """One-sample KS distance D = sup |F_n(x) - F(x)| of ``sample`` against the CDF interpolated from ``quantiles``
    at ``levels`` (fractions 0..1; default evenly spaced 0..1).

    F_n is a step function and F is piecewise linear with possible jumps, so the supremum is reached at a
    sample value or a knot, taking right values and left limits of both: D is exact for that F, not approximated
    on a grid. With the reference's coarse levels F is 0 below p2.5 and 1 from p97.5 (see ``reference_cdf``).
    Returns None for an empty sample.
    """
    s = np.sort(np.asarray(sample, float))
    s = s[np.isfinite(s)]
    if len(s) == 0:
        return None
    knots = reference_cdf(quantiles, levels)
    pts = np.unique(np.concatenate([s, knots[0]]))
    n = len(s)
    fn = np.searchsorted(s, pts, side="right") / n
    fn_left = np.searchsorted(s, pts, side="left") / n
    d = max(float(np.max(np.abs(fn - cdf_at(pts, knots)))), float(np.max(np.abs(fn_left - cdf_at(pts, knots, left=True)))))
    return d


# ---------------------------------------------------------------------------
# Median rule and traffic light
# ---------------------------------------------------------------------------

def median_position(value: float | None, rng, tolerance_frac: float) -> str | None:
    """``inside`` the DFL per-match median range, ``just_outside`` (within ``tolerance_frac`` of its width) or ``far_outside``."""
    if value is None or rng is None or not np.isfinite(value):
        return None
    lo, hi = float(rng[0]), float(rng[1])
    tol = tolerance_frac * (hi - lo)
    if lo <= value <= hi:
        return "inside"
    if lo - tol <= value <= hi + tol:
        return "just_outside"
    return "far_outside"


def traffic_light(d: float | None, position: str | None, cfg: RealismConfig = DEFAULT_REALISM_CONFIG) -> str | None:
    """green / yellow / red. ``d`` is None for range-only metrics (one value per match or team-match)."""
    if position is None:
        return None
    if position == "far_outside" or (d is not None and d > cfg.yellow_ks_max):
        return "red"
    if position == "just_outside" or (d is not None and d > cfg.green_ks_max):
        return "yellow"
    return "green"


def quantile_of(quantiles, level: float, levels=None) -> float | None:
    """The reference value at ``level`` (0..1), linear between the stored quantiles at ``levels`` (clamped to the
    outermost stored quantiles)."""
    if quantiles is None or len(quantiles) == 0:
        return None
    return float(np.interp(level, _levels(quantiles, levels), quantiles))


def ks_noise(n_synthetic: int, n_reference: int, cfg: RealismConfig = DEFAULT_REALISM_CONFIG) -> float | None:
    """D that two independent samples of these sizes from the **same** distribution exceed only 5 % of the time
    (two-sample Kolmogorov-Smirnov critical value c(0.05) * sqrt((n + m) / (n m)), c = 1.358).

    A diagnostic, never part of the traffic light: with 12-35 player-matches per side a D of 0.3-0.5 can be chance.
    Per-frame samples (ball, team shape) are autocorrelated, so their real noise level is higher than this value.
    """
    if n_synthetic <= 0 or n_reference <= 0:
        return None
    return float(cfg.ks_noise_c_alpha * math.sqrt((n_synthetic + n_reference) / (n_synthetic * n_reference)))


def _summary(values: np.ndarray) -> dict:
    v = np.asarray(values, float)
    if len(v) == 0:
        return {"n": 0, "median": None, "mean": None, "p10": None, "p90": None}
    p10, p50, p90 = np.percentile(v, [10, 50, 90])
    return {"n": int(len(v)), "median": float(p50), "mean": float(v.mean()), "p10": float(p10), "p90": float(p90)}


def compare_metric(ref: dict, pooled: np.ndarray, alone: np.ndarray, cfg: RealismConfig = DEFAULT_REALISM_CONFIG, levels=None) -> dict:
    """Comparison of one metric: synthetic pooled (match + replicas) and the match alone against the DFL reference.
    ``levels``: the levels (fractions 0..1) of the reference quantiles (``quantile_levels_pct`` / 100)."""
    q = ref.get("quantiles")
    ks = ref["comparison"] == "ks_and_range"
    rng = ref.get("match_median_range")
    out = {"group": ref["group"], "name": ref["name"], "role_group": ref.get("role_group"), "unit": ref["unit"],
           "observation": ref["observation"], "comparison": ref["comparison"]}
    out["dfl"] = {"n": ref["n"], "n_matches": ref["n_matches"], "median": quantile_of(q, 0.5, levels), "p10": quantile_of(q, 0.1, levels),
                  "p90": quantile_of(q, 0.9, levels), "mean": ref.get("mean"), "match_median_range": rng}
    for key, sample in (("synthetic", pooled), ("syn_match", alone)):
        s = _summary(sample)
        d = ks_distance(sample, q, levels) if (ks and q and len(sample)) else None
        pos = median_position(s["median"], rng, cfg.range_tolerance_frac)
        s.update(ks_d=d, ks_d_noise95=ks_noise(len(sample), ref["n"], cfg) if d is not None else None, median_position=pos,
                 light=traffic_light(d, pos, cfg) if (len(sample) and q) else None)
        if s["median"] is not None and out["dfl"]["median"] is not None:
            s["direction"] = "higher" if s["median"] > out["dfl"]["median"] else ("lower" if s["median"] < out["dfl"]["median"] else "equal")
        else:
            s["direction"] = None
        out[key] = s
    out["light"] = out["synthetic"]["light"]
    out["small_sample"] = bool(ks and (out["synthetic"]["n"] < cfg.small_sample_n or ref["n"] < cfg.small_sample_n))
    return out


# ---------------------------------------------------------------------------
# Synthetic matches through the pipeline
# ---------------------------------------------------------------------------

def synthetic_matches(match_id: str, replicas: int, repo: Path = REPO) -> list[tuple[str, Path]]:
    """(match id, folder) of the match and its replicas."""
    repo = Path(repo)
    out = [(match_id, repo / "synthetic" / match_id)]
    out += [(f"{match_id}-r{k}", repo / "synthetic" / "replicas" / f"{match_id}-r{k}") for k in range(1, replicas + 1)]
    return out


def stale_inputs(match_id: str, folder: Path, work_dir: Path) -> list[str]:
    """The input files of ``folder`` that differ from the ones the preparation in ``work_dir`` hashed (SHA-256 in
    ``qc_results.json`` ``input_files``) or are missing now. Empty: the preparation belongs to these files. A
    preparation without recorded hashes is stale (``["qc_results.json"]``): it cannot prove what it was made from."""
    qcr = Path(work_dir) / "reports" / "qc" / match_id / "qc_results.json"
    recorded = (json.loads(qcr.read_text()).get("input_files") or {}) if qcr.exists() else {}
    if not recorded:
        return ["qc_results.json"]
    return [name for name, rec in sorted(recorded.items())
            if not (Path(folder) / name).exists() or sha256(Path(folder) / name) != rec.get("sha256")]


def ensure_prepared(match_id: str, folder: Path, work_dir: Path, repo: Path = REPO, run_qc: bool = True, log=print) -> str:
    """Prepare a synthetic match with ``qc.run`` into ``work_dir`` unless it is already there, from the same input
    files (``stale_inputs``; a regenerated match is prepared again). Returns what was done."""
    prof = Path(work_dir) / "reports" / "qc" / match_id / "match_profile.json"
    tr = frames_dir(work_dir, match_id) / "tracking_canonical.parquet"
    if prof.exists() and tr.exists():
        stale = stale_inputs(match_id, folder, work_dir) if Path(folder).is_dir() else []
        if not stale:
            return "reused"
        if not run_qc:
            raise RuntimeError(f"{match_id} in {work_dir} was prepared from other input files ({', '.join(stale)} changed): run without --no-qc")
        from .qc import run as qc_run

        log(f"{match_id}: input files changed since its preparation ({', '.join(stale)}): preparing again into {work_dir}")
        qc_run(match_id, repo=repo, source="synthetic", synthetic_dir=folder, reports_dir=Path(work_dir) / "reports" / "qc", log=lambda *_: None,
               acc_window_test=False, overwrite_frames=True)
        return "prepared again"
    if prof.exists() != tr.exists():
        raise RuntimeError(f"{work_dir} holds a partial preparation of {match_id}: delete reports/qc/{match_id} and reports/qc/frames/{match_id} there")
    if not run_qc:
        raise FileNotFoundError(f"{match_id} is not prepared in {work_dir} (run without --no-qc)")
    if not Path(folder).is_dir():
        raise FileNotFoundError(f"{folder} does not exist: generate it first (generator: python -m insidethegame_gen.match <id> --replicas N --only-replicas)")
    from .qc import run as qc_run

    log(f"{match_id}: preparing with the pipeline (qc) into {work_dir}")
    qc_run(match_id, repo=repo, source="synthetic", synthetic_dir=folder, reports_dir=Path(work_dir) / "reports" / "qc", log=lambda *_: None,
           acc_window_test=False)
    return "prepared"


def woodwork_distance(bx, by, bz, L: float, goal_width_m: float, crossbar_m: float) -> tuple[np.ndarray, np.ndarray]:
    """3-D distance of ball positions from the nearest post or crossbar of either goal, as ``R-WOODWORK`` measures it
    (posts: vertical segments 0..crossbar at x = +-L/2, y = +-goal_width/2; crossbar: |y| <= goal_width/2 at z = crossbar).
    Returns (distance, part) with part 0 = post, 1 = crossbar. Without ball height (NaN z) only the posts count (2-D)."""
    bx, by, bz = (np.asarray(a, float) for a in (bx, by, bz))
    gw = goal_width_m / 2
    has_z = np.isfinite(bz)
    zz = np.where(has_z, bz, 0.0)
    dz_post = np.where(has_z, np.where(zz >= 0, np.maximum(0.0, zz - crossbar_m), -zz), 0.0)
    best = np.full(bx.shape, np.inf)
    part = np.zeros(bx.shape, dtype=np.int8)
    for gx in (-L / 2, L / 2):
        for py in (-gw, gw):
            d = np.sqrt((bx - gx) ** 2 + (by - py) ** 2 + dz_post ** 2)
            closer = d < best
            best, part = np.where(closer, d, best), np.where(closer, 0, part)
        d = np.where(has_z, np.sqrt((bx - gx) ** 2 + np.maximum(0.0, np.abs(by) - gw) ** 2 + (zz - crossbar_m) ** 2), np.inf)
        closer = d < best
        best, part = np.where(closer, d, best), np.where(closer, 1, part)
    return best, part


def woodwork_diagnosis(md: MatchData) -> list[dict]:
    """Shots labelled woodwork: the pipeline's observed outcome and, for located shots, why ``R-WOODWORK`` did or did not
    see the contact: the nearest approach of the ball samples to the woodwork during the free flight, the nearest player
    there, the shot speed and the ball's travel between two frames. Aggregates only, no IDs."""
    from .config import DEFAULT_CONFIG as PREP

    ev = md.events
    out = []
    if "shot_outcome_label" not in ev:
        return out
    conf = (md.qc_results or {}).get("config", {})
    thr = float(conf.get("woodwork_distance_m", PREP.woodwork_distance_m))
    gw, cb = float(conf.get("goal_width_m", PREP.goal_width_m)), float(conf.get("crossbar_height_m", PREP.crossbar_height_m))
    for r in ev.loc[(ev["event_type"] == "ShotAtGoal") & (ev["shot_outcome_label"] == "woodwork")].itertuples(index=False):
        h = int(r.half)
        t = (r.sync_time_utc if pd.notna(r.sync_time_utc) else r.time_utc).timestamp()
        clock = t - md.kickoff_s.get(h, t)
        row = {"period": h, "minute": int(clock // 60) + 1 + (45 if h == 2 else 0),
               "observed": r.shot_outcome_observed if isinstance(r.shot_outcome_observed, str) else None,
               "type_check": r.type_check if isinstance(r.type_check, str) else None, "located": bool(pd.notna(r.sync_frame)),
               "woodwork_distance_m": thr}
        bt = md.ball.get(h)
        if pd.notna(r.sync_frame) and bt is not None:
            k = int(bt.index([int(r.sync_frame)])[0])
            e = int(bt.index([int(r.flight_end_frame)])[0]) if pd.notna(getattr(r, "flight_end_frame", np.nan)) else -1
            if e <= k:
                e = min(len(bt.x) - 1, k + int(round(10 * md.fs)))
            idx = np.arange(k + 1, e + 1)
            idx = idx[bt.live[idx] & np.isfinite(bt.x[idx])]
            if k >= 0 and len(idx):
                d, part = woodwork_distance(bt.x[idx], bt.y[idx], bt.z[idx], md.L, gw, cb)
                j = int(np.argmin(d))
                f = int(bt.f0 + idx[j])
                pl = md.players.loc[(md.players["period"] == h) & (md.players["frame"] == f) & md.players["valid"]]
                near = np.hypot(pl["x"].to_numpy() - bt.x[idx[j]], pl["y"].to_numpy() - bt.y[idx[j]])
                seg = np.arange(k, min(k + int(round(0.5 * md.fs)), len(bt.x) - 1) + 1)
                v = bt.speed[seg][bt.valid[seg]]
                vmax = float(np.nanmax(v)) if np.isfinite(v).any() else float("nan")
                row.update(nearest_woodwork_m=float(d[j]), nearest_woodwork_part="post" if part[j] == 0 else "crossbar",
                           nearest_after_kick_s=(idx[j] - k) / md.fs, nearest_player_m=float(near.min()) if len(near) else None,
                           shot_speed_kmh=vmax * 3.6, ball_step_per_frame_m=vmax / md.fs)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

VISIBILITY = {  # how visible a metric is in a 2-minute video (5 = in every highlight), for ranking the improvements
    "shots_per_match": 5, "shots_per90": 5, "shot_share_goal": 5, "shot_share_saved": 5, "shot_share_blocked": 5, "shot_share_wide": 4,
    "shot_share_woodwork": 4, "shot_speed_kmh": 4, "shot_distance_m": 4, "shot_xg": 3,
    "ball_speed_live_kmh": 4, "pass_speed_kmh": 4, "pass_length_m": 4, "team_length_in_poss_m": 4, "team_length_out_poss_m": 4,
    "team_width_in_poss_m": 4, "team_width_out_poss_m": 4, "top_speed_kmh": 4,
    "live_phase_duration_s": 3, "possession_spell_duration_s": 3, "ball_in_play_share": 3, "passes_per90": 3, "pass_success_own_third": 3,
    "pass_success_middle_third": 3, "pass_success_final_third": 3, "sprints_per90": 3, "zone_25_plus_per90_m": 3, "accelerations_per90": 3,
    "decelerations_per90": 3, "corners_per90": 3, "fouls_per90": 3, "cards_per90": 3, "free_kicks_per90": 3, "throw_ins_per90": 3,
    "possession_dominant_share": 2, "peak_5min_m": 2, "distance_per90_m": 2, "distance_above_20kmh_per90_m": 2, "zone_0_7_per90_m": 2,
    "zone_7_15_per90_m": 2, "zone_15_20_per90_m": 2, "zone_20_25_per90_m": 2, "goal_kicks_per90": 2, "offsides_per90": 2,
    "tackles_per90": 2, "recoveries_per90": 2, "clearances_per90": 2,
}
SEVERITY = {"red": 2, "yellow": 1, "green": 0, None: 0}
SAME_ISSUE = {"shots_per90": "shots_per_match"}  # metrics that measure the same problem share one improvement item


def improvement_ranking(metrics: dict[str, dict]) -> list[dict]:
    """Red and yellow metrics, one item per metric name (physical role groups merged; ``SAME_ISSUE`` names merged), ranked
    by video visibility, then severity, then the number of role groups affected, then the largest D."""
    items: dict[str, dict] = {}
    for key, m in metrics.items():
        if m["light"] not in ("red", "yellow"):
            continue
        name = SAME_ISSUE.get(m["name"], m["name"])
        it = items.setdefault(name, {"name": name, "group": m["group"], "keys": [], "role_groups": [], "worst": "yellow",
                                     "max_ks_d": None, "visibility": VISIBILITY.get(name, 1), "directions": []})
        it["keys"].append(key)
        if m["role_group"]:
            it["role_groups"].append(m["role_group"])
        if m["light"] == "red":
            it["worst"] = "red"
        d = m["synthetic"]["ks_d"]
        if d is not None:
            it["max_ks_d"] = d if it["max_ks_d"] is None else max(it["max_ks_d"], d)
        it["directions"].append(m["synthetic"]["direction"])
    ranked = sorted(items.values(), key=lambda i: (-i["visibility"], -SEVERITY[i["worst"]], -len(i["keys"]), -(i["max_ks_d"] or 0.0), i["name"]))
    for k, it in enumerate(ranked, 1):
        it["rank"] = k
    return ranked


def mean_positions(sums: dict, position_maps: dict | None) -> dict:
    """Synthetic mean position per role and possession state vs the DFL baseline of ``position_maps.json``."""
    out = {}
    for role in sorted(sums):
        row = {}
        for state in ("in_possession", "out_of_possession"):
            n, sx, sy = sums[role][state]
            syn = [sx / n, sy / n] if n else None
            base = None
            if position_maps and role in position_maps.get("maps", {}):
                base = position_maps["maps"][role][state]["baseline"]
            dist = math.hypot(syn[0] - base[0], syn[1] - base[1]) if (syn and base) else None
            row[state] = {"n_samples": int(n), "synthetic_mean_xy_m": syn, "dfl_baseline_xy_m": base, "distance_m": dist}
        out[role] = row
    return out


def reference_levels(reference: dict) -> list[float]:
    """Levels (fractions 0..1) of the reference quantiles, from ``quantile_levels_pct``."""
    return [float(p) / 100.0 for p in reference["quantile_levels_pct"]]


def calibration_config_of(reference: dict) -> CalibrationConfig:
    """The ``CalibrationConfig`` recorded in the reference's meta (tuples restored), or the default."""
    conf = (reference.get("meta") or {}).get("config")
    if not conf:
        return DEFAULT_CAL_CONFIG
    known = set(CalibrationConfig.__dataclass_fields__)
    return CalibrationConfig(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in conf.items() if k in known})


def realism_report(match_id: str = "SYN-001", replicas: int = 6, *, repo: Path = REPO, work_dir: Path | None = None,
                   reference_path: Path | None = None, position_maps_path: Path | None = None, out_dir: Path | None = None,
                   run_qc: bool = True, write: bool = True, cfg: RealismConfig | None = None, log=print) -> dict:
    """Linesman realism report of ``match_id`` and its ``replicas`` against the DFL reference.

    Returns the content of ``realism.json``; with ``write`` also writes it, the figures and the README to
    ``out_dir`` (default ``reports/realism/``).
    """
    repo = Path(repo)
    work_dir = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    reference_path = Path(reference_path) if reference_path else repo / "calibration" / "realism_reference.json"
    position_maps_path = Path(position_maps_path) if position_maps_path else repo / "calibration" / "position_maps.json"
    out_dir = Path(out_dir) if out_dir else repo / "reports" / "realism"
    reference = json.loads(reference_path.read_text())
    if cfg is not None:
        rcfg = cfg
    elif "realism_config" in reference["meta"]:
        rcfg = RealismConfig.from_dict(reference["meta"]["realism_config"])  # the reference's parameters, so both sides match
    else:
        rcfg = DEFAULT_REALISM_CONFIG
    cal_cfg = calibration_config_of(reference)  # the same calibration parameters as the reference (e.g. min_valid_minutes)
    catalogue = {m.key: m for m in metric_catalogue(cal_cfg, rcfg)}
    levels = reference_levels(reference)

    per_match: dict[str, dict[str, np.ndarray]] = {}
    pos_sums: dict = {}
    inputs: dict = {}
    woodwork: dict = {}
    crosstabs: list[dict] = []
    for mid, folder in synthetic_matches(match_id, replicas, repo):
        done = ensure_prepared(mid, folder, work_dir, repo, run_qc, log)
        md = load_match(work_dir, mid)
        per_match[mid] = match_samples(md, cal_cfg, rcfg)
        for role, st in role_position_sums(md).items():
            acc = pos_sums.setdefault(role, {"in_possession": [0, 0.0, 0.0], "out_of_possession": [0, 0.0, 0.0]})
            for state, (n, sx, sy) in st.items():
                acc[state] = [acc[state][0] + n, acc[state][1] + sx, acc[state][2] + sy]
        woodwork[mid] = woodwork_diagnosis(md)
        crosstabs.append(shot_outcome_crosstab(md))
        inputs[mid] = {"input_hashes": md.input_hashes, "prep_config_hash": md.prep_config_hash,
                       "pipeline_version": (md.qc_results or {}).get("pipeline_version"), "provider": md.profile.get("provider")}
        log(f"{mid}: {done}, metrics computed")
        del md

    metrics = {}
    for key, ref in reference["metrics"].items():
        if key not in catalogue:
            continue
        pooled = np.concatenate([per_match[mid][key] for mid in per_match]) if per_match else np.array([])
        metrics[key] = compare_metric(ref, pooled, per_match[match_id][key], rcfg, levels)
    counts = {lab: sum(m["light"] == lab for m in metrics.values()) for lab in LIGHTS}
    counts_syn = {lab: sum(m["syn_match"]["light"] == lab for m in metrics.values()) for lab in LIGHTS}
    # Red or yellow only because of D, with D not above the chance level and the median inside the DFL range: read with care.
    within_noise = sorted(k for k, m in metrics.items() if m["light"] in ("red", "yellow") and m["synthetic"]["median_position"] == "inside"
                          and m["synthetic"]["ks_d"] is not None and m["synthetic"]["ks_d"] <= m["synthetic"]["ks_d_noise95"])
    by_group = {g: {lab: sum(m["light"] == lab for m in metrics.values() if m["group"] == g) for lab in LIGHTS} for g in GROUPS}
    pmaps = json.loads(position_maps_path.read_text()) if position_maps_path.exists() else None
    ref_meta = reference["meta"]
    prep_hashes = sorted({v for v in (ref_meta.get("prep_config_hash") or {}).values() if v})
    syn_hashes = sorted({v["prep_config_hash"] for v in inputs.values() if v["prep_config_hash"]})
    result = {
        "meta": {
            "description": f"Linesman realism report: {match_id} and {replicas} replicas against the DFL reference (guidelines v4, section 12)",
            "match_id": match_id,
            "synthetic_matches": list(per_match),
            "generated_by": f"cd data_pipeline && .venv/bin/python -m insidethegame_data.realism {match_id} --replicas {replicas}",
            "guidelines_version": GUIDELINES_VERSION,
            "pipeline_version": __version__,
            "reference": {"file": "calibration/realism_reference.json", "sha256": sha256(reference_path), "matches_used": ref_meta.get("matches_used"),
                          "source": ref_meta.get("source"), "prep_config_hash": prep_hashes, "pipeline_version": ref_meta.get("pipeline_version"),
                          "quantile_levels_pct": reference["quantile_levels_pct"]},
            "same_preparation_config": prep_hashes == syn_hashes,
            "realism_config_hash": rcfg.hash(),
            "realism_config": rcfg.to_dict(),
            "synthetic_inputs": inputs,
            "rules": {
                "ks": "one-sample Kolmogorov-Smirnov distance D = sup |F_syn(x) - F_dfl(x)|; F_dfl is the piecewise-linear CDF through the "
                      f"{len(levels)} DFL quantiles at meta.reference.quantile_levels_pct (atoms where quantiles repeat), 0 below the lowest and 1 from the "
                      f"highest; D is exact for that CDF (evaluated at every sample value and knot). The reference has no minimum or maximum "
                      f"(privacy), so against the full DFL distribution D can differ by at most {max(levels[0], 1.0 - levels[-1]):g} in the tails "
                      f"and by at most the largest spacing of two stored levels ({max(np.diff(levels)):g}) between them",
                "median_rule": "position of the synthetic median relative to the range (min-max) of the DFL per-match medians: inside, "
                               f"just_outside (within {100 * rcfg.range_tolerance_frac:g} % of the range width) or far_outside",
                "traffic_light": f"green = D <= {rcfg.green_ks_max} and median inside; yellow = D in ({rcfg.green_ks_max}, {rcfg.yellow_ks_max}] "
                                 f"or median just outside; red = D > {rcfg.yellow_ks_max} or median far outside. range_only metrics (one value per "
                                 "match or team-match) use the median rule only",
                "light_sample": "the light of a metric uses the pooled synthetic sample (the match and its replicas); syn_match repeats the "
                                "comparison for the match alone",
                "small_sample": f"ks_and_range metrics with fewer than {rcfg.small_sample_n} values on either side are indicative",
                "ks_d_noise95": f"diagnostic, not part of the light: the two-sample KS critical value {rcfg.ks_noise_c_alpha} * sqrt((n + m) / (n m)), "
                                "the D that two samples of these sizes from the same distribution exceed only 5 % of the time (per-frame samples "
                                "are autocorrelated: their real chance level is higher)",
            },
            "library_versions": _library_versions(),
        },
        "summary": {"n_metrics": len(metrics), "lights": counts, "lights_match_alone": counts_syn, "by_group": by_group,
                    "red_or_yellow_within_noise": within_noise},
        "metrics": metrics,
        "mean_positions": mean_positions(pos_sums, pmaps),
        "shots_diagnostic": {"label_vs_observed_outcome": merge_crosstabs(crosstabs),
                             "dfl_label_vs_observed_outcome": (reference.get("diagnostics") or {}).get("shot_outcome_label_vs_observed"),
                             "woodwork_shots": woodwork,
                             "note": "label = the outcome label (simulation or provider annotation, R-SHOT-COUNTS); observed = the pipeline's outcome "
                                     "from the trajectory (R-SHOT-OUTCOME, R-WOODWORK). A label 'woodwork' observed otherwise is a woodwork hit the "
                                     "pipeline does not recognise; woodwork_shots gives the nearest approach of the ball samples to the woodwork."},
        "improvements": improvement_ranking(metrics),
    }
    result = json.loads(dumps(result, rcfg.float_decimals))  # rounded, JSON-clean
    if write:
        from .realism_output import write_outputs

        write_outputs(result, reference, per_match, out_dir, rcfg, log=log)
    return result


def _library_versions() -> dict:
    import matplotlib
    import pandas
    import scipy

    return {"numpy": np.__version__, "pandas": pandas.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__,
            "insidethegame_data": __version__}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Linesman: realism report of a synthetic match and its replicas against the DFL reference.")
    ap.add_argument("match_id", help="synthetic match id, e.g. SYN-001 (folder synthetic/<id>/)")
    ap.add_argument("--replicas", type=int, default=6, help="replicas synthetic/replicas/<id>-r1..rN to include (default 6)")
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--work-dir", type=Path, default=None, help="git-ignored folder for the pipeline outputs (default reports/qc/frames/_realism)")
    ap.add_argument("--reference", type=Path, default=None, help="DFL reference (default calibration/realism_reference.json)")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default reports/realism)")
    ap.add_argument("--no-qc", action="store_true", help="do not prepare missing matches (fail instead)")
    a = ap.parse_args(argv)
    res = realism_report(a.match_id, a.replicas, repo=a.repo, work_dir=a.work_dir, reference_path=a.reference, out_dir=a.out, run_qc=not a.no_qc)
    s = res["summary"]["lights"]
    print(f"{a.match_id}: {res['summary']['n_metrics']} metrics - green {s['green']}, yellow {s['yellow']}, red {s['red']}")


if __name__ == "__main__":
    main()
