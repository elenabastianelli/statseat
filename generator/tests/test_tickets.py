"""Tests for the statseat digital tickets: codes, registry, seats, rendering and determinism.

The match SYN-001 is synthetic, so the real inputs are the fixture. One build (about 2 s) is
shared by the module; the determinism test builds a second time and compares every byte.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from insidethegame_gen.tickets import TIERS, code_sha256, make_code, normalise_code, validate_code
from insidethegame_gen.tickets.__main__ import BLOCKLIST, build, main
from insidethegame_gen.tickets.codes import ALPHABET, CODE_PATTERN, check_character
from insidethegame_gen.tickets.registry import (central_home_sectors, main_stand, sector_layout, seat_exists,
                                                text_problems)
from insidethegame_gen.tickets.render import CREST_BOXES, PNG_SIZE, qr_geometry, qr_symbol

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE = make_code(2026, "SYN-001", "gold", 1)
COUNTS = {"demo": 12, "gold": 30, "silver": 120}


@pytest.fixture(scope="module")
def built():
    return build("SYN-001", crests_dir=REPO_ROOT / "no-crests-here")


@pytest.fixture(scope="module")
def registry(built):
    return json.loads(built.files["tickets.json"])


@pytest.fixture(scope="module")
def stadium():
    universe = json.loads((REPO_ROOT / "synthetic" / "universe" / "universe.json").read_text(encoding="utf-8"))
    return universe["stadiums"][0]


def png_at(png: bytes, x: float, y: float) -> tuple[int, int, int]:
    """Pixel of the PNG at SVG coordinates (x, y)."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    k = PNG_SIZE[0] / 720
    return image.getpixel((int(x * k), int(y * k)))


# --- Codes -----------------------------------------------------------------------------


def test_code_format():
    for tier, letter in (("silver", "S"), ("gold", "G"), ("demo", "D")):
        code = make_code(2026, "SYN-001", tier, 7)
        assert re.fullmatch(CODE_PATTERN, code), code
        assert code.startswith(f"GNT-SYN001-{letter}-")
        assert all(ch in ALPHABET for ch in code[-9:].replace("-", ""))
        assert not set(code[-9:]) & set("ILOU")


def test_codes_are_deterministic_and_distinct():
    assert make_code(2026, "SYN-001", "gold", 1) == CODE
    others = {make_code(2026, "SYN-001", "gold", 2), make_code(2027, "SYN-001", "gold", 1),
              make_code(2026, "SYN-002", "gold", 1), make_code(2026, "SYN-001", "silver", 1)}
    assert CODE not in others and len(others) == 4


def test_tier_and_match_parsing():
    for tier in ("silver", "gold", "demo"):
        assert validate_code(make_code(2026, "SYN-001", tier, 3)) == (True, tier, "SYN-001")
    assert validate_code(make_code(2026, "SYN-042", "demo", 1)) == (True, "demo", "SYN-042")
    with pytest.raises(ValueError):
        make_code(2026, "SYN-001", "platinum", 1)
    with pytest.raises(ValueError):
        make_code(2026, "SYN-001", "judge", 1)  # no judge tier since L-0: the judges use DEMO codes
    assert not validate_code("GNT-SYN001-J-TPJ0-N4B1")[0]


def test_validate_accepts_normalised_variants():
    compact = CODE.replace("-", "")
    tail = CODE[-9:]
    variants = [CODE.lower(), f"  {CODE}  ", compact, compact.lower(), " ".join(CODE.split("-")),
                f"GNT SYN001 G {tail}", CODE.replace("-", "--"), CODE.replace("-", "_")]
    # Crockford look-alikes: 0 typed as O, 1 typed as I or L
    variants += [CODE[:-9] + tail.replace("0", "O").replace("1", "I"), CODE[:-9] + tail.replace("1", "l")]
    for v in variants:
        assert validate_code(v) == (True, "gold", "SYN-001"), v
        assert normalise_code(v) == CODE
        assert code_sha256(v) == code_sha256(CODE)


def test_validate_rejects_wrong_codes():
    wrong = ["", "GNT", "hello", CODE[:-1], CODE + "X", "XYZ" + CODE[3:], CODE.replace("-G-", "-X-"),
             CODE.replace("-G-", "-S-"), CODE.replace("SYN001", "SYN002"), CODE.replace("GNT-", "GNT-X"),
             CODE[:-1] + ("U" if CODE[-1] != "U" else "V"), None, 12345]
    for w in wrong:
        assert validate_code(w) == (False, None, None), w
    with pytest.raises(ValueError):
        code_sha256(CODE[:-1] + next(ch for ch in ALPHABET if ch != CODE[-1]))  # wrong check character
    assert check_character("GNTSYN001G" + CODE.replace("-", "")[-8:-1]) == CODE[-1]


def test_check_character_catches_every_single_typo():
    body = CODE.replace("-", "")
    caught = 0
    for i in range(len("GNT"), len(body)):  # match token, tier letter and the 8 code characters
        for ch in ALPHABET:
            if ch == body[i]:
                continue
            typo = body[:i] + ch + body[i + 1:]
            assert validate_code(typo)[0] is False, typo
            caught += 1
    assert caught == (len(body) - 3) * 31


def test_check_character_catches_neighbour_swaps():
    tail = CODE.replace("-", "")[-8:]
    swaps = [tail[:i] + tail[i + 1] + tail[i] + tail[i + 2:] for i in range(7) if tail[i] != tail[i + 1]]
    assert swaps and all(not validate_code("GNTSYN001G" + s)[0] for s in swaps)


# --- Registry and seats ------------------------------------------------------------------


def test_all_checks_pass(built):
    assert built.ok, [r for r in built.results if r[1]]


def test_counts_per_tier(registry):
    tickets = registry["tickets"]
    assert {t: sum(1 for k in tickets if k["tier"] == t) for t in COUNTS} == COUNTS
    assert registry["counts"] == COUNTS | {"total": 162}


def test_tier_features(registry):
    by_tier = {t["tier"]: t for t in registry["tickets"]}
    assert by_tier["silver"]["modes"] == ["casual"] and not by_tier["silver"]["inspector"]
    assert by_tier["gold"]["modes"] == ["analyst", "casual"] and by_tier["gold"]["default_mode"] == "analyst"
    assert by_tier["demo"]["inspector"] and by_tier["demo"]["demo_panel"] and by_tier["demo"]["multi_device"]
    assert not by_tier["gold"]["multi_device"] and not by_tier["silver"]["multi_device"]


def test_registry_has_hashes_not_codes(built, registry):
    text = built.files["tickets.json"].decode("utf-8")
    for t in built.tickets:
        assert t.code not in text and t.code.replace("-", "") not in text
    assert [k["code_sha256"] for k in registry["tickets"]] == [code_sha256(t.code) for t in built.tickets]
    assert all(re.fullmatch(r"[0-9a-f]{64}", k["code_sha256"]) for k in registry["tickets"])
    assert len({k["code_sha256"] for k in registry["tickets"]}) == 162


def test_only_published_codes_appear_in_readme(built):
    readme = built.files["README.md"].decode("utf-8")
    for t in built.tickets:
        assert (t.code in readme) == t.published, t.ticket_id
    assert sum(t.published for t in built.tickets) == 12 + 4 + 4


def test_no_duplicate_seats_and_seats_exist(registry, stadium):
    seats = [(k["seat"]["sector"], k["seat"]["row"], k["seat"]["seat"]) for k in registry["tickets"]]
    assert len(set(seats)) == len(seats)
    capacity = {s["id"]: s["capacity"] for st in stadium["stands"] for s in st["sectors"]}
    assert all(seat_exists(capacity[sec], row, seat) for sec, row, seat in seats)


def test_seats_follow_the_stadium_data(registry, stadium):
    sector_type = {s["id"]: s["type"] for st in stadium["stands"] for s in st["sectors"]}
    gate_of = {sec: g["id"] for g in stadium["gates"] for sec in g["serves"]}
    for k in registry["tickets"]:
        seat = k["seat"]
        assert sector_type[seat["sector"]] != "away" and seat["gate"] != "F", k["ticket_id"]
        assert seat["gate"] == gate_of[seat["sector"]]
        if k["tier"] == "demo":
            assert (seat["stand"], seat["sector"], seat["sector_type"], seat["gate"]) == \
                   ("Main Stand", "W8", "club_level", "H")
        elif k["tier"] == "gold":
            assert seat["sector"] in {"W3", "W4", "E4", "E5"}
        else:
            assert sector_type[seat["sector"]] == "home" and seat["sector"] not in {"W3", "W4", "E4", "E5"}
    silver_sectors = {k["seat"]["sector"] for k in registry["tickets"] if k["tier"] == "silver"}
    assert len(silver_sectors) == 18


def test_judges_sit_together(registry):
    seats = [k["seat"] for k in registry["tickets"] if k["tier"] == "demo"]
    assert len({s["row"] for s in seats}) == 1
    numbers = sorted(s["seat"] for s in seats)
    assert numbers == list(range(numbers[0], numbers[0] + 12))


def test_seat_rules(stadium):
    assert main_stand(stadium)["name"] == "Main Stand"
    east = next(st for st in stadium["stands"] if st["id"] == "E")
    assert [s["id"] for s in central_home_sectors(east)] == ["E4", "E5"]
    assert sector_layout(2000) == (25, 80) and sector_layout(2700) == (34, 80)
    assert seat_exists(2700, 34, 60) and not seat_exists(2700, 34, 61)


def test_alt_text_for_every_ticket(registry):
    for k in registry["tickets"]:
        alt = k["alt"]
        assert "statseat digital ticket" in alt and f"seat {k['seat']['seat']}" in alt and "not valid for entry" in alt
        assert "GNT-" not in alt


def test_blocklist_check_works():
    blocklist = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
    assert text_problems({"ok": "The Estuary Derby at the Lanternworks Stadium"}, blocklist) == []
    assert text_problems({"bad": "Messi buys a beer"}, blocklist)


# --- Rendering -------------------------------------------------------------------------


def test_rendered_files_exist_with_the_right_size(built):
    rendered = [t for t in built.tickets if t.files]
    assert sorted(t.stem for t in rendered) == sorted(
        [f"demo-{i:03d}" for i in range(1, 13)]
        + [f"{tier}-{i:03d}" for tier in ("gold", "silver") for i in range(1, 5)])
    for t in rendered:
        svg, pdf, png = (built.files[t.files[f]] for f in ("svg", "pdf", "png"))
        assert svg.startswith(b"<?xml") and t.code.encode() in svg and b"<desc" in svg
        assert pdf.startswith(b"%PDF") and b"/CreationDate (D:20000101000000" in pdf
        image = Image.open(io.BytesIO(png))
        assert image.format == "PNG" and image.size == PNG_SIZE == (1080, 1920)


def test_qr_in_the_png_matches_the_code(built):
    """Every module of the QR code is drawn where it should be (so the code scans)."""
    t = next(t for t in built.tickets if t.tier.name == "demo" and t.index == 5)
    qr = qr_symbol(t.code)
    image = Image.open(io.BytesIO(built.files[t.files["png"]])).convert("L")
    x0, y0, m = qr_geometry(qr)
    k = PNG_SIZE[0] / 720
    border = 4
    for r in range(-border, len(qr.matrix) + border):
        for c in range(-border, len(qr.matrix) + border):
            dark = 0 <= r < len(qr.matrix) and 0 <= c < len(qr.matrix) and qr.matrix[r][c]
            px = image.getpixel((int((x0 + (c + border + 0.5) * m) * k), int((y0 + (r + border + 0.5) * m) * k)))
            assert (px < 100) == bool(dark), (r, c, px)


def test_tier_colours(built):
    by_stem = {t.stem: t for t in built.tickets if t.files}
    gold = png_at(built.files[by_stem["gold-001"].files["png"]], 6, 640)  # frame
    silver = png_at(built.files[by_stem["silver-001"].files["png"]], 6, 640)
    assert gold[0] - gold[2] > 60  # warm
    assert abs(silver[0] - silver[2]) < 20  # neutral grey


def test_crests_are_used_when_present(tmp_path):
    crest = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 120"><defs><linearGradient id="g">'
             '<stop offset="0" stop-color="{c}"/><stop offset="1" stop-color="{c}"/></linearGradient></defs>'
             '<rect x="0" y="0" width="100" height="120" fill="url(#g)"/></svg>')
    (tmp_path / "BRA.svg").write_text(crest.format(c="#00AA55"))
    (tmp_path / "CAL.svg").write_text(crest.format(c="#AA0055"))  # same gradient id: must not clash
    result = build("SYN-001", crests_dir=tmp_path)
    assert result.ok
    t = next(t for t in result.tickets if t.stem == "demo-005")
    assert result.files[t.files["svg"]].count(b"data:image/svg+xml;base64,") == 2
    png = result.files[t.files["png"]]
    for side, colour in (("home", (0x00, 0xAA, 0x55)), ("away", (0xAA, 0x00, 0x55))):
        x, y, w, h = CREST_BOXES[side]
        assert all(abs(a - b) < 12 for a, b in zip(png_at(png, x + w / 2, y + h / 2), colour)), side


# --- Determinism and command line -------------------------------------------------------


def test_two_builds_are_identical(built):
    again = build("SYN-001", crests_dir=REPO_ROOT / "no-crests-here")
    assert again.files.keys() == built.files.keys() and len(built.files) == 2 + 20 * 3
    assert all(again.files[k] == built.files[k] for k in built.files)  # JSON, README, SVG, PDF and PNG


def test_command_line_writes_and_cleans(tmp_path):
    out = tmp_path / "tickets"
    (out / "png").mkdir(parents=True)
    (out / "png" / "old-ticket.png").write_bytes(b"stale")
    assert main(["SYN-001", "--out", str(out), "--crests", str(tmp_path / "none")]) == 0
    assert not (out / "png" / "old-ticket.png").exists()
    assert (out / "tickets.json").is_file() and (out / "README.md").is_file()
    assert len(list((out / "png").glob("*.png"))) == 20


def test_qr_url_option(tmp_path):
    result = build("SYN-001", qr_url="https://example.test/login", crests_dir=tmp_path)
    assert result.ok
    assert "https://example.test/login?ticket=<code>" in result.files["README.md"].decode()


def test_committed_tickets_are_up_to_date(built):
    """synthetic/SYN-001/tickets must match a fresh build (re-run the generator if this fails)."""
    folder = REPO_ROOT / "synthetic" / "SYN-001" / "tickets"
    if not folder.is_dir():
        pytest.skip("tickets not generated yet")
    if (REPO_ROOT / "synthetic" / "crests").is_dir():
        pytest.skip("crests present: compare with a build that uses them")
    # Hidden files (e.g. .DS_Store written by Finder) are not part of the output
    committed = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*")
                 if p.is_file() and not p.name.startswith(".")}
    assert committed.keys() == built.files.keys()
    stale = [k for k in built.files if committed[k] != built.files[k]]
    assert not stale, "run: cd generator && .venv/bin/python -m insidethegame_gen.tickets SYN-001"
