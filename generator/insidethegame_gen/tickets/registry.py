"""Ticket tiers, seats and the registry (`tickets.json`) of one synthetic match.

Everything is deterministic: the codes come from `codes.make_code` (universe seed, match id,
tier, ticket number) and the seats from one random stream seeded like Scout's
(`random.Random(f"{seed}:tickets:{match_id}")`).

Seat rules (from the stadium data in `universe.json`):
- DEMO: one block in the Club Level of the main stand (the stand named "Main", with the
  tunnel and the dugouts), so the judges, who use DEMO tickets, sit together;
- GOLD: the two central home sectors of each stand along a touchline;
- SILVER: round-robin over every other home sector;
- never an away sector or an away gate; the gate is the one that serves the sector
  (the data gives exactly one gate per sector).
Rows and seat numbers are synthetic: about 80 seats per row, so a 2,000-seat sector has
25 rows of 80 seats.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..names import WordSet, contains_word
from .codes import CODE_PATTERN, PREFIX, TIER_LETTER, code_sha256, make_code, match_token, validate_code

TICKETS_VERSION = "1.0.0"
SEATS_PER_ROW = 80
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
SECTOR_TYPE_LABEL = {"home": "Home", "family": "Family zone", "accessible": "Accessible seating",
                     "club_level": "Club Level", "away": "Away"}
DISCLAIMER = "Synthetic demo ticket – not valid for entry. Fictional match, clubs and people."


@dataclass(frozen=True)
class Tier:
    name: str
    count: int
    label: str  # printed on the ribbon
    modes: tuple[str, ...]
    default_mode: str
    inspector: bool
    demo_panel: bool
    multi_device: bool
    accent: str  # "gold" or "silver"
    rendered: int  # how many tickets of this tier get SVG, PDF and PNG files
    unlocks: str  # one line for the README

    @property
    def letter(self) -> str:
        return TIER_LETTER[self.name]


TIERS: dict[str, Tier] = {t.name: t for t in (
    Tier("demo", 12, "DEMO · Gold + Inspector", ("analyst", "casual"), "analyst", True, True, True, "gold", 12,
         "Gold, plus Inspector mode and the demo panel that jumps between match phases"),
    Tier("gold", 30, "GOLD · Analyst", ("analyst", "casual"), "analyst", False, False, False, "gold", 4,
         "Analyst mode, with a switch to Fan"),
    Tier("silver", 120, "SILVER · Fan", ("casual",), "casual", False, False, False, "silver", 4,
         "Fan mode"),
)}


@dataclass(frozen=True)
class Seat:
    gate: str
    stand_id: str
    stand: str
    side: str
    sector: str
    sector_type: str
    row: int
    seat: int

    def as_json(self) -> dict:
        return {"gate": self.gate, "stand_id": self.stand_id, "stand": self.stand, "sector": self.sector,
                "sector_type": self.sector_type, "row": self.row, "seat": self.seat}


@dataclass(frozen=True)
class Club:
    id: str
    name: str
    short_name: str
    rest: str  # the part of the name after the short name ("Athletic")
    initials: str
    founded: int
    shirt: str
    accent: str


@dataclass(frozen=True)
class MatchContext:
    match_id: str
    universe_id: str
    seed: int
    match_name: str
    competition: str
    season: str
    matchday: int
    kickoff: str  # ISO, local time with offset
    date_text: str  # "Saturday 24 October 2026"
    time_text: str  # "15:00"
    gates_open_text: str | None  # "13:30"
    stadium_id: str
    stadium: str
    address: str
    home: Club
    away: Club


@dataclass
class Ticket:
    match_id: str
    tier: Tier
    index: int  # 1-based within the tier
    code: str
    seat: Seat
    alt: str = ""
    files: dict[str, str] | None = None
    published: bool = False  # the plain code is printed in the README or on a rendered image

    @property
    def ticket_id(self) -> str:
        return f"{self.match_id}-{self.tier.letter}-{self.index:03d}"

    @property
    def stem(self) -> str:
        return f"{self.tier.name}-{self.index:03d}"

    def as_json(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "tier": self.tier.name,
            "code_sha256": code_sha256(self.code),
            "code_published": self.published,
            "modes": list(self.tier.modes),
            "default_mode": self.tier.default_mode,
            "inspector": self.tier.inspector,
            "demo_panel": self.tier.demo_panel,
            "multi_device": self.tier.multi_device,
            "seat": self.seat.as_json(),
            "files": self.files,
            "alt": self.alt,
        }


# --- Match context -----------------------------------------------------------------


def load_inputs(repo_root: Path, match_id: str) -> tuple[dict, dict]:
    universe = json.loads((repo_root / "synthetic" / "universe" / "universe.json").read_text(encoding="utf-8"))
    match = json.loads((repo_root / "synthetic" / match_id / "match_info.json").read_text(encoding="utf-8"))
    return universe, match


def luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def club_of(universe: dict, club_id: str) -> Club:
    club = next(c for c in universe["clubs"] if c["id"] == club_id)
    details = next((d for d in universe.get("club_details", []) if d["club_id"] == club_id), None)
    kit = (details or {}).get("kits", {}).get("home") or {}
    shirt = kit.get("shirt") or club["colours"]["primary"]
    candidates = [kit.get("socks"), kit.get("shorts"), club["colours"].get("secondary"), "#FFFFFF"]
    accent = next(c for c in candidates if c and c.upper() != shirt.upper() and contrast(c, shirt) >= 3.0)
    short = club.get("short_name") or club["name"]
    rest = club["name"][len(short):].strip() if club["name"].startswith(short) else ""
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", club["name"]) if w[0].isupper())[:3]
    return Club(club_id, club["name"], short, rest, initials, club["founded"], shirt, accent)


def build_context(universe: dict, match: dict) -> MatchContext:
    stadium = next(s for s in universe["stadiums"] if s["id"] == match["environment"]["stadium_id"])
    competition = next(c["name"] for c in universe["competitions"] if c["id"] == match["competition_id"])
    pair = {match["home_team_id"], match["guest_team_id"]}
    derby = next((h.get("name") for h in universe.get("head_to_head", []) if {h["club_a"], h["club_b"]} == pair), None)
    home, away = club_of(universe, match["home_team_id"]), club_of(universe, match["guest_team_id"])
    kickoff = datetime.fromisoformat(match["planned_kickoff"])  # local time, as printed on the ticket
    gates_open = None
    for policy in stadium.get("policies", []):
        found = re.search(r"Gates open (\d+) minutes before kick-off", policy)
        if found:
            gates_open = (kickoff - timedelta(minutes=int(found.group(1)))).strftime("%H:%M")
    return MatchContext(
        match_id=match["match_id"],
        universe_id=universe["meta"]["universe_id"],
        seed=universe["meta"]["seed"],
        match_name=derby or f"{home.name} v {away.name}",
        competition=competition,
        season=match["season"],
        matchday=match["matchday"],
        kickoff=match["planned_kickoff"],
        date_text=f"{WEEKDAYS[kickoff.weekday()]} {kickoff.day} {MONTHS[kickoff.month - 1]} {kickoff.year}",
        time_text=kickoff.strftime("%H:%M"),
        gates_open_text=gates_open,
        stadium_id=stadium["id"],
        stadium=stadium["name"],
        address=stadium["address"],
        home=home,
        away=away,
    )


# --- Seats -------------------------------------------------------------------------


def sector_layout(capacity: int) -> tuple[int, int]:
    """(rows, seats per row) of a sector: about SEATS_PER_ROW seats per row."""
    rows = max(1, round(capacity / SEATS_PER_ROW))
    return rows, math.ceil(capacity / rows)


def seat_exists(capacity: int, row: int, seat: int) -> bool:
    rows, per_row = sector_layout(capacity)
    return 1 <= row <= rows and 1 <= seat <= per_row and (row - 1) * per_row + seat <= capacity


def gate_map(stadium: dict) -> dict[str, dict]:
    """sector id -> the gate that serves it. The data gives exactly one gate per sector."""
    out: dict[str, dict] = {}
    for gate in stadium["gates"]:
        for sector in gate["serves"]:
            if sector in out:
                raise ValueError(f"sector {sector} served by gates {out[sector]['id']} and {gate['id']}")
            out[sector] = gate
    missing = [s["id"] for st in stadium["stands"] for s in st["sectors"] if s["id"] not in out]
    if missing:
        raise ValueError(f"sectors without a gate: {missing}")
    return out


def is_away_gate(gate: dict) -> bool:
    return "away" in (gate.get("note") or "").lower()


def main_stand(stadium: dict) -> dict:
    """The stand that best fits "main stand": its name says so, else it has the players' tunnel."""
    stands = stadium["stands"]
    for st in stands:
        if re.search(r"\bmain\b", st["name"], re.IGNORECASE):
            return st
    for st in stands:
        if "tunnel" in st.get("description", "").lower():
            return st
    return stands[0]


def touchline_stands(stadium: dict) -> list[dict]:
    stands = [st for st in stadium["stands"] if "touchline" in st.get("description", "").lower()]
    return stands or [st for st in stadium["stands"] if st.get("side") in ("east", "west")]


def central_home_sectors(stand: dict, n: int = 2) -> list[dict]:
    """The n home sectors closest to the middle of the stand's row of seating blocks.

    Blocks are the home and family sectors in their numbered order; accessible and Club Level
    sectors are special areas, not part of the row.
    """
    blocks = [s for s in stand["sectors"] if s["type"] in ("home", "family")]
    middle = (len(blocks) - 1) / 2
    ranked = sorted(range(len(blocks)), key=lambda i: (abs(i - middle), i))
    return [blocks[i] for i in ranked if blocks[i]["type"] == "home"][:n]


@dataclass
class SeatPlan:
    """Which sectors each tier uses, decided from the stadium data."""
    premium: dict  # demo
    gold: list[dict]
    silver: list[dict]
    stands: dict[str, dict] = field(default_factory=dict)  # sector id -> stand


def seat_plan(stadium: dict) -> SeatPlan:
    stand_of = {s["id"]: st for st in stadium["stands"] for s in st["sectors"]}
    gates = gate_map(stadium)
    main = main_stand(stadium)
    club_level = [s for s in main["sectors"] if s["type"] == "club_level"]
    premium = club_level[0] if club_level else central_home_sectors(main, 1)[0]
    touchlines = [main] + [st for st in touchline_stands(stadium) if st["id"] != main["id"]]
    gold = [s for st in touchlines for s in central_home_sectors(st)]
    taken = {premium["id"]} | {s["id"] for s in gold}
    silver = [s for st in stadium["stands"] for s in st["sectors"]
              if s["type"] == "home" and s["id"] not in taken and not is_away_gate(gates[s["id"]])]
    return SeatPlan(premium, gold, silver, stand_of)


def make_seat(stadium: dict, plan: SeatPlan, sector: dict, row: int, seat: int) -> Seat:
    stand = plan.stands[sector["id"]]
    return Seat(gate=gate_map(stadium)[sector["id"]]["id"], stand_id=stand["id"], stand=stand["name"],
                side=stand.get("side", ""), sector=sector["id"], sector_type=sector["type"], row=row, seat=seat)


def allocate_seats(stadium: dict, rng: random.Random) -> dict[str, list[Seat]]:
    plan = seat_plan(stadium)
    out: dict[str, list[Seat]] = {}
    used: set[tuple[str, int, int]] = set()

    # DEMO: one centred block, a quarter of the way up the Club Level.
    block = TIERS["demo"].count
    rows, per_row = sector_layout(plan.premium["capacity"])
    row = rows // 4 + 1
    first = (per_row - block) // 2 + 1
    seats = [make_seat(stadium, plan, plan.premium, row, first + i) for i in range(block)]
    out["demo"] = seats
    used |= {(s.sector, s.row, s.seat) for s in seats}

    def draw(tier: str, sectors: list[dict], central: bool) -> None:
        out[tier] = []
        for i in range(TIERS[tier].count):
            sector = sectors[i % len(sectors)]
            rows, per_row = sector_layout(sector["capacity"])
            while True:
                if central:  # lower half of the stand, middle half of the row
                    row = rng.randint(3, max(3, rows // 2))
                    seat = rng.randint(per_row // 4, 3 * per_row // 4)
                else:
                    row, seat = rng.randint(1, rows), rng.randint(1, per_row)
                if seat_exists(sector["capacity"], row, seat) and (sector["id"], row, seat) not in used:
                    break
            used.add((sector["id"], row, seat))
            out[tier].append(make_seat(stadium, plan, sector, row, seat))

    draw("gold", plan.gold, central=True)
    draw("silver", plan.silver, central=False)
    return out


# --- Tickets and registry -------------------------------------------------------------


def alt_text(ctx: MatchContext, ticket: Ticket) -> str:
    s = ticket.seat
    return (f"statseat digital ticket, {ticket.tier.label}. {ctx.competition}, "
            f"matchday {ctx.matchday}: {ctx.home.name} v {ctx.away.name}, {ctx.match_name}. {ctx.date_text}, "
            f"kick-off {ctx.time_text}, {ctx.stadium}, {ctx.address}. Gate {s.gate}, {s.stand}, sector {s.sector}, "
            f"row {s.row}, seat {s.seat}. Shows the ticket code and a QR code to log in to the statseat app. "
            f"{DISCLAIMER}")


def build_tickets(universe: dict, match: dict, blocklist: dict | None = None) -> tuple[MatchContext, list[Ticket]]:
    """All tickets of the match, in tier order (demo, gold, silver), with plain codes in memory only.

    With a blocklist, a code whose 4-character groups spell a blocked word gets another attempt.
    """
    ctx = build_context(universe, match)
    stadium = next(s for s in universe["stadiums"] if s["id"] == ctx.stadium_id)
    seats = allocate_seats(stadium, random.Random(f"{ctx.seed}:tickets:{ctx.match_id}"))
    tickets: list[Ticket] = []
    codes: set[str] = set()
    for tier in TIERS.values():
        for index in range(1, tier.count + 1):
            attempt = 0
            while ((code := make_code(ctx.seed, ctx.match_id, tier.name, index, attempt)) in codes
                   or (blocklist and text_problems({"code": code}, blocklist))):
                attempt += 1
            codes.add(code)
            ticket = Ticket(ctx.match_id, tier, index, code, seats[tier.name][index - 1])
            if index <= tier.rendered:
                ticket.published = True
                ticket.files = {fmt: f"{fmt}/{ticket.stem}.{fmt}" for fmt in ("svg", "pdf", "png")}
            ticket.alt = alt_text(ctx, ticket)
            tickets.append(ticket)
    return ctx, tickets


def registry_json(ctx: MatchContext, tickets: list[Ticket], qr_content: str) -> dict:
    counts = {t: sum(1 for k in tickets if k.tier.name == t) for t in TIERS}
    return {
        "match_id": ctx.match_id,
        "universe_id": ctx.universe_id,
        "seed": ctx.seed,
        "generator": f"insidethegame_gen.tickets {TICKETS_VERSION} (deterministic)",
        "app": "statseat",
        "disclaimer": DISCLAIMER,
        "code_format": {
            "pattern": f"{PREFIX}-{match_token(ctx.match_id)}-<S|G|D>-XXXX-XXXX",
            "regex": CODE_PATTERN,
            "alphabet": "Crockford base32 (0-9 and A-Z without I, L, O, U); on input O counts as 0 and I or L as 1",
            "check_character": "the last character: Luhn mod 32 over GNT, the match token, the tier letter and the "
                               "7 characters before it",
            "normalise": "upper case, remove spaces, dashes, dots and underscores, then GNT-<match>-<tier>-XXXX-XXXX",
            "hash": "code_sha256 = SHA-256 (hex) of the normalised code in UTF-8; plain codes are never stored",
            "qr_content": qr_content,
        },
        "match": {
            "name": ctx.match_name,
            "competition": ctx.competition,
            "season": ctx.season,
            "matchday": ctx.matchday,
            "kickoff_local": ctx.kickoff,
            "home_team_id": ctx.home.id,
            "guest_team_id": ctx.away.id,
            "stadium_id": ctx.stadium_id,
        },
        "tiers": {t.name: {"letter": t.letter, "label": t.label, "modes": list(t.modes), "default_mode": t.default_mode,
                           "inspector": t.inspector, "demo_panel": t.demo_panel, "multi_device": t.multi_device,
                           "devices": "several" if t.multi_device else "one", "count": t.count}
                  for t in TIERS.values()},
        "counts": counts | {"total": len(tickets)},
        "tickets": [t.as_json() for t in tickets],
    }


# --- Checks ------------------------------------------------------------------------


def check_registry(ctx: MatchContext, tickets: list[Ticket], universe: dict) -> list[tuple[str, list[str]]]:
    """(check name, problems) for the registry; any problem stops the generator before it writes."""
    stadium = next(s for s in universe["stadiums"] if s["id"] == ctx.stadium_id)
    sectors = {s["id"]: s for st in stadium["stands"] for s in st["sectors"]}
    gates = gate_map(stadium)
    results = []

    problems = [f"{t}: {sum(1 for k in tickets if k.tier.name == t)} tickets, expected {TIERS[t].count}"
                for t in TIERS if sum(1 for k in tickets if k.tier.name == t) != TIERS[t].count]
    results.append(("Ticket counts per tier", problems))

    problems = []
    for t in tickets:
        ok, tier, match_id = validate_code(t.code)
        if not ok or tier != t.tier.name or match_id != ctx.match_id or not re.fullmatch(CODE_PATTERN, t.code):
            problems.append(f"{t.ticket_id}: code does not validate as {t.tier.name} {ctx.match_id}")
    if len({t.code for t in tickets}) != len(tickets):
        problems.append("duplicate codes")
    if len({code_sha256(t.code) for t in tickets}) != len(tickets):
        problems.append("duplicate hashes")
    results.append(("Codes valid, unique, with the right tier and match", problems))

    problems = []
    seen: set[tuple[str, int, int]] = set()
    for t in tickets:
        s = t.seat
        key = (s.sector, s.row, s.seat)
        if key in seen:
            problems.append(f"{t.ticket_id}: seat {key} sold twice")
        seen.add(key)
        sector = sectors.get(s.sector)
        if sector is None or not seat_exists(sector["capacity"], s.row, s.seat):
            problems.append(f"{t.ticket_id}: seat {key} does not exist")
            continue
        if sector["type"] == "away" or is_away_gate(gates[s.sector]):
            problems.append(f"{t.ticket_id}: home ticket in the away section {s.sector}")
        if gates[s.sector]["id"] != s.gate:
            problems.append(f"{t.ticket_id}: gate {s.gate}, but {s.sector} is served by gate {gates[s.sector]['id']}")
    main = main_stand(stadium)["id"]
    problems += [f"{t.ticket_id}: not in the main stand" for t in tickets
                 if t.tier.name == "demo" and t.seat.stand_id != main]
    results.append(("Seats exist, unique, home sections only, right gate", problems))
    return results


def text_problems(texts: dict[str, str], blocklist: dict) -> list[str]:
    """Blocklist checks on every text of the outputs (same lists as the universe validator)."""
    problems = []
    allowed = [p.lower() for p in blocklist.get("allowed_phrases", [])]
    full_names = blocklist["famous_full_names"]
    lists = {
        "real person": WordSet(full_names + blocklist["famous_surnames"]),
        "real club or stadium": WordSet(blocklist["real_club_tokens"] + blocklist["real_stadium_tokens"]),
        "brand": WordSet(blocklist["brand_tokens"]),
        "alcohol, betting or tobacco word": WordSet(blocklist["forbidden_words"]),
    }
    words = {"real person": full_names + blocklist["famous_surnames"],
             "real club or stadium": blocklist["real_club_tokens"] + blocklist["real_stadium_tokens"],
             "brand": blocklist["brand_tokens"], "alcohol, betting or tobacco word": blocklist["forbidden_words"]}
    for name, text in texts.items():
        clean = text.lower()
        for phrase in allowed:
            clean = re.sub(rf"(?<![\w]){re.escape(phrase)}(?![\w])", " ", clean)
        for kind, wordset in lists.items():
            if wordset.found_in(clean):
                hits = sorted({w for w in words[kind] if contains_word(clean, w)})
                problems.append(f"{name}: {kind} {hits}")
    return problems
