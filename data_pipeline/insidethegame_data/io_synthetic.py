"""Adapter for our own synthetic matches (guidelines v4, sections 2.3 and 12).

A synthetic match is a folder ``synthetic/<match_id>/`` with three files written by
the generator (``generator/insidethegame_gen/match/``):

* ``match_info.json`` - DFL ``matchinformation`` structure with English codes
  (teams, line-ups with English positions, pitch size, planned kickoff);
* ``positions.parquet`` - one row per object per frame: ``period``, ``frame``,
  ``time_utc``, ``object_id`` (universe player id or ``BALL``), ``object_type``
  (``player``|``ball``|``referee``), ``team_side`` (``home``|``away``|``none``),
  ``x_m``, ``y_m``, ``z_m`` (centre origin, metres), ``ball_status`` (``live``|``dead``),
  ``ball_possession`` (``home``|``away``|``none``); optional ``provider_speed_ms``,
  ``provider_accel_ms2``, ``provider_step_m`` (tests only);
* ``events.json`` - ``{"match_id", "universe_id", "generator_version", "seed", "events": [...]}``
  with DFL event types and attribute names in English.

The loaders return the **same tables and dataclasses as** ``io_dfl`` (``MatchInfo``,
``EventData``, ``PositionData``), so every later step of the pipeline is shared:
the match profile is discovered, never assumed (origin, direction, clock offset,
edges), exactly as for DFL data.

Field mapping (documented for the preparation report):

* ``team_side`` -> the team ID of ``match_info.json`` (``guest`` is the away team); the ball's team is ``BALL``;
* ``ball_status`` live/dead -> 1/0; ``ball_possession`` home/away -> 1/2 (``none`` -> missing), as DFL;
* event ``x_m``/``y_m`` -> the event coordinates (the event-to-tracking transform is fitted as for DFL);
* event ``attributes`` -> ``attrs_json``. DFL attributes the pipeline reads are filled **only when
  missing**: ``Team``/``Player`` from ``team_side``/``player_id``; ``GameSection`` from ``period``;
  ``Winner``/``WinnerTeam`` of a duel from the acting player; ``PlayingPosition`` of a substitution from
  the line-up; ``TeamLeft``/``TeamRight`` of a kickoff from the goalkeepers' mean x in that period
  (discovered on the tracking, and logged). Team-valued attributes given as ``home``/``away`` become team IDs;
* ``true_frame`` (generator truth) -> ``provider_calculated_frame``: **tests only**, like DFL
  ``CalculatedFrame``; it never locates anything;
* ``Delete`` events are dropped and counted (the generator never writes them).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .formats import UnusableDataError
from .io_dfl import BALL_ID, DELETE_EVENT, EventData, MatchInfo, PositionData

SYNTHETIC_FILES = ("match_info.json", "positions.parquet", "events.json")
PROVIDER = "InsideTheGame synthetic"
SECTIONS = {1: "firstHalf", 2: "secondHalf", 3: "firstHalfExtra", 4: "secondHalfExtra"}
POSITION_COLUMNS = ("period", "frame", "time_utc", "object_id", "object_type", "team_side", "x_m", "y_m")
OPTIONAL_PROVIDER = {"provider_step_m": "d_m", "provider_speed_ms": "s_ms", "provider_accel_ms2": "a_ms2"}


@dataclass(frozen=True)
class SyntheticFiles:
    match_id: str
    folder: Path
    match_info: Path
    positions: Path
    events: Path


def is_synthetic_folder(path: Path) -> bool:
    p = Path(path)
    return p.is_dir() and all((p / f).is_file() for f in SYNTHETIC_FILES)


def find_synthetic_files(folder: Path, match_id: str | None = None) -> SyntheticFiles:
    folder = Path(folder)
    missing = [f for f in SYNTHETIC_FILES if not (folder / f).is_file()]
    if missing:
        raise FileNotFoundError(f"synthetic match folder {folder} is missing {missing}")
    return SyntheticFiles(match_id=match_id or folder.name, folder=folder, match_info=folder / "match_info.json",
                          positions=folder / "positions.parquet", events=folder / "events.json")


# ---------------------------------------------------------------------------
# Match information
# ---------------------------------------------------------------------------

def load_synthetic_matchinfo(path: Path) -> MatchInfo:
    mi = json.loads(Path(path).read_text(encoding="utf-8"))
    home, away = mi["home_team_id"], mi.get("guest_team_id") or mi.get("away_team_id")
    rows, formations = [], {}
    for team in mi["teams"]:
        side = "home" if team["role"] == "home" else "away"
        formations[side] = team.get("formation")
        for p in team.get("lineup", []):
            rows.append({"person_id": p["player_id"], "team_id": team["team_id"], "team_role": side, "shirt_number": p.get("shirt_number"),
                         "starting": bool(p.get("starting")), "playing_position": p.get("playing_position")})
    env = mi.get("environment", {})
    ko = mi.get("kickoff_utc") or mi.get("planned_kickoff")
    return MatchInfo(
        match_id=mi["match_id"], competition_id=mi.get("competition_id"), season=mi.get("season"), home_team_id=home, away_team_id=away,
        pitch_x_m=env.get("pitch_x_m"), pitch_y_m=env.get("pitch_y_m"),
        kickoff_time_utc=pd.Timestamp(ko).tz_convert("UTC") if ko else None,
        other_game_info={}, lineup=pd.DataFrame(rows, columns=["person_id", "team_id", "team_role", "shirt_number", "starting", "playing_position"]),
        formations=formations, country=env.get("country"),
    )


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def load_synthetic_positions(path: Path, info: MatchInfo) -> PositionData:
    df = pd.read_parquet(path)
    missing = [c for c in POSITION_COLUMNS if c not in df.columns]
    if "time_utc" in missing:
        raise UnusableDataError("stop condition (section 5.4): positions.parquet has no time information")
    if "object_id" in missing:
        raise UnusableDataError("stop condition (section 5.4): positions.parquet has no object identity")
    if missing:
        raise UnusableDataError(f"positions.parquet cannot be adapted: missing columns {missing}")
    otype = df["object_type"].astype(str).str.lower().to_numpy()
    is_ball = (otype == "ball") | (df["object_id"].astype(str).str.upper() == BALL_ID).to_numpy()
    side = df["team_side"].astype(str).str.lower().to_numpy()
    team = np.where(is_ball, BALL_ID, np.where(side == "home", info.home_team_id, np.where(side == "away", info.away_team_id,
                                                                                            np.where(otype == "referee", "referee", "none"))))
    obj = np.where(is_ball, BALL_ID, df["object_id"].astype(str).to_numpy())
    period = df["period"].astype(np.int64).to_numpy()
    # One frame set per (period, object), numbered in a fixed order (period, ball last, team, object).
    keys = pd.DataFrame({"half": period, "is_ball": is_ball, "team_id": team, "object_id": obj})
    fsets = keys.drop_duplicates().sort_values(["half", "is_ball", "team_id", "object_id"], kind="mergesort").reset_index(drop=True)
    fsets["frameset_idx"] = np.arange(len(fsets), dtype=np.int32)
    fsets["game_section"] = fsets["half"].map(SECTIONS).fillna("unknown")
    idx = keys.merge(fsets, on=["half", "is_ball", "team_id", "object_id"], how="left")["frameset_idx"].to_numpy(np.int32)

    def opt(col):
        return df[col].astype(float).to_numpy() if col in df.columns else np.full(len(df), np.nan)

    status = df["ball_status"].astype(str).str.lower().map({"live": 1.0, "dead": 0.0}) if "ball_status" in df else pd.Series(np.nan, index=df.index)
    poss = df["ball_possession"].astype(str).str.lower().map({"home": 1.0, "away": 2.0}) if "ball_possession" in df else pd.Series(np.nan, index=df.index)
    t = pd.to_datetime(df["time_utc"], utc=True)
    frames = pd.DataFrame({
        "frameset_idx": idx,
        "half": period.astype(np.int8),
        "object_id": pd.Categorical(obj),
        "team_id": pd.Categorical(team),
        "is_ball": is_ball,
        "n": df["frame"].astype(np.int64).to_numpy(),
        "t_utc": t,
        "x": df["x_m"].astype(float).to_numpy(),
        "y": df["y_m"].astype(float).to_numpy(),
        "z": opt("z_m"),
        "d_m": opt("provider_step_m"),
        "s_ms": opt("provider_speed_ms"),
        "a_ms2": opt("provider_accel_ms2"),
        "minute": np.full(len(df), np.nan),
        "ball_possession": poss.astype(float).to_numpy(),
        "ball_status": status.astype(float).to_numpy(),
    })
    pitch = (float(info.pitch_x_m), float(info.pitch_y_m)) if info.pitch_x_m and info.pitch_y_m else None
    framesets = fsets[["frameset_idx", "game_section", "half", "team_id", "object_id", "is_ball"]].copy()
    framesets["half"] = framesets["half"].astype(int)
    return PositionData(frames=frames, framesets=framesets, pitch_size_meta=pitch, timezone_offsets=["+00:00"])


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass
class AdapterLog:
    lines: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.lines.append(msg)


def _tz_suffix(s: str) -> str:
    if not isinstance(s, str):
        return "unknown"
    if s.endswith("Z"):
        return "+00:00"
    tail = s[-6:]
    return tail if len(tail) == 6 and tail[0] in "+-" and tail[3] == ":" else "unknown"


def goalkeeper_sides(pos: PositionData, info: MatchInfo) -> dict[int, dict]:
    """Per period: which team defends the goal at negative x (``left``), from the goalkeepers' mean x.

    Falls back to the team's mean x when no goalkeeper is tracked. Discovered on the tracking, never assumed.
    """
    f = pos.frames
    lu = info.lineup
    gks = set(lu.loc[lu["playing_position"].isin(["GK", "TW"]), "person_id"])
    out = {}
    players = f.loc[~f["is_ball"] & f["team_id"].astype(str).isin([info.home_team_id, info.away_team_id])]
    for h, g in players.groupby("half", sort=True):
        g_gk = g.loc[g["object_id"].astype(str).isin(gks)]
        src = g_gk if g_gk["team_id"].astype(str).nunique() == 2 else g
        mean_x = src.groupby(src["team_id"].astype(str), observed=True)["x"].mean()
        if len(mean_x) < 2:
            continue
        left = str(mean_x.idxmin())
        right = info.away_team_id if left == info.home_team_id else info.home_team_id
        out[int(h)] = {"left": left, "right": right, "basis": "goalkeeper mean x" if src is g_gk else "team mean x"}
    return out


def load_synthetic_events(path: Path, info: MatchInfo, pos: PositionData | None = None, log: AdapterLog | None = None) -> EventData:
    log = log if log is not None else AdapterLog()
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    team_of_side = {"home": info.home_team_id, "away": info.away_team_id}
    pos_of = dict(zip(info.lineup["person_id"], info.lineup["playing_position"]))
    sides = goalkeeper_sides(pos, info) if pos is not None else {}
    n_filled: dict[str, int] = {}

    def fill(attrs: dict, key: str, value) -> None:
        if value is not None and attrs.get(key) in (None, ""):
            attrs[key] = value
            n_filled[key] = n_filled.get(key, 0) + 1

    rows = []
    for order, e in enumerate(doc.get("events", [])):
        etype = e.get("event_type")
        attrs = {k: v for k, v in (e.get("attributes") or {}).items()}
        for k, v in list(attrs.items()):  # team-valued attributes given as sides -> team IDs
            if (k.endswith("Team") or k in ("TeamLeft", "TeamRight", "TeamFouler", "TeamFouled")) and isinstance(v, str) and v.lower() in team_of_side:
                attrs[k] = team_of_side[v.lower()]
        side = str(e.get("team_side") or "").lower()
        team = team_of_side.get(side)
        player = e.get("player_id")
        period = int(e["period"]) if e.get("period") is not None else None
        if etype not in ("KickOff", "FinalWhistle"):
            fill(attrs, "Team", team)
        fill(attrs, "Player", player)
        if etype in ("KickOff", "FinalWhistle") and period is not None:
            fill(attrs, "GameSection", SECTIONS.get(period))
        if etype == "KickOff" and period in sides and (not attrs.get("TeamLeft") or not attrs.get("TeamRight")):
            fill(attrs, "TeamLeft", sides[period]["left"])
            fill(attrs, "TeamRight", sides[period]["right"])
        if etype == "TacklingGame":
            fill(attrs, "Winner", player)
            fill(attrs, "WinnerTeam", team)
        if etype == "Substitution":
            fill(attrs, "PlayingPosition", pos_of.get(attrs.get("PlayerIn")))
        x, y = e.get("x_m"), e.get("y_m")
        tf = e.get("true_frame")
        rows.append({
            "file_order": order,
            "event_id": str(e.get("event_id")),
            "event_type": etype,
            "sub_types": e.get("sub_type") or "",
            "time_raw": e.get("time_utc"),
            "x": float(x) if x is not None else np.nan,
            "y": float(y) if y is not None else np.nan,
            "team_id": attrs.get("Team"),
            "player_id": attrs.get("Player"),
            "attrs_json": json.dumps(attrs, sort_keys=True, ensure_ascii=False),
            # Generator truth, like DFL CalculatedFrame: provider comparison (tests) only, never used to locate anything.
            "provider_calculated_frame": float(tf) if tf is not None else np.nan,
        })
    df = pd.DataFrame(rows, columns=["file_order", "event_id", "event_type", "sub_types", "time_raw", "x", "y", "team_id", "player_id",
                                     "attrs_json", "provider_calculated_frame"])
    if df["time_raw"].isna().any():
        raise UnusableDataError("stop condition (section 5.4): events without a timestamp")
    n_raw = len(df)
    is_delete = df["event_type"] == DELETE_EVENT
    n_delete = int(is_delete.sum())
    df = df.loc[~is_delete].copy()
    offsets = sorted({_tz_suffix(t) for t in df["time_raw"]})
    df["time_utc"] = pd.to_datetime(df["time_raw"], format="ISO8601", utc=True)
    t_ns = df["time_utc"].astype("int64").to_numpy()
    n_inv = int(np.sum(np.diff(t_ns) < 0))
    df = df.sort_values(["time_utc", "file_order"], kind="mergesort").reset_index(drop=True)
    for k, n in sorted(n_filled.items()):
        log.add(f"attribute {k} filled on {n} events (missing in events.json)")
    for h, s in sorted(sides.items()):
        if n_filled.get("TeamLeft"):
            log.add(f"P{h}: kickoff TeamLeft/TeamRight inferred from the {s['basis']}: left = {'home' if s['left'] == info.home_team_id else 'away'}")
    meta = {k: doc.get(k) for k in ("match_id", "universe_id", "generator_version", "seed")}
    log.add(f"events.json: {meta}")
    return EventData(events=df, n_raw=n_raw, n_delete=n_delete, n_out_of_order_in_file=n_inv, timezone_offsets=offsets)


def load_synthetic_match(folder: Path, match_id: str | None = None) -> tuple[SyntheticFiles, MatchInfo, EventData, PositionData, AdapterLog]:
    sf = find_synthetic_files(folder, match_id)
    info = load_synthetic_matchinfo(sf.match_info)
    pos = load_synthetic_positions(sf.positions, info)
    log = AdapterLog()
    ev = load_synthetic_events(sf.events, info, pos, log)
    return sf, info, ev, pos, log
