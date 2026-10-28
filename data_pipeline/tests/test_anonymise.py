"""R-ANON-LABELS (guidelines v4.2, section 13): the committable reports of real data name people only by anonymous labels.

The integration test rewrites the tiny DFL-format fixture with DFL-style object IDs (``DFL-OBJ-...``), runs the
preparation as a DFL source and fails if any ID reaches a report file. No real data.
"""

import json

import pandas as pd

from insidethegame_data import calibration as cal
from insidethegame_data import summary
from insidethegame_data.anonymise import ID_LABELS_FILE, anonymise, anonymise_text, build_labels, id_map, load_label_map
from insidethegame_data.config import FRAMES_VERSION
from insidethegame_data.qc import run

from .synthetic import write_synthetic_match


def test_r_anon_labels_side_role_group_and_number_in_id_order():
    lu = pd.DataFrame({"person_id": ["DFL-OBJ-Z9", "DFL-OBJ-A1", "DFL-OBJ-B2", "DFL-OBJ-C3", "DFL-OBJ-D4"],
                       "team_role": ["home", "home", "home", "away", "home"],
                       "role_group": ["CB", "CB", "GK", "CB", "UNMAPPED_NONE"], "role": ["LCB", "RCB", "GK", "CB", "UNMAPPED_NONE"]})
    labels = build_labels(lu, {"DFL-OBJ-R1": "referee", "DFL-OBJ-R0": "referee"})
    m = id_map(labels)
    assert m["DFL-OBJ-A1"] == "home CB 1" and m["DFL-OBJ-Z9"] == "home CB 2" and m["DFL-OBJ-B2"] == "home GK 1"
    assert m["DFL-OBJ-C3"] == "away CB 1" and m["DFL-OBJ-D4"] == "home unassigned 1"
    assert m["DFL-OBJ-R0"] == "referee 1" and m["DFL-OBJ-R1"] == "referee 2"
    doc = {"players": {"DFL-OBJ-A1": {"shots": 1}}, "log": ["DFL-OBJ-Z9 P1 2.0 s"], "n": 3, "t": ("DFL-OBJ-C3",)}
    out = anonymise(doc, m)
    assert out == {"players": {"home CB 1": {"shots": 1}}, "log": ["home CB 2 P1 2.0 s"], "n": 3, "t": ("away CB 1",)}
    assert anonymise_text("kicker DFL-OBJ-B2, deflected by DFL-OBJ-C3", m) == "kicker home GK 1, deflected by away CB 1"


def test_r_anon_labels_no_dfl_object_id_in_any_report_file_of_a_dfl_source(tmp_path):
    root = tmp_path / "repo"
    sm = write_synthetic_match(root)
    for p in sm.raw_dir.glob("*.xml"):  # DFL-style person IDs in all three files (P-H1 -> DFL-OBJ-PH1)
        p.write_text(p.read_text().replace('"P-', '"DFL-OBJ-P'))
    res = run(sm.match_id, repo=root, log=lambda *_: None, use_cache=False, kloppy_check=False)
    qc_dir = root / "reports" / "qc"
    summary.write_summary(qc_dir, [sm.match_id])
    files = [p for p in (qc_dir / sm.match_id).glob("*") if p.is_file()] + [qc_dir / "SUMMARY.md"]
    assert {p.name for p in files} >= {"README.md", "qc_results.json", "match_profile.json", "acc_window_sensitivity.csv", "SUMMARY.md"}
    for p in files:
        assert "DFL-OBJ-" not in p.read_text(), p.name
    assert "DFL-OBJ-" not in json.dumps(res)
    profile = json.loads((qc_dir / sm.match_id / "match_profile.json").read_text())
    assert {r["person_id"] for r in profile["lineup_roles"]} >= {"home GK 1", "away ST 1"}
    # The label <-> ID table exists only in the git-ignored frame-level folder; the frame-level files keep the IDs.
    to_id = load_label_map(qc_dir / "frames", sm.match_id)
    assert to_id["home GK 1"] == "DFL-OBJ-PH1" and to_id["home GK 2"] == "DFL-OBJ-PH9" and (qc_dir / "frames" / sm.match_id / ID_LABELS_FILE).exists()
    assert "DFL-OBJ-PH3" in (qc_dir / "frames" / sm.match_id / FRAMES_VERSION / "events_canonical.csv").read_text()
    # Calibration maps the labels of the committed profile back to the IDs of the frame-level files (in memory only).
    md = cal.load_match(root, sm.match_id)
    assert md.role_of.get("DFL-OBJ-PH2") == "CAM" and md.role_of.get("DFL-OBJ-PA5") == "ST"


def test_r_anon_labels_synthetic_matches_keep_their_fictional_ids(tmp_path):
    from .synthetic_itg import write_synthetic_folder

    f = write_synthetic_folder(tmp_path)
    run(f.match_id, repo=f.repo, source="synthetic", log=lambda *_: None, acc_window_test=False)
    assert not (f.repo / "reports" / "qc" / "frames" / f.match_id / ID_LABELS_FILE).exists()
    prof = json.loads((f.repo / "reports" / "qc" / f.match_id / "match_profile.json").read_text())
    assert not any(" " in str(r["person_id"]) for r in prof["lineup_roles"])  # universe IDs, not labels
