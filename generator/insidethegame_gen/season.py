"""Squads of the two match clubs and their season so far.

The season is simulated round by round from the league results, so every number
adds up: the scorers' goals equal the team's goals, starts are 11 per game,
minutes never exceed 90 per appearance, a red card means a one-match ban,
an injury keeps a player out for the next games. The SYN-001 line-ups are then
picked from the players who are available on matchday 9.

All positions are English codes (guidelines section 12): the natural position
of every player, and the formation slots of the starters.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .schema import POSITION_GROUP

FORMATIONS: dict[str, list[str]] = {
    "4-2-3-1": ["GK", "RB", "RCB", "LCB", "LB", "RDM", "LDM", "RW", "CAM", "LW", "ST"],
    "4-4-2": ["GK", "RB", "RCB", "LCB", "LB", "RM", "RCM", "LCM", "LM", "RS", "LS"],
}
# Natural position that fits a formation slot best (default: the slot itself).
SLOT_NATURAL = {"RDM": "CDM", "LDM": "CDM", "RS": "ST", "LS": "ST"}
CONVENTIONAL_SHIRTS = {
    "4-2-3-1": {"GK": 1, "RB": 2, "RCB": 4, "LCB": 5, "LB": 3, "RDM": 6, "LDM": 8, "RW": 7, "CAM": 10, "LW": 11, "ST": 9},
    "4-4-2": {"GK": 1, "RB": 2, "RCB": 5, "LCB": 6, "LB": 3, "RM": 7, "RCM": 4, "LCM": 8, "LM": 11, "RS": 9, "LS": 10},
}
# 25 natural positions per squad: two players for every formation slot, three goalkeepers.
SQUAD_TEMPLATES: dict[str, list[str]] = {
    "4-2-3-1": ["GK"] * 3 + ["RB", "RB", "RCB", "RCB", "LCB", "LCB", "LB", "LB", "CDM", "CDM", "CDM", "RCM", "LCM",
                             "CAM", "CAM", "RW", "RW", "LW", "LW", "ST", "ST", "ST"],
    "4-4-2": ["GK"] * 3 + ["RB", "RB", "RCB", "RCB", "LCB", "LCB", "LB", "LB", "CDM", "RCM", "RCM", "LCM", "LCM",
                           "CAM", "RM", "RM", "LM", "LM", "ST", "ST", "ST", "ST"],
}
BENCH_NEEDS: list[tuple[str, ...]] = [
    ("GK",), ("CB",), ("FB",), ("DM", "CM"), ("CM", "DM", "AM"), ("W", "AM"), ("AM", "W", "CM"), ("ST",), ("ST", "W"),
]
GROUP_FALLBACK = {"GK": [], "CB": ["FB", "DM"], "FB": ["CB", "W"], "DM": ["CM", "CB"], "CM": ["DM", "AM"],
                  "AM": ["CM", "W"], "W": ["AM", "FB", "ST"], "ST": ["AM", "W"]}

HEIGHT_CM = {"GK": (186, 198), "CB": (182, 195), "FB": (172, 184), "DM": (176, 188), "CM": (172, 186),
             "AM": (168, 182), "W": (166, 180), "ST": (176, 192)}
GOAL_RATE = {"GK": 0.0, "CB": 0.04, "FB": 0.03, "DM": 0.04, "CM": 0.08, "AM": 0.18, "W": 0.20, "ST": 0.36}
ASSIST_RATE = {"GK": 0.005, "CB": 0.03, "FB": 0.10, "DM": 0.06, "CM": 0.12, "AM": 0.22, "W": 0.25, "ST": 0.15}
CARD_RATE = {"GK": 0.2, "CB": 1.3, "FB": 1.0, "DM": 1.5, "CM": 1.1, "AM": 0.7, "W": 0.7, "ST": 0.8}
STYLES = {"GK": ["shot-stopper", "sweeper-keeper"], "CB": ["aerial", "ball-playing", "stopper"],
          "FB": ["overlapping", "defensive", "inverted"], "DM": ["ball-winner", "deep playmaker"],
          "CM": ["box-to-box", "playmaker", "engine"], "AM": ["creator", "second striker"],
          "W": ["pacy", "inverted", "crosser"], "ST": ["poacher", "target man", "pressing forward"]}

MATCH_ROUND = 9  # SYN-001 is matchday 9; rounds 1-8 are played


def poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


@dataclass(eq=False)
class Member:
    """A squad member: drafted profile plus the state of the simulated season."""

    club_id: str
    position: str
    tier: str
    slot: str | None = None  # first-choice formation slot
    shirt: int = 0
    quality: float = 0.0
    style: list[str] = field(default_factory=list)
    first: str = ""
    last: str = ""
    nationality: str = ""
    dob: date | None = None
    height: int = 0
    foot: str = "right"
    academy: bool = False
    birth_town: str = ""
    birth_country: str = ""
    # season state
    out: dict[int, str] = field(default_factory=dict)  # round -> reason (injury, suspension, illness)
    injuries: list[dict] = field(default_factory=list)
    long_injury: dict | None = None
    stats: Counter = field(default_factory=Counter)
    form: dict[int, dict] = field(default_factory=dict)

    @property
    def group(self) -> str:
        return POSITION_GROUP[self.position]

    @property
    def id(self) -> str:
        return f"{self.club_id}-{self.shirt:02d}"


def draft_squad(rng: random.Random, club_id: str, formation: str, strength: float, match_day: date) -> list[Member]:
    """25 members with natural positions, tiers, quality, ages, heights, feet and shirt numbers (no names yet)."""
    squad = [Member(club_id=club_id, position=p, tier="rotation") for p in SQUAD_TEMPLATES[formation]]
    for slot in FORMATIONS[formation]:
        natural = SLOT_NATURAL.get(slot, slot)
        m = next(x for x in squad if x.slot is None and x.position == natural)
        m.slot, m.shirt, m.tier = slot, CONVENTIONAL_SHIRTS[formation][slot], "starter"
    gks = [m for m in squad if m.position == "GK" and m.tier != "starter"]
    gks[-1].tier = "prospect"
    others = [m for m in squad if m.tier == "rotation" and m.position != "GK"]
    for m in rng.sample(others, 4):
        m.tier = "prospect"

    used_numbers = {m.shirt for m in squad if m.shirt}
    for m in squad:
        base = {"starter": (0.66, 0.90), "rotation": (0.48, 0.72), "prospect": (0.36, 0.60)}[m.tier]
        m.quality = round(min(1.0, rng.uniform(*base) * (0.85 + 0.25 * strength)), 3)
        m.style = sorted(rng.sample(STYLES[m.group], k=rng.choice([1, 1, 2])))
        age = {"starter": (22, 33), "rotation": (20, 35), "prospect": (17, 20)}[m.tier]
        m.dob = date(match_day.year - rng.randint(*age), 1, 1) - timedelta(days=rng.randint(0, 364))
        m.academy = (m.tier == "prospect" and rng.random() < 0.7) or rng.random() < 0.12
        m.height = rng.randint(*HEIGHT_CM[m.group])
        side = m.position[0] if m.position not in ("GK", "CDM", "CAM", "ST", "CB") else "C"
        if side == "L":
            m.foot = "left" if rng.random() < 0.8 else "right"
        elif side == "R":
            m.foot = "right" if rng.random() < 0.85 else "left"
        else:
            m.foot = rng.choices(["right", "left", "both"], weights=[0.75, 0.2, 0.05])[0]
        if not m.shirt:
            if m.position == "GK":
                choices = [n for n in (13, 31, 25, 30) if n not in used_numbers]
            elif m.tier == "prospect":
                choices = [n for n in range(36, 50) if n not in used_numbers]
            else:
                choices = [n for n in range(12, 36) if n not in used_numbers]
            m.shirt = rng.choice(choices)
            used_numbers.add(m.shirt)
    return sorted(squad, key=lambda m: m.shirt)


def set_piece_takers(rng: random.Random, squad: list[Member]) -> dict[str, list[str]]:
    """Order of preference for penalties, free kicks and corners (player ids)."""
    def best(groups: tuple[str, ...], k: int = 3) -> list[str]:
        pool = [m for m in squad if m.group in groups and m.tier != "prospect"]
        ranked = sorted(pool, key=lambda m: (-(m.quality + rng.uniform(0, 0.06) + (0.1 if m.tier == "starter" else 0)), m.shirt))
        return [m.id for m in ranked[:k]]
    return {"penalties": best(("ST", "AM", "W")), "free_kicks": best(("AM", "W", "CM")),
            "corners": best(("W", "CM", "AM", "FB"))}


def needs(formation: str) -> Counter:
    return Counter(POSITION_GROUP[s] for s in FORMATIONS[formation])


def available(squad: list[Member], rnd: int) -> list[Member]:
    return [m for m in squad if rnd not in m.out]


def depth_ok(squad: list[Member], formation: str, m: Member, rounds: list[int]) -> bool:
    """True if the group of `m` keeps one spare player in every round he would miss (up to the match round)."""
    need = needs(formation)[m.group]
    for r in rounds:
        if r > MATCH_ROUND:
            continue
        free = [x for x in available(squad, r) if x is not m and x.group == m.group]
        if len(free) < need + 1:
            return False
    return True


def select_xi(rng: random.Random, pool: list[Member], formation: str, rotation: bool) -> list[tuple[str, Member]]:
    chosen: list[tuple[str, Member]] = []
    taken: set[int] = set()
    for slot in FORMATIONS[formation]:
        natural = SLOT_NATURAL.get(slot, slot)
        group = POSITION_GROUP[slot]
        free = [m for m in pool if id(m) not in taken]
        ranked = sorted(free, key=lambda m: (-m.quality, m.shirt))
        cands = [m for m in ranked if m.position == natural] or [m for m in ranked if m.group == group]
        for g in GROUP_FALLBACK[group]:
            cands = cands or [m for m in ranked if m.group == g]
        pick = cands[1] if rotation and group != "GK" and len(cands) > 1 and rng.random() < 0.1 else cands[0]
        chosen.append((slot, pick))
        taken.add(id(pick))
    return chosen


def select_bench(pool: list[Member]) -> list[Member]:
    ranked = sorted(pool, key=lambda m: (-m.quality, m.shirt))
    bench: list[Member] = []
    for need in BENCH_NEEDS:
        m = next((p for g in need for p in ranked if p not in bench and p.group == g), None)
        m = m or next(p for p in ranked if p not in bench and p.group != "GK")
        bench.append(m)
    return bench


@dataclass
class ClubGame:
    round: int
    opponent_id: str
    venue: str
    goals_for: int
    goals_against: int


def simulate_season(rng: random.Random, squad: list[Member], formation: str, games: list[ClubGame],
                    set_pieces: dict[str, list[str]], injury_types: list[dict], long_injuries: list[dict]) -> list[dict]:
    """Plays rounds 1-8 for one club. Updates every member's stats, form, injuries and bans; returns the match log."""
    by_id = {m.id: m for m in squad}
    # One rotation player has been out since pre-season.
    long_pool = [m for m in squad if m.tier == "rotation" and m.group != "GK"]
    lm = rng.choice(long_pool)
    if depth_ok(squad, formation, lm, list(range(1, MATCH_ROUND + 1))):
        spec = rng.choice(long_injuries)
        lm.long_injury = spec
        for r in range(1, MATCH_ROUND + 1):
            lm.out[r] = "injury"

    log = []
    for g in games:
        rnd = g.round
        pool = available(squad, rnd)
        xi = select_xi(rng, pool, formation, rotation=True)
        starters = [m for _, m in xi]
        bench = select_bench([m for m in pool if m not in starters])
        span: dict[int, list[int]] = {id(m): [0, 90] for m in starters}
        outfield_bench = sorted((b for b in bench if b.group != "GK"), key=lambda m: m.shirt)
        subs = rng.sample(outfield_bench, k=min(rng.randint(3, 5), len(outfield_bench)))
        replaced: set[int] = set()
        for sub, minute in zip(subs, sorted(rng.randint(46, 88) for _ in subs)):
            cands = [s for s in starters if s.group != "GK" and id(s) not in replaced]
            same = [s for s in cands if s.group == sub.group]
            off = rng.choice(same or cands)
            replaced.add(id(off))
            span[id(off)][1] = minute
            span[id(sub)] = [minute, 90]
        played = starters + subs

        def on_pitch(minute: int) -> list[Member]:
            return [m for m in played if span[id(m)][0] < minute <= span[id(m)][1]]

        events: dict[int, list[int]] = {id(m): [] for m in played}
        scorers = []
        for minute in sorted(rng.sample(range(1, 91), g.goals_for)):
            here = on_pitch(minute)
            taker = next((by_id[t] for t in set_pieces["penalties"] if by_id[t] in here), None)
            if taker is not None and rng.random() < 0.12:
                scorer, penalty = taker, True
            else:
                scorer = rng.choices(here, weights=[GOAL_RATE[m.group] * (m.quality + 0.3) + 1e-9 for m in here])[0]
                penalty = False
            scorer.stats["goals"] += 1
            events[id(scorer)].append(minute)
            scorers.append({"player_id": scorer.id, "minute": minute, "penalty": penalty})
            if not penalty and rng.random() < 0.7:
                mates = [m for m in here if m is not scorer]
                helper = rng.choices(mates, weights=[ASSIST_RATE[m.group] * (m.quality + 0.3) for m in mates])[0]
                helper.stats["assists"] += 1
                events[id(helper)].append(minute)
        booked: set[int] = set()
        for _ in range(poisson(rng, 1.7)):
            cands = [m for m in played if id(m) not in booked]
            m = rng.choices(cands, weights=[CARD_RATE[x.group] for x in cands])[0]
            booked.add(id(m))
            m.stats["yellow_cards"] += 1
        if rng.random() < 0.05:
            cands = [m for m in played if m.group != "GK" and span[id(m)][1] == 90
                     and depth_ok(squad, formation, m, [rnd + 1])]
            if cands:
                m = rng.choice(sorted(cands, key=lambda x: x.shirt))
                earliest = max(span[id(m)][0] + 1, 25, max(events[id(m)], default=0) + 1)
                if earliest <= 89:
                    span[id(m)][1] = rng.randint(earliest, 89)
                    m.stats["red_cards"] += 1
                    m.out[rnd + 1] = "suspension"
        if rng.random() < 0.3:
            cands = [m for m in starters if m.group != "GK"]
            m = rng.choice(sorted(cands, key=lambda x: x.shirt))
            spec = rng.choice(injury_types)
            k = rng.randint(*spec["matches"])
            rounds = list(range(rnd + 1, rnd + 1 + k))
            if not any(r in m.out for r in rounds) and depth_ok(squad, formation, m, rounds):
                for r in rounds:
                    m.out[r] = "injury"
                m.injuries.append({"round": rnd, "opponent_id": g.opponent_id, "injury": spec["injury"], "rounds": rounds})
        for m in played:
            on, off = span[id(m)]
            m.stats["appearances"] += 1
            m.stats["starts"] += int(m in starters)
            m.stats["minutes"] += off - on
        for m in squad:
            on, off = span.get(id(m), [0, 0])
            m.form[rnd] = {"round": rnd, "opponent_id": g.opponent_id, "started": m in starters, "minutes": off - on,
                           "goals": sum(1 for s in scorers if s["player_id"] == m.id),
                           "assists": 0}
        log.append({"round": rnd, "opponent_id": g.opponent_id, "venue": g.venue, "goals_for": g.goals_for,
                    "goals_against": g.goals_against, "scorers": scorers})
        # assists per round for the form guide
        for m in played:
            m.form[rnd]["assists"] = sum(1 for t in events[id(m)]) - m.form[rnd]["goals"]
    return log


def matchday_illness(rng: random.Random, squad: list[Member], formation: str, chance: float) -> Member | None:
    """With some probability one available outfield player is ill on matchday."""
    if rng.random() >= chance:
        return None
    cands = [m for m in available(squad, MATCH_ROUND) if m.group != "GK" and m.tier != "prospect"
             and depth_ok(squad, formation, m, [MATCH_ROUND])]
    if not cands:
        return None
    m = rng.choice(sorted(cands, key=lambda x: x.shirt))
    m.out[MATCH_ROUND] = "illness"
    return m
