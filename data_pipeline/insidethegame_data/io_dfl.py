"""Loaders for the DFL / Sportec open dataset (Bassek et al., 2025).

Design choices
--------------
* Tracking is stream-parsed with ``lxml.etree.iterparse`` instead of kloppy's
  frame-oriented loader, because QC needs the raw provider attributes
  (``D`` in cm, ``S`` in km/h, ``A`` in m/s^2) and the per-``FrameSet`` structure
  (one FrameSet per object and half). kloppy is used as an independent
  cross-check in ``crosscheck.py``.
* Units are normalised to SI at load time: ``d_m = D / 100``,
  ``s_ms = S / 3.6``. Timestamps become timezone-aware UTC.
* ``Delete`` events are placeholders without information and are dropped here.
  Only their count is kept (``EventData.n_delete``).
* Player names are deliberately not loaded: the pipeline only needs IDs.
"""

from __future__ import annotations

import json
from array import array
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from lxml import etree

DELETE_EVENT = "Delete"
BALL_ID = "BALL"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchFiles:
    match_id: str
    matchinformation: Path
    events: Path
    positions: Path


def find_match_files(raw_dir: Path, match_id: str) -> MatchFiles:
    """Locate the three DFL XML files for ``match_id`` (e.g. 'J03WPY')."""
    raw_dir = Path(raw_dir)

    def one(pattern: str) -> Path:
        hits = sorted(raw_dir.glob(pattern))
        if len(hits) != 1:
            raise FileNotFoundError(f"expected exactly one file for {pattern!r} in {raw_dir}, found {len(hits)}")
        return hits[0]

    return MatchFiles(
        match_id=match_id,
        matchinformation=one(f"DFL_02_01_matchinformation_*_DFL-MAT-{match_id}.xml"),
        events=one(f"DFL_03_02_events_raw_*_DFL-MAT-{match_id}.xml"),
        positions=one(f"DFL_04_03_positions_raw_observed_*_DFL-MAT-{match_id}.xml"),
    )


# ---------------------------------------------------------------------------
# Match information
# ---------------------------------------------------------------------------

@dataclass
class MatchInfo:
    match_id: str
    competition_id: str | None
    season: str | None
    home_team_id: str
    away_team_id: str
    pitch_x_m: float | None
    pitch_y_m: float | None
    kickoff_time_utc: pd.Timestamp | None
    other_game_info: dict[str, float]  # milliseconds, as in the file
    lineup: pd.DataFrame  # person_id, team_id, team_role, shirt_number, starting, playing_position
    formations: dict[str, str | None] = field(default_factory=dict)  # team role -> provider formation (source value)
    country: str | None = None  # provider value, mapped to English in the profile


def _to_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_matchinformation(path: Path) -> MatchInfo:
    root = etree.parse(str(path)).getroot()
    mi = root.find("MatchInformation")
    general = mi.find("General").attrib
    env = mi.find("Environment")
    env_attr = env.attrib if env is not None else {}
    rows = []
    formations = {}
    for team in mi.find("Teams").findall("Team"):
        role = team.get("Role")
        formations["home" if role == "home" else "away"] = team.get("LineUp")
        for p in team.find("Players").findall("Player"):
            rows.append(
                {
                    "person_id": p.get("PersonId"),
                    "team_id": team.get("TeamId"),
                    "team_role": "home" if role == "home" else "away",
                    "shirt_number": int(p.get("ShirtNumber")) if p.get("ShirtNumber") else None,
                    "starting": p.get("Starting") == "true",
                    "playing_position": p.get("PlayingPosition"),
                }
            )
    ogi = mi.find("OtherGameInformation")
    other = {k: float(v) for k, v in (ogi.attrib.items() if ogi is not None else [])}
    ko = general.get("KickoffTime")
    return MatchInfo(
        match_id=general.get("MatchId"),
        competition_id=general.get("CompetitionId"),
        season=general.get("Season"),
        home_team_id=general.get("HomeTeamId"),
        away_team_id=general.get("GuestTeamId"),
        pitch_x_m=_to_float(env_attr.get("PitchX")),
        pitch_y_m=_to_float(env_attr.get("PitchY")),
        kickoff_time_utc=pd.Timestamp(ko).tz_convert("UTC") if ko else None,
        other_game_info=other,
        lineup=pd.DataFrame(rows),
        formations=formations,
        country=env_attr.get("Country"),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass
class EventData:
    events: pd.DataFrame  # sorted by time, Delete dropped
    n_raw: int
    n_delete: int
    n_out_of_order_in_file: int  # adjacent inversions of EventTime in file order
    timezone_offsets: list[str] = field(default_factory=list)


def load_events(path: Path) -> EventData:
    """Parse events_raw. Drops ``Delete`` events and sorts by timestamp."""
    root = etree.parse(str(path)).getroot()
    rows = []
    for order, ev in enumerate(root.iter("Event")):
        children = [c for c in ev if isinstance(c.tag, str)]
        etype = children[0].tag if children else None
        child_attr = dict(children[0].attrib) if children else {}
        subtypes = [d.tag for d in children[0].iterdescendants() if isinstance(d.tag, str)] if children else []
        t_raw = ev.get("EventTime")
        rows.append(
            {
                "file_order": order,
                "event_id": ev.get("EventId"),
                "event_type": etype,
                "sub_types": "/".join(subtypes),
                "time_raw": t_raw,
                "x": _to_float(ev.get("X-Position")),
                "y": _to_float(ev.get("Y-Position")),
                "team_id": child_attr.get("Team"),
                "player_id": child_attr.get("Player"),
                "attrs_json": json.dumps(child_attr, sort_keys=True, ensure_ascii=False),
                # Provider's own synchronised frame, present on a few events (mostly shots).
                # Kept for the provider comparison only (tests); never used to locate an event.
                "provider_calculated_frame": _to_float(ev.get("CalculatedFrame")),
            }
        )
    df = pd.DataFrame(rows)
    n_raw = len(df)
    is_delete = df["event_type"] == DELETE_EVENT
    n_delete = int(is_delete.sum())
    df = df.loc[~is_delete].copy()
    offsets = sorted({t[-6:] for t in df["time_raw"].dropna()})
    df["time_utc"] = pd.to_datetime(df["time_raw"], format="ISO8601", utc=True)
    # Out-of-order count on the cleaned file order (before our sort).
    t_ns = df["time_utc"].astype("int64").to_numpy()
    n_inv = int(np.sum(np.diff(t_ns) < 0))
    df = df.sort_values(["time_utc", "file_order"], kind="mergesort").reset_index(drop=True)
    return EventData(events=df, n_raw=n_raw, n_delete=n_delete, n_out_of_order_in_file=n_inv, timezone_offsets=offsets)


def event_attr(events: pd.DataFrame, key: str) -> pd.Series:
    """Extract one attribute of the event's first child element."""
    return events["attrs_json"].map(lambda s: json.loads(s).get(key))


# ---------------------------------------------------------------------------
# Positions (tracking)
# ---------------------------------------------------------------------------

@dataclass
class PositionData:
    frames: pd.DataFrame  # one row per (frameset, frame)
    framesets: pd.DataFrame  # one row per FrameSet in file order
    pitch_size_meta: tuple[float, float] | None
    timezone_offsets: list[str]


_HALF_CODES = {"firstHalf": 1, "secondHalf": 2, "firstHalfExtra": 3, "secondHalfExtra": 4}


def load_positions(path: Path) -> PositionData:
    """Stream-parse positions_raw_observed into a tidy long table (SI units)."""
    fs_idx = array("i")
    n_arr = array("q")
    xs, ys, zs = array("d"), array("d"), array("d")
    ds, ss, as_, ms = array("d"), array("d"), array("d"), array("d")
    poss, status = array("d"), array("d")
    t_list: list[str] = []
    framesets: list[dict] = []
    pitch_meta = None
    nan = float("nan")

    def f(v):
        return float(v) if v is not None and v != "" else nan

    current = -1
    for event, el in etree.iterparse(str(path), events=("start", "end"), tag=("FrameSet", "Frame", "PitchSize")):
        if el.tag == "FrameSet" and event == "start":
            current += 1
            team = el.get("TeamId")
            framesets.append(
                {
                    "frameset_idx": current,
                    "game_section": el.get("GameSection"),
                    "half": _HALF_CODES.get(el.get("GameSection"), -1),
                    "team_id": team,
                    "object_id": BALL_ID if team == BALL_ID else el.get("PersonId"),
                    "is_ball": team == BALL_ID,
                }
            )
        elif el.tag == "Frame" and event == "end":
            a = el.attrib
            fs_idx.append(current)
            n_arr.append(int(a["N"]))
            t_list.append(a.get("T"))
            xs.append(f(a.get("X")))
            ys.append(f(a.get("Y")))
            zs.append(f(a.get("Z")))
            ds.append(f(a.get("D")))
            ss.append(f(a.get("S")))
            as_.append(f(a.get("A")))
            ms.append(f(a.get("M")))
            poss.append(f(a.get("BallPossession")))
            status.append(f(a.get("BallStatus")))
            el.clear()
        elif el.tag == "PitchSize" and event == "end":
            pitch_meta = (f(el.get("X")), f(el.get("Y")))
        elif el.tag == "FrameSet" and event == "end":
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]

    fsets = pd.DataFrame(framesets)
    idx = np.frombuffer(fs_idx, dtype=np.int32)
    t_series = pd.Series(t_list, dtype="string")
    offsets = sorted(t_series.str[-6:].dropna().unique().tolist())
    frames = pd.DataFrame(
        {
            "frameset_idx": idx,
            "half": fsets["half"].to_numpy()[idx].astype(np.int8),
            "object_id": pd.Categorical(fsets["object_id"].to_numpy()[idx]),
            "team_id": pd.Categorical(fsets["team_id"].to_numpy()[idx]),
            "is_ball": fsets["is_ball"].to_numpy()[idx],
            "n": np.frombuffer(n_arr, dtype=np.int64),
            "t_utc": pd.to_datetime(t_series, format="ISO8601", utc=True),
            "x": np.frombuffer(xs, dtype=np.float64),
            "y": np.frombuffer(ys, dtype=np.float64),
            "z": np.frombuffer(zs, dtype=np.float64),
            "d_m": np.frombuffer(ds, dtype=np.float64) / 100.0,  # provider D is cm
            "s_ms": np.frombuffer(ss, dtype=np.float64) / 3.6,  # provider S is km/h
            "a_ms2": np.frombuffer(as_, dtype=np.float64),
            "minute": np.frombuffer(ms, dtype=np.float64),
            "ball_possession": np.frombuffer(poss, dtype=np.float64),
            "ball_status": np.frombuffer(status, dtype=np.float64),
        }
    )
    return PositionData(frames=frames, framesets=fsets, pitch_size_meta=pitch_meta, timezone_offsets=offsets)


# ---------------------------------------------------------------------------
# Local cache (git-ignored Parquet)
# ---------------------------------------------------------------------------

def load_positions_cached(path: Path, cache_dir: Path) -> PositionData:
    """Parse once, then reuse a local Parquet copy (data/ is git-ignored)."""
    cache_dir = Path(cache_dir)
    fp, fsp, mp = cache_dir / "positions.parquet", cache_dir / "framesets.parquet", cache_dir / "positions_meta.json"
    src_stat = Path(path).stat()
    if fp.exists() and fsp.exists() and mp.exists():
        meta = json.loads(mp.read_text())
        if meta.get("source_size") == src_stat.st_size and meta.get("source_name") == Path(path).name:
            frames = pd.read_parquet(fp)
            return PositionData(
                frames=frames,
                framesets=pd.read_parquet(fsp),
                pitch_size_meta=tuple(meta["pitch_size_meta"]) if meta["pitch_size_meta"] else None,
                timezone_offsets=meta["timezone_offsets"],
            )
        # Never overwrite data files silently (guidelines rule: ask first).
        raise FileExistsError(f"cache in {cache_dir} does not match {Path(path).name}; remove it manually after confirmation")
    if fp.exists() or fsp.exists():
        raise FileExistsError(f"incomplete cache in {cache_dir}; remove it manually after confirmation")
    pdata = load_positions(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pdata.frames.to_parquet(fp, index=False)
    pdata.framesets.to_parquet(fsp, index=False)
    mp.write_text(
        json.dumps(
            {
                "source_name": Path(path).name,
                "source_size": src_stat.st_size,
                "pitch_size_meta": list(pdata.pitch_size_meta) if pdata.pitch_size_meta else None,
                "timezone_offsets": pdata.timezone_offsets,
            }
        )
    )
    return pdata
