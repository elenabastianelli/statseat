"""The printed tickets of SYN-001 (S-4): the generator's tickets.json read as a registry (contract tickets.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contract import tickets as tk

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "synthetic/SYN-001/tickets/tickets.json"
pytestmark = pytest.mark.skipif(not GENERATED.is_file(), reason="SYN-001 tickets not in this checkout")

# Published in synthetic/SYN-001/tickets/README.md
DEMO, DEMO5, GOLD, SILVER = ("GNT-SYN001-D-WHQF-W1S6", "GNT-SYN001-D-FY05-RRWR", "GNT-SYN001-G-DKXB-1W23",
                             "GNT-SYN001-S-RDY3-TK6X")


@pytest.fixture(scope="module")
def reg() -> tk.Registry:
    return tk.load_registry(GENERATED)


def test_registry_has_every_ticket_with_hashes_only(reg) -> None:
    assert reg.hash == "sha256" and len(reg.tickets) == 162
    assert {t.tier.value for t in reg.tickets} == {"silver", "gold", "demo"}  # no judge tier since L-0
    text = GENERATED.read_text()
    assert not any(code in text for code in (DEMO, DEMO5, GOLD, SILVER))  # plain codes never stored


@pytest.mark.parametrize("code, ticket_id, tier", [(DEMO, "SYN-001-D-001", "demo"), (DEMO5, "SYN-001-D-005", "demo"),
                                                     (GOLD, "SYN-001-G-001", "gold"), (SILVER, "SYN-001-S-002", "silver")])
def test_published_codes_log_in(reg, code, ticket_id, tier) -> None:
    t = reg.find(code)
    assert t is not None and (t.ticket_id, t.tier.value) == (ticket_id, tier)


def test_typed_codes_are_normalised_like_the_generator(reg) -> None:
    assert reg.find("gnt syn001 d whqf wis6") is reg.find(DEMO)  # case, spaces, I for 1
    assert reg.find("GNT.SYN001.D.FYO5.RRWR") is reg.find(DEMO5)  # dots, O for 0


def test_wrong_or_foreign_codes_are_refused(reg) -> None:
    assert reg.find("GNT-SYN001-D-WHQF-W1S7") is None  # one character off
    assert reg.find("GNT-SYN001-D-MK9G-UGKC") is None  # a fake code of app/contract/README.md
    assert reg.find("hello") is None


def test_my_ticket_payload_has_seat_gate_and_images(reg) -> None:
    from datetime import datetime, timezone

    t = reg.find(DEMO5)
    info = tk.ticket_info(t, {"match_id": "SYN-001"}, tk.stadium_of("SYN-001"), datetime(2026, 10, 24, tzinfo=timezone.utc))
    assert info["seat"]["sector_id"] == "W8" and info["seat"]["gate_id"] == "H" and info["valid_now"]
    assert [i["available"] for i in info["images"]] == [True, True, True]
