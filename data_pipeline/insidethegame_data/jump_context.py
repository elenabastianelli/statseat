"""Provider events around unknown jumps (guidelines v4, ``R-JUMP-CONTEXT``, section 5.5).

For every ``unknown`` jump episode, the provider events (after ``R-DELETE``) whose annotated
time, corrected by the period's event clock offset, lies within +-``jump_context_s`` (2 s) of the
first jump frame are recorded: event ID, type, team and whether the jumping player is the
event's player. The report counts the unknown jumps in live play and in dead ball (ball status
at the first jump frame) and the event types found nearby. This is for finding the causes
later; it never changes the exclusion.
"""

from __future__ import annotations

import json
from collections import Counter

import pandas as pd

from .config import PrepConfig


def _involves(attrs: dict, player_id, object_id: str) -> bool:
    return str(player_id) == object_id or object_id in {str(v) for v in attrs.values()}


def events_near(events: pd.DataFrame, half: int, t_s: float, object_id: str, offset_s: float, side_of_team: dict, cfg: PrepConfig) -> list[dict]:
    """Provider events of period ``half`` within +-``jump_context_s`` of ``t_s`` (corrected annotated time)."""
    e = events.loc[events["half"] == half]
    t = e["time_utc"].map(lambda v: v.timestamp()).to_numpy(float) + offset_s
    sel = e.loc[abs(t - t_s) <= cfg.jump_context_s]
    out = []
    for (_, r), tt in zip(sel.iterrows(), t[abs(t - t_s) <= cfg.jump_context_s]):
        attrs = json.loads(r["attrs_json"]) if isinstance(r["attrs_json"], str) else {}
        team = attrs.get("Team") or attrs.get("WinnerTeam") or attrs.get("TeamFouler")
        out.append({"event_id": r["event_id"], "event_type": r["event_type"], "team_side": side_of_team.get(team), "dt_s": round(float(tt - t_s), 2),
                    "jumping_player_is_event_player": _involves(attrs, r.get("player_id"), object_id)})
    return out


def annotate_unknown_jumps(cat: pd.DataFrame, events: pd.DataFrame, offsets: dict, t_of: dict, side_of_team: dict, cfg: PrepConfig) -> tuple[pd.DataFrame, dict]:
    """Add ``nearby_events`` (JSON) to the unknown jump rows of the catalogue and return the report aggregates.

    ``t_of``: (half, frame_N) -> UTC seconds of the first jump frame."""
    cat = cat.copy()
    if cat.empty:
        cat["nearby_events"] = []
        return cat, {"unknown_jumps": 0}
    cat["nearby_events"] = None
    m = (cat["anomaly_type"] == "jump") & (cat["suspected_cause"] == "unknown")
    types, with_events, with_player = Counter(), 0, 0
    for i, r in cat.loc[m].iterrows():
        h = int(r["half"])
        near = events_near(events, h, t_of[(h, int(r["frame_N"]))], str(r["object_id"]),
                           float(offsets.get(h, {}).get("event_clock_offset_s", 0.0) or 0.0), side_of_team, cfg)
        cat.at[i, "nearby_events"] = json.dumps(near, sort_keys=True)
        types.update({e["event_type"] for e in near})
        with_events += bool(near)
        with_player += any(e["jumping_player_is_event_player"] for e in near)
    u = cat.loc[m]
    agg = {"unknown_jumps": int(m.sum()), "in_live_play": int((u["ball_status"] == "live").sum()), "in_dead_ball": int((u["ball_status"] == "dead").sum()),
           "with_events_within_window": int(with_events), "with_the_jumping_player_in_an_event": int(with_player),
           "event_types_nearby": {k: int(v) for k, v in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))},
           "window_s": cfg.jump_context_s}
    return cat, agg
