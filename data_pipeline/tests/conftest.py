"""Shared fixtures: one synthetic DFL-format match, loaded once per session (no real data)."""

from __future__ import annotations

import pytest

from insidethegame_data.config import DEFAULT_CONFIG
from insidethegame_data.io_dfl import find_match_files, load_events, load_matchinformation, load_positions
from insidethegame_data.profile import build_match_profile
from insidethegame_data.tables import build_half_arrays

from .synthetic import write_synthetic_match


@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    sm = write_synthetic_match(root)
    mf = find_match_files(sm.raw_dir, sm.match_id)
    info = load_matchinformation(mf.matchinformation)
    ev = load_events(mf.events)
    pos = load_positions(mf.positions)
    has = {h: build_half_arrays(pos.frames, h) for h in (1, 2)}
    profile = build_match_profile(info, ev, pos, has, DEFAULT_CONFIG)
    return {"root": root, "mf": mf, "info": info, "ev": ev, "pos": pos, "has": has, "profile": profile}
