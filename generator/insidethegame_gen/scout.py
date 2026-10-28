"""Scout v1: builds the fictional universe and the SYN-001 match information.

Deterministic: the same content files and the same seed always give the same
output (every random stream is seeded from the seed and a fixed name). Hand-written
content (clubs, stadium, staff, history, profile building blocks, rules) lives in
`content/`; this module adds everything that must be internally consistent
(squads, careers, transfer fees, coaches, honours, results, standings, season
numbers, line-ups, top scorers, previews) from seeded random generators.

Everything is in English natively (guidelines section 12). Player attributes
(1-99) come from `attributes.py`: our own scale, a percentile of the player's
position group on the DFL calibration (`calibration/attribute_scales.json`),
made from `attribute_profile`, the strengths and the age with a separate seeded
stream per player.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from datetime import date
from pathlib import Path

from .attributes import make_attributes
from .careers import (build_career, build_coaching_history, player_honours, season_label, season_start)
from .crests import crest_file, kit_file
from .games import foreign_nationalities  # noqa: F401  (re-exported for the command line)
from .names import NamePicker
from .profiles import international, pick_curiosities, pick_nickname, playing_style
from .schema import (
    POSITION_GROUP,
    POSITION_LABELS,
    AttributeProfile,
    Attributes,
    Availability,
    Birthplace,
    Chairman,
    Club,
    ClubDetails,
    ClubMatch,
    ClubRecords,
    Coach,
    Competition,
    Environment,
    Fixture,
    FormEntry,
    GoalRecord,
    HeadCoachProfile,
    HeadToHead,
    Injury,
    International,
    JobSecurity,
    KeyDuel,
    KitFiles,
    LastMeeting,
    LeaguePlayer,
    Legend,
    LineupEntry,
    MatchInfo,
    MatchOfficial,
    MatchPreview,
    MatchSponsor,
    MatchStaff,
    MatchTeam,
    Meeting,
    Official,
    Player,
    ProgrammeItem,
    RefereeCards,
    Result,
    RollOfHonourSeason,
    SeasonStats,
    SetPieces,
    Stadium,
    StaffMember,
    StandingRow,
    Standings,
    TeamNews,
    TopScorerRow,
    TopScorers,
    Universe,
    UniverseMeta,
)
from .season import (GOAL_RATE, MATCH_ROUND, ClubGame, Member, available, draft_squad, matchday_illness, select_bench, select_xi,
                     set_piece_takers, simulate_season)

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 2026
CONTENT_DIR = Path(__file__).parent / "content"
CONTENT_FILES = ("league", "match_clubs", "stadium", "names", "match", "blocklist", "profiles")
ROUNDS_PLAYED = MATCH_ROUND - 1  # SYN-001 is matchday 9
OFFICIAL_ROLES = ["referee", "assistant_referee_1", "assistant_referee_2", "fourth_official", "video_assistant_referee",
                  "assistant_video_assistant_referee"]

__all__ = ["build", "load_content", "season_label", "season_start", "age_on", "round_robin", "assign_home_away"]


def age_on(dob: date, day: date) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def load_content(content_dir: Path = CONTENT_DIR) -> dict:
    return {name: json.loads((content_dir / f"{name}.json").read_text(encoding="utf-8")) for name in CONTENT_FILES}


# --- Fixtures, results, standings ---------------------------------------------


def round_robin(club_ids: list[str]) -> list[list[tuple[str, str]]]:
    """Circle-method schedule: one cycle of rounds of unordered pairs."""
    teams = list(club_ids)
    if len(teams) % 2:
        teams.append("BYE")
    n = len(teams)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2) if "BYE" not in (teams[i], teams[n - 1 - i])])
        teams = [teams[0], teams[-1]] + teams[1:-1]
    return rounds


def assign_home_away(rounds: list[list[tuple[str, str]]]) -> list[list[tuple[str, str]]]:
    """Home goes to the club with fewer home games so far (then to the one that was away last time)."""
    homes: dict[str, int] = {}
    last_home: dict[str, bool] = {}
    out = []
    for pairs in rounds:
        fixed = []
        for a, b in pairs:
            key_a = (homes.get(a, 0), last_home.get(a, False))
            key_b = (homes.get(b, 0), last_home.get(b, False))
            h, w = (a, b) if key_a <= key_b else (b, a)
            homes[h] = homes.get(h, 0) + 1
            last_home[h], last_home[w] = True, False
            fixed.append((h, w))
        out.append(fixed)
    return out


def schedule_with_fixture(rng: random.Random, club_ids: list[str], fixture: tuple[str, str] | None,
                          fixture_round: int) -> list[list[tuple[str, str]]]:
    order = list(club_ids)
    rng.shuffle(order)
    rounds = round_robin(order)
    if fixture is not None:
        home, away = fixture
        idx = next(i for i, r in enumerate(rounds) if any({h, a} == {home, away} for h, a in r))
        rounds[idx], rounds[fixture_round - 1] = rounds[fixture_round - 1], rounds[idx]
    rounds = assign_home_away(rounds)
    if fixture is not None:
        rounds[fixture_round - 1] = [(home, away) if {h, a} == {home, away} else (h, a)
                                     for h, a in rounds[fixture_round - 1]]
    return rounds


def simulate_score(rng: random.Random, s_home: float, s_away: float) -> tuple[int, int]:
    lam_home = 1.30 * math.exp(1.8 * (s_home - s_away)) + 0.10
    lam_away = 1.05 * math.exp(1.8 * (s_away - s_home))
    return poisson(rng, lam_home), poisson(rng, lam_away)


def build_standings(competition_id: str, club_ids: list[str], results: list[Result], club_names: dict[str, str],
                    after_round: int) -> Standings:
    rec = {c: {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "form": []} for c in club_ids}
    for r in sorted(results, key=lambda x: x.round):
        if r.competition_id != competition_id or r.round > after_round:
            continue
        for me, gf, ga in ((r.home_id, r.home_goals, r.away_goals), (r.away_id, r.away_goals, r.home_goals)):
            row = rec[me]
            row["P"] += 1
            row["GF"] += gf
            row["GA"] += ga
            res = "W" if gf > ga else "D" if gf == ga else "L"
            row[res] += 1
            row["form"].append(res)
    ordered = sorted(club_ids, key=lambda c: (-(3 * rec[c]["W"] + rec[c]["D"]), -(rec[c]["GF"] - rec[c]["GA"]),
                                              -rec[c]["GF"], club_names[c]))
    rows = [StandingRow(position=i + 1, club_id=c, played=rec[c]["P"], won=rec[c]["W"], drawn=rec[c]["D"],
                        lost=rec[c]["L"], goals_for=rec[c]["GF"], goals_against=rec[c]["GA"],
                        goal_difference=rec[c]["GF"] - rec[c]["GA"], points=3 * rec[c]["W"] + rec[c]["D"],
                        form="".join(rec[c]["form"][-5:]))
            for i, c in enumerate(ordered)]
    return Standings(competition_id=competition_id, after_round=after_round, rows=rows)


# --- Helpers for the squads ----------------------------------------------------------


def assign_nationalities(rng: random.Random, squad: list[Member], quota: dict[str, int]) -> None:
    """Academy players are Meridian; foreign nationalities go to senior non-academy players first."""
    academy = [m for m in squad if m.academy]
    while len(academy) > quota["Meridia"]:
        academy.pop().academy = False
    foreign = [n for n, k in quota.items() if n != "Meridia" for _ in range(k)]
    rng.shuffle(foreign)
    seniors = [m for m in squad if not m.academy and m.tier != "prospect"]
    juniors = [m for m in squad if not m.academy and m.tier == "prospect"]
    rng.shuffle(seniors)
    rng.shuffle(juniors)
    order = seniors + juniors
    for m in academy:
        m.nationality = "Meridia"
    for i, m in enumerate(order):
        m.nationality = foreign[i] if i < len(foreign) else "Meridia"


def plan_networks(squads: list[list[Member]], networks: dict[str, list[str]]) -> dict[int, list[str]]:
    """Clubs each foreign player passes through: rotating pairs of the nationality's network clubs."""
    plan: dict[int, list[str]] = {}
    for nat, clubs in networks.items():
        if nat.startswith("_"):
            continue
        group = sorted((m for sq in squads for m in sq if m.nationality == nat and not m.academy),
                       key=lambda m: (m.club_id, m.shirt))
        for i, m in enumerate(group):
            plan[id(m)] = [clubs[i % len(clubs)], clubs[(i + 1) % len(clubs)]]
    return plan


def full_name(p) -> str:
    return f"{p.first_name} {p.last_name}"


# --- Main build -------------------------------------------------------------------


def build(seed: int = DEFAULT_SEED, content_dir: Path = CONTENT_DIR) -> tuple[Universe, MatchInfo]:
    content = load_content(content_dir)

    def stream(name: str) -> random.Random:
        return random.Random(f"{seed}:{name}")

    league, details, match, names_c, profiles_c = (content["league"], content["match_clubs"], content["match"],
                                                    content["names"], content["profiles"])
    kickoff_day = date.fromisoformat(match["planned_kickoff"][:10])
    clubs = [Club(**c) for c in league["clubs"]]
    club_by_id = {c.id: c for c in clubs}
    competitions = [Competition(**c) for c in league["competitions"]]
    roll = [RollOfHonourSeason(**r) for r in league["roll_of_honour"]]
    home_id, guest_id = match["home_team_id"], match["guest_team_id"]
    match_clubs = (home_id, guest_id)

    # --- Results of rounds 1-8 and the fixtures of round 10 (never the other games of round 9) ---
    rng = stream("league")
    results: list[Result] = []
    fixtures: list[Fixture] = []
    standings: list[Standings] = []
    for comp in competitions:
        ids = [c.id for c in clubs if c.competition_id == comp.id]
        fixture = (home_id, guest_id) if comp.id == match["competition_id"] else None
        rounds = schedule_with_fixture(rng, ids, fixture, match["matchday"])
        for rnd, pairs in enumerate(rounds[:ROUNDS_PLAYED], start=1):
            for h, a in pairs:
                hg, ag = simulate_score(rng, club_by_id[h].strength, club_by_id[a].strength)
                results.append(Result(competition_id=comp.id, round=rnd, home_id=h, away_id=a, home_goals=hg, away_goals=ag))
        fixtures += [Fixture(competition_id=comp.id, round=match["matchday"] + 1, home_id=h, away_id=a)
                     for h, a in rounds[match["matchday"]]]
        standings.append(build_standings(comp.id, ids, results, {c.id: c.name for c in clubs}, ROUNDS_PLAYED))
    table = {r.club_id: r for st in standings for r in st.rows}

    # --- Names: hand-written people are reserved first ---
    hand_people = [(s["first_name"], s["last_name"]) for c in details["clubs"].values() for s in c["staff"]]
    hand_people += [(lg["first_name"], lg["last_name"]) for c in details["clubs"].values() for lg in c["legends"]]
    hand_people += [(c["chairman"]["first_name"], c["chairman"]["last_name"]) for c in details["clubs"].values()]
    towns = sorted({c.town for c in clubs})
    birth_towns = towns + names_c["birthplaces"]["Meridia_extra"]
    avoid_words = {w for c in clubs for w in (c.name + " " + c.town + " " + c.stadium.name + " " + c.nickname).split()}
    avoid_words |= {w for t in birth_towns for w in t.split()}
    picker = NamePicker(stream("names"), names_c, content["blocklist"], hand_people, avoid_words)
    for first, _ in hand_people:
        picker.protect(first)

    # --- Officials (Meridian), first names protected ---
    rng = stream("officials")
    panel = match["referee_panel_average"]
    officials: list[Official] = []
    match_officials: list[MatchOfficial] = []
    for i, role in enumerate(OFFICIAL_ROLES):
        first, last = picker.pick("Meridia", unique_first=True)
        picker.protect(first)
        is_ref = role in ("referee", "fourth_official", "video_assistant_referee")
        born = rng.randint(1978, 1984) if role in ("referee", "video_assistant_referee") else rng.randint(1980, 1993)
        top = min(born + 36, 2023)
        since = rng.randint(min(born + 28, top), top)
        matches = rng.randint(12, 24) * (kickoff_day.year - since)
        career = season = None
        if is_ref:
            rate = rng.uniform(3.0, 4.9)
            career = RefereeCards(matches=matches, yellow_cards=round(matches * rate), red_cards=round(matches * rng.uniform(0.06, 0.18)),
                                  penalties_awarded=round(matches * rng.uniform(0.18, 0.32)))
            n = rng.randint(4, 7)
            season = RefereeCards(matches=n, yellow_cards=max(0, round(n * rate + rng.uniform(-2, 2))),
                                  red_cards=poisson(rng, n * 0.12), penalties_awarded=poisson(rng, n * 0.25))
        notes = profiles_c["officials"]
        if role == "referee":
            experience = notes["referee_strict"] if career.yellow_cards / career.matches > panel["yellow_cards_per_match"] \
                else notes["referee_lenient"]
        else:
            experience = notes[role]
        officials.append(Official(id=f"REF-{i + 1:02d}", first_name=first, last_name=last, nationality="Meridia", born=born,
                                  birthplace=Birthplace(town=rng.choice(birth_towns), country="Meridia"),
                                  specialism="referee" if is_ref else "assistant_referee", panel_since=since,
                                  top_flight_matches=matches, experience=experience, career_cards=career,
                                  season_cards=season))
        match_officials.append(MatchOfficial(official_id=f"REF-{i + 1:02d}", role=role))

    # --- Squads: draft, nationalities, names, birthplaces ---
    squads: dict[str, list[Member]] = {}
    for cid in match_clubs:
        rng = stream(f"squad-{cid}")
        squad = draft_squad(rng, cid, details["clubs"][cid]["formation"], club_by_id[cid].strength, kickoff_day)
        assign_nationalities(rng, squad, names_c["squad_nationalities"][cid])
        for m in squad:
            m.first, m.last = picker.pick(m.nationality, unique_first=True)
            picker.protect(m.first)
            if m.nationality == "Meridia":
                m.birth_town = club_by_id[cid].town if m.academy and rng.random() < 0.6 else rng.choice(birth_towns)
            else:
                m.birth_town = rng.choice(names_c["birthplaces"][m.nationality])
            m.birth_country = m.nationality
        squads[cid] = squad

    # --- The season so far ---
    log: dict[str, list[dict]] = {}
    set_pieces: dict[str, dict[str, list[str]]] = {}
    ill: dict[str, Member | None] = {}
    for cid in match_clubs:
        rng = stream(f"season-{cid}")
        formation = details["clubs"][cid]["formation"]
        set_pieces[cid] = set_piece_takers(rng, squads[cid])
        games = [ClubGame(round=r.round, opponent_id=r.away_id if r.home_id == cid else r.home_id,
                          venue="home" if r.home_id == cid else "away",
                          goals_for=r.home_goals if r.home_id == cid else r.away_goals,
                          goals_against=r.away_goals if r.home_id == cid else r.home_goals)
                 for r in sorted(results, key=lambda r: r.round) if cid in (r.home_id, r.away_id)]
        log[cid] = simulate_season(rng, squads[cid], formation, games, set_pieces[cid], profiles_c["injuries"],
                                   profiles_c["long_injuries"])
        ill[cid] = matchday_illness(rng, squads[cid], formation, chance=0.5)

    # --- Coaching history (new coaches get names now) ---
    rng = stream("coaches")
    coaches: list[Coach] = []
    nat_w = names_c["coach_nationality_weights"]

    def new_coach(year: int) -> str:
        nats = [n for n in nat_w if picker.capacity(n) > 0]
        nat = rng.choices(nats, weights=[nat_w[n] for n in nats])[0]
        first, last = picker.pick(nat, unique_first=False)
        cid = f"COA-{len(coaches) + 1:03d}"
        coaches.append(Coach(id=cid, first_name=first, last_name=last, nationality=nat, born=year - rng.randint(36, 55)))
        return cid

    history = build_coaching_history(rng, clubs, details["fixed_coaching_spells"], new_coach)

    # --- Careers ---
    rounds_by_club = {c.id: next(cp.rounds for cp in competitions if cp.id == c.competition_id) for c in clubs}
    weights = {k: v for k, v in names_c["hub_clubs"]["weights"].items()}
    plans = plan_networks(list(squads.values()), names_c["scouting_networks"])
    careers: dict[int, list] = {}
    for cid in match_clubs:
        rng = stream(f"careers-{cid}")
        for m in squads[cid]:
            careers[id(m)] = build_career(rng, m, clubs, weights, plans.get(id(m), []), set(match_clubs), rounds_by_club,
                                          history)

    # --- Profiles, line-ups and players ---
    players: list[Player] = []
    lineups: dict[str, list[LineupEntry]] = {}
    status_by_id: dict[str, tuple[str, str | None]] = {}
    armband: dict[str, str] = {}
    captains: dict[str, tuple[Member, Member]] = {}
    xi_by_club: dict[str, list[tuple[str, Member]]] = {}
    bench_by_club: dict[str, list[Member]] = {}
    nick_taken: set[str] = set()  # nicknames and curiosities are not repeated within the match
    curio_taken: set[str] = set()
    for cid in match_clubs:
        rng = stream(f"profiles-{cid}")
        squad = squads[cid]
        formation = details["clubs"][cid]["formation"]
        club = club_by_id[cid]
        # Captain and vice: the first-choice players who have been at the club longest (then the oldest).
        seniority = sorted((m for m in squad if m.tier == "starter"),
                           key=lambda m: (season_start(careers[id(m)][-1].from_season), m.dob, m.shirt))
        captains[cid] = (seniority[0], seniority[1])
        # SYN-001 selection from the players available on matchday 9.
        pool = available(squad, MATCH_ROUND)
        xi = select_xi(rng, pool, formation, rotation=False)
        bench = select_bench([m for m in pool if m not in [x for _, x in xi]])
        xi_by_club[cid], bench_by_club[cid] = xi, bench
        in_xi = [m for _, m in xi]
        captain, vice = captains[cid]
        wearer = captain if captain in in_xi else vice if vice in in_xi else next(m for m in seniority if m in in_xi)
        armband[cid] = wearer.id
        entries = [LineupEntry(player_id=m.id, shirt_number=m.shirt, starting=True, playing_position=slot,
                               captain=m is wearer) for slot, m in xi]
        entries += [LineupEntry(player_id=m.id, shirt_number=m.shirt, starting=False, playing_position=m.position)
                    for m in bench]
        lineups[cid] = entries
        for m in squad:
            if m in in_xi:
                status_by_id[m.id] = ("starting", None)
            elif m in bench:
                status_by_id[m.id] = ("substitute", None)
            else:
                status_by_id[m.id] = ("not_selected", m.out.get(MATCH_ROUND, "coach_decision"))

        # Squad-level facts for the computed curiosities (only when one player holds them alone).
        ages = {id(m): age_on(m.dob, kickoff_day) for m in squad}
        n_clubs = {id(m): len({sp.club_id for sp in careers[id(m)]}) for m in squad}

        def sole(values: dict[int, float], pick_max: bool) -> int | None:
            target = max(values.values()) if pick_max else min(values.values())
            holders = [k for k, v in values.items() if v == target]
            return holders[0] if len(holders) == 1 else None

        computed: dict[int, list[str]] = {id(m): [] for m in squad}
        cc = profiles_c["computed_curiosities"]
        by_obj = {id(m): m for m in squad}
        if (k := sole({id(m): m.dob.toordinal() for m in squad}, True)) is not None:
            computed[k].append(cc["youngest"].format(age=ages[k]))
        if (k := sole({id(m): m.dob.toordinal() for m in squad}, False)) is not None:
            computed[k].append(cc["oldest"].format(age=ages[k]))
        if (k := sole({id(m): m.height for m in squad}, True)) is not None:
            computed[k].append(cc["tallest"].format(height=by_obj[k].height))
        if (k := sole(n_clubs, True)) is not None:
            computed[k].append(cc["most_clubs"].format(n_clubs=n_clubs[k]))
        fees = {id(m): careers[id(m)][-1].fee_eur for m in squad}
        if (k := sole(fees, True)) is not None and fees[k] > 0:
            car = careers[k]
            computed[k].append(cc["big_fee"].format(fee=format_eur(fees[k]), from_club=club_by_id[car[-2].club_id].name,
                                                    season=car[-1].from_season))

        for m in squad:
            car = careers[id(m)]
            loans = [sp for sp in car if sp.move == "loan"]
            ctx = {
                "first": m.first, "last": m.last, "group": m.group, "height": m.height, "age": ages[id(m)],
                "foot": m.foot, "meridian": m.nationality == "Meridia", "academy": m.academy, "town": m.birth_town,
                "first_club": club_by_id[car[0].club_id].name, "first_season": car[0].from_season,
                "debut_age": season_start(car[0].from_season) - m.dob.year,
                "loan_club": club_by_id[loans[0].club_id].name if loans else "", "shirt": m.shirt, "club": club.name,
                "club_ground": club.stadium.name, "first_spell_goals": car[0].goals,
                "career_goals": sum(sp.goals for sp in car), "small_town": m.birth_town not in towns,
            }
            nickname, origin = pick_nickname(rng, ctx, profiles_c, nick_taken)
            style_text, strengths = playing_style(rng, {**ctx, "style": m.style}, profiles_c)
            curiosities = pick_curiosities(rng, ctx, profiles_c, curio_taken, computed[id(m)])
            caps, intl_goals = international(rng, m.nationality, m.tier, ages[id(m)], m.group, m.quality)
            status, reason = status_by_id[m.id]
            players.append(Player(
                id=m.id, first_name=m.first, last_name=m.last, title=None, nickname=nickname, nickname_origin=origin,
                short_name=m.last, date_of_birth=m.dob,
                birthplace=Birthplace(town=m.birth_town, country=m.birth_country), nationality=m.nationality,
                international=International(team=m.nationality, caps=caps, goals=intl_goals), height_cm=m.height,
                preferred_foot=m.foot, club_id=cid, shirt_number=m.shirt, position=m.position,
                position_group=POSITION_GROUP[m.position],
                captaincy="captain" if m is captain else "vice_captain" if m is vice else "none",
                academy_graduate=m.academy, playing_style=style_text, strengths=strengths, curiosities=curiosities,
                career=car, honours=player_honours(car, roll),
                this_season=SeasonStats(competition_id=club.competition_id, appearances=m.stats["appearances"],
                                        starts=m.stats["starts"], minutes=m.stats["minutes"], goals=m.stats["goals"],
                                        assists=m.stats["assists"], yellow_cards=m.stats["yellow_cards"],
                                        red_cards=m.stats["red_cards"]),
                form=[FormEntry(**m.form[r]) for r in range(ROUNDS_PLAYED - 4, ROUNDS_PLAYED + 1)],
                availability=availability(m, club_by_id, profiles_c, ill[cid]),
                injury_history=injury_history(rng, m, club_by_id, profiles_c),
                match_status=status, not_selected_reason=reason,
                attribute_profile=AttributeProfile(tier=m.tier, quality=m.quality, style=m.style),
                attributes=Attributes(**make_attributes(seed, m.id, m.group, m.quality, m.style, strengths,
                                                        ages[id(m)])),
                attributes_status="calibrated",
            ))

    # --- Head-to-head, last meeting with scorers (players who were at the clubs that season) ---
    rng = stream("head-to-head")
    meetings = []
    for y in (2023, 2024, 2025):
        for h, a in ((guest_id, home_id), (home_id, guest_id)):
            hg, ag = simulate_score(rng, club_by_id[h].strength, club_by_id[a].strength)
            meetings.append(Meeting(season=season_label(y), competition="Meridian League", home_id=h, away_id=a,
                                    home_goals=hg, away_goals=ag))
    last = meetings[-1]
    by_id = {p.id: p for p in players}
    scorers: list[GoalRecord] = []
    derby_goals: Counter = Counter()
    for side, goals in ((last.home_id, last.home_goals), (last.away_id, last.away_goals)):
        for _ in range(goals):
            scorers.append(derby_scorer(rng, [p for p in players if p.club_id == side], season_start(last.season),
                                        derby_goals))
    scorers.sort(key=lambda g: g.minute)
    summary = last_meeting_summary(last, scorers, by_id, club_by_id)
    h2h = HeadToHead(club_a=home_id, club_b=guest_id, name=details["derby_name"], story=details["derby_story"],
                     all_time=match["head_to_head_all_time"], last_meetings=meetings,
                     last_meeting=LastMeeting(**last.model_dump(), scorers=scorers, summary=summary))

    # --- Top scorers of the match competition (other clubs: their leading scorer only) ---
    rng = stream("top-scorers")
    lp_weights = names_c["league_player_nationality_weights"]
    league_players: list[LeaguePlayer] = []
    for c in sorted((c for c in clubs if c.competition_id == match["competition_id"] and c.id not in match_clubs),
                    key=lambda c: c.id):
        gf = table[c.id].goals_for
        if gf < 2:
            continue
        nats = [n for n in lp_weights if picker.capacity(n) > 0]
        nat = rng.choices(nats, weights=[lp_weights[n] for n in nats])[0]
        first, last_name = picker.pick(nat, unique_first=False)
        league_players.append(LeaguePlayer(id=f"LP-{c.id}", first_name=first, last_name=last_name, club_id=c.id,
                                           nationality=nat, position=rng.choice(["ST", "ST", "ST", "CAM", "LW", "RW"]),
                                           goals=max(1, round(gf * rng.uniform(0.25, 0.5)))))
    cands = [(p.this_season.goals, p.last_name, p.id, p.club_id) for p in players if p.this_season.goals > 0]
    cands += [(lp.goals, lp.last_name, lp.id, lp.club_id) for lp in league_players]
    cands.sort(key=lambda x: (-x[0], x[1]))
    rows = [TopScorerRow(rank=1 + sum(1 for o in cands if o[0] > g), player_id=pid, club_id=club_id, goals=g)
            for g, _, pid, club_id in cands]
    top = TopScorers(competition_id=match["competition_id"], after_round=ROUNDS_PLAYED,
                     rows=[r for r in rows if r.rank <= 10])

    # --- Staff, club details ---
    staff = [StaffMember(id=f"{cid}-S{i + 1}", club_id=cid, **s)
             for cid in match_clubs for i, s in enumerate(details["clubs"][cid]["staff"])]
    thresholds = details["job_security_thresholds"]
    club_details = []
    for cid in match_clubs:
        d = details["clubs"][cid]
        hc = d["head_coach"]
        row = table[cid]
        level = "secure" if row.position <= thresholds["secure"] else "stable" if row.position <= thresholds["stable"] \
            else "under_pressure"
        summary_text = hc["job_security"][level].format(contract=hc["contract_until"], position=ordinal(row.position),
                                                        played=row.played)
        club_details.append(ClubDetails(
            club_id=cid, formation=d["formation"], playing_style=d["playing_style"], colour_names=d["colour_names"],
            crest=club_by_id[cid].crest, kits=d["kits"], chairman=Chairman(**d["chairman"]), history=d["history"],
            honours=d["honours"], records=ClubRecords(**d["records"]), last_season=d["last_season"],
            legends=[Legend(**lg) for lg in d["legends"]], traditions=d["traditions"],
            head_coach=HeadCoachProfile(staff_id=f"{cid}-S1", tactical_style=hc["tactical_style"], career=hc["career"],
                                        job_security=JobSecurity(level=level, summary=summary_text)),
            set_pieces=SetPieces(**set_pieces[cid]),
            season_matches=[ClubMatch(**{**g, "scorers": [GoalRecord(**s) for s in g["scorers"]]}) for g in log[cid]],
        ))

    # --- Match preview ---
    preview = MatchPreview(
        match_id=match["match_id"],
        programme=[ProgrammeItem(**p) for p in match["programme"]],
        team_news=[TeamNews(club_id=cid, items=team_news(cid, players, xi_by_club[cid], captains[cid], club_by_id,
                                                          details["clubs"][cid]["staff"][0]))
                   for cid in match_clubs],
        key_duels=key_duels(home_id, guest_id, xi_by_club, by_id, table, club_by_id),
        sponsor=MatchSponsor(**{k: v for k, v in match["sponsor"].items() if not k.startswith("_")}),
    )

    stadium = Stadium(**{k: v for k, v in content["stadium"].items() if not k.startswith("_")})
    universe = Universe(
        meta=UniverseMeta(universe_id="MERIDIA-1.0", generator="Scout v1 (deterministic)",
                          generator_version=GENERATOR_VERSION, seed=seed, country=league["country"],
                          country_note=league["country_note"], season=league["season"],
                          disclaimer="Synthetic data. Every club, place and person is invented; any resemblance to real "
                                     "ones is coincidental. Nationalities and birthplaces abroad use real country and "
                                     "city names."),
        competitions=competitions, clubs=clubs, club_details=club_details, roll_of_honour=roll, players=players,
        league_players=league_players, staff=staff, coaches=coaches, coaching_history=history, officials=officials,
        stadiums=[stadium], results=results, fixtures=fixtures, standings=standings, top_scorers=[top],
        head_to_head=[h2h], match_preview=preview,
    )

    env = match["environment"]
    teams = []
    for role, cid, kit in (("home", home_id, match["home_kit"]), ("guest", guest_id, match["guest_kit"])):
        teams.append(MatchTeam(role=role, team_id=cid, formation=details["clubs"][cid]["formation"], kit=kit,
                               crest_file=crest_file(cid),
                               kit_files=KitFiles(home=kit_file(cid, "home"), away=kit_file(cid, "away")),
                               lineup=lineups[cid],
                               staff=[MatchStaff(staff_id=s.id, role=s.role) for s in staff
                                      if s.club_id == cid and s.role != "kit_manager"]))
    match_info = MatchInfo(
        match_id=match["match_id"], universe_id=universe.meta.universe_id, competition_id=match["competition_id"],
        season=league["season"], matchday=match["matchday"], planned_kickoff=match["planned_kickoff"],
        home_team_id=home_id, guest_team_id=guest_id,
        environment=Environment(stadium_id=env["stadium_id"], pitch_x_m=stadium.pitch.length_m,
                                pitch_y_m=stadium.pitch.width_m, roof=env["roof"], floodlight=env["floodlight"],
                                weather=env["weather"], forecast=env["forecast"], stadium_capacity=stadium.capacity,
                                tickets_sold=env["tickets_sold"]),
        teams=teams, officials=match_officials,
    )
    return universe, match_info


# --- Text and record helpers ------------------------------------------------------


def format_eur(eur: int) -> str:
    if eur == 0:
        return "€0"
    if eur >= 1_000_000:
        return f"€{eur / 1_000_000:.1f}m".replace(".0m", "m")
    return f"€{eur // 1000}k"


def availability(m: Member, clubs: dict[str, Club], content: dict, ill: Member | None) -> Availability:
    reason = m.out.get(MATCH_ROUND)
    if reason == "injury":
        if m.long_injury:
            return Availability(status="injured", detail=f"{m.long_injury['injury'].capitalize()}.",
                                expected_return=m.long_injury["return"].removeprefix("expected back ").strip())
        inj = m.injuries[-1]
        return Availability(status="injured", detail=f"{inj['injury'].capitalize()} picked up against "
                            f"{clubs[inj['opponent_id']].name} on matchday {inj['round']}.",
                            expected_return=f"matchday {max(inj['rounds']) + 1}")
    if reason == "suspension":
        opp = clubs[m.form[MATCH_ROUND - 1]["opponent_id"]].name
        return Availability(status="suspended", detail=f"One-match ban after a red card against {opp} on matchday "
                            f"{MATCH_ROUND - 1}.", expected_return=f"matchday {MATCH_ROUND + 1}")
    if reason == "illness":
        illness = content["illnesses"][m.shirt % len(content["illnesses"])]
        return Availability(status="ill", detail=f"Ruled out on the morning of the match with {illness}.",
                            expected_return=f"matchday {MATCH_ROUND + 1}")
    if m.injuries:
        inj = m.injuries[-1]
        return Availability(status="available", detail=f"Fit again after a {inj['injury']} that kept him out of "
                            f"{len(inj['rounds'])} league game{'s' if len(inj['rounds']) != 1 else ''}.")
    return Availability(status="available", detail="Fit and available.")


def injury_history(rng: random.Random, m: Member, clubs: dict[str, Club], content: dict) -> list[Injury]:
    out = []
    if m.tier != "prospect" and rng.random() < 0.2:
        spec = rng.choice(content["injuries"])
        n = rng.randint(3, 12)
        note = rng.choice(content["past_injuries"]).format(n=n, injury=spec["injury"])
        out.append(Injury(season="2025/26", injury=spec["injury"], matches_missed=n, note=note))
    if m.long_injury:
        out.append(Injury(season="2026/27", injury=m.long_injury["injury"], matches_missed=ROUNDS_PLAYED,
                          note=f"Has missed every league game this season; {m.long_injury['return']}."))
    for inj in m.injuries:
        missed = [r for r in inj["rounds"] if r <= ROUNDS_PLAYED]
        still_out = max(inj["rounds"]) >= MATCH_ROUND
        back = max(inj["rounds"]) + 1
        note = (f"Picked up against {clubs[inj['opponent_id']].name} on matchday {inj['round']}; "
                + ("still out." if still_out else "fit again in time for this match." if back == MATCH_ROUND
                   else f"back for matchday {back}."))
        out.append(Injury(season="2026/27", injury=inj["injury"], matches_missed=len(missed), note=note))
    return out


def derby_scorer(rng: random.Random, squad: list[Player], year: int, used: Counter) -> GoalRecord:
    """A scorer of the last derby: a current player who was at the club that season (not on loan elsewhere).

    Players whose spell still has goals to spare are preferred; if nobody has, the chosen spell gets one more
    goal, so the career numbers always include the derby goals.
    """
    def spell_for(p: Player):
        return next((sp for sp in p.career if sp.club_id == p.club_id and season_start(sp.from_season) <= year
                     and (sp.to_season is None or season_start(sp.to_season) >= year)), None)

    def season_of(sp):
        return next(s for s in sp.seasons if season_start(s.season) == year)

    eligible = [(p, spell_for(p)) for p in sorted(squad, key=lambda p: p.shirt_number)]
    eligible = [(p, season_of(sp), sp) for p, sp in eligible
                if sp is not None and p.position_group != "GK" and season_of(sp).appearances > 0]
    with_goals = [e for e in eligible if e[1].goals > used[(e[0].id, e[1].season)]]
    pool = with_goals or eligible
    p, season, sp = rng.choices(pool, weights=[GOAL_RATE[p.position_group] + 0.01 for p, _, _ in pool])[0]
    key = (p.id, season.season)
    used[key] += 1
    season.goals = max(season.goals, min(season.appearances, used[key]))
    sp.goals = sum(s.goals for s in sp.seasons)
    return GoalRecord(player_id=p.id, minute=rng.randint(1, 90), penalty=False)


def last_meeting_summary(last: Meeting, scorers: list[GoalRecord], players: dict[str, Player], clubs: dict[str, Club]) -> str:
    home, away = clubs[last.home_id], clubs[last.away_id]
    if last.home_goals > last.away_goals:
        verdict = f"{home.name} beat {away.name} {last.home_goals}-{last.away_goals}"
    elif last.home_goals < last.away_goals:
        verdict = f"{away.name} won {last.away_goals}-{last.home_goals} away at {home.name}"
    else:
        verdict = f"{home.name} and {away.name} drew {last.home_goals}-{last.away_goals}"
    text = f"In the last meeting, in the {last.season} Meridian League, {verdict} at the {home.stadium.name}."
    if scorers:
        goals = ", ".join(f"{full_name(players[g.player_id])} ({clubs[players[g.player_id].club_id].short_name}, "
                          f"{g.minute}')" for g in scorers)
        text += f" Goals: {goals}."
    return text


def team_news(cid: str, players: list[Player], xi: list[tuple[str, Member]], captains: tuple[Member, Member],
              clubs: dict[str, Club], coach: dict) -> list[str]:
    squad = [p for p in players if p.club_id == cid]
    starters = {m.id for _, m in xi}
    items = []
    changes = sum(1 for p in squad if p.id in starters and not p.form[-1].started)
    items.append("Unchanged starting XI from matchday 8." if changes == 0 else
                 f"{changes} change{'s' if changes != 1 else ''} to the starting XI from matchday 8.")
    for p in sorted(squad, key=lambda p: p.shirt_number):
        label = POSITION_LABELS[p.position].lower()
        if p.availability.status == "injured":
            items.append(f"{full_name(p)} ({label}) is out: {p.availability.detail[0].lower()}"
                         f"{p.availability.detail[1:].rstrip('.')}. Expected back: {p.availability.expected_return}.")
        elif p.availability.status == "suspended":
            items.append(f"{full_name(p)} ({label}) is suspended: {p.availability.detail[0].lower()}"
                         f"{p.availability.detail[1:]}")
        elif p.availability.status == "ill":
            items.append(f"{full_name(p)} ({label}) misses out: {p.availability.detail[0].lower()}"
                         f"{p.availability.detail[1:]}")
        elif p.injury_history and p.injury_history[-1].season == "2026/27" and p.match_status != "not_selected":
            inj = p.injury_history[-1]
            items.append(f"{full_name(p)} is back in the matchday squad after missing {inj.matches_missed} league "
                         f"game{'s' if inj.matches_missed != 1 else ''} with a {inj.injury}.")
    for p in sorted(squad, key=lambda p: p.shirt_number):
        if p.id in starters and p.this_season.starts == 0:
            items.append(f"{full_name(p)} ({POSITION_LABELS[p.position].lower()}, {p.this_season.appearances} league "
                         f"appearance{'s' if p.this_season.appearances != 1 else ''} so far) makes his first league start "
                         "of the season.")
    captain, vice = captains
    if captain.id not in starters:
        where = "out" if next(p for p in squad if p.id == captain.id).match_status == "not_selected" else "on the bench"
        wearer = f"vice-captain {vice.first} {vice.last}" if vice.id in starters else "the longest-serving starter"
        items.append(f"Captain {captain.first} {captain.last} is {where}, so {wearer} wears the armband.")
    return items


def key_duels(home_id: str, guest_id: str, xi_by_club: dict, players: dict[str, Player], table: dict,
              clubs: dict[str, Club]) -> list[KeyDuel]:
    def at(cid: str, *slots: str) -> Player:
        for s in slots:
            for slot, m in xi_by_club[cid]:
                if slot == s:
                    return players[m.id]
        raise KeyError(slots)

    def goals(p: Player) -> str:
        g = p.this_season.goals
        return f"{g} league goal{'s' if g != 1 else ''} this season"

    played = table[home_id].played
    home, guest = clubs[home_id], clubs[guest_id]
    duels = []
    a, b = at(home_id, "ST"), at(guest_id, "LCB", "RCB")
    duels.append(KeyDuel(home_player_id=a.id, guest_player_id=b.id, title=f"{a.last_name} v {b.last_name}",
                         note=f"{home.short_name}'s striker {full_name(a)} ({goals(a)}) against {guest.short_name}'s "
                              f"centre back {full_name(b)}, {b.height_cm} cm, who has started {b.this_season.starts} of "
                              f"{played} league games."))
    a, b = at(home_id, "RW", "RM"), at(guest_id, "LB")
    duels.append(KeyDuel(home_player_id=a.id, guest_player_id=b.id, title=f"{a.last_name} v {b.last_name}",
                         note=f"{home.short_name}'s right winger {full_name(a)} ({a.this_season.assists} "
                              f"assist{'s' if a.this_season.assists != 1 else ''}, {goals(a)}) against "
                              f"{guest.short_name}'s left back {full_name(b)}."))
    strikers = sorted((at(guest_id, s) for s in ("RS", "LS")), key=lambda p: (-p.this_season.goals, p.shirt_number))
    a, b = at(home_id, "RCB", "LCB"), strikers[0]
    duels.append(KeyDuel(home_player_id=a.id, guest_player_id=b.id, title=f"{a.last_name} v {b.last_name}",
                         note=f"{guest.short_name}'s striker {full_name(b)} ({goals(b)}) against {home.short_name}'s "
                              f"centre back {full_name(a)}, {a.height_cm} cm."))
    return duels
