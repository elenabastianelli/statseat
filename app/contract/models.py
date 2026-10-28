"""Pydantic models of the statseat API contract (version in `app.contract.CONTRACT_VERSION`).

Rules that every implementation of these models must keep:
- `match_id` and the ticket tier never come from the client: the server derives them from the ticket token.
- Modes: `casual` and `analyst`; the app calls them Fan and Analyst (v0.3.0).
- Ticket tiers (`app/contract/tickets.py`): silver = Casual only; gold = Analyst with switch to Casual;
  demo = gold + Inspector + demo phase panel, on several devices (v0.2.2: the judges use demo tickets, there is no
  judge tier any more). One device per ticket except demo.
- Every `inspector` field is filled only for demo tickets; for silver and gold it is `null`.
- No look-ahead: every payload built from the match carries `as_of_s`, the playback time it was computed at;
  nothing in it may use frames or events after that instant.
- A metric the backend cannot compute (no tracking in the source, method still pending) is `null` (or an empty list)
  and listed in `unavailable` with the reason; it is never filled with a made-up number (v0.2.1).
- Numbers come from deterministic code; Archie only explains them (see `ArchieAnswer.grounding`).
- Metrics and their methods follow Materials/METRICS.md (decisions of 25 October, v0.3.0); highlight fields follow
  the VAR draft (v0).
- Coordinates: metres, origin at the centre spot (x in [-52.5, 52.5], y in [-34, 34]). Events, shots and zone maps
  are normalised so that the team of the item attacks towards +x.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """Base model: unknown fields are rejected, so fake data cannot drift from the contract."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- common


class ApiError(Model):
    code: str = Field(description="Machine-readable code, e.g. section_locked, invalid_ticket, ticket_in_use")
    message: str
    section: str | None = None
    unlocks_at_phase: str | None = None


class TicketTier(str, Enum):
    """silver = Casual only; gold = Analyst + Casual; demo = gold + Inspector + phase panel (also for the judges)."""

    silver = "silver"
    gold = "gold"
    demo = "demo"


class Mode(str, Enum):
    casual = "casual"
    analyst = "analyst"


class Phase(str, Enum):
    pre_match = "pre_match"
    first_half = "first_half"
    half_time = "half_time"
    second_half = "second_half"
    full_time = "full_time"


class Section(str, Enum):
    pre_match = "pre_match"
    live = "live"
    data_stats = "data_stats"
    games = "games"
    recap = "recap"
    ask_archie = "ask_archie"


class TeamId(str, Enum):
    """Team ids of the match, as in synthetic/<match_id>/match_info.json."""

    home = "BRA"
    away = "CAL"


class TeamScore(Model):
    home: int = Field(ge=0)
    away: int = Field(ge=0)


AS_OF_DESCRIPTION = (
    "Playback time (seconds since kick-off, first-half added time included) up to which frames and events were "
    "used; nothing after this instant (no look-ahead, S-7)"
)


# ---------------------------------------------------------------- auth and preferences


class LoginRequest(Model):
    ticket_code: str = Field(examples=["GNT-SYN001-G-XXXX-XXXX"],
                             description="GNT-<MATCH>-<S|G|D>-XXXX-XXXX; case and spaces are ignored")
    device_id: str = Field(min_length=8, description="Random id generated once by the app and kept on the device")


MAX_FAVOURITES = 3


class Preferences(Model):
    """Mode, favourite players and language of the session. The favourites are at most 3 (v0.2.6): the fan confirms
    exactly 3 with `favourites_locked = true`, and after that the server refuses any change to them (409)."""

    mode: Mode = Mode.casual
    favourite_player_ids: list[str] = Field(default_factory=list, max_length=MAX_FAVOURITES,
                                            description="Player ids of this match, at most 3, no repeats")
    favourites_locked: bool = Field(default=False, description="True once the fan has confirmed exactly 3 favourites; "
                                                               "then they can no longer change (409 favourites_locked)")
    language: str = Field(default="en", description="BCP 47 tag; Archie answers in any language, UI in en/it")


class MatchHeader(Model):
    """What the app shows right after login. The match_id is informative only: the client never sends it back."""

    match_id: str
    home_team_id: TeamId
    away_team_id: TeamId
    home_name: str
    away_name: str
    title: str
    kickoff: datetime
    stadium_name: str


class Session(Model):
    tier: TicketTier = Field(description="From the ticket, server-side; the client cannot change it")
    allowed_modes: list[Mode] = Field(description="Modes this tier may use: silver [casual]; others [analyst, casual]")
    inspector_enabled: bool = Field(description="True only for demo tickets")
    demo_panel_enabled: bool = Field(description="True only for demo tickets (phase jumps)")
    multi_device: bool = Field(description="True for demo tickets; silver and gold work on one device")
    match: MatchHeader
    preferences: Preferences = Field(description="preferences.mode is always one of allowed_modes")


# ---------------------------------------------------------------- My ticket


class TicketSeat(Model):
    """Seat from the stadium of synthetic/universe (stands, sectors, gates). No personal data."""

    stand_id: str
    stand_name: str
    sector_id: str
    sector_type: str = Field(description="home, away, family, accessible, club_level")
    gate_id: str | None = None
    row: str
    seat: str


class TicketFeatures(Model):
    """What the tier unlocks (the same rules the server enforces)."""

    allowed_modes: list[Mode]
    default_mode: Mode
    inspector: bool
    demo_panel: bool
    multi_device: bool
    perks: list[str] = Field(description="Short English lines for the My ticket page")


class TicketImage(Model):
    format: Literal["svg", "pdf", "png"]
    url: str = Field(description="API path that serves the image of this session's ticket")
    path: str = Field(description="Source file in the repo, e.g. synthetic/SYN-001/tickets/<ticket_id>.svg")
    available: bool = Field(description="False until the ticket generator (S-4) has written the file")


class TicketInfo(Model):
    """The My ticket page. Holder-agnostic: no name, e-mail or other personal data; never the code itself."""

    ticket_id: str = Field(description="Public id printed on the ticket, e.g. TKT-SYN001-G-0001 (not the code)")
    tier: TicketTier
    match: MatchHeader
    seat: TicketSeat | None = Field(description="null for passes without a seat (demo)")
    valid_from: datetime
    valid_until: datetime
    valid_now: bool
    features: TicketFeatures
    images: list[TicketImage]


class LoginResponse(Model):
    token: str = Field(description="Opaque bearer token; the server maps it to ticket, device and match_id")
    token_type: Literal["bearer"] = "bearer"
    session: Session


# ---------------------------------------------------------------- match overview (Pre-match)


class LineupPlayer(Model):
    player_id: str
    name: str
    short_name: str
    shirt_number: int
    position: str = Field(description="English position code, e.g. GK, RB, LDM, RS")
    starting: bool
    captain: bool


class KitColours(Model):
    shirt: str
    shorts: str
    socks: str


class TeamColours(Model):
    """Club colours (home kit). The app picks the team's mark from these and switches to `away_kit` when the two
    teams' colours are too similar (v0.2.4)."""

    primary: str
    secondary: str
    number: str
    away_kit: KitColours | None = Field(default=None, description="Second kit, from the universe (v0.2.4)")


class TeamSheet(Model):
    team_id: TeamId
    role: Literal["home", "away"]
    name: str
    short_name: str
    nickname: str
    colours: TeamColours
    formation: str
    head_coach: str
    lineup: list[LineupPlayer]


class OfficialEntry(Model):
    role: str = Field(description="snake_case role, e.g. referee, video_assistant_referee")
    name: str


class MatchOverview(Model):
    match_id: str
    competition: str
    season: str
    matchday: int
    title: str
    kickoff: datetime
    stadium_name: str
    stadium_capacity: int
    teams: list[TeamSheet]
    officials: list[OfficialEntry]


# ---------------------------------------------------------------- match state


class SectionState(Model):
    section: Section
    unlocked: bool
    unlocks_at_phase: Phase | None = Field(default=None, description="Next phase in which it opens, if locked")


class MatchState(Model):
    match_id: str
    phase: Phase
    minute: int = Field(ge=0, le=130, description="Match minute shown on the scoreboard (0 before kick-off)")
    added_time: int = Field(default=0, ge=0, description="Added minutes being played, e.g. 2 for 45+2")
    clock_running: bool
    score: TeamScore
    sections: list[SectionState]
    as_of_s: float = Field(ge=0, description=AS_OF_DESCRIPTION)
    server_time: datetime


# ---------------------------------------------------------------- events (key events feed)


class EventType(str, Enum):
    """DFL taxonomy in English, without `Delete` (a post-processing placeholder, never served)."""

    kick_off = "KickOff"
    shot_at_goal = "ShotAtGoal"
    caution = "Caution"
    substitution = "Substitution"
    corner_kick = "CornerKick"
    foul = "Foul"
    video_assistant_action = "VideoAssistantAction"
    final_whistle = "FinalWhistle"


class MatchEvent(Model):
    event_id: str
    type: EventType
    period: Literal[1, 2]
    minute: int = Field(ge=0, le=130)
    match_time_s: float = Field(ge=0, description="Seconds since the start of the period")
    team_id: TeamId | None = None
    player_id: str | None = None
    secondary_player_id: str | None = Field(default=None, description="Assist, player coming on, fouled player")
    x: float | None = None
    y: float | None = None
    qualifiers: dict[str, str | float | bool] = Field(
        default_factory=dict, description="e.g. outcome=goal|saved|wide|blocked, card=yellow, decision=penalty"
    )


class EventFeed(Model):
    match_id: str
    as_of_s: float = Field(ge=0, description=AS_OF_DESCRIPTION)
    events: list[MatchEvent] = Field(description="Only events whose frames have already been processed")


# ---------------------------------------------------------------- statistics (METRICHE.md)


class DataScope(str, Enum):
    live = "live"
    first_half = "first_half"
    full_match = "full_match"


class ReliabilityLevel(str, Enum):
    """Symbol next to a tracking-based number (v0.3.0): high ●, medium ◐, low ○."""

    high = "high"
    medium = "medium"
    low = "low"


RELIABILITY_HIGH_PCT = 95.0
RELIABILITY_MEDIUM_PCT = 85.0


class Reliability(Model):
    """Share of the time actually measured by the tracking (gaps not counted) and its symbol (v0.3.0): high at least
    95 %, medium 85-95 %, low below 85 %. The app shows only the symbol; the Inspector explains it."""

    measured_pct: float = Field(ge=0, le=100)
    level: ReliabilityLevel

    @classmethod
    def of(cls, measured_pct: float) -> Reliability:
        pct = round(min(max(measured_pct, 0.0), 100.0), 1)
        level = (ReliabilityLevel.high if pct >= RELIABILITY_HIGH_PCT else
                 ReliabilityLevel.medium if pct >= RELIABILITY_MEDIUM_PCT else ReliabilityLevel.low)
        return cls(measured_pct=pct, level=level)


class TeamValue(Model):
    team_id: TeamId
    value: float


class TeamMeasure(Model):
    """A per-team value with its reliability symbol (tracking-based metrics, v0.3.0)."""

    team_id: TeamId
    value: float
    reliability: Reliability | None = None


class PlayerValue(Model):
    player_id: str
    value: float


class TimePoint(Model):
    minute: int
    value: float


class TeamSeries(Model):
    """One team's series over time: the home team is drawn above the axis and the away team below (mirror chart)."""

    team_id: TeamId
    points: list[TimePoint]


class Distance(Model):
    km: float = Field(ge=0)
    approx: bool = Field(description="True when part of the time is not measured: show the value with '≈'")
    unmeasured_pct: float = Field(ge=0, le=100)
    reliability: Reliability = Field(description="Symbol from the measured share, 100 - unmeasured_pct (v0.3.0)")


class DistanceSplits(Model):
    ball_in_play_km: float
    ball_stopped_km: float
    in_possession_km: float
    out_of_possession_km: float


class PlayerDistance(Model):
    player_id: str
    distance: Distance
    splits: DistanceSplits | None = Field(default=None, description="Analyst only")


class TeamDistance(Model):
    team_id: TeamId
    distance: Distance = Field(description="Outfield players only (goalkeepers appear as players)")
    splits: DistanceSplits | None = Field(default=None, description="Analyst only")


class DistanceCovered(Model):
    """Distance covered (METRICS.md §1): a table since v0.3.0, not a chart."""

    teams: list[TeamDistance]
    top_runner: PlayerDistance
    players: list[PlayerDistance] | None = Field(default=None, description="Analyst only")


class SpeedZones(Model):
    """Distance per FIFA speed zone (0-7, 7-15, 15-20, 20-25, 25+ km/h). Analyst only."""

    team_id: TeamId
    player_id: str | None = Field(default=None, description="null = the team; set for a favourite player (v0.3.0)")
    km_0_7: float
    km_7_15: float
    km_15_20: float
    km_20_25: float
    km_over_25: float


class TopSpeed(Model):
    """Fan: the fastest player of each team; Analyst: up to 5 per team (v0.3.0)."""

    player_id: str
    team_id: TeamId
    kmh: float = Field(gt=0, le=38)
    minute: int


class TeamPeak(Model):
    """A team's most intense 5 minutes: mean metres per minute of its outfield players on the pitch."""

    team_id: TeamId
    start_minute: int
    end_minute: int
    metres_per_minute: float


class PlayerPeak(Model):
    player_id: str
    team_id: TeamId
    start_minute: int
    end_minute: int
    metres_per_minute: float


class PeakIntensity(Model):
    """Peak intensity (METRICS.md §4, v0.3.0): rolling 5-minute window, step 1 s, metres per minute per player; a team
    is the mean of its outfield players on the pitch. `series` feeds the mirror chart, one point per minute."""

    teams: list[TeamPeak]
    series: list[TeamSeries] | None = Field(default=None, description="One point per minute and team (mirror chart)")
    players: list[PlayerPeak] | None = Field(default=None, description="Analyst only: each player's peak")
    chart_id: str = "peak_intensity"


class ShotSpeed(Model):
    """The hardest shot of a team (headers excluded, v0.3.0)."""

    player_id: str
    team_id: TeamId
    kmh: float = Field(gt=0, le=211)
    minute: int
    event_id: str


class BallCirculation(Model):
    """Ball circulation speed in possession, in km/h since v0.3.0, per team, without the 2 s after shots and
    clearances (METRICS.md §6)."""

    teams: list[TeamMeasure]
    unit: Literal["km/h"] = "km/h"
    series: list[TeamSeries] | None = Field(default=None, description="Analyst only: 5-minute blocks (mirror chart)")
    chart_id: str = "ball_circulation"


class Possession(Model):
    """Share of the live-ball time with the ball: one bar, the same in both versions (v0.3.0: no 5-minute series)."""

    teams: list[TeamValue] = Field(description="Percent, sums to 100")


class TeamShots(Model):
    """Penalties count in shots, shots on target and goals; on target = goals + saves (METRICS.md §8)."""

    team_id: TeamId
    shots: int = Field(ge=0)
    on_target: int = Field(ge=0)
    goals: int = Field(ge=0)


class ExpectedGoals(Model):
    """xG per team and cumulative xG (step line), METRICS.md §9; penalties count 0.79. Analyst only."""

    teams: list[TeamValue]
    cumulative: list[TeamSeries]
    method: str
    chart_id: str = "xg_cumulative"


class GoalsVsXg(Model):
    """Goals next to the xG of the team's chances (METRICS.md §10, v0.3.0): numbers only, no judgement words."""

    team_id: TeamId
    goals: int = Field(ge=0)
    xg: float = Field(ge=0)


class TeamPasses(Model):
    team_id: TeamId
    passes: int
    accuracy_pct: float = Field(ge=0, le=100)
    avg_length_m: float | None = Field(default=None, description="Analyst only: successful passes")
    long_balls: int | None = Field(default=None, description="Analyst only: successful passes of 32 m or more (v0.3.0)")


class Momentum(Model):
    """Who is pushing (METRICS.md §13): value in [-1, 1] over the last 5 minutes, positive = home team. `series`: one
    point per minute, a single signed value drawn as an area above or below the axis."""

    current: float = Field(ge=-1, le=1)
    series: list[TimePoint] | None = None
    chart_id: str = "momentum"


class Shot(Model):
    event_id: str
    team_id: TeamId
    player_id: str
    minute: int
    x: float
    y: float
    xg: float | None = Field(default=None, ge=0, le=1, description="Analyst only (dot size)")
    outcome: Literal["goal", "saved", "wide", "blocked", "woodwork"] = Field(
        description="Map symbols (v0.3.0): goal filled, woodwork ×, every other shot hollow")
    is_penalty: bool = False
    is_header: bool = False
    speed_kmh: float | None = Field(default=None, gt=0, le=211, description="Ball speed of the strike; null for headers")


class ShotMap(Model):
    shots: list[Shot]
    chart_id: str = "shot_map"


class ZoneMap(Model):
    """Heatmap of play zones: rows x cols cells over the pitch, share of time (%), attacking left to right. Analyst
    only."""

    team_id: TeamId
    player_id: str | None = Field(default=None, description="null = the team; set for a favourite player (v0.3.0)")
    rows: int
    cols: int
    cells_pct: list[list[float]]
    chart_id: str = "zone_map"


class DefensiveShape(Model):
    """Defensive line height and team length (m), 5-minute blocks (mirror chart). Analyst only."""

    team_id: TeamId
    defensive_line_m: list[TimePoint]
    team_length_m: list[TimePoint]
    chart_id: str = "defensive_shape"


class TeamCounts(Model):
    team_id: TeamId
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    penalties: int = Field(default=0, ge=0, description="Penalty kicks taken (v0.2.5)")
    woodwork: int = Field(default=0, ge=0, description="Shots off the post or the bar, never on target (v0.3.0)")
    offsides: int = Field(default=0, ge=0, description="Official offside decisions (v0.3.0)")
    sprints: int | None = Field(default=0, description="Sprints of the team; null when the source has no tracking")
    tackles: int | None = Field(default=None, description="Tackles won; null when the source has no duel events")
    ball_recoveries: int | None = Field(default=None, description="null when the source has no recovery events")


class TeamLoad(Model):
    team_id: TeamId
    total_kmh: float = Field(ge=0, description="Sum over the outfield players of the absolute changes of speed, km/h")
    per_minute_kmh: float = Field(ge=0, description="Per minute played")
    reliability: Reliability | None = None


class AccelerationLoad(Model):
    """Acceleration load (METRICS.md §22, v0.3.0): the sum of |dv/dt| dt, the total change of speed."""

    teams: list[TeamLoad]
    series: list[TeamSeries] | None = Field(default=None,
                                            description="Analyst only: km/h per minute, 5-minute blocks (mirror chart)")
    chart_id: str = "acceleration_load"


class TeamCount(Model):
    team_id: TeamId
    count: int = Field(ge=0)


class CounterAttack(Model):
    team_id: TeamId
    minute: int
    recovery_event_id: str | None = None
    shot_event_id: str


class CounterAttacks(Model):
    """Counter-attacks (METRICS.md §25, v0.3.0): ball won in the team's own half, then a shot within 15 s without
    losing it."""

    teams: list[TeamCount]
    items: list[CounterAttack] | None = Field(default=None, description="Analyst only, linked to the timeline")


class EffectivePlayingTime(Model):
    """Minutes with the ball in play (METRICS.md §12, v0.3.0), with the two references shown to the fan."""

    first_half_min: float | None = None
    second_half_min: float | None = None
    total_min: float = Field(ge=0)
    played_min: float = Field(ge=0, description="Minutes played so far, added time included")
    in_play_pct: float = Field(ge=0, le=100)
    reference_world_cup_2022_min: float = Field(default=59.0,
                                                description="Average at the 2022 World Cup (Associated Press, 30/11/2022)")
    reference_bundesliga_pct: float = Field(default=62.0,
                                            description="Share of time in play in the Bundesliga (Siegle & Lames 2012)")


class TeamLeaders(Model):
    """Who leads each team, for the Fan tables (v0.3.0)."""

    team_id: TeamId
    top_runner: PlayerValue | None = Field(default=None, description="km")
    top_sprinter: PlayerValue | None = Field(default=None, description="Sprints")
    top_passer: PlayerValue | None = Field(default=None, description="Passes")
    most_touches: list[PlayerValue] = Field(default_factory=list, max_length=3,
                                            description="The 3 players with the most touches (METRICS.md §24)")


class PlayerRow(Model):
    """One row of the Analyst table of all players (v0.3.0). null = not available for this source, or not computed
    yet (see `unavailable`)."""

    player_id: str
    team_id: TeamId
    minutes_played: int = Field(ge=0)
    distance: Distance | None = None
    top_speed_kmh: float | None = None
    sprints: int | None = None
    peak_m_per_min: float | None = None
    acceleration_load_kmh: float | None = None
    passes: int | None = None
    pass_accuracy_pct: float | None = None
    long_balls: int | None = None
    touches: int | None = None
    shots: int = 0
    goals: int = 0
    xg: float | None = None
    tackles: int | None = None
    ball_recoveries: int | None = None
    fouls_committed: int = 0
    fouls_won: int = 0


class PlayerFocus(Model):
    """Card of a favourite player (at most 3), the same in Data & Statistics, Recap and the PDF (v0.3.0). The fields
    marked 'Analyst only' are null in Casual."""

    player_id: str
    team_id: TeamId
    minutes_played: int = Field(ge=0)
    distance: Distance | None = Field(default=None, description="null when the source has no tracking")
    top_speed_kmh: float | None = None
    top_speed_minute: int | None = None
    sprints: int | None = None
    acceleration_load_kmh: float | None = None
    passes: int | None = Field(default=None, description="null when the source has no pass events")
    pass_accuracy_pct: float | None = None
    shots: int = 0
    goals: int = 0
    touches: int | None = None
    tackles: int | None = None
    ball_recoveries: int | None = None
    fouls_committed: int = 0
    fouls_won: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    highlight_ids: list[str] = Field(default_factory=list)
    xg: float | None = Field(default=None, description="Analyst only")
    speed_zones: SpeedZones | None = Field(default=None, description="Analyst only")
    zone_map: ZoneMap | None = Field(default=None, description="Analyst only")
    acceleration_load_series: list[TimePoint] | None = Field(default=None,
                                                             description="Analyst only: km/h per minute, 5-minute blocks")


class GoalMark(Model):
    """A goal on the time axis of the charts over time (a vertical line, v0.3.0)."""

    team_id: TeamId
    minute: int


class MetricMethod(Model):
    """Inspector 'How it's computed' entry for one metric."""

    metric: str
    method: str
    tool: str = Field(description="Deterministic function that produced the value")
    parameters: dict[str, str | float | int] = Field(default_factory=dict)
    data_excluded_pct: float = Field(ge=0, le=100)


class StatsInspector(Model):
    methods: list[MetricMethod]
    computed_at: datetime
    source: str = Field(description="MATCH_SOURCE used by the engine, e.g. fake, synthetic:SYN-001")


class MetricUnavailable(Model):
    """A metric that is null (or an empty list) in this payload, and why. The app shows the reason, never a number."""

    metric: str = Field(description="Field name in the payload, e.g. distance_covered, xg, momentum")
    reason: str = Field(description="Short English reason, e.g. 'needs tracking frames (source fake has events only)'")
    available_from_minute: int | None = Field(
        default=None, ge=0,
        description="Match minute from which the metric appears, e.g. 5 (v0.3.0): the app shows 'Available from 5''. "
                    "null = not for this source, or not computed yet: the app hides the card")


DATA_STATS_TABLES = ("match_table", "player_focus", "running_table", "ball_table", "defence_table", "players_table")
"""Ids of the Data & Statistics tables in display order (v0.3.0); Archie can point to them as to a chart."""

ANALYZER_TABLES = ("players_table",)
"""The tables of the Analyst version only: a Fan ticket shows them locked, like the charts of `locked_charts`."""


class ChartRef(Model):
    """A chart in Data & Statistics; Archie points to it instead of drawing."""

    chart_id: str
    title: str
    section: Section = Section.data_stats
    modes: list[Mode]


class LiveStats(Model):
    """Live section, only during play (v0.2.5): the key numbers per team next to the event timeline. Live shows
    goals, shots, shots on target, penalties, yellow and red cards; every other statistic is in Data & Statistics."""

    match_id: str
    phase: Phase
    minute: int
    as_of_s: float = Field(ge=0, description=AS_OF_DESCRIPTION)
    score: TeamScore
    shots: list[TeamShots]
    counts: list[TeamCounts]
    unavailable: list[MetricUnavailable] = Field(default_factory=list, description="Null metrics and why (v0.2.1)")


class MatchStats(Model):
    """Data & Statistics, from kick-off to the end (v0.2.5): `scope` is `live` during play (numbers up to `as_of_s`),
    `first_half` at half-time, `full_match` after the final whistle. Fields marked 'Analyst only' are null in Casual
    mode. v0.3.0 follows the metric decisions of Materials/METRICS.md; the app calls the two modes Fan (casual) and
    Analyst (analyst). The tables come first, in the order of `DATA_STATS_TABLES`, then the charts."""

    match_id: str
    mode: Mode
    scope: DataScope
    up_to_minute: int
    as_of_s: float = Field(ge=0, description=AS_OF_DESCRIPTION)
    score: TeamScore
    charts: list[ChartRef]
    locked_charts: list[ChartRef] = Field(
        default_factory=list,
        description="Charts of the other (Analyst) version, shown blurred as a preview in Casual (v0.2.5); empty in Analyst")
    goal_marks: list[GoalMark] = Field(default_factory=list, description="Goals so far, for the charts over time")
    # match table
    shots: list[TeamShots]
    counts: list[TeamCounts]
    possession: Possession | None = None
    goals_vs_xg: list[GoalsVsXg] = Field(default_factory=list, description="Empty until xG is available")
    effective_playing_time: EffectivePlayingTime | None = None
    # favourite players (Player Focus)
    player_focus: list[PlayerFocus] = Field(default_factory=list, description="The session's favourite players")
    # running table
    distance_covered: DistanceCovered | None = None
    top_speed: list[TopSpeed]
    peak_intensity: PeakIntensity | None = None
    acceleration_load: AccelerationLoad | None = None
    speed_zones: list[SpeedZones] | None = None
    # ball table
    passes: list[TeamPasses]
    ball_circulation: BallCirculation | None = None
    shot_speed: list[ShotSpeed] = Field(default_factory=list, description="The hardest shot of each team")
    counter_attacks: CounterAttacks | None = None
    # defence table
    ppda: list[TeamValue] | None = Field(default=None, description="Analyst only: passes per defensive action")
    # leaders and the table of all players
    leaders: list[TeamLeaders] = Field(default_factory=list)
    players: list[PlayerRow] | None = Field(default=None, description="Analyst only")
    # charts
    xg: ExpectedGoals | None = None
    momentum: Momentum | None = None
    shot_map: ShotMap
    zone_map: list[ZoneMap] | None = None
    defensive_shape: list[DefensiveShape] | None = None
    inspector: StatsInspector | None = None
    unavailable: list[MetricUnavailable] = Field(default_factory=list, description="Null metrics and why (v0.2.1)")


# ---------------------------------------------------------------- highlights (VAR.md, v0)


class HighlightType(str, Enum):
    goal = "goal"
    woodwork = "woodwork"
    save = "save"
    big_chance = "big_chance"
    var_review = "var_review"
    card = "card"
    spectacular_play = "spectacular_play"
    speed_peak = "speed_peak"
    other = "other"


class HighlightInspector(Model):
    importance_factors: dict[str, float] = Field(description="Deterministic contributions: type, minute, game_state, xg")
    candidate_rank: int
    labelled_by: str = Field(description="Agent that wrote label and why_it_mattered")
    model: str
    trace_id: str


class Highlight(Model):
    """Highlights v0: fields may change."""

    schema_version: Literal["v0"] = "v0"
    highlight_id: str
    type: HighlightType
    period: Literal[1, 2]
    minute: int
    team_id: TeamId | None
    player_ids: list[str]
    event_ids: list[str] = Field(description="Events of the feed this moment is built from")
    label: str = Field(description="Timeline label, e.g. \"23' – Goal by Audun Ulvestad (Brackwater Athletic)\"")
    why_it_mattered: str = Field(description="Explanation written by VAR from deterministic facts")
    importance: float = Field(ge=0, le=100, description="Deterministic importance score")
    score_after: TeamScore
    xg: float | None = None
    inspector: HighlightInspector | None = None


class HighlightList(Model):
    match_id: str
    as_of_s: float = Field(ge=0, description=AS_OF_DESCRIPTION)
    highlights: list[Highlight]


# ---------------------------------------------------------------- games


class GameId(str, Enum):
    tiki_taka_toe = "tiki_taka_toe"
    guess_the_player = "guess_the_player"
    quiz = "quiz"


class GameInfo(Model):
    game_id: GameId
    title: str
    available: bool
    unlocks_at_phase: Phase | None = None
    rules: str


class GamePrize(Model):
    """The match sponsor's prize for the winner of the half-time games (invented sponsor, universe `match_preview.sponsor`)."""

    sponsor_name: str
    sponsor_sector: str
    name: str
    description: str
    value_eur: int
    how_to_claim: str


class GameList(Model):
    games: list[GameInfo]
    prize: GamePrize | None = Field(default=None, description="The sponsor's prize for the half-time winner (v0.2.3)")


class PickerPlayer(Model):
    """Player picker entry: only name, team and shirt number (no careers, no nationality)."""

    player_id: str
    name: str
    team_id: TeamId
    shirt_number: int


class PlayerPicker(Model):
    players: list[PickerPlayer] = Field(description="The 50 players of the two squads")


class Criterion(Model):
    kind: Literal["club", "nationality"]
    id: str = Field(description="Club id (never one of the two match clubs) or country name")
    label: str


class TttCell(Model):
    row: int = Field(ge=0, le=2)
    col: int = Field(ge=0, le=2)
    owner: Literal["me", "opponent"] | None = None
    player_id: str | None = None


class Opponent(Model):
    display_name: str
    is_bot: bool


class TttGame(Model):
    game_session_id: str
    rows: list[Criterion] = Field(min_length=3, max_length=3)
    cols: list[Criterion] = Field(min_length=3, max_length=3)
    board: list[TttCell] = Field(min_length=9, max_length=9)
    opponent: Opponent
    my_symbol: Literal["X", "O"]
    turn: Literal["me", "opponent"]
    turn_seconds: int
    turn_deadline: datetime
    ends_at: datetime | None = Field(default=None, description="End of the 10 minutes: a draw without three in a row (v0.3.1)")
    status: Literal["playing", "won", "lost", "draw"]


class TttMoveRequest(Model):
    row: int = Field(ge=0, le=2)
    col: int = Field(ge=0, le=2)
    player_id: str


class TttMoveResult(Model):
    correct: bool
    reason: str | None = Field(default=None, description="wrong_answer or too_late (the turn is lost), cell_taken (the turn "
                               "is not lost), time_up (the 10 minutes are over)")
    elapsed_ms: int = Field(description="Measured by the server")
    game: TttGame
    points_awarded: int = 0


class CareerClue(Model):
    club_name: str = Field(description='"?" for the hidden current club')
    from_season: str
    to_season: str | None
    move: str | None = Field(default=None, description="transfer, loan, free_transfer, academy, loan_return (v0.3.1)")
    fee_eur: int | None = Field(default=None, ge=0, description="Only for paid transfers (v0.3.1)")
    hidden: bool = Field(default=False, description="The current club, to be guessed (v0.3.1)")


class TransferClue(Model):
    club_name: str
    season: str
    fee_eur: int = Field(ge=0)
    move: str = Field(description="transfer, loan, free_transfer, academy, loan_return")


class GuessRound(Model):
    """Guess the player: version 'career' (the full career with the fees, current club hidden; the game of M-3) or
    'transfers' (3 clubs with fees in EUR, kept for compatibility: the real game always answers 'career')."""

    round_id: str
    version: Literal["career", "transfers"]
    career: list[CareerClue] | None = None
    transfers: list[TransferClue] | None = None
    options: list[PickerPlayer] = Field(min_length=4, max_length=4)
    answer_seconds: int
    deadline: datetime
    round_number: int | None = Field(default=None, ge=1, description="1-5 within the session (v0.3.1)")
    rounds_total: int | None = Field(default=None, ge=1, description="Questions per session: 5 (v0.3.1)")


class AnswerRequest(Model):
    option_id: str = Field(description="player_id for Guess the player, option id for the quiz")


class AnswerResult(Model):
    correct: bool
    correct_option_id: str
    elapsed_ms: int = Field(description="Measured by the server from the round start")
    points_awarded: int
    speed_bonus: int
    fact: str | None = Field(default=None, description="The verified fact behind the answer")


class QuizOption(Model):
    option_id: str
    label: str
    player_id: str | None = None


class QuizRound(Model):
    round_id: str
    question: str
    metric: str = Field(description="Metric of METRICHE.md the answer comes from")
    scope: DataScope
    options: list[QuizOption] = Field(min_length=4, max_length=4)
    answer_seconds: int
    deadline: datetime


class LeaderboardEntry(Model):
    rank: int
    display_name: str
    points: int
    is_me: bool = False
    is_bot: bool = False


class Leaderboard(Model):
    match_id: str
    my_rank: int | None
    my_points: int
    entries: list[LeaderboardEntry]


# ---------------------------------------------------------------- Ask Archie


class ArchieRequest(Model):
    message: str = Field(min_length=1, max_length=1000)
    language: str | None = Field(default=None, description="Override of the session language")


class GroundedValue(Model):
    """One number of the answer and where it came from (checked by the grounding validator)."""

    text: str = Field(description="How the number appears in the answer, e.g. '6.3 km'")
    value: float
    unit: str | None = None
    tool: str
    arguments: dict[str, str | int | float] = Field(default_factory=dict)
    source_field: str = Field(description="Path in the tool result, e.g. distance_covered.top_runner.distance.km")


class ChartPointer(Model):
    chart_id: str = Field(description="A chart id of MatchStats.charts, or a table id of DATA_STATS_TABLES (v0.3.0)")
    section: Section
    locked: bool
    unlocks_at_phase: Phase | None = None


class AgentStep(Model):
    agent: Literal["Archie", "Kit Man", "The Analyst", "VAR", "The Ref"]
    model: str
    role: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class ToolCall(Model):
    agent: str
    tool: str
    arguments: dict[str, str | int | float] = Field(default_factory=dict)
    result_summary: str
    latency_ms: int


class GroundingCheck(Model):
    passed: bool
    numbers_checked: int
    numbers_unmatched: list[str] = Field(default_factory=list)


class InspectorTrace(Model):
    trace_id: str
    method: str
    agents: list[AgentStep]
    tool_calls: list[ToolCall]
    evidence: list[str] = Field(default_factory=list, description="Documents or data used (e.g. Kit Man files)")
    grounding: GroundingCheck
    total_tokens: int
    latency_ms: int
    cost_usd: float


class ArchieAnswer(Model):
    answer_id: str
    text: str
    language: str
    in_scope: bool = Field(description="False when Archie politely declines (other matches, invented numbers)")
    grounding: list[GroundedValue]
    points_to: ChartPointer | None = Field(default=None, description="Chart to open instead of drawing one")
    inspector: InspectorTrace | None = None


# ---------------------------------------------------------------- recap


class Recap(Model):
    match_id: str
    mode: Mode
    final_score: TeamScore
    headline: str
    narrative: str = Field(description="Summary written by the code from the match numbers, no model (Casual: score "
                                       "and scorers; Analyst: also shots and possession)")
    highlight_ids: list[str]
    key_numbers: list[GroundedValue]
    shots: list[TeamShots]
    possession: list[TeamValue]
    favourite_players: list[PlayerFocus] = Field(description="The same Player Focus cards as Data & Statistics (v0.3.0)")
    inspector: InspectorTrace | None = None
    unavailable: list[MetricUnavailable] = Field(default_factory=list, description="Null metrics and why (v0.2.1)")


# ---------------------------------------------------------------- demo panel


class DemoPhaseRequest(Model):
    phase: Phase
