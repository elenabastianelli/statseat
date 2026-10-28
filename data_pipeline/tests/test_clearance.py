"""R-CLEARANCE (guidelines v4, section 9.9): a located clearance is confirmed with at least 4 of 7 conditions.

Base scene (home attacks +x, own goal at x = -50): the home centre back D at (-40, 0) clears the ball at frame 200
at 25 m/s (90 km/h) along +x to the opponent E at (-14.6, 0.3), who stops it at frame 225. Base conditions:
C1 yes (no earlier touch), C2 yes (90 km/h), C3 yes (further from the own goal, no team-mate near the end),
C4 no (nobody near the ball at the kick), C5 no (not a header), C6 yes (own half), C7 yes (opponent touch at 1 s).
"""

import numpy as np

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.outcomes import clearance_check, clearance_conditions, free_flight
from insidethegame_data.touches import ball_kinematics, touch_table

from .scenes import FS, L, N, locate, period


def scene(speed=25.0, dribble=False, mate_near_end=False, crowd=False, header=False, box_opponents=0, x0=-40.0, receiver="away", end_after_s=None):
    k = 200
    xs = x0 + 0.3
    dist = 24.7
    f_end = k + int(round(dist / speed * FS)) if end_after_s is None else k + int(round(end_after_s * FS))
    x_end = xs + dist if end_after_s is None else xs + speed * end_after_s
    knots = [(0, xs, 0)]
    if dribble:  # D pushes the ball 1.6 m forward 0.9 s before the clearance (6.4 m/s for 0.25 s) and catches it up again
        knots += [(172, xs, 0), (178, xs + 1.6, 0), (k, xs + 1.6, 0)]
        xs += 1.6
    knots += [(k, xs, 0), (f_end, x_end, 0), (N - 1, x_end, 0)]
    players = {"D": (x0, 0, "home"), "E": (x_end + 0.4, 0.3, receiver),
               "G": (-49, 0, "home"), "K": (49, 0, "away")}
    if mate_near_end:
        players["T"] = (x_end + 1.0, 3.0, "home")
    if crowd:
        players.update({"X1": (xs + 1.0, 1.5, "away"), "X2": (xs - 1.0, -1.5, "away"), "X3": (xs, 2.0, "home")})
    for i in range(box_opponents):
        players[f"O{i}"] = (-45.0 + i, 8.0 + 2 * i, "away")
    bz = None
    if header:  # a headed clearance: the ball arrives in the air at 4 m/s and leaves at 25 m/s
        bz = np.full(N, 1.8)
    sp = period(knots, players, bz=bz, groups={"D": "CB"})
    if dribble:
        sp.px[180:, list(sp.pids).index("D")] = x0 + 1.6  # D follows the ball
    return sp, k


def conditions(sp, k, aerial=False):
    tt = touch_table(sp, ball_kinematics(sp, CFG), CFG)
    col = sp.pids.index("D")
    fl = free_flight(sp, tt, k, col, CFG, shot=False)
    return clearance_conditions(sp, tt, k, col, fl, aerial, "CB", L, CFG)


def test_r_clearance_base_scene_conditions():
    sp, k = scene()
    c = conditions(sp, k)
    assert c == {"C1": True, "C2": True, "C3": True, "C4": False, "C5": False, "C6": True, "C7": True}
    assert clearance_check(c, CFG) == ("confirmed", 5)


def test_r_clearance_exactly_4_conditions_is_confirmed_and_3_is_not():
    sp, k = scene(speed=10.0)  # C2 fails (36 km/h, low ball)
    c = conditions(sp, k)
    assert c["C2"] is False and clearance_check(c, CFG) == ("confirmed", 4)
    sp, k = scene(speed=10.0, mate_near_end=True)  # C2 and C3 fail
    c = conditions(sp, k)
    assert c["C2"] is False and c["C3"] is False and clearance_check(c, CFG) == ("not_confirmed", 3)


def test_r_clearance_located_event_lists_the_conditions_met():
    sp, k = scene(speed=10.0, mate_near_end=True)
    r = locate(sp, [("OtherBallAction", {"Player": "D", "Team": "T-H", "DefensiveClearance": "true"}, k - 2 * FS)]).iloc[0]
    assert r["event_kind"] == "clearance" and r["type_check"] == "not_confirmed" and r["clearance_score"] == 3
    assert r["type_check_reason"] == "3 of 7 conditions (C1, C6, C7)"


def test_r_clearance_c1_short_possession():
    assert conditions(*scene())["C1"] is True
    assert conditions(*scene(dribble=True))["C1"] is False


def test_r_clearance_c2_powerful_kick_or_high_ball():
    assert conditions(*scene(speed=25.0))["C2"] is True
    assert conditions(*scene(speed=10.0))["C2"] is False
    sp, k = scene(speed=10.0)
    sp.bz = np.where((np.arange(N) > k) & (np.arange(N) < k + 40), 3.5, 0.11)  # a high ball in the free flight
    assert conditions(sp, k)["C2"] is True


def test_r_clearance_c3_away_from_the_own_goal_and_no_teammate_near():
    assert conditions(*scene())["C3"] is True
    assert conditions(*scene(mate_near_end=True))["C3"] is False
    sp, k = scene()
    sp.bx[k:] = sp.bx[k] - (sp.bx[k:] - sp.bx[k])  # the same kick towards the own goal
    assert conditions(sp, k)["C3"] is False


def test_r_clearance_c4_v4_1_an_opponent_within_4_m_of_the_ball_at_the_kick():
    assert conditions(*scene())["C4"] is False
    assert conditions(*scene(crowd=True))["C4"] is True
    sp, k = scene()
    sp.pids.append("Q")  # one opponent 3.5 m from the ball at the kick (v4.1: at least one opponent within 4 m)
    sp.team_side.append("away")
    sp.px = np.column_stack([sp.px, np.full(N, sp.bx[k] - 0.5)])
    sp.py = np.column_stack([sp.py, np.full(N, 3.46)])
    assert conditions(sp, k)["C4"] is True
    sp.py[:, -1] = 4.5  # 4.53 m away: too far
    assert conditions(sp, k)["C4"] is False
    sp.team_side[-1] = "home"  # a team-mate close to the ball does not count
    sp.py[:, -1] = 1.0
    assert conditions(sp, k)["C4"] is False


def test_r_clearance_c5_header_by_a_defender_in_his_own_crowded_box():
    sp, k = scene(box_opponents=3)
    assert conditions(sp, k, aerial=False)["C5"] is False  # not a header
    assert conditions(sp, k, aerial=True)["C5"] is True
    sp, k = scene(box_opponents=2)
    assert conditions(sp, k, aerial=True)["C5"] is False  # only 2 opponents in the box


def test_r_clearance_c6_start_from_the_own_half():
    assert conditions(*scene(x0=-40.0))["C6"] is True
    assert conditions(*scene(x0=5.0))["C6"] is False


def test_r_clearance_c7_possession_change_or_ball_out_soon_after():
    assert conditions(*scene())["C7"] is True
    assert conditions(*scene(receiver="home"))["C7"] is False  # a team-mate receives it
    assert conditions(*scene(speed=6.0, end_after_s=4.0))["C7"] is False  # the opponent touches it after 4 s


def test_r_highlight_anchored_on_the_outcome_and_walked_back_to_the_action_start():
    from insidethegame_data.highlights import action_start, highlights
    from insidethegame_data.config import DEFAULT_CONFIG as C

    n = 1000
    st = np.ones(n, dtype=int)
    st[:100] = 0  # restart (dead->live) at frame 100
    poss = np.full(n, 2)  # the opponents have the ball ...
    poss[400:] = 1  # ... until home wins it at frame 400
    anchor = 600  # a save of the home team's shot
    assert action_start(st, None, anchor, "home") == 100  # restart only
    assert action_start(st, poss, anchor, "home") == 400  # the later of restart and possession change
    h = highlights([{"kind": "save", "index": anchor, "attack_side": "home", "period": 1}], st, poss, 25.0, C)[0]
    assert h["start_index"] == 400 and h["end_index"] == anchor + 75 and h["start_to_anchor_s"] == 8.0
    assert highlights([{"kind": "goal", "index": 990, "attack_side": "home", "period": 1}], st, poss, 25.0, C)[0]["end_index"] == 999
