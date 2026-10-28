"""statseat digital tickets for a synthetic match (task S-4).

    cd generator
    .venv/bin/python -m insidethegame_gen.tickets SYN-001

Writes `synthetic/<match_id>/tickets/`: the registry `tickets.json` (hashes, never plain codes),
a README for the judges with the DEMO codes, and SVG, PDF and PNG tickets for every
DEMO ticket plus four silver and four gold samples. Deterministic: the same universe,
match and seed always give the same files, byte for byte.

Modules: `codes` (format, check character, hashing), `registry` (tiers, seats, tickets.json,
checks), `render` (SVG template -> SVG, PDF, PNG), `readme` (the judges' README).
"""

from .codes import code_sha256, make_code, normalise_code, validate_code
from .registry import TIERS, build_tickets

__all__ = ["TIERS", "build_tickets", "code_sha256", "make_code", "normalise_code", "validate_code"]
