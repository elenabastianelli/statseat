"""The universe data the games read: the 50 players of the two match squads and the club names."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "synthetic" / "universe" / "universe.json"
CRESTS = ROOT / "synthetic" / "crests"


@lru_cache(maxsize=1)
def universe() -> dict:
    return json.loads(UNIVERSE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def club_names() -> dict[str, str]:
    return {c["id"]: c["name"] for c in universe()["clubs"]}


@lru_cache(maxsize=None)
def squads(home: str, away: str) -> tuple[dict, ...]:
    """The players of the two clubs, home first, by shirt number (the order of the player picker)."""
    players = [p for p in universe()["players"] if p["club_id"] in (home, away)]
    return tuple(sorted(players, key=lambda p: (p["club_id"] != home, p["shirt_number"])))


def full_name(p: dict) -> str:
    return f"{p['first_name']} {p['last_name']}"


def picker(p: dict) -> dict:
    """Player picker entry of the contract: only name, team and shirt number."""
    return {"player_id": p["id"], "name": full_name(p), "team_id": p["club_id"], "shirt_number": p["shirt_number"]}
