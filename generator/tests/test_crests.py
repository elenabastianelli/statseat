"""Tests for the crests and kits (M-1): one of each per club, deterministic, readable by svglib, up to date."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from svglib.svglib import svg2rlg

from insidethegame_gen.crests import build, contrast

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = json.loads((REPO_ROOT / "synthetic" / "universe" / "universe.json").read_text(encoding="utf-8"))
CLUBS = [c["id"] for c in UNIVERSE["clubs"]]


def test_one_crest_and_two_kits_per_club():
    files, _ = build(UNIVERSE)
    assert {f"crests/{c}.svg" for c in CLUBS} <= files.keys()
    assert {f"kits/{c}_{s}.svg" for c in CLUBS for s in ("home", "away")} <= files.keys()
    assert len(files) == len(CLUBS) * 3 + 1  # plus kits/colours.json


def test_deterministic():
    assert build(UNIVERSE)[0] == build(UNIVERSE)[0]


def test_svgs_are_valid_and_crests_readable_by_svglib(tmp_path):
    files, _ = build(UNIVERSE)
    for rel, text in files.items():
        if rel.endswith(".svg"):
            ET.fromstring(text)
        if rel.startswith("crests/"):
            path = tmp_path / rel.replace("/", "_")
            path.write_text(text, encoding="utf-8")
            assert svg2rlg(str(path)) is not None, rel  # the PDF tickets convert crests with svglib


def test_kit_colours():
    colours = json.loads(build(UNIVERSE)[0]["kits/colours.json"])
    details = {d["club_id"]: d["kits"] for d in UNIVERSE["club_details"]}
    for cid, kits in details.items():
        assert colours[cid]["home"]["shirt"] == kits["home"]["shirt"]
        assert colours[cid]["away"]["shirt"] == kits["away"]["shirt"]
    for cid in CLUBS:
        assert contrast(colours[cid]["home"]["shirt"], colours[cid]["away"]["shirt"]) > 1.3, cid


def test_committed_files_are_up_to_date():
    files, _ = build(UNIVERSE)
    for rel, text in files.items():
        assert (REPO_ROOT / "synthetic" / rel).read_text(encoding="utf-8") == text, f"rerun crests: {rel}"
