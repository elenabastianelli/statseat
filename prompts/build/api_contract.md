# API contract and fake data

- **Date:** 2026-10-24
- **Tool:** Claude Code (Claude Opus 5.5)
- **Area:** `[APP > Contract]`

## Prompt (English version of the team prompt)

> Create in app/contract/ the API contract between backend and app (Python 3.12, FastAPI, Pydantic models),
> following the project plan: login with the ticket code (match_id in the token, server-side; Inspector
> only for DEMO and JUDGE tickets), match state (phase, minute, score), statistics (Casual and Analyst, with the names
> of the team's metric spec), highlights (fields of the team's VAR spec), games
> (Tiki-Taka-Toe, Guess the player, quiz) and chat with Archie (answer plus trace for the Inspector). For every
> endpoint write a JSON file of fake data in app/contract/fake/, with names taken from synthetic/ (never real people),
> and a mock server that returns them (python -m app.contract.mock). Export openapi.json and add a test that checks
> the fake data against the models. Short README in English; explain it to me in Italian.

Clarifications from the orchestrating agent: use universe v1 names, numbers, clubs and positions exactly as in
synthetic/ (SYN-001); fake data internally consistent (score = goal events, minute = phase, player ids in
match_info.json); sections unlocked per phase in the match state; modes, favourite players, language; match_id only
from the token; Inspector fields only for DEMO/JUDGE, honoured by the mock; Archie's answer with grounding (tool and
value of every number) and an Inspector trace (agents, tools, tokens, latency), pointing to a Data & Statistics chart
instead of drawing; Tiki-Taka-Toe grid from real crossings of universe.json (career clubs and nationalities only,
never BRA/CAL, every cell with at least 2 answers among the 50 squad players), picker with only name, team and shirt
number; Guess the player in both versions (career clubs and years, or 3 clubs with fees in EUR); quiz with 4 options;
server-timed answers; highlights as a v0 model with a 2D replay reference; versioned contract (0.1.0); in-memory,
simple opaque token, no secrets; pinned requirements, venv, pytest; everything in English.

## Result

- `app/contract/`: `models.py`, `phases.py`, `mock.py`, `make_fake.py`, `registry.py`, `export_openapi.py`,
  `openapi.json`, `fake/` (36 response files + `server/` with tickets and correct answers), `README.md`.
- `app/tests/test_contract.py`: 56 tests (fake data vs models, consistency with synthetic/, every endpoint of the
  mock, Inspector only for DEMO/JUDGE, one device per ticket, locked sections).
- `app/requirements.txt` with pinned versions.

## v0.2.0 (2026-10-24, afternoon)

Prompt (from the orchestrating agent, after the decisions of 24/10 in `main`): update the contract to v0.2.0 with the
app name statseat; ticket tiers silver (Casual only, Analyst refused), gold (Analyst with switch to Casual), demo (gold +
Inspector + phase panel) and judge (gold + Inspector), FAN removed, tier and match_id only server-side; codes
`GNT-SYN001-<S|G|D|J>-XXXX-XXXX` stored only as hashes (per-deployment salt from the environment, public dev default);
one device per ticket except demo and judge; a "My ticket" endpoint (tier, match, seat from the stadium, validity,
image links, what the tier unlocks, no personal data); `allowed_modes` in the session; a minimal `as_of` field for the
no-look-ahead rule; a documented ticket file shape so the real tickets of the match generator can replace the fake ones.

Result:
- `app/contract/tickets.py`: tier rules, code format, HMAC-SHA256 hashing (`STATSEAT_TICKET_SALT`, public `DEV_SALT`),
  registry model and `python -m app.contract.tickets hash <manifest.json> <registry.json>`.
- `GET /api/ticket` and `GET /api/ticket/image`; `Session.tier`, `allowed_modes`, `multi_device`; 403
  `mode_not_allowed` for Analyst with silver; 403 `ticket_not_valid` outside the validity window; `as_of_s` on match
  state, events, live stats, stats and highlights.
- 12 fake codes (3 per tier) in `app/contract/README.md`; `fake/server/tickets.json` keeps only hashes; 72 tests.
