"""Parameters of the match generator: literature defaults plus optional DFL calibration.

Every file in `calibration/` is optional. When a file (or a key inside it) is missing or has a
shape we do not recognise, the default stays and the summary says so. Nothing here ever reads
real frame-level data: the calibration files are aggregates (DFL open data, Bassek et al.
2025, CC-BY 4.0) written by the data pipeline.

Expected inputs (all optional, tolerant parsing):
- `attribute_scales.json`: per role group, the mapping between the 1-99 `pace` attribute and the
  top-speed percentile (we use: pace ~ percentile of the group's top-speed distribution).
- `physical_profiles.json`: per role group (GK, CB, FB, DM, CM, AM, W, ST), top speed percentiles
  (km/h) and distance per 90 (km, on measured time).
- `position_maps.json`: `grid` (x_edges, y_edges, team-attacking coordinates) and
  `maps[ROLE][in_possession|out_of_possession]` cell means (mean_x, mean_y, count), plus `fallback` per group.
- `event_model.json`: pass success by zone, shot outcome shares, ball-in-play share.
- `ball.json`: pass, long-pass and shot speed distributions (m/s); high-pass air time.
- `xg_model.json`: logistic coefficients.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GROUPS = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")
PROBS = np.array([0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0])

# Default per-player match top speed (km/h) by role group: min, p10, p25, p50, p75, p90, max.
# Literature values for elite football (Bundesliga / Premier League tracking) and the valid
# peaks of our DFL QC reports (24.7-33.5 km/h on J03WPY, 36.1 km/h max over six matches).
DEFAULT_TOP_SPEED_KMH = {
    "GK": [22.0, 23.0, 24.0, 25.3, 26.5, 27.5, 29.0],
    "CB": [27.0, 28.4, 29.3, 30.5, 31.5, 32.4, 33.6],
    "FB": [28.0, 29.4, 30.3, 31.5, 32.5, 33.3, 34.5],
    "DM": [27.0, 28.3, 29.2, 30.4, 31.4, 32.3, 33.5],
    "CM": [27.5, 28.8, 29.7, 30.9, 31.9, 32.8, 34.0],
    "AM": [27.5, 28.9, 29.9, 31.1, 32.1, 33.0, 34.2],
    "W": [28.5, 29.8, 30.8, 31.9, 32.9, 33.8, 35.0],
    "ST": [28.0, 29.3, 30.3, 31.5, 32.5, 33.4, 34.6],
}
# Reference distance per 90 (km, measured time) by group: the DFL p50 of physical_profiles.json
# (2026-10-24) that the controller effort of players.GROUP_WORK was tuned against. If a new
# calibration file gives other values, the effort scales by the ratio.
DEFAULT_DISTANCE_KM = {"GK": 5.63, "CB": 9.69, "FB": 10.08, "DM": 11.0, "CM": 11.82, "AM": 11.37, "W": 10.93, "ST": 10.18}
DEFAULT_ACC_PEAK = [5.4, 5.8, 6.2, 6.6, 6.9, 7.1, 7.3]  # m/s2, peak positive tangential acceleration (by PROBS)

PLAYER_SPEED_LIMIT_MS = 38.0 / 3.6  # Materials/PHYSICAL_LIMITS.md: hard limit, never reached
SPRINT_MARGIN_MS = 0.8  # the scripted sprinter's top speed exceeds every other player's by this
UNSCRIPTED_VMAX_MS = 35.2 / 3.6  # cap of every player's own top speed (DFL max over six matches: 36.1 km/h)
SCRIPTED_SPRINT_MAX_MS = 36.2 / 3.6  # the scripted fastest sprint never exceeds this (hard limit 38 km/h)


@dataclass
class EventDefaults:
    """Markov model of the possession chains (literature defaults, v0.1)."""

    # Pass success of a flat pass by attacking band (own goal -> opponent goal).
    pass_success_by_band: tuple = (0.92, 0.91, 0.88, 0.83, 0.75, 0.66)
    long_pass_penalty: float = 0.24
    cross_success: float = 0.30
    through_success: float = 0.52
    # Shot outcome shares when the shot is not scripted (on target = saved in scripted matches).
    shot_on_target: float = 0.34
    shot_blocked: float = 0.26
    ball_in_play_share: float = 0.57
    # xG (replicas only): logit = b0 + b_dist * distance + b_angle * angle(rad) + b_head * header
    xg_b0: float = -0.35
    xg_b_dist: float = -0.115
    xg_b_angle: float = 1.05
    xg_b_head: float = -0.55
    xg_b_opp: float = 0.0  # per opponent inside the ball-posts triangle
    head_share: float = 0.2


# The DFL shot speeds are *measured* (peak Savitzky-Golay ball speed in the 0.5 s after the kick, horizontal).
# On synthetic straight shots that measure is about 0.87 times the launch speed (launch 76 km/h, measured 64), so
# the launch range is the measured p10-p90 divided by this ratio (L-3, E-2 item 2).
SHOT_MEASURED_TO_LAUNCH = 0.87


@dataclass
class BallDefaults:
    pass_arrival_ms: tuple = (4.0, 8.5)  # arrival speed of a flat pass (uniform range)
    long_pass_speed_ms: tuple = (18.0, 25.0)
    shot_speed_ms: tuple = (17.0, 27.5)
    header_speed_ms: tuple = (10.0, 15.0)
    clearance_speed_ms: tuple = (19.0, 26.5)
    max_unscripted_kick_ms: float = 29.0
    high_pass_air_time_s: tuple = (1.6, 3.0)


@dataclass
class Calibration:
    top_speed_kmh: dict = field(default_factory=lambda: {g: list(v) for g, v in DEFAULT_TOP_SPEED_KMH.items()})
    top_speed_probs: dict = field(default_factory=lambda: {g: PROBS.copy() for g in GROUPS})
    distance_km: dict = field(default_factory=dict)  # calibrated distance per 90 by group, if any
    pace_scale: dict = field(default_factory=dict)  # group -> (pace values, percentiles)
    acc_scale: dict = field(default_factory=dict)  # group -> (probs, peak acceleration m/s2)
    zone_x_edges: list | None = None  # event-model zone grid (team-attacking coordinates)
    zone_y_edges: list | None = None
    pass_success_zone: dict = field(default_factory=dict)
    pass_length_zone: dict = field(default_factory=dict)
    shot_rate_zone: dict = field(default_factory=dict)
    per90_team: dict = field(default_factory=dict)  # DFL targets for the realism report
    position_maps: dict | None = None
    events: EventDefaults = field(default_factory=EventDefaults)
    ball: BallDefaults = field(default_factory=BallDefaults)
    calibrated: list = field(default_factory=list)
    defaults: list = field(default_factory=list)
    files: dict = field(default_factory=dict)  # name -> sha256
    attribute_scales: dict | None = None  # raw attribute_scales.json, for attributes.physical_value

    # --- player physical parameters ------------------------------------------------------

    def top_speed_for_pace(self, group: str, pace: float) -> float:
        """Match top speed (km/h) of a player with this 1-99 pace in his role group."""
        pct = pace / 100.0
        if group in self.pace_scale:
            values, pcts = self.pace_scale[group]
            pct = float(np.interp(pace, values, pcts))
        probs = self.top_speed_probs.get(group, PROBS)
        speeds = self.top_speed_kmh.get(group, DEFAULT_TOP_SPEED_KMH["CM"])
        return float(np.interp(np.clip(pct, 0.0, 1.0), probs, speeds))

    def acc_for_pace(self, group: str, pace: float) -> float:
        probs, vals = self.acc_scale.get(group, (PROBS, np.array(DEFAULT_ACC_PEAK)))
        return float(np.interp(np.clip(pace / 100.0, 0, 1), probs, vals))

    def zone_key(self, x_att: float, y_att: float) -> str | None:
        if self.zone_x_edges is None:
            return None
        ix = int(np.clip(np.searchsorted(self.zone_x_edges, x_att, side="right") - 1, 0, len(self.zone_x_edges) - 2))
        iy = int(np.clip(np.searchsorted(self.zone_y_edges, y_att, side="right") - 1, 0, len(self.zone_y_edges) - 2))
        return f"x{ix}y{iy}"

    def work_rate(self, group: str) -> float:
        """Effort scale relative to the defaults, from the calibrated distance per 90 (clipped)."""
        if group in self.distance_km:
            return float(np.clip(self.distance_km[group] / DEFAULT_DISTANCE_KM[group], 0.8, 1.2))
        return 1.0

    def summary(self) -> dict:
        return {"calibrated": sorted(set(self.calibrated)), "defaults": sorted(set(self.defaults)),
                "files": dict(sorted(self.files.items())),
                "source": ("DFL open data aggregates (Bassek et al. 2025, CC-BY 4.0) via calibration/"
                           if self.calibrated else "literature defaults (v0.1)")}


# --- Tolerant parsing helpers --------------------------------------------------------------


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _percentile_pairs(obj) -> tuple[np.ndarray, np.ndarray] | None:
    """Accepts {"p10": v, "p50": v, ...} (optionally "min"/"max"), {"percentiles": {...}},
    {"mean": m, "sd": s} or a list of values; returns (probabilities, values) sorted."""
    if obj is None:
        return None
    if isinstance(obj, list) and len(obj) >= 3 and all(isinstance(v, (int, float)) for v in obj):
        vals = np.sort(np.asarray(obj, float))
        return np.linspace(0, 1, len(vals)), vals
    if not isinstance(obj, dict):
        return None
    if "percentiles" in obj and isinstance(obj["percentiles"], dict):
        return _percentile_pairs(obj["percentiles"])
    pairs = []
    for k, v in obj.items():
        if not isinstance(v, (int, float)):
            continue
        key = str(k).lower()
        if key in ("min", "p0"):
            pairs.append((0.0, float(v)))
        elif key in ("max", "p100"):
            pairs.append((1.0, float(v)))
        elif key in ("median",):
            pairs.append((0.5, float(v)))
        elif key.startswith("p") and key[1:].replace("_", ".").replace(".", "", 1).isdigit():
            pairs.append((float(key[1:].replace("_", ".")) / 100.0, float(v)))
    if len(pairs) >= 2:
        pairs.sort()
        probs = np.array([p for p, _ in pairs])
        vals = np.maximum.accumulate(np.array([v for _, v in pairs]))
        if probs[0] > 0:  # extend the tails a little so every pace maps inside the range
            probs = np.r_[0.0, probs]
            vals = np.r_[vals[0] - 0.6 * (vals[-1] - vals[0]) * probs[1], vals]
        if probs[-1] < 1:
            probs = np.r_[probs, 1.0]
            vals = np.r_[vals, vals[-1] + 0.6 * (vals[-1] - vals[0]) * (1 - probs[-2])]
        return probs, vals
    if "mean" in obj and ("sd" in obj or "std" in obj):
        m, s = float(obj["mean"]), float(obj.get("sd", obj.get("std")))
        z = np.array([-2.0, -1.2816, -0.6745, 0.0, 0.6745, 1.2816, 2.0])
        return np.array([0.0, 0.10, 0.25, 0.5, 0.75, 0.90, 1.0]), m + s * z
    return None


def _scale_values(values: list) -> tuple[np.ndarray, np.ndarray]:
    """A 1-99 attribute scale given as N percentile values (pace 1 -> 1st percentile ...): (probs, values),
    extended to the 0th and 100th percentile."""
    vals = np.maximum.accumulate(np.asarray(values, float))
    n = vals.size
    probs = np.arange(1, n + 1) / (n + 1.0)
    lo = vals[0] - (vals[1] - vals[0])
    hi = vals[-1] + (vals[-1] - vals[-2])
    return np.r_[0.0, probs, 1.0], np.r_[lo, vals, hi]


def _find_key(d: dict, *needles: str):
    """First value whose key contains every needle (case-insensitive), searching one level deep."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if all(n in str(k).lower() for n in needles):
            return v
    for v in d.values():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if all(n in str(k2).lower() for n in needles):
                    return v2
    return None


def _groups_block(data: dict) -> dict:
    """The dict that holds the role groups, wherever it is ("groups", "by_group", "profiles" or top level)."""
    for key in ("groups", "by_group", "role_groups", "profiles", "roles"):
        if isinstance(data.get(key), dict) and any(g in data[key] for g in GROUPS):
            return data[key]
    return data if any(g in data for g in GROUPS) else {}


def _to_kmh(probs_vals):
    probs, vals = probs_vals
    if np.nanmax(vals) < 15:  # given in m/s
        vals = vals * 3.6
    return probs, vals


def load_calibration(calibration_dir: Path | None) -> Calibration:
    cal = Calibration()
    d = Path(calibration_dir) if calibration_dir else None

    def mark(ok: bool, name: str):
        (cal.calibrated if ok else cal.defaults).append(name)

    def read(name: str) -> dict | None:
        if d is None:
            return None
        path = d / name
        data = _read(path) if path.is_file() else None
        if data is not None:
            cal.files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return data

    # Physical profiles: top speed percentiles and distance per 90 per role group.
    phys = read("physical_profiles.json")
    got_speed, got_dist = False, False
    if phys:
        for g, block in _groups_block(phys).items():
            if g not in GROUPS or not isinstance(block, dict):
                continue
            ts = _find_key(block, "top", "speed")
            pv = _percentile_pairs(ts) if ts is not None else None
            if pv is not None:
                probs, vals = _to_kmh(pv)
                if 15 < np.nanmedian(vals) < 40:
                    cal.top_speed_probs[g], cal.top_speed_kmh[g] = probs, list(vals)
                    got_speed = True
            dist = _find_key(block, "distance", "90")
            if dist is None:
                dist = _find_key(block, "total", "distance")
            dpv = _percentile_pairs(dist) if isinstance(dist, (dict, list)) else None
            val = float(np.interp(0.5, *dpv)) if dpv is not None else (float(dist) if isinstance(dist, (int, float)) else None)
            if val is not None:
                if val > 100:  # metres
                    val /= 1000.0
                if 2.0 < val < 16.0:
                    cal.distance_km[g] = val
                    got_dist = True
    mark(got_speed, "player top speed by role group")
    mark(got_dist, "player work rate (distance per 90) by role group")

    # Attribute scales: pace (1-99) -> top-speed percentile per group.
    scales = read("attribute_scales.json")
    cal.attribute_scales = scales if isinstance(scales, dict) and isinstance(scales.get("attributes"), dict) else None
    got_pace = False
    if scales:
        pace = (scales.get("attributes", {}).get("pace") if isinstance(scales.get("attributes"), dict) else None)
        pace = pace or (scales.get("pace") if isinstance(scales.get("pace"), dict) else _find_key(scales, "pace"))
        if isinstance(pace, dict):
            overall = pace.get("overall")
            groups = dict(pace.get("by_role_group") or _groups_block(pace))
            for g in GROUPS:
                blk = groups.get(g)
                if not (isinstance(blk, dict) and isinstance(blk.get("values"), list)) and isinstance(overall, dict) \
                        and isinstance(overall.get("values"), list) and g != "GK":
                    groups[g] = overall
            for g, block in groups.items():
                if g not in GROUPS:
                    continue
                if isinstance(block, dict) and isinstance(block.get("values"), list) and len(block["values"]) >= 9:
                    pv = _scale_values(block["values"])
                else:
                    pv = _percentile_pairs(block if not isinstance(block, dict) else
                                           (block.get("top_speed_kmh") or block.get("breakpoints") or block))
                if pv is not None:
                    probs, vals = _to_kmh(pv)
                    if 15 < np.nanmedian(vals) < 40:
                        # the scale gives the speed at each pace percentile: use it as the speed distribution
                        cal.top_speed_probs[g], cal.top_speed_kmh[g] = probs, list(vals)
                        got_pace = True
    mark(got_pace, "pace attribute scale")
    got_acc = False
    acc = (scales or {}).get("attributes", {}).get("acceleration") if scales else None
    if isinstance(acc, dict):
        groups = dict(acc.get("by_role_group") or {})
        for g in GROUPS:
            blk = groups.get(g)
            if not (isinstance(blk, dict) and isinstance(blk.get("values"), list)) and g != "GK" \
                    and isinstance(acc.get("overall"), dict) and isinstance(acc["overall"].get("values"), list):
                groups[g] = acc["overall"]
        for g, block in groups.items():
            if g in GROUPS and isinstance(block, dict) and isinstance(block.get("values"), list) and len(block["values"]) >= 9:
                probs, vals = _scale_values(block["values"])
                if 3.0 < np.median(vals) < 9.0:
                    cal.acc_scale[g] = (probs, np.minimum(vals, 7.3))
                    got_acc = True
    mark(got_acc, "acceleration attribute scale")

    # Position maps.
    pm = read("position_maps.json")
    if pm and isinstance(pm.get("maps"), dict) and isinstance(pm.get("grid"), dict):
        cal.position_maps = pm
        mark(True, "role mean positions given ball zone and possession")
    else:
        mark(False, "role mean positions given ball zone and possession")

    # Event model.
    em = read("event_model.json")
    got_events = False
    if em:
        ib = _find_key(em, "ball", "in", "play")
        if isinstance(ib, dict):
            ib = ib.get("share", ib.get("mean", ib.get("median")))
        if isinstance(ib, (int, float)):
            share = float(ib) / (100.0 if ib > 1.5 else 1.0)
            if 0.4 < share < 0.75:
                cal.events.ball_in_play_share = share
                got_events = True
        so = _find_key(em, "shot", "outcome")
        if isinstance(so, dict):
            tot = sum(float(v) for v in so.values() if isinstance(v, (int, float))) or 1.0
            on = sum(float(v) for k, v in so.items() if isinstance(v, (int, float)) and
                     any(s in k.lower() for s in ("saved", "successful", "goal", "on_target")))
            bl = sum(float(v) for k, v in so.items() if isinstance(v, (int, float)) and "block" in k.lower())
            if 0 < on / tot < 1:
                cal.events.shot_on_target = on / tot
                cal.events.shot_blocked = bl / tot
                got_events = True
    if em:
        zones = em.get("zones", {})
        if isinstance(zones.get("x_edges"), list) and isinstance(zones.get("y_edges"), list):
            cal.zone_x_edges, cal.zone_y_edges = list(map(float, zones["x_edges"])), list(map(float, zones["y_edges"]))
            ps = (em.get("pass") or {}).get("success_share_by_zone") or {}
            cal.pass_success_zone = {k: float(v) for k, v in ps.items() if isinstance(v, (int, float))}
            pl = (em.get("pass") or {}).get("length_m_by_zone") or {}
            cal.pass_length_zone = {k: float(v["p50"]) for k, v in pl.items() if isinstance(v, dict) and "p50" in v}
            sr = (em.get("shot") or {}).get("rate_per_action_by_zone") or {}
            cal.shot_rate_zone = {k: float(v) for k, v in sr.items() if isinstance(v, (int, float))}
            got_events = got_events or bool(cal.pass_success_zone)
        hs = (em.get("shot") or {}).get("head_share")
        if isinstance(hs, (int, float)) and 0 < hs < 0.6:
            cal.events.head_share = float(hs)
        for k, v in (em.get("per90_per_team") or {}).items():
            if isinstance(v, dict) and "mean" in v:
                cal.per90_team[k] = float(v["mean"])
    mark(got_events, "event model (ball in play share, shot outcomes, pass success and length by zone, shot rate by zone)")

    # Ball speeds.
    bj = read("ball.json")
    got_ball = False
    if bj:
        for attr, needles in (("shot_speed_ms", ("shot", "speed")), ("long_pass_speed_ms", ("long", "pass"))):
            pv = _percentile_pairs(_find_key(bj, *needles))
            if pv is not None:
                probs, vals = pv
                if np.nanmax(vals) > 60:  # km/h
                    vals = vals / 3.6
                lo, hi = float(np.interp(0.1, probs, vals)), float(np.interp(0.9, probs, vals))
                if attr == "shot_speed_ms":
                    lo, hi = lo / SHOT_MEASURED_TO_LAUNCH, hi / SHOT_MEASURED_TO_LAUNCH
                if 5 < lo < hi < 40:
                    setattr(cal.ball, attr, (lo, min(hi, cal.ball.max_unscripted_kick_ms)))
                    got_ball = True
    if bj:
        at = _percentile_pairs(bj.get("air_time_high_pass_s"))
        if at is not None:
            hi = float(np.interp(0.9, *at))
            if 1.5 < hi < 4.5:
                cal.ball.high_pass_air_time_s = (float(np.interp(0.25, *at)), hi)
                got_ball = True
    mark(got_ball, "ball speeds and high-pass air time")

    xg = read("xg_model.json")
    got_xg = False
    if xg:
        if isinstance(xg.get("coef"), list) and isinstance(xg.get("features"), list) and "intercept" in xg:
            names = {"distance_m": "xg_b_dist", "visible_angle_rad": "xg_b_angle", "opponents_in_triangle": "xg_b_opp",
                     "header": "xg_b_head"}
            cal.events.xg_b0 = float(xg["intercept"])
            cal.events.xg_b_head = 0.0
            for f, c in zip(xg["features"], xg["coef"]):
                if f in names:
                    setattr(cal.events, names[f], float(c))
            got_xg = True
        coef = xg.get("coefficients", xg)
        try:
            cal.events.xg_b0 = float(coef["intercept"])
            cal.events.xg_b_dist = float(coef["distance"])
            cal.events.xg_b_angle = float(coef["angle"])
            cal.events.xg_b_head = float(coef.get("header", cal.events.xg_b_head))
            got_xg = True
        except (KeyError, TypeError, ValueError):
            pass
    mark(got_xg, "xG model")
    return cal
