"""Pydantic models for the synthetic universe (v1) and the SYN-001 match information.

Everything is in English natively (guidelines section 12): position codes,
position groups, staff roles, referee roles and formations. The match
information keeps the *structure* of the DFL `matchinformation` file (teams with
line-ups and trainer staff, referees with roles, environment), so the same
adapters can read real and synthetic matches.

Every person (player, staff member, coach, official, chairman, legend, other
league player) uses the same name fields: `first_name`, `last_name`, `title`
(for example "Dr."), `nickname`.

Money: every transfer fee is an integer amount in euros (`fee_eur`). Loans,
free transfers, academy promotions and returns from loan are 0.

Player attributes use our own 1-99 scale: a percentile of the player's position
group on the DFL calibration (`calibration/attribute_scales.json`); `attributes.py`
makes them and documents the mapping to physical values.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- English codes (guidelines section 12 and section 16) -----------------------

POSITION_LABELS: dict[str, str] = {
    "GK": "Goalkeeper",
    "LCB": "Left centre back", "RCB": "Right centre back", "CB": "Centre back",
    "LB": "Left back", "RB": "Right back", "LWB": "Left wing back", "RWB": "Right wing back",
    "CDM": "Central defensive midfielder", "LDM": "Left defensive midfielder", "RDM": "Right defensive midfielder",
    "LCM": "Left central midfielder", "RCM": "Right central midfielder",
    "LM": "Left wide midfielder", "RM": "Right wide midfielder",
    "CAM": "Attacking midfielder", "LW": "Left winger", "RW": "Right winger",
    "ST": "Striker", "LS": "Left striker", "RS": "Right striker",
}
POSITION_GROUP: dict[str, str] = {
    "GK": "GK", "LCB": "CB", "RCB": "CB", "CB": "CB", "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB",
    "CDM": "DM", "LDM": "DM", "RDM": "DM", "LCM": "CM", "RCM": "CM", "LM": "W", "RM": "W",
    "CAM": "AM", "LW": "W", "RW": "W", "ST": "ST", "LS": "ST", "RS": "ST",
}
GROUP_LABELS: dict[str, str] = {"GK": "Goalkeeper", "CB": "Centre back", "FB": "Full back", "DM": "Defensive midfielder",
                                "CM": "Central midfielder", "AM": "Attacking midfielder", "W": "Wide player", "ST": "Striker"}
STAFF_ROLE_LABELS: dict[str, str] = {
    "head_coach": "Head coach", "assistant_coach": "Assistant coach", "goalkeeper_coach": "Goalkeeper coach",
    "fitness_coach": "Fitness coach", "head_physio": "Head physio", "club_doctor": "Club doctor", "kit_manager": "Kit manager",
}
REFEREE_ROLE_LABELS: dict[str, str] = {
    "referee": "Referee", "assistant_referee_1": "Assistant referee 1", "assistant_referee_2": "Assistant referee 2",
    "fourth_official": "Fourth official", "video_assistant_referee": "Video assistant referee (VAR)",
    "assistant_video_assistant_referee": "Assistant video assistant referee (AVAR)",
}
COMPETITION_NAMES = {"league": "Meridian League", "cup": "Meridian Cup", "championship": "Meridian Championship",
                     "super_shield": "Meridian Super Shield"}

Position = Literal["GK", "LCB", "RCB", "CB", "LB", "RB", "LWB", "RWB", "CDM", "LDM", "RDM", "LCM", "RCM", "LM", "RM",
                   "CAM", "LW", "RW", "ST", "LS", "RS"]
PositionGroup = Literal["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
Foot = Literal["left", "right", "both"]
StaffRole = Literal["head_coach", "assistant_coach", "goalkeeper_coach", "fitness_coach", "head_physio", "club_doctor",
                    "kit_manager"]
RefereeRole = Literal["referee", "assistant_referee_1", "assistant_referee_2", "fourth_official", "video_assistant_referee",
                      "assistant_video_assistant_referee"]
SectorType = Literal["home", "away", "family", "accessible", "club_level"]
Move = Literal["academy", "transfer", "free_transfer", "loan", "loan_return"]
MatchStatus = Literal["starting", "substitute", "not_selected"]
NotSelectedReason = Literal["injury", "suspension", "illness", "coach_decision"]
Precipitation = Literal["none", "light_rain", "rain", "snow"]
SeasonLabel = Annotated[str, Field(pattern=r"^\d{4}/\d{2}$")]
Text = Annotated[str, Field(min_length=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonName(Strict):
    """The one name schema shared by every person of the universe."""

    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    title: str | None = Field(default=None, description='Honorific such as "Dr."; never part of the first name.')
    nickname: str | None = Field(default=None, description="Never part of the first or last name.")


# --- League and clubs -------------------------------------------------------


class Competition(Strict):
    id: str
    name: str
    tier: int
    clubs: int
    rounds: int


class Colours(Strict):
    primary: str
    secondary: str
    number: str


class ClubStadium(Strict):
    name: str
    capacity: int = Field(gt=0)


class Club(Strict):
    id: str = Field(pattern=r"^[A-Z]{3}$")
    name: str
    short_name: str
    town: str
    founded: int
    nickname: str
    colours: Colours
    stadium: ClubStadium
    competition_id: str
    strength: float = Field(ge=0, le=1, description="Latent team strength used only to simulate results.")
    rival_id: str | None = None
    summary: str
    crest: Text = Field(description="Description of the club crest, enough to draw it.")


class RollOfHonourSeason(Strict):
    """Winners of each national competition in one season (club ids)."""

    season: SeasonLabel
    league: str
    cup: str
    championship: str = Field(description="Winner of the second division (Meridian Championship).")
    super_shield: str = Field(description="Played in August at the start of this season: league champions v cup winners "
                                          "of the season before. Labelled by year, e.g. '2018'.")


class Honour(Strict):
    competition: str
    seasons: list[str]


class Legend(PersonName):
    role: str
    fact: str


class Kit(Strict):
    shirt: str
    shorts: str
    socks: str
    description: Text


class LastSeason(Strict):
    competition_id: str
    position: int


class Chairman(PersonName):
    since: int
    bio: Text


class ClubRecords(Strict):
    biggest_win: Text
    top_scorer: Text
    most_appearances: Text


class SetPieces(Strict):
    """Order of preference (player ids). The first player on the pitch takes it."""

    penalties: list[str] = Field(min_length=2)
    free_kicks: list[str] = Field(min_length=2)
    corners: list[str] = Field(min_length=2)


class JobSecurity(Strict):
    level: Literal["secure", "stable", "under_pressure"]
    summary: Text


class HeadCoachProfile(Strict):
    staff_id: str
    tactical_style: Text
    career: list[str] = Field(min_length=1)
    job_security: JobSecurity


class GoalRecord(Strict):
    player_id: str
    minute: int = Field(ge=1, le=90)
    penalty: bool = False


class ClubMatch(Strict):
    """One league game of this season from the club's point of view, with its scorers."""

    round: int
    opponent_id: str
    venue: Literal["home", "away"]
    goals_for: int = Field(ge=0)
    goals_against: int = Field(ge=0)
    scorers: list[GoalRecord]


class ClubDetails(Strict):
    """Rich details, only for the two clubs of the match."""

    club_id: str
    formation: str
    playing_style: str
    colour_names: Text
    crest: Text
    kits: dict[Literal["home", "away"], Kit]
    chairman: Chairman
    history: list[str]
    honours: list[Honour]
    records: ClubRecords
    last_season: LastSeason
    legends: list[Legend]
    traditions: list[str]
    head_coach: HeadCoachProfile
    set_pieces: SetPieces
    season_matches: list[ClubMatch]


# --- People -----------------------------------------------------------------


class Birthplace(Strict):
    town: Text
    country: Text


class SpellSeason(Strict):
    """League numbers of one season of a career spell."""

    season: SeasonLabel
    appearances: int = Field(ge=0)
    goals: int = Field(ge=0)


class CareerSpell(Strict):
    club_id: str
    from_season: SeasonLabel
    to_season: str | None = Field(default=None, pattern=r"^\d{4}/\d{2}$", description="None = still at the club.")
    move: Move = Field(description="How the player arrived: academy promotion, transfer, free transfer (end of "
                                   "contract), loan or return from loan.")
    parent_club_id: str | None = Field(default=None, description="Club that owns the player during a loan.")
    fee_eur: int = Field(ge=0, description="Transfer fee in euros; 0 for academy, loan, loan return and free transfer.")
    appearances: int = Field(ge=0, description="Sum of the seasons.")
    goals: int = Field(ge=0, description="Sum of the seasons.")
    seasons: list[SpellSeason] = Field(min_length=1, description="League numbers season by season.")
    head_coach_ids: list[str] = Field(min_length=1, description="Head coaches of the club during the spell.")


class PlayerHonour(Strict):
    competition: str
    season: str
    club_id: str


class SeasonStats(Strict):
    """Current-season league numbers, consistent with the simulated results."""

    competition_id: str
    appearances: int = Field(ge=0)
    starts: int = Field(ge=0)
    minutes: int = Field(ge=0)
    goals: int = Field(ge=0)
    assists: int = Field(ge=0)
    yellow_cards: int = Field(ge=0)
    red_cards: int = Field(ge=0)


class FormEntry(Strict):
    round: int
    opponent_id: str
    started: bool
    minutes: int = Field(ge=0, le=90)
    goals: int = Field(ge=0)
    assists: int = Field(ge=0)


class Availability(Strict):
    status: Literal["available", "injured", "suspended", "ill"]
    detail: Text
    expected_return: str | None = Field(default=None, description="Only when not available.")


class Injury(Strict):
    season: SeasonLabel
    injury: Text
    matches_missed: int = Field(ge=0)
    note: Text


class International(Strict):
    team: str
    caps: int = Field(ge=0)
    goals: int = Field(ge=0)


class Attributes(Strict):
    """Our own 1-99 scale: attribute k is the k-th DFL percentile of the quantity below among players of the
    same position group (`calibration/attribute_scales.json`; mapping and fallbacks in `attributes.py`)."""

    pace: int = Field(ge=1, le=99, description="top speed per match (km/h)")
    acceleration: int = Field(ge=1, le=99, description="peak positive acceleration per match (m/s2)")
    stamina: int = Field(ge=1, le=99, description="distance per 90 minutes (m)")
    work_rate: int = Field(ge=1, le=99, description="distance above 20 km/h per 90 minutes (m)")
    passing: int = Field(ge=1, le=99, description="share of passes whose next touch is a team-mate's")
    shot_power: int = Field(ge=1, le=99, description="ball speed of an open-play shot (m/s), one scale for all groups")


class AttributeProfile(Strict):
    """Latent inputs that `attributes.py` turns into 1-99 attributes (with the strengths and the age)."""

    tier: Literal["starter", "rotation", "prospect"]
    quality: float = Field(ge=0, le=1)
    style: list[str]


class Player(PersonName):
    nickname: str = Field(min_length=1)
    nickname_origin: Text
    id: str
    short_name: str
    date_of_birth: date
    birthplace: Birthplace
    nationality: str
    international: International
    height_cm: int
    preferred_foot: Foot
    club_id: str
    shirt_number: int = Field(ge=1, le=99)
    position: Position = Field(description="Natural position, English code (guidelines section 12).")
    position_group: PositionGroup
    captaincy: Literal["captain", "vice_captain", "none"]
    academy_graduate: bool
    playing_style: Text
    strengths: list[str] = Field(min_length=2)
    curiosities: list[str] = Field(min_length=1, max_length=2)
    career: list[CareerSpell]
    honours: list[PlayerHonour]
    this_season: SeasonStats
    form: list[FormEntry] = Field(description="The club's last five league games, oldest first.")
    availability: Availability
    injury_history: list[Injury]
    match_status: MatchStatus = Field(description="Status for the match of this universe (SYN-001).")
    not_selected_reason: NotSelectedReason | None = Field(default=None, description="Only when not selected.")
    attribute_profile: AttributeProfile
    attributes: Attributes
    attributes_status: Literal["pending_calibration", "calibrated"] = "pending_calibration"


class LeaguePlayer(PersonName):
    """A player of another club, named only because he is in the top-scorer table."""

    id: str
    club_id: str
    nationality: str
    position: Position
    goals: int = Field(ge=0)


class StaffMember(PersonName):
    id: str
    club_id: str
    role: StaffRole
    nationality: str
    born: int
    since: int
    bio: str


class Coach(PersonName):
    """A head coach of the coaching history who is not on the staff of the two match clubs."""

    id: str
    nationality: str
    born: int


class CoachingSpell(Strict):
    club_id: str
    coach_id: str = Field(description="A Coach id (COA-...) or a StaffMember id of a match club.")
    from_season: SeasonLabel
    to_season: str | None = Field(default=None, pattern=r"^\d{4}/\d{2}$")


class RefereeCards(Strict):
    matches: int = Field(ge=0)
    yellow_cards: int = Field(ge=0)
    red_cards: int = Field(ge=0)
    penalties_awarded: int = Field(ge=0)


class Official(PersonName):
    id: str
    nationality: str
    born: int
    birthplace: Birthplace
    specialism: Literal["referee", "assistant_referee"]
    panel_since: int
    top_flight_matches: int = Field(ge=0)
    experience: Text
    career_cards: RefereeCards | None = Field(default=None, description="Top-division games as referee; referees only.")
    season_cards: RefereeCards | None = Field(default=None, description="This season as referee; referees only.")


# --- Stadium ----------------------------------------------------------------


class Sector(Strict):
    id: str
    capacity: int = Field(gt=0)
    type: SectorType


class Stand(Strict):
    id: str
    name: str
    side: Literal["north", "south", "east", "west"]
    description: str
    sectors: list[Sector]


class Gate(Strict):
    id: str
    stand_id: str
    serves: list[str]
    note: str | None = None


class Service(Strict):
    type: str
    name: str
    near: list[str]
    offer: str | None = None


class Transport(Strict):
    mode: str
    detail: str


class Pitch(Strict):
    length_m: float
    width_m: float
    surface: str
    undersoil_heating: bool


class RecordAttendance(Strict):
    value: int
    match: str
    season: str


class Stadium(Strict):
    id: str
    name: str
    club_id: str
    town: str
    address: str
    opened: int
    capacity: int
    record_attendance: RecordAttendance
    pitch: Pitch
    roof: str
    floodlights: str
    big_screens: list[str]
    history: str
    notable_matches: list[str] = Field(min_length=1)
    stands: list[Stand]
    gates: list[Gate]
    services: list[Service]
    transport: list[Transport]
    policies: list[str]


# --- Results, standings, head-to-head ---------------------------------------


class Result(Strict):
    competition_id: str
    round: int
    home_id: str
    away_id: str
    home_goals: int = Field(ge=0)
    away_goals: int = Field(ge=0)


class Fixture(Strict):
    competition_id: str
    round: int
    home_id: str
    away_id: str


class StandingRow(Strict):
    position: int
    club_id: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: str = Field(description="Last five results, oldest first: W, D or L.")


class Standings(Strict):
    competition_id: str
    after_round: int
    rows: list[StandingRow]


class TopScorerRow(Strict):
    rank: int
    player_id: str = Field(description="A Player id (match squads) or a LeaguePlayer id.")
    club_id: str
    goals: int


class TopScorers(Strict):
    competition_id: str
    after_round: int
    rows: list[TopScorerRow]


class Meeting(Strict):
    season: str
    competition: str
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int


class LastMeeting(Meeting):
    scorers: list[GoalRecord]
    summary: Text


class HeadToHead(Strict):
    club_a: str
    club_b: str
    name: str
    story: str
    all_time: dict[str, int] = Field(description="played, <club_a>_wins, <club_b>_wins, draws")
    last_meetings: list[Meeting]
    last_meeting: LastMeeting


# --- Match preview (Kit Man and the Pre-match section) ------------------------


class ProgrammeItem(Strict):
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    item: Text


class TeamNews(Strict):
    club_id: str
    items: list[str] = Field(min_length=1)


class KeyDuel(Strict):
    home_player_id: str
    guest_player_id: str
    title: Text
    note: Text


class SponsorPrize(Strict):
    name: Text
    description: Text
    value_eur: int = Field(ge=0, description="Face value in euros.")
    awarded_to: Text = Field(description="Who wins it (the winner of the half-time games).")
    how_to_claim: Text


class MatchSponsor(Strict):
    """The invented sponsor of the match. Never alcohol, betting or tobacco (check 6)."""
    id: str = Field(pattern=r"^SPO-[A-Z]+$")
    name: Text
    sector: Text
    description: Text
    prize: SponsorPrize


class MatchPreview(Strict):
    match_id: str
    programme: list[ProgrammeItem] = Field(min_length=1)
    team_news: list[TeamNews] = Field(min_length=2)
    key_duels: list[KeyDuel] = Field(min_length=2)
    sponsor: MatchSponsor = Field(description="Match sponsor (invented), with the prize for the winner of the games.")


# --- Universe and match -----------------------------------------------------


class UniverseMeta(Strict):
    universe_id: str
    generator: str
    generator_version: str
    seed: int
    country: str
    country_note: str
    season: str
    currency: Literal["EUR"] = "EUR"
    disclaimer: str


class Universe(Strict):
    meta: UniverseMeta
    competitions: list[Competition]
    clubs: list[Club]
    club_details: list[ClubDetails]
    roll_of_honour: list[RollOfHonourSeason]
    players: list[Player]
    league_players: list[LeaguePlayer]
    staff: list[StaffMember]
    coaches: list[Coach]
    coaching_history: list[CoachingSpell]
    officials: list[Official]
    stadiums: list[Stadium]
    results: list[Result]
    fixtures: list[Fixture] = Field(description="The next round after the match (no concurrent fixtures).")
    standings: list[Standings]
    top_scorers: list[TopScorers]
    head_to_head: list[HeadToHead]
    match_preview: MatchPreview


class LineupEntry(Strict):
    player_id: str
    shirt_number: int
    starting: bool
    playing_position: Position = Field(description="English code: the formation slot for starters, the natural "
                                                   "position for substitutes.")
    captain: bool = False


class MatchStaff(Strict):
    staff_id: str
    role: StaffRole


class KitFiles(Strict):
    """The SVG drawings of a club's two kits, paths relative to the repository root."""

    home: str
    away: str


class MatchTeam(Strict):
    role: Literal["home", "guest"]
    team_id: str
    formation: str
    kit: Literal["home", "away"] = Field(description="The kit the team wears in this match (a key of kit_files).")
    crest_file: str = Field(description="SVG of the club crest, path relative to the repository root "
                                        "(synthetic/crests/<team_id>.svg).")
    kit_files: KitFiles = Field(description="SVGs of the home and away kits (synthetic/kits/<team_id>_<kit>.svg).")
    lineup: list[LineupEntry]
    staff: list[MatchStaff] = Field(min_length=1, description="Head coach and bench staff (DFL TrainerStaff).")


class MatchOfficial(Strict):
    official_id: str
    role: RefereeRole


class Weather(Strict):
    temperature_c: int
    precipitation: Precipitation
    wind: str
    humidity_pct: int


class ForecastSlot(Strict):
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    phase: str
    temperature_c: int
    precipitation: Precipitation
    chance_of_rain_pct: int = Field(ge=0, le=100)
    wind: str
    sky: str


class Environment(Strict):
    stadium_id: str
    pitch_x_m: float
    pitch_y_m: float
    roof: str
    floodlight: Literal["on", "off"]
    weather: Weather
    forecast: list[ForecastSlot] = Field(min_length=3)
    stadium_capacity: int = Field(gt=0, description="Seats of the stadium, the universe's stadiums[].capacity.")
    tickets_sold: int


class MatchInfo(Strict):
    match_id: str
    universe_id: str
    competition_id: str
    season: str
    matchday: int
    planned_kickoff: str = Field(description="ISO 8601 local time with offset.")
    home_team_id: str
    guest_team_id: str
    environment: Environment
    teams: list[MatchTeam]
    officials: list[MatchOfficial]
    result: str | None = Field(default=None, description="Filled when the match is generated (Tactician).")
