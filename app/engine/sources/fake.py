"""MATCH_SOURCE=fake: the fake key events of the contract (`app/contract/fake/events.json`). Events only, no frames.

- Line-up and pitch: `synthetic/SYN-001/match_info.json` (universe v1), known before kick-off.
- Feed time: first half t = match_time_s; second half t = (first-half final whistle) + HALF_TIME_BREAK_S +
  match_time_s, because the fake file has no wall-clock times.
- Coordinates: the fake feed is attack-normalised (the event's team attacks +x); they are turned back into the raw
  orientation (home attacks +x in period 1) so that every source looks the same inside the engine.
- The provider-style numbers in the fake qualifiers (`xg`, `speed_kmh`) and the display field `added_time` are
  dropped: the engine computes its own numbers, or says that it cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..pipeline import ROOT
from .base import AWAY, HOME, Event, LineupEntry, MatchData, team_side_of

FAKE_EVENTS = ROOT / "app" / "contract" / "fake" / "events.json"
MATCH_INFO = ROOT / "synthetic" / "SYN-001" / "match_info.json"
HALF_TIME_BREAK_S = 900.0
DROPPED_QUALIFIERS = ("xg", "speed_kmh", "added_time")


def attacks_plus_x(side: str | None, period: int) -> bool:
    """Raw orientation: home attacks +x in period 1 and -x in period 2."""
    return (side == HOME) == (period == 1)


def lineup_from_match_info(info: dict) -> tuple[LineupEntry, ...]:
    out = []
    for team in info["teams"]:
        side = HOME if team["role"] == "home" else AWAY
        for p in team["lineup"]:
            out.append(LineupEntry(p["player_id"], side, p.get("playing_position"), bool(p.get("starting")),
                                   p.get("shirt_number")))
    return tuple(out)


def load_fake(events_path: Path = FAKE_EVENTS, match_info_path: Path = MATCH_INFO) -> MatchData:
    info = json.loads(Path(match_info_path).read_text(encoding="utf-8"))
    raw = json.loads(Path(events_path).read_text(encoding="utf-8"))
    rows = raw["events"]
    fw1 = next((e["match_time_s"] for e in rows if e["type"] == "FinalWhistle" and e["period"] == 1), None)
    events = []
    for e in rows:
        if e["type"] == "Delete":
            continue
        p = int(e["period"])
        if p == 2 and fw1 is None:
            raise ValueError("fake events: second-half events without a first-half FinalWhistle")
        t = float(e["match_time_s"]) + (0.0 if p == 1 else fw1 + HALF_TIME_BREAK_S)
        side = team_side_of(e.get("team_id"))
        x, y = e.get("x"), e.get("y")
        if x is not None and y is not None and not attacks_plus_x(side, p):
            x, y = -x, -y
        attrs = {k: v for k, v in (e.get("qualifiers") or {}).items() if k not in DROPPED_QUALIFIERS}
        events.append(Event(event_id=e["event_id"], type=e["type"], period=p, t=t, team_side=side,
                            player_id=e.get("player_id"), secondary_player_id=e.get("secondary_player_id"),
                            x=x, y=y, attrs=attrs))
    events.sort(key=lambda ev: (ev.t, ev.event_id))
    env = info["environment"]
    return MatchData(
        source="fake", match_id=raw["match_id"], pitch_length_m=float(env["pitch_x_m"]),
        pitch_width_m=float(env["pitch_y_m"]), lineup=lineup_from_match_info(info), events=tuple(events), frames=None,
        full_event_stream=False,
        notes=("fake key events of app/contract/fake/events.json (not SYN-001); no tracking frames",
               f"second half placed {HALF_TIME_BREAK_S:.0f} s after the first-half final whistle",
               "provider-style qualifiers xg and speed_kmh dropped: the engine computes its own numbers"))
