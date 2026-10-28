"""The whole preparation run on a tiny DFL-format match (guidelines v4.2): deterministic, every log line names its rule."""

import hashlib
import json

from insidethegame_data.config import FRAMES_VERSION
from insidethegame_data.qc import run

RULES = ["R-DELETE", "R-SORT", "R-DEDUP", "R-RESAMPLE", "R-MISSING", "R-UNITS", "R-MAP-EN", "R-BALL-DEAD,", "R-EDGE", "R-PARKING", "R-FROZEN",
         "R-JUMP", "R-SWAP,", "R-SUB-INHERIT", "R-EXIT-REENTRY", "R-OVERLAP-CROWD", "R-SPEED-LIMIT,", "R-ACC-BAND", "R-UNKNOWN,", "R-TRACK-START"]


def _hashes(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob("*")) if p.is_file()}


def test_preparation_run_on_synthetic_match_is_deterministic(synth):
    root = synth["root"]
    out = root / "reports" / "qc" / "SYN001"
    res = run("SYN001", repo=root, log=lambda *_: None, use_cache=False, kloppy_check=False)
    h1 = _hashes(out)
    frames_csv = root / "reports" / "qc" / "frames" / "SYN001" / FRAMES_VERSION / "anomaly_catalogue.csv"
    mtime = frames_csv.stat().st_mtime_ns
    run("SYN001", repo=root, log=lambda *_: None, use_cache=False, kloppy_check=False)
    assert _hashes(out) == h1  # two runs give identical outputs
    assert frames_csv.stat().st_mtime_ns == mtime  # frame-level files are not overwritten without the flag
    assert {"README.md", "qc_results.json", "match_profile.json", "acc_window_sensitivity.csv", "acc_window_sensitivity.md"} <= set(h1)

    assert res["guidelines_version"].startswith("v4")
    assert "checks" not in res  # no pass/warn/fail
    assert [p["rule"].split()[0] for p in res["preparation_log"]] == RULES  # every line names its rule
    assert res["preparation_log"][0]["count"] == 1  # one Delete event dropped
    assert res["acc_window_sensitivity"]["available"] and [w["window_samples"] for w in res["acc_window_sensitivity"]["windows"]] == [13, 19, 25]
    profile = json.loads((out / "match_profile.json").read_text())
    for key in ("fs_hz", "frame_ranges", "pitch_length_m", "tracking_origin", "event_to_canonical_transform", "direction_per_half",
                "kickoff_per_period", "tracking_area_edges", "parking_values", "event_clock_offset", "provider_kinematics_supplied", "ball_status_supplied",
                "ball_height_supplied", "objects", "substitutions", "cards", "delete_events", "playing_time"):
        assert key in profile, key
    assert profile["tracking_origin"] == "corner"
    assert profile["tracking_area_edges"]["edges"] == [] and profile["parking_values"]["parking_values"] == []
    assert profile["ball_height_supplied"] is False  # a constant height of 0.10 m is not usable
    report = (out / "README.md").read_text()
    assert "<!-- analyst-notes:start -->" in report and "CC-BY 4.0" in report and "v4.2 (2026-10-25)" in report and "### To review" in report
    assert "| PASS" not in report and "WARN" not in report
    assert res["swaps"]  # the injected swap is found
