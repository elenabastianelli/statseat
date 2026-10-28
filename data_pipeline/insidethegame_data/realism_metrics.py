"""Linesman metrics: the same realism metrics for real (DFL) and synthetic matches (guidelines v4, section 12).

The Linesman check compares a synthetic match with the DFL matches "by running the same preparation
pipeline on both". This module is the one place where the metrics are defined, so both sides are
computed by the same code:

* ``calibration.py`` calls :func:`match_samples` on every prepared DFL match and writes the reference
  distributions with :func:`build_reference` to ``calibration/realism_reference.json``;
* ``realism.py`` calls :func:`match_samples` on the synthetic match and its replicas and compares them
  with that reference.

Every input is a :class:`~insidethegame_data.calibration.MatchData` loaded from the frame-level outputs
of ``qc.py``, so the rules of the guidelines hold on both sides: valid samples only (``valid_kinematics``),
estimated gap samples never count for speed or peaks, the acceleration of ``R-ACC-BAND`` noise samples is
never used, ball status and possession are the provider's, event locations come from the synchronised
frame. Physical rates are per 90 minutes of **measured** time, as in ``physical_profiles.json`` (team
decision 2026-10-24).

Metric groups:

* ``physical`` - per player-match, by role group (GK, CB, FB, MF, W, ST; see ``REALISM_GROUP``);
* ``ball_play`` - ball in play, possession, live ball speed, live phases, possession spells;
* ``passes`` - passes per team, length, speed, success by third;
* ``shots`` - shots per team, distance, speed, xG, outcome mix;
* ``events`` - rates per 90 of the DFL event types;
* ``team_shape`` - team length and width (compactness), in and out of possession.

``comparison`` says how a metric is judged: ``ks_and_range`` (many values per match: one-sample KS plus
the median rule) or ``range_only`` (one value per match or per team-match: with 6-7 matches a KS test is
meaningless, so only the median rule applies).

Nothing here identifies a person or an object: player IDs are used in memory only, to group samples.
Deterministic: no randomness, no wall-clock time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .calibration import (CAL_GROUP, DEFAULT_CAL_CONFIG, CalibrationConfig, EventRecords, MatchData, collect_events, confirmed_shot_speeds,
                          count_runs, event_team, player_match_table, xg)
from .mapping import map_shot_outcome

# Role groups of the realism report. The calibration groups DM (20 DFL player-matches), CM (2) and AM (9) are
# merged into MF: CM and AM have fewer than 10 DFL player-matches, too few for a distribution (task E-2).
REALISM_GROUP = {"GK": "GK", "CB": "CB", "FB": "FB", "DM": "MF", "CM": "MF", "AM": "MF", "W": "W", "ST": "ST"}
PHYS_GROUPS = ("GK", "CB", "FB", "MF", "W", "ST")
GROUPS = ("physical", "ball_play", "passes", "shots", "events", "team_shape")
SHOT_OUTCOMES_EN = ("goal", "saved", "blocked", "wide", "woodwork")
THIRDS = ("own", "middle", "final")
# DFL event types counted per team and per 90 minutes ("clearance" is the pipeline's event_kind of an
# OtherBallAction with DefensiveClearance = true; R-CLEARANCE, guidelines 9.9).
EVENT_RATES = (("fouls", "Foul"), ("corners", "CornerKick"), ("throw_ins", "ThrowIn"), ("free_kicks", "FreeKick"), ("cards", "Caution"),
               ("tackles", "TacklingGame"), ("recoveries", "BallClaiming"), ("clearances", "clearance"), ("goal_kicks", "GoalKick"),
               ("offsides", "Offside"))


# Quantile levels (percent) stored in the DFL reference: p2.5, p5, p10, p15, ..., p90, p95, p97.5 (21 levels). No minimum (p0)
# and no maximum (p100) (privacy fix, 2026-10-25). Known limit, accepted (guidelines section 19; CC-BY aggregates): with linear
# interpolation a quantile whose position p * (n - 1) / 100 is a whole number coincides with one player-match value
# (calibration.exact_order_statistic_levels).
# The KS CDF is linear between them, 0 below p2.5 and 1 from p97.5.
QUANTILE_LEVELS_PCT = (2.5, 5.0, *[float(p) for p in range(10, 95, 5)], 95.0, 97.5)


@dataclass(frozen=True)
class RealismConfig:
    """Parameters of the realism metrics and of the traffic light (recorded and hashed in every output)."""

    quantile_levels_pct: tuple[float, ...] = QUANTILE_LEVELS_PCT  # the 21 quantiles of the DFL reference (no p0, no p100)
    acc_effort_ms2: float = 3.0  # acceleration effort: tangential acceleration above +3 m/s2 (STATSports; a metric, never an outlier rule)
    acc_effort_min_s: float = 0.5  # ... for at least 0.5 s of consecutive valid samples (about the 13-sample filter window)
    peak_window_s: float = 300.0  # 5-minute peak: highest measured distance in a rolling 5-minute window
    peak_min_valid_frac: float = 0.9  # ... where the player is on the pitch for the whole window and >= 90 % of it is measured
    shape_sample_s: float = 1.0  # team shape: one live frame per second
    shape_min_outfield: int = 9  # ... with at least 9 outfield players of the team on the pitch, all with valid samples
    possession_spell_min_s: float = 1.0  # possession spells shorter than 1 s (duels make possession flicker) are left out
    green_ks_max: float = 0.15  # traffic light: green needs D <= 0.15 ...
    yellow_ks_max: float = 0.30  # ... yellow up to 0.30, red above
    range_tolerance_frac: float = 0.10  # "just outside" the DFL per-match median range: within 10 % of its width
    small_sample_n: int = 20  # fewer values on either side: the result is indicative
    ks_noise_c_alpha: float = 1.358  # two-sample KS critical coefficient at 5 %: the "D by chance" diagnostic (never in the light)
    float_decimals: int = 4

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d: dict) -> "RealismConfig":
        """The configuration recorded in a JSON meta block (lists back to tuples; unknown keys ignored)."""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items() if k in known})


DEFAULT_REALISM_CONFIG = RealismConfig()


@dataclass(frozen=True)
class Metric:
    key: str  # unique: "<group>.<name>" or "physical.<name>.<role group>"
    group: str
    name: str
    unit: str
    observation: str  # what one value is: player-match, pass, shot, ...
    comparison: str  # ks_and_range | range_only
    definition: str
    role_group: str | None = None


def _physical_names(cal: CalibrationConfig, rc: RealismConfig) -> list[tuple[str, str, str]]:
    zones = [(f"zone_{lab.replace('-', '_').replace('+', '_plus')}_per90_m", "m",
              f"distance per 90 minutes of measured time in the FIFA speed zone {lab} km/h (R-ZONES)") for lab in cal.speed_zone_labels]
    return [
        ("distance_per90_m", "m", "distance (valid speed x dT) per 90 minutes of measured time"),
        *zones,
        ("distance_above_20kmh_per90_m", "m", "distance above 20 km/h (zones 20-25 and 25+) per 90 minutes of measured time"),
        ("top_speed_kmh", "km/h", "maximum valid speed of the player-match"),
        ("sprints_per90", "count", f"sprints (valid samples above {cal.sprint_speed_kmh:g} km/h for >= {cal.sprint_min_s:g} s, R-SPRINT) per 90 minutes of measured time"),
        ("peak_5min_m", "m", f"highest measured distance in a rolling {rc.peak_window_s / 60:g}-minute window (whole window on the pitch, "
                             f">= {100 * rc.peak_min_valid_frac:g} % measured, scaled to the measured time)"),
        ("accelerations_per90", "count", f"accelerations (valid tangential acceleration above +{rc.acc_effort_ms2:g} m/s2 for >= {rc.acc_effort_min_s:g} s) "
                                         "per 90 minutes of measured time"),
        ("decelerations_per90", "count", f"decelerations (valid tangential acceleration below -{rc.acc_effort_ms2:g} m/s2 for >= {rc.acc_effort_min_s:g} s) "
                                         "per 90 minutes of measured time"),
    ]


def metric_catalogue(cal: CalibrationConfig = DEFAULT_CAL_CONFIG, rc: RealismConfig = DEFAULT_REALISM_CONFIG) -> list[Metric]:
    """Every realism metric, in report order."""
    out: list[Metric] = []
    for name, unit, definition in _physical_names(cal, rc):
        for g in PHYS_GROUPS:
            out.append(Metric(f"physical.{name}.{g}", "physical", name, unit, "player-match", "ks_and_range", definition, g))
    ks, rng = "ks_and_range", "range_only"
    out += [
        Metric("ball_play.ball_in_play_share", "ball_play", "ball_in_play_share", "share", "match", rng,
               "time with the provider's ball status live / tracked time"),
        Metric("ball_play.possession_dominant_share", "ball_play", "possession_dominant_share", "share", "match", rng,
               "possession share of the team with more possession (live samples, provider possession)"),
        Metric("ball_play.ball_speed_live_kmh", "ball_play", "ball_speed_live_kmh", "km/h", "ball sample", ks,
               "valid ball speed in live play (every valid live sample)"),
        Metric("ball_play.live_phase_duration_s", "ball_play", "live_phase_duration_s", "s", "live phase", ks,
               "duration of each run of the provider's ball status live"),
        Metric("ball_play.possession_spell_duration_s", "ball_play", "possession_spell_duration_s", "s", "possession spell", ks,
               f"duration of each run of one team's possession while the ball is live (runs >= {rc.possession_spell_min_s:g} s)"),
        Metric("passes.passes_per90", "passes", "passes_per90", "count", "team-match", rng,
               "open-play passes (DFL Play events) per team per 90 minutes of match time"),
        Metric("passes.pass_length_m", "passes", "pass_length_m", "m", "pass", ks, "length of each located open-play pass (kick to next touch)"),
        Metric("passes.pass_speed_kmh", "passes", "pass_speed_kmh", "km/h", "pass", ks,
               f"peak valid ball speed in the {cal.kick_speed_window_s:g} s after the kick of a located open-play pass"),
        *[Metric(f"passes.pass_success_{t}_third", "passes", f"pass_success_{t}_third", "share", "team-match", rng,
                 f"share of located open-play passes from the {t} third whose next touch is a team-mate's (trajectory, R-PASS-OUTCOME)")
          for t in THIRDS],
        Metric("shots.shots_per_match", "shots", "shots_per_match", "count", "match", rng,
               "shots (DFL ShotAtGoal events) of both teams in the match, raw count"),
        Metric("shots.shots_per90", "shots", "shots_per90", "count", "team-match", rng,
               "shots (DFL ShotAtGoal events; set-piece shots are nested in their restart) per team per 90 minutes of match time"),
        Metric("shots.shot_distance_m", "shots", "shot_distance_m", "m", "shot", ks,
               "distance from the ball to the centre of the goal line at the located kick"),
        Metric("shots.shot_speed_kmh", "shots", "shot_speed_kmh", "km/h", "shot", ks,
               f"peak valid ball speed in the {cal.kick_speed_window_s:g} s after the kick of a confirmed shot, headers excluded "
               "(calibration.confirmed_shot_speeds)"),
        Metric("shots.shot_xg", "shots", "shot_xg", "xG", "shot", ks,
               "expected goals of each located shot (committed calibration/xg_model.json, features from shot_features)"),
        *[Metric(f"shots.shot_share_{o}", "shots", f"shot_share_{o}", "share", "match", rng,
                 f"share of the match's shots with the outcome label '{o}' (section 16.3: provider annotation or simulation label)")
          for o in SHOT_OUTCOMES_EN],
        *[Metric(f"events.{name}_per90", "events", f"{name}_per90", "count", "team-match", rng,
                 f"{etype} {'events (event_kind)' if etype == 'clearance' else 'events'} per team per 90 minutes of match time")
          for name, etype in EVENT_RATES],
    ]
    for dim, axis in (("length", "x (along the pitch)"), ("width", "y (across the pitch)")):
        for state, text in (("in_poss", "own team in possession"), ("out_poss", "opponent in possession")):
            out.append(Metric(f"team_shape.team_{dim}_{state}_m", "team_shape", f"team_{dim}_{state}_m", "m", "team-frame", ks,
                              f"spread of the team's outfield players along {axis}, max - min, {text}; one live frame per "
                              f"{rc.shape_sample_s:g} s with >= {rc.shape_min_outfield} outfield players, all valid"))
    return out


# ---------------------------------------------------------------------------
# Per player-match extras: rolling 5-minute peak and acceleration efforts
# ---------------------------------------------------------------------------

def rolling_peak(period: np.ndarray, frame: np.ndarray, speed: np.ndarray, valid: np.ndarray, fs: float, window_s: float,
                 min_valid_frac: float) -> float:
    """Highest measured distance in a rolling window (m per ``window_s``), NaN when no window qualifies.

    A window qualifies when the player has a sample on every frame of it (one period) and at least
    ``min_valid_frac`` of them are valid; its distance (valid speed x dT) is scaled to the measured time.
    """
    dt = 1.0 / fs
    w = int(round(window_s * fs))
    best = np.nan
    for h in np.unique(period):
        m = period == h
        f = frame[m]
        if len(f) < w:
            continue
        n = int(f.max() - f.min() + 1)
        idx = (f - f.min()).astype(np.int64)
        present = np.zeros(n)
        vmask = np.zeros(n)
        dist = np.zeros(n)
        present[idx] = 1.0
        ok = valid[m] & np.isfinite(speed[m])
        vmask[idx] = ok
        dist[idx] = np.where(ok, speed[m], 0.0) * dt
        cp, cv, cd = (np.r_[0.0, np.cumsum(a)] for a in (present, vmask, dist))
        if n < w:
            continue
        sp, sv, sd = cp[w:] - cp[:-w], cv[w:] - cv[:-w], cd[w:] - cd[:-w]
        q = (sp >= w - 0.5) & (sv >= min_valid_frac * w)
        if q.any():
            vals = sd[q] / (sv[q] * dt) * window_s
            best = float(np.nanmax([best, vals.max()])) if np.isfinite(best) else float(vals.max())
    return best


def effort_counts(period: np.ndarray, frame: np.ndarray, acc: np.ndarray, valid: np.ndarray, fs: float, threshold: float, min_s: float) -> tuple[int, int]:
    """Runs of consecutive valid samples with tangential acceleration above +threshold / below -threshold lasting >= min_s.

    ``acc`` is NaN for samples without a usable acceleration (``R-ACC-BAND`` noise, segments shorter than the
    filter window): a NaN ends a run.
    """
    contiguous = np.r_[False, (np.diff(frame) == 1) & (np.diff(period) == 0)]
    n_min = int(round(min_s * fs))
    a = np.where(valid & np.isfinite(acc), acc, 0.0)
    ok = valid & np.isfinite(acc)
    return count_runs(ok & (a > threshold), contiguous, n_min), count_runs(ok & (a < -threshold), contiguous, n_min)


def physical_rows(md: MatchData, cal: CalibrationConfig, rc: RealismConfig) -> pd.DataFrame:
    """Eligible player-matches of one match with the realism physical metrics (player IDs dropped)."""
    pm = player_match_table([md], cal)
    if pm.empty:
        return pd.DataFrame(columns=["role_group"])
    extras = {}
    p = md.players
    ids = p["object_id"].to_numpy()
    bounds = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        g = p.iloc[a:b]
        per, fr = g["period"].to_numpy(), g["frame"].to_numpy()
        sp, acc, val = g["speed"].to_numpy(), g["acc_tan"].to_numpy(), g["valid"].to_numpy()
        n_acc, n_dec = effort_counts(per, fr, acc, val, md.fs, rc.acc_effort_ms2, rc.acc_effort_min_s)
        extras[ids[a]] = (rolling_peak(per, fr, sp, val, md.fs, rc.peak_window_s, rc.peak_min_valid_frac), n_acc, n_dec)
    pm = pm.loc[(pm["valid_minutes"] >= cal.min_valid_minutes) & pm["group"].notna()].copy()
    pm["role_group"] = pm["group"].map(REALISM_GROUP)
    f90 = 90.0 / pm["valid_minutes"].to_numpy(float)
    ex = np.array([extras[pid] for pid in pm["player"]], float).reshape(-1, 3)
    pm["peak_5min_m"] = ex[:, 0]
    pm["accelerations_per90"] = ex[:, 1] * f90
    pm["decelerations_per90"] = ex[:, 2] * f90
    pm["distance_above_20kmh_per90_m"] = pm["work_rate_per90_m"]
    for lab in cal.speed_zone_labels:
        pm[f"zone_{lab.replace('-', '_').replace('+', '_plus')}_per90_m"] = pm[f"zone_{lab}_per90_m"]
    return pm.drop(columns=["player"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Ball, team shape and role positions
# ---------------------------------------------------------------------------

def _runs_of(mask: np.ndarray) -> np.ndarray:
    """Lengths (samples) of the True runs of a boolean array."""
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return np.flatnonzero(d == -1) - np.flatnonzero(d == 1)


def ball_samples(md: MatchData, rc: RealismConfig) -> dict[str, np.ndarray]:
    speeds, phases, spells = [], [], []
    home = away = 0
    for _, bt in sorted(md.ball.items()):
        v = bt.speed[bt.valid & bt.live]
        speeds.append(v[np.isfinite(v)] * 3.6)
        phases.append(_runs_of(bt.live) / md.fs)
        key = np.where(bt.live, bt.poss, 0).astype(np.int64)
        brk = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        lens = np.diff(np.r_[brk, len(key)])
        dur = lens[key[brk] > 0] / md.fs
        spells.append(dur[dur >= rc.possession_spell_min_s])
        home += int((bt.live & (bt.poss == 1)).sum())
        away += int((bt.live & (bt.poss == 2)).sum())
    pt = md.profile["playing_time"].values()
    total = sum(float(v["tracking_duration_s"]) for v in pt)
    live = sum(float(v.get("ball_live_s") or 0.0) for v in pt)
    return {
        "ball_play.ball_in_play_share": np.array([live / total]) if total > 0 else np.array([]),
        "ball_play.possession_dominant_share": np.array([max(home, away) / (home + away)]) if home + away else np.array([]),
        "ball_play.ball_speed_live_kmh": np.concatenate(speeds) if speeds else np.array([]),
        "ball_play.live_phase_duration_s": np.concatenate(phases) if phases else np.array([]),
        "ball_play.possession_spell_duration_s": np.concatenate(spells) if spells else np.array([]),
    }


def team_shape_samples(md: MatchData, cal: CalibrationConfig, rc: RealismConfig) -> dict[str, np.ndarray]:
    """Team length and width at one live frame per ``shape_sample_s`` (canonical metres scaled to 105 x 68)."""
    step = max(1, int(round(rc.shape_sample_s * md.fs)))
    sx, sy = cal.std_pitch_length_m / md.L, cal.std_pitch_width_m / md.W
    p = md.players
    gk = np.array([md.role_of.get(pid) == "GK" for pid in p["object_id"].to_numpy()])
    out = {f"team_shape.team_{d}_{s}_m": [] for d in ("length", "width") for s in ("in_poss", "out_poss")}
    for h, bt in sorted(md.ball.items()):
        sel = (p["period"].to_numpy() == h) & ~gk & p["team_side"].isin(["home", "away"]).to_numpy()
        g = p.loc[sel, ["frame", "team_side", "x", "y", "valid"]]
        bi = bt.index(g["frame"].to_numpy())
        keep = (bi >= 0) & (bi % step == 0)
        g, bi = g.loc[keep], bi[keep]
        keep = bt.live[bi] & (bt.poss[bi] > 0)
        g = g.loc[keep].assign(poss=bt.poss[bi[keep]], ok=lambda d: d["valid"] & np.isfinite(d["x"]) & np.isfinite(d["y"]))
        if g.empty:
            continue
        agg = g.groupby(["frame", "team_side"], sort=True).agg(n=("ok", "size"), n_ok=("ok", "sum"), poss=("poss", "first"),
                                                                 xmin=("x", "min"), xmax=("x", "max"), ymin=("y", "min"), ymax=("y", "max")).reset_index()
        agg = agg.loc[(agg["n"] == agg["n_ok"]) & (agg["n"] >= rc.shape_min_outfield)]
        own = np.where(agg["team_side"] == "home", 1, 2)
        inp = agg["poss"].to_numpy() == own
        length = (agg["xmax"] - agg["xmin"]).to_numpy() * sx
        width = (agg["ymax"] - agg["ymin"]).to_numpy() * sy
        out["team_shape.team_length_in_poss_m"].append(length[inp])
        out["team_shape.team_length_out_poss_m"].append(length[~inp])
        out["team_shape.team_width_in_poss_m"].append(width[inp])
        out["team_shape.team_width_out_poss_m"].append(width[~inp])
    return {k: (np.concatenate(v) if v else np.array([])) for k, v in out.items()}


def role_position_sums(md: MatchData, cal: CalibrationConfig = DEFAULT_CAL_CONFIG) -> dict[str, dict[str, list[float]]]:
    """Sums for the mean position per role and possession state, in the team's attacking coordinates (105 x 68).

    Same samples as ``position_maps.json``: player valid, ball live and valid, a team in possession.
    Returns {role: {"in_possession": [n, sum_x, sum_y], "out_of_possession": [...]}} (no person IDs).
    """
    sx, sy = cal.std_pitch_length_m / md.L, cal.std_pitch_width_m / md.W
    out: dict[str, dict[str, list[float]]] = {}
    for pid, g in md.players.groupby("object_id", sort=True):
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
            ok = (bi >= 0) & gh["valid"].to_numpy() & np.isfinite(gh["x"].to_numpy())
            bi0 = np.where(ok, bi, 0)
            ok &= bt.live[bi0] & bt.valid[bi0] & (bt.poss[bi0] > 0)
            if not ok.any():
                continue
            s = md.attack_sign(int(h), side)
            x, y = s * gh["x"].to_numpy()[ok] * sx, s * gh["y"].to_numpy()[ok] * sy
            inp = bt.poss[bi[ok]] == own
            d = out.setdefault(role, {"in_possession": [0, 0.0, 0.0], "out_of_possession": [0, 0.0, 0.0]})
            for state, m in (("in_possession", inp), ("out_of_possession", ~inp)):
                d[state][0] += int(m.sum())
                d[state][1] += float(x[m].sum())
                d[state][2] += float(y[m].sum())
    return out


# ---------------------------------------------------------------------------
# Events: passes, shots, rates
# ---------------------------------------------------------------------------

def _third(zone: str | None, cal: CalibrationConfig) -> str | None:
    if not isinstance(zone, str):
        return None
    ix = int(zone[1:zone.index("y")])
    return THIRDS[min(2, ix * 3 // cal.zone_nx)]


def event_samples(md: MatchData, cal: CalibrationConfig) -> dict[str, np.ndarray]:
    rec = EventRecords()
    collect_events(md, rec, cal)
    out: dict[str, list] = {}
    minutes = {tc["side"]: tc["minutes"] for tc in rec.team_counts}
    counts = {tc["side"]: tc["counts"] for tc in rec.team_counts}
    # Clearances: annotated clearances (event_kind), team from the event's team attribute.
    clear = {"home": 0, "away": 0}
    ev = md.events
    if "event_kind" in ev:
        for kind, attrs in zip(ev["event_kind"], ev["attrs"]):
            side = md.team_side_of.get(event_team(attrs))
            if kind == "clearance" and side in clear:
                clear[side] += 1
    for side in ("home", "away"):
        f = 90.0 / minutes[side] if minutes.get(side) else np.nan
        c = counts.get(side, {})
        out.setdefault("passes.passes_per90", []).append(c.get("Play", 0) * f)
        out.setdefault("shots.shots_per90", []).append(c.get("ShotAtGoal", 0) * f)
        for name, etype in EVENT_RATES:
            n = clear[side] if etype == "clearance" else c.get(etype, 0)
            out.setdefault(f"events.{name}_per90", []).append(n * f)

    out["shots.shots_per_match"] = [sum(counts.get(side, {}).get("ShotAtGoal", 0) for side in ("home", "away"))]
    P = pd.DataFrame([p for p in rec.passes if p.get("located")])
    if len(P):
        out["passes.pass_length_m"] = P["length"].to_numpy(float)
        out["passes.pass_speed_kmh"] = P["speed"].to_numpy(float) * 3.6
        P["third"] = [_third(z, cal) for z in P["zone"]]
        for t in THIRDS:
            vals = []
            for side in ("home", "away"):
                d = P.loc[(P["third"] == t) & (P["side"] == side), "success"]
                if len(d):
                    vals.append(float(d.astype(bool).mean()))
            out[f"passes.pass_success_{t}_third"] = vals

    S = rec.shots
    loc = [s for s in S if s.get("located")]
    out["shots.shot_distance_m"] = [s["distance_m"] for s in loc]
    out["shots.shot_xg"] = [xg(s["distance_m"], s["visible_angle_rad"], s["opponents_in_triangle"]) for s in loc]
    out["shots.shot_speed_kmh"] = confirmed_shot_speeds(rec) * 3.6  # v4.2: headers excluded, as ball.json and shot_power
    if S:
        labels = [map_shot_outcome(s["outcome"])["outcome"] for s in S]
        for o in SHOT_OUTCOMES_EN:
            out[f"shots.shot_share_{o}"] = [sum(lab == o for lab in labels) / len(labels)]
    return {k: np.asarray(v, float) for k, v in out.items()}


def shot_outcome_crosstab(md: MatchData) -> dict[str, dict[str, int]]:
    """Shots by outcome label (annotation or simulation, English) and by the pipeline's observed outcome
    (``R-SHOT-OUTCOME``, ``R-WOODWORK``): {label: {observed: count}}. A diagnostic of the outcome checks."""
    ev = md.events
    out: dict[str, dict[str, int]] = {}
    if "shot_outcome_label" not in ev:
        return out
    s = ev.loc[ev["event_type"] == "ShotAtGoal"]
    for lab, obs in zip(s["shot_outcome_label"], s["shot_outcome_observed"]):
        lab = lab if isinstance(lab, str) else "none"
        obs = obs if isinstance(obs, str) else "none"
        row = out.setdefault(lab, {})
        row[obs] = row.get(obs, 0) + 1
    return out


def merge_crosstabs(tabs) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t in tabs:
        for lab, row in t.items():
            acc = out.setdefault(lab, {})
            for obs, n in row.items():
                acc[obs] = acc.get(obs, 0) + n
    return {lab: dict(sorted(row.items())) for lab, row in sorted(out.items())}


# ---------------------------------------------------------------------------
# One match -> samples of every metric
# ---------------------------------------------------------------------------

def match_samples(md: MatchData, cal: CalibrationConfig = DEFAULT_CAL_CONFIG, rc: RealismConfig = DEFAULT_REALISM_CONFIG) -> dict[str, np.ndarray]:
    """Values of every realism metric for one prepared match: {metric key: 1-D array} (finite values only)."""
    out: dict[str, np.ndarray] = {}
    phys = physical_rows(md, cal, rc)
    for m in metric_catalogue(cal, rc):
        if m.group == "physical":
            d = phys.loc[phys["role_group"] == m.role_group, m.name] if len(phys) else pd.Series(dtype=float)
            out[m.key] = d.to_numpy(float)
    out |= ball_samples(md, rc)
    out |= event_samples(md, cal)
    out |= team_shape_samples(md, cal, rc)
    keys = [m.key for m in metric_catalogue(cal, rc)]
    return {k: (lambda a: a[np.isfinite(a)])(np.asarray(out.get(k, []), float)) for k in keys}


def physical_group_counts(per_match: dict[str, dict[str, np.ndarray]]) -> dict[str, int]:
    """Player-matches per realism role group (from the distance metric, which every eligible player-match has)."""
    return {g: int(sum(len(s.get(f"physical.distance_per90_m.{g}", [])) for s in per_match.values())) for g in PHYS_GROUPS}


# ---------------------------------------------------------------------------
# DFL reference
# ---------------------------------------------------------------------------

def quantiles(values, levels_pct) -> list[float]:
    """The quantiles of ``values`` at ``levels_pct`` (percent, linear interpolation): the reference CDF of a metric."""
    v = np.asarray(values, float)
    return [float(x) for x in np.percentile(v, np.asarray(levels_pct, float))]


def build_reference(per_match: dict[str, dict[str, np.ndarray]], cal: CalibrationConfig = DEFAULT_CAL_CONFIG,
                    rc: RealismConfig = DEFAULT_REALISM_CONFIG, shot_crosstab: dict | None = None) -> dict:
    """Reference distributions from the per-match samples of the DFL matches.

    For each metric: ``n``, ``mean``, the quantiles of the pooled values at ``rc.quantile_levels_pct`` (p2.5, p5,
    p10, ..., p90, p95, p97.5: no minimum and no maximum; see the accepted limit on whole-number positions), and the range
    (min-max) of the per-match medians (``match_median_range``). Match IDs are not written (only in meta).
    ``shot_crosstab``: the pooled shot outcome label vs observed outcome (``shot_outcome_crosstab``), a diagnostic.
    """
    metrics = {}
    for m in metric_catalogue(cal, rc):
        parts = [per_match[mid][m.key] for mid in sorted(per_match) if len(per_match[mid].get(m.key, []))]
        pooled = np.concatenate(parts) if parts else np.array([])
        meds = [float(np.median(p)) for p in parts]
        metrics[m.key] = {
            "group": m.group, "name": m.name, "role_group": m.role_group, "unit": m.unit, "observation": m.observation,
            "comparison": m.comparison, "definition": m.definition,
            "n": int(len(pooled)), "n_matches": len(parts),
            "mean": float(pooled.mean()) if len(pooled) else None,
            "match_median_range": [min(meds), max(meds)] if meds else None,
            "quantiles": quantiles(pooled, rc.quantile_levels_pct) if len(pooled) else None,
        }
    groups = {}
    counts = physical_group_counts(per_match)
    for g in PHYS_GROUPS:
        groups[g] = {"calibration_groups": [c for c, r in REALISM_GROUP.items() if r == g], "n_player_matches": counts[g]}
    out = {"quantile_levels_pct": [float(x) for x in rc.quantile_levels_pct], "role_groups": groups, "metrics": metrics}
    if shot_crosstab is not None:
        out["diagnostics"] = {"shot_outcome_label_vs_observed": shot_crosstab}
    return out
