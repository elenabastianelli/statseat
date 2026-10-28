"""Validity mask, windows, gaps, substitutions, exits, limits, acceleration band, ball windows (guidelines v4, sections 5-8)."""

from dataclasses import replace

import numpy as np

from insidethegame_data.acc_window import acc_window_sensitivity, sensitivity_table
from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.prepare import ACC_CODE, CODE, acc_band_runs, prepare_half

from .arrays import FS, make_half, run_x

PITCH = (100.0, 60.0)
W = int(FS)  # +-1 s window


def test_r_jump_2m_jump_creates_pm1s_window():
    x = run_x(600, 2.0, -20)
    x[300:] += 2.0
    hr = prepare_half(make_half([x], [np.zeros(600)]), [], [], PITCH, CFG)
    bad = np.flatnonzero(hr.reason[:, 0] != 0)
    assert bad.min() == 300 - W and bad.max() == 300 + W
    assert hr.reason[300, 0] == CODE["unknown"]  # R-UNKNOWN: no signature, excluded whatever the cause
    assert hr.valid[300 - W - 1, 0] and hr.valid[300 + W + 1, 0]


def _frozen_case(v: float, frames: int):
    x = run_x(600, v, -20)
    x[200:200 + frames] = x[199]
    return prepare_half(make_half([x], [np.zeros(600)]), [], [], PITCH, CFG), x


def test_r_gap_frozen_run_resuming_3m_away_is_estimated():
    hr, x = _frozen_case(1.5, 50)  # resumes 51 x 0.06 = 3.06 m away
    g = [g for g in hr.gaps if g["kind"] == "frozen"][0]
    assert 2.9 < g["resume_m"] < 3.2 and g["handling"] == "estimated"
    assert hr.estimated[200:250, 0].all() and not hr.valid[200:250, 0].any()
    assert (hr.reason[200:250 + W, 0] != 0).all()  # frozen samples + 1 s after the resume
    assert hr.valid[199, 0]  # R-FROZEN: the first sample of the run is the last real measurement
    assert abs(g["estimated_distance_m"] - 3.06) < 0.1


def test_r_gap_frozen_run_resuming_8m_away_is_discarded():
    hr, _ = _frozen_case(2.0, 100)  # resumes 101 x 0.08 = 8.08 m away
    g = [g for g in hr.gaps if g["kind"] == "frozen"][0]
    assert g["resume_m"] > 6 and g["handling"] == "discarded"
    assert hr.discarded[200:300, 0].all() and not hr.estimated[:, 0].any()
    assert hr.dist[200:300, 0].sum() == 0.0


def test_r_exit_reentry_frozen_run_ending_the_track_is_an_exit():
    x = run_x(600, 1.0, -20)
    x[500:] = x[499]
    hr = prepare_half(make_half([x], [np.zeros(600)]), [], [], PITCH, CFG)
    assert [g["kind"] for g in hr.gaps] == ["exit"]
    assert (hr.reason[500:, 0] == CODE["exit"]).all() and hr.discarded[500:, 0].all()


def test_r_exit_reentry_reentry_after_frozen_off_pitch_run():
    n = 600
    y = np.r_[np.linspace(25, 32, 150), np.full(150, 32.0), np.linspace(29.5, 20, 300)]  # frozen beyond the touchline (30 m)
    hr = prepare_half(make_half([np.zeros(n)], [y]), [], [], PITCH, CFG)
    g = [g for g in hr.gaps if g["kind"] == "reentry"]
    assert len(g) == 1
    end = g[0]["hi"]
    assert (hr.reason[end + 1:end + 1 + int(2 * FS), 0] != 0).all()  # invalid until 2 s after the resume


# ---------------------------------------------------------------------------
# R-SUB-INHERIT
# ---------------------------------------------------------------------------

def _sub_case(start_offset_m: float, jump_after_s: float | None, x_last: float = 0.0, n: int = 800):
    """The outgoing track ends at frame 299 at x = ``x_last``; the incoming track starts at frame 300,
    ``start_offset_m`` away; it jumps to the substitute's real position ``jump_after_s`` later (None: no jump)."""
    xo = run_x(n, 2.0, x_last - 2.0 * 299 / FS)
    xo[300:] = np.nan
    xi = np.full(n, np.nan)
    real = x_last + start_offset_m + run_x(n - 300, 1.0)
    xi[300:] = real
    if jump_after_s is not None:
        j = 300 + int(round(jump_after_s * FS))
        xi[j:] += 14.0  # the re-acquisition jump: the track moves to the substitute's real position
    y = np.zeros(n)
    hd = make_half([xo, xi], [y, y.copy()], pids=["OUT", "IN"])
    sub = {"player_out": "OUT", "player_in": "IN", "team": "home", "event_time_s": float(hd.t_s[290])}
    return prepare_half(hd, [sub], [], PITCH, CFG)


def test_r_sub_inherit_inheritance_with_a_jump_10s_later_is_invalid_until_the_jump_plus_2s():
    hr = _sub_case(0.0, 10.0)
    s = hr.substitutions[0]
    assert s["inheritance"] and s["inherit_distance_m"] == 0.0 and s["inherit_frame_gap"] == 1
    assert s["reacquisition_jump"] and abs(s["reacquisition_after_s"] - 10.0) < 1e-9
    j = 300 + 250
    assert (hr.reason[300:j + 2 * W + 1, 1] != 0).all() and hr.valid[j + 2 * W + 1, 1]
    assert (hr.reason[290:300, 0] == CODE["substitution"]).all() and hr.valid[288, 0]  # outgoing invalid from the event


def test_r_sub_inherit_inheritance_without_a_jump_is_invalid_for_1s_only():
    hr = _sub_case(0.0, None)
    s = hr.substitutions[0]
    assert s["inheritance"] and not s["reacquisition_jump"] and s["in_invalid_s"] == 1.0
    assert (hr.reason[300:300 + W, 1] == CODE["substitution"]).all()
    assert hr.valid[300 + W, 1]


def test_r_sub_inherit_track_starting_0_6m_away_has_no_inheritance_and_no_jump_search():
    hr = _sub_case(0.6, 10.0)
    s = hr.substitutions[0]
    assert not s["inheritance"] and not s["reacquisition_jump"] and s["in_invalid_s"] == 1.0
    assert (hr.reason[300:300 + W, 1] == CODE["substitution"]).all() and hr.valid[300 + W, 1]
    j = 300 + 250
    assert (hr.reason[j - W:j + W + 1, 1] == CODE["unknown"]).all()  # the later jump is an ordinary anomaly with its own window


def test_r_sub_inherit_halfway_distance_is_logged_and_changes_nothing():
    near, far = _sub_case(0.0, None, x_last=0.5), _sub_case(0.0, None, x_last=30.0)
    assert near.substitutions[0]["halfway_distance_m"] == 0.5 and far.substitutions[0]["halfway_distance_m"] == 30.0
    assert np.array_equal(near.reason, far.reason) and np.array_equal(near.valid, far.valid)


def test_r_sub_inherit_frozen_head_of_substitute_is_discarded_not_estimated():
    n = 300
    xo = run_x(n, 1.0)
    xo[100:] = np.nan
    xi = np.full(n, np.nan)
    xi[100:160] = xo[99]
    xi[160:] = xo[99] + 0.5 + run_x(140, 1.0)
    hd = make_half([xo, xi], [np.zeros(n), np.zeros(n)], pids=["OUT", "IN"])
    hr = prepare_half(hd, [{"player_out": "OUT", "player_in": "IN", "team": "home", "event_time_s": float(hd.t_s[99])}], [], PITCH, CFG)
    head = [g for g in hr.gaps if g["kind"] == "track_head"]
    assert head and not hr.estimated[:, 1].any() and hr.discarded[101:160, 1].all()  # never bridge across two players' tracks
    assert (hr.reason[101:160, 1] == CODE["substitution"]).all()


# ---------------------------------------------------------------------------
# R-SG-ACC, R-ACC-BAND, R-SPEED-LIMIT, R-ACC-WINDOW-TEST
# ---------------------------------------------------------------------------

def test_r_acc_band_4_and_5_sample_exceedances_are_noise_and_6_samples_are_approx():
    acc = np.zeros((40, 3))
    acc[10:14, 0] = 9.0  # 4 samples (0.16 s)
    acc[10:15, 1] = -9.0  # 5 samples (0.20 s)
    acc[10:16, 2] = 8.0  # 6 samples (0.24 s)
    valid = np.ones((40, 3), dtype=bool)
    status, runs = acc_band_runs(acc, valid, CFG, FS)
    assert (status[10:14, 0] == ACC_CODE["noise"]).all() and (status[10:15, 1] == ACC_CODE["noise"]).all()
    assert (status[10:16, 2] == ACC_CODE["approx"]).all() and (status[:10] == ACC_CODE["measured"]).all()
    assert [(r["class"], r["sign"], r["samples"]) for r in runs] == [("noise", "acceleration", 4), ("noise", "deceleration", 5), ("approx", "acceleration", 6)]
    status, _ = acc_band_runs(acc, valid & (np.arange(40)[:, None] != 12), CFG, FS)
    assert status[12, 0] == ACC_CODE["none"]  # an invalid sample has no acceleration status


def test_r_acc_band_never_excludes_a_sample_and_the_value_is_kept():
    t = np.arange(400) / FS
    v = np.where(t < 4, 0.5, np.minimum(0.5 + 9.0 * (t - 4), 9.0))  # jogging, then +9 m/s2 for ~0.94 s
    x = np.cumsum(v) / FS - 30
    hr = prepare_half(make_half([x], [np.zeros(400)]), [], [], PITCH, CFG)
    beyond = hr.acc_tan[:, 0] > CFG.player_acc_max_ms2
    assert beyond.sum() > 5 and hr.valid[beyond, 0].all()  # no exclusion, no window
    assert (hr.acc_status[beyond, 0] == ACC_CODE["approx"]).all()  # longer than 0.2 s: kept, reported with "≈"
    assert np.nanmax(np.where(hr.valid, hr.acc_tan, np.nan)) > CFG.player_acc_max_ms2
    assert hr.valid[:, 0].sum() == 400 and not hr.catalogue  # nothing excluded, nothing catalogued


def test_r_speed_limit_speed_above_38_kmh_is_limit_with_pm1s():
    """At 25 Hz a sustained 39 km/h is a run of position jumps (R-JUMP: steps > v_max * dT = 0.42 m). The speed limit
    catches what the step test cannot: here a walk at 0.25 m/s turns into steps of 0.42 m (just below the jump
    threshold) for the last 7 samples of the track; the filtered speed there exceeds 38 km/h (about 55 km/h)."""
    n = 300
    x = np.full(n, np.nan)
    x[:100] = 0.01 * np.arange(100)
    x[100:107] = x[99] + 0.42 * np.arange(1, 8)
    hr = prepare_half(make_half([x], [np.zeros(n)]), [], [], PITCH, CFG)
    over = np.flatnonzero(hr.speed[:, 0] > CFG.player_speed_max_ms)
    assert len(over) and not hr.raw_jumps[:, 0].any()
    assert (hr.reason[over.min() - W:107, 0] == CODE["limit"]).all() and hr.valid[over.min() - W - 1, 0]
    lim = [c for c in hr.catalogue if c["anomaly_type"] == "limit"]
    assert lim and lim[0]["cause_detail"] == "speed above 38 km/h" and lim[0]["value"] * 3.6 > 38.0


def test_r_acc_window_test_mask_speeds_and_distances_do_not_depend_on_the_window():
    n = 700
    x = np.cumsum(np.where(np.arange(n) % 200 < 100, 2.0, 6.0)) / FS - 30
    x[300:] += 2.0  # a jump
    x[450:480] = x[449]  # a frozen run
    y = np.sin(np.arange(n) / 40.0)
    out = []
    for f in CFG.acc_window_test_fractions:
        c = replace(CFG, acc_window_fraction_of_fs=f)
        out.append(prepare_half(make_half([x, x[::-1].copy()], [y, -y]), [], [], PITCH, c))
    for hr in out[1:]:
        assert np.array_equal(hr.valid, out[0].valid) and np.array_equal(hr.reason, out[0].reason)
        assert np.allclose(hr.speed, out[0].speed, equal_nan=True) and np.allclose(hr.dist, out[0].dist)
    assert not np.allclose(out[0].acc_tan, out[2].acc_tan, equal_nan=True)  # only the acceleration changes
    hd = make_half([x, x[::-1].copy()], [y, -y])
    res = acc_window_sensitivity([(hd, out[0])], CFG)
    t = sensitivity_table(res)
    assert t["window_samples"].tolist() == [13, 19, 25] and {"noise_samples", "approx_samples", "acc_p0_1_ms2"} <= set(t.columns)
    assert res["windows"][0]["diagnostic_max_speed_change_kmh"]["max_abs"] == 0.0  # 13 against itself
    assert res["windows"][0]["noise_runs"] + res["windows"][0]["approx_runs"] == len(out[0].acc_band)


def test_provider_channels_change_no_result():
    x = run_x(600, 3.0, -20)
    x[300:] += 1.5
    a = prepare_half(make_half([x], [np.zeros(600)]), [], [], PITCH, CFG)
    rng = np.random.default_rng(0)  # seeded: test data only
    b = prepare_half(make_half([x], [np.zeros(600)], prov=rng.normal(5, 3, (600, 1))), [], [], PITCH, CFG)
    assert np.array_equal(a.reason, b.reason) and np.array_equal(a.valid, b.valid)
    assert np.allclose(a.speed, b.speed, equal_nan=True) and np.allclose(a.dist, b.dist)


# ---------------------------------------------------------------------------
# R-BALL-DEAD, R-BALL-WINDOW
# ---------------------------------------------------------------------------

def test_r_ball_window_restart_and_live_jump_windows_are_0_25_s():
    n = 400
    st = np.r_[np.zeros(50), np.ones(350)]
    bx = np.r_[np.zeros(50), run_x(350, 20.0, 30.0)]
    bx[200:] += 10.0  # 10 m jump in live play
    by = np.full(n, 10.0)
    hd = make_half([run_x(n, 1.0)], [np.zeros(n)], bstatus=st, bx=bx, by=by)
    hr = prepare_half(hd, [], [], PITCH, CFG)
    bw = CFG.frames(CFG.ball_window_s, FS)
    assert bw == 6
    assert (hr.ball_reason[:50] == CODE["dead_ball"]).all()
    assert (hr.ball_reason[50:56] == CODE["restart"]).all() and hr.ball_valid[56]  # the first 0.25 s after the restart
    assert hr.ball_valid[50 + 8]  # 0.3 s after the restart: valid
    assert [c for c in hr.catalogue if c["anomaly_type"] == "ball_jump"][0]["index"] == 200  # the restart jump is ignored
    assert (hr.ball_reason[200 - bw:200 + bw + 1] == CODE["jump"]).all()
    assert hr.ball_valid[200 - bw - 1] and hr.ball_valid[200 + bw + 1]
    assert abs(np.nanmedian(hr.ball_speed[hr.ball_valid]) - 20.0) < 0.05
