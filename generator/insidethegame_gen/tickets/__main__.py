"""Command line: build, check and write the statseat tickets of one synthetic match.

    cd generator
    .venv/bin/python -m insidethegame_gen.tickets SYN-001
    .venv/bin/python -m insidethegame_gen.tickets SYN-001 --qr-url https://<demo-host>/login

Nothing is written if a check fails. Outputs in synthetic/<match_id>/tickets/:
    tickets.json      the registry (hashes, never plain codes)
    README.md         for the judges: tiers, how to log in, DEMO codes
    svg/ pdf/ png/    every DEMO ticket, plus four silver and four gold samples
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .codes import normalise_code
from .readme import render_readme
from .registry import MatchContext, Ticket, build_tickets, check_registry, load_inputs, registry_json, text_problems
from .render import PNG_SIZE, qr_content, render_ticket, svg_texts

REPO_ROOT = Path(__file__).resolve().parents[3]
BLOCKLIST = Path(__file__).resolve().parents[1] / "content" / "blocklist.json"
FORMATS = ("svg", "pdf", "png")


@dataclass
class Build:
    ctx: MatchContext
    tickets: list[Ticket]
    files: dict[str, bytes]  # path relative to the tickets folder -> bytes
    results: list[tuple[str, list[str]]]  # (check, problems)

    @property
    def ok(self) -> bool:
        return not any(problems for _, problems in self.results)


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build(match_id: str, repo_root: Path = REPO_ROOT, qr_url: str | None = None,
          crests_dir: Path | None = None) -> Build:
    universe, match = load_inputs(repo_root, match_id)
    blocklist = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
    ctx, tickets = build_tickets(universe, match, blocklist)
    crests_dir = crests_dir if crests_dir is not None else repo_root / "synthetic" / "crests"
    qr_rule = f"a login link, `{qr_url}?ticket=<code>`" if qr_url else "the ticket code only, no link"

    files: dict[str, bytes] = {}
    svgs: dict[str, str] = {}
    for t in tickets:
        if t.files:
            rendered = render_ticket(ctx, t, qr_content(t.code, qr_url), crests_dir)
            for fmt in FORMATS:
                files[t.files[fmt]] = rendered[fmt]
            svgs[t.files["svg"]] = rendered["svg"].decode("utf-8")
    registry = dumps(registry_json(ctx, tickets, qr_rule))
    readme = render_readme(ctx, tickets, qr_rule)
    files["tickets.json"] = registry.encode("utf-8")
    files["README.md"] = readme.encode("utf-8")

    results = check_registry(ctx, tickets, universe)
    compact = {t.ticket_id: (t.code, t.code.replace("-", "")) for t in tickets}
    results.append(("No plain code in tickets.json",
                    [f"{tid}: code found" for tid, forms in compact.items() if any(f in registry for f in forms)]))
    texts = "\n".join([readme, *svgs.values()])
    results.append(("Unpublished codes appear in no output",
                    [f"{t.ticket_id}: code found" for t in tickets if not t.published
                     and any(f in texts for f in compact[t.ticket_id])]))
    found = set(re.findall(r"GNT-[A-Z0-9-]{15,}", texts))
    results.append(("Every code in the README and images is valid and published",
                    [f"{c}: not a published code" for c in sorted(found)
                     if normalise_code(c) not in {t.code for t in tickets if t.published}]))
    problems = []
    for t in tickets:
        if t.published and not t.files:
            problems.append(f"{t.ticket_id}: published but not rendered")
        for path in (t.files or {}).values():
            if path not in files:
                problems.append(f"{t.ticket_id}: {path} missing")
        if t.files and Image.open(io.BytesIO(files[t.files["png"]])).size != PNG_SIZE:
            problems.append(f"{t.ticket_id}: PNG is not {PNG_SIZE[0]} x {PNG_SIZE[1]}")
    results.append(("Ticket images: SVG, PDF and PNG for every published ticket", problems))
    checked = {"README.md": readme} | {path: svg_texts(svg) for path, svg in svgs.items()} \
        | {f"alt of {t.ticket_id}": t.alt for t in tickets}
    results.append(("No real people, clubs, brands; no alcohol, betting or tobacco words",
                    text_problems(checked, blocklist)))
    return Build(ctx, tickets, files, results)


def write(result: Build, out_dir: Path) -> list[Path]:
    """Write every file and delete stale images in svg/, pdf/ and png/."""
    for fmt in FORMATS:
        folder = out_dir / fmt
        if folder.is_dir():
            for old in folder.iterdir():
                if f"{fmt}/{old.name}" not in result.files:
                    old.unlink()
    written = []
    for rel, data in sorted(result.files.items()):
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m insidethegame_gen.tickets", description=__doc__.split("\n")[0])
    parser.add_argument("match_id", help="synthetic match, e.g. SYN-001")
    parser.add_argument("--qr-url", help="put a login link in the QR codes: <URL>?ticket=<code> (default: the code only)")
    parser.add_argument("--crests", type=Path, help="folder of <club_id>.svg crests (default: synthetic/crests)")
    parser.add_argument("--out", type=Path, help="output folder (default: synthetic/<match_id>/tickets)")
    args = parser.parse_args(argv)

    result = build(args.match_id, qr_url=args.qr_url, crests_dir=args.crests)
    for name, problems in result.results:
        print(f"{'pass' if not problems else 'FAIL'}  {name}")
        for p in problems[:10]:
            print(f"        {p}")
    if not result.ok:
        print("Nothing written: fix the failing checks first.", file=sys.stderr)
        return 1
    out_dir = args.out or REPO_ROOT / "synthetic" / args.match_id / "tickets"
    written = write(result, out_dir)
    counts = {name: sum(1 for t in result.tickets if t.tier.name == name) for name in ("demo", "gold", "silver")}
    print(f"{len(result.tickets)} tickets ({', '.join(f'{v} {k}' for k, v in counts.items())}); "
          f"{sum(1 for t in result.tickets if t.files)} rendered as SVG, PDF and PNG; {len(written)} files in {out_dir}")
    print(f"Codes for the judges: {out_dir / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
