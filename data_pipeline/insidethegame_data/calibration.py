"""Calibration aggregates for the synthetic generator (guidelines v4, section 11).

Reads the **frame-level outputs of the preparation pipeline** (git-ignored
``reports/qc/frames/<id>/v4/``) plus the committed ``match_profile.json`` and
``qc_results.json`` of every DFL match, and writes aggregate-only JSON files to
``calibration/`` at the repo root. Nothing frame-level or person-level is written:
person IDs are used in memory only, to group samples into player-matches.

Files (the contract with the generator, ``generator/insidethegame_gen/match/``):

* ``physical_profiles.json`` - per role group: distance and its splits, distance per
  FIFA speed zone, top speed, sprints, acceleration peak, distance per 5 minutes;
* ``position_maps.json`` - mean positions per role conditioned on the ball cell and
  on possession, in team-attacking-direction coordinates;
* ``event_model.json`` - event frequencies, pass length / success / direction by
  zone, zone transitions, shots, set pieces, possession, ball in play;
* ``ball.json`` - pass, long-pass and shot speeds, circulation speed, air time; ``pass_speed_ms``
  also stores ``p99``, the 99th percentile that ``R-SHOT-POWER`` reads (guidelines 9.7, 11);
* ``xg_model.json`` - the logistic xG model of ``Materials/METRICS.md`` section 9 (v4.2):
  ``logit(xG) = a + 2.84 * theta + b * n`` with the visible-angle slope 2.84 **fixed** (Soccermatics angle-only fit
  on the open Wyscout data, Pappalardo et al. 2019, CC BY 4.0) and only ``a`` and ``b`` (opponents in the shot
  triangle) fitted on the located open-play DFL shots; a penalty is a fixed 0.79;
* ``attribute_scales.json`` - tables to map 1-99 attributes (v4.2): each stores only the **21 quantiles** p2.5,
  p5, p10, ..., p95, p97.5 (no minimum or maximum, as ``realism_reference.json``); the 99 ``values`` the generator
  reads are interpolated from them, flat below p2.5 and above p97.5, so no value is a single real player's;
* ``artefact_profile.json`` - provider artefact rates (guidelines section 11, last bullet), including the
  ``R-ACC-BAND`` acceleration spikes (noise / approx runs by sign, per match and pooled);
* ``realism_reference.json`` - the DFL reference of the Linesman realism report (guidelines section 12):
  for every metric of ``realism_metrics.py``, 21 quantiles (p2.5, p5, p10, ..., p90, p95, p97.5; no
  minimum or maximum), ``n`` and the range of the per-match medians. Read by ``realism.py``.

Rules applied (guidelines v4):

* **valid samples only** (``valid_kinematics``); estimated gap samples never count for
  speed, peaks or per-90 values; accelerations of ``acc_status = noise`` samples are never
  used (``R-ACC-BAND``);
* events located by any kick method (ground touch, aerial touch, soft kick, goal backwards,
  restart transition) count as located;
* **per-90 values are normalised on measured (valid) time** (team decision
  2026-10-24): the generator must reproduce the true pace of play, and synthetic
  data is fully measured. The share of time on the pitch that was measured is
  reported as ``coverage`` (app statistics keep the classic "whole time on the
  pitch" convention with the "≈" precision rule; that is ``metrics.py``);
* ball status and possession are the provider's, never derived;
* event locations come from the synchronised frame where the event was located,
  otherwise from the provider's annotated coordinates converted to the canonical
  frame (the share of each is reported);
* only **DFL** matches are used: synthetic matches are never calibration input.

Determinism: no wall-clock time and no randomness; floats are rounded; two runs on
the same inputs give byte-identical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import __version__
from .config import DEFAULT_CONFIG as PREP_CONFIG
from .config import FRAMES_VERSION, GUIDELINES_VERSION

REPO = Path(__file__).resolve().parents[2]
DOI = "10.1038/s41597-025-04505-y"
SOURCE = ("DFL/Sportec open data: Bassek, M., Rein, R., Weber, H., Memmert, D. (2025). An integrated dataset of spatiotemporal "
          "and event data in elite soccer. Scientific Data 12:195. https://doi.org/10.1038/s41597-025-04505-y. Licence: CC-BY 4.0. "
          "Aggregate statistics only; no raw, frame-level or person-level data.")
FILES = ("physical_profiles.json", "position_maps.json", "event_model.json", "ball.json", "xg_model.json",
         "attribute_scales.json", "artefact_profile.json", "realism_reference.json")

# ---------------------------------------------------------------------------
# Roles and role groups (English codes, guidelines section 16)
# ---------------------------------------------------------------------------

ROLES = ["GK", "LB", "LCB", "CB", "RCB", "RB", "LWB", "RWB", "CDM", "LDM", "RDM", "LCM", "CM", "RCM", "LM", "RM",
         "CAM", "LW", "RW", "ST", "LS", "RS"]
GROUPS = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
# Calibration groups. LM/RM are placed in W (wide players), not CM: this matches the reviewed mapping
# file (reference/dfl_to_english.yaml, guidelines 16.1) and, on DFL, the only LM/RM play on the whole
# flank (mean y_att +-17-19 m) with winger-like high-speed running (team decision 2026-10-24).
CAL_GROUP = {"GK": "GK", "LCB": "CB", "CB": "CB", "RCB": "CB", "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB",
             "CDM": "DM", "LDM": "DM", "RDM": "DM", "LCM": "CM", "CM": "CM", "RCM": "CM", "LM": "W", "RM": "W",
             "CAM": "AM", "LW": "W", "RW": "W", "ST": "ST", "LS": "ST", "RS": "ST", "CF": "ST"}
# Mirror-symmetric roles: right role -> left partner. In attacking coordinates the team attacks +x and
# left is +y (a left back's mean y_att is about +18 m on DFL).
MIRROR = {"RB": "LB", "RCB": "LCB", "RWB": "LWB", "RDM": "LDM", "RCM": "LCM", "RM": "LM", "RW": "LW", "RS": "LS"}
LEFT_ROLES = {v: k for k, v in MIRROR.items()}  # left -> right partner
CENTRAL = ("GK", "CB", "CDM", "CM", "CAM", "ST")
ROLE_ALIAS = {"CF": "ST"}  # roles outside the contract list use the map of this role
CANON = [r for r in ROLES if r in CENTRAL or r in LEFT_ROLES]  # roles that are accumulated (left or central)

ACTION_TYPES = ("Play", "ShotAtGoal", "OtherBallAction", "BallClaiming", "ThrowIn", "FreeKick", "CornerKick", "GoalKick", "KickOff", "Penalty")
RESTART_TYPES = ("ThrowIn", "FreeKick", "CornerKick", "GoalKick", "KickOff", "Penalty")
SET_PIECES = ("CornerKick", "FreeKick", "ThrowIn", "GoalKick", "Penalty")
PASS_COLS = ["match", "located", "high_annotated", "evaluation", "zone", "length", "success", "angle", "speed", "air", "airborne", "side"]
SHOT_COLS = ["match", "outcome", "head", "goal", "located", "zone", "distance_m", "visible_angle_rad", "opponents_in_triangle", "confirmed",
             "trajectory_goal", "speed", "invalid_opponents_near", "player"]
SHOT_OUTCOMES = ("SuccessfulShot", "SavedShot", "ShotWide", "BlockedShot", "ShotWoodWork", "OtherShot")
# Team attribute of each event type (DFL names): first key present wins.
TEAM_KEYS = ("Team", "WinnerTeam", "TeamFouler")


@dataclass(frozen=True)
class CalibrationConfig:
    """Every calibration parameter (recorded and hashed in each output)."""

    min_valid_minutes: float = 60.0  # player-matches with fewer valid minutes are left out of the physical profiles
    speed_zone_edges_kmh: tuple[float, ...] = (7.0, 15.0, 20.0, 25.0)  # FIFA zones 0-7, 7-15, 15-20, 20-25, 25+
    speed_zone_labels: tuple[str, ...] = ("0-7", "7-15", "15-20", "20-25", "25+")
    sprint_speed_kmh: float = 25.0  # a sprint: valid samples above 25 km/h ...
    sprint_min_s: float = 1.0  # ... for at least 1 s
    work_rate_speed_kmh: float = 20.0  # work rate = distance above 20 km/h per 90
    bin_s: float = 300.0  # 5-minute bins of the match clock
    bins_per_period: int = 9  # 0-45 min per period; stoppage time is not binned
    bin_min_presence_frac: float = 0.95  # the player is on the pitch for >= 95 % of the bin ...
    bin_min_valid_frac: float = 0.90  # ... and >= 90 % of the bin is measured
    std_pitch_length_m: float = 105.0  # maps and zones are expressed on a 105 x 68 m pitch
    std_pitch_width_m: float = 68.0
    pos_grid_nx: int = 12
    pos_grid_ny: int = 8
    pos_min_count: int = 50  # samples per cell below which a cell falls back to the role-group shift
    zone_nx: int = 6
    zone_ny: int = 4
    direction_sectors: int = 8
    long_pass_m: float = 30.0
    kick_speed_window_s: float = 0.5  # peak ball speed in the 0.5 s after the kick (METRICS.md, shot speed)
    air_rise_m: float = 1.0  # a pass is airborne once the ball is above 1 m ...
    air_landing_m: float = 0.5  # ... and lands when it is back below 0.5 m
    progression_min_s: float = 3.0  # possession runs used for the progression speed
    passing_min_passes: int = 20  # located passes per player-match for the passing scale
    percentile_min_n: int = 5  # smallest n for a percentile table
    # attribute_scales (v4.2): only these 21 quantiles are stored (as realism_reference.json, no minimum or maximum);
    # the 99 values p1..p99 are interpolated from them, flat below the first and above the last level.
    attribute_quantile_levels_pct: tuple[float, ...] = (2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0,
                                                        70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 97.5)
    # xG (v4.2, Materials/METRICS.md section 9): logit(xG) = a + xg_angle_slope * theta + b * n. The angle slope is fixed
    # (an offset); only a and b are fitted, with xg_l2 on the standardised b (intercept not penalised).
    xg_angle_slope: float = 2.84
    xg_angle_slope_source: str = ("Soccermatics angle-only logistic fit, logit(xG) = -3.52 + 2.84 * theta, on 8,451 shots of the 2017/18 Premier League "
                                  "in the open Wyscout data (Pappalardo et al. 2019, Scientific Data 6:236, doi:10.1038/s41597-019-0247-7, CC BY 4.0)")
    penalty_xg: float = 0.79  # fixed value of a penalty (Opta, as reported by FBref)
    xg_l2: float = 1.0  # L2 penalty on standardised coefficients (intercept not penalised)
    xg_min_shots: int = 20  # the model is fitted only with at least this many located shots and at least one goal and one miss
    xg_max_iter: int = 100
    xg_tol: float = 1e-10
    xg_bins: tuple[float, ...] = (0.0, 0.05, 0.1, 0.2, 0.35, 1.0)
    float_decimals: int = 4

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


DEFAULT_CAL_CONFIG = CalibrationConfig()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def stats(values) -> dict:
    """STATS block: n, mean, sd (ddof 1), p10, p25, p50, p75, p90 (linear percentiles)."""
    v = pd.to_numeric(pd.Series(values if isinstance(values, (np.ndarray, pd.Series)) else list(values), dtype=object), errors="coerce").to_numpy(float)
    v = v[np.isfinite(v)]
    keys = ("p10", "p25", "p50", "p75", "p90")
    if len(v) == 0:
        return {"n": 0, "mean": None, "sd": None, **{k: None for k in keys}}
    q = np.percentile(v, [10, 25, 50, 75, 90])
    return {"n": int(len(v)), "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None,
            **{k: float(x) for k, x in zip(keys, q)}}


def percentile_table(values, min_n: int) -> dict | None:
    """p1..p99 (linear): ``values[k-1]`` is the k-th percentile, i.e. the physical value of attribute k."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < min_n:
        return None
    return {"n": int(len(v)), "values": [float(x) for x in np.percentile(v, np.arange(1, 100))]}


def quantile_scale_table(values, min_n: int, levels_pct) -> dict | None:
    """Attribute scale (v4.2): only the quantiles of ``values`` at ``levels_pct`` (p2.5 .. p97.5, linear) are kept; the 99
    ``values`` (``values[k-1]`` = the physical value of attribute k, the format the generator reads) are interpolated
    linearly from them, flat below the first level and above the last. No minimum or maximum is stored. Known limit,
    accepted (guidelines section 19): a stored quantile coincides with one player-match value at the levels returned by
    :func:`exact_order_statistic_levels`."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < min_n:
        return None
    levels = np.asarray(levels_pct, float)
    q = np.percentile(v, levels)
    return {"n": int(len(v)), "quantile_levels_pct": [float(x) for x in levels], "quantiles": [float(x) for x in q],
            "values": [float(x) for x in np.interp(np.arange(1, 100, dtype=float), levels, q)]}


def exact_order_statistic_levels(n: int, levels_pct) -> list[float]:
    """Levels (in %) at which the linear quantile of ``n`` values falls exactly on one value: its position
    ``p * (n - 1) / 100`` is a whole number (for example the median of an odd n). Exact arithmetic, no rounding."""
    if not n or n < 1:
        return []
    out = []
    for p in levels_pct:
        pos = Fraction(str(float(p))) * (int(n) - 1) / 100
        if pos.denominator == 1:
            out.append(float(p))
    return out


def attribute_to_value(table: dict, attribute: float) -> float:
    """1-99 attribute -> physical value (linear between integer percentiles; clipped to 1..99)."""
    a = float(np.clip(attribute, 1, 99))
    return float(np.interp(a, np.arange(1, 100), table["values"]))


def value_to_attribute(table: dict, value: float) -> float:
    """Physical value -> 1-99 attribute (inverse of ``attribute_to_value``; flat stretches take their lowest attribute)."""
    vals = np.asarray(table["values"], float)
    ks = np.arange(1, 100, dtype=float)
    if value <= vals[0]:
        return 1.0
    if value >= vals[-1]:
        return 99.0
    i = int(np.searchsorted(vals, value, side="left"))
    lo, hi = vals[i - 1], vals[i]
    return float(ks[i - 1] + (value - lo) / (hi - lo)) if hi > lo else float(ks[i])


def run_starts(mask: np.ndarray, contiguous: np.ndarray) -> np.ndarray:
    """Run ids (1-based, 0 outside) of True runs that only continue across contiguous samples."""
    prev = np.r_[False, mask[:-1]]
    start = mask & ~(prev & contiguous)
    ids = np.cumsum(start)
    return np.where(mask, ids, 0)


def count_runs(mask: np.ndarray, contiguous: np.ndarray, min_len: int) -> int:
    ids = run_starts(mask, contiguous)
    if not ids.any():
        return 0
    lengths = np.bincount(ids[ids > 0])[1:]
    return int((lengths >= min_len).sum())


def visible_angle(x_from_line: np.ndarray, y: np.ndarray, goal_width: float) -> np.ndarray:
    """Angle (rad) between the lines from the ball to the two posts (METRICS.md section 9)."""
    x = np.maximum(np.asarray(x_from_line, float), 0.0)
    y = np.asarray(y, float)
    return np.arctan2(goal_width * x, x ** 2 + y ** 2 - (goal_width / 2) ** 2)


def in_triangle(px, py, a, b, c) -> np.ndarray:
    """Points inside (or on the edge of) triangle abc (sign test)."""
    px, py = np.asarray(px, float), np.asarray(py, float)

    def s(p1, p2):
        return (px - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (py - p2[1])

    d1, d2, d3 = s(a, b), s(b, c), s(c, a)
    neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return ~(neg & pos)


def shot_features(ball_x_att: float, ball_y_att: float, opp_x_att, opp_y_att, pitch_length_m: float, goal_width_m: float) -> dict:
    """xG features at the shot frame, in the shooting team's attacking coordinates (goal at x = +L/2).

    ``opp_*``: opposing **outfield** players with valid positions at that frame.
    """
    gx = pitch_length_m / 2
    x_line = max(gx - ball_x_att, 0.0)
    n = int(in_triangle(opp_x_att, opp_y_att, (ball_x_att, ball_y_att), (gx, goal_width_m / 2), (gx, -goal_width_m / 2)).sum()) if len(opp_x_att) else 0
    return {"distance_m": float(math.hypot(x_line, ball_y_att)), "visible_angle_rad": float(visible_angle(x_line, ball_y_att, goal_width_m)),
            "opponents_in_triangle": n}


def fit_logistic_l2(X: np.ndarray, y: np.ndarray, l2: float, max_iter: int = 100, tol: float = 1e-10, offset: np.ndarray | None = None) -> dict:
    """L2-penalised logistic regression by Newton's method (deterministic).

    Features are standardised; the intercept is not penalised. Minimises
    sum(log loss) + l2/2 * ||b||^2 on the standardised coefficients and returns the
    coefficients in raw units. ``offset``: a fixed term added to the linear predictor
    (v4.2 xG: the angle term 2.84 * theta with its slope fixed).
    """
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    off = np.zeros(len(X)) if offset is None else np.asarray(offset, float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    pen = np.r_[0.0, np.full(X.shape[1], l2)]
    beta = np.zeros(Z.shape[1])
    it = 0
    for it in range(1, max_iter + 1):
        p = 1.0 / (1.0 + np.exp(-(Z @ beta + off)))
        g = Z.T @ (p - y) + pen * beta
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(pen)
        step = np.linalg.solve(H, g)
        beta = beta - step
        if float(np.max(np.abs(step))) < tol:
            break
    coef = beta[1:] / sd
    intercept = float(beta[0] - np.sum(beta[1:] * mu / sd))
    return {"coef": coef, "intercept": intercept, "coef_standardised": beta[1:], "intercept_standardised": float(beta[0]),
            "feature_mean": mu, "feature_sd": sd, "iterations": it}


def predict_logistic(X: np.ndarray, coef, intercept: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(np.asarray(X, float) @ np.asarray(coef, float) + intercept)))


@lru_cache(maxsize=4)
def _xg_model(path: str) -> dict:
    return json.loads(Path(path).read_text())


def xg(distance_m: float, visible_angle_rad: float, opponents_in_triangle: int, model_path: Path | None = None) -> float:
    """Expected goals of one open-play shot from the committed model ``calibration/xg_model.json``."""
    m = _xg_model(str(model_path or (REPO / "calibration" / "xg_model.json")))
    z = m["intercept"] + sum(c * v for c, v in zip(m["coef"], (distance_m, visible_angle_rad, opponents_in_triangle)))
    return float(1.0 / (1.0 + math.exp(-z)))


def _round(o, nd: int):
    if isinstance(o, dict):
        return {str(k): _round(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v, nd) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), nd) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(o, np.ndarray):
        return _round(o.tolist(), nd)
    return o


_SCALAR = r'(?:-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|null|true|false|"[^"\n\\]*")'
_FLAT_LIST = re.compile(r"\[\n\s*(" + _SCALAR + r"(?:,\n\s*" + _SCALAR + r")*)\n\s*\]")


def dumps(obj, nd: int = 4) -> str:
    """Stable JSON: rounded floats, insertion key order, flat lists of scalars on one line."""
    s = json.dumps(_round(obj, nd), indent=2, ensure_ascii=False)
    s = _FLAT_LIST.sub(lambda m: "[" + ", ".join(p.strip() for p in re.split(r",\n\s*", m.group(1))) + "]", s)
    return s + "\n"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Loading one prepared match
# ---------------------------------------------------------------------------

@dataclass
class BallTrack:
    """Ball of one period, indexed by ``frame - f0`` (canonical coordinates)."""

    f0: int
    t_s: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    speed: np.ndarray
    valid: np.ndarray
    live: np.ndarray
    poss: np.ndarray  # 0 none, 1 home, 2 away

    def index(self, frames) -> np.ndarray:
        """Row index of each frame, -1 when the frame is outside the period."""
        i = np.asarray(frames, dtype=np.int64) - self.f0
        return np.where((i >= 0) & (i < len(self.x)), i, -1)

    def frame_at(self, t: float) -> int:
        return int(self.f0 + np.clip(np.searchsorted(self.t_s, t), 0, len(self.t_s) - 1))


@dataclass
class MatchData:
    match_id: str
    profile: dict
    fs: float
    L: float
    W: float
    rot: float  # +1 / -1: raw -> canonical rotation
    home_sign: dict[int, float]  # +1 if home attacks +x (canonical) in the period
    kickoff_s: dict[int, float]
    clock_offset_s: dict[int, float]
    players: pd.DataFrame
    ball: dict[int, BallTrack]
    events: pd.DataFrame
    role_of: dict[str, str]
    team_side_of: dict[str, str]
    input_hashes: dict[str, str]
    prep_config_hash: str | None = None
    qc_results: dict | None = None
    sx_ev: float = 1.0
    sy_ev: float = 1.0
    event_centre: tuple[float, float] = (0.0, 0.0)
    notes: list[str] = field(default_factory=list)

    def attack_sign(self, period: int, side: str) -> float:
        return self.home_sign[int(period)] * (1.0 if side == "home" else -1.0)

    def provider_to_att(self, x, y, period: int, side: str) -> tuple[float, float]:
        """Annotated event coordinates -> canonical -> the acting team's attacking coordinates."""
        xc = self.rot * self.sx_ev * (float(x) - self.event_centre[0])
        yc = self.rot * self.sy_ev * (float(y) - self.event_centre[1])
        s = self.attack_sign(period, side)
        return s * xc, s * yc


PLAYER_COLS = ["period", "frame", "t_s", "object_id", "object_type", "team_side", "x_m", "y_m", "z_m", "ball_status", "ball_possession",
               "speed_ms", "acc_tan_ms2", "valid_kinematics"]
# Events located from trajectories by a kick (guidelines v4, section 9.5); any of them counts as located.
LOCATED_KICK_METHODS = ("kick_signature", "aerial_signature", "soft_kick_signature", "goal_backwards", "restart_transition")


def frames_dir(repo: Path, match_id: str) -> Path:
    return Path(repo) / "reports" / "qc" / "frames" / match_id / FRAMES_VERSION


def available_matches(repo: Path) -> list[str]:
    """Matches with frame-level outputs and a **DFL** match profile (synthetic matches are never calibration input)."""
    out = []
    for d in sorted((Path(repo) / "reports" / "qc").glob("*/match_profile.json")):
        mid = d.parent.name
        if not (frames_dir(repo, mid) / "tracking_canonical.parquet").exists():
            continue
        if str(json.loads(d.read_text()).get("provider", "")).startswith("DFL"):
            out.append(mid)
    return out


def load_match(repo: Path, match_id: str) -> MatchData:
    repo = Path(repo)
    fdir = frames_dir(repo, match_id)
    prof_path = repo / "reports" / "qc" / match_id / "match_profile.json"
    qc_path = repo / "reports" / "qc" / match_id / "qc_results.json"
    tr_path, ev_path = fdir / "tracking_canonical.parquet", fdir / "events_canonical.csv"
    profile = json.loads(prof_path.read_text())
    qc_results = json.loads(qc_path.read_text()) if qc_path.exists() else None
    hashes = {p.name: sha256(p) for p in (tr_path, ev_path, prof_path)}

    cols = PLAYER_COLS + (["acc_status"] if "acc_status" in pq.read_schema(tr_path).names else [])
    tbl = pq.read_table(tr_path, columns=cols)
    df = tbl.to_pandas(strings_to_categorical=True)
    if "acc_status" in df:
        # R-ACC-BAND: the acceleration of a noise sample is never used.
        df.loc[(df["acc_status"] == "noise").to_numpy(), "acc_tan_ms2"] = np.nan
    is_ball = (df["object_type"] == "ball").to_numpy()
    live = (df["ball_status"] == "live").to_numpy()
    poss = np.select([(df["ball_possession"] == "home").to_numpy(), (df["ball_possession"] == "away").to_numpy()], [1, 2], 0).astype(np.int8)
    fs = float(profile["fs_hz"]["1"])

    ball: dict[int, BallTrack] = {}
    bdf = pd.DataFrame({"period": df.loc[is_ball, "period"].to_numpy(), "frame": df.loc[is_ball, "frame"].to_numpy(), "t_s": df.loc[is_ball, "t_s"].to_numpy(),
                        "x": df.loc[is_ball, "x_m"].to_numpy(), "y": df.loc[is_ball, "y_m"].to_numpy(), "z": df.loc[is_ball, "z_m"].to_numpy(),
                        "speed": df.loc[is_ball, "speed_ms"].to_numpy(), "valid": df.loc[is_ball, "valid_kinematics"].to_numpy().astype(bool),
                        "live": live[is_ball], "poss": poss[is_ball]})
    for h, b in bdf.groupby("period", sort=True):
        b = b.sort_values("frame", kind="mergesort")
        f0, f1 = int(b["frame"].min()), int(b["frame"].max())
        n = f1 - f0 + 1
        idx = (b["frame"].to_numpy() - f0).astype(np.int64)

        def arr(col, fill=np.nan, dtype=float):
            a = np.full(n, fill, dtype=dtype)
            a[idx] = b[col].to_numpy()
            return a

        ball[int(h)] = BallTrack(f0=f0, t_s=arr("t_s"), x=arr("x"), y=arr("y"), z=arr("z"), speed=arr("speed"), valid=arr("valid", False, bool),
                                 live=arr("live", False, bool), poss=arr("poss", 0, np.int8))

    players = pd.DataFrame({
        "period": df.loc[~is_ball, "period"].to_numpy().astype(np.int16),
        "frame": df.loc[~is_ball, "frame"].to_numpy().astype(np.int64),
        "t_s": df.loc[~is_ball, "t_s"].to_numpy(),
        "object_id": df.loc[~is_ball, "object_id"].astype(str).to_numpy(),
        "team_side": df.loc[~is_ball, "team_side"].astype(str).to_numpy(),
        "x": df.loc[~is_ball, "x_m"].to_numpy(), "y": df.loc[~is_ball, "y_m"].to_numpy(),
        "speed": df.loc[~is_ball, "speed_ms"].to_numpy(), "acc_tan": df.loc[~is_ball, "acc_tan_ms2"].to_numpy(),
        "valid": df.loc[~is_ball, "valid_kinematics"].to_numpy().astype(bool),
        "live": live[~is_ball], "poss": poss[~is_ball],
    }).sort_values(["object_id", "period", "frame"], kind="mergesort").reset_index(drop=True)
    del df, tbl

    events = pd.read_csv(ev_path, low_memory=False)
    events["attrs"] = events["attrs_json"].map(lambda s: json.loads(s) if isinstance(s, str) else {})
    for c in ("time_utc", "sync_time_utc"):
        events[c] = pd.to_datetime(events[c], utc=True, format="ISO8601")

    # R-ANON-LABELS (v4.2): the committed profile of a real match names people by anonymous labels; the git-ignored
    # label -> ID table maps them back to the IDs of the frame-level files (in memory only).
    from .anonymise import load_label_map

    to_id = load_label_map(fdir.parent.parent, match_id)  # reports/qc/frames/<id>/id_labels.json
    role_of = {to_id.get(r["person_id"], r["person_id"]): r["role"] for r in profile.get("lineup_roles", [])}
    side_of_person = {to_id.get(r["person_id"], r["person_id"]): r["team_role"] for r in profile.get("lineup_roles", [])}
    votes: dict[str, Counter] = {}
    for team, pid in zip(events["team_id"], events["player_id"]):
        if isinstance(team, str) and isinstance(pid, str) and pid in side_of_person:
            votes.setdefault(team, Counter())[side_of_person[pid]] += 1
    team_side_of = {t: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for t, c in votes.items()}

    rot = -1.0 if int(profile["canonical_transform"]["rotation_deg"]) == 180 else 1.0
    home_sign = {int(h): rot * (1.0 if d["home_attacks_positive_x"] else -1.0) for h, d in profile["direction_per_half"].items()}
    kickoff_s = {int(h): pd.Timestamp(v["kickoff_utc"]).timestamp() for h, v in profile["kickoff_per_period"].items()}
    offsets = {int(h): float(v.get("event_clock_offset_s") or 0.0) for h, v in profile["event_clock_offset"].items()}
    best = profile["event_to_tracking_transform"]["best"]
    return MatchData(match_id=match_id, profile=profile, fs=fs, L=float(profile["pitch_length_m"]), W=float(profile["pitch_width_m"]),
                     rot=rot, home_sign=home_sign, kickoff_s=kickoff_s, clock_offset_s=offsets, players=players, ball=ball, events=events,
                     role_of=role_of, team_side_of=team_side_of, input_hashes=hashes,
                     prep_config_hash=(qc_results or {}).get("config_hash"), qc_results=qc_results,
                     sx_ev=float(best["sx"]), sy_ev=float(best["sy"]), event_centre=tuple(profile["centre_spot_in_events"]))


# ---------------------------------------------------------------------------
# Physical profiles (per player-match)
# ---------------------------------------------------------------------------

def player_match_metrics(period: np.ndarray, frame: np.ndarray, t_s: np.ndarray, speed: np.ndarray, acc_tan: np.ndarray, valid: np.ndarray,
                         live: np.ndarray, own_poss: np.ndarray, opp_poss: np.ndarray, kickoff_s: dict[int, float], fs: float,
                         cfg: CalibrationConfig = DEFAULT_CAL_CONFIG) -> dict:
    """Raw totals of one player-match (arrays sorted by period, frame; one row per sample on the pitch).

    Distances are measured distances (valid speed x dT): estimated gap samples count for the app's
    total distance, never for calibration rates, which are normalised on measured time.
    """
    dt = 1.0 / fs
    valid = np.asarray(valid, bool)
    sp = np.where(valid, np.nan_to_num(speed), 0.0)
    vd = sp * dt
    kmh = sp * 3.6
    zone = np.digitize(kmh, cfg.speed_zone_edges_kmh)
    zones = [float(vd[valid & (zone == k)].sum()) for k in range(len(cfg.speed_zone_labels))]
    contiguous = np.r_[False, (np.diff(frame) == 1) & (np.diff(period) == 0)]
    sprints = count_runs(valid & (kmh > cfg.sprint_speed_kmh), contiguous, int(round(cfg.sprint_min_s * fs)))
    vs = speed[valid] if valid.any() else np.array([])
    va = acc_tan[valid] if valid.any() else np.array([])
    # 5-minute bins of the match clock (period 1: bins 0-8; period 2: bins 9-17; stoppage time not binned).
    nb = cfg.bins_per_period
    periods = np.unique(period)
    ko = np.array([kickoff_s.get(int(p), np.nan) for p in periods])
    clock = t_s - ko[np.searchsorted(periods, period)]
    b = np.floor(clock / cfg.bin_s)
    okb = np.isfinite(b) & (b >= 0) & (b < nb) & ((period == 1) | (period == 2))
    gb = (b[okb] + nb * (period[okb] - 1)).astype(np.int64)
    presence = np.bincount(gb, minlength=2 * nb) * dt
    vtime = np.bincount(gb, weights=valid[okb].astype(float), minlength=2 * nb) * dt
    vdist = np.bincount(gb, weights=vd[okb], minlength=2 * nb)
    with np.errstate(invalid="ignore", divide="ignore"):
        per5 = np.where((presence >= cfg.bin_min_presence_frac * cfg.bin_s) & (vtime >= cfg.bin_min_valid_frac * cfg.bin_s), vdist / vtime * cfg.bin_s, np.nan)
    return {
        "samples": int(len(valid)), "valid_samples": int(valid.sum()), "minutes_on_pitch": len(valid) * dt / 60.0, "valid_minutes": valid.sum() * dt / 60.0,
        "distance_m": float(vd.sum()), "distance_live_m": float(vd[live].sum()), "distance_dead_m": float(vd[~live].sum()),
        "distance_in_poss_m": float(vd[own_poss].sum()), "distance_out_poss_m": float(vd[~own_poss].sum()),
        "distance_opp_poss_m": float(vd[opp_poss].sum()),
        "zones_m": zones, "sprints": sprints,
        "top_speed_kmh": float(np.nanmax(vs) * 3.6) if np.isfinite(vs).any() else np.nan,
        "acc_peak_ms2": float(np.nanmax(va)) if np.isfinite(va).any() else np.nan,
        "per5_m": per5,
    }


def player_match_table(matches: list[MatchData], cfg: CalibrationConfig) -> pd.DataFrame:
    """One row per player-match with per-90 values on measured time (person IDs in memory only)."""
    rows = []
    for md in matches:
        p = md.players
        bounds = np.flatnonzero(np.r_[True, p["object_id"].to_numpy()[1:] != p["object_id"].to_numpy()[:-1], True])
        for a, b in zip(bounds[:-1], bounds[1:]):
            g = p.iloc[a:b]
            pid = g["object_id"].iat[0]
            role = md.role_of.get(pid, "UNMAPPED_NONE")
            side = g["team_side"].iat[0]
            own = 1 if side == "home" else 2
            poss = g["poss"].to_numpy()
            m = player_match_metrics(g["period"].to_numpy(), g["frame"].to_numpy(), g["t_s"].to_numpy(), g["speed"].to_numpy(), g["acc_tan"].to_numpy(),
                                     g["valid"].to_numpy(), g["live"].to_numpy(), poss == own, poss == 3 - own, md.kickoff_s, md.fs, cfg)
            f = 90.0 / m["valid_minutes"] if m["valid_minutes"] > 0 else np.nan
            row = {"match_id": md.match_id, "player": pid, "team_side": side, "role": role, "group": CAL_GROUP.get(role), **m,
                   "coverage": m["valid_samples"] / m["samples"], "distance_per90_m": m["distance_m"] * f,
                   "distance_live_per90_m": m["distance_live_m"] * f, "distance_dead_per90_m": m["distance_dead_m"] * f,
                   "distance_in_poss_per90_m": m["distance_in_poss_m"] * f, "distance_out_poss_per90_m": m["distance_out_poss_m"] * f,
                   "sprints_per90": m["sprints"] * f, "work_rate_per90_m": (m["zones_m"][3] + m["zones_m"][4]) * f}
            for lab, z in zip(cfg.speed_zone_labels, m["zones_m"]):
                row[f"zone_{lab}_per90_m"] = z * f
            rows.append(row)
    return pd.DataFrame(rows)


def physical_profiles(pm: pd.DataFrame, cfg: CalibrationConfig) -> dict:
    elig = pm.loc[(pm["valid_minutes"] >= cfg.min_valid_minutes) & pm["group"].notna()]
    out = {}
    for g in GROUPS:
        d = elig.loc[elig["group"] == g]
        per5 = np.vstack(d["per5_m"].to_numpy()) if len(d) else np.full((0, 2 * cfg.bins_per_period), np.nan)
        out[g] = {
            "n_player_matches": int(len(d)),
            "roles": dict(sorted(Counter(d["role"]).items())),
            "coverage": stats(d["coverage"].to_numpy()),
            "valid_minutes": stats(d["valid_minutes"].to_numpy()),
            "distance_per90_m": stats(d["distance_per90_m"].to_numpy()),
            "distance_live_per90_m": stats(d["distance_live_per90_m"].to_numpy()),
            "distance_dead_per90_m": stats(d["distance_dead_per90_m"].to_numpy()),
            "distance_in_poss_per90_m": stats(d["distance_in_poss_per90_m"].to_numpy()),
            "distance_out_poss_per90_m": stats(d["distance_out_poss_per90_m"].to_numpy()),
            "speed_zone_per90_m": {lab: stats(d[f"zone_{lab}_per90_m"].to_numpy()) for lab in cfg.speed_zone_labels},
            "top_speed_kmh": stats(d["top_speed_kmh"].to_numpy()),
            "sprints_per90": stats(d["sprints_per90"].to_numpy()),
            "acc_peak_ms2": stats(d["acc_peak_ms2"].to_numpy()),
            "distance_per5min_m": [float(np.nanmean(c)) if np.isfinite(c).any() else None for c in per5.T],
            "distance_per5min_n": [int(np.isfinite(c).sum()) for c in per5.T],
        }
    return out


# ---------------------------------------------------------------------------
# Position maps
# ---------------------------------------------------------------------------

class PositionAccumulator:
    """Sums per (canonical role, possession state, ball cell): count, x, y, x^2, y^2."""

    def __init__(self, cfg: CalibrationConfig):
        self.cfg = cfg
        self.ncell = cfg.pos_grid_nx * cfg.pos_grid_ny
        shape = (len(CANON), 2, self.ncell)
        self.cnt = np.zeros(shape)
        self.sx, self.sy, self.sxx, self.syy = (np.zeros(shape) for _ in range(4))

    def cell(self, bx: np.ndarray, by: np.ndarray) -> np.ndarray:
        c = self.cfg
        ix = np.clip(np.floor((bx + c.std_pitch_length_m / 2) / c.std_pitch_length_m * c.pos_grid_nx), 0, c.pos_grid_nx - 1).astype(np.int64)
        iy = np.clip(np.floor((by + c.std_pitch_width_m / 2) / c.std_pitch_width_m * c.pos_grid_ny), 0, c.pos_grid_ny - 1).astype(np.int64)
        return ix * c.pos_grid_ny + iy

    def add(self, role: str, state: np.ndarray, px, py, bx, by):
        """``role`` as played; right roles are mirrored into their left partner; central roles are added both ways."""
        role = ROLE_ALIAS.get(role, role)
        if role in MIRROR:
            variants = [(MIRROR[role], -1.0)]
        elif role in LEFT_ROLES:
            variants = [(role, 1.0)]
        elif role in CENTRAL:
            variants = [(role, 1.0), (role, -1.0)]
        else:
            return 0
        n = 0
        for canon, fy in variants:
            k = CANON.index(canon)
            yy, byy = fy * py, fy * by
            idx = state.astype(np.int64) * self.ncell + self.cell(bx, byy)
            size = 2 * self.ncell
            for arr, w in ((self.cnt, None), (self.sx, px), (self.sy, yy), (self.sxx, px * px), (self.syy, yy * yy)):
                arr[k] += np.bincount(idx, weights=w, minlength=size).reshape(2, self.ncell)
            n += len(px)
        return n

    def maps(self) -> dict:
        """Role maps (with group fallback), group shift maps, and the source of every cell."""
        c = self.cfg
        nx, ny, minc = c.pos_grid_nx, c.pos_grid_ny, c.pos_min_count
        tot = self.cnt.sum(axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            base_x = self.sx.sum(axis=2) / tot
            base_y = self.sy.sum(axis=2) / tot
            base_sdx = np.sqrt(np.maximum(self.sxx.sum(axis=2) / tot - base_x ** 2, 0))
            base_sdy = np.sqrt(np.maximum(self.syy.sum(axis=2) / tot - base_y ** 2, 0))
        # Roles without any sample (for example CM on DFL): baseline from the group's other roles, centred.
        for k, r in enumerate(CANON):
            for s in range(2):
                if tot[k, s] > 0:
                    continue
                mates = [j for j, q in enumerate(CANON) if CAL_GROUP[q] == CAL_GROUP[r] and tot[j, s] > 0]
                if mates:
                    base_x[k, s] = float(np.mean(base_x[mates, s]))
                    base_y[k, s] = 0.0 if r in CENTRAL else float(np.mean(np.abs(base_y[mates, s])))
                    base_sdx[k, s] = float(np.mean(base_sdx[mates, s]))
                    base_sdy[k, s] = float(np.mean(base_sdy[mates, s]))
        # Group shift maps: displacement from each role's baseline, pooled over the group's roles (left orientation).
        groups = {}
        for g in GROUPS:
            ks = [k for k, r in enumerate(CANON) if CAL_GROUP[r] == g and tot[k].sum() > 0]
            gc = np.zeros((2, self.ncell))
            sdx, sdy, sdxx, sdyy = (np.zeros((2, self.ncell)) for _ in range(4))
            for k in ks:
                bxk, byk = base_x[k][:, None], base_y[k][:, None]
                cnt = self.cnt[k]
                gc += cnt
                sdx += self.sx[k] - cnt * bxk
                sdy += self.sy[k] - cnt * byk
                sdxx += self.sxx[k] - 2 * bxk * self.sx[k] + cnt * bxk ** 2
                sdyy += self.syy[k] - 2 * byk * self.sy[k] + cnt * byk ** 2
            with np.errstate(invalid="ignore", divide="ignore"):
                mdx, mdy = sdx / gc, sdy / gc
                gsx, gsy = np.sqrt(np.maximum(sdxx / gc - mdx ** 2, 0)), np.sqrt(np.maximum(sdyy / gc - mdy ** 2, 0))
            groups[g] = {"count": gc, "mean_dx": mdx, "mean_dy": mdy, "sd_x": gsx, "sd_y": gsy}

        def grid(a):
            return np.asarray(a).reshape(nx, ny)

        role_maps = {}
        for k, r in enumerate(CANON):
            g = groups[CAL_GROUP[r]]
            st = {}
            for s, name in ((0, "in_possession"), (1, "out_of_possession")):
                cnt = self.cnt[k, s]
                with np.errstate(invalid="ignore", divide="ignore"):
                    mx, my = self.sx[k, s] / cnt, self.sy[k, s] / cnt
                    sdx = np.sqrt(np.maximum(self.sxx[k, s] / cnt - mx ** 2, 0))
                    sdy = np.sqrt(np.maximum(self.syy[k, s] / cnt - my ** 2, 0))
                use_role = cnt >= minc
                use_group = ~use_role & (g["count"][s] >= minc)
                src = np.where(use_role, "role", np.where(use_group, "group", "baseline"))
                mx = np.where(use_role, mx, np.where(use_group, base_x[k, s] + g["mean_dx"][s], base_x[k, s]))
                my = np.where(use_role, my, np.where(use_group, base_y[k, s] + g["mean_dy"][s], base_y[k, s]))
                sdx = np.where(use_role, sdx, np.where(use_group, g["sd_x"][s], base_sdx[k, s]))
                sdy = np.where(use_role, sdy, np.where(use_group, g["sd_y"][s], base_sdy[k, s]))
                st[name] = {"mean_x": grid(mx), "mean_y": grid(my), "sd_x": grid(sdx), "sd_y": grid(sdy), "count": grid(cnt).astype(int),
                            "source": grid(src), "baseline": [float(base_x[k, s]), float(base_y[k, s])], "n_samples": int(cnt.sum())}
            role_maps[r] = st
        # Right roles: mirror of the left partner (y -> -y, iy -> ny-1-iy).
        out = {}
        for r in ROLES:
            if r in role_maps:
                out[r] = role_maps[r]
                continue
            left = role_maps[MIRROR[r]]
            out[r] = {}
            for name, m in left.items():
                out[r][name] = {"mean_x": m["mean_x"][:, ::-1], "mean_y": -m["mean_y"][:, ::-1], "sd_x": m["sd_x"][:, ::-1], "sd_y": m["sd_y"][:, ::-1],
                                "count": m["count"][:, ::-1], "source": m["source"][:, ::-1], "baseline": [m["baseline"][0], -m["baseline"][1]],
                                "n_samples": m["n_samples"]}
        fallback = {g: {name: {"mean_dx": grid(v["mean_dx"][s]), "mean_dy": grid(v["mean_dy"][s]), "sd_x": grid(v["sd_x"][s]), "sd_y": grid(v["sd_y"][s]),
                               "count": grid(v["count"][s]).astype(int)} for s, name in ((0, "in_possession"), (1, "out_of_possession"))}
                    for g, v in groups.items()}
        return {"maps": out, "fallback": fallback}


def accumulate_positions(md: MatchData, acc: PositionAccumulator) -> int:
    p = md.players
    sx, sy = acc.cfg.std_pitch_length_m / md.L, acc.cfg.std_pitch_width_m / md.W
    n = 0
    for pid, g in p.groupby("object_id", sort=True):
        role = md.role_of.get(pid)
        if role is None or CAL_GROUP.get(role) is None:
            continue
        side = g["team_side"].iat[0]
        own = 1 if side == "home" else 2
        for h, gh in g.groupby("period", sort=True):
            bt = md.ball.get(int(h))
            if bt is None:
                continue
            bi = bt.index(gh["frame"].to_numpy())
            ok = (bi >= 0) & gh["valid"].to_numpy()
            bi_ok = np.where(ok, bi, 0)
            ok &= bt.live[bi_ok] & bt.valid[bi_ok] & np.isfinite(bt.x[bi_ok]) & (bt.poss[bi_ok] > 0)
            if not ok.any():
                continue
            s = md.attack_sign(int(h), side)
            bi = bi[ok]
            state = (bt.poss[bi] != own).astype(np.int64)  # 0 in possession, 1 out of possession
            n += acc.add(role, state, s * gh["x"].to_numpy()[ok] * sx, s * gh["y"].to_numpy()[ok] * sy, s * bt.x[bi] * sx, s * bt.y[bi] * sy)
    return n


# ---------------------------------------------------------------------------
# Events: locations, passes, shots, possessions
# ---------------------------------------------------------------------------

def event_team(attrs: dict) -> str | None:
    for k in TEAM_KEYS:
        if attrs.get(k):
            return attrs[k]
    return None


def zone_of(xa: float, ya: float, cfg: CalibrationConfig) -> str:
    ix = int(np.clip(math.floor((xa + cfg.std_pitch_length_m / 2) / cfg.std_pitch_length_m * cfg.zone_nx), 0, cfg.zone_nx - 1))
    iy = int(np.clip(math.floor((ya + cfg.std_pitch_width_m / 2) / cfg.std_pitch_width_m * cfg.zone_ny), 0, cfg.zone_ny - 1))
    return f"x{ix}y{iy}"


def zone_keys(cfg: CalibrationConfig) -> list[str]:
    return [f"x{i}y{j}" for i in range(cfg.zone_nx) for j in range(cfg.zone_ny)]


@dataclass
class EventRecords:
    team_counts: list[dict] = field(default_factory=list)  # per team-match: {"match", "side", "minutes", "counts": Counter}
    match_counts: list[dict] = field(default_factory=list)  # per match: team-less event types
    passes: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    shots: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    circulation: list[np.ndarray] = field(default_factory=list)
    circulation_medians: list[float] = field(default_factory=list)
    player_passes: list[dict] = field(default_factory=list)
    live_s: float = 0.0
    total_s: float = 0.0
    location_source: Counter = field(default_factory=Counter)


def _int_or_none(v) -> int | None:
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v) else int(v)


def collect_events(md: MatchData, rec: EventRecords, cfg: CalibrationConfig) -> None:
    ev = md.events
    fs = md.fs
    kick_w = int(round(cfg.kick_speed_window_s * fs))
    sxs, sys_ = cfg.std_pitch_length_m / md.L, cfg.std_pitch_width_m / md.W
    gk_ids = {pid for pid, r in md.role_of.items() if r == "GK"}
    minutes = sum(float(v["tracking_duration_s"]) for v in md.profile["playing_time"].values()) / 60.0
    rec.live_s += sum(float(v.get("ball_live_s") or 0.0) for v in md.profile["playing_time"].values())
    rec.total_s += minutes * 60.0

    # --- counts per team-match and per match ---
    counts = {"home": Counter(), "away": Counter()}
    teamless = Counter()
    for et, attrs in zip(ev["event_type"], ev["attrs"]):
        side = md.team_side_of.get(event_team(attrs))
        if side in counts:
            counts[side][et] += 1
        else:
            teamless[et] += 1
    for side in ("home", "away"):
        rec.team_counts.append({"match": md.match_id, "side": side, "minutes": minutes, "counts": counts[side]})
    rec.match_counts.append({"match": md.match_id, "minutes": minutes, "counts": teamless})

    # --- per-event location (sync frame if located, else annotated -> canonical) and corrected time ---
    rows = []
    for r in ev.itertuples(index=False):
        attrs = r.attrs
        side = md.team_side_of.get(event_team(attrs))
        h = int(r.half)
        t_corr = r.time_utc.timestamp() + md.clock_offset_s.get(h, 0.0)
        located = isinstance(r.sync_x_att_m, float) and np.isfinite(r.sync_x_att_m) and r.acting_team_side == side
        if located:
            xa, ya, src = float(r.sync_x_att_m), float(r.sync_y_att_m), "sync_frame"
        elif side in ("home", "away") and np.isfinite(r.x) and np.isfinite(r.y) and h in md.home_sign:
            xa, ya = md.provider_to_att(r.x, r.y, h, side)
            src = "annotated"
        else:
            xa = ya = np.nan
            src = "none"
        sync_frame = _int_or_none(r.sync_frame)
        t_order = r.sync_time_utc.timestamp() if pd.notna(r.sync_time_utc) else t_corr
        rows.append({"type": r.event_type, "half": h, "side": side, "player": r.player_id if isinstance(r.player_id, str) else attrs.get("Player"),
                     "xa": xa * sxs if np.isfinite(xa) else np.nan, "ya": ya * sys_ if np.isfinite(ya) else np.nan, "src": src,
                     "t": t_order, "sync_frame": sync_frame, "frame_est": sync_frame if sync_frame is not None else (md.ball[h].frame_at(t_corr) if h in md.ball else None),
                     "row": r})
    loc = pd.DataFrame([{k: v for k, v in d.items() if k != "row"} for d in rows])

    # --- on-ball actions and possession chains (events) ---
    act = loc.loc[loc["type"].isin(ACTION_TYPES) & loc["side"].isin(["home", "away"]) & loc["xa"].notna()].sort_values(["half", "t"], kind="mergesort")
    for t in act["src"]:
        rec.location_source[t] += 1
    prev = None
    chain = 0
    for a in act.itertuples(index=False):
        new = (prev is None or a.half != prev.half or a.side != prev.side or a.type in RESTART_TYPES or prev.type == "ShotAtGoal")
        if new:
            chain += 1
        rec.actions.append({"match": md.match_id, "chain": chain, "type": a.type, "zone": zone_of(a.xa, a.ya, cfg)})
        prev = a

    # --- passes (open play, located) ---
    for d in rows:
        r = d["row"]
        if d["type"] != "Play":
            continue
        attrs = r.attrs
        high = str(attrs.get("Height", "")).lower() == "high"
        rec_p = {"match": md.match_id, "located": False, "high_annotated": high, "evaluation": attrs.get("Evaluation")}
        if r.sync_method in LOCATED_KICK_METHODS and d["src"] == "sync_frame" and d["sync_frame"] is not None:
            h, sf = d["half"], d["sync_frame"]
            bt = md.ball[h]
            ntf = _int_or_none(r.next_touch_frame)
            success = bool(r.next_touch_team_side == d["side"] and isinstance(r.next_touch_object_id, str) and r.next_touch_object_id != d["player"])
            i0 = int(bt.index([sf])[0])
            i_end = int(bt.index([ntf])[0]) if ntf is not None else -1
            angle = np.nan
            if i0 >= 0 and i_end >= 0:
                s = md.attack_sign(h, d["side"])
                dx = s * (bt.x[i_end] - bt.x[i0]) * sxs
                dy = s * (bt.y[i_end] - bt.y[i0]) * sys_
                if np.hypot(dx, dy) > 0:
                    angle = math.degrees(math.atan2(dy, dx))
            # Initial speed: peak valid ball speed in [kick, kick + 0.5 s], stopped before the next touch.
            speed = np.nan
            if i0 >= 0:
                hi = i0 + kick_w
                if i_end >= 0:
                    hi = min(hi, i_end - 1)
                seg = np.arange(i0, max(hi, i0) + 1)
                seg = seg[seg < len(bt.x)]
                v = bt.speed[seg][bt.valid[seg]]
                speed = float(np.nanmax(v)) if np.isfinite(v).any() else np.nan
            air = np.nan
            airborne = None
            if i0 >= 0 and i_end > i0:
                z = bt.z[i0:i_end + 1]
                ok = bt.live[i0:i_end + 1] & np.isfinite(z)
                if ok.all():
                    up = np.flatnonzero(z >= cfg.air_rise_m)
                    airborne = bool(len(up))
                    if high and len(up):
                        down = np.flatnonzero(z[up[0]:] <= cfg.air_landing_m)
                        land = up[0] + int(down[0]) if len(down) else len(z) - 1
                        if bt.valid[i0:i0 + land + 1].all():
                            air = land / fs
            length = float(r.pass_length_m) if np.isfinite(r.pass_length_m) else np.nan
            rec_p.update(located=True, zone=zone_of(d["xa"], d["ya"], cfg), length=length * sxs if np.isfinite(length) else np.nan, success=success,
                         angle=angle, speed=speed, air=air, airborne=airborne, side=d["side"])
            if d["player"]:
                rec.player_passes.append({"match": md.match_id, "player": d["player"], "success": success})
        rec.passes.append(rec_p)

    # --- shots (open play = top-level ShotAtGoal) ---
    pl = md.players
    for d in rows:
        r = d["row"]
        if d["type"] != "ShotAtGoal":
            continue
        attrs = r.attrs
        outcome = r.sub_types if isinstance(r.sub_types, str) and r.sub_types else "none"
        rs = {"match": md.match_id, "outcome": outcome, "head": str(attrs.get("TypeOfShot", "")).lower() == "head",
              "goal": outcome == "SuccessfulShot", "located": False, "zone": zone_of(d["xa"], d["ya"], cfg) if np.isfinite(d["xa"]) else None}
        if r.sync_method in LOCATED_KICK_METHODS and d["src"] == "sync_frame" and d["sync_frame"] is not None:
            h, sf, side = d["half"], d["sync_frame"], d["side"]
            bt = md.ball[h]
            i0 = int(bt.index([sf])[0])
            s = md.attack_sign(h, side)
            fr = pl.loc[(pl["period"] == h) & (pl["frame"] == sf) & (pl["team_side"] != side)]
            outfield = ~fr["object_id"].isin(gk_ids).to_numpy()
            ok = fr["valid"].to_numpy() & outfield & np.isfinite(fr["x"].to_numpy())
            ox, oy = s * fr["x"].to_numpy()[ok] * sxs, s * fr["y"].to_numpy()[ok] * sys_
            feats = shot_features(d["xa"], d["ya"], ox, oy, cfg.std_pitch_length_m, PREP_CONFIG.goal_width_m)
            # Diagnostic: opposing outfield players within 20 m of the ball whose sample is not valid (not counted).
            near_invalid = int(((~fr["valid"].to_numpy()) & outfield & (np.hypot(s * fr["x"].to_numpy() * sxs - d["xa"], s * fr["y"].to_numpy() * sys_ - d["ya"]) < 20)).sum())
            speed = np.nan
            if i0 >= 0 and r.type_check == "confirmed":
                seg = np.arange(i0, min(i0 + kick_w, len(bt.x) - 1) + 1)
                v = bt.speed[seg][bt.valid[seg]]
                speed = float(np.nanmax(v)) if np.isfinite(v).any() else np.nan
            z_kick = float(bt.z[i0]) if i0 >= 0 else np.nan
            header = shot_is_header(getattr(r, "shot_header", None), z_kick, rs["head"])
            rs.update(located=True, **feats, confirmed=r.type_check == "confirmed", trajectory_goal=r.type_check_basis == "goal", speed=speed,
                      header_at_kick=header, invalid_opponents_near=near_invalid, player=d["player"])
        rec.shots.append(rs)

    # --- possession runs (provider ball status + possession) and circulation speed ---
    plays = loc.loc[(loc["type"] == "Play") & loc["side"].isin(["home", "away"]) & loc["frame_est"].notna()]
    for h, bt in sorted(md.ball.items()):
        key = np.where(bt.live, bt.poss, 0).astype(np.int64)
        brk = np.r_[True, key[1:] != key[:-1]]
        starts = np.flatnonzero(brk)
        ends = np.r_[starts[1:] - 1, len(key) - 1]
        ph = plays.loc[plays["half"] == h]
        pf = ph["frame_est"].to_numpy(dtype=np.int64) - bt.f0
        pside = ph["side"].to_numpy()
        for a, b in zip(starts, ends):
            k = key[a]
            if k == 0:
                continue
            side = "home" if k == 1 else "away"
            dur = (b - a + 1) / fs
            n_pass = int(((pf >= a) & (pf <= b) & (pside == side)).sum())
            prog = np.nan
            if dur >= cfg.progression_min_s and np.isfinite(bt.x[a]) and np.isfinite(bt.x[b]):
                prog = md.attack_sign(h, side) * (bt.x[b] - bt.x[a]) * sxs / dur
            rec.runs.append({"match": md.match_id, "duration_s": dur, "passes": n_pass, "progression_ms": prog})
    for side, k in (("home", 1), ("away", 2)):
        sp = np.concatenate([bt.speed[bt.valid & bt.live & (bt.poss == k)] for bt in md.ball.values()])
        sp = sp[np.isfinite(sp)]
        rec.circulation.append(sp)
        if len(sp):
            rec.circulation_medians.append(float(np.median(sp)))


def event_model(rec: EventRecords, cfg: CalibrationConfig) -> dict:
    zones = zone_keys(cfg)
    types = sorted({t for tc in rec.team_counts for t in tc["counts"]})
    teamless = sorted({t for mc in rec.match_counts for t in mc["counts"]})
    per90_team = {t: stats([tc["counts"].get(t, 0) * 90.0 / tc["minutes"] for tc in rec.team_counts]) for t in types}
    per90_match = {t: stats([mc["counts"].get(t, 0) * 90.0 / mc["minutes"] for mc in rec.match_counts]) for t in teamless}

    P = pd.DataFrame(rec.passes, columns=PASS_COLS)
    LP = P.loc[P["located"].astype(bool)]
    edges = np.linspace(-180, 180, cfg.direction_sectors + 1)
    sector_labels = [f"{int(a)}..{int(b)}" for a, b in zip(edges[:-1], edges[1:])]
    pass_block = {"length_m_by_zone": {}, "success_share_by_zone": {}, "n_by_zone": {}, "high_share": float(P["high_annotated"].mean()) if len(P) else None,
                  "direction_deg_by_zone": {}, "direction_sector_share_by_zone": {}}
    for z in zones:
        d = LP.loc[LP["zone"] == z]
        pass_block["n_by_zone"][z] = int(len(d))
        pass_block["length_m_by_zone"][z] = stats(d["length"].to_numpy())
        pass_block["success_share_by_zone"][z] = float(d["success"].mean()) if len(d) else None
        pass_block["direction_deg_by_zone"][z] = stats(d["angle"].to_numpy())
        a = d["angle"].to_numpy(dtype=float)
        a = a[np.isfinite(a)]
        h = np.histogram(a, bins=edges)[0]
        pass_block["direction_sector_share_by_zone"][z] = dict(zip(sector_labels, (h / h.sum()).tolist())) if h.sum() else None
    pass_block["overall"] = {"n_annotated": int(len(P)), "n_located": int(len(LP)), "length_m": stats(LP["length"].to_numpy()),
                             "success_share": float(LP["success"].mean()) if len(LP) else None,
                             "annotated_success_share": float(P["evaluation"].isin(["successfullyCompleted", "successful"]).mean()) if len(P) else None,
                             "sector_labels_deg": sector_labels}
    lp_air = LP.loc[LP["airborne"].notna()] if len(LP) else LP
    pass_block["height_check"] = {
        "annotated_high_with_ball_above_rise_share": float(lp_air.loc[lp_air["high_annotated"], "airborne"].astype(bool).mean()) if len(lp_air) else None,
        "annotated_flat_with_ball_above_rise_share": float(lp_air.loc[~lp_air["high_annotated"].astype(bool), "airborne"].astype(bool).mean()) if len(lp_air) else None,
        "rise_m": cfg.air_rise_m, "n": int(len(lp_air))}

    A = pd.DataFrame(rec.actions, columns=["match", "chain", "type", "zone"])
    trans = {z: Counter() for z in zones}
    from_n = Counter()
    ends = Counter()
    zone_n = Counter()
    for (_, _), g in A.groupby(["match", "chain"], sort=True):
        zs = g["zone"].tolist()
        for z in zs:
            zone_n[z] += 1
        for a, b in zip(zs[:-1], zs[1:]):
            trans[a][b] += 1
            from_n[a] += 1
        ends[zs[-1]] += 1
    transitions = {z: ({b: trans[z][b] / from_n[z] for b in zones if trans[z][b]} if from_n[z] else {}) for z in zones}

    S = pd.DataFrame(rec.shots, columns=SHOT_COLS)
    shot_zone_n = Counter(S["zone"].dropna()) if len(S) else Counter()
    chains_visiting = Counter()
    for (_, _), g in A.groupby(["match", "chain"], sort=True):
        for z in set(g["zone"]):
            chains_visiting[z] += 1
    shot_block = {
        "rate_per_possession_by_zone": {z: (shot_zone_n[z] / chains_visiting[z] if chains_visiting[z] else None) for z in zones},
        "rate_per_action_by_zone": {z: (shot_zone_n[z] / zone_n[z] if zone_n[z] else None) for z in zones},
        "outcome_shares": {o: float((S["outcome"] == o).mean()) for o in SHOT_OUTCOMES} if len(S) else {},
        "head_share": float(S["head"].mean()) if len(S) else None,
        "n_shots": int(len(S)), "n_located": int(S["located"].sum()) if len(S) else 0,
        "share_by_zone": {z: shot_zone_n[z] / max(1, sum(shot_zone_n.values())) for z in zones},
        "distance_m": stats(S.loc[S["located"].astype(bool), "distance_m"].to_numpy()),
    }
    R = pd.DataFrame(rec.runs, columns=["match", "duration_s", "passes", "progression_ms"])
    return {
        "per90_per_team": per90_team,
        "per90_per_match": per90_match,
        "zones": {"x_edges": np.linspace(-cfg.std_pitch_length_m / 2, cfg.std_pitch_length_m / 2, cfg.zone_nx + 1).tolist(),
                  "y_edges": np.linspace(-cfg.std_pitch_width_m / 2, cfg.std_pitch_width_m / 2, cfg.zone_ny + 1).tolist(),
                  "keys": zones},
        "pass": pass_block,
        "transitions": transitions,
        "transitions_n_by_zone": {z: from_n[z] for z in zones},
        "possession_end_share_by_zone": {z: (ends[z] / zone_n[z] if zone_n[z] else None) for z in zones},
        "shot": shot_block,
        "set_pieces_per90_per_team": {t: per90_team.get(t, stats([0.0] * len(rec.team_counts))) for t in SET_PIECES},
        "possession": {"passes_per_sequence": stats(R["passes"].to_numpy()), "duration_s": stats(R["duration_s"].to_numpy()),
                       "n_sequences": int(len(R)), "share_shorter_than_1s": float((R["duration_s"] < 1.0).mean()) if len(R) else None,
                       "passes_per_sequence_at_least_1s": stats(R.loc[R["duration_s"] >= 1.0, "passes"].to_numpy()) if len(R) else stats([]),
                       "passes_per_event_chain": stats(A.groupby(["match", "chain"], sort=True)["type"].agg(lambda t: int((t == "Play").sum())).to_numpy()) if len(A) else stats([]),
                       "actions_per_event_chain": stats(A.groupby(["match", "chain"], sort=True).size().to_numpy()) if len(A) else stats([])},
        "ball_in_play_share": rec.live_s / rec.total_s if rec.total_s else None,
        "fouls_per90_per_team": per90_team.get("Foul", stats([])),
        "cautions_per90_per_team": per90_team.get("Caution", stats([])),
        "actions_location_source": dict(sorted(rec.location_source.items())),
    }


def shot_is_header(flag, z_kick_m: float, annotated_head: bool) -> bool:
    """Header for shot speeds (v4.2, E-3): the same test as the header exception of ``R-SHOT-POWER``. The pipeline's
    own ``shot_header`` flag when the frame-level file has it; otherwise the ball above ``aerial_min_height_m`` (1.5 m)
    at the kick; without ball height, the annotated body part (``TypeOfShot = head``)."""
    if isinstance(flag, (bool, np.bool_)):
        return bool(flag)
    if isinstance(flag, str) and flag.strip().lower() in ("true", "false"):
        return flag.strip().lower() == "true"
    if np.isfinite(z_kick_m):
        return bool(z_kick_m > PREP_CONFIG.aerial_min_height_m)
    return bool(annotated_head)


def confirmed_shot_speeds(rec: EventRecords) -> np.ndarray:
    """Shot speeds (m/s) of the located, confirmed open-play shots **without headers** (v4.2, ``shot_is_header``);
    soft shots stay in. Used by ball.json shot_speed_ms, the shot_power attribute scale and the realism metric."""
    return np.array([s["speed"] for s in rec.shots if s.get("located") and s.get("confirmed") and not s.get("header_at_kick", False)
                     and np.isfinite(s.get("speed", np.nan))], float)


def ball_model(rec: EventRecords, cfg: CalibrationConfig) -> dict:
    P = pd.DataFrame(rec.passes, columns=PASS_COLS)
    LP = P.loc[P["located"].astype(bool)]
    R = pd.DataFrame(rec.runs, columns=["match", "duration_s", "passes", "progression_ms"])
    circ = np.concatenate(rec.circulation) if rec.circulation else np.array([])
    shot_speed = confirmed_shot_speeds(rec)
    pass_speed = LP["speed"].to_numpy(dtype=float)
    finite = pass_speed[np.isfinite(pass_speed)]
    return {
        # p99 is read by R-SHOT-POWER (guidelines 9.7): never a limit, only extra evidence for a shot.
        "pass_speed_ms": stats(pass_speed) | {"p99": float(np.percentile(finite, 99)) if len(finite) else None},
        "long_pass_speed_ms": stats(LP.loc[LP["length"] >= cfg.long_pass_m, "speed"].to_numpy()),
        "shot_speed_ms": stats(shot_speed),
        "circulation_speed_ms": stats(circ),
        "circulation_speed_median_per_team_match_ms": stats(rec.circulation_medians),
        "progression_speed_ms": stats(R["progression_ms"].to_numpy()),
        "air_time_high_pass_s": stats(LP.loc[LP["high_annotated"].astype(bool), "air"].to_numpy()),
    }


XG_EXAMPLES = (  # (distance from the goal line x, lateral offset y, opponents in the triangle, description); METRICS.md section 9 checks
    (6.0, 0.0, 0, "6 m central (centre of the goal-area line), no defender in the triangle"),
    (11.0, 0.0, 0, "penalty spot in open play, no defender in the triangle"),
    (11.0, 0.0, 2, "penalty spot in open play, two defenders in the triangle"),
    (16.5, 0.0, 0, "edge of the box, central, no defender in the triangle"),
    (16.5, 0.0, 1, "edge of the box, central, one defender in the triangle"),
    (2.0, 10.0, 0, "tight angle: 2 m from the goal line, 10 m wide of the centre, no defender"),
    (25.0, 0.0, 1, "25 m central, one defender in the triangle"),
)


def xg_fit(theta: np.ndarray, n: np.ndarray, y: np.ndarray, cfg: CalibrationConfig) -> dict:
    """``logit(xG) = a + slope * theta + b * n`` with the angle slope fixed (``cfg.xg_angle_slope``, an offset): Newton's
    method for ``a`` and ``b``, L2 ``cfg.xg_l2`` on the standardised ``b`` only (deterministic). Returns
    ``{"a", "b", "b_standardised", "iterations"}``."""
    f = fit_logistic_l2(np.asarray(n, float).reshape(-1, 1), y, cfg.xg_l2, cfg.xg_max_iter, cfg.xg_tol, offset=cfg.xg_angle_slope * np.asarray(theta, float))
    return {"a": float(f["intercept"]), "b": float(f["coef"][0]), "b_standardised": float(f["coef_standardised"][0]), "iterations": f["iterations"]}


def xg_predict(theta, n, a: float, b: float, slope: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(a + slope * np.asarray(theta, float) + b * np.asarray(n, float))))


def xg_model(rec: EventRecords, cfg: CalibrationConfig) -> tuple[dict, pd.DataFrame]:
    """The xG model of ``Materials/METRICS.md`` section 9 (v4.2). Same file format as before, read by the generator
    (``features``, ``coef``, ``intercept``): ``coef`` = [0 (distance), fixed angle slope, fitted ``b``], ``intercept`` = ``a``."""
    S = pd.DataFrame(rec.shots, columns=SHOT_COLS)
    L = S.loc[S["located"].astype(bool)].copy()
    L["goal"] = L["goal"].astype(bool)
    L["trajectory_goal"] = L["trajectory_goal"].astype(bool)
    feats = ["distance_m", "visible_angle_rad", "opponents_in_triangle"]
    fixed = {"angle_slope_fixed": cfg.xg_angle_slope, "angle_slope_source": cfg.xg_angle_slope_source, "penalty_xg": cfg.penalty_xg}
    if len(L) < cfg.xg_min_shots or L["goal"].all() or not L["goal"].any():
        return {"model": "not fitted", "reason": f"{len(L)} located shots with {int(L['goal'].sum())} goals (needs >= {cfg.xg_min_shots} shots, goals and misses)",
                "features": feats, "coef": None, "intercept": None, "n_shots": int(len(L)), "n_goals": int(L["goal"].sum()), **fixed}, L
    theta = L["visible_angle_rad"].to_numpy(float)
    n = L["opponents_in_triangle"].to_numpy(float)
    y = L["goal"].to_numpy(float)
    fit = xg_fit(theta, n, y, cfg)
    p = xg_predict(theta, n, fit["a"], fit["b"], cfg.xg_angle_slope)
    L["xg"] = p
    eps = 1e-12

    def logloss(yy, pp):
        pp = np.clip(pp, eps, 1 - eps)
        return float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp)))

    # Leave-one-match-out cross-validation against a constant-rate baseline.
    cv_p = np.full(len(L), np.nan)
    base_p = np.full(len(L), np.nan)
    matches = L["match"].to_numpy()
    for m in sorted(set(matches)):
        tr, te = matches != m, matches == m
        if y[tr].sum() == 0:
            continue
        f = xg_fit(theta[tr], n[tr], y[tr], cfg)
        cv_p[te] = xg_predict(theta[te], n[te], f["a"], f["b"], cfg.xg_angle_slope)
        base_p[te] = y[tr].mean()
    okcv = np.isfinite(cv_p)
    bins = np.asarray(cfg.xg_bins)
    table = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (p >= lo) & ((p < hi) if hi < bins[-1] else (p <= hi))
        table.append({"xg_from": float(lo), "xg_to": float(hi), "n_shots": int(m.sum()), "predicted_goals": float(p[m].sum()),
                      "observed_goals": int(y[m].sum()), "mean_xg": float(p[m].mean()) if m.any() else None,
                      "observed_rate": float(y[m].mean()) if m.any() else None})
    examples = []
    for x, yy, k, desc in XG_EXAMPLES:
        th = float(visible_angle(x, yy, PREP_CONFIG.goal_width_m))
        examples.append({"distance_m": float(math.hypot(x, yy)), "visible_angle_rad": th, "opponents_in_triangle": k,
                         "xg": float(xg_predict(th, k, fit["a"], fit["b"], cfg.xg_angle_slope)), "description": desc})
    ex = {e["description"]: e["xg"] for e in examples}
    out = {
        "model": ("logistic regression with the visible-angle slope fixed (model updating, Materials/METRICS.md section 9): only the intercept a and the "
                  "slope b of opponents_in_triangle are fitted (Newton's method, L2 on the standardised b, intercept not penalised)"),
        "formula": "xG = 1 / (1 + exp(-(intercept + coef[0]*distance_m + coef[1]*visible_angle_rad + coef[2]*opponents_in_triangle)))",
        "features": feats,
        "coef": [0.0, float(cfg.xg_angle_slope), fit["b"]],
        "intercept": fit["a"],
        "fitted_parameters": ["intercept", "opponents_in_triangle"],
        **fixed,
        "l2_lambda": cfg.xg_l2,
        "b_standardised": fit["b_standardised"],
        "iterations": fit["iterations"],
        "n_shots": int(len(L)),
        "n_goals": int(y.sum()),
        "checks": {"b_negative": bool(fit["b"] < 0),
                   "six_m_central_above_penalty_spot_above_edge_of_box": bool(ex[XG_EXAMPLES[0][3]] > ex[XG_EXAMPLES[1][3]] > ex[XG_EXAMPLES[3][3]]),
                   "sum_xg_vs_goals": {"sum_xg": float(p.sum()), "goals": int(y.sum())}},
        "n_open_play_shots_annotated": int(len(S)),
        "n_dropped_not_located": int((~S["located"]).sum()),
        "goals_not_located": int(S.loc[~S["located"], "goal"].sum()),
        "label": "annotated outcome SuccessfulShot",
        "label_agreement_with_tracking": {"annotated_goals": int(y.sum()), "tracking_goal_line_crossings": int(L["trajectory_goal"].sum()),
                                          "both": int((L["goal"] & L["trajectory_goal"]).sum())},
        "features_summary": {f: stats(L[f].to_numpy()) for f in feats},
        "opponents_in_triangle_counts": {str(k): int(v) for k, v in sorted(Counter(L["opponents_in_triangle"].astype(int)).items())},
        "shots_with_invalid_opponent_within_20m": int((L["invalid_opponents_near"] > 0).sum()),
        "calibration_table": table,
        "in_sample": {"log_loss": logloss(y, p), "brier": float(np.mean((p - y) ** 2)), "sum_xg": float(p.sum())},
        "leave_one_match_out": {"n_shots": int(okcv.sum()), "log_loss": logloss(y[okcv], cv_p[okcv]) if okcv.any() else None,
                                "baseline_constant_rate_log_loss": logloss(y[okcv], base_p[okcv]) if okcv.any() else None,
                                "brier": float(np.mean((cv_p[okcv] - y[okcv]) ** 2)) if okcv.any() else None},
        "examples": examples,
    }
    return out, L


def attribute_scales(pm: pd.DataFrame, rec: EventRecords, cfg: CalibrationConfig) -> dict:
    elig = pm.loc[(pm["valid_minutes"] >= cfg.min_valid_minutes) & pm["group"].notna()]

    lv = cfg.attribute_quantile_levels_pct

    def scale(values_by_row: pd.Series, groups: pd.Series, unit: str, metric: str, by_group: bool = True) -> dict:
        # v4.2: 21 quantiles only (no minimum or maximum); the 99 values are interpolated from them.
        out = {"unit": unit, "metric": metric, "overall": quantile_scale_table(values_by_row.to_numpy(), cfg.percentile_min_n, lv)}
        if by_group:
            out["by_role_group"] = {g: quantile_scale_table(values_by_row[groups == g].to_numpy(), cfg.percentile_min_n, lv) for g in GROUPS}
        return out

    pp = pd.DataFrame(rec.player_passes)
    passing = pd.DataFrame(columns=["share", "group"])
    if len(pp):
        agg = pp.groupby(["match", "player"], sort=True)["success"].agg(["size", "mean"]).reset_index()
        agg = agg.loc[agg["size"] >= cfg.passing_min_passes]
        grp = pm.set_index(["match_id", "player"])["group"]
        agg["group"] = [grp.get((m, p)) for m, p in zip(agg["match"], agg["player"])]
        passing = agg.rename(columns={"mean": "share"})
    shot_speed = pd.Series(confirmed_shot_speeds(rec), dtype=float)
    return {
        "pace": scale(elig["top_speed_kmh"], elig["group"], "km/h", "top speed: maximum of our valid speed per player-match"),
        "acceleration": scale(elig["acc_peak_ms2"], elig["group"], "m/s2", "peak positive tangential acceleration on valid samples per player-match"),
        "stamina": scale(elig["distance_per90_m"], elig["group"], "m per 90 (measured time)", "distance per 90 per player-match"),
        "work_rate": scale(elig["work_rate_per90_m"], elig["group"], "m per 90 (measured time)", "distance above 20 km/h per 90 per player-match"),
        "passing": scale(passing["share"].astype(float), passing["group"], "share 0-1",
                         f"pass success share (next touch by a team-mate) per player-match with >= {cfg.passing_min_passes} located passes"),
        "shot_power": {"unit": "m/s", "metric": "peak valid ball speed in the 0.5 s after the kick of a confirmed open-play shot, headers excluded (per shot)",
                       "overall": quantile_scale_table(shot_speed.to_numpy(dtype=float), cfg.percentile_min_n, lv)},
    }


ACC_BAND_CLASSES = ("noise", "approx")
ACC_BAND_SIGNS = ("acceleration", "deceleration")


def acc_band_rates(by_class_and_sign: dict | None, player_hours: float) -> dict:
    """``R-ACC-BAND`` (v4) runs by class (noise <= 0.2 s, approx longer) and sign, from a match's
    ``qc_results.json`` ``acc_band.by_class_and_sign``: runs, samples, runs per player-hour and mean run length
    (samples). ``all`` sums both signs of a class."""
    out: dict = {}
    for cls in ACC_BAND_CLASSES:
        out[cls] = {}
        tot_runs = tot_samples = 0
        for sign in ACC_BAND_SIGNS:
            d = ((by_class_and_sign or {}).get(cls) or {}).get(sign) or {}
            runs, samples = int(d.get("runs", 0)), int(d.get("samples", 0))
            tot_runs, tot_samples = tot_runs + runs, tot_samples + samples
            out[cls][sign] = {"runs": runs, "samples": samples, "runs_per_player_hour": runs / player_hours if player_hours > 0 else None,
                              "mean_run_samples": samples / runs if runs else None}
        out[cls]["all"] = {"runs": tot_runs, "samples": tot_samples,
                           "runs_per_player_hour": tot_runs / player_hours if player_hours > 0 else None,
                           "mean_run_samples": tot_samples / tot_runs if tot_runs else None}
    return out


def _acc_band_settings(r: dict, fs: float) -> dict:
    """The preparation settings behind a match's band counts (from its qc_results.json configuration)."""
    c = r.get("config", {})
    noise_s = c.get("acc_noise_max_s")
    return {"band_ms2": [c.get("player_acc_min_ms2"), c.get("player_acc_max_ms2")], "noise_max_s": noise_s,
            "noise_max_samples": int(round(float(noise_s) * fs)) if noise_s is not None else None,
            "acc_window_samples": c.get("acc_window_samples"), "fs_hz": fs}


def acc_band_profile(matches: list[MatchData]) -> dict:
    """``R-ACC-BAND`` (v4) exceedance runs per match and pooled, for the generator's acceleration spikes.

    Since v4 an acceleration outside -8.5..+7.5 m/s2 is no longer a limit anomaly: it never excludes a sample and
    is classified as ``noise`` (a run of <= 0.2 s, its value is not used) or ``approx`` (longer, kept with "≈").
    The counts are read from each match's ``qc_results.json`` (``acc_band.by_class_and_sign``); they depend on the
    acceleration filter window (``acc_window_samples``, 19 samples since v4.1).
    Pooled rates are total runs / total player-hours; pooled mean run length is total samples / total runs."""
    per_match: dict = {}
    pooled: dict = {cls: {sign: {"runs": 0, "samples": 0} for sign in ACC_BAND_SIGNS} for cls in ACC_BAND_CLASSES}
    pooled_ph = 0.0
    settings: list[dict] = []
    for md in matches:
        r = md.qc_results
        if not r or "acc_band" not in r:
            continue
        ph = r["mask_summary"]["player_samples"] / md.fs / 3600.0
        bcs = r["acc_band"].get("by_class_and_sign", {})
        s = _acc_band_settings(r, md.fs)
        if s not in settings:
            settings.append(s)
        per_match[md.match_id] = {"player_hours": ph, "acc_window_samples": s["acc_window_samples"], **acc_band_rates(bcs, ph)}
        pooled_ph += ph
        for cls in ACC_BAND_CLASSES:
            for sign in ACC_BAND_SIGNS:
                d = (bcs.get(cls) or {}).get(sign) or {}
                pooled[cls][sign]["runs"] += int(d.get("runs", 0))
                pooled[cls][sign]["samples"] += int(d.get("samples", 0))
    if not per_match:
        return {}
    return {
        "rule": "R-ACC-BAND (v4): player tangential acceleration outside band_ms2 never excludes a sample. noise = a run of at most "
                "noise_max_samples consecutive valid samples (its acceleration is not used); approx = a longer run (value kept, reported with '≈').",
        "settings": settings[0] if len(settings) == 1 else settings,
        "per_match": per_match,
        "pooled": {"player_hours": pooled_ph, **acc_band_rates(pooled, pooled_ph)},
    }


def artefact_profile(matches: list[MatchData]) -> dict:
    """Provider artefact rates per match (guidelines section 11): aggregates of the committed qc_results.json."""
    per = []
    for md in matches:
        r = md.qc_results
        if not r:
            continue
        ms = r["mask_summary"]
        fs = md.fs
        ph = ms["player_samples"] / fs / 3600.0
        cat = r["catalogue"]
        by_type = Counter()
        for row in cat["by_type_and_cause"]:
            by_type[row["anomaly_type"]] += row["n"]
        subs = r.get("substitutions", [])
        ins = [s for s in subs if "inheritance" in s]
        prof = md.profile
        band = acc_band_rates(r.get("acc_band", {}).get("by_class_and_sign"), ph) if "acc_band" in r else None
        band_cols = {f"acc_band_{cls}_{sign}_{q}": (band[cls][sign][q] if band else None)
                     for cls in ACC_BAND_CLASSES for sign in ACC_BAND_SIGNS for q in ("runs_per_player_hour", "mean_run_samples")}
        per.append({
            "player_hours": ph,
            "valid_share": ms["player_valid"] / ms["player_samples"],
            "estimated_share": ms["player_estimated"] / ms["player_samples"],
            "discarded_share": ms["player_discarded"] / ms["player_samples"],
            "jump_episodes_per_player_hour": cat["jumps"]["episodes"] / ph,
            "jump_median_m": cat["jumps"]["median_m"],
            "swap_incidents_per_player_hour": len(r.get("swaps", [])) / ph,
            "frozen_runs_per_player_hour": r["gaps"]["frozen"].get("runs", 0) / ph,
            "frozen_run_mean_s": (r["gaps"]["frozen"].get("samples", 0) / max(1, r["gaps"]["frozen"].get("runs", 0))) / fs,
            "edge_runs": len(r.get("edge_runs", [])),
            "edges_beyond_line_m": [e["beyond_line_m"] for e in prof["tracking_area_edges"]["edges"]],
            "overlap_episodes_per_player_hour": by_type.get("overlap", 0) / ph,
            # True limit episodes only: player speed above 38 km/h (R-SPEED-LIMIT). Since v4 this no longer includes
            # accelerations outside the band: they are R-ACC-BAND runs (acc_band_* below and the "acc_band" block).
            # Live ball speed above 211 km/h (R-BALL-LIMIT) is counted separately in ball_limit_episodes.
            "limit_episodes_per_player_hour": by_type.get("limit", 0) / ph,
            "ball_limit_episodes": by_type.get("ball_limit", 0),
            **band_cols,
            "ball_live_jump_episodes": sum(b["live_jump_episodes"] for b in ms["ball_log"].values()),
            "substitutions": len(subs),
            "inheritance_share": (sum(bool(s["inheritance"]) for s in ins) / len(ins)) if ins else None,
            "reacquisition_after_s": [s["reacquisition_after_s"] for s in ins if s.get("reacquisition_jump")],
            "tracking_start_offset_s": [v["tracking_start_offset_s"] for v in prof["kickoff_per_period"].values()],
            "event_clock_offset_s": [v["event_clock_offset_s"] for v in prof["event_clock_offset"].values()],
            "event_offset_dispersion_s": [v["event_offset_dispersion_s"] for v in prof["event_clock_offset"].values()],
            "pass_sync_residual_mad_s": r["time_alignment"]["event_location"]["play_events"]["located"]["pass"]["residual_mad_s"],
        })
    if not per:
        return {}
    df = pd.DataFrame(per)
    keys = [c for c in df.columns if df[c].map(lambda v: isinstance(v, (int, float, np.floating)) or v is None).all()]
    out = {k: stats(df[k].to_numpy(dtype=float)) for k in keys}
    for k in ("edges_beyond_line_m", "reacquisition_after_s", "tracking_start_offset_s", "event_clock_offset_s", "event_offset_dispersion_s"):
        out[k] = stats([v for lst in df[k] for v in lst if v is not None])
    out["n_matches"] = int(len(df))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def library_versions() -> dict:
    from .qc import library_versions as lv
    import pyarrow

    return lv() | {"pyarrow": pyarrow.__version__}


def meta_block(matches: list[MatchData], cfg: CalibrationConfig, description: str, notes: list[str]) -> dict:
    return {
        "description": description,
        "source": SOURCE,
        "doi": DOI,
        "licence": "CC-BY 4.0 (data); these aggregates are derived statistics with attribution",
        "matches_used": [md.match_id for md in matches],
        "generated_by": "cd data_pipeline && .venv/bin/python -m insidethegame_data.calibration",
        "guidelines_version": GUIDELINES_VERSION,
        "pipeline_version": __version__,
        "config_hash": cfg.hash(),
        "config": cfg.to_dict(),
        "prep_config_hash": {md.match_id: md.prep_config_hash for md in matches},
        "input_hashes": {md.match_id: md.input_hashes for md in matches},
        "library_versions": library_versions(),
        "conventions": {
            "coordinates": "metres, centre origin, team-attacking direction: the team attacks +x, left is +y; pitch 105 x 68 m",
            "validity": "valid samples only (valid_kinematics = true); estimated gap samples never count for speed, peaks or per-90 values",
            "per90": "normalised on measured (valid) time of each player-match (team decision 2026-10-24); see coverage",
            "ball": "ball status and possession are the provider's (never derived from a stationary ball)",
        },
        "notes": notes,
    }


def run(match_ids: list[str] | None = None, repo: Path = REPO, out_dir: Path | None = None, cfg: CalibrationConfig = DEFAULT_CAL_CONFIG,
        log=print, only: list[str] | None = None, realism_cfg=None) -> dict[str, dict]:
    """Compute every calibration file and write them to ``out_dir`` (default ``<repo>/calibration``).

    ``only``: write only these file names (every file is still computed and returned), for example
    ``["realism_reference.json"]`` to add the realism reference without touching the other files.
    ``realism_cfg``: a ``realism_metrics.RealismConfig`` (default: the module default).
    """
    from . import realism_metrics as rm  # imported here: realism_metrics builds on this module

    rcfg = realism_cfg or rm.DEFAULT_REALISM_CONFIG
    unknown = sorted(set(only or []) - set(FILES))
    if unknown:
        raise ValueError(f"unknown calibration files {unknown} (known: {list(FILES)})")
    repo = Path(repo)
    out_dir = Path(out_dir) if out_dir else repo / "calibration"
    ids = list(match_ids) if match_ids else available_matches(repo)
    if not ids:
        raise FileNotFoundError(f"no DFL match with frame-level outputs under reports/qc/frames/<id>/{FRAMES_VERSION}/")
    rec = EventRecords()
    acc = PositionAccumulator(cfg)
    matches: list[MatchData] = []
    pm_parts = []
    realism_samples: dict[str, dict] = {}
    realism_shots: list[dict] = []
    n_pos = 0
    for mid in ids:
        md = load_match(repo, mid)
        pm_parts.append(player_match_table([md], cfg))
        n_pos += accumulate_positions(md, acc)
        collect_events(md, rec, cfg)
        realism_samples[mid] = rm.match_samples(md, cfg, rcfg)
        realism_shots.append(rm.shot_outcome_crosstab(md))
        md.players = md.players.iloc[0:0]  # free memory; aggregates are kept
        md.ball = {}
        md.events = md.events.iloc[0:0]
        matches.append(md)
        log(f"{mid}: loaded and aggregated")
    pm = pd.concat(pm_parts, ignore_index=True)

    elig = pm.loc[(pm["valid_minutes"] >= cfg.min_valid_minutes) & pm["group"].notna()]
    cov = float(np.median(elig["coverage"])) if len(elig) else float("nan")
    phys = physical_profiles(pm, cfg)
    small = [g for g, v in phys.items() if v["n_player_matches"] < 10]
    phys_notes = [
        f"Player-matches with >= {cfg.min_valid_minutes:g} valid minutes: {len(elig)} of {len(pm)} (6 matches; one row per player and match).",
        f"Per-90 values are normalised on measured (valid) time; coverage (valid share of the time on the pitch) median {cov:.4f}, "
        f"range {elig['coverage'].min():.4f}-{elig['coverage'].max():.4f}. Distances are measured distances (valid speed x dT); "
        "estimated gap paths are not included, so the speed-zone distances add up to the total and the ball-status and possession splits add up to the total.",
        "distance_in_poss_per90_m: own team in possession (provider); distance_out_poss_per90_m: everything else (opponent in possession; "
        "possession 'none' does not occur in live play on DFL).",
        f"Sprints: runs of valid samples above {cfg.sprint_speed_kmh:g} km/h lasting >= {cfg.sprint_min_s:g} s. acc_peak_ms2: maximum positive tangential acceleration on valid samples (noise samples of R-ACC-BAND excluded; approx values kept).",
        f"distance_per5min_m: mean over player-matches of the measured distance per 5 minutes of the match clock (time since the period's kickoff; "
        f"stoppage time beyond 45 min is not binned); a bin counts when the player is on the pitch for >= {100 * cfg.bin_min_presence_frac:g} % of it and "
        f">= {100 * cfg.bin_min_valid_frac:g} % of it is measured; distance_per5min_n gives the player-matches per bin.",
        "Role groups: GK; CB = LCB/CB/RCB; FB = LB/RB/LWB/RWB; DM = CDM/LDM/RDM; CM = LCM/CM/RCM; AM = CAM; W = LW/RW/LM/RM; ST = ST/LS/RS/CF. "
        "LM/RM are in W (team decision 2026-10-24): on DFL the only LM/RM (one team, 3-4-1-2) play on the whole flank with winger-like high-speed running.",
        f"Small samples (n < 10 player-matches): {small or 'none'}; treat their percentiles as indicative.",
    ]
    out = {}
    out["physical_profiles.json"] = {"meta": meta_block(matches, cfg, "Physical profiles per role group (DFL, valid samples, per 90 of measured time)", phys_notes),
                                     "role_groups": phys}

    pmaps = acc.maps()
    nx, ny = cfg.pos_grid_nx, cfg.pos_grid_ny
    src_counts = {r: {s: dict(sorted(Counter(np.asarray(m[s]["source"]).ravel().tolist()).items())) for s in m} for r, m in pmaps["maps"].items()}
    pos_notes = [
        f"Samples: player valid, ball live and valid, a team in possession (provider); {n_pos:,} samples before mirror pooling.",
        "Coordinates: the player's team attacks +x, left is +y (centre origin, metres, scaled to 105 x 68 m). The ball cell uses the same coordinates.",
        f"Grids are indexed [ix][iy]: ix over x_edges ({nx} bins, own goal -> opponent goal), iy over y_edges ({ny} bins, right touchline y = -34 -> left touchline y = +34).",
        "Mirror pooling: right-side roles are mirrored (y -> -y) into their left partner (RB -> LB, RCB -> LCB, RWB -> LWB, RDM -> LDM, RCM -> LCM, RM -> LM, RW -> LW, RS -> LS) "
        "and the right map is the mirror of the pooled left map; central roles (GK, CB, CDM, CM, CAM, ST) are pooled with their own mirror, so their maps are symmetric. "
        "count includes the mirrored copies.",
        f"Fallback (cells with count < {cfg.pos_min_count}): the role's mean position in that possession state (baseline) plus the role group's mean shift for that ball cell "
        "(fallback.<GROUP>: displacement from each role's own baseline, left orientation, pooled over the group's roles). If the group cell is also below the threshold, the "
        "baseline is used. source tells which: 'role', 'group' or 'baseline'. sd_x/sd_y of fallback cells are the group's (or the role's overall) spread.",
        "CM has no DFL samples (DFL has no plain central-midfield code; HL/HR map to LCM/RCM): its baseline is the CM group's mean x with y = 0 and every cell comes from the group or the baseline.",
        f"Cells per source: {src_counts}",
    ]
    out["position_maps.json"] = {"meta": meta_block(matches, cfg, "Mean player positions per role conditioned on the ball cell and possession (DFL)", pos_notes),
                                 "grid": {"x_edges": np.linspace(-cfg.std_pitch_length_m / 2, cfg.std_pitch_length_m / 2, nx + 1).tolist(),
                                          "y_edges": np.linspace(-cfg.std_pitch_width_m / 2, cfg.std_pitch_width_m / 2, ny + 1).tolist(),
                                          "index": "[ix][iy]", "min_count": cfg.pos_min_count},
                                 "maps": pmaps["maps"], "fallback": pmaps["fallback"]}

    em = event_model(rec, cfg)
    ls = em["actions_location_source"]
    ev_notes = [
        "Event types are the provider's (DFL taxonomy, Delete already dropped at load). per90_per_team: STATS over team-matches (zeros included) of the count per 90 "
        "minutes of match time (sum of the tracked period durations, stoppage time included). Team attribute: Team, else WinnerTeam (TacklingGame), else TeamFouler (Foul). "
        "Types without a team (KickOff, FinalWhistle, RefereeBall, VideoAssistantAction) are in per90_per_match.",
        "Zones: 6 x 4 in the acting team's attacking coordinates (team attacks +x, left is +y); keys x{ix}y{iy}, ix 0 = own goal end, iy 0 = right flank.",
        f"Locations: the ball at the synchronised frame where the event was located, otherwise the annotated provider coordinates converted to the canonical frame "
        f"(on-ball actions: {ls}).",
        "pass: open-play Play events (set-piece passes are nested in their restart events). Length, success, direction and speed use located passes only. "
        "Success = the next touch (from the ball trajectory) is by a team-mate; annotated_success_share is the provider's Evaluation for comparison. "
        "Direction: ball at the kick -> ball at the next touch (or where the ball went dead), degrees, 0 = straight forward, +90 = to the left; signed angles do not average well, "
        "so direction_sector_share_by_zone gives the share per 45-degree sector. high_share is the annotated Height = high share; height_check compares it with the ball height.",
        "transitions: P(next on-ball action zone | current zone) within the same possession chain. Chain = consecutive on-ball actions (Play, ShotAtGoal, OtherBallAction, "
        "BallClaiming, restarts) of the same team in the same period; a restart starts a new chain and a shot ends one. possession_end_share_by_zone: share of actions in a zone "
        "that are the last of their chain.",
        "shot: open-play ShotAtGoal events (penalties and direct free kicks are separate DFL events). rate_per_possession_by_zone = shots from the zone / chains with an action in "
        "the zone; rate_per_action_by_zone = shots from the zone / on-ball actions in the zone. outcome_shares and head_share are the annotated SubType and TypeOfShot.",
        "possession: runs of the provider's ball possession while the ball is live (a run ends when possession changes or the ball goes dead); passes_per_sequence counts the "
        "possessing team's Play events inside the run (located frame, else the corrected event time). Very short runs are kept (duels make possession flicker): see share_shorter_than_1s "
        "and passes_per_sequence_at_least_1s. Set-piece passes are nested in their restart events and are not counted as Play. passes_per_event_chain / actions_per_event_chain "
        "use the event chains of 'transitions' (closer to the usual 'passes per possession').",
        "ball_in_play_share: provider ball status live time / tracked time, all matches pooled.",
    ]
    out["event_model.json"] = {"meta": meta_block(matches, cfg, "Event model: frequencies, zones, passes, transitions, shots, set pieces, possession (DFL)", ev_notes), **em}

    bm = ball_model(rec, cfg)
    ball_notes = [
        f"Our kinematics only (Savitzky-Golay ball speed on valid live samples). pass_speed_ms (with p99, read by R-SHOT-POWER): peak valid ball speed in the {cfg.kick_speed_window_s:g} s after the kick of a located "
        "open-play pass, stopped before the next touch. long_pass_speed_ms: the same for passes >= "
        f"{cfg.long_pass_m:g} m. shot_speed_ms: peak valid ball speed in the {cfg.kick_speed_window_s:g} s after the kick of a confirmed open-play shot (Materials/METRICS.md), "
        f"headers excluded (v4.2: ball above {PREP_CONFIG.aerial_min_height_m:g} m at the kick, the header test of R-SHOT-POWER); soft shots included.",
        "circulation_speed_ms: every valid live ball sample with a team in possession (sample level); circulation_speed_median_per_team_match_ms: one median per team-match.",
        f"progression_speed_ms: forward ball displacement (attacking direction) / duration of possession runs >= {cfg.progression_min_s:g} s.",
        f"air_time_high_pass_s: located passes annotated high; from the kick to the first landing (ball back below {cfg.air_landing_m:g} m after rising above {cfg.air_rise_m:g} m) "
        "or to the next touch, whichever comes first; passes whose ball never rises above the threshold, or with invalid ball samples in the flight, are left out.",
        "Realism range for maximal kicks: 18-35 m/s (Kellis & Katis 2007, Materials/PHYSICAL_LIMITS.md). Limit: 211 km/h in live play.",
    ]
    out["ball.json"] = {"meta": meta_block(matches, cfg, "Ball speeds and air time (DFL, our kinematics)", ball_notes), **bm}

    xm, shots_used = xg_model(rec, cfg)
    xg_notes = [
        "Materials/METRICS.md section 9. Open-play shots (DFL ShotAtGoal) located from the trajectories; features measured at the synchronised frame in the shooting team's "
        "attacking coordinates: distance_m from the ball to the centre of the goal line; visible_angle_rad = atan2(w x, x^2 + y^2 - (w/2)^2) with w = 7.32 m, x the distance "
        "from the goal line and y the lateral offset; opponents_in_triangle = opposing outfield players with valid samples inside the ball-post-post triangle.",
        "Use from Python: insidethegame_data.calibration.xg(distance_m, visible_angle_rad, opponents_in_triangle); features: shot_features(...).",
    ]
    if xm.get("coef") is None:
        xg_notes.append(f"Model not fitted: {xm['reason']}.")
    else:
        lomo = xm["leave_one_match_out"]
        xg_notes += [
            f"Label: annotated outcome SuccessfulShot (team decision 2026-10-24). Agreement with the tracking goal-line crossing: "
            f"{xm['label_agreement_with_tracking']['both']} of {xm['n_goals']} annotated goals are also detected as a crossing.",
            f"{xm['n_dropped_not_located']} of {xm['n_open_play_shots_annotated']} open-play shots could not be located and are left out "
            f"({xm['goals_not_located']} of them goals).",
            f"Model updating (v4.2, Materials/METRICS.md section 9): logit(xG) = a + {cfg.xg_angle_slope:g} * visible_angle_rad + b * opponents_in_triangle. "
            f"The angle slope is fixed ({cfg.xg_angle_slope_source}); only a and b are fitted on the {xm['n_shots']} located open-play shots ({xm['n_goals']} goals), "
            f"two parameters as the rule of about 10 events per variable allows (Peduzzi et al. 1996); L2 = {cfg.xg_l2:g} on the standardised b. "
            "The distance coefficient is 0 (coef[0]): the visible angle already contains the distance. The first fit (three free slopes) gave the angle a wrong, "
            "negative sign; it is replaced.",
            f"Checks: b < 0 {xm['checks']['b_negative']}; 6 m central > penalty spot > edge of the box "
            f"{xm['checks']['six_m_central_above_penalty_spot_above_edge_of_box']}; sum of xG {xm['checks']['sum_xg_vs_goals']['sum_xg']:.2f} vs "
            f"{xm['checks']['sum_xg_vs_goals']['goals']} goals; calibration_table; examples.",
            f"Leave-one-match-out log loss {lomo['log_loss']:.4f} vs {lomo['baseline_constant_rate_log_loss']:.4f} for a constant rate. Use it for relative chance "
            "quality (a wider visible angle and fewer defenders give a higher xG); do not read single coefficients as causal effects.",
            f"Penalties: a fixed penalty_xg = {cfg.penalty_xg:g} (Opta, as reported by FBref), counted in the team's xG; direct free kicks use the same model; headers "
            "and the goalkeeper position have no feature of their own (too few goals).",
            f"Opponents with an invalid sample within 20 m of the ball are not counted (never use an invalid value): this happens on "
            f"{xm['shots_with_invalid_opponent_within_20m']} shots.",
        ]
    out["xg_model.json"] = {"meta": meta_block(matches, cfg, "Expected-goals model (visible angle with a fixed slope plus opponents in the shot triangle, DFL open-play shots)",
                                               xg_notes), **xm}

    ats = attribute_scales(pm, rec, cfg)
    small_tabs = [f"{a}/{g}: n={t['n']}" for a, v in ats.items() for g, t in (v.get("by_role_group") or {}).items() if t and t["n"] < 15]
    missing = [f"{a}/{g}" for a, v in ats.items() for g, t in (v.get("by_role_group") or {}).items() if t is None]
    at_notes = [
        f"Privacy (v4.2, team decision 2026-10-25): each table stores only the {len(cfg.attribute_quantile_levels_pct)} quantiles at quantile_levels_pct "
        "(p2.5, p5, p10, ..., p90, p95, p97.5; linear interpolation of the per-player-match metric), with no minimum and no maximum, as realism_reference.json. "
        "values[k-1] (k = 1..99, the format the generator reads) is interpolated linearly from those quantiles at level k, flat below p2.5 and above p97.5 "
        "(attributes 1-2 equal p2.5, 98-99 equal p97.5). Known limit, accepted (guidelines section 19; CC-BY aggregates): with linear interpolation a "
        "stored quantile can coincide with one player-match value, when its position p x (n - 1) / 100 is a whole number (for example the median of an odd n). "
        "A 1-99 attribute k maps to values[k-1]; "
        "use insidethegame_data.calibration.attribute_to_value / value_to_attribute (clipped to 1..99).",
        f"pace, acceleration, stamina, work_rate: player-matches with >= {cfg.min_valid_minutes:g} valid minutes (as in physical_profiles.json). "
        f"passing: player-matches with >= {cfg.passing_min_passes} located open-play passes. shot_power: per confirmed open-play shot without headers (as ball.json shot_speed_ms), overall only.",
        f"Tables need n >= {cfg.percentile_min_n}; missing (null): {missing or 'none'}. Small tables (n < 15), coarse percentiles: {small_tabs or 'none'}.",
        "With 6 matches the tails are coarse on purpose: between stored levels the values are linear, and the extremes are flat.",
    ]
    out["attribute_scales.json"] = {"meta": meta_block(matches, cfg, "Percentile tables to map 1-99 player attributes to physical values (DFL)", at_notes), "attributes": ats}

    ap = artefact_profile(matches)
    band = acc_band_profile(matches)
    ap_notes = [
        "Aggregates of the committed preparation reports (reports/qc/<id>/qc_results.json, match_profile.json), STATS across matches. Rates are per player-hour of tracking.",
        "Optional input for the generator: realistic provider artefacts (jumps, swaps, frozen runs, tracking-area edges, identity inheritance at substitutions) and the event "
        "clock offset and dispersion of the annotations (event time minus physical time is about -offset, with the given MAD).",
        "limit_episodes_per_player_hour: true limit episodes only, player speed above 38 km/h (R-SPEED-LIMIT); ball_limit_episodes: live ball speed above 211 km/h "
        "(R-BALL-LIMIT), count per match. Since guidelines v4 neither includes accelerations outside the band.",
        "Acceleration spikes (R-ACC-BAND, v4): tangential acceleration outside -8.5..+7.5 m/s2 never excludes a sample; a run of at most 0.2 s (5 samples at 25 Hz) is "
        "'noise' (value not used), a longer run is 'approx' (value kept with '≈'). acc_band.per_match and acc_band.pooled give runs, samples, runs per player-hour and mean "
        "run length (samples) by class and sign (acceleration > +7.5, deceleration < -8.5); per_match_stats.acc_band_<class>_<sign>_* give their STATS across matches. "
        "Pooled rate = total runs / total player-hours. The counts depend on the acceleration filter window (acc_window_samples: 19 samples, chosen by the team "
        "after the v4 sensitivity test, R-ACC-WINDOW-TEST). The pipeline derives acceleration from positions, so a generator reproduces these runs as abrupt velocity changes in the positions, "
        "never by writing acceleration values. A match without runs of a kind has mean_run_samples null (and is left out of its STATS).",
    ]
    out["artefact_profile.json"] = {"meta": meta_block(matches, cfg, "Provider artefact profile (DFL/TRACAB, per match)", ap_notes), "per_match_stats": ap,
                                    "acc_band": band}

    ref = rm.build_reference(realism_samples, cfg, rcfg, shot_crosstab=rm.merge_crosstabs(realism_shots))
    rg = ref["role_groups"]
    rr_notes = [
        "Reference of the Linesman realism report (guidelines section 12; data_pipeline/insidethegame_data/realism.py). Every metric is computed by "
        "realism_metrics.match_samples on the frame-level outputs of the preparation pipeline, the same code that runs on the synthetic matches.",
        f"quantiles: the {len(rcfg.quantile_levels_pct)} quantiles at quantile_levels_pct (p2.5, p5, p10, p15, ..., p90, p95, p97.5; linear "
        "interpolation) of the values pooled over the matches. No minimum (p0) and no maximum (p100) are stored (privacy, team decision 2026-10-25). "
        "Known limit, accepted (guidelines section 19; CC-BY aggregates): with linear interpolation a stored quantile can coincide with one value, when "
        "its position p x (n - 1) / 100 is a whole number, and a per-match median of an odd number of player-matches is one player-match value. The reference CDF "
        "is linear between these quantiles, 0 below p2.5 and 1 from p97.5: against the full DFL distribution it is off by at most 0.025 in each tail "
        "and by at most the spacing of two stored levels (0.05) between them, and so is the KS distance D. "
        "match_median_range: min and max of the per-match medians (matches with at least one value). n: pooled values. "
        "No person or object IDs are stored; match IDs appear only in meta.",
        "comparison: ks_and_range = many values per match (player-matches, passes, shots, ball samples, live phases, possession spells, team frames): "
        "one-sample KS distance against the reference CDF plus the median rule; range_only = one value per match or per team-match: with 6 matches "
        "a KS test is meaningless, only the median rule applies.",
        "Physical metrics: player-matches with >= "
        f"{cfg.min_valid_minutes:g} valid minutes, per 90 minutes of measured time (as physical_profiles.json), by realism role group: "
        + "; ".join(f"{g} = {'+'.join(v['calibration_groups'])} (n = {v['n_player_matches']})" for g, v in rg.items())
        + ". DM, CM (2 DFL player-matches) and AM (9) are merged into MF: CM and AM are too small for a distribution.",
        f"Accelerations / decelerations: runs of consecutive valid samples with tangential acceleration above +{rcfg.acc_effort_ms2:g} / below "
        f"-{rcfg.acc_effort_ms2:g} m/s2 lasting >= {rcfg.acc_effort_min_s:g} s (R-ACC-BAND noise samples have no acceleration). A realism metric only, "
        "never an outlier rule (guidelines section 8).",
        "Events per team: the team attribute of the event (Team, else WinnerTeam, else TeamFouler, as event_model.json); per 90 minutes of match time "
        "(sum of the tracked period durations). Clearances are the pipeline's event_kind 'clearance' (OtherBallAction with DefensiveClearance = true). "
        "Shot outcomes are the annotated labels mapped to English (section 16.3; OtherShot -> wide).",
        "Passes: located open-play passes; success = next touch by a team-mate (trajectory, R-PASS-OUTCOME), by the third of the kick location "
        "(attacking coordinates). Team shape: outfield players only, max - min of x (length) and y (width), scaled to 105 x 68 m.",
        "This file can be written alone, leaving the other calibration files untouched: python -m insidethegame_data.calibration --repo <work dir> "
        "--out ../calibration --only realism_reference.json, where <work dir> holds the qc outputs (qc --reports-dir <work dir>/reports/qc; the "
        "Linesman uses reports/qc/frames/_realism). input_hashes identify the frame-level files it was built from.",
        "diagnostics.shot_outcome_label_vs_observed: shots by annotated outcome label (English) and by the outcome the pipeline observes on the "
        "trajectory (R-SHOT-OUTCOME, R-WOODWORK; 'none' = no coherent outcome or not located), pooled counts.",
    ]
    out["realism_reference.json"] = {"meta": meta_block(matches, cfg, "DFL reference distributions for the Linesman realism report (synthetic vs real)",
                                                        rr_notes) | {"realism_config_hash": rcfg.hash(), "realism_config": rcfg.to_dict()},
                                     **ref}

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in out.items():
        if only and name not in only:
            continue
        text = dumps(obj, cfg.float_decimals)
        p = out_dir / name
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
        log(f"wrote {p}")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Write the calibration aggregates for the synthetic generator (guidelines v4, section 11).")
    ap.add_argument("match_ids", nargs="*", help="DFL match ids (default: every DFL match with frame-level outputs)")
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: <repo>/calibration)")
    ap.add_argument("--only", nargs="+", default=None, metavar="FILE", choices=FILES,
                    help="write only these files (for example realism_reference.json); every file is still computed")
    a = ap.parse_args(argv)
    run(a.match_ids or None, a.repo, a.out, only=a.only)


if __name__ == "__main__":
    main()
