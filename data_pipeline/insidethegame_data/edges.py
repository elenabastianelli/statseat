"""Tracking-area edges and parking values, discovered per match and per provider (guidelines v4, section 5.2).

``R-EDGE`` (section 5.2.1). An edge is a coordinate value beyond a pitch line (goal line or touchline) that is
reached **exactly** by at least ``min_samples`` player samples. Two extra conditions
keep frozen tracks from being mistaken for edges (a player frozen 2 m behind a
touchline produces many identical samples too):

* the value is the **outermost** value observed on that side of that axis (a clamp
  cannot be exceeded);
* the samples at the value come from at least two distinct positions (the other
  coordinate differs) or from at least two separate runs.

No edge value is ever hard-coded.

``R-PARKING`` (section 5.2.2). A parking value is a coordinate value beyond a pitch
line where the provider leaves **several frozen tracks**: the frozen runs (identical
position for >= 1 s) beyond a line are grouped by the coordinate that lies beyond it
(per axis and side, rounded); a value is a parking value when its runs come from at
least ``min_tracks`` different players and total at least ``min_samples`` samples, and
it is not an edge. It need not be the outermost value. Every run of identical
consecutive positions (>= 2 samples) at a parking value is ``parked``, including its
first sample.
"""

from __future__ import annotations

import numpy as np

from .segments import run_lengths


def _as_tracks(a) -> list[np.ndarray]:
    """Normalise the input to a list of 2-D (frames x tracks) arrays.

    A 1-D array is one track; a 2-D array has one column per track; a list holds
    one 2-D array per period. Runs are always counted **along the frames of one
    track**: flattening a (frames x players) array row by row would put the samples
    of a frozen player ``n_players`` apart and turn each into its own "run".
    """
    if isinstance(a, (list, tuple)):
        return [np.atleast_2d(np.asarray(v, float).T).T if np.ndim(v) == 1 else np.asarray(v, float) for v in a]
    a = np.asarray(a, float)
    return [a.reshape(-1, 1)] if a.ndim == 1 else [a]


def discover_edges(x, y, half_length: float, half_width: float, min_samples: int, decimals: int = 4) -> dict:
    """``x``, ``y``: canonical player coordinates (centred, metres), as a 1-D track, a
    (frames x tracks) array or a list of such arrays (one per period).

    Returns ``{"edges": [...], "candidates_rejected": [...]}``; each edge is
    ``{"axis", "side", "value_m", "beyond_line_m", "samples", "distinct_positions", "runs", "tracks"}``.
    """
    xs, ys = _as_tracks(x), _as_tracks(y)
    edges, rejected = [], []
    for axis, vs, others, lim in (("x", xs, ys, half_length), ("y", ys, xs, half_width)):
        vr = [np.round(v, decimals) for v in vs]
        orr = [np.round(o, decimals) for o in others]
        for side in ("+", "-"):
            beyond = np.concatenate([(v[np.isfinite(v) & ((v > lim) if side == "+" else (v < -lim))]) for v in vr])
            if not len(beyond):
                continue
            vals, counts = np.unique(beyond, return_counts=True)
            outer = vals.max() if side == "+" else vals.min()
            for val, cnt in zip(vals, counts):
                if cnt < min_samples:
                    continue
                n_runs, n_tracks, other_vals = 0, 0, []
                for v, o in zip(vr, orr):
                    at = v == val  # (frames, tracks)
                    if not at.any():
                        continue
                    n_runs += int(at[0].sum() + (at[1:] & ~at[:-1]).sum())
                    n_tracks += int(at.any(axis=0).sum())
                    other_vals.append(o[at])
                n_pos = len(np.unique(np.concatenate(other_vals)))
                cand = {"axis": axis, "side": side, "value_m": float(val), "beyond_line_m": round(float(abs(val) - lim), 4), "samples": int(cnt),
                        "distinct_positions": int(n_pos), "runs": int(n_runs), "tracks": int(n_tracks)}
                if val == outer and (n_pos >= 2 or n_runs >= 2):
                    edges.append(cand)
                else:
                    cand["reason"] = "not the outermost value on that side" if val != outer else "a single frozen position (one run)"
                    rejected.append(cand)
    rejected.sort(key=lambda c: (-c["samples"], c["axis"], c["value_m"]))
    return {"edges": edges, "candidates_rejected": rejected[:10], "n_candidates_rejected": len(rejected)}


def edge_mask(x: np.ndarray, y: np.ndarray, edges: list[dict], decimals: int = 4) -> np.ndarray:
    """Samples sitting exactly on a discovered edge."""
    m = np.zeros(np.shape(x), dtype=bool)
    for e in edges:
        v = x if e["axis"] == "x" else y
        m |= np.round(v, decimals) == e["value_m"]
    return m


def _frozen_runs(x: np.ndarray, y: np.ndarray, min_repeats: int):
    """(start, end) index pairs (inclusive) of runs of identical positions whose position repeats >= ``min_repeats``
    times, **including the first sample** (the last real measurement before the repeats)."""
    with np.errstate(invalid="ignore"):
        still = np.r_[False, (x[1:] == x[:-1]) & (y[1:] == y[:-1])]
    out = []
    for s, ln in zip(*run_lengths(still)):
        if ln >= min_repeats:
            out.append((int(s) - 1, int(s + ln - 1)))
    return out


def discover_parking_values(xs, ys, pids, half_length: float, half_width: float, frozen_min_frames: int, min_samples: int,
                            min_tracks: int, edges: list[dict] | None = None, decimals: int = 4, same_track: dict | None = None) -> dict:
    """``R-PARKING`` discovery, after ``R-EDGE``.

    ``xs``, ``ys``: one (frames x players) array per period (canonical metres); ``pids``: the player IDs of the
    columns of each period (a player counts once across periods). ``frozen_min_frames``: repeats that make a
    frozen run (``R-FROZEN``, 1 s). ``same_track``: incoming -> outgoing player ID of the substitutions with identity
    inheritance (``R-SUB-INHERIT``): the incoming ID continues the outgoing player's track, so a frozen tail and the
    inherited frozen head at the same position count as **one** track, not two players. Returns
    ``{"parking_values": [...], "candidates_rejected": [...]}``; each value is
    ``{"axis", "side", "value_m", "beyond_line_m", "samples", "runs", "players"}``.
    """
    xs, ys = _as_tracks(xs), _as_tracks(ys)
    if not isinstance(pids[0], (list, tuple)):
        pids = [pids]
    links = dict(same_track or {})

    def track_of(pid: str) -> str:
        seen = set()
        while pid in links and pid not in seen:  # follow chains of inheritance (bounded)
            seen.add(pid)
            pid = links[pid]
        return pid

    edge_keys = {(e["axis"], float(e["value_m"])) for e in (edges or [])}
    groups: dict[tuple[str, str, float], dict] = {}
    for xa, ya, ids in zip(xs, ys, pids):
        for c in range(xa.shape[1]):
            for lo, hi in _frozen_runs(xa[:, c], ya[:, c], frozen_min_frames):
                px, py = float(xa[lo, c]), float(ya[lo, c])
                for axis, v, lim in (("x", px, half_length), ("y", py, half_width)):
                    if not np.isfinite(v) or abs(v) <= lim:
                        continue
                    key = (axis, "+" if v > 0 else "-", float(np.round(v, decimals)))
                    g = groups.setdefault(key, {"samples": 0, "runs": 0, "players": set()})
                    g["samples"] += hi - lo + 1
                    g["runs"] += 1
                    g["players"].add(track_of(str(ids[c])))
    values, rejected = [], []
    for (axis, side, val), g in sorted(groups.items()):
        lim = half_length if axis == "x" else half_width
        row = {"axis": axis, "side": side, "value_m": val, "beyond_line_m": round(abs(val) - lim, 4), "samples": int(g["samples"]),
               "runs": int(g["runs"]), "players": int(len(g["players"]))}
        if (axis, val) in edge_keys:
            row["reason"] = "a tracking-area edge (R-EDGE), never a parking value"
        elif len(g["players"]) < min_tracks:
            row["reason"] = f"frozen runs from fewer than {min_tracks} players (only frozen)"
        elif g["samples"] < min_samples:
            row["reason"] = f"fewer than {min_samples} frozen samples"
        else:
            values.append(row)
            continue
        rejected.append(row)
    rejected.sort(key=lambda c: (-c["samples"], c["axis"], c["value_m"]))
    return {"parking_values": values, "candidates_rejected": rejected[:10], "n_candidates_rejected": len(rejected)}


def parking_mask(x: np.ndarray, y: np.ndarray, values: list[dict], decimals: int = 4) -> np.ndarray:
    """``R-PARKING`` marking: samples of runs of identical consecutive positions (>= 2 samples) sitting at a
    parking value, **including the first sample of each run** (a parked position is never a real measurement)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.zeros(x.shape, dtype=bool)
    if not values or x.shape[0] < 2:
        return m
    at = np.zeros(x.shape, dtype=bool)
    for v in values:
        at |= np.round(x if v["axis"] == "x" else y, decimals) == v["value_m"]
    with np.errstate(invalid="ignore"):
        same = (x[1:] == x[:-1]) & (y[1:] == y[:-1])
    ident = np.zeros(x.shape, dtype=bool)
    ident[1:] |= same
    ident[:-1] |= same
    return at & ident
