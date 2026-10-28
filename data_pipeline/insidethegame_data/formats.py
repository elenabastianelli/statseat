"""Provider/format detection, unit inference and a second tiny adapter (guidelines v4, section 2).

* ``detect_format`` looks at the extension **and** the content; a folder with
  ``match_info.json`` + ``positions.parquet`` + ``events.json`` is one of our synthetic
  matches (adapter ``io_synthetic``).
* ``infer_coordinate_units`` / ``infer_speed_units`` infer units from the data and
  return the scale to SI; the inference is recorded in the match profile.
* ``load_generic_csv`` adapts a long CSV (one row per object per frame) to the
  same tidy table that the DFL loader produces, so that every later step is shared.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


class UnusableDataError(RuntimeError):
    """Raised only for the stop conditions of section 5.4."""


SYNTHETIC_FILES = ("match_info.json", "positions.parquet", "events.json")
SYNTHETIC_CONTENT = {"match_info.json": "matchinformation", "positions.parquet": "positions", "events.json": "events"}


def _is_synthetic_folder(folder: Path) -> bool:
    return folder.is_dir() and all((folder / f).is_file() for f in SYNTHETIC_FILES)


def detect_format(path: Path) -> dict:
    """Provider and format from the extension **and** the content (a folder or a single file)."""
    path = Path(path)
    # Our synthetic matches: a folder with match_info.json + positions.parquet + events.json (guidelines 2.3, 12).
    if _is_synthetic_folder(path):
        return {"provider": "InsideTheGame synthetic", "format": "folder (json + parquet)", "content": "match", "adapter": "io_synthetic"}
    if path.is_file() and path.name in SYNTHETIC_FILES and _is_synthetic_folder(path.parent):
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if (magic == b"PAR1") if path.suffix == ".parquet" else magic.lstrip()[:1] == b"{":
            return {"provider": "InsideTheGame synthetic", "format": path.suffix.lstrip("."), "content": SYNTHETIC_CONTENT[path.name], "adapter": "io_synthetic"}
    ext = path.suffix.lower()
    with open(path, "rb") as fh:
        head = fh.read(4096).decode("utf-8", errors="replace")
    stripped = head.lstrip()
    if ext == ".xml" or stripped.startswith("<?xml") or stripped.startswith("<"):
        if "<PutDataRequest" in head:
            kind = "positions" if "<Positions" in head else "events" if "<Event" in head else "matchinformation" if "<MatchInformation" in head else "unknown"
            return {"provider": "DFL/Sportec", "format": "xml", "content": kind, "adapter": "io_dfl"}
        return {"provider": "unknown", "format": "xml", "content": "unknown", "adapter": None}
    if ext in (".json", ".jsonl") or stripped.startswith("{") or stripped.startswith("["):
        return {"provider": "unknown (JSON: StatsBomb, Second Spectrum, SkillCorner?)", "format": "json", "content": "unknown", "adapter": None}
    if ext == ".parquet":
        return {"provider": "InsideTheGame", "format": "parquet", "content": "canonical cache", "adapter": "parquet"}
    first = head.splitlines()[0] if head else ""
    if ext == ".csv" or "," in first:
        cols = [c.strip().lower() for c in first.split(",")]
        if {"frame", "object_id", "x", "y"} <= set(cols):
            return {"provider": "generic", "format": "csv", "content": "positions", "adapter": "generic_csv"}
        return {"provider": "unknown (CSV: Metrica?)", "format": "csv", "content": "unknown", "adapter": None}
    return {"provider": "unknown", "format": ext or "unknown", "content": "unknown", "adapter": None}


def infer_coordinate_units(x: np.ndarray, y: np.ndarray) -> dict:
    """Coordinates spanning ~105 suggest metres, ~10,500 centimetres, [0, 1] normalised."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    x, y = x[np.isfinite(x)], y[np.isfinite(y)]
    span = float(max(np.ptp(x), np.ptp(y))) if len(x) and len(y) else float("nan")
    if not np.isfinite(span):
        return {"units": "unknown", "scale_to_m": None, "span": span}
    if span <= 1.5:
        return {"units": "normalised", "scale_to_m": None, "span": span}  # needs the pitch size
    if span <= 300:
        return {"units": "m", "scale_to_m": 1.0, "span": span}
    if span <= 30000:
        return {"units": "cm", "scale_to_m": 0.01, "span": span}
    return {"units": "mm", "scale_to_m": 0.001, "span": span}


def infer_speed_units(peak: float) -> dict:
    """Player speeds peaking near 30-35 are km/h, near 8-10 m/s (tests only: provider speed)."""
    if not np.isfinite(peak):
        return {"units": "unknown", "scale_to_ms": None, "peak": peak}
    if 15.0 <= peak <= 60.0:
        return {"units": "km/h", "scale_to_ms": 1 / 3.6, "peak": float(peak)}
    if 4.0 <= peak < 15.0:
        return {"units": "m/s", "scale_to_ms": 1.0, "peak": float(peak)}
    return {"units": "unknown", "scale_to_ms": None, "peak": float(peak)}


def load_generic_csv(path: Path, pitch_length_m: float | None = None, pitch_width_m: float | None = None) -> pd.DataFrame:
    """Long CSV -> the tidy table used by the pipeline (SI units, UTC, one row per object per frame).

    Field mapping: ``period -> half``, ``frame -> n``, ``time_s`` (seconds since an
    epoch) or ``time_utc`` -> ``t_utc``, ``object_id``, ``team`` (``BALL`` for the
    ball), ``x``, ``y``; optional ``ball_status`` (``live``/``dead`` or 1/0).
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"object_id", "frame"} <= set(df.columns):
        raise UnusableDataError("no object identity or frame information")
    if "time_utc" in df:
        t = pd.to_datetime(df["time_utc"], utc=True)
    elif "time_s" in df:
        t = pd.Timestamp(0, tz="UTC") + pd.to_timedelta(df["time_s"].astype(float), unit="s")
    else:
        raise UnusableDataError("no time information (no timestamps)")
    u = infer_coordinate_units(df["x"].to_numpy(), df["y"].to_numpy())
    x, y = df["x"].astype(float).to_numpy(), df["y"].astype(float).to_numpy()
    if u["units"] == "normalised":
        if not (pitch_length_m and pitch_width_m):
            raise UnusableDataError("normalised coordinates without a pitch size")
        x, y = x * pitch_length_m, y * pitch_width_m
    elif u["scale_to_m"]:
        x, y = x * u["scale_to_m"], y * u["scale_to_m"]
    team = df["team"].astype(str) if "team" in df else pd.Series(["unknown"] * len(df))
    is_ball = (team.str.upper() == "BALL").to_numpy()
    status = df["ball_status"].map({"live": 1.0, "dead": 0.0, 1: 1.0, 0: 0.0, "1": 1.0, "0": 0.0}) if "ball_status" in df else pd.Series(np.nan, index=df.index)
    keys = (df["period"].astype(str) + "|" + df["object_id"].astype(str)) if "period" in df else df["object_id"].astype(str)
    fs_idx = pd.factorize(keys, sort=True)[0]
    out = pd.DataFrame({
        "frameset_idx": fs_idx.astype(np.int32),
        "half": (df["period"] if "period" in df else 1).astype(np.int8),
        "object_id": pd.Categorical(np.where(is_ball, "BALL", df["object_id"].astype(str))),
        "team_id": pd.Categorical(team),
        "is_ball": is_ball,
        "n": df["frame"].astype(np.int64),
        "t_utc": t,
        "x": x, "y": y, "z": np.nan,
        "d_m": np.nan, "s_ms": np.nan, "a_ms2": np.nan, "minute": np.nan,
        "ball_possession": np.nan, "ball_status": status.astype(float).to_numpy(),
    })
    out.attrs["units"] = u
    return out
