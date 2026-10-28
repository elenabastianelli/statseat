"""The match script: the key moments that the generator must make happen.

A script fixes the story (goals, cards, substitutions, the woodwork, the VAR check, the save,
the most intense spell), the stoppage time of each period and a "who is pushing" profile per
5 minutes. Between the moments, the possession chains come from the Markov model.

Clocks are match clocks "MM:SS" (period 2 starts at 45:00); every moment also states its
period, so stoppage time of the first half ("45:40" in period 1) is unambiguous.

`validate_script` returns the list of problems (empty when the script is consistent);
`load_script` raises `ScriptError` when there is any.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .pitch import clock_minute, parse_clock, period_start_clock

MomentType = Literal["nutmeg", "goal", "caution", "woodwork", "intense_spell", "var_penalty_check", "substitution",
                     "save", "penalty"]
PenaltyOutcome = Literal["goal", "saved", "wide"]
Side = Literal["home", "away"]
MAX_SUBS = 5
MAX_WINDOWS = 3  # substitution opportunities per team, half-time excluded (IFAB)
MIN_GAP_S = 20.0


class ScriptError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid match script:\n- " + "\n- ".join(problems))
        self.problems = problems


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Score(Strict):
    home: int = Field(ge=0)
    away: int = Field(ge=0)


class Kickoff(Strict):
    period1_team: Side
    delay_s: float = Field(ge=0, le=300, description="Real kick-off after the planned time.")
    half_time_break_s: float = Field(ge=60, le=1800)


class PeriodPlan(Strict):
    period: Literal[1, 2]
    end_clock: str = Field(description="Match clock of the final whistle of the period (not before).")
    stoppage_announced_min: int = Field(ge=0, le=15)


class Momentum(Strict):
    bin_minutes: int = 5
    home_push: list[float] = Field(description="Who is pushing per bin of match clock: +1 home, -1 away.")
    intensity: list[float]
    stoppage_home_push: dict[str, float] = Field(default_factory=dict, description="Per period ('1', '2').")
    stoppage_intensity: dict[str, float] = Field(default_factory=dict)


class Moment(Strict):
    id: str
    type: MomentType
    period: Literal[1, 2]
    clock: str
    minute: str | None = Field(default=None, description="Displayed minute, checked against the clock.")
    team: Side | None = None
    player_id: str | None = None
    assist_id: str | None = None
    interceptor_id: str | None = None
    opponent_id: str | None = None
    goalkeeper_id: str | None = None
    player_out: str | None = None
    player_in: str | None = None
    build_up: Literal["counter_attack", "corner", "long_range", "open_play"] | None = None
    body_part: Literal["rightLeg", "leftLeg", "head"] | None = None
    card: Literal["yellow"] | None = None
    score_after: str | None = None
    until_clock: str | None = None
    intensity: float | None = None
    sprint_player_id: str | None = None
    sprint_clock: str | None = None
    decision: Literal["no_penalty"] | None = None
    fouled_id: str | None = Field(default=None, description="penalty: the attacker fouled in the box.")
    penalty_outcome: PenaltyOutcome | None = Field(default=None, description="penalty: goal, saved or wide.")
    shot_speed_ms: float | None = Field(default=None, ge=10, le=40)
    description: str = ""

    @property
    def clock_s(self) -> float:
        return parse_clock(self.clock)


class Script(Strict):
    match_id: str
    universe_id: str
    version: int = 1
    title: str = ""
    format: Literal["full", "test"] = "full"
    final_score: Score
    kickoff: Kickoff
    periods: list[PeriodPlan]
    momentum: Momentum
    moments: list[Moment]

    def period_end_s(self, period: int) -> float:
        return parse_clock(next(p.end_clock for p in self.periods if p.period == period))


def _lineup(match_info: dict) -> dict[str, dict]:
    out = {}
    for team in match_info["teams"]:
        side = "home" if team["role"] == "home" else "away"
        out[side] = {"starters": [e["player_id"] for e in team["lineup"] if e["starting"]],
                     "bench": [e["player_id"] for e in team["lineup"] if not e["starting"]],
                     "gk": [e["player_id"] for e in team["lineup"] if e["playing_position"] == "GK"],
                     "team_id": team["team_id"]}
    return out


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def validate_script(script: Script, match_info: dict) -> list[str]:
    problems: list[str] = []
    lu = _lineup(match_info)
    if script.match_id != match_info["match_id"] and not script.match_id.startswith(match_info["match_id"]):
        problems.append(f"match_id {script.match_id} does not match match_info {match_info['match_id']}")
    if script.universe_id != match_info.get("universe_id", script.universe_id):
        problems.append(f"universe_id {script.universe_id} does not match match_info {match_info.get('universe_id')}")

    # Periods.
    periods = {p.period: p for p in script.periods}
    if sorted(periods) != [1, 2]:
        problems.append("the script needs exactly periods 1 and 2")
        return problems
    ends = {k: parse_clock(p.end_clock) for k, p in periods.items()}
    for k, p in periods.items():
        start = period_start_clock(k)
        length = ends[k] - start
        if script.format == "full":
            if length < 45 * 60:
                problems.append(f"period {k} ends before 45 minutes ({p.end_clock})")
            elif length >= (45 + p.stoppage_announced_min + 1) * 60:
                problems.append(f"period {k} ends at {p.end_clock}, beyond the announced +{p.stoppage_announced_min}")
        elif length < 60:
            problems.append(f"period {k} is shorter than one minute")

    # Momentum profile.
    m = script.momentum
    n_bins_needed = 18 if script.format == "full" else max(1, int(max(ends[1], ends[2] - 45 * 60) // (60 * m.bin_minutes)) + 1)
    if len(m.home_push) < n_bins_needed or len(m.intensity) < n_bins_needed:
        problems.append(f"momentum needs {n_bins_needed} bins of {m.bin_minutes} minutes")
    if any(not -1.0 <= v <= 1.0 for v in list(m.home_push) + list(m.stoppage_home_push.values())):
        problems.append("momentum values must be between -1 and +1")
    if any(not 0.5 <= v <= 2.0 for v in list(m.intensity) + list(m.stoppage_intensity.values())):
        problems.append("intensity values must be between 0.5 and 2.0")

    # Moments: order, period bounds, displayed minute.
    keys = [(mo.period, mo.clock_s) for mo in script.moments]
    if keys != sorted(keys):
        problems.append("moments are not in time order")
    if len({mo.id for mo in script.moments}) != len(script.moments):
        problems.append("moment ids are not unique")
    last_t: dict[int, float] = {}
    for mo in script.moments:
        t = mo.clock_s
        start = period_start_clock(mo.period)
        if not start <= t < ends[mo.period]:
            problems.append(f"{mo.id}: clock {mo.clock} is outside period {mo.period}")
        if mo.minute is not None and mo.minute != clock_minute(t, mo.period):
            problems.append(f"{mo.id}: minute {mo.minute} does not match clock {mo.clock} ({clock_minute(t, mo.period)})")
        if mo.type not in ("substitution", "intense_spell"):
            prev = last_t.get(mo.period)
            if prev is not None and t - prev < MIN_GAP_S:
                problems.append(f"{mo.id}: less than {MIN_GAP_S:.0f} s after the previous moment")
            last_t[mo.period] = t

    # Who is on the pitch, moment by moment.
    on = {s: set(lu[s]["starters"]) for s in ("home", "away")}
    used_in: dict[str, set] = {"home": set(), "away": set()}
    gone: dict[str, set] = {"home": set(), "away": set()}
    sub_count = {"home": 0, "away": 0}
    windows: dict[str, set] = {"home": set(), "away": set()}
    yellow: set[str] = set()
    score = {"home": 0, "away": 0}

    def need_on(mo: Moment, pid: str | None, side: str | None, what: str):
        if pid is None:
            problems.append(f"{mo.id}: {what} is missing")
        elif side is None or pid not in on[side]:
            problems.append(f"{mo.id}: {what} {pid} is not on the pitch for the {side} team at {mo.clock}")

    for mo in script.moments:
        side = mo.team
        if mo.type != "intense_spell" and side is None:
            problems.append(f"{mo.id}: team is missing")
            continue
        if mo.type == "goal":
            need_on(mo, mo.player_id, side, "scorer")
            if mo.assist_id is not None:
                need_on(mo, mo.assist_id, side, "assist")
            if mo.interceptor_id is not None:
                need_on(mo, mo.interceptor_id, side, "interceptor")
            if mo.build_up is None:
                problems.append(f"{mo.id}: build_up is missing")
            if mo.build_up == "corner" and mo.assist_id is None:
                problems.append(f"{mo.id}: a corner goal needs the corner taker as assist_id")
            score[side] += 1
            if mo.score_after is not None and mo.score_after != f"{score['home']}-{score['away']}":
                problems.append(f"{mo.id}: score_after {mo.score_after} but the running score is "
                                f"{score['home']}-{score['away']}")
        elif mo.type == "caution":
            need_on(mo, mo.player_id, side, "booked player")
            if mo.opponent_id is not None:
                need_on(mo, mo.opponent_id, _other(side), "fouled player")
            if mo.player_id in yellow:
                problems.append(f"{mo.id}: second yellow card for {mo.player_id}; red cards are not generated in v0.1")
            yellow.add(mo.player_id)
        elif mo.type in ("nutmeg", "var_penalty_check"):
            need_on(mo, mo.player_id, side, "player")
            need_on(mo, mo.opponent_id, _other(side), "opponent")
        elif mo.type == "woodwork":
            need_on(mo, mo.player_id, side, "shooter")
        elif mo.type == "penalty":
            # the clock is the kick from the spot; the foul in the box comes about 95 s earlier
            need_on(mo, mo.player_id, side, "penalty taker")
            need_on(mo, mo.fouled_id, side, "fouled player")
            need_on(mo, mo.opponent_id, _other(side), "fouler")
            need_on(mo, mo.goalkeeper_id, _other(side), "goalkeeper")
            if mo.goalkeeper_id is not None and mo.goalkeeper_id not in lu[_other(side)]["gk"]:
                problems.append(f"{mo.id}: {mo.goalkeeper_id} is not a goalkeeper")
            if mo.penalty_outcome is None:
                problems.append(f"{mo.id}: penalty_outcome is missing")
            elif mo.penalty_outcome == "goal":
                score[side] += 1
                if mo.score_after is not None and mo.score_after != f"{score['home']}-{score['away']}":
                    problems.append(f"{mo.id}: score_after {mo.score_after} but the running score is "
                                    f"{score['home']}-{score['away']}")
        elif mo.type == "save":
            need_on(mo, mo.player_id, side, "shooter")
            need_on(mo, mo.goalkeeper_id, _other(side), "goalkeeper")
            if mo.goalkeeper_id is not None and mo.goalkeeper_id not in lu[_other(side)]["gk"]:
                problems.append(f"{mo.id}: {mo.goalkeeper_id} is not a goalkeeper")
        elif mo.type == "intense_spell":
            if mo.until_clock is None or parse_clock(mo.until_clock) <= mo.clock_s:
                problems.append(f"{mo.id}: until_clock must follow clock")
            if mo.sprint_player_id is not None:
                sp_side = "home" if mo.sprint_player_id in lu["home"]["starters"] + lu["home"]["bench"] else "away"
                need_on(mo, mo.sprint_player_id, sp_side, "sprinter")
        elif mo.type == "substitution":
            if mo.player_out is None or mo.player_in is None:
                problems.append(f"{mo.id}: player_out and player_in are required")
                continue
            if mo.player_out not in on[side]:
                problems.append(f"{mo.id}: {mo.player_out} is not on the pitch at {mo.clock}")
            if mo.player_in not in lu[side]["bench"]:
                problems.append(f"{mo.id}: {mo.player_in} is not on the {side} bench")
            if mo.player_in in used_in[side] or mo.player_in in gone[side]:
                problems.append(f"{mo.id}: {mo.player_in} has already been used")
            if (mo.player_in in lu[side]["gk"]) != (mo.player_out in lu[side]["gk"]):
                problems.append(f"{mo.id}: a goalkeeper can only be replaced by a goalkeeper")
            sub_count[side] += 1
            if not (mo.period == 2 and mo.clock_s == 45 * 60):
                windows[side].add((mo.period, mo.clock_s))
            if sub_count[side] > MAX_SUBS:
                problems.append(f"{mo.id}: more than {MAX_SUBS} substitutions for the {side} team")
            if len(windows[side]) > MAX_WINDOWS:
                problems.append(f"{mo.id}: more than {MAX_WINDOWS} substitution windows for the {side} team")
            on[side].discard(mo.player_out)
            gone[side].add(mo.player_out)
            on[side].add(mo.player_in)
            used_in[side].add(mo.player_in)

    if (score["home"], score["away"]) != (script.final_score.home, script.final_score.away):
        problems.append(f"final_score {script.final_score.home}-{script.final_score.away} does not match the goals "
                        f"{score['home']}-{score['away']}")
    return problems


def load_script(src: Path | str | dict, match_info: dict) -> Script:
    data = src if isinstance(src, dict) else json.loads(Path(src).read_text(encoding="utf-8"))
    script = Script.model_validate(data)
    problems = validate_script(script, match_info)
    if problems:
        raise ScriptError(problems)
    return script
