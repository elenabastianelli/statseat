"""Careers, transfer fees, the coaching history and the honours of players.

- A career is a list of contiguous spells from the senior debut to the current club.
  Each spell says how the player arrived (academy promotion, transfer, free transfer,
  loan, return from loan) and the fee in euros (0 except for transfers).
- Earlier clubs never include the two match clubs, so every former club can be a
  Tiki-Taka-Toe criterion. Some clubs sign more than others (hub clubs) and some have
  a scouting network in one country, so crossings have several answers (UNIVERSO-SPEC 3).
- The coaching history gives every club a head coach in every season since the registry
  starts, so each spell can name the coaches the player worked with.
- Player honours follow from the roll of honour and the spells: nobody wins a trophy
  with a club he was not at.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .schema import COMPETITION_NAMES, CareerSpell, Club, CoachingSpell, PlayerHonour, RollOfHonourSeason, SpellSeason
from .season import GOAL_RATE, Member

CURRENT_START = 2026
REGISTRY_START = 2005


def season_label(start_year: int) -> str:
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def season_start(label: str) -> int:
    return int(label[:4])


def round_fee(eur: float) -> int:
    if eur < 1_000_000:
        step = 50_000
    elif eur < 10_000_000:
        step = 100_000
    else:
        step = 500_000
    return max(step, int(round(eur / step)) * step)


def transfer_fee(rng: random.Random, quality: float, age: int, dest: Club) -> int:
    """Plausible fee for a move to `dest`: higher for stronger top-division clubs, peak age 23-27."""
    base = 14_000_000 * dest.strength ** 2 if dest.competition_id == "MER-1" else 2_500_000 * dest.strength
    if age <= 20:
        age_f = 0.55
    elif age <= 27:
        age_f = 1.0
    else:
        age_f = max(0.3, 1.0 - 0.13 * (age - 27))
    return round_fee(base * (0.4 + quality) * age_f * rng.uniform(0.6, 1.4))


# --- Coaching history ----------------------------------------------------------


@dataclass
class NewCoach:
    id: str
    nationality: str
    born: int


def build_coaching_history(rng: random.Random, clubs: list[Club], fixed: list[dict], new_coach) -> list[CoachingSpell]:
    """Head coach of every club for every season from REGISTRY_START to now.

    `fixed` spells are placed first (the head coaches of the match clubs); `new_coach(year)`
    returns the id of a newly created coach. Coaches move between clubs, never two at once.
    """
    busy: dict[str, list[tuple[int, int]]] = {}
    for f in fixed:
        end = season_start(f["to_season"]) if f["to_season"] else CURRENT_START
        busy.setdefault(f["coach_id"], []).append((season_start(f["from_season"]), end))
    out: list[CoachingSpell] = []
    for club in sorted(clubs, key=lambda c: c.id):
        own = sorted((f for f in fixed if f["club_id"] == club.id), key=lambda f: f["from_season"])
        year = REGISTRY_START
        while year <= CURRENT_START:
            f = next((x for x in own if season_start(x["from_season"]) == year), None)
            if f:
                out.append(CoachingSpell(**f))
                year = (season_start(f["to_season"]) if f["to_season"] else CURRENT_START) + 1
                continue
            next_fixed = min((season_start(x["from_season"]) for x in own if season_start(x["from_season"]) > year),
                             default=CURRENT_START + 1)
            length = min(rng.choice([2, 2, 3, 3, 3, 4, 4, 5, 5, 6]), next_fixed - year)
            end = year + length - 1
            free = sorted(c for c, spans in busy.items() if c.startswith("COA-")
                          and all(e < year or s > end for s, e in spans) and max(e for _, e in spans) < year)
            coach = rng.choice(free) if free and rng.random() < 0.6 else new_coach(year)
            busy.setdefault(coach, []).append((year, end))
            out.append(CoachingSpell(club_id=club.id, coach_id=coach, from_season=season_label(year),
                                     to_season=None if end == CURRENT_START else season_label(end)))
            year = end + 1
    return out


def coaches_during(history: list[CoachingSpell], club_id: str, start: int, end: int) -> list[str]:
    ids = []
    for h in history:
        if h.club_id != club_id:
            continue
        hs, he = season_start(h.from_season), season_start(h.to_season) if h.to_season else CURRENT_START
        if hs <= end and he >= start and h.coach_id not in ids:
            ids.append(h.coach_id)
    return ids


# --- Careers -------------------------------------------------------------------


def _split(rng: random.Random, total: int, parts: int) -> list[int]:
    """Random composition of `total` seasons into `parts` positive lengths."""
    cuts = sorted(rng.sample(range(1, total), parts - 1)) if parts > 1 else []
    edges = [0] + cuts + [total]
    return [b - a for a, b in zip(edges, edges[1:])]


def build_career(rng: random.Random, m: Member, clubs: list[Club], weights: dict[str, float], planned: list[str],
                 excluded: set[str], rounds: dict[str, int], history: list[CoachingSpell]) -> list[CareerSpell]:
    club_by_id = {c.id: c for c in clubs}
    first_year = min(m.dob.year + rng.randint(17, 20), CURRENT_START if m.academy else CURRENT_START - 1)
    if m.tier == "prospect":
        first_year = max(first_year, CURRENT_START - 1)
    seasons = list(range(first_year, CURRENT_START + 1))
    candidates = sorted(c.id for c in clubs if c.id not in excluded)

    def draw(avoid: set[str], second_division: bool = False) -> str:
        pool = [c for c in candidates if c not in avoid]
        w = [weights.get(c, 1.0) * (1.6 if second_division and club_by_id[c].competition_id == "MER-2" else 1.0)
             for c in pool]
        return rng.choices(pool, weights=w)[0]

    # Ownership blocks: (club, seasons, move, parent)
    blocks: list[tuple[str, list[int], str, str | None]] = []
    if m.academy:
        blocks.append((m.club_id, seasons, "academy", None))
        if len(seasons) >= 3 and rng.random() < 0.5:
            i = rng.randint(1, len(seasons) - 2)
            loan_club = draw(set(), second_division=True)
            blocks = [(m.club_id, seasons[:i], "academy", None), (loan_club, [seasons[i]], "loan", m.club_id),
                      (m.club_id, seasons[i + 1:], "loan_return", m.club_id)]
    else:
        n = len(seasons)
        cur_len = rng.randint(1, max(1, min(3, n - 1)))
        earlier = seasons[: n - cur_len]
        plan = [c for c in planned if c not in excluded][: len(earlier)]
        k = min(len(earlier), max(len(plan), 1, round(len(earlier) / rng.uniform(1.8, 2.6))))
        lengths = _split(rng, len(earlier), k)
        slots = rng.sample(range(k), len(plan))
        used: set[str] = set()
        chosen: list[str] = [""] * k
        for s, c in zip(slots, plan):
            chosen[s] = c
            used.add(c)
        prev = None
        pos = 0
        for i in range(k):
            club = chosen[i] or draw(used | ({prev} if prev else set()))
            used.add(club)
            yrs = earlier[pos: pos + lengths[i]]
            pos += lengths[i]
            move = "academy" if i == 0 else ("free_transfer" if (len(blocks[-1][1]) >= 3 or yrs[0] - m.dob.year >= 30)
                                              and rng.random() < 0.4 else "transfer")
            if len(yrs) >= 3 and rng.random() < 0.3:
                j = rng.randint(1, len(yrs) - 2)
                loan_club = draw(used, second_division=True)
                used.add(loan_club)
                blocks += [(club, yrs[:j], move, None), (loan_club, [yrs[j]], "loan", club),
                           (club, yrs[j + 1:], "loan_return", club)]
            else:
                blocks.append((club, yrs, move, None))
            prev = club
        last_len = len(blocks[-1][1])
        move = "free_transfer" if (last_len >= 3 or seasons[n - cur_len] - m.dob.year >= 30) and rng.random() < 0.35 \
            else "transfer"
        blocks.append((m.club_id, seasons[n - cur_len:], move, None))

    spells = []
    for club, yrs, move, parent in blocks:
        seasons = []
        for y in yrs:
            if y == CURRENT_START and club == m.club_id:
                seasons.append(SpellSeason(season=season_label(y), appearances=m.stats["appearances"],
                                           goals=m.stats["goals"]))
                continue
            hi = 36 if m.tier == "starter" else 28
            lo = 18 if m.tier == "starter" else 4 if m.tier == "rotation" else 0
            if move == "loan":
                lo, hi = 10, 30
            a = min(rng.randint(lo, hi), rounds[club])
            g = sum(rng.random() < GOAL_RATE[m.group] * (0.6 + m.quality) for _ in range(a))
            seasons.append(SpellSeason(season=season_label(y), appearances=a, goals=g))
        apps = sum(s.appearances for s in seasons)
        goals = sum(s.goals for s in seasons)
        fee = transfer_fee(rng, m.quality, yrs[0] - m.dob.year, club_by_id[club]) if move == "transfer" else 0
        end = yrs[-1]
        spells.append(CareerSpell(
            club_id=club, from_season=season_label(yrs[0]),
            to_season=None if (club == m.club_id and end == CURRENT_START) else season_label(end),
            move=move, parent_club_id=parent, fee_eur=fee, appearances=apps, goals=goals, seasons=seasons,
            head_coach_ids=coaches_during(history, club, yrs[0], end)))
    return spells


# --- Honours ---------------------------------------------------------------------


def player_honours(career: list[CareerSpell], roll: list[RollOfHonourSeason]) -> list[PlayerHonour]:
    """Trophies won with a club in a season the player was there (current season excluded)."""
    by_season = {r.season: r for r in roll}
    out = []
    for sp in career:
        start = season_start(sp.from_season)
        end = season_start(sp.to_season) if sp.to_season else CURRENT_START
        for y in range(start, end + 1):
            r = by_season.get(season_label(y))
            if r is None:
                continue
            for key in ("league", "cup", "championship"):
                if getattr(r, key) == sp.club_id:
                    out.append(PlayerHonour(competition=COMPETITION_NAMES[key], season=r.season, club_id=sp.club_id))
            if r.super_shield == sp.club_id and y < CURRENT_START:
                out.append(PlayerHonour(competition=COMPETITION_NAMES["super_shield"], season=str(y), club_id=sp.club_id))
    return sorted(out, key=lambda h: (h.season[:4], h.competition))
