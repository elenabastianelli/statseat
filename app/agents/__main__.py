"""Ask Archie from the terminal (AG-1b). Run from the repo root, with the Foundry settings in .env and `az login`.

    python -m app.agents ask "Who has run the most?" ["¿Quién ha corrido más?" ...]
           [--phase first_half] [--minute 30] [--mode casual|analyst] [--lang es] [--ticket CODE] [--out FILE.json]
    python -m app.agents setup [--dry-run] [--only archie|analyst]   # creates the agent versions on Foundry
    python -m app.agents show                                          # the agent versions in use, from Foundry

`ask` loads the match (MATCH_SOURCE, default synthetic:SYN-001, about 40 s), logs in with a DEMO ticket, moves that
session's clock to the phase and minute, and sends each question to POST /api/archie/ask, the same path as the app.
Every command but `setup --dry-run` calls Foundry.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .settings import FoundrySettings, load_dotenv

DEMO_TICKET = "GNT-SYN001-D-WHQF-W1S6"  # SYN-001-D-001, synthetic/SYN-001/tickets/README.md


def _ask(args: argparse.Namespace) -> None:
    from fastapi.testclient import TestClient

    from app.engine.server import create_app

    t0 = time.monotonic()
    app = create_app()
    if app.state.archie is None:
        sys.exit("Foundry is not configured: set FOUNDRY_PROJECT_ENDPOINT, ARCHIE_AGENT_ID and ANALYST_AGENT_ID (.env)")
    with TestClient(app) as client:  # one event loop for every question: one Foundry client, closed at the end
        _questions(app, client, args, t0)


def _questions(app, client, args: argparse.Namespace, t0: float) -> None:
    from app.contract.models import Phase

    engine = app.state.engine
    r = client.post("/api/auth/login", json={"ticket_code": args.ticket, "device_id": "terminal-archie"})
    r.raise_for_status()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    prefs = client.get("/api/session", headers=h).json()["preferences"]
    client.put("/api/session/preferences", headers=h,
               json={**prefs, "mode": args.mode or prefs["mode"], "language": args.lang or prefs["language"]})
    phase = Phase(args.phase)
    engine.jump(token, phase)
    pos = engine.starts[phase]
    if args.minute is not None and phase in (Phase.first_half, Phase.second_half):
        pos += max(0, args.minute - (45 if phase is Phase.second_half else 0)) * 60.0
    engine.demo[token].seek(pos, False)  # paused: every question sees the same instant
    snap = engine.snapshot(token)
    engine.core(engine.stats_snapshot(snap))
    state = client.get("/api/match/state", headers=h).json()
    print(f"match loaded in {time.monotonic() - t0:.0f} s; {state['phase']} {state['minute']}', score "
          f"{state['score']['home']}-{state['score']['away']}, mode {args.mode or prefs['mode']}\n")
    out = []
    for q in args.questions:
        a = client.post("/api/archie/ask", headers=h, json={"message": q, "language": args.lang}).json()
        ins = a.get("inspector") or {}
        g = ins.get("grounding", {})
        print(f"Q: {q}\nArchie: {a.get('text')}")
        print(f"   in_scope={a.get('in_scope')} language={a.get('language')} points_to="
              f"{(a.get('points_to') or {}).get('chart_id')} grounding={'ok' if g.get('passed') else g}")
        print(f"   tools: {', '.join(c['tool'] for c in ins.get('tool_calls', [])) or 'none'}; "
              f"{ins.get('total_tokens')} tokens, {ins.get('latency_ms')} ms, ${ins.get('cost_usd')}")
        print(f"   method: {ins.get('method')}\n")
        out.append({"question": q, "answer": a})
    if args.out:
        Path(args.out).write_text(json.dumps({"state": state, "turns": out}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("questions", nargs="+")
    a.add_argument("--phase", default="first_half",
                   choices=["pre_match", "first_half", "half_time", "second_half", "full_time"])
    a.add_argument("--minute", type=int, help="match minute during play (default: the start of the phase)")
    a.add_argument("--mode", choices=["casual", "analyst"])
    a.add_argument("--lang")
    a.add_argument("--ticket", default=DEMO_TICKET)
    a.add_argument("--out")
    s = sub.add_parser("setup")
    s.add_argument("--dry-run", action="store_true", help="print the definitions, no call to Foundry")
    s.add_argument("--only", choices=["archie", "analyst"])
    sub.add_parser("show")
    args = p.parse_args()
    settings = FoundrySettings.from_env()
    if args.cmd == "ask":
        _ask(args)
        return
    try:
        from . import foundry
    except ModuleNotFoundError as exc:
        sys.exit(f"{exc.name} is missing: use the worktree's environment (source app/.venv/bin/activate) or "
                 "pip install -r app/requirements.txt")

    if args.cmd == "setup" and args.dry_run:
        defs = foundry.definitions(settings)
        print(json.dumps(defs, ensure_ascii=False, indent=1))
        for k, d in defs.items():
            size = len(d["instructions"]) + len(json.dumps(d["tools"])) + len(json.dumps(d["text"] or ""))
            print(f"{k}: about {size // 4} tokens of instructions, tools and format", file=sys.stderr)
        return
    if not settings.endpoint or not settings.archie_agent:
        sys.exit("set FOUNDRY_PROJECT_ENDPOINT and ARCHIE_AGENT_ID (.env)")
    if args.cmd == "setup":
        if not settings.analyst_agent and args.only != "archie":
            sys.exit("set ANALYST_AGENT_ID (.env): the name of The Analyst agent to create")
        which = (args.only,) if args.only else ("analyst", "archie")
        for k, v in foundry.setup(settings, which).items():
            print(f"{k}: {v}")
        print("Put the new versions in .env (ARCHIE_AGENT_VERSION, ANALYST_AGENT_VERSION) and in azd (C-3).")
    else:
        print(json.dumps(foundry.show(settings), indent=1))


if __name__ == "__main__":
    main()
