"""Consistency and safety checks for the universe, the match information and the Kit Man documents.

Every check returns pass, warn or fail with the values behind the verdict. A `fail`
means nothing is written. No check is a warn any more: the attributes 1-99 are
checked like everything else (range, DFL calibration mapping, physical limits and
consistency with the player's strengths and style).

The checks cover UNIVERSO-SPEC section 1.4 (line-ups, positions, alignment between
squad and line-up, names, name schema, academy graduates, documents), the games
requirement of section 3 (Tiki-Taka-Toe crossings) and the coherence of the data
added in section 2 (honours, fees, scorers, top scorers, previews).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import games
from .attributes import (ATTRIBUTES, BALL_SPEED_MAX_MS, PLAYER_SPEED_MAX_KMH, SCALES_FILE, floors_for, load_scales,
                         physical_value, scale_table)
from .careers import CURRENT_START, coaches_during, season_label, season_start
from .crests import REPO_ROOT, crest_file, kit_file
from .names import WordSet, contains_word
from .schema import COMPETITION_NAMES, POSITION_GROUP, POSITION_LABELS, MatchInfo, Universe
from .season import FORMATIONS

# Thresholds of the games check (UNIVERSO-SPEC section 3).
MIN_ANSWERS = 2
MIN_FOREIGN_NATIONALITIES = 3
MIN_PLAYERS_PER_NATIONALITY = 4
MIN_CLUBS_PER_NATIONALITY = 3
MIN_GRIDS = 100
MIN_GRIDS_WITH_NATIONALITY = 50
# Surname rule: at most this many pairs may share a surname (UNIVERSO-SPEC 1.1). We allow none.
MAX_SHARED_SURNAME_PAIRS = 0
NAME_FIELDS = ("first_name", "last_name", "title", "nickname")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str


def _result(name: str, problems: list[str], ok_detail: str, warn: bool = False) -> CheckResult:
    if not problems:
        return CheckResult(name, "pass", ok_detail)
    shown = "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else "")
    return CheckResult(name, "warn" if warn else "fail", shown)


# --- People registry -------------------------------------------------------------


def people(u: Universe) -> list[tuple[str, str, object]]:
    """Every person of the universe as (kind, context, record). Context is the club or 'officials'."""
    out = [("player", p.club_id, p) for p in u.players]
    out += [("staff", s.club_id, s) for s in u.staff]
    out += [("official", "officials", o) for o in u.officials]
    out += [("coach", "coaches", c) for c in u.coaches]
    out += [("league_player", lp.club_id, lp) for lp in u.league_players]
    for d in u.club_details:
        out += [("legend", d.club_id, lg) for lg in d.legends]
        out.append(("chairman", d.club_id, d.chairman))
    return out


def full(p) -> str:
    return f"{p.first_name} {p.last_name}"


def all_person_names(u: Universe) -> list[str]:
    return [full(p) for _, _, p in people(u)]


def match_people(u: Universe, m: MatchInfo) -> list[tuple[str, object]]:
    """Everyone named for the match: both squads, both clubs' staff and chairmen, the officials."""
    clubs = {t.team_id for t in m.teams}
    out = [("player", p) for p in u.players if p.club_id in clubs]
    out += [("staff", s) for s in u.staff if s.club_id in clubs]
    out += [("chairman", d.chairman) for d in u.club_details if d.club_id in clubs]
    out += [("official", o) for o in u.officials if o.id in {x.official_id for x in m.officials}]
    return out


def all_text(u: Universe, docs: dict[str, str] | None = None, m: MatchInfo | None = None) -> str:
    text = u.model_dump_json()
    if m is not None:
        text += m.model_dump_json()
    if docs:
        text += "\n".join(docs.values())
    return text


# --- Checks -------------------------------------------------------------------------


def check_ids(u: Universe, m: MatchInfo) -> CheckResult:
    problems = []
    for label, ids in (("club", [c.id for c in u.clubs]), ("player", [p.id for p in u.players]),
                       ("staff", [s.id for s in u.staff]), ("official", [o.id for o in u.officials]),
                       ("coach", [c.id for c in u.coaches]), ("league player", [p.id for p in u.league_players])):
        problems += [f"duplicate {label} id {i}" for i, n in Counter(ids).items() if n > 1]
    club_ids = {c.id for c in u.clubs}
    problems += [f"{c.id}: unknown rival {c.rival_id}" for c in u.clubs if c.rival_id and c.rival_id not in club_ids]
    problems += [f"{c.id}/{c.rival_id}: rivalry not mutual" for c in u.clubs
                 if c.rival_id and next(o for o in u.clubs if o.id == c.rival_id).rival_id != c.id]
    refs = [sp.club_id for p in u.players for sp in p.career] + [p.club_id for p in u.players]
    refs += [s.club_id for s in u.staff] + [h.club_id for h in u.coaching_history] + [lp.club_id for lp in u.league_players]
    refs += [x for r in u.roll_of_honour for x in (r.league, r.cup, r.championship, r.super_shield)]
    problems += [f"unknown club {c}" for c in sorted(set(refs) - club_ids)]
    player_ids = {p.id for p in u.players}
    coach_ids = {c.id for c in u.coaches} | {s.id for s in u.staff if s.role == "head_coach"}
    problems += [f"unknown coach {h.coach_id}" for h in u.coaching_history if h.coach_id not in coach_ids]
    problems += [f"spell of {p.id} names unknown coach {c}" for p in u.players for sp in p.career
                 for c in sp.head_coach_ids if c not in coach_ids]
    problems += [f"line-up refers to unknown player {e.player_id}" for t in m.teams for e in t.lineup
                 if e.player_id not in player_ids]
    for d in u.club_details:
        refs = d.set_pieces.penalties + d.set_pieces.free_kicks + d.set_pieces.corners
        problems += [f"{d.club_id}: set-piece taker {x} is not in the squad" for x in refs
                     if x not in player_ids or not x.startswith(d.club_id)]
        problems += [f"{d.club_id}: scorer {g.player_id} is not in the squad" for g_ in d.season_matches
                     for g in g_.scorers if g.player_id not in player_ids or not g.player_id.startswith(d.club_id)]
    staff_ids = {s.id: s for s in u.staff}
    for t in m.teams:
        problems += [f"{t.team_id}: match staff {s.staff_id} unknown or of another club" for s in t.staff
                     if s.staff_id not in staff_ids or staff_ids[s.staff_id].club_id != t.team_id
                     or staff_ids[s.staff_id].role != s.role]
    return _result("References and ids", problems,
                   f"{len(u.clubs)} clubs, {len(u.players)} squad players, {len(u.staff)} staff, {len(u.coaches)} "
                   f"former and other head coaches, {len(u.league_players)} other league players; ids unique and resolved")


def check_competitions(u: Universe) -> CheckResult:
    problems = []
    for comp in u.competitions:
        n = sum(1 for c in u.clubs if c.competition_id == comp.id)
        if n != comp.clubs:
            problems.append(f"{comp.id}: {n} clubs, expected {comp.clubs}")
    return _result("Competitions", problems, ", ".join(f"{c.name}: {c.clubs} clubs" for c in u.competitions))


def check_names(u: Universe, m: MatchInfo) -> CheckResult:
    """Surname rule universe-wide, first names within the match, never an official's first name."""
    problems = []
    reg = people(u)
    names = [full(p).lower() for _, _, p in reg]
    problems += [f"duplicate person {n}" for n, k in Counter(names).items() if k > 1]
    problems += [f"duplicate club {n}" for n, k in Counter(c.name.lower() for c in u.clubs).items() if k > 1]
    by_surname: dict[str, list[tuple[str, str, object]]] = {}
    for kind, ctx, p in reg:
        by_surname.setdefault(p.last_name.lower(), []).append((kind, ctx, p))
    shared = {s: v for s, v in by_surname.items() if len(v) > 1}
    pairs = sum(len(v) * (len(v) - 1) // 2 for v in shared.values())
    match_ids = {id(p) for _, p in match_people(u, m)}
    for s, v in shared.items():
        kinds = {k for k, _, _ in v}
        same_ctx = len({c for _, c, _ in v}) < len(v)
        in_match = sum(1 for _, _, p in v if id(p) in match_ids) > 1
        mixed = "player" in kinds and kinds & {"official", "staff", "coach"}
        if mixed or same_ctx or in_match or pairs > MAX_SHARED_SURNAME_PAIRS:
            problems.append(f"surname {v[0][2].last_name} shared by " + ", ".join(full(p) for _, _, p in v))
    mp = match_people(u, m)
    firsts = Counter(p.first_name.lower() for _, p in mp)
    problems += [f"first name {f} used {k} times in the match" for f, k in firsts.items() if k > 1]
    official_firsts = {o.first_name.lower() for o in u.officials}
    problems += [f"{full(p)} shares an official's first name" for kind, _, p in reg
                 if kind != "official" and p.first_name.lower() in official_firsts]
    nick = Counter(p.nickname.lower() for p in u.players)
    problems += [f"nickname {n} used {k} times" for n, k in nick.items() if k > 1]
    repeated = sum(k - 1 for k in Counter(p.first_name.lower() for _, _, p in reg).values() if k > 1)
    return _result("Names (surnames, first names, nicknames)", problems,
                   f"{len(reg)} people: no surname repeated anywhere; {len(mp)} people of the match with distinct first "
                   f"names, none equal to an official's; {repeated} repeated first names universe-wide (allowed only for "
                   f"coaches and other league players); nicknames unique")


def check_name_schema(u: Universe) -> CheckResult:
    problems = []
    for kind, _, p in people(u):
        data = p.model_dump()
        missing = [f for f in NAME_FIELDS if f not in data]
        if missing:
            problems.append(f"{kind} {full(p)}: missing {missing}")
        if p.title and p.first_name.startswith(p.title):
            problems.append(f"{kind} {full(p)}: title inside the first name")
        if p.nickname and (p.nickname in p.first_name or p.nickname in p.last_name):
            problems.append(f"{kind} {full(p)}: nickname inside the name")
        if any(ch in p.first_name + p.last_name for ch in "'\"()") or p.first_name.endswith("."):
            problems.append(f"{kind} {full(p)}: quote, bracket or title in the name fields")
    kinds = Counter(k for k, _, _ in people(u))
    return _result("Same name schema for every person", problems,
                   f"first_name, last_name, title, nickname for all {sum(kinds.values())} people ("
                   + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")")


def check_blocklist(u: Universe, blocklist: dict, docs: dict[str, str] | None = None) -> CheckResult:
    problems = []
    full_names = {n.lower() for n in blocklist["famous_full_names"]}
    surnames = WordSet(blocklist["famous_surnames"])
    names = all_person_names(u)
    for name in names:
        if name.lower() in full_names:
            problems.append(f"real person: {name}")
    for _, _, p in people(u):
        if surnames.found_in(p.last_name):
            problems.append(f"famous surname in {full(p)}")
    tokens = WordSet(blocklist["real_club_tokens"] + blocklist["real_stadium_tokens"])
    club_and_stadium = [c.name for c in u.clubs] + [c.stadium.name for c in u.clubs] + [s.name for s in u.stadiums]
    problems += [f"real club/stadium token in {t}" for t in club_and_stadium if tokens.found_in(t)]
    brands = WordSet(blocklist["brand_tokens"])
    text = all_text(u, docs)
    found = [b for b in blocklist["brand_tokens"] if contains_word(text, b)] if brands.found_in(text) else []
    problems += [f"brand '{b}' mentioned" for b in found]
    return _result("No real people, clubs or brands", problems,
                   f"{len(names)} names checked against {len(full_names)} full names and "
                   f"{len(blocklist['famous_surnames'])} famous surnames; {len(club_and_stadium)} club and stadium names "
                   "and all text checked against club, stadium and brand lists")


def check_content_safety(u: Universe, blocklist: dict, docs: dict[str, str] | None = None) -> CheckResult:
    # Allowed phrases are prohibitions such as "smoke-free"; they are removed before the search.
    text = all_text(u, docs).lower()
    allowed = blocklist.get("allowed_phrases", [])
    for phrase in allowed:
        text = re.sub(rf"(?<![\w]){re.escape(phrase.lower())}(?![\w])", " ", text)
    words = WordSet(blocklist["forbidden_words"])
    found = sorted({w for w in blocklist["forbidden_words"] if contains_word(text, w)}) if words.found_in(text) else []
    return _result("No alcohol, betting or tobacco promotion", [f"word '{w}'" for w in found],
                   f"{len(blocklist['forbidden_words'])} forbidden words and phrases, none found "
                   f"({len(allowed)} allowed prohibition phrase{'s' if len(allowed) != 1 else ''})")


def check_lineups(u: Universe, m: MatchInfo) -> CheckResult:
    """UNIVERSO-SPEC 1.4: 11 + 9 + 1 captain, no duplicates, no null, English codes, formation, GK."""
    problems = []
    players = {p.id: p for p in u.players}
    for team in m.teams:
        starters = [e for e in team.lineup if e.starting]
        subs = [e for e in team.lineup if not e.starting]
        if len(starters) != 11 or len(subs) != 9:
            problems.append(f"{team.team_id}: {len(starters)} starters, {len(subs)} substitutes (expected 11 and 9)")
        if sum(1 for e in team.lineup if e.captain) != 1 or not any(e.captain for e in starters):
            problems.append(f"{team.team_id}: not exactly one captain among the starters")
        ids = [e.player_id for e in team.lineup]
        problems += [f"{team.team_id}: {i} twice in the line-up" for i, k in Counter(ids).items() if k > 1]
        problems += [f"{team.team_id}: shirt {n} twice" for n, k in Counter(e.shirt_number for e in team.lineup).items() if k > 1]
        for e in team.lineup:
            if e.playing_position is None or e.playing_position not in POSITION_LABELS:
                problems.append(f"{e.player_id}: position {e.playing_position!r} is not an English code")
        if team.formation not in FORMATIONS:
            problems.append(f"{team.team_id}: unknown formation {team.formation}")
        elif sorted(e.playing_position for e in starters) != sorted(FORMATIONS[team.formation]):
            problems.append(f"{team.team_id}: starting positions {sorted(e.playing_position for e in starters)} do not "
                            f"match {team.formation}")
        if sum(1 for e in starters if e.playing_position == "GK") != 1:
            problems.append(f"{team.team_id}: not exactly one goalkeeper in the starting XI")
        if not any(players[e.player_id].position == "GK" for e in subs if e.player_id in players):
            problems.append(f"{team.team_id}: no goalkeeper on the bench")
        if not any(s.role == "head_coach" for s in team.staff):
            problems.append(f"{team.team_id}: no head coach in the match staff")
    for p in u.players:
        if p.position is None or p.position not in POSITION_LABELS or POSITION_GROUP[p.position] != p.position_group:
            problems.append(f"{p.id}: squad position {p.position!r} / group {p.position_group!r} not valid")
    return _result("Line-ups and positions", problems,
                   "per team 11 starters in exactly the formation's English codes, 9 substitutes with their natural "
                   "position and a goalkeeper, one captain, head coach and bench staff listed; no null or DFL code")


def check_squad_lineup_alignment(u: Universe, m: MatchInfo) -> CheckResult:
    """Number, club, position and status aligned between the squad (universe) and the line-up (match)."""
    problems = []
    players = {p.id: p for p in u.players}
    for team in m.teams:
        entries = {e.player_id: e for e in team.lineup}
        squad = [p for p in u.players if p.club_id == team.team_id]
        if len(squad) != 25:
            problems.append(f"{team.team_id}: squad of {len(squad)}")
        problems += [f"{team.team_id}: shirt {n} used twice in the squad" for n, k in
                     Counter(p.shirt_number for p in squad).items() if k > 1]
        for e in team.lineup:
            p = players.get(e.player_id)
            if p is None:
                continue
            if p.club_id != team.team_id:
                problems.append(f"{p.id}: in the {team.team_id} line-up but plays for {p.club_id}")
            if e.shirt_number != p.shirt_number:
                problems.append(f"{p.id}: shirt {e.shirt_number} in the line-up, {p.shirt_number} in the squad")
            if e.starting and POSITION_GROUP[e.playing_position] != p.position_group:
                problems.append(f"{p.id}: starts as {e.playing_position} but is a {p.position_group}")
            if not e.starting and e.playing_position != p.position:
                problems.append(f"{p.id}: substitute listed as {e.playing_position}, natural position {p.position}")
        for p in squad:
            e = entries.get(p.id)
            expected = "not_selected" if e is None else "starting" if e.starting else "substitute"
            if p.match_status != expected:
                problems.append(f"{p.id}: status {p.match_status} in the squad, {expected} in the line-up")
            if (p.match_status == "not_selected") != (p.not_selected_reason is not None):
                problems.append(f"{p.id}: reason for not being selected missing or not expected")
            reason_for = {"injured": "injury", "suspended": "suspension", "ill": "illness"}
            if p.availability.status != "available":
                if p.match_status != "not_selected" or p.not_selected_reason != reason_for[p.availability.status]:
                    problems.append(f"{p.id}: {p.availability.status} but status {p.match_status}/{p.not_selected_reason}")
                if not p.availability.expected_return:
                    problems.append(f"{p.id}: unavailable without an expected return")
            elif p.not_selected_reason not in (None, "coach_decision"):
                problems.append(f"{p.id}: available but not selected for {p.not_selected_reason}")
        caps = [p for p in squad if p.captaincy == "captain"]
        vices = [p for p in squad if p.captaincy == "vice_captain"]
        if len(caps) != 1 or len(vices) != 1:
            problems.append(f"{team.team_id}: {len(caps)} captains and {len(vices)} vice-captains in the squad")
        else:
            wearer = next((e.player_id for e in team.lineup if e.captain), None)
            starters = {e.player_id for e in team.lineup if e.starting}
            want = caps[0].id if caps[0].id in starters else vices[0].id if vices[0].id in starters else wearer
            if wearer != want:
                problems.append(f"{team.team_id}: armband on {wearer}, expected {want}")
    return _result("Squad and line-up aligned", problems,
                   "number, club, position group (starters), natural position (substitutes), status and captaincy agree "
                   "between universe.json and match_info.json; every player not selected has a reason")


def check_people(u: Universe, kickoff: date) -> CheckResult:
    problems = []
    for p in u.players:
        a = kickoff.year - p.date_of_birth.year - ((kickoff.month, kickoff.day) < (p.date_of_birth.month, p.date_of_birth.day))
        if not 16 <= a <= 40:
            problems.append(f"{p.id}: age {a}")
        if not 160 <= p.height_cm <= 205:
            problems.append(f"{p.id}: height {p.height_cm} cm")
        if p.international.goals > p.international.caps or (p.position == "GK" and p.international.goals):
            problems.append(f"{p.id}: {p.international.goals} international goals in {p.international.caps} caps")
    for s in u.staff:
        if not 18 <= s.since - s.born <= 70 or s.since > kickoff.year:
            problems.append(f"{s.id}: joined in {s.since}, born {s.born}")
    for o in u.officials:
        if not 25 <= kickoff.year - o.born <= 50:
            problems.append(f"{o.id}: age {kickoff.year - o.born}")
    for c in u.coaches:
        first = min(season_start(h.from_season) for h in u.coaching_history if h.coach_id == c.id)
        if not 30 <= first - c.born <= 60:
            problems.append(f"{c.id}: first job at {first - c.born}")
    ages = sorted(kickoff.year - p.date_of_birth.year for p in u.players)
    return _result("Ages and physical data", problems,
                   f"players aged about {ages[0]}-{ages[-1]}, heights {min(p.height_cm for p in u.players)}-"
                   f"{max(p.height_cm for p in u.players)} cm; staff, officials and coaches of plausible age")


def check_careers(u: Universe) -> CheckResult:
    """Contiguous spells, moves and fees, loans with their parent club, academy graduates, coaches."""
    problems = []
    current = int(u.meta.season[:4])
    match_clubs = {d.club_id for d in u.club_details}
    for p in u.players:
        spells = p.career
        if not spells or spells[-1].club_id != p.club_id or spells[-1].to_season is not None:
            problems.append(f"{p.id}: career does not end at the current club")
            continue
        expected = season_start(spells[0].from_season)
        debut = expected - p.date_of_birth.year
        if not 16 <= debut <= 21:
            problems.append(f"{p.id}: senior debut at {debut}")
        owner = None
        for i, sp in enumerate(spells):
            start = season_start(sp.from_season)
            end = season_start(sp.to_season) if sp.to_season else current
            if start != expected or end < start:
                problems.append(f"{p.id}: gap or overlap at {sp.from_season}")
            n = end - start + 1
            if [s.season for s in sp.seasons] != [season_label(y) for y in range(start, end + 1)]:
                problems.append(f"{p.id}: seasons of the {sp.club_id} spell do not match {sp.from_season}-{sp.to_season}")
            if (sp.appearances, sp.goals) != (sum(s.appearances for s in sp.seasons), sum(s.goals for s in sp.seasons)):
                problems.append(f"{p.id}: {sp.club_id} spell totals differ from the sum of its seasons")
            for s in sp.seasons:
                if s.appearances > 50 or s.goals > s.appearances or (p.position == "GK" and s.goals > 0):
                    problems.append(f"{p.id}: {s.goals} goals in {s.appearances} appearances in {s.season}")
            if sp.appearances > 50 * n or sp.goals > sp.appearances or (p.position == "GK" and sp.goals > 0):
                problems.append(f"{p.id}: {sp.goals} goals in {sp.appearances} appearances over {n} seasons")
            if sp.move == "transfer" and sp.fee_eur <= 0:
                problems.append(f"{p.id}: transfer to {sp.club_id} without a fee")
            if sp.move != "transfer" and sp.fee_eur != 0:
                problems.append(f"{p.id}: {sp.move} to {sp.club_id} with a fee of {sp.fee_eur}")
            if sp.move == "academy" and i != 0:
                problems.append(f"{p.id}: academy promotion after the first spell")
            if i == 0 and sp.move not in ("academy",):
                problems.append(f"{p.id}: first spell is a {sp.move}")
            if sp.move == "loan":
                if sp.parent_club_id != owner or sp.parent_club_id is None:
                    problems.append(f"{p.id}: loan to {sp.club_id} owned by {sp.parent_club_id}, expected {owner}")
            elif sp.parent_club_id is not None and sp.move != "loan_return":
                problems.append(f"{p.id}: parent club on a {sp.move}")
            if sp.move == "loan_return" and (i == 0 or spells[i - 1].move != "loan" or sp.club_id != owner):
                problems.append(f"{p.id}: return from loan to {sp.club_id} without a loan from it")
            if sp.move != "loan":
                owner = sp.club_id
            if sp.club_id != p.club_id and sp.club_id in match_clubs:
                problems.append(f"{p.id}: earlier spell at a match club ({sp.club_id})")
            want = coaches_during(u.coaching_history, sp.club_id, start, end)
            if sp.head_coach_ids != want:
                problems.append(f"{p.id}: coaches of the {sp.club_id} spell differ from the coaching history")
            expected = end + 1
        if expected != current + 1:
            problems.append(f"{p.id}: career does not reach the current season")
        cur = spells[-1]
        if cur.appearances < p.this_season.appearances or cur.goals < p.this_season.goals:
            problems.append(f"{p.id}: current spell smaller than this season's numbers")
        academy = spells[0].club_id == p.club_id and spells[0].move == "academy"
        if p.academy_graduate != academy:
            problems.append(f"{p.id}: academy_graduate={p.academy_graduate} but the career starts with "
                            f"{spells[0].move} at {spells[0].club_id}")
    moves = Counter(sp.move for p in u.players for sp in p.career)
    fees = [sp.fee_eur for p in u.players for sp in p.career if sp.move == "transfer"]
    return _result("Careers, transfers and academy graduates", problems,
                   f"{sum(moves.values())} spells (" + ", ".join(f"{k} {v}" for k, v in sorted(moves.items()))
                   + f"): contiguous, end at the current club, loans with their parent club; fees in euros "
                   f"{min(fees):,}-{max(fees):,} for transfers and 0 otherwise; academy graduates start at their club")


def check_coaching_history(u: Universe) -> CheckResult:
    problems = []
    current = int(u.meta.season[:4])
    for club in u.clubs:
        spells = sorted((h for h in u.coaching_history if h.club_id == club.id), key=lambda h: h.from_season)
        year = season_start(spells[0].from_season) if spells else None
        for h in spells:
            if season_start(h.from_season) != year:
                problems.append(f"{club.id}: coaching gap or overlap at {h.from_season}")
            year = (season_start(h.to_season) if h.to_season else current) + 1
        if not spells or spells[-1].to_season is not None:
            problems.append(f"{club.id}: no current head coach")
    busy: dict[str, list[tuple[int, int]]] = {}
    for h in u.coaching_history:
        busy.setdefault(h.coach_id, []).append((season_start(h.from_season),
                                                season_start(h.to_season) if h.to_season else current))
    for c, spans in busy.items():
        spans.sort()
        problems += [f"{c}: two clubs at once in {season_label(b[0])}" for a, b in zip(spans, spans[1:]) if b[0] <= a[1]]
    for s in u.staff:
        if s.role == "head_coach":
            cur = next((h for h in u.coaching_history if h.club_id == s.club_id and h.to_season is None), None)
            if cur is None or cur.coach_id != s.id or season_start(cur.from_season) != s.since:
                problems.append(f"{s.club_id}: head coach {full(s)} not current in the coaching history since {s.since}")
    return _result("Coaching history", problems,
                   f"{len(u.coaching_history)} spells: every club has one head coach per season and one now; no coach at "
                   "two clubs at once; the match clubs' head coaches match their staff entry")


def check_honours(u: Universe) -> CheckResult:
    """Player honours follow from the roll of honour and the spells; club honours agree with the roll."""
    problems = []
    roll = {r.season: r for r in u.roll_of_honour}
    first_season = min(roll)
    for p in u.players:
        expected = set()
        for sp in p.career:
            start = season_start(sp.from_season)
            end = season_start(sp.to_season) if sp.to_season else CURRENT_START
            for y in range(start, end + 1):
                r = roll.get(season_label(y))
                if r is None:
                    continue
                for key in ("league", "cup", "championship"):
                    if getattr(r, key) == sp.club_id:
                        expected.add((COMPETITION_NAMES[key], r.season, sp.club_id))
                if r.super_shield == sp.club_id:
                    expected.add((COMPETITION_NAMES["super_shield"], str(y), sp.club_id))
        got = {(h.competition, h.season, h.club_id) for h in p.honours}
        if got != expected:
            problems.append(f"{p.id}: honours {sorted(got ^ expected)} do not follow from the career")
    for d in u.club_details:
        listed = {(h.competition, s) for h in d.honours for s in h.seasons}
        for r in u.roll_of_honour:
            for key in ("league", "cup", "championship"):
                if getattr(r, key) == d.club_id and (COMPETITION_NAMES[key], r.season) not in listed:
                    problems.append(f"{d.club_id}: {COMPETITION_NAMES[key]} {r.season} in the roll but not in the honours")
            if r.super_shield == d.club_id and (COMPETITION_NAMES["super_shield"], r.season[:4]) not in listed:
                problems.append(f"{d.club_id}: Super Shield {r.season[:4]} in the roll but not in the honours")
        for comp, s in listed:
            if s[:4] < first_season[:4]:
                continue
            key = next(k for k, v in COMPETITION_NAMES.items() if v == comp)
            label = s if key != "super_shield" else season_label(int(s))
            r = roll.get(label)
            if r is None or getattr(r, key) != d.club_id:
                problems.append(f"{d.club_id}: {comp} {s} in the honours but not in the roll")
    n = sum(len(p.honours) for p in u.players)
    return _result("Trophies of players and clubs", problems,
                   f"{n} player honours, all from a spell at the winning club in that season; the match clubs' honours "
                   f"agree with the roll of honour since {first_season}")


def check_results_and_standings(u: Universe) -> CheckResult:
    problems = []
    for st in u.standings:
        res = [r for r in u.results if r.competition_id == st.competition_id and r.round <= st.after_round]
        for rnd in sorted({r.round for r in res}):
            teams = [t for r in res if r.round == rnd for t in (r.home_id, r.away_id)]
            if len(teams) != len(set(teams)):
                problems.append(f"{st.competition_id} round {rnd}: a club plays twice")
        pairs = Counter(frozenset((r.home_id, r.away_id)) for r in res)
        problems += [f"{st.competition_id}: {sorted(p)} met {k} times" for p, k in pairs.items() if k > 1]
        for row in st.rows:
            if row.won + row.drawn + row.lost != row.played or row.points != 3 * row.won + row.drawn:
                problems.append(f"{row.club_id}: inconsistent row")
            if row.goal_difference != row.goals_for - row.goals_against or row.played != st.after_round:
                problems.append(f"{row.club_id}: goals or games played inconsistent")
        if sum(r.goals_for for r in st.rows) != sum(r.goals_against for r in st.rows):
            problems.append(f"{st.competition_id}: goals scored != goals conceded")
        if sum(r.won for r in st.rows) != sum(r.lost for r in st.rows):
            problems.append(f"{st.competition_id}: wins != losses")
        keys = [(-r.points, -r.goal_difference, -r.goals_for) for r in st.rows]
        if keys != sorted(keys):
            problems.append(f"{st.competition_id}: table not sorted")
    return _result("Results and standings", problems,
                   f"{len(u.results)} results; every club plays once per round, points = 3W + D, goals for = goals against")


def check_no_concurrent_matches(u: Universe, m: MatchInfo, docs: dict[str, str] | None) -> CheckResult:
    """Kit Man only covers the match of the ticket: no other game of the same matchday anywhere (question 12)."""
    problems = []
    same_day = [r for r in u.results if r.competition_id == m.competition_id and r.round >= m.matchday]
    problems += [f"result of matchday {r.round}: {r.home_id} v {r.away_id}" for r in same_day]
    problems += [f"fixture {f.home_id} v {f.away_id} on matchday {f.round}" for f in u.fixtures if f.round <= m.matchday]
    if m.result is not None:
        problems.append("result already set before the match is generated")
    if docs:
        # A line about matchday N that names a club other than the two of the match would be a concurrent game.
        pattern = re.compile(rf"matchday {m.matchday}\b", re.IGNORECASE)
        others = [c.name for c in u.clubs if c.id not in (m.home_team_id, m.guest_team_id)]
        for name, text in docs.items():
            for line in text.splitlines():
                if pattern.search(line) and any(o in line for o in others):
                    problems.append(f"{name}: matchday {m.matchday} line names another club: '{line.strip()[:80]}'")
    return _result("Only the match of the ticket", problems,
                   f"results up to matchday {m.matchday - 1}, fixtures from matchday {m.matchday + 1}; no other game of "
                   f"matchday {m.matchday} in the data or in the documents")


def check_season_numbers(u: Universe) -> CheckResult:
    problems = []
    details = {d.club_id: d for d in u.club_details}
    for club_id in sorted({p.club_id for p in u.players}):
        squad = [p for p in u.players if p.club_id == club_id]
        res = [r for r in u.results if club_id in (r.home_id, r.away_id)]
        team_goals = sum(r.home_goals if r.home_id == club_id else r.away_goals for r in res)
        if sum(p.this_season.goals for p in squad) != team_goals:
            problems.append(f"{club_id}: players scored {sum(p.this_season.goals for p in squad)}, team {team_goals}")
        if sum(p.this_season.starts for p in squad) != 11 * len(res):
            problems.append(f"{club_id}: {sum(p.this_season.starts for p in squad)} starts in {len(res)} games")
        if sum(p.this_season.minutes for p in squad) > 990 * len(res):
            problems.append(f"{club_id}: more than 990 minutes per game")
        if sum(p.this_season.assists for p in squad) > team_goals:
            problems.append(f"{club_id}: more assists than goals")
        for p in squad:
            ts = p.this_season
            if ts.appearances > len(res) or ts.starts > ts.appearances or ts.minutes > 90 * ts.appearances:
                problems.append(f"{p.id}: {ts.appearances} apps, {ts.starts} starts, {ts.minutes} minutes")
            if len(p.form) != 5 or [f.round for f in p.form] != list(range(len(res) - 4, len(res) + 1)):
                problems.append(f"{p.id}: form guide is not the last five games")
        log = details[club_id].season_matches
        for g in log:
            r = next(r for r in res if r.round == g.round)
            gf = r.home_goals if r.home_id == club_id else r.away_goals
            if len(g.scorers) != gf or g.goals_for != gf:
                problems.append(f"{club_id} matchday {g.round}: {len(g.scorers)} scorers for {gf} goals")
        per_player = Counter(s.player_id for g in log for s in g.scorers)
        problems += [f"{p.id}: {per_player[p.id]} goals in the match log, {p.this_season.goals} in the stats" for p in squad
                     if per_player[p.id] != p.this_season.goals]
    return _result("Current-season numbers", problems,
                   "players' goals equal the team's goals and the match logs; 11 starts per game; at most 90 minutes "
                   "per appearance; assists never exceed goals; form guide = last five games")


def check_head_to_head(u: Universe) -> CheckResult:
    problems = []
    for h in u.head_to_head:
        lm = h.last_meeting
        if (lm.season, lm.home_id, lm.away_id, lm.home_goals, lm.away_goals) != (
                h.last_meetings[-1].season, h.last_meetings[-1].home_id, h.last_meetings[-1].away_id,
                h.last_meetings[-1].home_goals, h.last_meetings[-1].away_goals):
            problems.append("last meeting differs from the last of the meetings")
        players = {p.id: p for p in u.players}
        year = season_start(lm.season)
        for side, goals in ((lm.home_id, lm.home_goals), (lm.away_id, lm.away_goals)):
            mine = [g for g in lm.scorers if players[g.player_id].club_id == side]
            if len(mine) != goals:
                problems.append(f"{side}: {len(mine)} scorers for {goals} goals in the last derby")
            for pid, k in Counter(g.player_id for g in mine).items():
                p = players[pid]
                sp = next((s for s in p.career if s.club_id == side and season_start(s.from_season) <= year
                           and (s.to_season is None or season_start(s.to_season) >= year)), None)
                if sp is None or sp.goals < k:
                    problems.append(f"{pid}: scored in the {lm.season} derby but was not at {side} then (or too few goals)")
        a, b = h.club_a, h.club_b
        if h.all_time["played"] != h.all_time[f"{a}_wins"] + h.all_time[f"{b}_wins"] + h.all_time["draws"]:
            problems.append("all-time record does not add up")
    return _result("Head-to-head", problems, "all-time record adds up; the last derby's scorers were at the club "
                   "that season and are counted in their career numbers")


def check_top_scorers(u: Universe) -> CheckResult:
    problems = []
    goals = {p.id: p.this_season.goals for p in u.players} | {lp.id: lp.goals for lp in u.league_players}
    clubs = {p.id: p.club_id for p in u.players} | {lp.id: lp.club_id for lp in u.league_players}
    for t in u.top_scorers:
        table = {r.club_id: r for st in u.standings if st.competition_id == t.competition_id for r in st.rows}
        for r in t.rows:
            if goals.get(r.player_id) != r.goals or clubs.get(r.player_id) != r.club_id:
                problems.append(f"{r.player_id}: {r.goals} goals in the table, {goals.get(r.player_id)} in the data")
        for i, r in enumerate(t.rows):
            if r.rank != 1 + sum(1 for o in t.rows if o.goals > r.goals):
                problems.append(f"{r.player_id}: wrong rank {r.rank}")
        best_out = max((g for pid, g in goals.items() if pid not in {r.player_id for r in t.rows}
                        and clubs[pid] in table), default=0)
        if t.rows and best_out > t.rows[-1].goals:
            problems.append(f"a player with {best_out} goals is missing from the table")
        for lp in u.league_players:
            if lp.goals > table[lp.club_id].goals_for:
                problems.append(f"{lp.id}: {lp.goals} goals, club scored {table[lp.club_id].goals_for}")
    return _result("Top scorers", problems, "table built from the squads' numbers and one leading scorer per other club, "
                   "never more goals than the club scored")


def check_stadium(u: Universe, m: MatchInfo) -> CheckResult:
    problems = []
    for s in u.stadiums:
        sectors = {sec.id: sec for st in s.stands for sec in st.sectors}
        total = sum(sec.capacity for sec in sectors.values())
        if total != s.capacity:
            problems.append(f"{s.id}: sectors add up to {total}, capacity {s.capacity}")
        served = [x for g in s.gates for x in g.serves]
        problems += [f"sector {x} served by {k} gates" for x, k in Counter(served).items() if k > 1]
        problems += [f"sector {x} has no gate" for x in sectors if x not in served]
        problems += [f"gate or service refers to unknown sector {x}" for x in
                     set(served) | {x for sv in s.services for x in sv.near} if x not in sectors]
        if s.record_attendance.value > s.capacity:
            problems.append("record attendance above capacity")
        club = next(c for c in u.clubs if c.id == s.club_id)
        if club.stadium.capacity != s.capacity or club.stadium.name != s.name:
            problems.append(f"{club.id}: stadium name or capacity differs from the club entry")
    stadium = next(s for s in u.stadiums if s.id == m.environment.stadium_id)
    if m.environment.stadium_capacity != stadium.capacity:
        problems.append(f"match capacity {m.environment.stadium_capacity:,} differs from the stadium's {stadium.capacity:,}")
    if m.environment.tickets_sold > m.environment.stadium_capacity:
        problems.append("more tickets sold than seats")
    if (m.environment.pitch_x_m, m.environment.pitch_y_m) != (stadium.pitch.length_m, stadium.pitch.width_m):
        problems.append("match pitch size differs from the stadium")
    return _result("Stadium", problems, f"{stadium.name}: {stadium.capacity:,} seats in "
                   f"{sum(len(st.sectors) for st in stadium.stands)} sectors, every sector has exactly one gate; "
                   f"match information: the same capacity, {m.environment.tickets_sold:,} tickets sold")


def check_match(u: Universe, m: MatchInfo, root: Path | None = None) -> CheckResult:
    """Officials, competition, stadium and forecast of the match, and the crest and kit files each team cites
    (paths relative to `root`, the repository root by default; the files are drawn by `crests.py`)."""
    root = REPO_ROOT if root is None else root
    problems = []
    roles = Counter(o.role for o in m.officials)
    if len(roles) != 6 or any(k != 1 for k in roles.values()):
        problems.append(f"officials: {dict(roles)}")
    officials = {o.id: o for o in u.officials}
    for mo in m.officials:
        o = officials[mo.official_id]
        is_ref = mo.role in ("referee", "fourth_official", "video_assistant_referee")
        if is_ref != (o.specialism == "referee") or is_ref != (o.career_cards is not None and o.season_cards is not None):
            problems.append(f"{o.id}: {mo.role} with specialism {o.specialism} and card stats {o.career_cards is not None}")
    for cid in (m.home_team_id, m.guest_team_id):
        if next(c for c in u.clubs if c.id == cid).competition_id != m.competition_id:
            problems.append(f"{cid} is not in the match competition")
    stadium = next(s for s in u.stadiums if s.id == m.environment.stadium_id)
    if stadium.club_id != m.home_team_id:
        problems.append("stadium is not the home club's")
    times = [f.time for f in m.environment.forecast]
    if times != sorted(times) or m.environment.forecast[0].time != m.planned_kickoff[11:16]:
        problems.append("forecast does not start at kick-off or is not in time order")
    for t in m.teams:
        cited = {"crest": (t.crest_file, crest_file(t.team_id)),
                 "home kit": (t.kit_files.home, kit_file(t.team_id, "home")),
                 "away kit": (t.kit_files.away, kit_file(t.team_id, "away"))}
        for what, (path, expected) in cited.items():
            if path != expected:
                problems.append(f"{t.team_id}: {what} file {path}, expected {expected}")
            elif not (root / path).is_file():
                problems.append(f"{t.team_id}: {what} file {path} is missing (run python -m insidethegame_gen.crests)")
    return _result("Match information", problems,
                   f"{m.match_id}: matchday {m.matchday}, 6 officials with English roles (card numbers for the three "
                   f"referees), forecast from kick-off to full time, crest and both kits of each team on file, "
                   f"no result yet")


def check_must_data(u: Universe, m: MatchInfo) -> CheckResult:
    """The Must and Should data of UNIVERSO-SPEC section 2 are present for everyone who needs them."""
    problems = []
    for p in u.players:
        if not (p.birthplace.town and p.nickname and p.nickname_origin and p.playing_style and len(p.strengths) >= 2
                and 1 <= len(p.curiosities) <= 2):
            problems.append(f"{p.id}: profile incomplete")
        if any("{" in t or "}" in t for t in [p.nickname, p.nickname_origin, *p.curiosities]):
            problems.append(f"{p.id}: unfilled placeholder")
    for d in u.club_details:
        if not (d.chairman and d.crest and d.colour_names and d.records and d.head_coach.career
                and len(d.season_matches) >= 5 and d.set_pieces.penalties):
            problems.append(f"{d.club_id}: club details incomplete")
        if d.head_coach.staff_id not in {s.id for s in u.staff if s.role == "head_coach" and s.club_id == d.club_id}:
            problems.append(f"{d.club_id}: head-coach profile refers to {d.head_coach.staff_id}")
    if not (u.match_preview.programme and len(u.match_preview.team_news) == 2 and len(u.match_preview.key_duels) >= 2):
        problems.append("match preview incomplete")
    if not u.fixtures or not u.top_scorers or not u.stadiums[0].notable_matches:
        problems.append("next round, top scorers or stadium history missing")
    players = {p.id: p for p in u.players}
    for duel in u.match_preview.key_duels:
        for pid, club in ((duel.home_player_id, m.home_team_id), (duel.guest_player_id, m.guest_team_id)):
            if pid not in players or players[pid].club_id != club or players[pid].match_status != "starting":
                problems.append(f"key duel player {pid} is not a {club} starter")
    return _result("Data for Kit Man (spec section 2)", problems,
                   "every player has birthplace, nickname and its origin, playing style, strengths, 1-2 curiosities, "
                   "season numbers, form, availability and caps; clubs have chairman, crest, colours, kits, records, "
                   "honours, coach profile with job security, set-piece takers and the last games with scorers; "
                   "officials have experience and cards per game; programme, team news, key duels, forecast, next round "
                   "and top scorers exist")


def section(text: str, heading: str) -> str:
    """The body of a Markdown section (up to the next heading of the same or higher level)."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return ""
    out = []
    for line in lines[start:]:
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= level:
            break
        out.append(line)
    return "\n".join(out)


def check_documents(u: Universe, m: MatchInfo, docs: dict[str, str] | None) -> CheckResult:
    """No substitute in the starters section and every listed player in the right section, with his position."""
    problems = []
    if not docs:
        return _result("Kit Man documents", ["no documents rendered"], "")
    players = {p.id: p for p in u.players}
    text = docs["matchday.md"]
    for team in m.teams:
        club = next(c for c in u.clubs if c.id == team.team_id)
        xi = section(text, f"### {club.name}: starting XI ({team.formation})")
        subs = section(text, f"### {club.name}: substitutes")
        found_xi = set(re.findall(r"^- #(\d+) ", xi, re.MULTILINE))
        found_subs = set(re.findall(r"^- #(\d+) ", subs, re.MULTILINE))
        want_xi = {str(e.shirt_number) for e in team.lineup if e.starting}
        want_subs = {str(e.shirt_number) for e in team.lineup if not e.starting}
        if found_xi != want_xi:
            problems.append(f"{team.team_id}: starting XI section lists {sorted(found_xi ^ want_xi)} wrongly")
        if found_subs != want_subs:
            problems.append(f"{team.team_id}: substitutes section lists {sorted(found_subs ^ want_subs)} wrongly")
        for e in team.lineup:
            p = players[e.player_id]
            line = f"- #{e.shirt_number} {full(p)}, {POSITION_LABELS[e.playing_position]}"
            if line not in (xi if e.starting else subs):
                problems.append(f"{p.id}: line '{line}' missing")
    for p in u.players:
        if f"## #{p.shirt_number} {full(p)}\n" not in docs[f"squad-{p.club_id}.md"]:
            problems.append(f"{p.id}: no section in squad-{p.club_id}.md")
    for name, body in docs.items():
        if not body.startswith("---\ndoc_id: ") or f"match_id: {m.match_id}\n" not in body:
            problems.append(f"{name}: front matter missing")
        if re.search(r"\b(TW|IVL|IVR|RV|LV|DML|DMR|ZO|OLM|ORM|STZ|STL|STR|HL|HR)\b", body):
            problems.append(f"{name}: a DFL position code appears")
    return _result("Kit Man documents", problems,
                   f"{len(docs)} documents with front matter; matchday.md has separate Starting XI and Substitutes "
                   "sections with the right players and positions; every player has a section; no DFL codes")


def check_games(u: Universe, m: MatchInfo) -> CheckResult:
    """Tiki-Taka-Toe (UNIVERSO-SPEC 3): crossings of career clubs and nationalities with at least 2 answers."""
    problems = []
    excluded = {m.home_team_id, m.guest_team_id}
    squad = [p for p in u.players if p.club_id in excluded]
    crit = games.criteria(squad, excluded)
    foreign = games.foreign_nationalities(squad, u.meta.country, MIN_PLAYERS_PER_NATIONALITY)
    if len(foreign) < MIN_FOREIGN_NATIONALITIES:
        problems.append(f"only {len(foreign)} foreign nationalities with {MIN_PLAYERS_PER_NATIONALITY}+ players: {foreign}")
    adj = games.valid_pairs(crit, MIN_ANSWERS)
    for nat in foreign:
        clubs = sorted(c[1] for c in adj[("nationality", nat)])
        if len(clubs) < MIN_CLUBS_PER_NATIONALITY:
            problems.append(f"{nat} crosses only {len(clubs)} clubs with {MIN_ANSWERS}+ answers")
    n_grids = games.count_grids(crit, MIN_ANSWERS)
    n_nat = games.count_grids(crit, MIN_ANSWERS, with_nationality=True)
    if n_grids < MIN_GRIDS:
        problems.append(f"only {n_grids} valid 3x3 grids (minimum {MIN_GRIDS})")
    if n_nat < MIN_GRIDS_WITH_NATIONALITY:
        problems.append(f"only {n_nat} valid grids with a nationality (minimum {MIN_GRIDS_WITH_NATIONALITY})")
    if any(sp.club_id in excluded for p in squad for sp in p.career if sp.club_id != p.club_id):
        problems.append("a former club is one of the two match clubs")
    pairs = sum(len(v) for v in adj.values()) // 2
    clubs = sum(1 for k in crit if k[0] == "club")
    return _result("Games: Tiki-Taka-Toe crossings", problems,
                   f"{clubs} career clubs and {len(foreign)} foreign nationalities with {MIN_PLAYERS_PER_NATIONALITY}+ "
                   f"players (" + ", ".join(f"{n} {k}" for n, k in foreign.items()) + f"); {pairs} crossings with "
                   f"{MIN_ANSWERS}+ answers among the {len(squad)} players; {n_grids:,} valid 3x3 grids, {n_nat:,} of "
                   "them with a nationality")


def check_attributes(u: Universe, scales: dict | None = None) -> CheckResult:
    """Attributes 1-99: all six for every player, each with a DFL calibration table for his group (or the
    documented fallback), mapped values inside the physical limits, and no number below what the player's
    strengths or main style promise (attributes.floors_for)."""
    name = "Attributes 1-99"
    if scales is None:
        try:
            scales = load_scales()
        except (OSError, ValueError) as exc:
            return _result(name, [f"cannot read {SCALES_FILE}: {exc}"], "")
    problems: list[str] = []
    meta = scales.get("meta") or {}
    if "DFL" not in str(meta.get("source", "")) or not meta.get("matches_used"):
        problems.append(f"{SCALES_FILE} has no DFL source or no matches_used")
    sources: dict[tuple[str, str], str] = {}
    missing: set[tuple[str, str]] = set()
    speeds: list[float] = []
    n_floors = 0
    for p in u.players:
        if p.attributes_status != "calibrated":
            problems.append(f"{p.id} attributes_status is {p.attributes_status}")
        values = p.attributes.model_dump()
        ok = {}
        for a in ATTRIBUTES:
            v = values.get(a)
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 99:
                problems.append(f"{p.id} {a} = {v!r}, not an integer in 1-99")
                continue
            found = scale_table(scales, a, p.position_group)
            if found is None:
                missing.add((a, p.position_group))
                continue
            sources[(a, p.position_group)] = found[1]
            ok[a] = v
        if "pace" in ok:
            kmh = physical_value(scales, "pace", p.position_group, ok["pace"])
            speeds.append(kmh)
            if kmh > PLAYER_SPEED_MAX_KMH:
                problems.append(f"{p.id} pace {ok['pace']} maps to {kmh:.1f} km/h, above {PLAYER_SPEED_MAX_KMH:g}")
        if "shot_power" in ok:
            ms = physical_value(scales, "shot_power", p.position_group, ok["shot_power"])
            if ms > BALL_SPEED_MAX_MS:
                problems.append(f"{p.id} shot power maps to {ms:.1f} m/s, above {BALL_SPEED_MAX_MS:.1f}")
        for a, (floor, why) in floors_for(p.position_group, p.attribute_profile.style, p.strengths).items():
            n_floors += 1
            if a in ok and ok[a] < floor:
                problems.append(f"{p.id} {a} {ok[a]} is below {floor}, promised by his {why}")
    problems += [f"no calibration table for {a} in group {g}" for a, g in sorted(missing)]
    borrowed: dict[tuple[str, str], list[str]] = {}
    for (a, g), src in sorted(sources.items()):
        if src != g and a != "shot_power":
            borrowed.setdefault((g, src), []).append(a)
    detail = (f"{len(u.players)} players x {len(ATTRIBUTES)} attributes in 1-99, each a percentile of the player's "
              f"position group in {SCALES_FILE} (DFL, {len(meta.get('matches_used') or [])} matches, config "
              f"{meta.get('config_hash', '?')}); tables borrowed: "
              + "; ".join(f"{g} {', '.join(v)} from the {src}" for (g, src), v in borrowed.items())
              + "; shot power has one overall scale; "
              + (f"top speeds {min(speeds):.1f}-{max(speeds):.1f} km/h; " if speeds else "")
              + f"{n_floors} promises of strengths and styles kept")
    return _result(name, problems, detail)


def run_all(u: Universe, m: MatchInfo, blocklist: dict, docs: dict[str, str] | None = None,
            scales: dict | None = None) -> list[CheckResult]:
    kickoff = date.fromisoformat(m.planned_kickoff[:10])
    return [
        check_ids(u, m),
        check_competitions(u),
        check_names(u, m),
        check_name_schema(u),
        check_blocklist(u, blocklist, docs),
        check_content_safety(u, blocklist, docs),
        check_lineups(u, m),
        check_squad_lineup_alignment(u, m),
        check_people(u, kickoff),
        check_careers(u),
        check_coaching_history(u),
        check_honours(u),
        check_results_and_standings(u),
        check_no_concurrent_matches(u, m, docs),
        check_season_numbers(u),
        check_head_to_head(u),
        check_top_scorers(u),
        check_stadium(u, m),
        check_match(u, m),
        check_must_data(u, m),
        check_documents(u, m, docs),
        check_games(u, m),
        check_attributes(u, scales),
    ]
