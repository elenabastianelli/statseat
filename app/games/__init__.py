"""Half-time games (task M-3): Tic Tac Goal and Guess the player.

Deterministic logic on the universe (synthetic/universe/universe.json): the code chooses the data first, then the
question, so every answer can be checked. The same grid and the same 5 players for every fan of a match.
The contract's endpoints (app/contract/mock.py) call these modules; the real backend inherits them from the mock.
"""
