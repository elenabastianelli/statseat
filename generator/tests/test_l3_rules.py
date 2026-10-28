"""Tests for the L-3 rules of the match generator: blocked shots, the scripted penalty written like DFL, and offside
judged from the positions (IFAB Law 11). Unit tests use tiny hand-made positions; one short scripted match checks the
penalty end to end."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest

from insidethegame_gen.match import events as E
from insidethegame_gen.match.api import REPO_ROOT, generate_match, generate_replica
from insidethegame_gen.match.calibration import EventDefaults
from insidethegame_gen.match.pitch import FRAME0, PENALTY_AREA_DEPTH_M, PENALTY_AREA_HALF_WIDTH_M, Pitch
from insidethegame_gen.match.script import ScriptError, load_script

SYN = REPO_ROOT / "synthetic" / "SYN-001"


class Stub:
    """The few Engine attributes the offside and lane helpers read: 2 v 3 players, home attacks +x."""
    att = E.Engine.att
    offside_line_att = E.Engine.offside_line_att
    offside_margin = E.Engine.offside_margin
    _hold_the_line = E.Engine._hold_the_line
    _shooting_lane_defender = E.Engine._shooting_lane_defender
    sample_shot_outcome = E.Engine.sample_shot_outcome

    def __init__(self, home_xy, away_xy, ball_xy, away_roles=None):
        self.P = np.array(list(home_xy) + list(away_xy), float)
        nh = len(home_xy)
        self.sides = ["home"] * nh + ["away"] * len(away_xy)
        self.side_slots = {"home": list(range(nh)), "away": list(range(nh, len(self.sides)))}
        roles = ["ST"] * nh + list(away_roles or (["GK"] + ["CB"] * (len(away_xy) - 1)))
        self.roster = SimpleNamespace(slot_role=roles)
        self.ball = np.array([ball_xy[0], ball_xy[1], 0.11])
        self.pitch = Pitch()
        self.dir = {"home": 1.0, "away": -1.0}

    def side_of(self, s):
        return self.sides[s]

    def goal_x(self, side):
        return self.dir[side] * self.pitch.hl


# --- offside from positions (IFAB Law 11) -------------------------------------------------------


def test_offside_position_needs_the_second_last_defender_and_the_ball():
    # away: goalkeeper at x = 50, last outfield defender at x = 30 -> the second-last opponent is at 30
    st = Stub(home_xy=[(32.0, 5.0), (28.0, -5.0)], away_xy=[(50.0, 0.0), (30.0, 2.0), (36.0, -8.0)], ball_xy=(10.0, 0.0))
    assert st.offside_line_att("home") == pytest.approx(36.0)  # second-last of 50, 36, 30
    st.P[3] = (20.0, 2.0)
    st.P[4] = (30.0, -8.0)
    assert st.offside_line_att("home") == pytest.approx(30.0)
    assert st.offside_margin(0) == pytest.approx(2.0)  # 32 beyond 30: offside position
    assert st.offside_margin(1) == pytest.approx(-2.0)  # 28: onside


def test_offside_position_never_behind_the_ball_or_in_the_own_half():
    st = Stub(home_xy=[(32.0, 5.0)], away_xy=[(50.0, 0.0), (20.0, 2.0)], ball_xy=(34.0, 0.0))
    assert st.offside_margin(0) < 0  # level with or behind the ball: not offside
    st2 = Stub(home_xy=[(-3.0, 5.0)], away_xy=[(50.0, 0.0), (-10.0, 2.0)], ball_xy=(-20.0, 0.0))
    assert st2.offside_margin(0) < 0  # in his own half: never offside


def test_offside_from_the_away_side_uses_its_attacking_coordinates():
    # away attacks -x: an away striker at x = -32 with the home second-last defender at x = -30 is offside
    st = Stub(home_xy=[(-50.0, 0.0), (-30.0, 1.0)], away_xy=[(-32.0, 4.0)], ball_xy=(-5.0, 0.0), away_roles=["ST"])
    st.roster.slot_role = ["GK", "CB", "ST"]
    assert st.offside_margin(2) == pytest.approx(2.0)


def test_attackers_hold_the_line_except_runs_that_start_too_early():
    st = Stub(home_xy=[(25.0, 5.0), (25.0, -5.0)], away_xy=[(50.0, 0.0), (30.0, 2.0)], ball_xy=(10.0, 0.0))
    T = np.zeros((3, 4, 2))
    T[:, 0] = (40.0, 5.0)  # wants to stand beyond the line
    T[:, 1] = (40.0, -5.0)
    ball = np.array([[10.0, 0.0], [12.0, 0.0], [14.0, 0.0]])
    st._hold_the_line(T, ball, "home", skip={1})
    assert np.all(T[:, 0, 0] == pytest.approx(30.0 - E.ONSIDE_MARGIN_M))  # held onside
    assert np.all(T[:, 1, 0] == 40.0)  # a mistimed run is not held


def test_offside_events_come_only_from_offside_positions(tmp_path):
    """In a replica every Offside event is a pass to a receiver beyond the line at the kick frame, and every such
    pass is whistled (no scripted sequence)."""
    s = generate_replica("SYN-001", 3, tmp_path, period_minutes=12.0)
    events = json.loads((tmp_path / "events.json").read_text())["events"]
    offsides = [e for e in events if e["event_type"] == "Offside"]
    log = s["offside_positions"]
    assert all(o["whistled"] and o["margin_m"] > 0 for o in log)
    assert len(offsides) == len(log)
    assert [e["player_id"] for e in offsides] == [o["receiver"] for o in log]


# --- blocked shots ----------------------------------------------------------------------------


def test_shooting_lane_defender_must_be_near_the_line_to_goal():
    st = Stub(home_xy=[(30.0, 0.0)], away_xy=[(52.0, 0.0), (35.0, 0.5), (34.0, 9.0)], ball_xy=(30.0, 0.0))
    assert st._shooting_lane_defender(st.ball[:2], 52.5, "away") == 2  # 5 m along, 0.5 m off: in the lane
    st.P[2] = (60.0, 0.0)
    assert st._shooting_lane_defender(st.ball[:2], 52.5, "away") is None  # 9 m off at 4 m along: not in the lane


def test_shot_is_meant_to_be_blocked_only_with_a_defender_in_the_lane():
    st = Stub(home_xy=[(30.0, 0.0)], away_xy=[(52.0, 0.0), (35.0, 0.5)], ball_xy=(30.0, 0.0))
    st.plan = SimpleNamespace(scripted=True)
    st.allow_random_goals = False
    st.cal = SimpleNamespace(events=EventDefaults())
    st.rng = np.random.default_rng(7)
    with_lane = [st.sample_shot_outcome("home", 22.0, False) for _ in range(400)]
    share = with_lane.count("blocked") / len(with_lane)
    assert share == pytest.approx(E.BLOCK_GIVEN_LANE, abs=0.08)
    st.P[2] = (20.0, 20.0)  # nobody in the lane any more
    without = [st.sample_shot_outcome("home", 22.0, False) for _ in range(200)]
    assert "blocked" not in without and set(without) <= {"saved", "wide"}


# --- the scripted penalty ----------------------------------------------------------------------


def penalty_script() -> dict:
    return {
        "match_id": "SYN-001", "universe_id": "MERIDIA-1.0", "version": 1, "title": "penalty test", "format": "test",
        "final_score": {"home": 0, "away": 0},
        "kickoff": {"period1_team": "home", "delay_s": 5.0, "half_time_break_s": 900.0},
        "periods": [{"period": 1, "end_clock": "03:30", "stoppage_announced_min": 0},
                    {"period": 2, "end_clock": "46:00", "stoppage_announced_min": 0}],
        "momentum": {"bin_minutes": 5, "home_push": [0.2], "intensity": [1.0]},
        "moments": [
            {"id": "P1", "type": "penalty", "period": 1, "clock": "02:40", "minute": "3", "team": "home",
             "player_id": "BRA-11", "fouled_id": "BRA-09", "opponent_id": "CAL-05", "goalkeeper_id": "CAL-01",
             "penalty_outcome": "saved", "body_part": "rightLeg"},
        ],
    }


@pytest.fixture(scope="module")
def penalty_match(tmp_path_factory):
    out = tmp_path_factory.mktemp("penalty")
    s = generate_match(penalty_script(), 20261025, out)
    events = json.loads((out / "events.json").read_text())["events"]
    return {"out": out, "summary": s, "events": events}


def test_penalty_moment_is_validated():
    info = json.loads((SYN / "match_info.json").read_text(encoding="utf-8"))
    script = penalty_script()
    load_script(script, info)
    broken = copy.deepcopy(script)
    broken["moments"][0]["goalkeeper_id"] = "CAL-05"
    with pytest.raises(ScriptError, match="not a goalkeeper"):
        load_script(broken, info)
    goal = copy.deepcopy(script)
    goal["moments"][0]["penalty_outcome"] = "goal"
    with pytest.raises(ScriptError, match="final_score"):  # a scored penalty counts in the score
        load_script(goal, info)
    goal["final_score"] = {"home": 1, "away": 0}
    load_script(goal, info)


def test_scripted_penalty_is_written_like_dfl(penalty_match):
    """Foul in the box, then a `Penalty` restart event that nests the shot (`ShotAtGoal/<result>`), at the scripted
    minute, with the DFL attributes and the fixed penalty xG."""
    ev = penalty_match["events"]
    pen = [e for e in ev if e["event_type"] == "Penalty"]
    assert len(pen) == 1
    p = pen[0]
    assert p["sub_type"] == "ShotAtGoal/SavedShot" and p["player_id"] == "BRA-11"
    a = p["attributes"]
    assert a["CausingPlayer"] == "CAL-05" and a["FouledPlayer"] == "BRA-09" and a["ProspectiveTaker"] == "BRA-11"
    assert a["xG"] == pytest.approx(E.PENALTY_XG) and a["DistanceToGoal"] == pytest.approx(11.0, abs=0.3)
    fouls = [e for e in ev if e["event_type"] == "Foul" and e["true_frame"] < p["true_frame"]]
    foul = fouls[-1]
    assert foul["attributes"]["Fouler"] == "CAL-05" and foul["attributes"]["Fouled"] == "BRA-09"
    assert 60 <= (p["true_frame"] - foul["true_frame"]) / 25 <= 140
    m = next(m for m in penalty_match["summary"]["moments"] if m["id"] == "P1")
    assert m["actual_minute"] == m["planned_minute"] == "3"
    assert penalty_match["summary"]["score"] == {"home": 0, "away": 0}


def test_penalty_layout_follows_law_14(penalty_match):
    """At the kick only the taker and the goalkeeper are inside the penalty area."""
    p = next(e for e in penalty_match["events"] if e["event_type"] == "Penalty")
    t = pq.read_table(penalty_match["out"] / "positions.parquet").to_pandas()
    fr = t[(t.period == p["period"]) & (t.frame == p["true_frame"]) & (t.object_type == "player")]
    ball = t[(t.period == p["period"]) & (t.frame == p["true_frame"]) & (t.object_id == "BALL")].iloc[0]
    gx = 52.5 * np.sign(ball.x_m)
    assert abs(abs(ball.x_m) - (52.5 - E.PENALTY_SPOT_M)) < 0.3 and abs(ball.y_m) < 0.3
    inside = fr[(np.abs(fr.x_m) > 52.5 - PENALTY_AREA_DEPTH_M) & (np.sign(fr.x_m) == np.sign(gx))
                & (np.abs(fr.y_m) < PENALTY_AREA_HALF_WIDTH_M)]
    assert set(inside.object_id.astype(str)) == {"BRA-11", "CAL-01"}


def test_committed_syn001_has_blocks_offsides_and_the_penalty():
    path = SYN / "events.json"
    if not path.exists():
        pytest.skip("SYN-001 has not been generated yet")
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    pen = [e for e in events if e["event_type"] == "Penalty"]
    assert len(pen) == 1 and pen[0]["sub_type"] == "ShotAtGoal/SavedShot" and pen[0]["player_id"] == "CAL-07"
    # at least one block (one random match: 1 of 18 after L-3 point 2; the block rate is tested on its own above)
    assert sum(e["sub_type"] == "BlockedShot" for e in events if e["event_type"] == "ShotAtGoal") >= 1
    assert sum(e["event_type"] == "Offside" for e in events) >= 1
    assert FRAME0[2] <= pen[0]["true_frame"]


# ---------------------------------------------------------------- team spread (E-2 item 1)

def _spread_model():
    from insidethegame_gen.match.players import PositionModel
    return PositionModel(SimpleNamespace(position_maps=None), 52.5, 34.0)


def test_team_spread_stretches_outfield_around_centroid_and_keeps_gk():
    from insidethegame_gen.match.players import TEAM_SPREAD
    roles = ["GK", "CB", "LCM", "ST"]
    t = np.array([[[-48.0, 0.0], [-20.0, -10.0], [0.0, 0.0], [20.0, 10.0]]])  # outfield centroid (0, 0)
    for ip_val, phase in ((True, "in_possession"), (False, "out_of_possession")):
        out = _spread_model()._spread(t.copy(), roles, np.full(4, ip_val))
        fx, fy = TEAM_SPREAD[phase]
        assert out[0, 0].tolist() == [-48.0, 0.0]  # the goalkeeper is not stretched
        assert out[0, 3, 0] - out[0, 1, 0] == pytest.approx(40.0 * fx)
        assert out[0, 3, 1] - out[0, 1, 1] == pytest.approx(20.0 * fy)
        assert out[0, 1:].mean(axis=0) == pytest.approx([0.0, 0.0])


def test_team_spread_keeps_targets_on_the_pitch():
    roles = ["GK", "LB", "RW", "ST"]
    t = np.array([[[-48.0, 0.0], [10.0, -33.0], [50.0, 33.0], [40.0, 0.0]]])
    out = _spread_model()._spread(t.copy(), roles, np.full(4, True))
    assert np.all(np.abs(out[..., 0]) <= 52.5 - 1.0) and np.all(np.abs(out[..., 1]) <= 34.0 - 1.5)


# ---------------------------------------------------------------- where shots are taken, shot speed (E-2 item 2)

def test_shot_rate_scale_by_band_and_fade_in_x4():
    assert E.shot_rate_scale(5, 8.0) == E.SHOT_RATE_SCALE[5]
    assert E.shot_rate_scale(5, 30.0) == E.SHOT_RATE_SCALE[5]  # no fade in the last 17.5 m band
    assert E.shot_rate_scale(4, E.X4_SHOT_FADE_FROM_M - 1.0) == E.SHOT_RATE_SCALE[4]
    far = E.shot_rate_scale(4, E.X4_SHOT_FADE_FROM_M + E.X4_SHOT_FADE_M)
    assert far == pytest.approx(E.SHOT_RATE_SCALE[4] / np.e)
    assert E.shot_rate_scale(3, 40.0) == E.SHOT_RATE_SCALE_DEFAULT
    assert E.SHOT_RATE_SCALE[5] > E.SHOT_RATE_SCALE[4]


def test_shot_launch_speed_is_measured_range_over_ratio(tmp_path):
    from insidethegame_gen.match.calibration import SHOT_MEASURED_TO_LAUNCH, load_calibration
    # measured shot speed p10 = 15, p90 = 24 m/s; long passes unchanged
    ball = {"shot_speed_ms": {"p10": 15.0, "p50": 19.0, "p90": 24.0},
            "long_pass_speed_ms": {"p10": 17.0, "p50": 20.0, "p90": 23.0}}
    (tmp_path / "ball.json").write_text(json.dumps(ball))
    cal = load_calibration(tmp_path)
    lo, hi = cal.ball.shot_speed_ms
    assert lo == pytest.approx(15.0 / SHOT_MEASURED_TO_LAUNCH)
    assert hi == pytest.approx(min(24.0 / SHOT_MEASURED_TO_LAUNCH, cal.ball.max_unscripted_kick_ms))
    assert cal.ball.long_pass_speed_ms == pytest.approx((17.0, 23.0))


def test_shot_launch_speed_capped_below_scripted_goal(tmp_path):
    from insidethegame_gen.match.calibration import load_calibration
    (tmp_path / "ball.json").write_text(json.dumps({"shot_speed_ms": {"p10": 16.0, "p50": 21.0, "p90": 28.0}}))
    cal = load_calibration(tmp_path)
    assert cal.ball.shot_speed_ms[1] == cal.ball.max_unscripted_kick_ms < 31.5  # the 88' goal stays the fastest


def test_scripted_save_aim_passes_offset_from_keeper_at_his_depth():
    from insidethegame_gen.match.director import save_aim_y
    from insidethegame_gen.match.pitch import GOAL_HALF_WIDTH_M
    ball, gk, gx = np.array([41.05, -4.8]), np.array([50.0, 0.0]), 52.5  # the M12 case that went wide
    y = save_aim_y(ball, gk, gx)
    y_at_gk = ball[1] + (y - ball[1]) * (gk[0] - ball[0]) / (gx - ball[0])
    assert abs(y_at_gk - gk[1]) == pytest.approx(1.5)
    assert abs(y) <= GOAL_HALF_WIDTH_M - 0.4
    # straight in front: 1.5 m from the keeper at his depth, extended to the goal line
    y2 = save_aim_y(np.array([40.0, 0.0]), np.array([52.0, 0.5]), gx)
    assert y2 == pytest.approx((0.5 - 1.5) * (gx - 40.0) / (52.0 - 40.0))  # aim at y = -1.0 at x = 52
