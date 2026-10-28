"""Physical and ball metrics from tracking frames, computed with the data pipeline (guidelines v3, section 10).

Input: the frames of a `Snapshot` (never the whole match). Each period is prepared with
`insidethegame_data.prepare.prepare_half` (validity mask, gaps, Savitzky-Golay kinematics, ball kinematics), exactly
as the offline pipeline does, but on the frames released so far; tracking-area edges are discovered on the released
frames of the period and substitutions come from the released events. Distances and top speeds then come from
`insidethegame_data.metrics.player_summary`. Valid samples only; the "≈" precision rule of section 10.2.

Engine choices on top of the pipeline (documented in app/engine/README.md; metrics of Materials/METRICS.md, E-4):
- goalkeepers are out of every team total (distance and splits, speed zones, sprints, intensity), shown as players;
- speed zones: FIFA zones 0-7, 7-15, 15-20, 20-25, 25+ km/h over valid samples (estimated gap paths are counted in
  the total only);
- sprints: `R-SPRINT` of the pipeline (`metrics.sprint_runs`): at least 25 km/h for at least 1 s on valid, measured
  samples; two sprints less than 1 s apart count as one; an invalid or estimated sample is never bridged;
- peak intensity: rolling five-minute window with a 1 s step per player (`metrics.rolling_intensity`); a team is the
  mean of its outfield players on the pitch for the whole window;
- ball circulation: median ball speed (km/h) while the ball is live and in the team's possession (valid ball samples),
  without the 2 s after each shot and clearance, with the share of valid ball samples for the reliability symbol;
- pass length: successful passes only, from the kick located by the pipeline's touch functions to the team-mate's
  reception (`metrics.pass_reception`), once the frames of the reception are released;
- shots (task E-4): each shot is located once on the tracking, and that one frame gives the shot map position, the
  xG features and the shot speed, so the three agree: the shooter's best-scoring kick candidate of the pipeline
  (`metrics.shot_kick`, as the calibration locates shots), else the last frame near the event with the shooter within
  1.5 m of the ball (`kick_index`), else the annotated position at the frame of the event time. Shot speed: peak valid
  ball speed in the 0.5 s after the kick; the statistics leave headers out. xG (Materials/METRICS.md section 9):
  `metrics.shot_xg` of the pipeline (the calibration's own `shot_features` and `xg`, the committed
  `calibration/xg_model.json`) with the ball at the located frame in the shooting team's attacking coordinates and the
  opposing outfield players with valid positions there; a penalty is a fixed 0.79;
- possession and effective playing time (per period too): from the provider's ball possession and ball status.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.contract.models import Reliability

from .pipeline import (CODE, DEFAULT_CONFIG, HalfData, HalfResult, SyncPeriod, ball_kinematics, discover_edges,
                       map_position, metrics, player_summary, prepare_half, touch_table)
from .sources.base import AWAY, HOME, PeriodFrames
from .sources.fake import attacks_plus_x
from .window import Snapshot

CFG = DEFAULT_CONFIG
ZONES_KMH = (0.0, 7.0, 15.0, 20.0, 25.0, np.inf)
SPRINT_KMH = CFG.sprint_min_kmh
SPRINT_MIN_S = CFG.sprint_min_s
SHOT_SPEED_WINDOW_S = 0.5
BUCKET_S = 300.0  # five-minute buckets of the period clock (bucket ends 5', 10', ...)
BUCKETS_PER_PERIOD = 9  # the last bucket of a period (40'-45', 85'-90') also holds its added time
PASS_SEARCH_S = CFG.event_search_min_s  # search window of a pass or shot kick (guidelines 9.3 step 1: at least 5 s)
# Shot speed: the event time is an annotation (a few tenths of a second off, up to about 1.6 s on SYN-001 after the
# clock offset), so the kick is located on the tracking: the last frame in [t - 2.5 s, t + 1 s] where the shooter is
# within 1.5 m of the ball. t + 1 s is always released before the event itself (event buffer 5 s): no look-ahead.
SHOT_SEARCH_BEFORE_S = 2.5
SHOT_SEARCH_AFTER_S = 1.0
SHOT_CONTACT_M = 1.5
SIDE_CODE = {HOME: 1.0, AWAY: 2.0}


@dataclass
class Prepared:
    pf: PeriodFrames
    hd: HalfData
    hr: HalfResult
    key: tuple = ()  # the cache key of the preparation (also keys its touch table)


class PrepCache:
    """Small LRU of prepared periods, keyed by (period, frames released, substitutions released).

    A period's preparation depends only on its released frames and substitutions, so the key identifies it; the
    first half is prepared once and reused for the whole second half.
    """

    def __init__(self, size: int = 8) -> None:
        self.size = size
        self._d: OrderedDict = OrderedDict()

    def get(self, key, build):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        val = self._d[key] = build()
        while len(self._d) > self.size:
            self._d.popitem(last=False)
        return val


def prepare(snap: Snapshot, cache: PrepCache | None = None) -> list[Prepared]:
    L, W = snap.pitch
    periods = [pf for _, pf in sorted(snap.frames.items()) if len(pf) >= CFG.savgol_window(pf.fs) + 2]
    if not periods:
        return []
    out = []
    for pf in periods:
        # Tracking-area edges are discovered on the released frames of this period (the offline pipeline uses the
        # whole match): a finished period is then never prepared again.
        edges = discover_edges([pf.x], [pf.y], L / 2, W / 2, CFG.edge_min_samples, CFG.edge_value_decimals)["edges"]
        subs = [{"player_out": e.player_id, "player_in": e.secondary_player_id, "team": e.team_side, "half": pf.period,
                 "event_time_s": e.t} for e in snap.events if e.type == "Substitution" and e.period == pf.period]
        nan2 = np.full(pf.x.shape, np.nan)
        hd = HalfData(half=pf.period, fs=pf.fs, n=pf.frame, t_s=pf.t, pids=list(pf.pids), team_side=list(pf.team_side),
                      x=pf.x, y=pf.y, prov_s=nan2, prov_d=nan2, prov_a=nan2, bx=pf.bx, by=pf.by, bz=pf.bz,
                      bstatus=pf.bstatus, bposs=pf.bposs, ball_prov_s=np.full(len(pf), np.nan))
        key = (pf.period, len(pf), tuple(sorted(str(s) for s in subs)), tuple(sorted(str(e) for e in edges)))

        def build(hd=hd, subs=subs, edges=edges):
            return prepare_half(hd, subs, edges, (L, W), CFG)

        hr = cache.get(key, build) if cache is not None else build()
        out.append(Prepared(pf, hd, hr, key))
    return out


def distance_payload(dist_m: float, unmeasured: float) -> dict:
    """Contract `Distance` with the precision rule of guidelines 10.2 and the reliability symbol (contract v0.3.0)."""
    km = dist_m / 1000.0
    if unmeasured < CFG.precision_exact_below:
        v, approx = round(km, 2), False
    elif unmeasured < CFG.precision_tenth_below:
        v, approx = round(km, 1), True
    else:
        v, approx = round(km * 2) / 2, True
    return {"km": float(v), "approx": approx, "unmeasured_pct": round(100.0 * unmeasured, 1),
            "reliability": Reliability.of(100.0 * (1.0 - unmeasured)).model_dump(mode="json")}


def period_clock(snap: Snapshot, pf: PeriodFrames) -> np.ndarray:
    """Seconds since the period's kick-off at each frame (the tracking clock): 0 before it, the period length after
    its final whistle. Non-decreasing."""
    clock = np.maximum(pf.t - snap.state.kickoff[pf.period], 0.0)
    length = snap.period_length_s(pf.period)
    return np.minimum(clock, length) if length is not None else clock


def bucket_of(clock: np.ndarray) -> np.ndarray:
    """Five-minute bucket of each frame (0..8); the last bucket of a period also holds its added time."""
    return np.minimum((clock // BUCKET_S).astype(int), BUCKETS_PER_PERIOD - 1)


def bucket_label(period: int, b: int) -> int:
    """Bucket end in match minutes, as in the contract's 5-minute series: 5, 10, ... 45, then 50 ... 90."""
    return 45 * (period - 1) + 5 * (b + 1)


def minute_label(period: int, k: int) -> int:
    """Match minute of the end of the k-th minute of a period: 1 ... 45, then 46 ... 90. Added time keeps the last
    label (45 or 90): a series keeps only the latest value of a label."""
    return 45 * (period - 1) + min(k, 45)


def compute(snap: Snapshot, cache: PrepCache | None = None) -> dict | None:
    """All frame-based metrics of the snapshot, or None when no period has enough released frames."""
    preps = prepare(snap, cache)
    if not preps:
        return None
    gks = gk_ids(snap)
    lineup = pd.DataFrame([{"person_id": p.player_id, "role": p.position or "UNMAPPED_NONE",
                            "role_group": map_position(p.position)["role_group"] if p.position else "UNMAPPED_NONE"}
                           for p in snap.lineup])
    summary = player_summary([(p.hd, p.hr) for p in preps], lineup, CFG)
    by_period = {p.pf.period: p for p in preps}

    players, teams = {}, {}
    for r in summary.itertuples(index=False):
        top = None
        if np.isfinite(r.top_speed_ms):
            pp = by_period[int(r.top_speed_period)]
            t = float(pp.pf.t[int(r.top_speed_index)])
            minute, _ = snap.minute(pp.pf.period, snap.clock_s(pp.pf.period, t))
            top = {"kmh": round(float(r.top_speed_ms) * 3.6, 1), "minute": minute}
        # Sprints: R-SPRINT of the pipeline (>= 25 km/h for >= 1 s, merged when less than 1 s apart, never bridged)
        players[r.object_id] = {"side": r.team_side, "gk": r.object_id in gks, "dist_m": float(r.dist_m),
                                "unmeasured": float(r.not_measured_share), "samples": int(r.samples),
                                "valid": int(r.valid), "top": top, "sprints": int(r.sprints), "zones_m": [0.0] * 5,
                                "splits_m": (float(r.dist_live_m), float(r.dist_dead_m), float(r.dist_in_poss_m),
                                             float(r.dist_out_poss_m))}
    for side in (HOME, AWAY):
        ps = [v for v in players.values() if v["side"] == side and not v["gk"]]  # goalkeepers out of team totals
        samples = sum(v["samples"] for v in ps)
        teams[side] = {"dist_m": sum(v["dist_m"] for v in ps),
                       "unmeasured": 1.0 - sum(v["valid"] for v in ps) / samples if samples else 1.0,
                       "splits_m": tuple(sum(v["splits_m"][i] for v in ps) for i in range(4)),
                       "zones_m": [0.0] * 5, "sprints": sum(v["sprints"] for v in ps)}

    touch = {p.pf.period: touches(snap, p, cache) for p in preps}  # the pipeline's touch table of each period
    shots = _shots(snap, by_period, gks, touch)
    kicks = _kick_times(snap, by_period, shots)

    live_n = {HOME: 0, AWAY: 0}
    ball_valid_n = {HOME: 0, AWAY: 0}
    ball_speed = {HOME: [], AWAY: []}
    live_s = 0.0
    live_by_period: dict[int, float] = {}
    circ_bins: dict[tuple[int, int], dict[str, np.ndarray]] = {}
    for p in preps:
        pf, hr = p.pf, p.hr
        kmh = hr.speed * 3.6
        for c, side in enumerate(pf.team_side):
            v = hr.valid[:, c]
            if not v.any() or pf.pids[c] not in players:
                continue
            zone = np.digitize(kmh[v, c], ZONES_KMH[1:-1])
            d = np.nan_to_num(hr.dist[v, c])
            for z in range(5):
                km = float(d[zone == z].sum())
                players[pf.pids[c]]["zones_m"][z] += km
                if pf.pids[c] not in gks:
                    teams[side]["zones_m"][z] += km
        clock = period_clock(snap, pf)
        done = snap.clock_s(pf.period, snap.cursor_t)  # clock reached in this period
        live = pf.bstatus == 1
        live_s += float(live.sum()) / pf.fs
        live_by_period[pf.period] = live_by_period.get(pf.period, 0.0) + float(live.sum()) / pf.fs
        # Ball circulation (METRICS.md decision 6): valid live samples, without the 2 s after each shot and clearance
        circ = live & hr.ball_valid & np.isfinite(hr.ball_speed) & ~metrics.after_kicks_mask(
            pf.t, kicks.get(pf.period, []), CFG.circulation_exclude_after_s)
        for side in (HOME, AWAY):
            own = live & (pf.bposs == SIDE_CODE[side])
            live_n[side] += int(own.sum())
            ball_valid_n[side] += int((own & hr.ball_valid).sum())
            ball_speed[side].append(hr.ball_speed[own & circ])
        bins5 = bucket_of(clock)
        for b in np.unique(bins5):
            if (b + 1) * BUCKET_S > done:
                continue  # complete five-minute buckets only
            sel = bins5 == b
            circ_bins[(pf.period, int(b))] = {s: hr.ball_speed[sel & circ & (pf.bposs == SIDE_CODE[s])] for s in (HOME, AWAY)}

    possession = None
    if live_n[HOME] + live_n[AWAY] > 0:
        h = round(100.0 * live_n[HOME] / (live_n[HOME] + live_n[AWAY]), 1)
        possession = {"home_pct": h, "away_pct": round(100.0 - h, 1)}

    circulation = None
    if any(len(np.concatenate(v)) for v in ball_speed.values()):
        vals = {s: (round(float(np.median(np.concatenate(v))) * 3.6, 1) if len(np.concatenate(v)) else 0.0)
                for s, v in ball_speed.items()}
        series = [{"side": s, "points": [{"minute": bucket_label(*k), "value": round(float(np.median(v[s])) * 3.6, 1)}
                                         for k, v in sorted(circ_bins.items()) if len(v[s])]} for s in (HOME, AWAY)]
        measured = {s: 100.0 * ball_valid_n[s] / live_n[s] if live_n[s] else 0.0 for s in (HOME, AWAY)}
        circulation = {"values": vals, "series": series, "measured_pct": measured,
                       "excluded_kicks": sum(len(v) for v in kicks.values())}

    shot_speeds = {k: v for k, v in shots.items() if v["kmh"] is not None}  # every shot with valid ball samples

    samples = sum(v["samples"] for v in players.values())
    excluded = 100.0 * (1.0 - sum(v["valid"] for v in players.values()) / samples) if samples else 0.0
    recoveries = _recoveries(snap, by_period)
    passes = _pass_lengths(snap, preps, touch)
    return {"players": players, "teams": teams, "possession": possession, "circulation": circulation,
            "peaks": _peaks(snap, preps, gks), "passes": passes, "shots": shots,
            "momentum": _momentum(snap, preps, shots), "load": _acceleration_load(snap, preps, gks),
            "recoveries": recoveries, "counter_attacks": _counter_attacks(by_period, recoveries, shots),
            "zone_maps": _zone_maps(snap, preps, gks), "shapes": _shapes(snap, preps, gks),
            "ppda": _ppda(snap, by_period, passes["kicks"]),
            "shot_speeds": shot_speeds, "effective_min": round(live_s / 60.0, 1),
            "effective_min_by_period": {p: round(v / 60.0, 1) for p, v in live_by_period.items()},
            "excluded_pct": round(excluded, 1)}


def released_end(snap: Snapshot, pf: PeriodFrames, clock: np.ndarray) -> float:
    """Period clock reached by the released frames (the last frame stands for one sample interval)."""
    return min(snap.clock_s(pf.period, snap.cursor_t), float(clock[-1]) + 1.0 / pf.fs)


def _momentum(snap: Snapshot, preps: list[Prepared], shots: dict) -> dict | None:
    """Momentum (METRICS.md section 13, decision 15) over the last five minutes of the current period: territory (each
    team's possession time with the ball in the opponent's final third: attacking x > L/6, live play, valid ball
    samples, provider possession) and danger (xG share of the shots located in the window), `metrics.momentum_value`.
    At the start of a period the window starts at its kick-off (shorter than five minutes); it never crosses half-time.
    One point per played minute (the window ending at that minute) and `current`, the window ending now."""
    L, _ = snap.pitch
    w = CFG.momentum_window_s
    series: dict[int, float] = {}
    current = None
    for p in preps:
        pf, hr = p.pf, p.hr
        clock = period_clock(snap, pf)
        done = released_end(snap, pf, clock)
        ok = (pf.bstatus == 1) & hr.ball_valid & np.isfinite(pf.bx)
        cum = {s: np.concatenate([[0.0], np.cumsum(ok & (pf.bposs == SIDE_CODE[s]) & (attack_sign(s, pf.period) * pf.bx
                                                                                   > L / 6)) / pf.fs]) for s in (HOME, AWAY)}
        ko = snap.state.kickoff[pf.period]
        xgs = [(v["t_kick"] - ko, v["event"].team_side, v["xg"]) for v in shots.values()
               if v["period"] == pf.period and v["xg"] is not None]

        def value(end: float) -> float:
            start = max(0.0, end - w)
            lo, hi = (int(np.searchsorted(clock, c, side="right")) for c in (start, end))
            x = {s: sum(g for c, side, g in xgs if side == s and start < c <= end) for s in (HOME, AWAY)}
            return round(metrics.momentum_value(cum[HOME][hi] - cum[HOME][lo], cum[AWAY][hi] - cum[AWAY][lo],
                                                x[HOME], x[AWAY], CFG), 2)

        for k in range(1, int(done // 60) + 1):
            series[minute_label(pf.period, k)] = value(60.0 * k)
        current = value(done)
    if current is None:
        return None
    return {"current": current, "series": [{"minute": m, "value": v} for m, v in sorted(series.items())]}


def _acceleration_load(snap: Snapshot, preps: list[Prepared], gks: set[str]) -> dict:
    """Acceleration load (METRICS.md section 22, decision 24): Σ |dv/dt| dT over the valid samples whose acceleration
    is measured or approx (`metrics.acceleration_load_samples`), in km/h. Players: the total and, per complete
    five-minute bucket, km/h per minute on the pitch; teams (outfield players): the total and km/h per minute of each
    bucket (the last bucket of a period holds its added time)."""
    players: dict[str, dict] = {}
    teams = {s: {"kmh": 0.0, "series": {}} for s in (HOME, AWAY)}
    for p in preps:
        pf, hr = p.pf, p.hr
        clock = period_clock(snap, pf)
        end = released_end(snap, pf, clock)
        b = bucket_of(clock)
        done = [k for k in range(BUCKETS_PER_PERIOD) if (k + 1) * BUCKET_S <= end and (b == k).any()]
        st = hr.acc_status
        load = 3.6 * np.column_stack([metrics.acceleration_load_samples(hr.acc_tan[:, c], None if st is None else st[:, c],
                                                                        hr.valid[:, c], pf.fs) for c in range(len(pf.pids))])
        for c, pid in enumerate(pf.pids):
            if not hr.present[:, c].any():
                continue
            pl = players.setdefault(pid, {"kmh": 0.0, "series": {}})
            pl["kmh"] += float(load[:, c].sum())
            for k in done:
                on_min = float(hr.present[b == k, c].sum()) / pf.fs / 60.0
                if on_min > 0:
                    pl["series"][bucket_label(pf.period, k)] = float(load[b == k, c].sum()) / on_min
        for side in (HOME, AWAY):
            cols = [c for c, s in enumerate(pf.team_side) if s == side and pf.pids[c] not in gks]
            teams[side]["kmh"] += float(load[:, cols].sum())
            for k in done:
                sel = b == k
                teams[side]["series"][bucket_label(pf.period, k)] = float(load[sel][:, cols].sum()) / (sel.sum() / pf.fs / 60.0)
    return {"players": players, "teams": teams}


def _recoveries(snap: Snapshot, by_period: dict) -> list[dict]:
    """Ball recoveries (METRICS.md section 21, decision 23): the team's `BallClaiming` events (balls claimed or
    intercepted) and duels won by the defending player (`winner_role` without_ball; a duel without the role is a
    candidate too), each confirmed by the provider possession column changing from the opponent to the team in live
    play within 3 s of the event (`metrics.possession_gain_frame`). That change is the recovery's located frame: the
    ball there gives its position for the counter-attacks (own half: attacking x < 0). One change confirms one
    recovery: the first event in time order (a tackle and the team-mate's claim of the loose ball count once)."""
    out = []
    used: set[tuple[int, int]] = set()
    for e in snap.events:
        if e.period not in by_period or e.team_side not in (HOME, AWAY):
            continue
        if not (e.type == "BallClaiming" or (e.type == "TacklingGame" and e.attrs.get("winner_role") != "with_ball")):
            continue
        pf = by_period[e.period].pf
        i_event = min(int(np.searchsorted(pf.t, e.t, side="left")), len(pf.t) - 1)
        other = AWAY if e.team_side == HOME else HOME
        i = metrics.possession_gain_frame(pf.bposs, pf.bstatus == 1, i_event, SIDE_CODE[e.team_side], SIDE_CODE[other],
                                          CFG.frames(CFG.recovery_check_s, pf.fs))
        if i is None or (e.period, i) in used:
            continue
        used.add((e.period, i))
        x = attack_sign(e.team_side, e.period) * float(pf.bx[i])
        out.append({"event": e, "period": e.period, "index": i, "side": e.team_side,
                    "own_half": bool(np.isfinite(x) and x < 0.0)})
    return out


def _counter_attacks(by_period: dict, recoveries: list[dict], shots: dict) -> list[dict]:
    """Counter-attacks (METRICS.md section 25, decision 26): a recovery in the team's own half in live play, then a shot
    of the same team within 15 s, the ball live and never the opponent's in between (`metrics.counter_attacks`)."""
    out = []
    for period, pp in sorted(by_period.items()):
        pf = pp.pf
        rec = [r for r in recoveries if r["period"] == period]
        sh = sorted((v for v in shots.values() if v["period"] == period), key=lambda v: (v["index"], v["event"].event_id))
        pairs = metrics.counter_attacks([(r["index"], SIDE_CODE[r["side"]], r["own_half"]) for r in rec],
                                        [(v["index"], SIDE_CODE[v["event"].team_side]) for v in sh], pf.bposs,
                                        pf.bstatus == 1, pf.fs, CFG)
        out += [{"side": rec[a]["side"], "minute": sh[b]["minute"], "recovery_event_id": rec[a]["event"].event_id,
                 "shot_event_id": sh[b]["event"].event_id} for a, b in pairs]
    return out


def _kick_times(snap: Snapshot, by_period: dict, shots: dict) -> dict[int, list[float]]:
    """Kick times per period of the released shots (their located kick) and clearances (located the same way,
    `kick_index`): the ball circulation leaves out the 2 s after each (METRICS.md decision 6)."""
    out: dict[int, list[float]] = {}
    for v in shots.values():
        out.setdefault(v["period"], []).append(v["t_kick"])
    for e in snap.events:
        if e.type == "OtherBallAction" and e.attrs.get("clearance") and e.period in by_period:
            i, located = kick_index(by_period[e.period].pf, e)
            out.setdefault(e.period, []).append(float(by_period[e.period].pf.t[i]) if located else e.t)
    return {p: sorted(v) for p, v in out.items()}


def _peaks(snap: Snapshot, preps: list[Prepared], gks: set[str]) -> dict:
    """Peak intensity (METRICS.md section 4, decision 4): each player's metres per minute over a rolling five-minute
    window with a 1 s step (`metrics.rolling_intensity`, tracking clock, never across half-time); a team is the mean of
    its outfield players on the pitch for the whole window. Returns each team's and each player's peak window and,
    per team, one point per minute (the window ending at that minute) for the mirror chart."""
    w = CFG.peak_window_s
    best_team: dict[str, tuple] = {}
    best_player: dict[str, tuple] = {}
    series: dict[str, dict[int, float]] = {HOME: {}, AWAY: {}}
    for p in preps:
        pf, hr = p.pf, p.hr
        clock = period_clock(snap, pf)
        ends, mpm, full = metrics.rolling_intensity(hr.dist, hr.present, clock, released_end(snap, pf, clock), CFG)
        if not len(ends):
            continue
        for side in (HOME, AWAY):
            team = metrics.team_intensity(mpm, full, [c for c, s in enumerate(pf.team_side)
                                                      if s == side and pf.pids[c] not in gks])
            if np.isfinite(team).any():
                k = int(np.nanargmax(team))
                if side not in best_team or team[k] > best_team[side][0]:
                    best_team[side] = (float(team[k]), pf.period, float(ends[k]))
            for j in np.flatnonzero((ends % 60.0 == 0.0) & np.isfinite(team)):
                series[side][minute_label(pf.period, int(ends[j] // 60))] = round(float(team[j]), 1)
        for c, pid in enumerate(pf.pids):
            if full[:, c].any():
                k = int(np.argmax(np.where(full[:, c], mpm[:, c], -np.inf)))
                if pid not in best_player or mpm[k, c] > best_player[pid][0]:
                    best_player[pid] = (float(mpm[k, c]), pf.period, float(ends[k]))

    def window(b: tuple) -> dict:
        start = 45 * (b[1] - 1) + int((b[2] - w) // 60)
        return {"start_minute": start, "end_minute": start + int(round(w / 60)), "metres_per_minute": round(b[0], 1)}

    return {"teams": {s: window(b) for s, b in best_team.items()},
            "players": {pid: window(b) for pid, b in best_player.items()},
            "series": {s: [{"minute": m, "value": v} for m, v in sorted(series[s].items())] for s in (HOME, AWAY)}}


def sync_period(snap: Snapshot, p: Prepared) -> SyncPeriod:
    """The pipeline's event-location view of a prepared period (as `insidethegame_data.qc` builds it): positions that
    are not real (discarded gaps, off the pitch) are NaN, the ball speed is the valid one, and the period is complete
    only once its final whistle is released (R-LIVE-BUFFER)."""
    hd, hr = p.hd, p.hr
    px, py = hr.x_est.copy(), hr.y_est.copy()
    px[hr.discarded | ~hr.present], py[hr.discarded | ~hr.present] = np.nan, np.nan
    group = {q.player_id: map_position(q.position)["role_group"] if q.position else None for q in snap.lineup}
    return SyncPeriod(half=hd.half, fs=hd.fs, n=hd.n, t_s=hd.t_s, pids=list(hd.pids), team_side=list(hd.team_side),
                      px=px, py=py, bx=hd.bx, by=hd.by, bz=hd.bz, bstatus=hd.bstatus,
                      home_attack_sign=attack_sign(HOME, hd.half),
                      ball_speed=np.where(hr.ball_valid, hr.ball_speed, np.nan), ball_valid=hr.ball_valid,
                      ball_pos_ok=~np.isin(hr.ball_reason, [CODE["jump"], CODE["limit"]]),
                      role_group=[group.get(pid) for pid in hd.pids], complete=hd.half in snap.state.final_whistle)


def touches(snap: Snapshot, p: Prepared, cache: PrepCache | None = None):
    """(SyncPeriod, touch table) of a prepared period, cached with the preparation's key."""
    def build():
        sp = sync_period(snap, p)
        return sp, touch_table(sp, ball_kinematics(sp, CFG, snap.pitch[0]), CFG)

    key = ("touches", p.key, p.hd.half in snap.state.final_whistle)
    return cache.get(key, build) if cache is not None and p.key else build()


def _pass_lengths(snap: Snapshot, preps: list[Prepared], touch: dict) -> dict:
    """Passes located on the tracking (`metrics.pass_reception`: the pipeline's kick candidates and next touch). The
    length (METRICS.md section 11, decision 13) is measured for the annotated complete `Play` events whose next touch
    is a team-mate's; the located kick of every pass also places it for PPDA. A pass is processed only once the frames
    of its search window and of the reception search (`next_touch_max_s`) are released, or its period is over.
    Returns the lengths and the kicks by event, and the counts (of the complete passes) for the Inspector."""
    out: dict[str, dict] = {}
    kicks: dict[str, dict] = {}
    counts = {"complete": 0, "measured": 0, "waiting": 0, "not_located": 0, "not_received": 0}
    for p in preps:
        pf = p.pf
        evs = [e for e in snap.events if e.type == "Play" and e.period == pf.period and e.team_side in (HOME, AWAY)]
        counts["complete"] += sum(e.attrs.get("outcome") == "complete" for e in evs)
        if not evs:
            continue
        sp, tt = touch[pf.period]
        t_last = float(pf.t[-1])
        for e in evs:
            complete = e.attrs.get("outcome") == "complete"
            if not sp.complete and e.t + PASS_SEARCH_S + CFG.next_touch_max_s > t_last:
                counts["waiting"] += complete
                continue
            r = metrics.pass_reception(sp, tt, pf.pids.index(e.player_id), e.t, PASS_SEARCH_S, CFG) \
                if e.player_id in pf.pids else None
            if r is not None:
                kicks[e.event_id] = {"period": pf.period, "index": r["kick"], "side": e.team_side}
            if not complete:
                continue
            if r is None:
                counts["not_located"] += 1
            elif r["length_m"] is None:
                counts["not_received"] += 1
            else:
                counts["measured"] += 1
                out[e.event_id] = {"side": e.team_side, "player_id": e.player_id, "length_m": r["length_m"]}
    return {"lengths": out, "kicks": kicks, "counts": counts}


def _zone_maps(snap: Snapshot, preps: list[Prepared], gks: set[str]) -> dict:
    """Zone maps (METRICS.md section 15, decision 19): valid samples in live play, in the attacking coordinates of the
    player's team (`metrics.zone_map`); a team is the sum of its outfield players; also every player (the favourites
    are picked by the payload)."""
    L, W = snap.pitch
    xs: dict[str, list] = {}
    ys: dict[str, list] = {}
    side_of: dict[str, str] = {}
    for p in preps:
        pf, hr = p.pf, p.hr
        live = pf.bstatus == 1
        for c, pid in enumerate(pf.pids):
            m = hr.valid[:, c] & live & np.isfinite(hr.x[:, c]) & np.isfinite(hr.y[:, c])
            if m.any():
                s = attack_sign(pf.team_side[c], pf.period)
                xs.setdefault(pid, []).append(s * hr.x[m, c])
                ys.setdefault(pid, []).append(s * hr.y[m, c])
                side_of[pid] = pf.team_side[c]
    players = {pid: metrics.zone_map(np.concatenate(xs[pid]), np.concatenate(ys[pid]), L, W, CFG) for pid in xs}
    teams = {}
    for side in (HOME, AWAY):
        out = [pid for pid in xs if side_of[pid] == side and pid not in gks]
        if out:
            teams[side] = metrics.zone_map(np.concatenate([v for pid in out for v in xs[pid]]),
                                           np.concatenate([v for pid in out for v in ys[pid]]), L, W, CFG)
    return {"teams": {s: v for s, v in teams.items() if v is not None},
            "players": {pid: v for pid, v in players.items() if v is not None}}


def _shapes(snap: Snapshot, preps: list[Prepared], gks: set[str]) -> dict:
    """Defensive line and team length (METRICS.md section 17, decision 20): per live frame where every outfield player
    on the pitch has a valid position (`metrics.team_shape`), averaged per complete five-minute bucket."""
    L, _ = snap.pitch
    out = {s: {"line": {}, "length": {}} for s in (HOME, AWAY)}
    for p in preps:
        pf, hr = p.pf, p.hr
        clock = period_clock(snap, pf)
        end = released_end(snap, pf, clock)
        b = bucket_of(clock)
        live = pf.bstatus == 1
        for side in (HOME, AWAY):
            cols = [c for c, s in enumerate(pf.team_side) if s == side and pf.pids[c] not in gks]
            if not cols:
                continue
            x = np.where(hr.valid[:, cols], attack_sign(side, pf.period) * hr.x[:, cols], np.nan)
            line, length = metrics.team_shape(x, hr.present[:, cols], L)
            for k in range(BUCKETS_PER_PERIOD):
                sel = (b == k) & live & np.isfinite(line)
                if (k + 1) * BUCKET_S <= end and sel.any():
                    out[side]["line"][bucket_label(pf.period, k)] = float(line[sel].mean())
                    out[side]["length"][bucket_label(pf.period, k)] = float(length[sel].mean())
    return out


def _ppda(snap: Snapshot, by_period: dict, kicks: dict) -> dict:
    """PPDA (METRICS.md section 13, decision 17): the opponent's passes (`Play`, at the ball of their located kick) in
    the team's pressing zone (its attacking 60 %) per defensive action of the team there: duels in which its player
    was the defending player (won: `winner_role` without_ball, the winner's team; lost: with_ball, the loser's team;
    no role: the winner's team), fouls committed and balls claimed, at the ball of their located frame (`kick_index`)
    or at the annotated position when not located."""
    L, _ = snap.pitch
    passes = {HOME: 0, AWAY: 0}
    actions = {HOME: 0, AWAY: 0}
    for k in kicks.values():
        d = AWAY if k["side"] == HOME else HOME
        x = attack_sign(d, k["period"]) * float(by_period[k["period"]].pf.bx[k["index"]])
        passes[d] += bool(np.isfinite(x) and metrics.in_pressing_zone(x, L, CFG))
    for e in snap.events:
        if e.period not in by_period or e.team_side not in (HOME, AWAY):
            continue
        if e.type in ("Foul", "BallClaiming"):
            d = e.team_side
        elif e.type == "TacklingGame":
            d = e.team_side if e.attrs.get("winner_role") != "with_ball" else (AWAY if e.team_side == HOME else HOME)
        else:
            continue
        pf = by_period[e.period].pf
        i, located = kick_index(pf, e)
        x = float(pf.bx[i]) if located else (float(e.x) if e.x is not None else np.nan)
        x = attack_sign(d, e.period) * x
        actions[d] += bool(np.isfinite(x) and metrics.in_pressing_zone(x, L, CFG))
    return {s: {"passes": passes[s], "actions": actions[s], "value": metrics.ppda(passes[s], actions[s])}
            for s in (HOME, AWAY)}


def gk_ids(snap: Snapshot) -> set[str]:
    """Goalkeepers of the line-up (role group GK, guidelines 16.1): out of every team total (METRICS.md decision 1)."""
    return {p.player_id for p in snap.lineup if p.position and map_position(p.position)["role_group"] == "GK"}


def attack_sign(side: str, period: int) -> float:
    """+1 when `side` attacks +x in `period` (raw orientation), else -1: x_att = sign * x."""
    return 1.0 if attacks_plus_x(side, period) else -1.0


def _shots(snap: Snapshot, by_period: dict, gks: set[str], touch: dict) -> dict:
    """Every released shot, located once on the tracking: one frame for the shot map, the xG features and the shot
    speed (Materials/METRICS.md sections 5, 9 and 14). The kick is the shooter's best-scoring kick candidate of the
    pipeline in the search window (`metrics.shot_kick`, method `kick_candidate`); else the last frame near the event
    with the shooter within 1.5 m of the ball (`kick_index`, method `contact`); else the annotated position at the
    frame of the event time (method `annotated`). Positions in the shooting team's attacking coordinates (the
    contract's attack-normalised x, y). `touch`: period -> (SyncPeriod, touch table)."""
    L, _ = snap.pitch
    out = {}
    for e in snap.events:
        if e.type != "ShotAtGoal" or e.period not in by_period or e.team_side not in (HOME, AWAY):
            continue
        pf, hr = by_period[e.period].pf, by_period[e.period].hr
        syncp, tt = touch[e.period]
        i = metrics.shot_kick(syncp, tt, pf.pids.index(e.player_id), e.t, PASS_SEARCH_S, CFG) \
            if e.player_id in pf.pids else None
        if i is not None:
            located, method = True, "kick_candidate"
        else:
            i, located = kick_index(pf, e)
            method = "contact" if located else "annotated"
        t_kick = float(pf.t[i]) if located else e.t
        s = attack_sign(e.team_side, e.period)
        if located:
            x, y = s * float(pf.bx[i]), s * float(pf.by[i])
        elif e.x is not None and e.y is not None:  # fallback: the annotated position
            x, y = s * float(e.x), s * float(e.y)
        else:
            x = y = None
        opp = [c for c, side in enumerate(pf.team_side) if side != e.team_side and pf.pids[c] not in gks
               and hr.valid[i, c] and np.isfinite(hr.x[i, c]) and np.isfinite(hr.y[i, c])]
        feats = None
        if e.attrs.get("penalty"):
            xg = metrics.penalty_xg()
        elif x is not None:
            feats = metrics.shot_xg(x, y, s * hr.x[i, opp], s * hr.y[i, opp], L, CFG.goal_width_m)
            xg = feats.pop("xg")
        else:
            xg = None
        sel = (pf.t >= t_kick) & (pf.t <= t_kick + SHOT_SPEED_WINDOW_S) & hr.ball_valid
        sp = hr.ball_speed[sel]
        sp = sp[np.isfinite(sp)]
        kmh = round(float(sp.max()) * 3.6, 1) if len(sp) else None
        minute, _ = snap.minute(e.period, snap.clock_s(e.period, e.t))
        out[e.event_id] = {"event": e, "period": e.period, "index": i, "t_kick": t_kick, "located": located,
                           "method": method, "x": x, "y": y, "xg": xg, "features": feats, "minute": minute,
                           "kmh": kmh if kmh is not None and 0 < kmh <= CFG.ball_speed_max_kmh else None}
    return out


def kick_index(pf: PeriodFrames, e) -> tuple[int, bool]:
    """(frame index, located) of the kick of event `e` (a shot; also used for other on-ball events): the last frame in
    [t - 2.5 s, t + 1 s] where the player is within SHOT_CONTACT_M of the ball. When the player is unknown, not tracked
    or never that close, the frame of the event time itself, with located = False."""
    i_event = min(int(np.searchsorted(pf.t, e.t, side="left")), len(pf.t) - 1)
    if e.player_id not in pf.pids:
        return i_event, False
    idx = np.flatnonzero((pf.t >= e.t - SHOT_SEARCH_BEFORE_S) & (pf.t <= e.t + SHOT_SEARCH_AFTER_S))
    col = pf.pids.index(e.player_id)
    with np.errstate(invalid="ignore"):
        d = np.hypot(pf.x[idx, col] - pf.bx[idx], pf.y[idx, col] - pf.by[idx])
    near = idx[d <= SHOT_CONTACT_M]
    return (int(near[-1]), True) if len(near) else (i_event, False)


def shot_kick_time(pf: PeriodFrames, e) -> float:
    """Feed time of the kick of shot `e` (`kick_index`); the event time itself when the kick is not located."""
    i, located = kick_index(pf, e)
    return float(pf.t[i]) if located else e.t
