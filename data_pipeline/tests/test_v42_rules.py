"""Rules new or changed in guidelines v4.1 / v4.2 (E-1, 25 October 2026). Tiny synthetic scenes with hand-computed
answers (no real data). Scenes: see ``tests/scenes.py`` (pitch 100 x 60, the home team attacks the goal at x = +50,
25 Hz; annotated times are 2 s early and the period's event clock offset of +2 s corrects them).
"""

from dataclasses import replace

import numpy as np

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.events_sync import align, mark_duel_shot_pairs
from insidethegame_data.goals import ball_out_mask, goal_crossings
from insidethegame_data.outcomes import shot_direction, wide_margin
from insidethegame_data.touches import aerial_frames, ball_kinematics, goal_extension, touch_table, woodwork_frames

from .scenes import FS, L, N, N0, locate, period, restart, shot

PLAYERS = {"A": (0, 20, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, -10, "away")}


def at(frame: float) -> float:
    """Annotated frame of an event whose corrected time is ``frame`` (the scenes' offset is +2 s)."""
    return frame - 2 * FS


# ---------------------------------------------------------------------------
# R-SG-ACC (v4.1)
# ---------------------------------------------------------------------------

def test_r_sg_acc_v4_1_acceleration_window_is_19_samples_and_speed_stays_13():
    assert CFG.acc_window(25.0) == 19 and CFG.savgol_window(25.0) == 13


# ---------------------------------------------------------------------------
# R-GOAL entry rule (v4.2)
# ---------------------------------------------------------------------------

def _exit_scene(back_samples: int, exit_x: float = 52.0):
    """A cross goes over the goal line 5 m from the centre (outside the post) at frame 240 (x = ``exit_x``); the provider
    then puts it back on the pitch for ``back_samples`` samples and the next sample is behind the line between the posts.
    The ball is dead from frame 255; a goal kick restarts play at frame 400."""
    knots = [(0, 30.0, 5.0), (200, 30.0, 5.0), (240, exit_x, 5.0)]
    for j in range(back_samples):
        knots.append((241 + j, 49.0, 3.0 - 0.1 * j))
    f = 241 + back_samples
    knots += [(f, 51.0, 2.0), (390, 51.0, 2.0), (391, 44.5, 0.0), (N - 1, 44.5, 0.0)]
    st = np.ones(N)
    st[255:400] = 0
    return period(knots, PLAYERS, status=st), f


def test_r_goal_v4_2_entry_rule_a_ball_already_out_does_not_come_back_with_two_wrong_samples():
    sp, f = _exit_scene(back_samples=2)
    assert ball_out_mask(sp, 1.0, L, CFG)[f - 1]  # still out after 2 samples on the pitch
    assert goal_crossings(sp, L, CFG) == []
    ev = locate(sp, [restart("GoalKick", "T-A", 400)])
    assert ev.attrs["goal_crossings"] == []  # v4 listed a fake disallowed goal here (episode 2)


def test_r_goal_v4_2_entry_rule_where_the_ball_crossed_decides_not_where_it_is_afterwards():
    """Episode 2: the ball crosses the line 1.1 m wide of the post (y = -4.77), its first sample wholly over the line is
    already behind the line between the posts (y = -3.65, from the side), two wrong samples bring it back on the pitch,
    and the next sample is behind the line between the posts again: no crossing."""
    knots = [(0, -30.0, -30.0), (200, -30.0, -30.0), (236, -52.46, -4.98), (237, -52.59, -4.30), (238, -52.75, -3.65),
             (239, -52.36, -2.38), (240, -52.40, -2.44), (241, -53.10, -3.20), (390, -53.1, -3.2), (391, -47.0, 0.0), (N - 1, -47.0, 0.0)]
    st = np.ones(N)
    st[250:400] = 0
    sp = period(knots, PLAYERS, status=st)
    assert ball_out_mask(sp, -1.0, L, CFG)[240] and goal_crossings(sp, L, CFG) == []


def test_r_goal_v4_2_entry_rule_five_samples_back_on_the_pitch_make_the_ball_valid_again():
    sp, f = _exit_scene(back_samples=5)  # 0.2 s at 25 Hz
    rows = goal_crossings(sp, L, CFG)
    assert [r["index"] for r in rows] == [f] and rows[0]["crossing_method"] == "observed"


def test_r_goal_v4_2_entry_rule_a_ball_grazing_the_line_never_goes_out():
    sp, f = _exit_scene(back_samples=2, exit_x=50.05)  # centre beyond the line by less than the ball radius (0.11 m)
    assert not ball_out_mask(sp, 1.0, L, CFG).any()
    assert [r["index"] for r in goal_crossings(sp, L, CFG)] == [f]


# ---------------------------------------------------------------------------
# R-GOAL-FREEZE (v4.2)
# ---------------------------------------------------------------------------

def _freeze_scene(stop_x: float = 49.8, keeper=(50.0, -10.0)):
    """B shoots at 25 m/s (1 m per frame) along y = 0.5; the last live sample is at frame 219 at x = ``stop_x``; from frame
    220 the provider holds the ball there (dead, frozen) until a kickoff at frame 400."""
    k0 = 219 - (stop_x - 30.3)
    knots = [(0, 30.3, 0.5), (k0, 30.3, 0.5), (219, stop_x, 0.5), (390, stop_x, 0.5), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    st = np.ones(N)
    st[220:400] = 0
    return period(knots, {"A": (0, 20, "home"), "B": (30, 0.5, "home"), "C": (20, 25, "away"), "G": (*keeper, "away")}, status=st), k0


def test_r_goal_freeze_v4_2_crossing_extrapolated_over_a_frozen_ball_is_a_goal():
    sp, k0 = _freeze_scene()
    rows = goal_crossings(sp, L, CFG, ball_kinematics(sp, CFG, L))
    assert len(rows) == 1 and rows[0]["crossing_method"] == "extrapolated" and rows[0]["index"] == 220
    assert 0 < rows[0]["extrapolated_s"] <= 0.08 and abs(rows[0]["y_line_m"] - 0.5) < 0.05
    ev = locate(sp, [shot("B", "T-H", k0, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["confirmed_by"] == "kickoff" and gc["kicker_id"] == "B"


def test_r_goal_v4_2_a_peak_within_0_2_s_of_the_crossing_is_not_a_deflection():
    """Episode 3 after the data lead's decisions: the filter-edge peak on the last live sample before a provider freeze, with the
    goalkeeper 1.3 m away, is not his deflection (the net, as for R-WOODWORK): the goal stays with the shooter. The ball
    curves slightly 3 frames before the freeze, so the filter puts an |a| peak on the last live sample, given to G."""
    st = np.ones(N)
    st[220:400] = 0
    k0 = 219 - (49.8 - 30.3)
    knots = [(0, 30.3, 0.5), (k0, 30.3, 0.5), (216, 46.8, 0.5), (219, 49.8, 1.1), (390, 49.8, 1.1), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    sp = period(knots, {"A": (0, 20, "home"), "B": (30, 0.5, "home"), "C": (20, 25, "away"), "G": (50.6, 2.1, "away")}, status=st)
    tt = touch_table(sp, ball_kinematics(sp, CFG, L), CFG)
    assert [(int(i), sp.pids[c]) for i, c in zip(tt.frames, tt.col) if 205 <= i <= 221 and c >= 0] == [(219, "G")]  # the edge peak
    ev = locate(sp, [shot("B", "T-H", k0, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["crossing_method"] == "extrapolated" and gc["index"] == 220
    assert gc["scorer_id"] == "B" and gc["own_goal"] is False and gc["deflected_by_id"] is None


def test_r_goal_freeze_v4_2_not_with_a_player_within_1_m_nor_beyond_2_frames():
    sp, _ = _freeze_scene(keeper=(50.3, 1.2))  # the keeper is 0.86 m from the ball at the last live sample
    assert goal_crossings(sp, L, CFG) == []
    sp, _ = _freeze_scene(stop_x=47.0)  # 3 m before the line at 25 m/s: 0.12 s, more than 2 frames
    assert goal_crossings(sp, L, CFG) == []


# ---------------------------------------------------------------------------
# R-GOAL: end of the period (v4.1), disallowed reason (v4.2), shot search on unconfirmed crossings (v4.2)
# ---------------------------------------------------------------------------

def _end_scene():
    """B shoots at frame 200 from (30.3, 0) at 25 m/s into the goal (crossing at frame 220); the ball is dead from frame
    223 to the end of the period: no restart follows."""
    st = np.ones(N)
    st[223:] = 0
    return period([(0, 30.3, 0), (200, 30.3, 0), (222, 52.0, 0.5), (N - 1, 52.0, 0.5)], PLAYERS, status=st)


def test_r_goal_v4_1_no_restart_and_a_final_whistle_confirm_the_goal_by_the_end_of_the_period():
    ev = locate(_end_scene(), [shot("B", "T-H", 200, "SuccessfulShot"), ("FinalWhistle", {}, at(500))])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["confirmed_by"] == "end_of_period" and gc["shot_event_id"] == "E0"
    assert ev.iloc[0]["type_check_basis"] == "goal" and ev.iloc[0]["goal_confirmed_by"] == "end_of_period"


def test_r_goal_v4_1_a_var_check_before_the_whistle_leaves_it_unconfirmed_and_a_caution_does_not():
    rows = [shot("B", "T-H", 200, "SuccessfulShot"), ("VideoAssistantAction", {"ProofedEvent": "goal"}, at(300)), ("FinalWhistle", {}, at(500))]
    gc = locate(_end_scene(), rows).attrs["goal_crossings"][0]
    assert gc["goal_check"] == "unconfirmed" and gc["confirmed_by"] is None
    # v4.2: the backward shot search also runs on an unconfirmed crossing, and the annotated shot is matched.
    assert gc["kicker_id"] == "B" and abs(gc["kick_frame"] - (N0 + 200)) <= 1 and gc["shot_event_id"] == "E0"
    rows[1] = ("Caution", {"Player": "B", "Team": "T-H"}, at(300))  # a caution alone (for the celebration) does not block
    assert locate(_end_scene(), rows).attrs["goal_crossings"][0]["goal_check"] == "goal"
    assert locate(_end_scene(), rows[:1]).attrs["goal_crossings"][0]["goal_check"] == "unconfirmed"  # no final whistle


def test_r_goal_v4_2_a_disallowed_goal_takes_the_reason_of_a_nearby_goal_disallowed():
    st = np.ones(N)
    st[223:400] = 0
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (222, 52.0, 0.5), (390, 52.0, 0.5), (391, 20, 0), (N - 1, 20, 0)], PLAYERS, status=st)
    rows = [("GoalDisallowed", {"Player": "B", "Team": "T-H", "Reason": "handBall"}, at(232)), restart("FreeKick", "T-A", 400)]
    gc = locate(sp, rows).attrs["goal_crossings"][0]
    assert gc["goal_check"] == "disallowed_goal" and gc["disallowed_reason"] == "handBall" and gc["goal_disallowed_event_id"] == "E0"


# ---------------------------------------------------------------------------
# R-GOAL step 4 (v4.2): in-goal kinematics, search up to the crossing frame; attribution with a deflection (v4.1)
# ---------------------------------------------------------------------------

def test_r_goal_v4_2_live_segment_continues_through_the_in_goal_samples_until_the_ball_is_frozen():
    st = np.ones(N)
    st[223:400] = 0
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (222, 52.0, 0.5), (226, 52.6, 0.5), (390, 52.6, 0.5), (391, 0, 0), (N - 1, 0, 0)], PLAYERS, status=st)
    ext = goal_extension(sp, sp.bstatus == 1, L, CFG)
    assert ext[223:226].all() and not ext[227:].any()  # frozen from 227 (position held)
    bk = ball_kinematics(sp, CFG, L)
    assert np.isfinite(bk["acc"][223]) and np.isnan(ball_kinematics(sp, CFG)["acc"][223])


def test_r_goal_v4_2_a_tap_in_just_before_the_line_is_the_goal_shot_not_the_pass_across_the_box():
    """P passes across the box at frame 250 (about 7 m/s) to the goal mouth; S taps it in at frame 300, 0.4 m before the
    line, at 15 m/s (the crossing comes about 1 frame later). The goal shot is S's touch, searched up to and including
    the crossing frame with the in-goal samples in the ball's kinematics (episode 6)."""
    st = np.ones(N)
    st[305:400] = 0
    knots = [(0, 40.0, -10.0), (250, 40.0, -10.0), (300, 49.6, 0.0), (304, 52.0, 0.6), (306, 52.6, 0.6), (390, 52.6, 0.6), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    sp = period(knots, {"P": (39.7, -10.2, "home"), "S": (49.3, 0.25, "home"), "C": (20, 25, "away"), "G": (50, -3.5, "away")}, status=st)
    ev = locate(sp, [shot("S", "T-H", 300, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["kicker_id"] == "S" and abs(gc["kick_frame"] - (N0 + 300)) <= 2 and gc["shooter_mismatch"] is False


def _deflection_scene(target_y: float, deflector: str, deflect_side: str, deflect_x: float = 44.0):
    """B shoots at frame 200 from (30.3, 0) at about 25 m/s towards the goal line at y = ``target_y``; at x = ``deflect_x``
    the player ``deflector`` (side ``deflect_side``) deflects it into the goal (crossing near y = 1); the ball is dead
    soon after the crossing, kickoff at frame 400."""
    yd = target_y * (deflect_x - 30.3) / (50 - 30.3)
    f_d = 200 + int(round(np.hypot(deflect_x - 30.3, yd)))
    f_e = f_d + int(round(np.hypot(52.0 - deflect_x, 0.8 - yd) / 0.95))
    st = np.ones(N)
    st[f_e + 2:400] = 0
    # The ball goes on into the net for 6 more frames before the provider holds it (the stop is far from the deflection).
    knots = [(0, 30.3, 0), (200, 30.3, 0), (f_d, deflect_x, yd), (f_e, 52.0, 0.8), (f_e + 6, 57.5, 0.8), (390, 57.5, 0.8), (391, 0, 0), (400, 0, 0),
             (N - 1, -12, 0)]
    players = {"A": (0, 20, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, -12, "away"), deflector: (deflect_x + 0.3, yd + 0.2, deflect_side)}
    return period(knots, players, status=st)


def test_r_goal_v4_1_defender_deflecting_a_shot_that_was_going_wide_scores_an_own_goal():
    ev = locate(_deflection_scene(12.0, "D", "away"), [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["kicker_id"] == "B" and gc["deflected_by_id"] == "D"
    assert gc["pre_deflection_on_target"] is False and gc["own_goal"] is True and gc["scorer_id"] == "D"
    assert gc["shooter_mismatch"] is True  # the annotated scorer B is not the attributed scorer: listed for review


def test_r_goal_v4_1_defender_deflecting_a_shot_on_target_leaves_the_goal_to_the_shooter():
    # The shot was going in at y = -3.0 (between the posts); the defender's touch at x = 44 turns it by about 29 deg.
    ev = locate(_deflection_scene(-3.0, "D", "away"), [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["deflected_by_id"] == "D" and gc["pre_deflection_on_target"] is True
    assert gc["own_goal"] is False and gc["scorer_id"] == "B" and gc["shooter_mismatch"] is False


def test_r_goal_v4_1_teammate_deflection_gives_the_goal_to_the_teammate():
    gc = locate(_deflection_scene(12.0, "T", "home"), [restart("KickOff", "T-A", 400)]).attrs["goal_crossings"][0]
    assert gc["kicker_id"] == "T" and gc["scorer_id"] == "T" and gc["own_goal"] is False


# ---------------------------------------------------------------------------
# R-TOUCH-NEAREST (v4.2), ground touch height (v4.2), R-AERIAL incoming path and thresholds (v4.1)
# ---------------------------------------------------------------------------

def _pass_scene(opponent_xy):
    """B (home) at (-0.5, 0), 0.8 m from the ball at (0.3, 0), passes at frame 100 at 15 m/s to T at (15.5, 0); the
    opponent C stands at ``opponent_xy``."""
    return period([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)],
                  {"B": (-0.5, 0, "home"), "T": (15.5, 0, "home"), "C": (*opponent_xy, "away"), "G": (48, 0, "away")})


def test_r_touch_nearest_v4_2_a_kick_is_not_the_players_when_an_opponent_is_much_nearer_at_the_contact():
    r = locate(_pass_scene((0.35, 0.05)), [("Play", {"Player": "B", "Team": "T-H"}, at(100))]).iloc[0]
    assert r["sync_method"] == "no_signature_in_window"  # C is 0.07 m from the ball, B 0.8 m (more than 0.3 m further)
    r = locate(_pass_scene((-0.2, 1.0)), [("Play", {"Player": "B", "Team": "T-H"}, at(100))]).iloc[0]
    assert r["sync_method"] == "kick_signature" and r["next_touch_object_id"] == "T"  # C 1.12 m behind: B is the nearest
    r = locate(_pass_scene((0.3, 0.6)), [("Play", {"Player": "B", "Team": "T-H"}, at(100))]).iloc[0]
    assert r["sync_method"] == "kick_signature"  # C 0.6 m: B (0.8 m) is within 0.3 m of the nearest


def _header_scene(z: float, turn_deg: float = 30.0, opponent=None):
    v, th = 4.0, np.radians(turn_deg)
    p1 = np.array([40.0, 0.0])
    p0 = p1 - v * 2.0 * np.array([np.cos(th), np.sin(th)])
    end = 40.0 + v * 37 / FS
    players = {"A": (0, 0, "home"), "B": (40.0, 0.12, "home"), "C": (20, 25, "away"), "G": (end + 0.5, 0.5, "away")}
    if opponent is not None:
        players["X"] = (*opponent, "away")
    return period([(0, *p0), (250, *p0), (300, *p1), (337, end, 0), (N - 1, end, 0)], players, bz=np.full(N, z))


def test_r_touch_nearest_v4_2_compares_the_closest_approaches_within_0_2_s():
    # X (opponent) is 0.10 m from the incoming ball 4 frames before the header, B is 0.12 m from it at the header: at the
    # frame of X's closest approach B is 0.7 m away, but the closest approaches within +-0.2 s are compared (0.12 m is
    # within 0.3 m of 0.10 m), as the reach test of R-TOUCH does.
    th = np.radians(30.0)
    xb = np.array([40.0, 0.0]) - 4.0 * (4 / FS) * np.array([np.cos(th), np.sin(th)])
    sp = _header_scene(1.7, opponent=(xb[0], xb[1] + 0.10))
    r = locate(sp, [shot("B", "T-H", 300, "SavedShot", TypeOfShot="head")]).iloc[0]
    assert r["sync_method"] == "aerial_signature" and int(r["sync_frame"]) == N0 + 300


def test_r_touch_nearest_v4_2_aerial_margin_is_0_5_m_ground_stays_0_3_m():
    """Decision (E-1): tracking measures the torso, not the head, so in an aerial duel the defender's torso can be
    0.3-0.4 m nearer to the ball than the header taker's. B's closest approach is 0.45 m, the opponent X's 0.05 m (0.40 m
    nearer): with the aerial margin of 0.5 m the header is B's; with 0.3 m it would not be."""
    from insidethegame_data.events_sync import locate_play_events

    from .scenes import GK, OFF, SIDES, events

    th = np.radians(30.0)
    xb = np.array([40.0, 0.0]) - 4.0 * (3 / FS) * np.array([np.cos(th), np.sin(th)])
    sp = _header_scene(1.7, opponent=(xb[0], xb[1] + 0.05))
    sp.py[:, sp.pids.index("B")] = 0.45
    rows = [shot("B", "T-H", 300, "SavedShot", TypeOfShot="head")]
    assert CFG.touch_nearest_margin_aerial_m == 0.5 and CFG.touch_nearest_margin_m == 0.3
    r = locate_play_events(events(rows), {1: sp}, OFF, GK, SIDES, L, CFG).iloc[0]
    assert r["sync_method"] == "aerial_signature" and int(r["sync_frame"]) == N0 + 300
    r = locate_play_events(events(rows), {1: sp}, OFF, GK, SIDES, L, replace(CFG, touch_nearest_margin_aerial_m=0.3)).iloc[0]
    assert r["sync_method"] != "aerial_signature" or int(r["sync_frame"]) != N0 + 300


def test_r_aerial_v4_1_incoming_path_height_ignores_a_single_wrong_sample():
    sp = _header_scene(1.7)
    sp.bz[300] = 0.0  # a height reset to 0 at the contact sample, between airborne samples
    assert 300 in aerial_frames(sp, ball_kinematics(sp, CFG), CFG)


def test_r_aerial_v4_1_thresholds_10_deg_and_2_ms2():
    sp = _header_scene(1.7, turn_deg=15.0)  # a 15 deg turn: |a| peak of about 2.5 m/s2
    bk = ball_kinematics(sp, CFG)
    assert 2.0 <= bk["acc"][300] < 4.0
    assert 300 in aerial_frames(sp, bk, CFG)
    assert 300 not in aerial_frames(sp, bk, replace(CFG, aerial_dir_change_min_deg=20.0, aerial_acc_min_ms2=4.0))  # the v4 thresholds


def test_r_touch_v4_2_a_peak_above_1_5_m_is_not_a_ground_touch_but_stays_a_touch():
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (244, 47.9, 0), (N - 1, 47.9, 0)], {"B": (30, 0, "home"), "G": (48.4, 0.3, "away")}, bz=np.full(N, 1.8))
    tt = touch_table(sp, ball_kinematics(sp, CFG), CFG)
    j = int(np.flatnonzero(np.abs(tt.frames - 244) <= 1)[0])
    assert not tt.ground[j] and tt.touch[j] and sp.pids[tt.col[j]] == "G"  # the keeper's catch at 1.8 m ends the flight
    j0 = np.flatnonzero(np.abs(tt.frames - 200) <= 1)
    assert not tt.ground[j0].any()  # a kick of a ball resting at 1.8 m is never a ground kick


# ---------------------------------------------------------------------------
# R-SHOT-DIRECTION margins (v4.1), blocks at the kick (v4.2)
# ---------------------------------------------------------------------------

def test_r_shot_direction_v4_1_8_m_margin_beyond_a_post_for_long_range_or_powerful_shots():
    def line(x0, y_line):
        return period([(0, x0, 0), (100, x0, 0), (100 + int(50 - x0), 50.0, y_line), (N - 1, 50.0, y_line)], PLAYERS, with_speed=False)

    sp = line(30.0, 3.66 + 7.0)  # kick 20 m from the goal line, the line crosses 7 m beyond the post
    m = wide_margin(sp, 100, 1.0, L, None, 94.86, CFG)
    assert m == 8.0 and shot_direction(sp, 100, 115, 1.0, L, CFG, margin_m=m)["direction"] == "ok"
    sp = line(38.0, 3.66 + 7.0)  # 12 m from the goal line: 6 m
    m = wide_margin(sp, 100, 1.0, L, None, 94.86, CFG)
    assert m == 6.0 and shot_direction(sp, 100, 108, 1.0, L, CFG, margin_m=m)["direction"] == "direction_outside_6m"
    assert wide_margin(sp, 100, 1.0, L, 100.0, 94.86, CFG) == 8.0  # above the power threshold


def _away_scene(opponent: str, opp_xy):
    """B kicks at frame 200 from (30.3, 0) and the ball flies back towards his own goal (kick and block merged in one
    |a| peak); the opponent ``opponent`` stands at ``opp_xy`` at the kick."""
    players = {"A": (0, 20, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, -10, "away")}
    players[opponent] = (*opp_xy, "away")
    st = np.ones(N)
    st[231:] = 0
    return period([(0, 30.3, 0), (200, 30.3, 0), (230, 18.3, 0.0), (N - 1, 18.3, 0)], players, status=st)


def test_r_shot_outcome_v4_2_away_from_goal_with_an_opponent_within_1_m_at_the_kick_is_blocked():
    r = locate(_away_scene("C", (30.9, 0.3)), [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]
    assert r["shot_direction"] == "away_from_goal" and bool(r["shot_blocked_at_kick"])
    assert r["shot_outcome_observed"] == "blocked" and r["type_check"] == "confirmed" and r["type_check_basis"] == "blocked"
    r = locate(_away_scene("G", (30.9, 0.3)), [shot("B", "T-H", 200, "SavedShot")]).iloc[0]
    assert r["shot_outcome_observed"] == "saved" and r["type_check_basis"] == "saved"  # the goalkeeper: a save
    r = locate(_away_scene("C", (31.5, 0.8)), [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]
    assert not r["shot_blocked_at_kick"] and r["type_check"] == "not_confirmed" and r["type_check_reason"] == "away_from_goal"  # 1.4 m


# ---------------------------------------------------------------------------
# R-WOODWORK (v4.2): the ball's path, and the decision against the players
# ---------------------------------------------------------------------------

def _post_scene(player_dist=None, z=0.5):
    """The ball runs along y = 3.76 (0.10 m outside the post at y = 3.66) at 30 m/s: samples at x = 49.5 (frame 220) and
    50.7 (frame 221), 0.51 m and 0.71 m from the post, the path between them 0.10 m; it then bounces back into play.
    A defender ``D`` stands ``player_dist`` m from the ball's position at frame 220 (on the pitch side)."""
    knots = [(0, 30.0, 3.76), (200, 30.0, 3.76), (220, 49.5, 3.76), (221, 50.7, 3.76), (240, 44.0, 12.0), (N - 1, 44.0, 12.0)]
    players = {"B": (30, 3.4, "home"), "G": (50, -3, "away")}
    if player_dist is not None:
        players["D"] = (49.5, 3.76 - player_dist, "away")
    return period(knots, players, bz=np.full(N, z))


def test_r_woodwork_v4_2_the_path_between_two_samples_passes_the_post():
    sp = _post_scene()
    ww = woodwork_frames(sp, touch_table(sp, ball_kinematics(sp, CFG, L), CFG), L, CFG)
    assert len(ww) >= 1 and ww[0]["part"] == "post" and ww[0]["distance_m"] <= 0.11 and ww[0]["decided_by"] == "clear_of_players"


def test_r_woodwork_v4_2_a_bounce_between_two_samples_is_reconstructed_at_the_post():
    """The ball runs at 1 m per frame along y = 3.76 and bounces off the post (x = 50, y = 3.66) between two samples
    (contact at x = 49.89, frame 220.39); no sample and no straight segment between samples comes within 0.5 m of the
    post, but the incoming and outgoing lines meet 0.15 m from it (the SYN-001 post at 58' in v4)."""
    knots = [(0, 27.5, 3.76), (198, 27.5, 3.76), (220.39, 49.89, 3.76), (240, 38.12, 19.45), (N - 1, 38.12, 19.45)]
    sp = period(knots, {"B": (27.2, 3.5, "home"), "G": (50, -3, "away")}, bz=np.full(N, 0.5))
    d_samples = np.hypot(sp.bx[215:226] - 50.0, sp.by[215:226] - 3.66)
    assert d_samples.min() > 0.5
    ww = woodwork_frames(sp, touch_table(sp, ball_kinematics(sp, CFG, L), CFG), L, CFG)
    assert len(ww) == 1 and ww[0]["part"] == "post" and ww[0]["distance_m"] <= 0.2


def test_r_woodwork_v4_2_the_net_is_not_the_woodwork():
    """Decision (E-1): a ball that enters the goal just inside the post (y = 3.5 at the line) and stops in the net
    0.4 m behind the line gives an |a| peak with the ball within 0.5 m of the post and nobody near: without the rule it
    would be woodwork; with the ball in the net within 0.2 s of the peak it is never a woodwork contact."""
    st = np.ones(N)
    st[226:] = 0
    knots = [(0, 30.3, 3.0), (200, 30.3, 3.0), (220, 50.0, 3.5), (221, 50.4, 3.5), (N - 1, 50.4, 3.5)]
    sp = period(knots, {"B": (30.0, 2.7, "home"), "G": (50, -3, "away")}, bz=np.full(N, 0.5), status=st)
    bk = ball_kinematics(sp, CFG, L)
    tt = touch_table(sp, bk, CFG)
    assert [c["index"] for c in goal_crossings(sp, L, CFG, bk)] == [220]
    assert len(woodwork_frames(sp, tt, L, CFG, bk, crossing_frames=[])) >= 1  # what v4.2 found before the decision
    assert woodwork_frames(sp, tt, L, CFG, bk) == []
    assert locate(sp, [shot("B", "T-H", 200, "SuccessfulShot")]).attrs["sync_log"]["woodwork_contacts"] == 0


def test_r_woodwork_v4_2_a_player_close_to_the_ball_takes_the_contact_unless_the_ball_is_much_closer_to_the_frame():
    def ww(d):
        sp = _post_scene(player_dist=d)
        return woodwork_frames(sp, touch_table(sp, ball_kinematics(sp, CFG, L), CFG), L, CFG)

    assert ww(0.5) == []  # (i) a player within 1 m; (ii) 0.5 - 0.1 = 0.4 m, not more than 0.5 m: the player's touch
    hit = ww(0.7)  # (ii) 0.7 - 0.1 = 0.6 m: closer to the post than to any player by more than 0.5 m
    assert len(hit) >= 1 and hit[0]["decided_by"] == "closer_to_frame"
    hit = ww(1.2)  # (i) the path within 0.3 m of the post and every player at least 1 m away
    assert len(hit) >= 1 and hit[0]["decided_by"] == "clear_of_players"


def test_r_woodwork_v4_2_crossbar_with_a_3d_reach_for_high_balls():
    """A ball at 2.70 m passes 0.26 m above the crossbar and bounces back; the keeper stands 0.70 m away horizontally.
    In 2-D the keeper would be only 0.44 m further than the bar; in 3-D from 2.3 m above his position he is 0.81 m away."""
    def scene(z):
        knots = [(0, 30.0, 0.0), (200, 30.0, 0.0), (220, 49.5, 0.0), (221, 50.7, 0.0), (240, 40.0, 0.0), (N - 1, 40.0, 0.0)]
        return period(knots, {"B": (30, -0.4, "home"), "K": (49.5, 0.70, "away")}, bz=np.full(N, z))

    sp = scene(2.70)
    hit = woodwork_frames(sp, touch_table(sp, ball_kinematics(sp, CFG, L), CFG), L, CFG)
    assert len(hit) >= 1 and hit[0]["part"] == "crossbar" and hit[0]["decided_by"] == "closer_to_frame"
    sp = scene(2.20)  # 0.24 m below the bar, below the reach height: 2-D distance, 0.70 - 0.24 = 0.46 m: the keeper's touch
    assert woodwork_frames(sp, touch_table(sp, ball_kinematics(sp, CFG, L), CFG), L, CFG) == []


# ---------------------------------------------------------------------------
# R-LATE-ANNOTATION (v4.2) and R-DUEL / R-ALIGN (v4.2)
# ---------------------------------------------------------------------------

def _late_scene(dead_after: bool):
    """A passes at frame 100 to B (stopped at frame 124); with ``dead_after`` the ball is dead from frame 150 to 500."""
    st = np.ones(N)
    if dead_after:
        st[150:500] = 0
    return period([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)],
                  {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")}, status=st)


def test_r_late_annotation_v4_2_mostly_dead_window_extends_the_search_back_to_the_previous_live_phase():
    rows = [("Play", {"Player": "A", "Team": "T-H"}, at(300))]  # corrected time 8 s after the kick
    r = locate(_late_scene(dead_after=True), rows).iloc[0]
    assert r["sync_method"] == "kick_signature" and abs(int(r["sync_frame"]) - (N0 + 100)) <= 1 and bool(r["sync_window_extended"])
    assert locate(_late_scene(dead_after=True), rows).attrs["sync_log"]["late_annotation_windows"] == 1
    r = locate(_late_scene(dead_after=False), rows).iloc[0]  # a live window: no extension, the kick is out of reach
    assert r["sync_method"] == "no_signature_in_window" and r["sync_window_extended"] != True  # noqa: E712


def test_r_duel_r_align_v4_2_aerial_duel_won_with_a_shot_may_share_the_shots_frame():
    rows = [(0, "duel", 5, 0.0, ["duel_min_distance"]), (1, "shot", 5, 0.0, ["aerial_signature"])]
    sh = {"kick": True, "cands": [(300, 0.9)], "sep_after": 2}
    duel = {"kick": False, "cands": [(301, 0.9)], "shot_duel_winner": 5}  # located one frame after the header
    assert None in align([dict(duel), dict(sh)], sep=5)  # v4: the order drops one of them
    assert align(mark_duel_shot_pairs([dict(duel), dict(sh)], rows), sep=5) == [0, 0]  # v4.2: both located
    before = dict(duel, cands=[(299, 0.9)])  # located one frame before the header: both orders are allowed
    assert align(mark_duel_shot_pairs([before, dict(sh)], rows), sep=5) == [0, 0]
    other = mark_duel_shot_pairs([dict(duel, shot_duel_winner=7), dict(sh)], rows)  # won by another player: unchanged
    assert "order_free_next" not in other[0] and None in align(other, sep=5)
    # The duel still has to follow the items before it.
    prev = {"kick": True, "cands": [(305, 2.0)]}
    rows3 = [(9, None, None, None, [])] + rows
    assert align(mark_duel_shot_pairs([prev, dict(duel), dict(sh)], rows3), sep=5)[1] is None
