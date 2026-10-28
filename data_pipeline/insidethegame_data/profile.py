"""Match profile: discover, don't assume (guidelines section 3).

Every later step reads sampling rate, pitch size, origin, frame offsets and
playing direction from the profile built here.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import PrepConfig as QCConfig
from .io_dfl import EventData, MatchInfo, PositionData, event_attr
from .mapping import map_position
from .tables import HalfArrays, to_seconds


# ---------------------------------------------------------------------------
# Sampling rate and frame ranges
# ---------------------------------------------------------------------------

def sampling_stats(frames: pd.DataFrame, tolerance_frac: float) -> dict[int, dict]:
    """dT statistics per half, pooled over objects, consecutive samples only.

    Times are taken relative to the first timestamp so that float precision
    does not bias dT (epoch seconds only resolve ~0.2 microseconds).
    """
    out = {}
    f = frames.sort_values(["frameset_idx", "n"], kind="mergesort")
    t = (f["t_utc"] - f["t_utc"].min()).dt.total_seconds().to_numpy()
    g = f["frameset_idx"].to_numpy()
    n = f["n"].to_numpy()
    halves = f["half"].to_numpy()
    same = g[1:] == g[:-1]
    dt = np.diff(t)[same]
    dn = np.diff(n)[same]
    hh = halves[1:][same]
    for h in sorted(np.unique(hh)):
        m = hh == h
        d = dt[m]
        med = float(np.median(d))
        outside = np.abs(d - med) > tolerance_frac * med
        dn1 = dn[m] == 1
        out[int(h)] = {
            "fs_hz": 1.0 / med,
            "dt_median_s": med,
            "dt_std_s": float(np.std(d)),
            "dt_min_s": float(np.min(d)),
            "dt_max_s": float(np.max(d)),
            "pct_dt_outside_tol": float(100.0 * outside.mean()),
            "n_steps": int(len(d)),
            "n_steps_dn_not_1": int((~dn1).sum()),
            # N-T relationship: share of dN == 1 steps whose dT is within tolerance of 1/fs
            "pct_dn1_with_dt_in_tol": float(100.0 * (~outside[dn1]).mean()) if dn1.any() else float("nan"),
        }
    return out


def frame_ranges(frames: pd.DataFrame, framesets: pd.DataFrame) -> dict:
    agg = (
        frames.groupby("frameset_idx", observed=True)
        .agg(n_first=("n", "min"), n_last=("n", "max"), n_frames=("n", "size"), t_first=("t_utc", "min"), t_last=("t_utc", "max"))
        .reset_index()
        .merge(framesets, on="frameset_idx")
    )
    per_half = {}
    for h, g in agg.groupby("half"):
        per_half[int(h)] = {
            "n_first": int(g["n_first"].min()),
            "n_last": int(g["n_last"].max()),
            "n_frames_span": int(g["n_last"].max() - g["n_first"].min() + 1),
            "t_first_utc": g["t_first"].min().isoformat(),
            "t_last_utc": g["t_last"].max().isoformat(),
            "n_framesets": int(len(g)),
        }
    halves = sorted(per_half)
    offsets = {}
    for a, b in zip(halves[:-1], halves[1:]):
        offsets[f"{a}->{b}"] = {
            "first_n_next_half": per_half[b]["n_first"],
            "jump_from_last_n": per_half[b]["n_first"] - per_half[a]["n_last"],
        }
    return {"per_half": per_half, "half_offsets": offsets, "per_frameset": agg}


# ---------------------------------------------------------------------------
# Kickoff frames and reference system
# ---------------------------------------------------------------------------

def kickoff_events(events: pd.DataFrame) -> pd.DataFrame:
    ko = events.loc[events["event_type"] == "KickOff"].copy()
    ko["game_section"] = event_attr(ko, "GameSection")
    ko["team_left"] = event_attr(ko, "TeamLeft")
    ko["team_right"] = event_attr(ko, "TeamRight")
    return ko


def find_kickoff_frame(ha: HalfArrays, t_event_s: float, window_s: float, centre_xy: tuple[float, float] = (0.0, 0.0), fit_frames: int = 5) -> dict:
    """Locate the frame where play (re)starts near a KickOff event.

    DFL ball positions are frozen while BallStatus = 0, so the ball spot cannot be
    read from dead-ball frames. We take the first live frame after the 0->1
    transition nearest the event time (or the first frame of the window when the
    tracking already starts live) and report:
    * ``ball_dist_first_live_m``: ball distance from the centre spot at that frame;
    * ``backtrack_dist_m``: distance from the centre spot to the backward
      extension of the ball path fitted on the first ``fit_frames`` live frames
      (small when the kick did start from the centre spot).
    """
    t = ha.t_s
    in_win = np.flatnonzero(np.isfinite(t) & (np.abs(t - t_event_s) <= window_s))
    if len(in_win) == 0:
        return {"found": False}
    st = ha.ball_status[in_win]
    trans = np.flatnonzero((st[:-1] == 0) & (st[1:] == 1))
    if len(trans):
        k = trans[np.argmin(np.abs(t[in_win[trans + 1]] - t_event_s))]
        i_go = in_win[k + 1]
        dead = True
    else:
        i_go = in_win[0]
        dead = False
    seg = slice(i_go, min(i_go + fit_frames, len(t)))
    px, py = ha.ball_x[seg], ha.ball_y[seg]
    cx, cy = centre_xy
    backtrack = float("nan")
    if len(px) >= 2 and np.all(np.isfinite(px)):
        d = np.array([px[-1] - px[0], py[-1] - py[0]])
        norm = np.hypot(*d)
        if norm > 1e-6:
            u = d / norm
            rel = np.array([cx - px[0], cy - py[0]])
            along = float(rel @ u)
            # Only the backward ray (along <= 0) is a valid origin of the kick.
            backtrack = float(abs(rel[0] * u[1] - rel[1] * u[0])) if along <= 0 else float(np.hypot(*rel))
    return {
        "found": True,
        "dead_ball_before": dead,
        "frame_index": int(i_go),
        "n": int(ha.n[i_go]),
        "t_utc": pd.Timestamp(t[i_go], unit="s", tz="UTC").isoformat(),
        "ball_x": float(ha.ball_x[i_go]),
        "ball_y": float(ha.ball_y[i_go]),
        "ball_dist_first_live_m": float(np.hypot(ha.ball_x[i_go] - cx, ha.ball_y[i_go] - cy)),
        "backtrack_dist_m": backtrack,
        "frame_minus_event_s": float(t[i_go] - t_event_s),
    }


def discover_origin(frames: pd.DataFrame, pitch_l: float, pitch_w: float, kickoff_ball_xy: list[tuple[float, float]]) -> dict:
    """Decide centred vs corner origin from player extents and kickoff ball spots."""
    p = frames.loc[~frames["is_ball"], ["x", "y"]].dropna()
    xq = np.quantile(p["x"], [0.0, 0.005, 0.995, 1.0])
    yq = np.quantile(p["y"], [0.0, 0.005, 0.995, 1.0])
    mid = ((xq[1] + xq[2]) / 2.0, (yq[1] + yq[2]) / 2.0)
    candidates = {"centre": (0.0, 0.0), "corner": (pitch_l / 2.0, pitch_w / 2.0)}

    def closest(pt):
        return min(candidates, key=lambda k: np.hypot(pt[0] - candidates[k][0], pt[1] - candidates[k][1]))

    votes = [closest(mid)] + [closest(b) for b in kickoff_ball_xy if np.all(np.isfinite(b))]
    origin = max(set(votes), key=votes.count)
    span_x, span_y = xq[2] - xq[1], yq[2] - yq[1]
    return {
        "tracking_origin": origin,
        "centre_spot_in_tracking": list(candidates[origin]),
        "consistent_votes": votes.count(origin) == len(votes),
        "votes": votes,
        "player_x_range": [float(xq[0]), float(xq[3])],
        "player_y_range": [float(yq[0]), float(yq[3])],
        "player_x_p0_5_p99_5": [float(xq[1]), float(xq[2])],
        "player_y_p0_5_p99_5": [float(yq[1]), float(yq[2])],
        "long_axis": "x" if span_x >= span_y else "y",
    }


def discover_event_origin(events: pd.DataFrame, pitch_l: float, pitch_w: float) -> dict:
    e = events.dropna(subset=["x", "y"])
    ko = e.loc[e["event_type"] == "KickOff", ["x", "y"]]
    ko_xy = ko.median().tolist() if len(ko) else [np.nan, np.nan]
    cand = {"corner": (pitch_l / 2.0, pitch_w / 2.0), "centre": (0.0, 0.0)}
    origin = min(cand, key=lambda k: np.hypot(ko_xy[0] - cand[k][0], ko_xy[1] - cand[k][1])) if len(ko) else "unknown"
    return {
        "event_origin": origin,
        "kickoff_xy": [float(v) for v in ko_xy],
        "event_x_range": [float(e["x"].min()), float(e["x"].max())],
        "event_y_range": [float(e["y"].min()), float(e["y"].max())],
        "centre_spot_in_events": list(cand[origin]) if origin != "unknown" else None,
    }


def _ball_lookup(has: dict[int, HalfArrays]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    t, x, y, st = [], [], [], []
    for h in sorted(has):
        ha = has[h]
        ok = np.isfinite(ha.t_s) & np.isfinite(ha.ball_x)
        t.append(ha.t_s[ok]); x.append(ha.ball_x[ok]); y.append(ha.ball_y[ok]); st.append(ha.ball_status[ok])
    t, x, y, st = (np.concatenate(a) for a in (t, x, y, st))
    o = np.argsort(t, kind="mergesort")
    return t[o], x[o], y[o], st[o]


def fit_event_transform(
    events: pd.DataFrame,
    has: dict[int, HalfArrays],
    event_centre: tuple[float, float],
    tracking_centre: tuple[float, float],
    fs_hz: float,
    lag_search_s: float,
    event_types: tuple[str, ...],
) -> dict:
    """Grid search over y/x sign flips and a global time lag.

    For each candidate ``x_t = sx (x_e - cx_e) + cx_t``, ``y_t = sy (y_e - cy_e) + cy_t``
    and lag L (event time + L = tracking time), score = median distance between the
    transformed event location and the ball at the nearest frame. This is a coarse,
    global alignment, not a per-event synchronisation (DataBallPy is planned later).
    """
    bt, bx, by, _ = _ball_lookup(has)
    e = events.loc[events["event_type"].isin(event_types)].dropna(subset=["x", "y"])
    te = to_seconds(e["time_utc"])
    ex, ey = e["x"].to_numpy(), e["y"].to_numpy()
    lags = np.arange(-lag_search_s, lag_search_s + 1e-9, 1.0 / fs_hz)
    results = []
    for sx in (1, -1):
        for sy in (1, -1):
            tx = sx * (ex - event_centre[0]) + tracking_centre[0]
            ty = sy * (ey - event_centre[1]) + tracking_centre[1]
            best = (np.inf, 0.0)
            for lag in lags:
                idx = np.clip(np.searchsorted(bt, te + lag), 1, len(bt) - 1)
                idx = np.where(np.abs(bt[idx - 1] - (te + lag)) < np.abs(bt[idx] - (te + lag)), idx - 1, idx)
                ok = np.abs(bt[idx] - (te + lag)) < 1.0 / fs_hz
                d = np.hypot(tx[ok] - bx[idx[ok]], ty[ok] - by[idx[ok]])
                med = float(np.median(d)) if len(d) else np.inf
                if med < best[0]:
                    best = (med, float(lag))
            results.append({"sx": sx, "sy": sy, "median_dist_m": best[0], "best_lag_s": best[1]})
    results.sort(key=lambda r: r["median_dist_m"])
    best = results[0]
    # Distances for the best transform, per event type and half.
    tx = best["sx"] * (ex - event_centre[0]) + tracking_centre[0]
    ty = best["sy"] * (ey - event_centre[1]) + tracking_centre[1]
    idx = np.clip(np.searchsorted(bt, te + best["best_lag_s"]), 1, len(bt) - 1)
    idx = np.where(np.abs(bt[idx - 1] - (te + best["best_lag_s"])) < np.abs(bt[idx] - (te + best["best_lag_s"])), idx - 1, idx)
    ok = np.abs(bt[idx] - (te + best["best_lag_s"])) < 1.0 / fs_hz
    dist = np.where(ok, np.hypot(tx - bx[idx], ty - by[idx]), np.nan)
    by_type = (
        pd.DataFrame({"type": e["event_type"].to_numpy(), "d": dist}).groupby("type")["d"].agg(["count", "median", "mean"]).round(2)
    )
    by_half = {}
    ev_half = e["half"].to_numpy() if "half" in e else np.ones(len(e), dtype=int)
    for h in np.unique(ev_half):
        m = ev_half == h
        by_half[str(int(h))] = {"n": int(np.isfinite(dist[m]).sum()), "median_m": float(np.nanmedian(dist[m]))}
    return {
        "by_half": by_half,
        "candidates": results,
        "best": best,
        "n_events_used": int(np.isfinite(dist).sum()),
        "dist_mean_m": float(np.nanmean(dist)),
        "dist_std_m": float(np.nanstd(dist)),
        "dist_p90_m": float(np.nanquantile(dist, 0.9)),
        "by_type": by_type.reset_index().to_dict(orient="records"),
        "y_sign_margin_m": float(min(r["median_dist_m"] for r in results if r["sy"] != best["sy"]) - best["median_dist_m"]),
    }


def goalkeeper_mean_x(frames: pd.DataFrame, lineup: pd.DataFrame) -> pd.DataFrame:
    # Goalkeepers through the reviewed mapping (DFL "TW" and English "GK" both give the GK group).
    is_gk = [map_position(c)["role_group"] == "GK" for c in lineup["playing_position"].tolist()]
    gk_ids = set(lineup.loc[is_gk, "person_id"])
    p = frames.loc[frames["object_id"].astype(str).isin(gk_ids)]
    return (
        p.groupby(["half", p["object_id"].astype(str), p["team_id"].astype(str)], observed=True)
        .agg(mean_x=("x", "mean"), mean_y=("y", "mean"), n_frames=("x", "size"))
        .reset_index()
        .rename(columns={"object_id": "person_id"})
    )


def direction_per_half(kickoffs: pd.DataFrame, gk: pd.DataFrame, home: str, away: str) -> dict:
    out = {}
    sec_to_half = {"firstHalf": 1, "secondHalf": 2, "firstHalfExtra": 3, "secondHalfExtra": 4}
    for _, k in kickoffs.dropna(subset=["game_section"]).iterrows():
        h = sec_to_half.get(k["game_section"])
        g = gk.loc[gk["half"] == h]
        # The team whose goalkeeper has the lower mean x defends the left goal.
        gk_by_team = g.sort_values("n_frames", ascending=False).drop_duplicates("team_id").set_index("team_id")["mean_x"]
        gk_left = gk_by_team.idxmin() if len(gk_by_team) == 2 else None
        role = {home: "home", away: "away"}
        out[h] = {
            "team_left": role.get(k["team_left"], k["team_left"]),
            "team_right": role.get(k["team_right"], k["team_right"]),
            "gk_mean_x_home": float(gk_by_team.get(home, np.nan)),
            "gk_mean_x_away": float(gk_by_team.get(away, np.nan)),
            "gk_left_team": role.get(gk_left, gk_left),
            "confirmed": gk_left == k["team_left"],
            # attack_sign: +1 if the team attacks towards +x in this half
            "home_attacks_positive_x": k["team_left"] == home,
        }
    return out


# ---------------------------------------------------------------------------
# Halves for events, substitutions, objects, playing time
# ---------------------------------------------------------------------------

def assign_event_half(events: pd.DataFrame, half_windows: dict[int, tuple[float, float]]) -> np.ndarray:
    """Half of each event: the half whose tracking window is nearest in time."""
    t = to_seconds(events["time_utc"])
    halves = sorted(half_windows)
    dist = np.stack([np.maximum(0, np.maximum(half_windows[h][0] - t, t - half_windows[h][1])) for h in halves], axis=1)
    return np.array(halves)[np.argmin(dist, axis=1)]


def substitutions(events: pd.DataFrame, framesets_agg: pd.DataFrame, home: str) -> list[dict]:
    s = events.loc[events["event_type"] == "Substitution"]
    rows = []
    for _, e in s.iterrows():
        a = json.loads(e["attrs_json"])
        fin = framesets_agg.loc[framesets_agg["object_id"] == a.get("PlayerIn")]
        fout = framesets_agg.loc[framesets_agg["object_id"] == a.get("PlayerOut")]
        t_in = fin["t_first"].min() if len(fin) else pd.NaT
        t_out = fout["t_last"].max() if len(fout) else pd.NaT
        rows.append(
            {
                "time_utc": e["time_utc"].isoformat(),
                "half": int(e["half"]),
                "team": "home" if a.get("Team") == home else "away",
                "player_out": a.get("PlayerOut"),
                "player_in": a.get("PlayerIn"),
                "playing_position": a.get("PlayingPosition"),
                "in_frameset_start_minus_event_s": float((t_in - e["time_utc"]).total_seconds()) if pd.notna(t_in) else None,
                "out_frameset_end_minus_event_s": float((t_out - e["time_utc"]).total_seconds()) if pd.notna(t_out) else None,
            }
        )
    return rows


def objects_summary(lineup: pd.DataFrame, framesets: pd.DataFrame, home: str, away: str) -> dict:
    with_fs = set(framesets.loc[~framesets["is_ball"], "object_id"])
    lu = set(lineup["person_id"])
    teams = {}
    for team, role in ((home, "home"), (away, "away")):
        l = lineup.loc[lineup["team_id"] == team]
        teams[role] = {
            "lineup_size": int(len(l)),
            "starters": int(l["starting"].sum()),
            "with_frameset": int(l["person_id"].isin(with_fs).sum()),
            "framesets_per_half": {int(h): int(n) for h, n in framesets.loc[framesets["team_id"] == team].groupby("half").size().items()},
        }
    no_fs = lineup.loc[~lineup["person_id"].isin(with_fs)]
    # Tracked objects that belong to neither team (for example referees): discovered, not assumed.
    other = framesets.loc[~framesets["is_ball"] & ~framesets["team_id"].astype(str).isin([home, away])]
    other_by_label = other.drop_duplicates("object_id").groupby(other.drop_duplicates("object_id")["team_id"].astype(str)).size().to_dict()
    return {
        "teams": teams,
        "ball_present_per_half": {int(h): bool(g["is_ball"].any()) for h, g in framesets.groupby("half")},
        "lineup_without_frameset": int(len(no_fs)),
        "lineup_without_frameset_starters": int(no_fs["starting"].sum()),
        "framesets_without_lineup": sorted(with_fs - lu),
        "referees_tracked": bool(len(other)),
        "non_player_objects_by_team_label": {str(k): int(v) for k, v in sorted(other_by_label.items())},
    }


def playing_time(info: MatchInfo, frames: pd.DataFrame, fs_hz: dict[int, float]) -> dict:
    ogi = info.other_game_info
    keys = {1: ("TotalTimeFirstHalf", "PlayingTimeFirstHalf"), 2: ("TotalTimeSecondHalf", "PlayingTimeSecondHalf")}
    ball = frames.loc[frames["is_ball"]]
    out = {}
    for h, (gk, nk) in keys.items():
        b = ball.loc[ball["half"] == h]
        if b.empty:
            continue
        n_frames = int(b["n"].max() - b["n"].min() + 1)
        dur = n_frames / fs_hz[h]
        alive = float((b["ball_status"] == 1).sum() / fs_hz[h])
        gross = ogi.get(gk, np.nan) / 1000.0
        net = ogi.get(nk, np.nan) / 1000.0
        out[h] = {
            "gross_s": gross,
            "net_s": net,
            "tracking_duration_s": dur,
            "tracking_minus_gross_s": dur - gross,
            "ball_alive_s": alive,
            "ball_alive_minus_net_s": alive - net,
        }
    return out


def infer_pitch_size(frames: pd.DataFrame) -> tuple[float, float]:
    """Fallback when no metadata gives the pitch size (section 3): twice the largest
    |x| and |y| of the ball at dead->live restarts (goal kicks and corners sit on the
    goal line, throw-ins on the touchline), around the centre of the restart extents.
    Rounded to 0.5 m. Recorded as inferred in the profile."""
    b = frames.loc[frames["is_ball"]].sort_values(["half", "n"])
    st = b["ball_status"].to_numpy()
    first_live = np.r_[False, (st[:-1] == 0) & (st[1:] == 1)]
    r = b.loc[first_live]
    cx = (r["x"].max() + r["x"].min()) / 2
    cy = (r["y"].max() + r["y"].min()) / 2
    L = round(2 * float((r["x"] - cx).abs().max()) * 2) / 2
    W = round(2 * float((r["y"] - cy).abs().max()) * 2) / 2
    return L, W


def build_match_profile(info: MatchInfo, ev: EventData, pos: PositionData, has: dict[int, HalfArrays], cfg: QCConfig) -> dict:
    frames, fsets = pos.frames, pos.framesets
    samp = sampling_stats(frames, cfg.sampling_tolerance_frac)
    fs_hz = {h: s["fs_hz"] for h, s in samp.items()}
    fr = frame_ranges(frames, fsets)
    L = info.pitch_x_m or (pos.pitch_size_meta[0] if pos.pitch_size_meta else None)
    W = info.pitch_y_m or (pos.pitch_size_meta[1] if pos.pitch_size_meta else None)
    pitch_source = "match metadata" if (L and W) else "inferred from ball restarts"
    if not (L and W):
        L, W = infer_pitch_size(frames)

    half_windows = {h: (has[h].t_s[np.isfinite(has[h].t_s)].min(), has[h].t_s[np.isfinite(has[h].t_s)].max()) for h in has}
    events = ev.events
    events["half"] = assign_event_half(events, half_windows)

    ko = kickoff_events(events)
    ko_frames = []
    for _, k in ko.iterrows():
        h = int(k["half"])
        r = find_kickoff_frame(has[h], to_seconds(pd.Series([k["time_utc"]]))[0], cfg.kickoff_search_window_s)
        r.update({"half": h, "game_section": k["game_section"], "event_time_utc": k["time_utc"].isoformat()})
        ko_frames.append(r)

    origin = discover_origin(frames, L, W, [(r["ball_x"], r["ball_y"]) for r in ko_frames if r.get("found")])
    # Re-measure kickoff distances against the discovered centre spot.
    for r, (_, k) in zip(ko_frames, ko.iterrows()):
        r.update(find_kickoff_frame(has[r["half"]], to_seconds(pd.Series([k["time_utc"]]))[0], cfg.kickoff_search_window_s, tuple(origin["centre_spot_in_tracking"])))
    ev_origin = discover_event_origin(events, L, W)
    tr = fit_event_transform(
        events, has, tuple(ev_origin["centre_spot_in_events"]), tuple(origin["centre_spot_in_tracking"]),
        float(np.median(list(fs_hz.values()))), cfg.transform_lag_search_s, cfg.transform_event_types,
    )
    gk = goalkeeper_mean_x(frames, info.lineup)
    direction = direction_per_half(ko, gk, info.home_team_id, info.away_team_id)

    agg = fr.pop("per_frameset")
    agg["object_id"] = agg["object_id"].astype(str)
    subs = substitutions(events, agg, info.home_team_id)
    caution = events.loc[events["event_type"] == "Caution"]
    cards = event_attr(caution, "CardColor").value_counts().to_dict() if len(caution) else {}

    profile = {
        "match_id": info.match_id,
        "competition_id": info.competition_id,
        "season": info.season,
        "fs_hz": {str(h): round(v, 6) for h, v in fs_hz.items()},
        "sampling": {str(h): v for h, v in samp.items()},
        "frame_ranges": {"per_half": {str(h): v for h, v in fr["per_half"].items()}, "half_offsets": fr["half_offsets"]},
        "pitch_length_m": L,
        "pitch_width_m": W,
        "pitch_size_source": pitch_source,
        "pitch_size_tracking_meta": list(pos.pitch_size_meta) if pos.pitch_size_meta else None,
        **origin,
        "axis_orientation": {
            "long_axis": origin["long_axis"],
            "x_positive_towards": "right goal (team_right defends +x)",
            "y_sign_vs_events": "same" if tr["best"]["sy"] == 1 else "flipped",
        },
        **ev_origin,
        "event_to_tracking_transform": {
            "x_tracking": f"{tr['best']['sx']:+d} * (x_event - {ev_origin['centre_spot_in_events'][0]}) + {origin['centre_spot_in_tracking'][0]}",
            "y_tracking": f"{tr['best']['sy']:+d} * (y_event - {ev_origin['centre_spot_in_events'][1]}) + {origin['centre_spot_in_tracking'][1]}",
            **tr,
        },
        "kickoff_frames": ko_frames,
        "direction_per_half": {str(h): v for h, v in direction.items()},
        "goalkeepers": gk.to_dict(orient="records"),
        "timezones": {"events": ev.timezone_offsets, "tracking": pos.timezone_offsets},
        "objects": objects_summary(info.lineup, fsets, info.home_team_id, info.away_team_id),
        "substitutions": subs,
        "cards": cards,
        "delete_events": ev.n_delete,
        "events_raw": ev.n_raw,
        "events_kept": int(len(events)),
        "events_out_of_order_in_file": ev.n_out_of_order_in_file,
        "event_types_kept": events["event_type"].value_counts().to_dict(),
        "playing_time": {str(h): v for h, v in playing_time(info, frames, fs_hz).items()},
    }
    return profile
