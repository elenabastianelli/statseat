"""Tiki-Taka-Toe feasibility (UNIVERSO-SPEC section 3, guidelines section 12).

Criteria are only the clubs of a player's career (except the two match clubs) and
nationalities. The valid answers are the players of the two match squads. A grid
is 3 row criteria x 3 column criteria; it is valid when every cell has at least
`min_answers` players who meet both criteria. Two nationalities never cross (a
player has one nationality), so such a pair is never a valid cell.

Counting the valid grids: the valid crossings form a graph whose vertices are the
criteria; a grid is a complete bipartite subgraph K(3,3). For every triple R of
rows, the columns must be chosen among the common neighbours N(R), giving
C(|N(R)|, 3) grids; each grid is found twice (rows and columns swapped).
"""

from __future__ import annotations

from itertools import combinations
from math import comb

Criterion = tuple[str, str]  # ("club", club_id) or ("nationality", name)


def criteria(players, excluded_clubs: set[str]) -> dict[Criterion, set[str]]:
    """Every club of the careers (minus the excluded ones) and every nationality -> the players who meet it."""
    out: dict[Criterion, set[str]] = {}
    for p in players:
        for sp in p.career:
            if sp.club_id not in excluded_clubs:
                out.setdefault(("club", sp.club_id), set()).add(p.id)
        out.setdefault(("nationality", p.nationality), set()).add(p.id)
    return out


def answers(crit: dict[Criterion, set[str]], a: Criterion, b: Criterion) -> set[str]:
    if a[0] == "nationality" and b[0] == "nationality":
        return set()
    return crit[a] & crit[b]


def valid_pairs(crit: dict[Criterion, set[str]], min_answers: int = 2) -> dict[Criterion, set[Criterion]]:
    keys = sorted(crit)
    adj: dict[Criterion, set[Criterion]] = {k: set() for k in keys}
    for a, b in combinations(keys, 2):
        if len(answers(crit, a, b)) >= min_answers:
            adj[a].add(b)
            adj[b].add(a)
    return adj


def count_grids(crit: dict[Criterion, set[str]], min_answers: int = 2, with_nationality: bool = False) -> int:
    """Number of distinct valid 3x3 grids (unordered rows and columns, transposition counted once).

    With `with_nationality`, only grids that use at least one nationality are counted.
    """
    adj = valid_pairs(crit, min_answers)
    keys = [k for k in sorted(adj) if len(adj[k]) >= 3]
    total = 0
    for rows in combinations(keys, 3):
        common = adj[rows[0]] & adj[rows[1]] & adj[rows[2]]
        if len(common) < 3:
            continue
        if not with_nationality:
            total += comb(len(common), 3)
            continue
        rows_nat = any(r[0] == "nationality" for r in rows)
        if rows_nat:
            total += comb(len(common), 3)
        else:
            clubs_only = [c for c in common if c[0] == "club"]
            total += comb(len(common), 3) - comb(len(clubs_only), 3)
    return total // 2


def example_grid(crit: dict[Criterion, set[str]], min_answers: int = 2) -> tuple[list[Criterion], list[Criterion]] | None:
    """A deterministic example: club rows, two club columns and one foreign-nationality column,
    chosen to maximise the smallest cell (ties broken by criterion order)."""
    adj = valid_pairs(crit, min_answers)
    clubs = sorted(k for k in adj if k[0] == "club")
    nats = sorted(k for k in adj if k[0] == "nationality")
    best = None
    for nat in nats:
        rows_pool = sorted(adj[nat])
        for rows in combinations(rows_pool, 3):
            common = adj[rows[0]] & adj[rows[1]] & adj[rows[2]]
            cols_pool = sorted(c for c in common if c[0] == "club")
            for cols in combinations(cols_pool, 2):
                cells = [len(answers(crit, r, c)) for r in rows for c in (*cols, nat)]
                key = (min(cells), sum(cells))
                if best is None or key > best[0]:
                    best = (key, list(rows), [*cols, nat])
    if best is None:
        return None
    return best[1], best[2]


def foreign_nationalities(players, home_country: str, min_players: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in players:
        if p.nationality != home_country:
            counts[p.nationality] = counts.get(p.nationality, 0) + 1
    return {n: k for n, k in sorted(counts.items()) if k >= min_players}
