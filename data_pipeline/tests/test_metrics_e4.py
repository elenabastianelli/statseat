"""Match metrics of Materials/METRICS.md implemented in task E-4 (pure functions of ``metrics.py``), on small hand-made
inputs. One test per metric; values worked out by hand or from the committed model file."""

import math

import numpy as np
import pytest

from insidethegame_data import calibration
from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.metrics import (acceleration_load_samples, after_kicks_mask, chance_word, count_sprints,
                                        counter_attacks, in_pressing_zone, long_balls, momentum_value, pass_reception,
                                        possession_gain_frame, penalty_xg, ppda, rolling_intensity, shot_kick, shot_xg,
                                        sprint_runs, team_intensity, team_shape, xg_model, zone_map)
from insidethegame_data.prepare import ACC_CODE
from insidethegame_data.touches import ball_kinematics, touch_table

from .scenes import FS, N, T0, period

L, GOAL_W = 105.0, 7.32


def test_xg_shared_function_is_the_calibration_model_with_outfield_opponents_in_the_triangle():
    """METRICS.md section 9: the engine's xG is the calibration's code path on the committed model."""
    m = xg_model()
    ex = {e["description"]: e for e in m["examples"]}
    six = shot_xg(L / 2 - 6.0, 0.0, [], [], L, GOAL_W)  # 6 m central, nobody in the triangle
    assert six["xg"] == pytest.approx(ex["6 m central (centre of the goal-area line), no defender in the triangle"]["xg"], abs=1e-4)
    assert six["visible_angle_rad"] == pytest.approx(2 * math.atan(3.66 / 6.0), abs=1e-9)
    # Two opponents inside the triangle from the penalty spot, one outside (behind the shooter): n = 2.
    spot = shot_xg(L / 2 - 11.0, 0.0, [L / 2 - 5.0, L / 2 - 3.0, L / 2 - 20.0], [0.5, -0.5, 0.0], L, GOAL_W)
    assert spot["opponents_in_triangle"] == 2
    assert spot["xg"] == pytest.approx(ex["penalty spot in open play, two defenders in the triangle"]["xg"], abs=1e-4)
    # The same value as the calibration's own two functions, and always a probability.
    f = calibration.shot_features(L / 2 - 11.0, 0.0, np.array([L / 2 - 5.0]), np.array([0.5]), L, GOAL_W)
    assert shot_xg(L / 2 - 11.0, 0.0, [L / 2 - 5.0], [0.5], L, GOAL_W)["xg"] == calibration.xg(**f)
    for x, y in ((L / 2 - 0.5, 0.0), (0.0, 30.0), (L / 2 + 2.0, 0.0), (L / 2 - 1.0, 20.0)):
        assert 0.0 < shot_xg(x, y, [], [], L, GOAL_W)["xg"] < 1.0


def test_xg_penalty_is_a_fixed_value_from_the_model_file():
    assert penalty_xg() == 0.79 == xg_model()["penalty_xg"]


def _shot(knots, players, frame, bz=None):
    sp = period(knots, players, bz=bz)
    tt = touch_table(sp, ball_kinematics(sp, CFG, 100.0), CFG)
    return shot_kick(sp, tt, sp.pids.index("A"), T0 + frame / FS, 5.0, CFG)


def test_shot_kick_is_the_shooters_best_kick_candidate_ground_or_header():
    """METRICS.md section 9: the shot is located at the shooter's kick (the calibration's kick candidates)."""
    players = {"A": (40.0, 0.0, "home"), "G": (49.5, 0.3, "away"), "C": (0.0, 25.0, "away")}
    ground = [(0, 40.3, 0), (100, 40.3, 0), (110, 48.3, 0), (N - 1, 48.3, 0)]  # struck at frame 100, 20 m/s
    assert abs(_shot(ground, players, 100 + 2 * FS) - 100) <= 1  # annotated 2 s late: still the kick
    header = [(0, 30.0, 20.0), (100, 40.2, 0.5), (110, 46.0, 0.5), (N - 1, 46.0, 0.5)]  # a cross nodded on at 2 m
    assert abs(_shot(header, players, 100, bz=np.full(N, 2.0)) - 100) <= 1
    assert _shot(ground, {**players, "A": (0.0, -20.0, "home")}, 100) is None  # the shooter never near the ball


def test_chance_words_follow_the_thresholds_of_decision_11():
    assert [chance_word(v, CFG) for v in (0.30, 0.2999, 0.10, 0.0999, 0.0, 0.79)] == \
        ["big chance", "decent chance", "decent chance", "half chance", "half chance", "big chance"]


def test_r_sprint_at_or_above_25_kmh_merged_when_less_than_1_s_apart_and_never_bridged():
    """METRICS.md decision 21: >= 25 km/h for >= 1 s; two sprints less than 1 s apart count as one."""
    ok = np.ones(300, dtype=bool)
    sp = np.full(300, 5.0)
    sp[10:35] = 25.0 / 3.6  # exactly 25.0 km/h for 1.0 s: a sprint
    assert count_sprints(sp, ok, FS, CFG) == 1
    sp = np.full(300, 5.0)
    sp[10:40] = sp[64:94] = 27.0 / 3.6  # two 1.2 s sprints, 24 samples (0.96 s) below the threshold in between
    assert sprint_runs(sp, ok, FS, CFG) == [(10, 93)]
    sp[64:94], sp[65:95] = 5.0, 27.0 / 3.6  # 25 samples (1.0 s) apart: two sprints
    assert count_sprints(sp, ok, FS, CFG) == 2
    sp = np.full(300, 5.0)
    sp[10:40] = sp[50:80] = 27.0 / 3.6
    bad = ok.copy()
    bad[45] = False  # an invalid or estimated sample in the gap is never bridged
    assert count_sprints(sp, ok, FS, CFG) == 1 and count_sprints(sp, bad, FS, CFG) == 2
    sp = np.full(300, 5.0)
    sp[10:34] = 30.0 / 3.6  # 0.96 s: no sprint
    assert count_sprints(sp, ok, FS, CFG) == 0


def test_peak_intensity_rolling_window_per_player_and_team_mean_of_players_on_the_pitch():
    """METRICS.md decision 4: rolling 5-minute window, 1 s step; a player counts only when on the pitch for it all."""
    fs = 1.0
    clock = np.arange(0.0, 900.0, 1.0 / fs) + 1.0  # samples at 1 ... 900 s of the period clock
    dist = np.full((900, 2), 2.0)  # 2 m per second = 120 m/min
    dist[400:700, 0] = 3.0  # player 0: 180 m/min from 401 s to 700 s
    present = np.ones((900, 2), dtype=bool)
    present[:499, 1] = False  # player 1 comes on at 500 s
    ends, mpm, full = rolling_intensity(dist, present, clock, 900.0, CFG)
    assert ends[0] == 300.0 and ends[-1] == 900.0 and len(ends) == 601  # one window per second
    assert mpm[int(np.argmax(mpm[:, 0])), 0] == pytest.approx(180.0) and ends[int(np.argmax(mpm[:, 0]))] == 700.0
    assert not full[ends < 799.0, 1].any() and full[ends >= 799.0, 1].all()
    team = team_intensity(mpm, full, [0, 1])
    k = int(np.flatnonzero(ends == 700.0)[0])
    assert team[k] == pytest.approx(180.0)  # player 1 is not on the pitch for the whole window yet
    k = int(np.flatnonzero(ends == 850.0)[0])
    assert team[k] == pytest.approx((mpm[k, 0] + 120.0) / 2)
    assert len(rolling_intensity(dist, present, clock, 299.0, CFG)[0]) == 0  # under five minutes: no window yet


def _pass(knots, players, frame):
    sp = period(knots, players)
    tt = touch_table(sp, ball_kinematics(sp, CFG, 100.0), CFG)
    return pass_reception(sp, tt, sp.pids.index("A"), T0 + frame / FS, 5.0, CFG)


def test_pass_length_of_successful_passes_only_and_long_balls_from_32_m():
    """METRICS.md decision 13: the length runs from the located kick to the team-mate's reception; >= 32 m is long."""
    mates = {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")}
    short = _pass([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)], mates, 100)
    assert short["teammate"] and abs(short["kick"] - 100) <= 1 and short["length_m"] == pytest.approx(14.7, abs=0.3)
    cut = _pass([(0, 0.3, 0), (100, 0.3, 0), (113, 8.0, 0), (N - 1, 8.0, 0)],
                {**mates, "C": (8.4, 0.5, "away")}, 100)
    assert not cut["teammate"] and cut["length_m"] is None  # intercepted: no length
    far = _pass([(0, 0.3, 0), (100, 0.3, 0), (150, 34.7, 0), (N - 1, 34.7, 0)], {**mates, "B": (35.2, 0, "home")}, 100)
    assert far["teammate"] and far["length_m"] == pytest.approx(34.4, abs=0.3)
    assert long_balls([short["length_m"], far["length_m"]], CFG) == 1 and long_balls([31.99, 32.0], CFG) == 1
    assert _pass([(0, 30.0, 20.0), (N - 1, 30.0, 20.0)], mates, 100) is None  # no kick of the passer: not located


def test_circulation_leaves_out_the_2_s_after_each_shot_and_clearance():
    t = np.arange(0.0, 20.0, 0.04)
    m = after_kicks_mask(t, [5.0, 12.0], CFG.circulation_exclude_after_s)
    assert m[(t >= 5.0) & (t <= 7.0)].all() and m[(t >= 12.0) & (t <= 14.0)].all()
    assert not m[(t < 5.0) | ((t > 7.0 + 1e-9) & (t < 12.0)) | (t > 14.0 + 1e-9)].any()
    assert not after_kicks_mask(t, [], 2.0).any()


def test_momentum_territory_and_danger_with_the_edge_cases_and_never_outside_minus_1_plus_1():
    """METRICS.md decision 15: 0.5 tilt + 0.5 danger; no shot: tilt only; no final-third time: danger only."""
    assert momentum_value(30.0, 10.0, 0.6, 0.2, CFG) == pytest.approx(0.5 * 0.5 + 0.5 * 0.5)
    assert momentum_value(10.0, 30.0, 0.0, 0.0, CFG) == pytest.approx(-0.5)  # no shot: tilt only
    assert momentum_value(0.0, 0.0, 0.1, 0.3, CFG) == pytest.approx(-0.5)  # no final-third possession: danger only
    assert momentum_value(0.0, 0.0, 0.0, 0.0, CFG) == 0.0  # neither
    assert momentum_value(25.0, 0.0, 0.4, 0.0, CFG) == 1.0 and momentum_value(0.0, 5.0, 0.0, 0.0, CFG) == -1.0
    rng = np.random.default_rng(0)
    for _ in range(2000):
        th, ta = rng.choice([0.0, 1e-9, 7.0, 300.0], 2) * rng.random(2)
        xh, xa = rng.choice([0.0, 1e-12, 0.05, 0.79], 2) * rng.random(2) * 3
        assert -1.0 <= momentum_value(th, ta, xh, xa, CFG) <= 1.0


def test_acceleration_load_sums_the_change_of_speed_on_measured_and_approx_samples_only():
    """METRICS.md decision 24: sum of |dv/dt| dT; noise samples (R-ACC-BAND) and invalid samples never count."""
    acc = np.r_[np.full(50, 2.0), np.full(50, -3.0), np.full(10, 9.0)]  # +4 m/s, then -6 m/s, then a 0.4 s spike
    st = np.r_[np.full(100, ACC_CODE["measured"]), np.full(10, ACC_CODE["noise"])]
    valid = np.ones(110, dtype=bool)
    assert acceleration_load_samples(acc, st, valid, FS).sum() == pytest.approx(10.0)  # m/s; x 3.6 = 36 km/h
    st[50:60] = ACC_CODE["approx"]  # an approximate exceedance still counts
    valid[:25] = False  # invalid samples do not
    assert acceleration_load_samples(acc, st, valid, FS).sum() == pytest.approx(8.0)
    assert acceleration_load_samples(np.full(5, np.nan), None, np.ones(5, dtype=bool), FS).sum() == 0.0


def test_ball_recovery_needs_a_live_possession_change_from_the_opponent_near_the_event():
    """METRICS.md decision 23: from the events, checked against the provider possession column."""
    poss = np.r_[np.full(100, 2.0), np.full(100, 1.0)]  # away, then home from frame 100
    live = np.ones(200, dtype=bool)
    assert possession_gain_frame(poss, live, 110, 1.0, 2.0, 75) == 100  # a home event 10 frames after the change
    assert possession_gain_frame(poss, live, 190, 1.0, 2.0, 75) is None  # too far from the change
    assert possession_gain_frame(poss, live, 100, 2.0, 1.0, 75) is None  # the away team lost it: no recovery
    dead = live.copy()
    dead[95:105] = False
    assert possession_gain_frame(poss, dead, 100, 1.0, 2.0, 75) is None  # the change happened with the ball dead


def test_counter_attack_recovery_in_own_half_then_a_shot_within_15_s_without_losing_the_ball():
    """METRICS.md decision 26: own half, live play, a shot by the same team within 15 s, no opponent possession."""
    n = 1000
    poss, live = np.full(n, 1.0), np.ones(n, dtype=bool)
    shots = [(400, 1.0), (450, 2.0), (900, 1.0)]
    assert counter_attacks([(100, 1.0, True)], shots, poss, live, FS, CFG) == [(0, 0)]  # 12 s later
    assert counter_attacks([(100, 1.0, False)], shots, poss, live, FS, CFG) == []  # in the opponent's half
    assert counter_attacks([(10, 1.0, True)], shots, poss, live, FS, CFG) == []  # the first shot comes 15.6 s later
    lost = poss.copy()
    lost[200:210] = 2.0
    assert counter_attacks([(100, 1.0, True)], shots, lost, live, FS, CFG) == []  # the opponent had the ball between
    stop = live.copy()
    stop[300] = False
    assert counter_attacks([(100, 1.0, True)], shots, poss, stop, FS, CFG) == []  # a stoppage ends it


def test_zone_map_1_m_grid_smoothed_in_5_by_4_m_cells_attacking_left_to_right():
    """METRICS.md decision 19: sigma 3 m, normalised to 100 %, row 0 = the left touchline, column 0 = own goal."""
    x = np.r_[np.full(300, -50.0), np.full(100, 40.0)]  # 3/4 of the time near the own goal line, 1/4 far forward
    y = np.r_[np.full(300, 30.0), np.full(100, -10.0)]  # ... on the left (+y), then right of centre
    z = zone_map(x, y, L, 68.0, CFG)
    assert z.shape == (17, 21) and z.sum() == pytest.approx(100.0)
    r, c = np.unravel_index(int(np.argmax(z)), z.shape)
    assert (r, c) == (0, 0)  # top-left cell: own goal line, left touchline
    assert z[:, :10].sum() == pytest.approx(75.0, abs=1.0) and z[10:12, 18].sum() > 5.0  # 40 m: column 18; -10 m: row 10-11
    assert 0 < z[1, 1] < z[0, 0]  # the 3 m Gaussian spreads the time into the next cells
    assert zone_map(np.array([np.nan]), np.array([0.0]), L, 68.0, CFG) is None


def test_defensive_line_two_deepest_and_robust_team_length():
    """METRICS.md decision 20: line = mean distance from the own goal line of the 2 deepest; length without the ends."""
    x = np.array([[-40.0, -30.0, -10.0, 0.0, 15.0, 30.0], [-40.0, -30.0, -10.0, 0.0, 15.0, np.nan],
                  [-40.0, -30.0, -10.0, 0.0, 15.0, 30.0]])
    on = np.ones((3, 6), dtype=bool)
    on[2, 4:] = False  # two players off the pitch: still 4 on it
    line, length = team_shape(x, on, L)
    assert line[0] == pytest.approx((12.5 + 22.5) / 2) and length[0] == pytest.approx(15.0 - (-30.0))
    assert np.isnan(line[1]) and np.isnan(length[1])  # a player on the pitch without a valid position: no value
    assert length[2] == pytest.approx(-10.0 - (-30.0))
    assert np.isnan(team_shape(x[:1, :3], on[:1, :3], L)[0][0])  # fewer than 4 outfield players


def test_ppda_pressing_zone_is_the_60_percent_nearest_the_opponent_goal():
    """METRICS.md decision 17: opponent passes in the zone per defensive action there; undefined without actions."""
    assert in_pressing_zone(-0.1 * L, L, CFG) and not in_pressing_zone(-0.1 * L - 0.01, L, CFG)
    assert ppda(24, 3) == 8.0 and ppda(5, 0) is None
