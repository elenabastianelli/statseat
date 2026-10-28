"""Rules added when the pipeline met the other five DFL matches (task V-L1). Synthetic fixtures only."""

import pandas as pd

from insidethegame_data.profile import objects_summary
from insidethegame_data.tables import build_half_arrays


def _frames_with_referee():
    rows = []
    for obj, team, x in (("P1", "T-H", 1.0), ("P2", "T-A", 2.0), ("R1", "referee", 3.0), ("BALL", "BALL", 0.0)):
        for i in range(4):
            rows.append({"half": 1, "object_id": obj, "team_id": team, "is_ball": obj == "BALL", "n": 100 + i,
                         "t_utc": pd.Timestamp(1_000_000 + i / 25, unit="s", tz="UTC"), "x": x, "y": 0.0, "z": 0.0,
                         "ball_status": 1.0, "ball_possession": 1.0})
    return pd.DataFrame(rows)


def test_objects_of_neither_team_are_not_players():
    f = _frames_with_referee()
    ha = build_half_arrays(f, 1, {"T-H", "T-A"})
    assert ha.object_ids == ["P1", "P2"] and ha.x.shape == (4, 2)
    # Without the filter the old behaviour is kept (all non-ball objects).
    assert build_half_arrays(f, 1).object_ids == ["P1", "P2", "R1"]


def test_profile_records_tracked_referees():
    fsets = pd.DataFrame({"object_id": ["P1", "P2", "R1", "BALL"], "team_id": ["T-H", "T-A", "referee", "BALL"],
                          "is_ball": [False, False, False, True], "half": [1, 1, 1, 1]})
    lineup = pd.DataFrame({"person_id": ["P1", "P2"], "team_id": ["T-H", "T-A"], "starting": [True, True]})
    o = objects_summary(lineup, fsets, "T-H", "T-A")
    assert o["referees_tracked"] is True and o["non_player_objects_by_team_label"] == {"referee": 1}
    o2 = objects_summary(lineup, fsets.iloc[[0, 1, 3]], "T-H", "T-A")
    assert o2["referees_tracked"] is False


def test_r_sub_inherit_substitute_without_inheritance_ignores_later_jumps():
    """Incoming track starts 12 m from the outgoing player's last position (no inheritance, as on most
    substitutions of J03WMX / J03WOH): a 2 m jump 10 s later is an ordinary anomaly, not a re-acquisition,
    so the substitute is invalid only for his first 1 s (v4), and the jump gets its own +-1 s window."""
    import numpy as np

    from insidethegame_data.config import DEFAULT_CONFIG as CFG
    from insidethegame_data.prepare import CODE, prepare_half

    from .arrays import FS, make_half, run_x

    n, w = 800, int(FS)
    xo = run_x(n, 1.0, -10.0)
    xo[300:] = np.nan
    xi = np.full(n, np.nan)
    xi[300:] = run_x(500, 1.0, xo[299] + 12.0)
    xi[550:] += 2.0  # unrelated 2 m jump 10 s after the start of the track
    y = np.zeros(n)
    hd = make_half([xo, xi], [y, y.copy()], pids=["OUT", "IN"])
    hr = prepare_half(hd, [{"player_out": "OUT", "player_in": "IN", "team": "home", "event_time_s": float(hd.t_s[299])}], [], (100.0, 60.0), CFG)
    s = hr.substitutions[0]
    assert s["inheritance"] is False and s["reacquisition_jump"] is False
    assert (hr.reason[300:300 + w, 1] == CODE["substitution"]).all()
    assert hr.valid[300 + w, 1] and hr.valid[500, 1]  # valid after 1 s, well before the jump
    assert (hr.reason[550 - w:550 + w, 1] != 0).all()  # the jump is windowed on its own


def test_r_sub_inherit_half_time_substitute_has_no_inheritance():
    """The outgoing player has no track in the period (substituted at half time, as on J03WOY): the
    incoming track starts with the period, nothing is inherited and no re-acquisition jump is searched."""
    import numpy as np

    from insidethegame_data.config import DEFAULT_CONFIG as CFG
    from insidethegame_data.prepare import CODE, prepare_half

    from .arrays import FS, make_half, run_x

    n, w = 600, int(FS)
    xi = run_x(n, 1.0, -10.0)
    xi[200:] += 3.0  # an ordinary jump 8 s into the period
    other = run_x(n, 1.5, 5.0)
    y = np.zeros(n)
    hd = make_half([xi, other], [y, y + 5.0], pids=["IN", "P9"])
    sub = {"player_out": "OUT", "player_in": "IN", "team": "home", "event_time_s": float(hd.t_s[0]) - 45.0}
    hr = prepare_half(hd, [sub], [], (100.0, 60.0), CFG)
    s = hr.substitutions[0]
    assert s["outgoing_tracked_in_period"] is False and s["inheritance"] is False and s["reacquisition_jump"] is False
    assert (hr.reason[:w, 0] == CODE["substitution"]).all() and hr.valid[w, 0] and hr.valid[150, 0]


def test_r_restart_sub_switch_time_is_the_first_appearance_of_a_substitute():
    import numpy as np

    from insidethegame_data.timealign import first_appearance_times

    pres1 = np.zeros((10, 2), dtype=bool)
    pres1[:, 0], pres1[6:, 1] = True, True  # S enters at index 6 of period 1
    pres2 = np.ones((10, 2), dtype=bool)
    t1, t2 = 100.0 + np.arange(10), 5000.0 + np.arange(10)
    out = first_appearance_times([(2, ["A", "S"], t2, pres2), (1, ["A", "S"], t1, pres1)])
    assert out == {"A": 100.0, "S": 106.0}


def test_summary_is_deterministic_and_keeps_analyst_notes(synth):
    from insidethegame_data.qc import run
    from insidethegame_data.summary import write_summary

    root = synth["root"]
    run("SYN001", repo=root, log=lambda *_: None, use_cache=False, kloppy_check=False, write_frames=False)
    qc_dir = root / "reports" / "qc"
    out = write_summary(qc_dir, ["SYN001"])
    text = out.read_text()
    assert "| SYN001 |" in text and "Events at a glance" in text and "Acceleration window sensitivity" in text and "| PASS" not in text and "WARN" not in text
    out.write_text(text.replace("_No analyst notes yet._", "Keep me."))
    write_summary(qc_dir, ["SYN001"])
    second = out.read_text()
    assert "Keep me." in second and second == text.replace("_No analyst notes yet._", "Keep me.")
