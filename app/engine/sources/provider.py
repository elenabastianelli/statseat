"""Tables of the data pipeline -> the engine's canonical `MatchData`, shared by `dfl:<id>` and `synthetic:<id>`.

Both sources are read like any provider (guidelines v3): the pipeline loaders return the same tables (`io_dfl`,
`io_synthetic`), and the committed match profile `reports/qc/<id>/match_profile.json` gives the canonical transform,
the kick-off of each period, the end of the first half and the event clock offset (events are annotated about 1 s
late, like a human operator). Only this module maps DFL event attributes to the engine's English events:
- shots get `outcome` from the DFL sub-type (OtherShot counts as wide; a shot without a result has no `outcome` and
  the statistics count it as off target); passes (`Play`) get `outcome` complete/incomplete from `Evaluation` and the
  receiver from `Recipient`; fouls use the fouler's team, duels the winner's team, cards `card`
  yellow/second_yellow/red, substitutions player out and player in;
- set pieces nest their action as in DFL (`FreeKick` > `ShotAtGoal` > `SavedShot`): a set piece with a shot also
  yields a `ShotAtGoal` event (id `<event_id>-shot`), so a free-kick goal counts in the score; a `Penalty` is a set
  piece too and its shot carries `penalty: true` (contract v0.2.5 counts penalties per team); a set piece with a pass
  carries the pass `outcome` and receiver; a header (`TypeOfShot` head) carries `header: true` (v0.3.0);
- a clearance (the pipeline's `events_sync.event_kind`: `OtherBallAction` with `DefensiveClearance`) carries
  `clearance: true` (the ball circulation leaves out the 2 s after it, Materials/METRICS.md section 6, E-4);
- a duel (`TacklingGame`) carries `winner_role`: `without_ball` when the defending player won it (DFL
  `withoutBallControl`, our generator `withoutBall`), `with_ball` otherwise: a tackle won is a duel won by the
  defending player (Materials/METRICS.md section 21, E-4);
- every other attribute is dropped, including the provider's `xG` (the engine shows only numbers it computes) and the
  `FinalResult` of `FinalWhistle` (the final score, i.e. the future).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..pipeline import DEFAULT_CONFIG, event_kind, map_position
from .base import AWAY, HOME, Event, LineupEntry, MatchData, SourceNotAvailable, periods_from_tidy

SHOT_OUTCOME = {"SuccessfulShot": "goal", "SavedShot": "saved", "ShotWide": "wide", "BlockedShot": "blocked",
                "ShotWoodWork": "woodwork", "OtherShot": "wide"}
CARD = {"yellow": "yellow", "yellowRed": "second_yellow", "red": "red"}
SET_PIECES = {"KickOff", "FreeKick", "CornerKick", "ThrowIn", "GoalKick", "Penalty"}


def read_profile(root: Path, match_id: str, how_to_build: str) -> dict:
    path = root / "reports" / "qc" / match_id / "match_profile.json"
    if not path.is_file():
        raise SourceNotAvailable(f"{match_id}: match profile {path} missing; run the data pipeline once first "
                                 f"({how_to_build})")
    return json.loads(path.read_text(encoding="utf-8"))


def _side(team: str | None, home: str, away: str) -> str | None:
    return HOME if team == home else AWAY if team == away else None


def _pass_outcome(a: dict) -> str:
    return "complete" if a.get("Evaluation") in ("successfullyCompleted", "successful") else "incomplete"


def _events(r, home: str, away: str, t: float, period: int, ex: tuple[float, float], sign: float) -> list[Event]:
    """One pipeline event row -> one engine event, or two for a set piece with a shot."""
    a = json.loads(r.attrs_json)
    typ = r.event_type
    sub = [s for s in (r.sub_types or "").split("/") if s]
    team, player, second = a.get("Team"), r.player_id, None
    attrs: dict = {}
    shot_outcome = None
    if typ == "ShotAtGoal":
        attrs["outcome"] = SHOT_OUTCOME.get(sub[0], "wide") if sub else "wide"
        if a.get("TypeOfShot") == "head":
            attrs["header"] = True  # headers stay out of the shot speed (Materials/METRICS.md section 5)
        second = a.get("AssistShotAtGoal")
    elif typ == "Play":
        attrs["outcome"] = _pass_outcome(a)
        second = a.get("Recipient")
    elif typ in SET_PIECES and sub[:1] == ["Play"]:
        attrs["outcome"] = _pass_outcome(a)
        second = a.get("Recipient")
    elif typ in SET_PIECES and sub[:1] == ["ShotAtGoal"]:
        shot_outcome = SHOT_OUTCOME.get(sub[1]) if len(sub) > 1 else None
    elif typ == "Foul":
        team, player, second = a.get("TeamFouler"), a.get("Fouler"), a.get("Fouled")
    elif typ == "TacklingGame":
        team, player, second = a.get("WinnerTeam"), a.get("Winner"), a.get("Loser")
        if a.get("WinnerRole"):
            attrs["winner_role"] = "without_ball" if str(a["WinnerRole"]).lower().startswith("without") else "with_ball"
    elif typ == "Caution":
        attrs["card"] = CARD.get(a.get("CardColor"), "yellow")
    elif typ == "Substitution":
        player, second = a.get("PlayerOut"), a.get("PlayerIn")
    elif event_kind(typ, a, DEFAULT_CONFIG) == "clearance":
        attrs["clearance"] = True
    x = y = None
    if pd.notna(r.x) and pd.notna(r.y):
        x, y = sign * (float(r.x) - ex[0]), sign * (float(r.y) - ex[1])
    player = None if pd.isna(player) else player
    side = _side(team, home, away)
    out = [Event(event_id=str(r.event_id), type=typ, period=period, t=t, team_side=side, player_id=player,
                 secondary_player_id=second, x=x, y=y, attrs=attrs)]
    if typ in SET_PIECES and sub[:1] == ["ShotAtGoal"]:
        out.append(Event(event_id=f"{r.event_id}-shot", type="ShotAtGoal", period=period, t=t, team_side=side,
                         player_id=player, secondary_player_id=None, x=x, y=y,
                         attrs={**({"outcome": shot_outcome} if shot_outcome else {}),
                                **({"penalty": True} if typ == "Penalty" else {})}))
    return out


def match_from_pipeline(source: str, info, events: pd.DataFrame, frames: pd.DataFrame, profile: dict,
                        notes: tuple[str, ...], match_id: str = "SYN-001") -> MatchData:
    """`info`: pipeline `MatchInfo`; `events`: pipeline event table (Delete already dropped); `frames`: long tidy
    tracking, sorted and de-duplicated; `profile`: the committed match profile; `match_id`: the payload match id
    (a DFL match is served as SYN-001, the only match of the contract)."""
    home, away = info.home_team_id, info.away_team_id
    sign = 1.0 if profile["direction_per_half"]["1"]["home_attacks_positive_x"] else -1.0
    centre = tuple(profile["centre_spot_in_tracking"])
    ex = tuple(profile["centre_spot_in_events"])
    ko = {int(h): pd.Timestamp(v["kickoff_utc"]).timestamp() for h, v in profile["kickoff_per_period"].items()}
    off = {int(h): v["event_clock_offset_s"] for h, v in profile["event_clock_offset"].items()}

    lineup = []
    for r in info.lineup.itertuples(index=False):
        side = HOME if r.team_role == "home" else AWAY
        role = map_position(r.playing_position)["role"]
        lineup.append(LineupEntry(r.person_id, side, None if role.startswith("UNMAPPED") else role, bool(r.starting),
                                  None if pd.isna(r.shirt_number) else int(r.shirt_number)))
    lineup = tuple(lineup)

    p1_end = pd.Timestamp(profile["frame_ranges"]["per_half"]["1"]["t_last_utc"]).timestamp()
    out = []
    for r in events.itertuples(index=False):
        s = r.time_utc.timestamp()
        period = 2 if 2 in ko and s > p1_end + 1.0 else 1  # half-time substitutions belong to period 2
        t = s + off.get(period, 0.0) - ko[1]
        out.extend(_events(r, home, away, t, period, ex, sign))
    out.sort(key=lambda e: (e.t, e.event_id))

    fs = {int(h): float(v) for h, v in profile["fs_hz"].items()}
    periods = periods_from_tidy(frames, lineup, {home: HOME, away: AWAY}, fs, ko[1], sign, centre)
    return MatchData(
        source=source, match_id=match_id, pitch_length_m=float(profile["pitch_length_m"]),
        pitch_width_m=float(profile["pitch_width_m"]), lineup=lineup, events=tuple(out), frames=periods,
        full_event_stream=True,
        notes=notes + ("event times corrected by the event clock offset of the committed match profile",
                       "provider xG and FinalWhistle FinalResult dropped (numbers not computed by the engine; "
                       "the final score)"))
