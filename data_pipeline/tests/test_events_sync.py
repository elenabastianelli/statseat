"""Events located from trajectories (guidelines v4, section 9). Tiny synthetic scenes with hand-computed answers.

Scenes: see ``tests/scenes.py``. Players: A, B (home), C (away), G (away goalkeeper). The home team attacks +x.
Annotated times are 2 s early; the period's event clock offset (+2 s) corrects them.
"""

import json

import numpy as np
import pandas as pd

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.events_sync import align, set_piece_shot_label, summarise
from insidethegame_data.goals import goal_crossings
from insidethegame_data.touches import ball_kinematics, touch_table

from .scenes import FS, L, N, N0, locate, period, restart, shot

HOME_PLAYERS = {"A": (0, 0, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, 10, "away")}


# ---------------------------------------------------------------------------
# R-TOUCH, R-PASS-KICK, R-PASS-OUTCOME, R-SYNC-LOCATION
# ---------------------------------------------------------------------------

def test_r_touch_pass_to_teammate_located_on_the_kick_with_simulated_delay():
    # A at (0, 0) kicks at frame 100 (15 m/s) to B at (15.5, 0), who stops the ball at frame 124.
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)],
                {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")})
    # Annotated 2 s early plus 0.3 s of noise (offset +2 s): corrected time = frame 100 - 7.5 frames.
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS - 7.5)]).iloc[0]
    assert r["sync_method"] == "kick_signature" and abs(int(r["sync_frame"]) - (N0 + 100)) <= 1
    assert r["type_check"] == "confirmed" and r["type_check_basis"] == "kick" and r["pass_outcome"] == "teammate" and r["next_touch_object_id"] == "B"
    # R-SYNC-LOCATION: from the synced frame: ball at x = 0.3 -> middle third, 49.7 m from the attacked goal.
    assert r["sync_zone"] == "middle_third" and r["sync_penalty_area"] == "none"
    assert abs(r["sync_goal_distance_m"] - 49.7) < 0.05 and abs(r["pass_length_m"] - 14.7) < 0.3


def test_r_pass_kick_intercepted_pass_is_confirmed_with_outcome_opponent():
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (113, 8.0, 0), (N - 1, 8.0, 0)],
                {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (8.4, 0.5, "away"), "G": (48, 0, "away")})
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS)]).iloc[0]
    assert r["type_check"] == "confirmed" and r["type_check_basis"] == "kick" and r["pass_outcome"] == "opponent"


def test_r_pass_outcome_out_of_play_and_open_space():
    players = {"A": (0, 0, "home"), "B": (-30, 0, "home"), "C": (0, 25, "away"), "G": (48, -20, "away")}
    st = np.ones(N)
    st[140:] = 0  # the ball goes dead 1.6 s after the pass, untouched
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (140, 12.3, 12.0), (N - 1, 12.3, 12.0)], players, status=st)
    assert locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS)]).iloc[0]["pass_outcome"] == "out_of_play"
    # A long ball into space that nobody touches for 10 s while live (it rolls slowly on).
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (125, 10.3, 5.0), (N - 1, 20.0, 9.0)], players)
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS)]).iloc[0]
    assert r["pass_outcome"] == "open_space" and r["type_check"] == "confirmed"


def test_r_touch_dribble_touch_is_skipped_and_the_pass_is_located():
    # A touches the ball 1.5 m ahead at frame 100, runs onto it (touch at 140), passes to B at frame 140.
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (110, 1.8, 0), (140, 1.8, 0), (160, 13.8, 0), (N - 1, 13.8, 0)],
                {"A": (0, 0, "home"), "B": (14.2, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")})
    sp.px[105:, 0] = 1.5  # A follows the ball
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 120 - 2 * FS)]).iloc[0]
    assert abs(int(r["sync_frame"]) - (N0 + 140)) <= 1 and r["next_touch_object_id"] == "B"


def test_r_touch_event_without_any_signature_is_not_located_and_not_confirmed():
    sp = period([(0, 0.3, 0), (N - 1, 0.3, 0)], {"A": (20, 20, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")})
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100)]).iloc[0]
    assert r["sync_method"] == "no_signature_in_window" and r["type_check"] == "not_confirmed" and r["type_check_reason"] == "event not located"


# ---------------------------------------------------------------------------
# R-LIVE-ONLY
# ---------------------------------------------------------------------------

def test_r_live_only_dead_ball_peak_and_duel_minimum_are_not_used():
    st = np.ones(N)
    st[80:200] = 0  # the "kick" at frame 100 happens while the ball is dead
    sp = period([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)],
                {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")}, status=st)
    tt = touch_table(sp, ball_kinematics(sp, CFG), CFG)
    assert not np.isin(tt.frames, np.arange(80, 200)).any()  # no touch in dead ball
    r = locate(sp, [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS)]).iloc[0]
    assert r["sync_method"] == "no_signature_in_window"
    # A duel whose only minimum of max(d(winner, ball), d(loser, ball)) falls in dead ball is not located.
    sp = period([(0, 10, 10), (N - 1, 10, 10)], {"A": (0, 0, "home"), "C": (0, 0, "away"), "B": (-30, 0, "home"), "G": (48, 0, "away")}, status=st)
    i = np.arange(N)
    sp.px[:, 0], sp.py[:, 0] = 10 + (i - 150) * 0.1, 10.0
    sp.px[:, 1], sp.py[:, 1] = 10.0, 10 + (i - 150) * 0.1
    r = locate(sp, [("TacklingGame", {"Winner": "A", "Loser": "C", "WinnerTeam": "T-H"}, 150 - 2 * FS)]).iloc[0]
    assert r["sync_method"] == "no_signature_in_window"


# ---------------------------------------------------------------------------
# R-GOAL
# ---------------------------------------------------------------------------

def _goal_scene(after_status=None, bz=None, cross_y=0.5, deflect=False):
    """B shoots at frame 200 from (30.3, 0) at 25 m/s: over the line (x = 50) at frame 220, into the net at 222.
    The ball is dead from frame 223, back on the centre spot, and play restarts at frame 400."""
    st = np.ones(N)
    st[223:400] = 0
    if after_status is not None:
        st = after_status
    knots = [(0, 30.3, 0), (200, 30.3, 0)]
    if deflect:  # an opponent's fingertip at frame 213 changes the direction a little
        knots += [(213, 46.0, 0.0)]
    knots += [(222, 52.0, cross_y), (390, 52.0, cross_y), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    players = {"A": (0, 0, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, 10, "away")}
    if deflect:
        players["G"] = (46.5, 1.0, "away")
    return period(knots, players, bz=bz, status=st)


def test_r_goal_crossing_followed_by_a_kickoff_is_a_goal_and_the_shot_is_found_backwards():
    sp = _goal_scene()
    ev = locate(sp, [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"]
    assert [c["goal_check"] for c in gc] == ["goal"] and gc[0]["next_restart_type"] == "KickOff" and gc[0]["kicker_id"] == "B"
    r = ev.iloc[0]
    assert r["sync_method"] == "goal_backwards" and abs(int(r["sync_frame"]) - (N0 + 200)) <= 1
    assert r["type_check"] == "confirmed" and r["type_check_basis"] == "goal" and r["shot_outcome_observed"] == "goal" and r["shooter_mismatch"] is not None and not r["shooter_mismatch"]


def test_r_goal_crossing_followed_by_a_free_kick_is_a_disallowed_goal():
    ev = locate(_goal_scene(), [shot("B", "T-H", 200, "SavedShot"), restart("FreeKick", "T-A", 400)])
    assert [c["goal_check"] for c in ev.attrs["goal_crossings"]] == ["disallowed_goal"]
    r = ev.iloc[0]
    assert r["shot_outcome_observed"] == "disallowed_goal" and r["type_check_basis"] == "disallowed_goal"


def test_r_goal_ball_over_the_bar_is_not_a_goal_even_if_a_kickoff_follows():
    bz = np.full(N, 0.11)
    bz[200:223] = np.linspace(0.2, 4.0, 23)  # crosses the line at about 3.7 m
    ev = locate(_goal_scene(bz=bz), [shot("B", "T-H", 200, "ShotWide"), restart("KickOff", "T-A", 400)])
    assert ev.attrs["goal_crossings"] == []
    assert ev.iloc[0]["shot_outcome_observed"] != "goal"


def test_r_goal_crossing_whose_first_sample_beyond_the_line_is_dead_is_found():
    st = np.ones(N)
    st[220:400] = 0  # the first frame beyond the line is already dead
    sp = _goal_scene(after_status=st)
    rows = goal_crossings(sp, L, CFG)
    assert len(rows) == 1 and rows[0]["index"] == 220 and rows[0]["status_at_f"] == "dead"
    assert [c["goal_check"] for c in locate(sp, [restart("KickOff", "T-A", 400)]).attrs["goal_crossings"]] == ["goal"]


def test_r_goal_play_continues_for_more_than_5_s_is_not_a_goal():
    sp = _goal_scene(after_status=np.ones(N))  # never dead after the crossing
    ev = locate(sp, [shot("B", "T-H", 200, "SuccessfulShot")])
    assert [c["goal_check"] for c in ev.attrs["goal_crossings"]] == ["play_continued"]
    assert ev.iloc[0]["shot_outcome_observed"] != "goal"


def test_r_goal_shot_is_the_last_attacking_kick_with_an_opponent_deflection_in_between():
    sp = _goal_scene(deflect=True)
    ev = locate(sp, [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["kicker_id"] == "B" and abs(gc["kick_frame"] - (N0 + 200)) <= 1
    assert ev.iloc[0]["type_check_basis"] == "goal" and ev.iloc[0]["next_touch_object_id"] == "G"


def test_r_goal_without_an_attacking_kick_has_no_located_shot():
    # The ball rolls into the goal at x = +50 from a restart taken by the away team, and nobody of the attacking
    # (home) team kicked it (an own goal): there is no goal shot.
    st = np.ones(N)
    st[:100] = 0
    st[223:400] = 0
    sp = period([(0, 30.3, 0), (100, 30.3, 0), (222, 52.0, 0.5), (390, 52, 0.5), (391, 0, 0), (N - 1, 0, 0)],
                {"A": (0, 0, "home"), "B": (0, 20, "home"), "C": (31, 0.5, "away"), "G": (50, 10, "away")}, status=st)
    ev = locate(sp, [restart("FreeKick", "T-A", 100), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["kick_frame"] is None and gc["shot_event_id"] is None


def _set_piece_scene(spot_x: float, spot_y: float, taker: str = "P"):
    """The home taker P kicks a dead ball from (spot_x, spot_y) when play restarts at frame 100; it crosses the line
    x = 50 near y = 0.4 before frame 122, the ball is dead from frame 123 and a kickoff restarts play at frame 400."""
    st = np.ones(N)
    st[:100] = 0
    st[123:400] = 0
    knots = [(0, spot_x, spot_y), (100, spot_x, spot_y), (122, 52.0, 0.5), (390, 52.0, 0.5), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    players = {taker: (spot_x - 0.5, spot_y, "home"), "A": (0, 0, "home"), "C": (20, 25, "away"), "G": (50, 4.5, "away")}
    return period(knots, players, status=st)


def test_r_goal_penalty_goal_is_an_annotated_goal_matched_at_the_restart_kick():
    # DFL: a penalty is a restart whose nested sub-type is the shot; the taker is ProspectiveTaker (no Player).
    ev = locate(_set_piece_scene(39.0, 0.0),
                [restart("Penalty", "T-H", 100, "ShotAtGoal/SuccessfulShot", ProspectiveTaker="P"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"]
    assert [c["goal_check"] for c in gc] == ["goal"]
    # R-GOAL step 4: the kick of a penalty is the restart's dead->live frame; step 5: matched to the penalty event.
    assert abs(gc[0]["kick_frame"] - (N0 + 100)) <= 1 and gc[0]["kicker_id"] == "P"
    assert gc[0]["shot_event_id"] == "E0" and gc[0]["annotated_label"] == "goal" and gc[0]["shooter_mismatch"] is False
    pen = ev.iloc[0]
    # Still a restart located at its transition, never an open-play shot (no R-SHOT-* checks, no xG).
    assert pen["sync_method"] == "dead_live_transition" and pen["type_check"] == "not_applicable" and pen["event_kind"] is None
    assert pen["shot_outcome_label"] == "goal" and pen["goal_check"] == "goal" and pen["shooter_mismatch"] is False
    s = summarise(ev, CFG)
    assert s["goals"]["annotated_goals"] == 1 and s["goals"]["annotated_goals_by_event_type"] == {"Penalty": 1}
    assert s["goals"]["annotated_goals_without_goal_crossing"] == [] and s["goals"]["goal_crossings_without_annotated_goal"] == 0
    assert s["goals"]["crossings"][0]["shot_event_type"] == "Penalty"
    assert s["shots"]["annotated"] == 0  # the open-play shot statistics are unchanged


def test_r_goal_direct_free_kick_goal_without_an_annotated_taker_is_matched_and_the_mismatch_is_not_judged():
    # DFL keeps the free-kick taker on the nested shot element only: the event has no Player, so no shooter_mismatch.
    ev = locate(_set_piece_scene(25.0, 5.0), [restart("FreeKick", "T-H", 100, "ShotAtGoal/SuccessfulShot"), restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"][0]
    assert gc["goal_check"] == "goal" and gc["shot_event_id"] == "E0" and gc["shooter_mismatch"] is None
    assert abs(gc["kick_frame"] - (N0 + 100)) <= 1 and gc["kicker_id"] == "P"
    g = summarise(ev, CFG)["goals"]
    assert g["annotated_goals"] == 1 and g["annotated_goals_by_event_type"] == {"FreeKick": 1} and g["shooter_mismatches"] == 0


def test_r_goal_rebound_after_a_saved_penalty_is_matched_to_the_open_play_goal_shot():
    # P's penalty (frame 100) is parried by G at frame 117; B shoots the rebound at frame 150 into the goal (crossing ~162).
    st = np.ones(N)
    st[:100] = 0
    st[170:400] = 0
    knots = [(0, 39, 0), (100, 39, 0), (117, 49, 0.4), (140, 44, 2), (150, 44, 2), (165, 52, 1), (390, 52, 1), (391, 0, 0), (400, 0, 0), (N - 1, -12, 0)]
    sp = period(knots, {"P": (38.5, 0, "home"), "B": (44, 2.4, "home"), "C": (20, 25, "away"), "G": (49.3, 0.4, "away")}, status=st)
    ev = locate(sp, [restart("Penalty", "T-H", 100, "ShotAtGoal/SavedShot", ProspectiveTaker="P"), shot("B", "T-H", 150, "SuccessfulShot"),
                     restart("KickOff", "T-A", 400)])
    gc = ev.attrs["goal_crossings"]
    assert [c["goal_check"] for c in gc] == ["goal"] and gc[0]["kicker_id"] == "B" and gc[0]["shot_event_id"] == "E1"
    assert ev.iloc[0]["shot_outcome_label"] == "saved" and pd.isna(ev.iloc[0]["goal_check"])  # the saved penalty is not the goal
    g = summarise(ev, CFG)["goals"]
    assert g["annotated_goals"] == 1 and g["annotated_goals_by_event_type"] == {"ShotAtGoal": 1}


def test_r_goal_set_piece_shot_label_only_for_restarts_that_are_shots():
    assert set_piece_shot_label("Penalty", "ShotAtGoal/SuccessfulShot", CFG) == "goal"
    assert set_piece_shot_label("FreeKick", "ShotAtGoal/SavedShot", CFG) == "saved"
    assert set_piece_shot_label("FreeKick", "Play/Pass", CFG) is None  # an ordinary free kick
    assert set_piece_shot_label("ShotAtGoal", "SuccessfulShot", CFG) is None  # open play: not a set piece
    assert set_piece_shot_label("Penalty", None, CFG) is None


# ---------------------------------------------------------------------------
# R-AERIAL, R-SHOT-HEADER, R-SHOT-SOFT, R-SHOT-SPLIT
# ---------------------------------------------------------------------------

def _header_scene(z: float, turn_deg: float = 30.0):
    """A ball at 4 m/s changes direction by ``turn_deg`` at frame 300 next to B (a 30 deg turn gives an |a| peak of
    about 5 m/s2, below the ground threshold of 8), then the goalkeeper G stops it at frame 337."""
    v, th = 4.0, np.radians(turn_deg)
    p1 = np.array([40.0, 0.0])
    p0 = p1 - v * 2.0 * np.array([np.cos(th), np.sin(th)])
    end = 40.0 + v * 37 / FS
    return period([(0, *p0), (250, *p0), (300, *p1), (337, end, 0), (N - 1, end, 0)],
                  {"A": (0, 0, "home"), "B": (40.3, 0.3, "home"), "C": (20, 25, "away"), "G": (end + 0.5, 0.5, "away")}, bz=np.full(N, z))


def test_r_aerial_r_shot_header_header_shot_is_located_and_saved():
    sp = _header_scene(1.7)
    bk = ball_kinematics(sp, CFG)
    assert 4.0 <= bk["acc"][300] < CFG.touch_acc_min_ms2  # softer than a ground touch
    r = locate(sp, [shot("B", "T-H", 300, "SavedShot", TypeOfShot="head")]).iloc[0]
    assert r["sync_method"] == "aerial_signature" and int(r["sync_frame"]) == N0 + 300 and bool(r["aerial"])
    assert r["shot_outcome_observed"] == "saved" and r["type_check"] == "confirmed"


def test_r_aerial_at_1_55_m_is_an_aerial_touch_for_a_pass_but_not_a_header_shot():
    sp = _header_scene(1.55)
    assert locate(sp, [("Play", {"Player": "B", "Team": "T-H"}, 300 - 2 * FS)]).iloc[0]["sync_method"] == "aerial_signature"
    assert locate(sp, [shot("B", "T-H", 300, "SavedShot")]).iloc[0]["sync_method"] == "no_signature_in_window"


def test_r_aerial_without_a_change_of_direction_there_is_no_aerial_touch():
    sp = _header_scene(1.7, turn_deg=0.0)
    tt = touch_table(sp, ball_kinematics(sp, CFG), CFG)
    assert not tt.aerial.any()


def _soft_scene(v_out: float):
    """A ball at 6 m/s is redirected by B at frame 300 (24 deg turn, |a| peak below 8 m/s2) towards the goal at ``v_out``."""
    v, th = 6.0, np.radians(24)
    p1 = np.array([40.0, 0.0])
    p0 = p1 - v * 2.0 * np.array([np.cos(th), np.sin(th)])
    end = p1 + v_out * 2.0 * np.array([1.0, 0.0])
    return period([(0, *p0), (250, *p0), (300, *p1), (350, *end), (N - 1, *end)],
                  {"A": (0, 0, "home"), "B": (40.0, 0.3, "home"), "C": (20, 25, "away"), "G": (49, 10, "away")}, bz=np.full(N, 0.11))


def test_r_shot_soft_placed_shot_is_located_when_the_distance_grows_by_more_than_1_m():
    sp = _soft_scene(4.6)
    bk = ball_kinematics(sp, CFG)
    assert np.nanmax(bk["acc"][290:310]) < CFG.touch_acc_min_ms2  # no ground touch at the kick
    d = np.hypot(sp.bx - 40.0, sp.by - 0.3)
    assert 1.1 < d[308] - d[300] < 1.3  # grows by about 1.2 m in 0.3 s
    r = locate(sp, [shot("B", "T-H", 300, "ShotWide")]).iloc[0]
    assert r["sync_method"] == "soft_kick_signature" and int(r["sync_frame"]) == N0 + 300


def test_r_shot_soft_not_located_with_0_9_m_or_after_a_dribble():
    sp = _soft_scene(3.6)
    d = np.hypot(sp.bx - 40.0, sp.by - 0.3)
    assert 0.85 < np.max(d[301:309]) - d[300] < 0.95
    assert locate(sp, [shot("B", "T-H", 300, "ShotWide")]).iloc[0]["sync_method"] == "no_signature_in_window"
    sp = _soft_scene(4.6)
    sp.px[301:, 1] = sp.bx[301:] - 0.4  # B runs with the ball: the next touch is his own (a dribble)
    sp.py[301:, 1] = 0.0
    sp.bx[340:], sp.by[340:] = sp.bx[340], 0.0  # ... and stops it again
    assert locate(sp, [shot("B", "T-H", 300, "ShotWide")]).iloc[0]["sync_method"] != "soft_kick_signature"


def test_r_shot_split_shot_blocked_two_frames_after_the_kick_is_located_with_outcome_blocked():
    # B kicks at frame 200 at 25 m/s; C, 2.6 m ahead, blocks at frame 202 and the ball bounces back.
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (202, 32.3, 0), (212, 31.3, 0), (N - 1, 31.3, 0)],
                {"A": (0, 0, "home"), "B": (30, 0, "home"), "C": (32.9, 0.3, "away"), "G": (49, 0, "away")})
    r = locate(sp, [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]
    assert r["sync_method"] == "kick_signature" and abs(int(r["sync_frame"]) - (N0 + 200)) <= 5
    assert r["next_touch_object_id"] == "C" and r["shot_outcome_observed"] == "blocked"


def test_r_shot_split_two_touches_two_frames_apart_in_a_pass_are_one_touch():
    # The same scene annotated as a pass: a touch 2 frames after the kick is not the pass's next touch.
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (202, 32.3, 0), (212, 31.3, 0), (N - 1, 31.3, 0)],
                {"A": (0, 0, "home"), "B": (30, 0, "home"), "C": (32.9, 0.3, "away"), "G": (49, 0, "away")})
    r = locate(sp, [("Play", {"Player": "B", "Team": "T-H"}, 200 - 2 * FS)]).iloc[0]
    assert r["next_touch_object_id"] != "C"


# ---------------------------------------------------------------------------
# R-SHOT-DIRECTION, R-SHOT-NOT-PASS, R-SHOT-POWER, R-SHOT-OUTCOME, R-WOODWORK, R-SHOT-CHECK
# ---------------------------------------------------------------------------

def test_r_shot_direction_within_6_m_beyond_a_post_passes_and_beyond_fails():
    from insidethegame_data.outcomes import shot_direction

    def line_to(y_line):
        # From (30, 0) along a straight line that reaches the goal line (x = 50) at y = y_line, 1 m per frame.
        sp = period([(0, 30.0, 0), (100, 30.0, 0), (120, 50.0, y_line), (N - 1, 50.0, y_line)], HOME_PLAYERS, with_speed=False)
        return shot_direction(sp, 100, 115, 1.0, L, CFG)

    assert line_to(3.66 + 5.9)["direction"] == "ok"
    assert line_to(-(3.66 + 6.1))["direction"] == "direction_outside_6m"
    sp = period([(0, 30.0, 0), (N - 1, 30.0, 0)], HOME_PLAYERS, with_speed=False)
    assert shot_direction(sp, 100, 101, 1.0, L, CFG)["direction"] == "direction_undetermined"  # 2 samples
    sp = period([(0, 30.0, 0), (100, 30.0, 0), (120, 20.0, 0), (N - 1, 20.0, 0)], HOME_PLAYERS, with_speed=False)
    assert shot_direction(sp, 100, 119, 1.0, L, CFG)["direction"] == "away_from_goal"


def _slow_shot(teammate_xy, saved=False, received=False):
    """B shoots slowly (10 m/s) from (30.3, 0) towards the goal; A stands at ``teammate_xy``; the ball goes out over the
    goal line 5 m from the centre (outside the post, within 6 m of it; dead), or the goalkeeper G stops it at x = 47.9
    (``saved``), or A, on the line of the kick, stops it at (39.6, 1.4) (``received``, about 5.8 m/s)."""
    st = np.ones(N)
    gk = (50, 15, "away")
    if saved:
        knots = [(0, 30.3, 0), (200, 30.3, 0), (244, 47.9, 0), (N - 1, 47.9, 0)]
        gk = (48.4, 0.3, "away")
    elif received:
        knots = [(0, 30.3, 0), (200, 30.3, 0), (240, 39.6, 1.4), (N - 1, 39.6, 1.4)]
    else:
        knots = [(0, 30.3, 0), (200, 30.3, 0), (250, 50.3, 5.0), (N - 1, 50.3, 5.0)]
        st[251:] = 0
    return period(knots, {"A": (*teammate_xy, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": gk}, status=st)


def test_r_shot_not_pass_teammate_who_receives_the_ball_makes_a_slow_shot_not_confirmed():
    # v4.1: A, ahead of the ball on the line of the kick, receives it (the next touch is his).
    r = locate(_slow_shot((40.0, 1.5), received=True), [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]
    assert r["next_touch_object_id"] == "A"
    assert bool(r["shot_directed_at_teammate"]) and r["type_check"] == "not_confirmed" and r["type_check_reason"] == "directed_at_teammate"


def test_r_shot_not_pass_v4_1_teammate_on_the_line_who_does_not_receive_is_ignored():
    # v4: a team-mate on the line made the shot not confirmed; v4.1: only if he receives it. Here the ball passes him (0.9 m)
    # and goes out within 6 m of the post: a confirmed wide shot.
    r = locate(_slow_shot((40.0, 1.5)), [shot("B", "T-H", 200, "ShotWide")]).iloc[0]
    assert not r["shot_directed_at_teammate"] and r["type_check"] == "confirmed" and r["type_check_basis"] == "wide"


def test_r_shot_power_v4_1_power_above_the_pass_p99_confirms_the_shot():
    # v4.1: power above the p99 of pass speeds overrides the team-mate check and confirms the shot on its own.
    r = locate(_slow_shot((40.0, 1.5), received=True), [shot("B", "T-H", 200, "BlockedShot")], pass_p99_kmh=15.0).iloc[0]
    assert bool(r["shot_power_evidence"]) and r["type_check"] == "confirmed" and r["type_check_basis"] == "shot_power"


def test_r_shot_power_v4_1_a_header_is_never_confirmed_by_power():
    # The header scene (ball at 1.7 m, 4 m/s = 14.4 km/h) with a power threshold of 10 km/h: the power is above it, but a
    # header is never confirmed by power; the goalkeeper's touch confirms it instead.
    r = locate(_header_scene(1.7), [shot("B", "T-H", 300, "SavedShot", TypeOfShot="head")], pass_p99_kmh=10.0).iloc[0]
    assert r["sync_method"] == "aerial_signature" and r["shot_power_kmh"] > 10.0
    assert bool(r["shot_header"]) and not r["shot_power_evidence"] and r["type_check_basis"] == "saved"


def test_r_shot_not_pass_teammate_behind_the_ball_is_ignored():
    r = locate(_slow_shot((25.0, 0.5)), [shot("B", "T-H", 200, "ShotWide")]).iloc[0]
    assert not r["shot_directed_at_teammate"] and r["type_check"] == "confirmed" and r["shot_outcome_observed"] == "wide"


def test_r_shot_outcome_a_slow_shot_that_is_saved_is_confirmed():
    r = locate(_slow_shot((0.0, 20.0), saved=True), [shot("B", "T-H", 200, "SavedShot")]).iloc[0]
    assert not r["shot_power_evidence"] and r["shot_outcome_observed"] == "saved" and r["type_check_basis"] == "saved"


def _block_at(seconds: float):
    """A slow shot (5 m/s) stopped by the opponent C at ``seconds`` after the kick."""
    k = 200
    f = k + int(round(seconds * FS))
    x_stop = 30.3 + 5.0 * seconds
    return period([(0, 30.3, 0), (k, 30.3, 0), (f, x_stop, 0), (N - 1, x_stop, 0)],
                  {"A": (0, 20, "home"), "B": (30, 0, "home"), "C": (x_stop + 0.5, 0.3, "away"), "G": (50, 10, "away")})


def test_r_shot_outcome_blocked_within_2_s_and_not_after():
    assert locate(_block_at(1.9), [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]["shot_outcome_observed"] == "blocked"
    r = locate(_block_at(2.1), [shot("B", "T-H", 200, "BlockedShot")]).iloc[0]
    assert r["shot_outcome_observed"] == "none" and r["type_check"] == "not_confirmed" and r["type_check_reason"] == "no_coherent_outcome"


def test_r_woodwork_contact_near_a_post_with_nobody_within_2_m():
    # B shoots at the near post (y = 3.66): the ball bounces off 0.3 m from the post, then rolls back into play.
    sp = period([(0, 30.3, 0), (200, 30.3, 0), (225, 49.7, 3.66 + 0.3), (260, 40.0, 10.0), (N - 1, 40.0, 10.0)],
                {"A": (0, 20, "home"), "B": (30, 0, "home"), "C": (20, 25, "away"), "G": (50, -3, "away")}, bz=np.full(N, 0.5))
    r = locate(sp, [shot("B", "T-H", 200, "ShotWoodWork")]).iloc[0]
    assert r["flight_end_kind"] == "woodwork" and r["shot_outcome_observed"] == "woodwork" and r["type_check_basis"] == "woodwork"


def test_r_shot_check_decision_table():
    from insidethegame_data.outcomes import shot_check

    assert shot_check(False, None, False, False, None) == ("not_confirmed", None, "event not located")
    assert shot_check(True, "goal", True, False, "away_from_goal") == ("confirmed", "goal", None)
    assert shot_check(True, "blocked", True, False, "away_from_goal", blocked_at_kick=True) == ("confirmed", "blocked", None)  # v4.2
    assert shot_check(True, "saved", True, False, "ok") == ("not_confirmed", None, "directed_at_teammate")
    assert shot_check(True, "saved", True, True, "ok") == ("confirmed", "shot_power", None)  # v4.1: power confirms
    assert shot_check(True, "none", False, True, "away_from_goal") == ("confirmed", "shot_power", None)
    assert shot_check(True, "wide", False, False, "direction_outside_6m") == ("not_confirmed", None, "direction_outside_6m")
    assert shot_check(True, "wide", False, False, "away_from_goal") == ("not_confirmed", None, "away_from_goal")
    assert shot_check(True, "none", False, False, "ok") == ("not_confirmed", None, "no_coherent_outcome")


# ---------------------------------------------------------------------------
# R-ALIGN, R-DUEL (kept from v3)
# ---------------------------------------------------------------------------

def test_r_align_keeps_the_order_and_never_shares_a_touch():
    items = [{"kick": True, "cands": [(10, 0.9)]}, {"kick": True, "cands": [(12, 0.95), (40, 0.5)]}]
    assert align(items, sep=5) == [0, 1]  # 0.9 + 0.5 beats 0.95 alone (12 is too close to 10)
    items = [{"kick": True, "cands": [(30, 0.8)]}, {"kick": True, "cands": [(20, 0.9)]}]
    assert align(items, sep=5) == [None, 0]  # out of order: keep the better one only
    items = [{"kick": True, "cands": [(50, 2.0)]}, {"kick": False, "cands": [(50, 0.4), (60, 0.3)]}]
    assert align(items, sep=5) == [0, 0]  # a duel may share the frame of a restart anchor


def test_r_align_r_shot_split_separation_after_a_shot_is_two_frames():
    items = [{"kick": True, "sep_after": 2, "cands": [(10, 0.9)]}, {"kick": True, "cands": [(12, 0.8)]}]
    assert align(items, sep=5) == [0, 0]  # a kick 2 frames after a shot is allowed
    items = [{"kick": True, "cands": [(10, 0.9)]}, {"kick": True, "cands": [(12, 0.8)]}]
    assert align(items, sep=5) == [0, None]  # after a pass, 0.2 s


def test_r_duel_located_at_the_minimum_of_the_larger_player_ball_distance():
    # Ball still at (10, 10). A crosses it along x (closest at frame 200), C along y (closest at frame 205).
    sp = period([(0, 10, 10), (N - 1, 10, 10)], {"A": (0, 0, "home"), "C": (0, 0, "away"), "B": (-30, 0, "home"), "G": (48, 0, "away")})
    i = np.arange(N)
    sp.px[:, 0], sp.py[:, 0] = 10 + (i - 200) * 0.1, 10.0
    sp.px[:, 1], sp.py[:, 1] = 10.0, 10 + (i - 205) * 0.1
    # max(dA, dC) = max(|i-200|, |i-205|) * 0.1 -> minimum 0.3 m at frames 202 and 203; the first is kept.
    r = locate(sp, [("TacklingGame", {"Winner": "A", "Loser": "C", "WinnerTeam": "T-H"}, 201 - 2 * FS)]).iloc[0]
    assert r["sync_method"] == "duel_min_distance" and int(r["sync_frame"]) == N0 + 202 and r["type_check"] == "not_applicable"


# ---------------------------------------------------------------------------
# R-LIVE-BUFFER
# ---------------------------------------------------------------------------

def _released(sp_full, n):
    """The first ``n`` frames of a scene, as the live engine sees them (period not complete)."""
    cut = period([(0, 0, 0), (N - 1, 0, 0)], {p: (0, 0, s) for p, s in zip(sp_full.pids, sp_full.team_side)}, n=n, complete=False, with_speed=False)
    for k in ("px", "py", "bx", "by", "bz", "bstatus", "ball_speed", "ball_valid"):
        setattr(cut, k, getattr(sp_full, k)[:n])
    return cut


def test_r_live_buffer_event_is_not_processed_before_its_frames():
    full = period([(0, 0.3, 0), (100, 0.3, 0), (124, 15.0, 0), (N - 1, 15.0, 0)],
                  {"A": (0, 0, "home"), "B": (15.5, 0, "home"), "C": (0, 25, "away"), "G": (48, 0, "away")})
    rows = [("Play", {"Player": "A", "Team": "T-H"}, 100 - 2 * FS)]
    early = locate(_released(full, 150), rows).iloc[0]  # corrected time + 5 s window = frame 225: not released yet
    assert early["sync_method"] == "pending" and early["type_check"] == "pending"
    later = locate(_released(full, 300), rows).iloc[0]
    assert later["sync_method"] == "kick_signature" and later["type_check"] == "confirmed"
    assert later["sync_frame"] == locate(full, rows).iloc[0]["sync_frame"]


def test_r_live_buffer_goal_stays_pending_until_the_next_restart_arrives():
    full = _goal_scene()
    ev = locate(_released(full, 390), [shot("B", "T-H", 200, "SuccessfulShot")])  # the kickoff (frame 400) has not arrived
    assert [c["goal_check"] for c in ev.attrs["goal_crossings"]] == ["pending"]
    assert ev.iloc[0]["type_check"] == "pending"
    ev = locate(full, [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    assert [c["goal_check"] for c in ev.attrs["goal_crossings"]] == ["goal"] and ev.iloc[0]["type_check_basis"] == "goal"


def test_annotated_events_are_never_changed():
    ev = locate(_goal_scene(), [shot("B", "T-H", 200, "SuccessfulShot"), restart("KickOff", "T-A", 400)])
    assert json.loads(ev.iloc[0]["attrs_json"])["Player"] == "B" and ev.iloc[0]["sub_types"] == "SuccessfulShot"
