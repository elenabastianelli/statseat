"""Synthetic-format adapter (io_synthetic) and the shared qc path, on a tiny hand-made folder (no real data)."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from insidethegame_data.formats import detect_format
from insidethegame_data.io_synthetic import find_synthetic_files, load_synthetic_match
from insidethegame_data.qc import run

from .synthetic_itg import AWAY, HOME, N0, write_synthetic_folder


@pytest.fixture()
def folder(tmp_path):
    return write_synthetic_folder(tmp_path)


def _hashes(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob("*")) if p.is_file()}


def test_detect_format_folder_and_files(folder, tmp_path):
    d = detect_format(folder.folder)
    assert d["provider"] == "InsideTheGame synthetic" and d["adapter"] == "io_synthetic"
    assert detect_format(folder.folder / "events.json")["content"] == "events"
    assert detect_format(folder.folder / "positions.parquet")["content"] == "positions"
    # A lone JSON file outside a synthetic folder is not taken for one.
    lone = tmp_path / "events.json"
    lone.write_text("{}")
    assert detect_format(lone)["provider"] != "InsideTheGame synthetic"
    with pytest.raises(FileNotFoundError):
        find_synthetic_files(tmp_path)


def test_loader_gives_the_same_tables_as_the_dfl_loader(folder, synth):
    _, info, ev, pos, log = load_synthetic_match(folder.folder)
    dfl = synth["pos"]
    assert list(pos.frames.columns) == list(dfl.frames.columns)
    assert list(pos.framesets.columns) == list(dfl.framesets.columns)
    for c in ("half", "n", "is_ball", "x", "ball_status", "ball_possession"):
        assert pos.frames[c].dtype.kind == dfl.frames[c].dtype.kind, c
    assert str(pos.frames["t_utc"].dt.tz) == "UTC"
    # ("half" is added later by the match profile, on both sources)
    assert list(ev.events.columns) == [c for c in synth["ev"].events.columns if c != "half"]
    assert set(info.lineup.columns) >= {"person_id", "team_id", "team_role", "shirt_number", "starting", "playing_position"}
    assert (info.home_team_id, info.away_team_id) == (HOME, AWAY)
    # Ball status and possession are coded like DFL (1 live / 0 dead; 1 home / 2 away).
    b = pos.frames.loc[pos.frames["is_ball"]]
    assert set(b["ball_status"].dropna().unique()) == {0.0, 1.0}
    assert set(b["ball_possession"].dropna().unique()) == {1.0}
    assert pos.frames.loc[pos.frames["half"] == 2, "n"].min() == N0[2]


def test_events_are_adapted_without_inventing_information(folder):
    _, info, ev, pos, log = load_synthetic_match(folder.folder)
    e = ev.events
    assert ev.n_delete == 1 and "Delete" not in set(e["event_type"])
    attrs = {r.event_id: json.loads(r.attrs_json) for r in e.itertuples()}
    # Team and player taken from team_side / player_id; side-valued team attributes mapped to team IDs.
    assert attrs["E2"]["Team"] == HOME and attrs["E2"]["Player"] == "HOM-04"
    assert attrs["E3"]["WinnerTeam"] == AWAY and attrs["E3"]["LoserTeam"] == HOME and attrs["E3"]["Winner"] == "AWY-05"
    # Kickoff sides inferred from the goalkeepers (home keeper at negative x in period 1).
    assert (attrs["E1"]["TeamLeft"], attrs["E1"]["TeamRight"], attrs["E1"]["GameSection"]) == (HOME, AWAY, "firstHalf")
    assert (attrs["E7"]["TeamLeft"], attrs["E7"]["GameSection"]) == (AWAY, "secondHalf")
    assert any("inferred from the goalkeeper" in line for line in log.lines)
    # Generator truth goes only to the tests-only provider column.
    assert e.set_index("event_id").loc["E4", "provider_calculated_frame"] == N0[1] + 200
    assert e["time_utc"].is_monotonic_increasing


def test_qc_runs_on_the_synthetic_source_and_is_deterministic(folder):
    repo, mid = folder.repo, folder.match_id
    frames_dir = repo / "local_frames"
    res = run(mid, repo=repo, source="synthetic", log=lambda *_: None, frames_dir=frames_dir)
    out = repo / "reports" / "qc" / mid
    h1 = _hashes(out)
    run(mid, repo=repo, source="synthetic", log=lambda *_: None, frames_dir=frames_dir)
    assert _hashes(out) == h1
    assert (frames_dir / "tracking_canonical.parquet").exists() and not (repo / "reports" / "qc" / "frames").exists()
    profile = json.loads((out / "match_profile.json").read_text())
    assert profile["provider"] == "InsideTheGame synthetic"
    assert profile["tracking_origin"] == "centre"
    assert profile["direction_per_half"]["1"]["home_attacks_positive_x"] is True and profile["direction_per_half"]["1"]["confirmed"] is True
    assert profile["direction_per_half"]["2"]["home_attacks_positive_x"] is False
    assert profile["pitch_length_m"] == 100.0 and profile["pitch_width_m"] == 64.0
    # The 0.3 s annotation delay is recovered as the event clock offset.
    assert res["time_alignment"]["event_clock_offset"]["1"]["event_clock_offset_s"] == pytest.approx(-0.3, abs=0.05)
    assert res["source"].startswith("InsideTheGame synthetic") and res["kloppy_crosscheck"] is None
    report = (out / "README.md").read_text()
    assert "InsideTheGame synthetic match" in report and "--source synthetic" in report and "Bassek" in report
    ev = pd.read_csv(frames_dir / "events_canonical.csv").set_index("event_id")
    assert ev.loc["E2", "sync_method"] == "kick_signature" and ev.loc["E2", "type_check"] == "confirmed"
    assert ev.loc["E4", "type_check"] == "confirmed" and ev.loc["E4", "type_check_basis"] == "goal"


def test_true_frame_never_decides_anything(tmp_path):
    a = write_synthetic_folder(tmp_path / "a")
    b = write_synthetic_folder(tmp_path / "b", true_frame_shift=40)
    for f in (a, b):
        run(f.match_id, repo=f.repo, source="synthetic", log=lambda *_: None, frames_dir=f.repo / "frames")
    cols = ["event_id", "sync_frame", "sync_method", "type_check", "sync_x_m", "sync_y_m"]
    ea = pd.read_csv(a.repo / "frames" / "events_canonical.csv")[cols]
    eb = pd.read_csv(b.repo / "frames" / "events_canonical.csv")[cols]
    pd.testing.assert_frame_equal(ea, eb)
