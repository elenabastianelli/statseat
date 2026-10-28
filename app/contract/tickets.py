"""Ticket tiers, ticket codes and the server-side ticket registry (hashes only).

Tier rules (24/10):
- silver = Casual only (Analyst refused with 403 mode_not_allowed);
- gold   = Analyst, with switch to Casual;
- demo   = gold + Inspector + demo phase panel, on several devices. The judges use demo tickets (25/10, task L-1:
  the judge tier is gone, so they can see every phase whenever they want).
One device per ticket, except demo (several devices allowed).

Codes: GNT-<MATCH>-<S|G|D>-XXXX-XXXX, e.g. GNT-SYN001-G-7KQM-X4HT (MATCH = match_id without '-').
The server never stores a code: the registry keeps HMAC-SHA256(salt, code) only. The salt comes from the
STATSEAT_TICKET_SALT environment variable; DEV_SALT below is a public, NOT SECRET default for local development and
for the fake tickets of app/contract/fake/server/tickets.json. A deployment sets its own salt and rebuilds the
registry with `python -m app.contract.tickets hash <manifest.json> <registry.json>`.

Manifest (plain codes, written by the ticket generator in synthetic/<match_id>/tickets/manifest.json, S-4):
    {"match_id": "SYN-001", "valid_from": "<ISO 8601>", "valid_until": "<ISO 8601>",
     "tickets": [{"ticket_id": "TKT-SYN001-G-0001", "code": "GNT-SYN001-G-....-....", "tier": "gold",
                  "seat": {"stand_id": "W", "sector_id": "W3", "row": "12", "seat": "7"} | null,
                  "files": {"svg": "TKT-SYN001-G-0001.svg", "pdf": "...", "png": "..."},
                  "valid_from": "<optional override>", "valid_until": "<optional override>"}]}
Registry (what the server reads, STATSEAT_TICKETS_FILE): the same entries with `code` replaced by `code_hash`, file
names expanded to repo paths, and validity always explicit (see `RegistryTicket`).

Generator tickets (S-4, contract v0.2.2): the ticket generator writes `synthetic/<match_id>/tickets/tickets.json`
with the plain SHA-256 of each normalised code (Crockford base32: O counts as 0, I and L as 1 in the last 8
characters). Its codes are derived from public inputs (universe seed, match id, generator code), so a salt would add
no secrecy: `load_registry` reads that file as is (`hash = "sha256"`). Validity: the demo window below.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Mode, TicketTier

ROOT = Path(__file__).resolve().parents[2]
DEV_SALT = "gantry-dev-salt-NOT-SECRET"  # public on purpose: dev and fake tickets only
DEV_SALT_LABEL = "dev-default"
SALT_ENV = "STATSEAT_TICKET_SALT"
REGISTRY_ENV = "STATSEAT_TICKETS_FILE"
DEFAULT_REGISTRY = Path(__file__).resolve().parent / "fake" / "server" / "tickets.json"
SCHEMA = "statseat-tickets/1"

TIER_LETTER = {TicketTier.silver: "S", TicketTier.gold: "G", TicketTier.demo: "D"}
IMAGE_ORDER = ("svg", "pdf", "png")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to type from a printed ticket
CODE_RE = re.compile(r"^GNT-(?P<match>[A-Z0-9]{3,12})-(?P<tier>[SGD])-(?P<a>[A-Z0-9]{4})-(?P<b>[A-Z0-9]{4})$")
GEN_SCHEMA_APP = "statseat"  # tickets.json of the generator has "app": "statseat" and no schema_version
GEN_SEPARATORS = re.compile(r"[\s\-_.]+")
GEN_COMPACT_RE = re.compile(r"^GNT(?P<match>[A-Z]+\d+)(?P<tier>[SGD])(?P<tail>[0-9A-Z]{8})$")
GEN_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1"})
# The demo stays online from the first rehearsal to past the end of judging (10 November, 23:59 Pacific time).
DEMO_VALID_FROM = "2026-10-20T00:00:00+01:00"
DEMO_VALID_UNTIL = "2026-11-15T23:59:59+01:00"


@dataclass(frozen=True)
class TierRules:
    allowed_modes: tuple[Mode, ...]
    default_mode: Mode
    inspector: bool
    demo_panel: bool
    multi_device: bool
    perks: tuple[str, ...]


_GOLD_PERKS = ("Analyst mode: advanced charts, xG, zone maps and the defensive line", "Switch to Fan at any time")
TIER_RULES: dict[TicketTier, TierRules] = {
    TicketTier.silver: TierRules((Mode.casual,), Mode.casual, False, False, False,
                                 ("Fan mode: the match story, key numbers and games",)),
    TicketTier.gold: TierRules((Mode.analyst, Mode.casual), Mode.analyst, False, False, False, _GOLD_PERKS),
    TicketTier.demo: TierRules((Mode.analyst, Mode.casual), Mode.analyst, True, True, True,
                               _GOLD_PERKS + ("Inspector: agents, tools, tokens and methods behind every answer",
                                              "Demo panel: jump to any phase of the match", "Works on several devices")),
}


def normalise(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def parse_code(code: str) -> tuple[str, TicketTier] | None:
    """(match part, tier) of a well-formed code, else None. Informative only: the registry decides."""
    m = CODE_RE.match(normalise(code))
    if m is None:
        return None
    tier = next(t for t, letter in TIER_LETTER.items() if letter == m["tier"])
    return m["match"], tier


def normalise_generator(code: str) -> str | None:
    """Canonical form of a typed or scanned code for generator tickets (insidethegame_gen.tickets.codes):
    separators removed, O/I/L read as 0/1 in the last 8 characters. The check character is not verified here: an
    invalid code simply matches no hash."""
    m = GEN_COMPACT_RE.match(GEN_SEPARATORS.sub("", code.strip().upper()))
    if m is None:
        return None
    tail = m["tail"].translate(GEN_ALIASES)
    return f"GNT-{m['match']}-{m['tier']}-{tail[:4]}-{tail[4:]}"


def match_part(match_id: str) -> str:
    return match_id.replace("-", "").upper()


def salt() -> tuple[str, str]:
    """(salt, label): STATSEAT_TICKET_SALT if set, else the public dev default."""
    env = os.environ.get(SALT_ENV)
    return (env, "deployment") if env else (DEV_SALT, DEV_SALT_LABEL)


def hash_code(code: str, key: str | None = None) -> str:
    return hmac.new((key or salt()[0]).encode(), normalise(code).encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- registry (server-only file)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeatRef(_Strict):
    stand_id: str
    sector_id: str
    row: str
    seat: str


class RegistryTicket(_Strict):
    ticket_id: str
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tier: TicketTier
    match_id: str
    seat: SeatRef | None
    valid_from: datetime
    valid_until: datetime
    files: dict[Literal["svg", "pdf", "png"], str] = Field(description="Repo paths of the ticket images")


class Registry(_Strict):
    schema_version: Literal["statseat-tickets/1"] = SCHEMA
    hash: Literal["hmac-sha256", "sha256"] = "hmac-sha256"
    salt_label: str = Field(description="dev-default (public DEV_SALT), deployment (STATSEAT_TICKET_SALT) or none "
                                        "(generator tickets, sha256)")
    tickets: list[RegistryTicket]

    def find(self, code: str, key: str | None = None) -> RegistryTicket | None:
        if self.hash == "sha256":
            canon = normalise_generator(code)
            if canon is None:
                return None
            h = hashlib.sha256(canon.encode()).hexdigest()
        else:
            h = hash_code(code, key)
        return next((t for t in self.tickets if hmac.compare_digest(t.code_hash, h)), None)


def registry_from_manifest(manifest: dict, key: str | None = None, label: str | None = None) -> Registry:
    """Hash the plain codes of a ticket manifest (shape in the module docstring). Checks format and tier letter."""
    match_id = manifest["match_id"]
    base = f"synthetic/{match_id}/tickets"
    out = []
    for t in manifest["tickets"]:
        tier = TicketTier(t["tier"])
        parsed = parse_code(t["code"])
        if parsed is None or parsed != (match_part(match_id), tier):
            raise ValueError(f"{t['ticket_id']}: code does not match GNT-{match_part(match_id)}-{TIER_LETTER[tier]}-XXXX-XXXX")
        out.append(RegistryTicket(
            ticket_id=t["ticket_id"], code_hash=hash_code(t["code"], key), tier=tier, match_id=match_id,
            seat=t.get("seat"), valid_from=t.get("valid_from", manifest["valid_from"]),
            valid_until=t.get("valid_until", manifest["valid_until"]),
            files={fmt: f"{base}/{name}" for fmt, name in t.get("files", {}).items()}))
    if len({t.code_hash for t in out}) != len(out):
        raise ValueError("duplicate ticket codes")
    used = key or salt()[0]
    return Registry(salt_label=label or (DEV_SALT_LABEL if used == DEV_SALT else "deployment"), tickets=out)


# ---------------------------------------------------------------- My ticket and session views


@lru_cache(maxsize=None)
def stadium_of(match_id: str) -> dict:
    u = json.loads((ROOT / "synthetic/universe/universe.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / f"synthetic/{match_id}/match_info.json").read_text(encoding="utf-8"))
    return next(s for s in u["stadiums"] if s["id"] == m["environment"]["stadium_id"])


def seat_view(ref: SeatRef | None, stadium: dict) -> dict | None:
    if ref is None:
        return None
    stand = next(s for s in stadium["stands"] if s["id"] == ref.stand_id)
    sector = next(x for x in stand["sectors"] if x["id"] == ref.sector_id)
    gate = next((g["id"] for g in stadium.get("gates", []) if ref.sector_id in g["serves"]), None)
    return {"stand_id": stand["id"], "stand_name": stand["name"], "sector_id": sector["id"],
            "sector_type": sector["type"], "gate_id": gate, "row": ref.row, "seat": ref.seat}


def features(tier: TicketTier) -> dict:
    r = TIER_RULES[tier]
    return {"allowed_modes": [m.value for m in r.allowed_modes], "default_mode": r.default_mode.value,
            "inspector": r.inspector, "demo_panel": r.demo_panel, "multi_device": r.multi_device, "perks": list(r.perks)}


def ticket_info(t: RegistryTicket, header: dict, stadium: dict, now: datetime) -> dict:
    """Payload of GET /api/ticket (model TicketInfo). Holder-agnostic; the code is never part of it."""
    return {
        "ticket_id": t.ticket_id, "tier": t.tier.value, "match": header,
        "seat": seat_view(t.seat, stadium), "valid_from": t.valid_from.isoformat(),
        "valid_until": t.valid_until.isoformat(), "valid_now": t.valid_from <= now <= t.valid_until,
        "features": features(t.tier),
        "images": [{"format": fmt, "url": f"/api/ticket/image?format={fmt}", "path": path,
                    "available": (ROOT / path).is_file()} for fmt, path in sorted(t.files.items(), key=lambda kv: IMAGE_ORDER.index(kv[0]))],
    }


def registry_from_generator(doc: dict, base: str) -> Registry:
    """The generator's tickets.json (S-4) as a registry. `base`: repo folder of the file, for the image paths."""
    out = []
    for t in doc["tickets"]:
        seat = t.get("seat")
        out.append(RegistryTicket(
            ticket_id=t["ticket_id"], code_hash=t["code_sha256"], tier=TicketTier(t["tier"]), match_id=doc["match_id"],
            seat=None if seat is None else SeatRef(stand_id=seat["stand_id"], sector_id=seat["sector"],
                                                   row=str(seat["row"]), seat=str(seat["seat"])),
            valid_from=DEMO_VALID_FROM, valid_until=DEMO_VALID_UNTIL,
            files={fmt: f"{base}/{name}" for fmt, name in (t.get("files") or {}).items()}))  # unprinted: none
    return Registry(hash="sha256", salt_label="none", tickets=out)


def load_registry(path: Path | None = None) -> Registry:
    """STATSEAT_TICKETS_FILE, else `path`, else the fake registry. Reads both a contract registry and the generator's
    tickets.json."""
    path = Path(os.environ.get(REGISTRY_ENV, path or DEFAULT_REGISTRY))
    doc = json.loads(path.read_text(encoding="utf-8"))
    if "schema_version" not in doc and doc.get("app") == GEN_SCHEMA_APP:
        try:
            base = path.resolve().parent.relative_to(ROOT).as_posix()
        except ValueError:
            base = path.resolve().parent.as_posix()
        return registry_from_generator(doc, base)
    return Registry.model_validate(doc)


def main(argv: list[str]) -> None:
    if len(argv) != 3 or argv[0] != "hash":
        sys.exit("usage: python -m app.contract.tickets hash <manifest.json> <registry.json>  (salt from STATSEAT_TICKET_SALT)")
    reg = registry_from_manifest(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    Path(argv[2]).write_text(reg.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(reg.tickets)} hashed tickets to {argv[2]} (salt: {reg.salt_label})")


if __name__ == "__main__":
    main(sys.argv[1:])
