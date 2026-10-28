"""Writes `positions.parquet` and `events.json` in the format read by the synthetic adapter.

positions.parquet: one row per object per frame, frame-major (period, frame), the ball row first,
then the home slots, then the away slots; only objects on the pitch have rows.
Columns: period (int8), frame (int32; period 1 starts at 10000, period 2 at 100000, 25 Hz),
time_utc (timestamp[ns, UTC]), object_id, object_type (player|ball), team_side (home|away|none),
x_m, y_m, z_m (float32, centre origin, metres, rounded to 0.01; z only for the ball),
ball_status (live|dead), ball_possession (home|away|none). zstd + dictionary encoding.

events.json: {match_id, universe_id, generator_version, seed, events: [...]}, each event with
event_id, event_type, sub_type, time_utc (the time of the event's frame: no annotation delay, the
second-half kick-off included), period, team_side, player_id, x_m, y_m (annotated location, with
N(0, 0.8 m) noise), attributes (DFL names), true_frame.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .pitch import FRAME0, FS
from .players import measurement_noise, smooth_noise

NS_PER_FRAME = 1_000_000_000 // FS
ANNOTATION_NOISE_M = 0.8
BALL_NOISE_M = 0.03


def _dict_array(codes: np.ndarray, values: list[str]) -> pa.DictionaryArray:
    return pa.DictionaryArray.from_arrays(pa.array(codes.astype(np.int16), type=pa.int16()), pa.array(values, pa.string()))


def iso_utc(ns: int) -> str:
    return str(np.datetime_as_string(np.datetime64(int(ns), "ns"), unit="ms")) + "+00:00"


def write_positions(engine, kickoff_ns: dict[int, int], path: Path, rng_noise: np.random.Generator) -> dict:
    """Frame-major parquet of all periods. Returns per-period noisy arrays for the summary."""
    object_ids = ["BALL"] + list(engine.object_ids)
    side_of_pid = {p: engine.roster.players[p].side for p in engine.object_ids}
    side_codes = ["home", "away", "none"]
    obj_side = np.array([2] + [side_codes.index(side_of_pid[p]) for p in engine.object_ids], np.int16)
    cols = {k: [] for k in ("period", "frame", "time", "obj", "otype", "side", "x", "y", "z", "status", "poss")}
    for period in sorted(engine.buffers):
        buf = engine.buffers[period]
        n = buf.n
        P = buf.P[:n].copy()
        B = buf.B[:n].copy()
        live = buf.live[:n]
        # measurement noise: smooth in time, ~0.1 m, larger at high speed (players); small for the live ball
        for s in range(P.shape[1]):
            P[:, s] += measurement_noise(rng_noise, P[:, s])
        bn = smooth_noise(rng_noise, (n, 3), 3.0) * BALL_NOISE_M
        B += bn * live[:, None]
        B[:, 2] = np.maximum(B[:, 2], 0.0)
        P = np.round(P, 2)
        B = np.round(B, 2)
        pid = buf.pid[:n].astype(np.int32)
        n_slots = pid.shape[1]
        obj = np.empty((n, n_slots + 1), np.int32)
        obj[:, 0] = 0
        obj[:, 1:] = np.where(pid >= 0, pid + 1, -1)
        present = obj >= 0
        frames = FRAME0[period] + np.arange(n)
        fr = np.repeat(frames[:, None], n_slots + 1, axis=1)[present]
        o = obj[present]
        x = np.concatenate([B[:, :1], P[:, :, 0]], axis=1)[present]
        y = np.concatenate([B[:, 1:2], P[:, :, 1]], axis=1)[present]
        z = np.concatenate([B[:, 2:3], np.full((n, n_slots), np.nan)], axis=1)[present]
        st = np.repeat(np.where(live, 0, 1)[:, None], n_slots + 1, axis=1)[present]
        poss_map = np.array([2, 0, 1], np.int16)  # engine code none/home/away -> dictionary index
        po = np.repeat(poss_map[buf.poss[:n]][:, None], n_slots + 1, axis=1)[present]
        cols["period"].append(np.full(o.shape[0], period, np.int8))
        cols["frame"].append(fr.astype(np.int32))
        cols["time"].append(kickoff_ns[period] + (fr - FRAME0[period]).astype(np.int64) * NS_PER_FRAME)
        cols["obj"].append(o)
        cols["otype"].append((o > 0).astype(np.int16) * 0 + (o == 0).astype(np.int16))
        cols["side"].append(obj_side[o])
        cols["x"].append(x.astype(np.float32))
        cols["y"].append(y.astype(np.float32))
        cols["z"].append(z.astype(np.float32))
        cols["status"].append(st.astype(np.int16))
        cols["poss"].append(po)
    cat = {k: np.concatenate(v) for k, v in cols.items()}
    table = pa.table({
        "period": pa.array(cat["period"], pa.int8()),
        "frame": pa.array(cat["frame"], pa.int32()),
        "time_utc": pa.array(cat["time"].astype("datetime64[ns]"), pa.timestamp("ns", tz="UTC")),
        "object_id": _dict_array(cat["obj"], object_ids),
        "object_type": _dict_array(cat["otype"], ["player", "ball"]),
        "team_side": _dict_array(cat["side"], side_codes),
        "x_m": pa.array(cat["x"], pa.float32()),
        "y_m": pa.array(cat["y"], pa.float32()),
        "z_m": pa.array(cat["z"], pa.float32()),
        "ball_status": _dict_array(cat["status"], ["live", "dead"]),
        "ball_possession": _dict_array(cat["poss"], ["home", "away", "none"]),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", compression_level=9, use_dictionary=True,
                   row_group_size=500_000, write_statistics=True)
    return {"rows": table.num_rows}


def _clean(v):
    if isinstance(v, (np.floating,)):
        return round(float(v), 3)
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def build_events(engine, kickoff_ns: dict[int, int], match_id: str, rng_annot: np.random.Generator) -> list[dict]:
    evs = sorted(engine.events, key=lambda e: (e["period"], e["f"], e["seq"]))
    out = []
    hl, hw = engine.pitch.hl, engine.pitch.hw
    for i, ev in enumerate(evs, start=1):
        frame = FRAME0[ev["period"]] + ev["f"]
        ns = kickoff_ns[ev["period"]] + ev["f"] * NS_PER_FRAME  # the event's frame, exactly
        x = float(np.clip(ev["x"] + rng_annot.normal(0.0, ANNOTATION_NOISE_M), -hl - 0.5, hl + 0.5))
        y = float(np.clip(ev["y"] + rng_annot.normal(0.0, ANNOTATION_NOISE_M), -hw - 0.5, hw + 0.5))
        attrs = {k: _clean(v) for k, v in ev["attributes"].items() if v is not None}
        out.append({
            "event_id": f"{match_id}-E{i:05d}",
            "event_type": ev["event_type"],
            "sub_type": ev["sub_type"],
            "time_utc": iso_utc(ns),
            "period": int(ev["period"]),
            "team_side": ev["team_side"],
            "player_id": ev["player_id"],
            "x_m": round(x, 2),
            "y_m": round(y, 2),
            "attributes": attrs,
            "true_frame": int(frame),
        })
    return out


def write_events(events: list[dict], path: Path, match_id: str, universe_id: str, version: str, seed: int) -> None:
    doc = {"match_id": match_id, "universe_id": universe_id, "generator_version": version, "seed": seed,
           "events": events}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def file_info(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
