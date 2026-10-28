"""Metrics and precision (guidelines v4, section 10), time alignment (9.1, 9.3 steps 1-2) and the unknown-jump context (5.5)."""

import json

import numpy as np
import pandas as pd
import pytest

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.metrics import count_sprints, format_acceleration, format_distance, player_summary, shot_statistics, zone_labels
from insidethegame_data.prepare import ACC_CODE, prepare_half
from insidethegame_data.timealign import event_clock_offset, locate_events, period_kickoffs, transitions

from .arrays import FS, make_half, run_x


def test_r_dist_precision_rule():
    assert format_distance(9.87, 0.005, CFG) == "9.87 km"
    assert format_distance(9.87, 0.03, CFG) == "≈ 9.9 km"
    assert format_distance(9.87, 0.08, CFG) == "≈ 10 km"


def _two_phase_match():
    n = 1000
    x = np.r_[run_x(500, 7.0, -40.0), run_x(500, -3.0, -40.0 + 7.0 * 500 / FS)]
    x[700:] += 3.0  # jump spike: must not become a top speed
    x[600:630] = x[599]  # 1.2 s frozen run at 3 m/s that resumes ~3.7 m away -> estimated
    st = np.r_[np.ones(400), np.zeros(200), np.ones(400)]
    hd = make_half([x], [np.zeros(n)], bstatus=st)
    hd.bposs = np.r_[np.ones(250), np.full(750, 2.0)]
    return hd


def test_fastest_ignores_jumps_and_estimated_samples_and_splits_add_up():
    hd = _two_phase_match()
    hr = prepare_half(hd, [], [], (100.0, 60.0), CFG)
    lineup = pd.DataFrame({"person_id": ["P0"], "role": ["ST"], "role_group": ["ST"]})
    s = player_summary([(hd, hr)], lineup, CFG).iloc[0]
    assert abs(s["top_speed_ms"] - 7.0) < 0.05
    assert hr.estimated[600:630, 0].all()
    assert abs(s["dist_live_m"] + s["dist_dead_m"] - s["dist_m"]) < 1e-9
    assert abs(s["dist_in_poss_m"] + s["dist_out_poss_m"] - s["dist_m"]) < 1e-9
    assert s["dist_dead_m"] > 0  # the classic convention counts ball-out-of-play time


def _alignment_fixture(delay_s=2.0):
    rng = np.random.default_rng(7)
    n = int(600 * FS)
    t = 1_000_000.0 + np.arange(n) / FS
    st = np.ones(n)
    tr = np.arange(int(60 * FS), n - int(40 * FS), int(45 * FS))
    for i in tr:
        st[i - int(10 * FS):i] = 0  # dead for 10 s, live again at frame i
    kickoff_s = t[0] - 1.0  # tracking starts 1 s after kickoff
    rows = [{"event_type": "KickOff", "time_utc": pd.Timestamp(kickoff_s, unit="s", tz="UTC"), "attrs_json": json.dumps({"GameSection": "firstHalf"}), "half": 1}]
    for i in tr:
        te = t[i] - delay_s + rng.normal(0, 0.3)  # events annotated ~2 s early
        rows.append({"event_type": "ThrowIn", "time_utc": pd.Timestamp(te, unit="s", tz="UTC"), "attrs_json": "{}", "half": 1})
    return t, st, tr, pd.DataFrame(rows)


def test_r_track_start_match_clock_starts_at_kickoff_event():
    t, st, _, ev = _alignment_fixture()
    k = period_kickoffs(ev, {1: t}, {1: st}, {1: (np.zeros_like(t), np.zeros_like(t))}, None, CFG)[1]
    assert k["source"] == "KickOff event" and abs(k["tracking_start_offset_s"] - 1.0) < 1e-6


def test_r_track_start_kickoff_fallbacks():
    t, st, tr, ev = _alignment_fixture()
    meta = pd.Timestamp(t[0] - 0.5, unit="s", tz="UTC")
    k = period_kickoffs(ev.iloc[1:], {1: t}, {1: st}, {1: (np.zeros_like(t), np.zeros_like(t))}, meta, CFG)[1]
    assert k["source"] == "match metadata KickoffTime"
    k = period_kickoffs(ev.iloc[1:], {1: t}, {1: st}, {1: (np.zeros_like(t), np.zeros_like(t))}, None, CFG)[1]
    assert k["source"] == "first dead->live transition at the centre spot" and abs(k["kickoff_s"] - t[tr[0]]) < 1e-9


def test_r_clock_offset_simulated_event_delay_is_recovered_and_restarts_located():
    t, st, tr, ev = _alignment_fixture(delay_s=2.0)
    off = event_clock_offset(ev, {1: t}, {1: st}, CFG)[1]
    assert abs(off["event_clock_offset_s"] - 2.0) < 0.3 and off["search_window_s"] == 5.0
    loc = locate_events(ev, {1: t}, {1: 1000 + np.arange(len(t))}, {1: st}, {1: off}, {}, CFG)
    th = loc.loc[loc["event_type"] == "ThrowIn"]
    assert (th["sync_method"] == "dead_live_transition").all()
    assert th["sync_frame"].astype(int).tolist() == (1000 + tr).tolist()


def test_r_clock_offset_period_starting_live_pairs_the_kickoff_with_its_first_frame():
    """Tracking that starts at the kick-off with the ball live (DFL, SYN-001): the period start is a dead->live
    transition, so the KickOff is paired with frame 0 and not with the next restart 20 s later."""
    t = 1000.0 + np.arange(0, 60.0, 0.04)
    st = np.ones(len(t), int)
    st[(t >= 1018.0) & (t < 1020.0)] = 0  # one throw-in: dead for 2 s, live again at t = 1020
    ev = pd.DataFrame({
        "event_type": ["KickOff", "ThrowIn"],
        "time_utc": [pd.Timestamp(1000.0, unit="s", tz="UTC"), pd.Timestamp(1020.0, unit="s", tz="UTC")],
        "half": [1, 1], "attrs_json": ['{"GameSection": "firstHalf"}', "{}"]})
    assert transitions(t, st).tolist()[0] == 0
    off = event_clock_offset(ev, {1: t}, {1: st}, CFG)[1]
    assert abs(off["event_clock_offset_s"]) < 0.05
    assert {r["type"]: r["median"] for r in off["by_type"]}["KickOff"] == pytest.approx(0.0, abs=1e-6)
    loc = locate_events(ev, {1: t}, {1: 1000 + np.arange(len(t))}, {1: st}, {1: off}, {}, CFG)
    assert int(loc.loc[loc["event_type"] == "KickOff", "sync_frame"].iloc[0]) == 1000


LINEUP = pd.DataFrame({"person_id": ["P0"], "role": ["ST"], "role_group": ["ST"]})


def test_r_zones_zone_distances_plus_unassigned_estimated_distance_add_up_to_the_total():
    hd = _two_phase_match()
    hr = prepare_half(hd, [], [], (100.0, 60.0), CFG)
    s = player_summary([(hd, hr)], LINEUP, CFG).iloc[0]
    zones = [s[f"dist_zone_{z}_m"] for z in zone_labels(CFG)]
    assert zone_labels(CFG) == ["0-7", "7-15", "15-20", "20-25", "25+"]
    assert s["dist_unassigned_m"] > 0 and abs(sum(zones) + s["dist_unassigned_m"] - s["dist_m"]) < 1e-9
    assert s["dist_zone_25+_m"] > 0  # the 7 m/s (25.2 km/h) phase


def test_r_sprint_1_2_s_is_one_sprint_0_9_s_is_none_and_an_estimated_sample_ends_it():
    sp = np.full(200, 5.0)
    sp[20:50] = 27.0 / 3.6  # 1.2 s
    sp[100:122] = 27.0 / 3.6  # 0.9 s
    ok = np.ones(200, dtype=bool)
    assert count_sprints(sp, ok, FS, CFG) == 1
    ok[35] = False  # an estimated (or invalid) sample in the middle of the 1.2 s run
    assert count_sprints(sp, ok, FS, CFG) == 0


def test_r_acc_report_approx_peak_has_approx_sign_and_noise_never_gives_a_peak():
    assert format_acceleration(-8.94, True) == "≈ -8.9 m/s2" and format_acceleration(3.214, False) == "+3.21 m/s2"
    t = np.arange(400) / FS
    v = np.where(t < 4, 0.5, np.minimum(0.5 + 9.0 * (t - 4), 9.0))  # +9 m/s2 for ~0.94 s: an approx run
    x = np.cumsum(v) / FS - 30
    hd = make_half([x], [np.zeros(400)])
    hr = prepare_half(hd, [], [], (100.0, 60.0), CFG)
    s = player_summary([(hd, hr)], LINEUP, CFG).iloc[0]
    assert s["acc_peak_approx"] and s["acc_peak_reported"].startswith("≈ +") and s["acc_peak_ms2"] > CFG.player_acc_max_ms2
    hr.acc_status[hr.acc_status == ACC_CODE["approx"]] = ACC_CODE["noise"]  # the same samples as noise: never a peak
    s = player_summary([(hd, hr)], LINEUP, CFG).iloc[0]
    assert s["acc_peak_ms2"] <= CFG.player_acc_max_ms2 and not s["acc_peak_approx"]


def test_r_shot_counts_r_woodwork_stat_on_target_is_goals_plus_saved_and_woodwork_is_separate():
    rows = [("SuccessfulShot", "P1", "T-H"), ("SavedShot", "P1", "T-H"), ("ShotWoodWork", "P1", "T-H"), ("BlockedShot", "P2", "T-H"),
            ("ShotWide", "P2", "T-H"), ("OtherShot", "P3", "T-A"), ("ShotWoodWork", "P3", "T-A")]
    ev = pd.DataFrame([{"event_type": "ShotAtGoal", "sub_types": o, "attrs_json": json.dumps({"Player": p, "Team": t})} for o, p, t in rows])
    st = shot_statistics(ev, {"T-H": "home", "T-A": "away"})
    h, a = st["teams"]["home"], st["teams"]["away"]
    assert (h["shots"], h["on_target"], h["goals"], h["saved"], h["woodwork"], h["blocked"], h["wide"]) == (5, 2, 1, 1, 1, 1, 1)
    assert (a["shots"], a["on_target"], a["woodwork"], a["wide"]) == (2, 0, 1, 1)  # OtherShot -> wide (section 16.3)
    assert st["players"]["P1"]["woodwork"] == 1 and st["players"]["P1"]["on_target"] == 2 and st["players"]["P3"]["team_side"] == "away"


def test_r_shot_counts_v4_1_penalties_count_in_shots_on_target_and_goals():
    rows = [("ShotAtGoal", "SavedShot", {"Player": "P1", "Team": "T-H"}),
            ("Penalty", "ShotAtGoal/SuccessfulShot", {"ProspectiveTaker": "P2", "Team": "T-H"}),
            ("Penalty", "ShotAtGoal/SavedShot", {"ProspectiveTaker": "P2", "Team": "T-H"}),
            ("FreeKick", "ShotAtGoal/ShotWide", {"Team": "T-A"}),  # a direct free kick without an annotated taker
            ("FreeKick", "Play/Pass", {"Team": "T-A"}),  # an ordinary free kick: not a shot
            ("CornerKick", "", {"Team": "T-A"})]
    ev = pd.DataFrame([{"event_type": t, "sub_types": s, "attrs_json": json.dumps(a)} for t, s, a in rows])
    st = shot_statistics(ev, {"T-H": "home", "T-A": "away"})
    h, a = st["teams"]["home"], st["teams"]["away"]
    assert (h["shots"], h["on_target"], h["goals"], h["saved"], h["penalties"], h["penalty_goals"], h["set_piece_shots"]) == (3, 3, 1, 2, 2, 1, 2)
    assert (a["shots"], a["wide"], a["set_piece_shots"], a["penalties"]) == (1, 1, 1, 0)
    assert st["players"]["P2"]["penalties"] == 2 and "None" not in st["players"]  # no taker annotated: team only


def test_r_jump_context_events_within_2_s_are_listed_and_dead_ball_jumps_counted():
    from insidethegame_data.jump_context import annotate_unknown_jumps

    t0 = 1_000_000.0
    cat = pd.DataFrame([{"half": 1, "frame_N": 100, "anomaly_type": "jump", "suspected_cause": "unknown", "object_id": "P7", "ball_status": "live"},
                        {"half": 1, "frame_N": 900, "anomaly_type": "jump", "suspected_cause": "unknown", "object_id": "P8", "ball_status": "dead"},
                        {"half": 1, "frame_N": 500, "anomaly_type": "jump", "suspected_cause": "crowding", "object_id": "P9", "ball_status": "live"}])
    t_of = {(1, 100): t0 + 4.0, (1, 900): t0 + 36.0, (1, 500): t0 + 20.0}
    ev = pd.DataFrame([{"event_id": "E1", "half": 1, "event_type": "TacklingGame", "player_id": None, "time_utc": pd.Timestamp(t0 + 4.0 + 1.5 - 0.5, unit="s", tz="UTC"),
                        "attrs_json": json.dumps({"Winner": "P7", "WinnerTeam": "T-H"})},
                       {"event_id": "E2", "half": 1, "event_type": "Play", "player_id": "P1", "time_utc": pd.Timestamp(t0 + 4.0 + 2.5 - 0.5, unit="s", tz="UTC"),
                        "attrs_json": json.dumps({"Player": "P1", "Team": "T-H"})}])
    out, agg = annotate_unknown_jumps(cat, ev, {1: {"event_clock_offset_s": 0.5}}, t_of, {"T-H": "home"}, CFG)
    near = json.loads(out.loc[0, "nearby_events"])
    assert [e["event_id"] for e in near] == ["E1"] and near[0]["jumping_player_is_event_player"] and abs(near[0]["dt_s"] - 1.5) < 1e-6
    assert agg["unknown_jumps"] == 2 and agg["in_live_play"] == 1 and agg["in_dead_ball"] == 1 and agg["event_types_nearby"] == {"TacklingGame": 1}
    assert out.loc[2, "nearby_events"] is None  # only unknown jumps get a context
