"""Tic Tac Goal (GIOCHI.md section 1): a 3x3 grid of career clubs and nationalities, played 1 v 1.

Rules (23-24/10): criteria are only career clubs (never the two match clubs) and
nationalities; answers only among the 50 players of the two squads; every cell has at least 2 answers; the same
player may fill several cells; a wrong answer loses the turn; 20 s per turn; three in a row wins; a full grid or the
10-minute limit without three in a row is a draw. Random pairing with another fan is not built yet: the opponent is
always the fan-bot (in the demo the judge plays alone).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from itertools import combinations

from .universe import club_names, squads

TURN_SECONDS, GAME_SECONDS = 20, 600
CELL_POINTS, WIN_POINTS, DRAW_POINTS = 10, 100, 50
BOT_ACCURACY, BOT_BLOCKS = 0.7, 0.5  # the bot answers right 7 times in 10 and blocks a fan's three half of the time
BOT_NAME = "Fan 0482 (Foundry End)"
LINES = [[(r, c) for c in range(3)] for r in range(3)] + [[(r, c) for r in range(3)] for c in range(3)] + \
        [[(i, i) for i in range(3)], [(i, 2 - i) for i in range(3)]]

Criterion = tuple[str, str]  # ("club", club_id) or ("nationality", country)


class GameOver(Exception):
    """A move on a game that has ended."""


@lru_cache(maxsize=None)
def grid(home: str, away: str) -> tuple[list[Criterion], list[Criterion], dict[str, list[str]]]:
    """The match's grid, the same for every fan: two club columns and one foreign-nationality column, three club rows.

    Among the valid grids (every cell >= 2 answers) it takes the one with the most cells of only 2-3 answers, so the
    grid is not trivial (GIOCHI.md: the richest grid of the spec example is too easy); ties by criterion order.
    """
    crit: dict[Criterion, set[str]] = {}
    for p in squads(home, away):
        for sp in p["career"]:
            if sp["club_id"] not in (home, away):
                crit.setdefault(("club", sp["club_id"]), set()).add(p["id"])
        crit.setdefault(("nationality", p["nationality"]), set()).add(p["id"])
    home_country = max({p["nationality"] for p in squads(home, away)},
                       key=lambda n: (sum(p["nationality"] == n for p in squads(home, away)), n))

    def answers(a: Criterion, b: Criterion) -> set[str]:
        return set() if a[0] == b[0] == "nationality" else crit[a] & crit[b]

    clubs = sorted(k for k in crit if k[0] == "club")
    adj = {k: {o for o in crit if o != k and len(answers(k, o)) >= 2} for k in crit}
    best = None
    for nat in sorted(k for k in crit if k[0] == "nationality" and k[1] != home_country):
        for rows in combinations(sorted(c for c in adj[nat] if c[0] == "club"), 3):
            common = sorted(c for c in clubs if c not in rows and all(c in adj[r] for r in rows))
            for cols in combinations(common, 2):
                cells = [len(answers(r, c)) for r in rows for c in (*cols, nat)]
                key = (sum(n <= 3 for n in cells), -max(cells))
                if best is None or key > best[0]:
                    best = (key, list(rows), [*cols, nat])
    if best is None:
        raise RuntimeError("no valid Tic Tac Goal grid for these squads")
    _, rows, cols = best
    sol = {f"{r},{c}": sorted(answers(rows[r], cols[c])) for r in range(3) for c in range(3)}
    return rows, cols, sol


def criterion(c: Criterion) -> dict:
    return {"kind": c[0], "id": c[1], "label": club_names()[c[1]] if c[0] == "club" else c[1]}


@dataclass
class Game:
    """One fan's game: `view` is the contract's TttGame payload, the rest stays on the server."""

    view: dict
    solutions: dict[str, list[str]]
    rng: random.Random
    started: float  # monotonic seconds
    turn_started: float


def start(home: str, away: str, game_session_id: str, t: float, now: datetime) -> Game:
    rows, cols, sol = grid(home, away)
    view = {"game_session_id": game_session_id, "rows": [criterion(c) for c in rows], "cols": [criterion(c) for c in cols],
            "board": [{"row": r, "col": c, "owner": None, "player_id": None} for r in range(3) for c in range(3)],
            "opponent": {"display_name": BOT_NAME, "is_bot": True}, "my_symbol": "X", "turn": "me",
            "turn_seconds": TURN_SECONDS, "turn_deadline": (now + timedelta(seconds=TURN_SECONDS)).isoformat(),
            "ends_at": (now + timedelta(seconds=GAME_SECONDS)).isoformat(), "status": "playing"}
    return Game(view, sol, random.Random(game_session_id), t, t)


def _cell(g: Game, r: int, c: int) -> dict:
    return g.view["board"][r * 3 + c]


def _has_line(g: Game, who: str) -> bool:
    return any(all(_cell(g, r, c)["owner"] == who for r, c in line) for line in LINES)


def _winning_cells(g: Game, who: str) -> list[tuple[int, int]]:
    """Free cells that would complete a line for `who`."""
    out = []
    for line in LINES:
        owners = [_cell(g, r, c)["owner"] for r, c in line]
        if owners.count(who) == 2 and owners.count(None) == 1:
            out.append(line[owners.index(None)])
    return sorted(set(out))


def _finish(g: Game) -> int:
    """Update the status after a move; the points of the result (win or draw) when the game ends."""
    if _has_line(g, "me"):
        g.view["status"] = "won"
        return WIN_POINTS
    if _has_line(g, "opponent"):
        g.view["status"] = "lost"
        return 0
    if all(cell["owner"] for cell in g.view["board"]):
        g.view["status"] = "draw"
        return DRAW_POINTS
    return 0


def _bot_turn(g: Game, t: float, now: datetime) -> int:
    """The bot plays one move, then it is the fan's turn again (with a new 20 s deadline)."""
    free = [(cell["row"], cell["col"]) for cell in g.view["board"] if cell["owner"] is None]
    target = _winning_cells(g, "opponent")
    block = _winning_cells(g, "me")
    if target:
        r, c = g.rng.choice(target)
    elif block and g.rng.random() < BOT_BLOCKS:
        r, c = g.rng.choice(block)
    else:
        r, c = g.rng.choice(free)
    if g.rng.random() < BOT_ACCURACY:
        _cell(g, r, c).update(owner="opponent", player_id=g.rng.choice(g.solutions[f"{r},{c}"]))
    points = _finish(g)
    g.view["turn"] = "me"
    g.turn_started = t
    g.view["turn_deadline"] = (now + timedelta(seconds=TURN_SECONDS)).isoformat()
    return points


def refresh(g: Game, t: float, now: datetime) -> int:
    """Apply the clocks: past the 10 minutes the game is a draw; past the 20 s the fan loses the turn.
    Returns the points earned (the draw's)."""
    if g.view["status"] != "playing":
        return 0
    if t - g.started >= GAME_SECONDS:
        g.view["status"] = "draw"
        return DRAW_POINTS
    if t - g.turn_started > TURN_SECONDS:
        return _bot_turn(g, t, now)
    return 0


def move(g: Game, row: int, col: int, player_id: str, t: float, now: datetime) -> dict:
    """The fan's move: the contract's TttMoveResult. A wrong or late answer loses the turn; a taken cell does not."""
    if g.view["status"] != "playing":
        raise GameOver
    elapsed_ms = int((t - g.turn_started) * 1000)
    if t - g.started >= GAME_SECONDS:
        g.view["status"] = "draw"
        return {"correct": False, "reason": "time_up", "elapsed_ms": elapsed_ms, "game": g.view, "points_awarded": DRAW_POINTS}
    cell = _cell(g, row, col)
    if elapsed_ms > TURN_SECONDS * 1000:
        reason = "too_late"
    elif cell["owner"] is not None:
        return {"correct": False, "reason": "cell_taken", "elapsed_ms": elapsed_ms, "game": g.view, "points_awarded": 0}
    elif player_id not in g.solutions[f"{row},{col}"]:
        reason = "wrong_answer"
    else:
        reason = None
        cell.update(owner="me", player_id=player_id)
    points = CELL_POINTS if reason is None else 0
    points += _finish(g)
    if g.view["status"] == "playing":
        g.view["turn"] = "opponent"
        points += _bot_turn(g, t, now)
    return {"correct": reason is None, "reason": reason, "elapsed_ms": elapsed_ms, "game": g.view, "points_awarded": points}
