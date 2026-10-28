"""Guess the player (GIOCHI.md section 2): the full career of a player, with the transfer fees, and 4 names to choose from.

Rules (24/10): only players with at least 3 different clubs in their career; the current club is
hidden (its spells read "?"); the 3 wrong options come from the same unit (goalkeepers, defenders, midfielders,
forwards), completed with other squad players when the unit is short; 5 questions of 20 s per session; fees only for
paid transfers (academy, loan, loan return and free transfer have none). The same 5 players, in the same order, for
every fan of the match (seed = match id).
"""

from __future__ import annotations

import random
from functools import lru_cache

from .universe import club_names, full_name, picker, squads

ROUNDS, ANSWER_SECONDS = 5, 20
UNIT = {"GK": "goalkeepers", "CB": "defenders", "FB": "defenders", "DM": "midfielders", "CM": "midfielders",
        "AM": "midfielders", "W": "forwards", "ST": "forwards"}


def clubs_in_career(p: dict) -> int:
    return len({sp["club_id"] for sp in p["career"]})


def career(p: dict) -> list[dict]:
    """The career clues of the contract (CareerClue), oldest first; the current club's spells are hidden."""
    names = club_names()
    out = []
    for sp in p["career"]:
        hidden = sp["club_id"] == p["club_id"]
        out.append({"club_name": "?" if hidden else names[sp["club_id"]], "from_season": sp["from_season"],
                    "to_season": sp["to_season"], "move": sp["move"],
                    "fee_eur": sp["fee_eur"] if sp["move"] == "transfer" else None, "hidden": hidden})
    return out


@lru_cache(maxsize=None)
def rounds(match_id: str, home: str, away: str) -> tuple[dict, ...]:
    """The 5 rounds of the match: answer, 4 options (shuffled) and the fact shown after the answer."""
    players = squads(home, away)
    rng = random.Random(f"guess-the-player/{match_id}")
    eligible = [p for p in players if clubs_in_career(p) >= 3]
    out = []
    for p in rng.sample(eligible, ROUNDS):
        same_unit = [q for q in eligible if q["id"] != p["id"] and UNIT[q["position_group"]] == UNIT[p["position_group"]]]
        wrong = rng.sample(same_unit, min(3, len(same_unit)))
        others = [q for q in players if q["id"] != p["id"] and q not in wrong]
        wrong += rng.sample(others, 3 - len(wrong))
        options = [p, *wrong]
        rng.shuffle(options)
        out.append({"answer": p["id"], "career": career(p), "options": [picker(q) for q in options],
                    "fact": f"{full_name(p)} ({p['club_id']} #{p['shirt_number']})"})
    return tuple(out)


def round_view(match_id: str, home: str, away: str, n: int) -> dict:
    """Round n (1-5) as the contract's GuessRound, without the deadline (the server adds it when the round starts)."""
    r = rounds(match_id, home, away)[n - 1]
    return {"round_id": f"GTP-{n}", "version": "career", "career": r["career"], "transfers": None,
            "options": r["options"], "answer_seconds": ANSWER_SECONDS, "round_number": n, "rounds_total": ROUNDS}


def answer_of(match_id: str, home: str, away: str, round_id: str) -> dict | None:
    """The answer and the fact of a round id (GTP-1 ... GTP-5), or None for an unknown id."""
    prefix, _, n = round_id.partition("-")
    if prefix != "GTP" or not n.isdigit() or not 1 <= int(n) <= ROUNDS:
        return None
    r = rounds(match_id, home, away)[int(n) - 1]
    return {"correct": r["answer"], "fact": r["fact"]}
