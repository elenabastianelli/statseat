"""Time alignment (guidelines v4, sections 9.1 and 9.3 steps 1-2: ``R-TRACK-START``, ``R-CLOCK-OFFSET``, ``R-RESTART-SUB``).

* Period start: the ``KickOff`` event of the period (primary), then the kickoff
  time in the match metadata, then the first dead->live transition with the ball
  within 2 m of the centre spot, then the first tracking frame. The source is recorded.
* ``tracking_start_offset_s``: first tracking frame minus kickoff.
* ``event_clock_offset_s`` per period: robust median of the offsets between anchor
  events (kickoffs and restarts) and the provider's dead->live transitions;
  ``event_offset_dispersion_s`` is their MAD; the search window is
  ``max(5 s, 4 x MAD)`` capped at 30 s.
* Restarts are located at the nearest dead->live transition inside the search
  window; substitutions at the frame-set switch. Goals, passes, shots, clearances and
  duels are located afterwards from trajectories (``events_sync.py``, section 9.3
  steps 3-7) and are marked ``not_located`` here; no event location or type is changed.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import PrepConfig


def transitions(t_s: np.ndarray, status: np.ndarray, include_start: bool = True) -> np.ndarray:
    """Indices of the first live frame after each dead period.

    The ball is dead before a period starts, so a period whose tracking begins with the ball live (the
    kick-off frame, as in DFL and our synthetic data) starts with a transition at index 0. Without it the
    period's ``KickOff`` was paired with the next restart, up to 30 s later (SYN-001 period 2: +22.8 s, a
    ``KickOff`` median of +11.4 s; DFL J03WQQ period 2: +20.6 s). The kick-off fallback of
    ``period_kickoffs`` keeps ``include_start=False``: there the first frame is a separate, later fallback.
    """
    st = np.asarray(status)
    if st.size == 0:
        return np.zeros(0, dtype=int)
    inner = np.flatnonzero((st[:-1] == 0) & (st[1:] == 1)) + 1
    return np.concatenate([[0], inner]).astype(int) if include_start and st[0] == 1 else inner


def period_kickoffs(events: pd.DataFrame, half_t: dict[int, np.ndarray], half_status: dict[int, np.ndarray], half_ball: dict[int, tuple[np.ndarray, np.ndarray]],
                    meta_kickoff_utc: pd.Timestamp | None, cfg: PrepConfig) -> dict[int, dict]:
    sec = {"firstHalf": 1, "secondHalf": 2, "firstHalfExtra": 3, "secondHalfExtra": 4}
    ko = events.loc[events["event_type"] == "KickOff"]
    out = {}
    for h, t in half_t.items():
        first_frame = float(np.nanmin(t))
        row, source = None, None
        for _, e in ko.iterrows():
            if sec.get(json.loads(e["attrs_json"]).get("GameSection")) == h:
                row, source = e["time_utc"].timestamp(), "KickOff event"
                break
        if row is None and h == 1 and meta_kickoff_utc is not None:
            row, source = meta_kickoff_utc.timestamp(), "match metadata KickoffTime"
        if row is None:
            tr = transitions(t, half_status[h], include_start=False)
            bx, by = half_ball[h]
            near = [i for i in tr if np.hypot(bx[i], by[i]) <= cfg.kickoff_centre_radius_m]
            if near:
                row, source = float(t[near[0]]), "first dead->live transition at the centre spot"
        if row is None:
            row, source = first_frame, "first tracking frame"
        out[h] = {"kickoff_utc": pd.Timestamp(row, unit="s", tz="UTC").isoformat(), "kickoff_s": row, "source": source,
                  "tracking_start_offset_s": round(first_frame - row, 3)}
    if meta_kickoff_utc is not None and 1 in out:
        out[1]["metadata_kickoff_minus_event_s"] = round(meta_kickoff_utc.timestamp() - out[1]["kickoff_s"], 3)
    return out


def event_clock_offset(events: pd.DataFrame, half_t: dict[int, np.ndarray], half_status: dict[int, np.ndarray], cfg: PrepConfig) -> dict[int, dict]:
    out = {}
    for h, t in half_t.items():
        tr_t = t[transitions(t, half_status[h])]
        e = events.loc[(events["half"] == h) & events["event_type"].isin(cfg.restart_event_types)]
        offs, types = [], []
        for _, r in e.iterrows():
            te = r["time_utc"].timestamp()
            if len(tr_t) == 0:
                continue
            j = int(np.argmin(np.abs(tr_t - te)))
            d = float(tr_t[j] - te)
            if abs(d) <= cfg.anchor_max_offset_s:
                offs.append(d)
                types.append(r["event_type"])
        offs = np.array(offs)
        if len(offs) == 0:
            out[h] = {"event_clock_offset_s": 0.0, "event_offset_dispersion_s": None, "n_anchors": 0, "search_window_s": cfg.event_search_min_s,
                      "note": "no anchors; events used as they are"}
            continue
        med = float(np.median(offs))
        mad = float(np.median(np.abs(offs - med)))
        window = float(min(max(cfg.event_search_min_s, cfg.event_search_mad_factor * mad), cfg.event_search_max_s))
        by_type = pd.DataFrame({"type": types, "o": offs}).groupby("type")["o"].agg(["count", "median"]).round(3)
        out[h] = {"event_clock_offset_s": round(med, 3), "event_offset_dispersion_s": round(mad, 3), "n_anchors": int(len(offs)),
                  "search_window_s": round(window, 3), "anchor_offset_p10_p90_s": [round(float(np.quantile(offs, 0.1)), 3), round(float(np.quantile(offs, 0.9)), 3)],
                  "by_type": by_type.reset_index().to_dict(orient="records"),
                  "negligible": bool(abs(med) < 0.04 and mad < 0.04)}
    return out


def first_appearance_times(periods: list[tuple[int, list[str], np.ndarray, np.ndarray]]) -> dict[str, float]:
    """Frame-set switch time of each object: the time of its **first** tracked sample in the match.

    ``periods``: (period, object ids, t_s, present[frames, objects]) in any order. A substitute
    who enters in period 1 also has a track in period 2; only the first appearance is the switch.
    """
    out: dict[str, float] = {}
    for _, pids, t_s, present in sorted(periods, key=lambda p: p[0]):
        for c, pid in enumerate(pids):
            f = np.flatnonzero(present[:, c])
            if len(f) and pid not in out:
                out[pid] = float(t_s[f[0]])
    return out


def locate_events(events: pd.DataFrame, half_t: dict[int, np.ndarray], half_n: dict[int, np.ndarray], half_status: dict[int, np.ndarray],
                  offsets: dict[int, dict], sub_switch_s: dict[str, float], cfg: PrepConfig) -> pd.DataFrame:
    """Add ``sync_*`` and ``type_check`` columns (restarts and substitutions only in this task)."""
    ev = events.copy()
    cols = {"sync_time_utc": [], "sync_frame": [], "sync_certainty": [], "sync_method": [], "sync_residual_s": []}
    for _, r in ev.iterrows():
        h = int(r["half"])
        te = r["time_utc"].timestamp() + offsets.get(h, {}).get("event_clock_offset_s", 0.0)
        win = offsets.get(h, {}).get("search_window_s", cfg.event_search_min_s)
        res = (pd.NaT, None, None, "not_located", None)
        if r["event_type"] in cfg.restart_event_types and h in half_t:
            t = half_t[h]
            tr = transitions(t, half_status[h])
            if len(tr):
                j = int(np.argmin(np.abs(t[tr] - te)))
                d = float(t[tr[j]] - te)
                if abs(d) <= win:
                    res = (pd.Timestamp(t[tr[j]], unit="s", tz="UTC"), int(half_n[h][tr[j]]), round(1.0 - abs(d) / win, 3), "dead_live_transition", round(d, 3))
                else:
                    res = (pd.NaT, None, 0.0, "no_transition_in_window", None)
        elif r["event_type"] == "Substitution":
            pin = json.loads(r["attrs_json"]).get("PlayerIn")
            if pin in sub_switch_s:
                ts = sub_switch_s[pin]
                i = int(np.searchsorted(half_t[h], ts))
                res = (pd.Timestamp(ts, unit="s", tz="UTC"), int(half_n[h][min(i, len(half_n[h]) - 1)]), 1.0, "frameset_switch", round(ts - te, 3))
        for k, v in zip(cols, res):
            cols[k].append(v)
    for k, v in cols.items():
        ev[k] = v
    ev["type_check"] = "not_applicable"
    return ev
