"""Tests for the match generator (Tactician v0.1).

Most tests use a short scripted "mini match" (two periods of 4 minutes, a corner header goal,
a counter-attack goal, a shot against the post, a long-range goal, one substitution; 2-1),
generated twice in temporary folders. Unit tests check the ball physics, the controller,
the script validator and the optional calibration loader on tiny inputs with known answers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from insidethegame_gen.match import ball as bp
from insidethegame_gen.match.api import REPO_ROOT, generate_match, generate_replica, replica_match_info
from insidethegame_gen.match.calibration import load_calibration
from insidethegame_gen.match.pitch import FRAME0, displayed_minute
from insidethegame_gen.match.players import simulate
from insidethegame_gen.match.report import player_kinematics
from insidethegame_gen.match.script import ScriptError, load_script
from insidethegame_gen.schema import MatchInfo

SYN = REPO_ROOT / "synthetic" / "SYN-001"
DFL_EVENT_TYPES = {"BallClaiming", "Caution", "ChanceWithoutShot", "CornerKick", "FairPlay", "FinalWhistle", "Foul",
                   "FreeKick", "GoalKick", "KickOff", "Nutmeg", "Offside", "OtherBallAction", "Penalty", "PenaltyNotAwarded",
                   "Play", "RefereeBall", "ShotAtGoal", "SpectacularPlay", "Substitution", "TacklingGame", "ThrowIn",
                   "VideoAssistantAction"}  # the DFL taxonomy without Delete
MINI_SEED = 20261024


def mini_script() -> dict:
    return {
        "match_id": "SYN-001", "universe_id": "MERIDIA-1.0", "version": 1, "title": "mini match for tests",
        "format": "test", "final_score": {"home": 2, "away": 1},
        "kickoff": {"period1_team": "home", "delay_s": 5.0, "half_time_break_s": 900.0},
        "periods": [{"period": 1, "end_clock": "04:00", "stoppage_announced_min": 0},
                    {"period": 2, "end_clock": "49:00", "stoppage_announced_min": 0}],
        "momentum": {"bin_minutes": 5, "home_push": [0.2], "intensity": [1.0]},
        "moments": [
            {"id": "T1", "type": "goal", "period": 1, "clock": "01:50", "minute": "2", "team": "home",
             "player_id": "BRA-05", "assist_id": "BRA-03", "build_up": "corner", "body_part": "head", "score_after": "1-0"},
            {"id": "T2", "type": "goal", "period": 1, "clock": "03:20", "minute": "4", "team": "away",
             "player_id": "CAL-09", "assist_id": "CAL-07", "interceptor_id": "CAL-08", "build_up": "counter_attack",
             "body_part": "leftLeg", "score_after": "1-1"},
            {"id": "T3", "type": "woodwork", "period": 2, "clock": "45:40", "minute": "46", "team": "away",
             "player_id": "CAL-10", "body_part": "rightLeg"},
            {"id": "T4", "type": "goal", "period": 2, "clock": "46:40", "minute": "47", "team": "home",
             "player_id": "BRA-08", "build_up": "long_range", "body_part": "rightLeg", "shot_speed_ms": 31.0,
             "score_after": "2-1"},
            {"id": "T5", "type": "substitution", "period": 2, "clock": "47:40", "minute": "48", "team": "away",
             "player_out": "CAL-04", "player_in": "CAL-37"},
        ],
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def match_info():
    return json.loads((SYN / "match_info.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    a = tmp_path_factory.mktemp("mini_a")
    b = tmp_path_factory.mktemp("mini_b")
    sa = generate_match(mini_script(), MINI_SEED, a)
    sb = generate_match(mini_script(), MINI_SEED, b)
    table = pq.read_table(a / "positions.parquet").to_pandas()
    events = json.loads((a / "events.json").read_text(encoding="utf-8"))
    return {"a": a, "b": b, "summary": sa, "summary_b": sb, "table": table, "events": events}


# --- script ------------------------------------------------------------------------------


def test_syn001_script_is_valid(match_info):
    script = load_script(SYN / "script.json", match_info)
    assert (script.final_score.home, script.final_score.away) == (2, 1)
    assert [m.minute for m in script.moments if m.type == "goal"] == ["23", "41", "88"]


@pytest.mark.parametrize("breaker, message", [
    (lambda s: s["moments"].append({"id": "X1", "type": "goal", "period": 1, "clock": "30:00", "team": "away",
                                    "player_id": "CAL-19", "build_up": "open_play", "score_after": "0-2"}),
     "not on the pitch"),
    (lambda s: s.update(final_score={"home": 3, "away": 1}), "final_score"),
    (lambda s: s["moments"].append({"id": "X2", "type": "caution", "period": 2, "clock": "85:10", "team": "away",
                                    "player_id": "CAL-04", "card": "yellow"}), "not on the pitch"),
    (lambda s: [m.update(minute="24") for m in s["moments"] if m["id"] == "M02"], "does not match clock"),
    (lambda s: s["moments"].extend(
        {"id": f"S{i}", "type": "substitution", "period": 2, "clock": f"{80 + i}:30", "team": "home",
         "player_out": out, "player_in": inn}
        for i, (out, inn) in enumerate([("BRA-02", "BRA-15"), ("BRA-04", "BRA-26"), ("BRA-06", "BRA-29"),
                                        ("BRA-10", "BRA-17")])), "substitution"),
])
def test_script_rejects_impossible_scripts(match_info, breaker, message):
    data = json.loads((SYN / "script.json").read_text(encoding="utf-8"))
    broken = copy.deepcopy(data)
    breaker(broken)
    broken["moments"].sort(key=lambda m: (m["period"], m["clock"]))
    with pytest.raises(ScriptError) as err:
        load_script(broken, match_info)
    assert any(message in p for p in err.value.problems), err.value.problems


# --- determinism and format ----------------------------------------------------------------


def test_two_runs_are_byte_identical(mini, tmp_path):
    for name in ("positions.parquet", "events.json"):
        assert sha(mini["a"] / name) == sha(mini["b"] / name)
    other = generate_match(mini_script(), MINI_SEED + 1, tmp_path)
    assert other["files"]["events"]["sha256"] != mini["summary"]["files"]["events"]["sha256"]


def test_existing_outputs_are_not_overwritten(mini):
    with pytest.raises(FileExistsError):
        generate_match(mini_script(), MINI_SEED, mini["a"])


def test_events_use_the_dfl_taxonomy_and_never_delete(mini):
    types = {e["event_type"] for e in mini["events"]["events"]}
    assert "Delete" not in types
    assert types <= DFL_EVENT_TYPES
    assert set(mini["events"]) == {"match_id", "universe_id", "generator_version", "seed", "events"}
    keys = {"event_id", "event_type", "sub_type", "time_utc", "period", "team_side", "player_id", "x_m", "y_m",
            "attributes", "true_frame"}
    assert all(set(e) == keys for e in mini["events"]["events"])


def test_ball_status_and_possession_on_every_row(mini):
    t = mini["table"]
    assert list(t.columns) == ["period", "frame", "time_utc", "object_id", "object_type", "team_side", "x_m", "y_m",
                               "z_m", "ball_status", "ball_possession"]
    assert t["ball_status"].notna().all() and t["ball_possession"].notna().all()
    assert set(t["ball_status"].astype(str)) <= {"live", "dead"}
    assert set(t["ball_possession"].astype(str)) <= {"home", "away", "none"}
    assert set(t.loc[t.period == 1, "frame"]) >= {FRAME0[1]} and t.loc[t.period == 2, "frame"].min() == FRAME0[2]
    ball = t[t.object_id == "BALL"]
    assert ball.groupby(["period", "frame"]).size().max() == 1
    assert t[t.object_type == "player"]["z_m"].isna().all() and ball["z_m"].notna().all()


def test_event_time_is_the_time_of_its_frame(mini):
    """No annotation delay (L-0): every event, the second-half kick-off included, has the time of its frame."""
    t = mini["table"]
    ball = t[t.object_id == "BALL"].set_index(["period", "frame"])["time_utc"]
    for e in mini["events"]["events"]:
        assert datetime.fromisoformat(e["time_utc"]) == ball.loc[(e["period"], e["true_frame"])].to_pydatetime(), e
    kickoffs = [e for e in mini["events"]["events"] if e["event_type"] == "KickOff"
                and e["attributes"].get("GameSection") == "secondHalf"]
    assert kickoffs[0]["true_frame"] == FRAME0[2]
    first2 = ball.loc[2].sort_index().iloc[0]
    assert datetime.fromisoformat(kickoffs[0]["time_utc"]) == first2.to_pydatetime()


def _touches(table, period, frame, ball_xy, side_players, radius=2.6):
    """Players of `side_players` within `radius` m of the ball at this frame (noisy positions)."""
    rows = table[(table.period == period) & (table.frame == frame) & table.object_id.isin(side_players)]
    return set(rows.loc[np.hypot(rows.x_m - ball_xy[0], rows.y_m - ball_xy[1]) <= radius, "object_id"].astype(str))


def test_outcomes_follow_the_simulation(mini):
    """Results are written after the simulation: a successful pass reaches a team-mate, every shot has a result
    that matches the trajectory, restart shots are nested as DFL (`FreeKick` > `ShotAtGoal/<result>`)."""
    from insidethegame_gen.match.outcomes import SHOT_RESULTS
    t = mini["table"]
    ball = t[t.object_id == "BALL"].set_index(["period", "frame"])
    events = mini["events"]["events"]
    side_of = {str(o): str(s) for o, s in zip(t.object_id, t.team_side) if str(o) != "BALL"}
    for e in events:
        if e["event_type"] == "ShotAtGoal":
            assert e["sub_type"] in SHOT_RESULTS, e
        if e["event_type"] in ("FreeKick", "CornerKick", "KickOff", "ThrowIn", "GoalKick") \
                and (e["sub_type"] or "").startswith("ShotAtGoal"):
            assert e["sub_type"].split("/")[1] in SHOT_RESULTS, e
        if (e["event_type"] == "Play" or e["sub_type"] == "Play") and \
                e["attributes"].get("Evaluation") == "successfullyCompleted":
            r = e["attributes"]["Recipient"]
            assert side_of[r] == e["team_side"] and r != e["player_id"], e
    assert mini["summary"]["outcome_problems"] == []


def test_resolver_reads_the_first_thing_that_happens():
    from insidethegame_gen.match.outcomes import shot_result
    touch = {"kind": "touch", "side": "away", "gk": True}
    assert shot_result(touch, "home") == "SavedShot"
    assert shot_result({**touch, "gk": False}, "home") == "BlockedShot"
    assert shot_result({"kind": "goal", "side": "home"}, "home") == "SuccessfulShot"
    assert shot_result({"kind": "post", "side": "home"}, "home") == "ShotWoodWork"
    assert shot_result({"kind": "out", "side": "home"}, "home") == "ShotWide"


def test_free_kick_shots_carry_their_result(tmp_path, monkeypatch):
    """Restart shots are nested as DFL, with the result from the simulation: FreeKick > ShotAtGoal/<result>.
    Every restart after the kick-off is turned into a direct free kick, so the test does not depend on luck."""
    from insidethegame_gen.match.events import Engine
    from insidethegame_gen.match.outcomes import SHOT_RESULTS
    original = Engine.restart_action

    def free_kick_shot(self, r, taker):
        if r.kind == "KickOff":
            return original(self, r, taker)
        r.kind, r.danger, r.reason = "FreeKick", True, "foul"
        self.shoot(taker, "saved", restart=r)

    monkeypatch.setattr(Engine, "restart_action", free_kick_shot)
    generate_replica("SYN-001", 1, tmp_path, period_minutes=3.0)
    events = json.loads((tmp_path / "events.json").read_text())["events"]
    fk = [e for e in events if e["event_type"] == "FreeKick" and (e["sub_type"] or "").startswith("ShotAtGoal")]
    assert fk and all(e["sub_type"].split("/")[1] in SHOT_RESULTS for e in fk)
    assert all("xG" in e["attributes"] and "Recipient" not in e["attributes"] for e in fk)


# --- physics -----------------------------------------------------------------------------------


def test_player_speed_and_acceleration_within_physical_limits(mini):
    t = mini["table"]
    players = t[t.object_type == "player"]
    for (pid, period), g in players.groupby(["object_id", "period"], observed=True):
        g = g.sort_values("frame")
        sp, at = player_kinematics(g.x_m.to_numpy(float), g.y_m.to_numpy(float))
        assert sp.max() <= 38.0 / 3.6, (pid, sp.max())
        assert at.min() >= -8.5 and at.max() <= 7.5, (pid, at.min(), at.max())


def test_top_speed_comes_from_the_attributes(mini):
    """L-0: the target top speed is the DFL value of the player's pace in his group (attributes.physical_value)."""
    from insidethegame_gen.attributes import load_scales, physical_value
    universe = json.loads((REPO_ROOT / "synthetic" / "universe" / "universe.json").read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in universe["players"]}
    scales = load_scales()
    for pid, p in mini["summary"]["players"].items():
        u = by_id[pid]
        assert p["pace_source"] == "attribute"
        want = physical_value(scales, "pace", u["position_group"], u["attributes"]["pace"])
        assert p["top_speed_kmh_target"] == pytest.approx(want, abs=0.01), pid


def test_goals_cross_the_line_between_the_posts_and_nothing_else_does(mini):
    t = mini["table"]
    ball = t[t.object_id == "BALL"].sort_values(["period", "frame"])
    goals = [e for e in mini["events"]["events"] if e["event_type"] == "ShotAtGoal" and e["sub_type"] == "SuccessfulShot"]
    assert len(goals) == 3
    crossing_frames = set()
    for e in goals:
        after = ball[(ball.period == e["period"]) & (ball.frame > e["true_frame"])]
        over = after[after.x_m.abs() > 52.5 + bp.BALL_RADIUS_M].iloc[0]
        assert over.ball_status == "live"
        assert abs(over.y_m) < 3.66 and over.z_m < 2.44
        crossing_frames.add((e["period"], int(over.frame)))
    live = ball[ball.ball_status == "live"]
    inside_goal = live[(live.x_m.abs() > 52.5 + bp.BALL_RADIUS_M) & (live.y_m.abs() < 3.66) & (live.z_m < 2.44)]
    for r in inside_goal.itertuples():  # every live frame inside a goal belongs to a goal (noise: +-2 frames)
        assert any(p == r.period and 0 <= int(r.frame) - f <= 2 for p, f in crossing_frames), (r.period, r.frame)


def test_final_score_follows_the_script(mini):
    goals = [e["team_side"] for e in mini["events"]["events"] if e["sub_type"] == "SuccessfulShot"]
    assert (goals.count("home"), goals.count("away")) == (2, 1)
    assert mini["summary"]["script_problems"] == []
    for m in mini["summary"]["moments"]:
        assert m["actual_minute"] == m["planned_minute"], m


def test_woodwork_really_bounces_off_a_post(mini):
    t = mini["table"]
    shot = next(e for e in mini["events"]["events"] if e["sub_type"] == "ShotWoodWork")
    ball = t[(t.object_id == "BALL") & (t.period == shot["period"]) & (t.frame >= shot["true_frame"])
             & (t.frame <= shot["true_frame"] + 50)].sort_values("frame")
    x, y = ball.x_m.to_numpy(float), ball.y_m.to_numpy(float)
    vx = np.diff(x)
    towards = np.sign(vx[0])
    flip = int(np.nonzero(np.sign(vx) == -towards)[0][0])
    assert abs(abs(x[flip]) - 52.5) < 0.6
    assert abs(abs(y[flip]) - (3.66 + 0.06)) < 0.6


def test_rolling_pass_arrives_with_the_planned_speed():
    v0 = bp.ground_v0(20.0, 6.0)
    t, v = bp.ground_arrival(20.0, v0)
    assert v == pytest.approx(6.0, abs=1e-6)
    s, vv = bp.ground_path(np.array([t]), v0)
    assert s[0] == pytest.approx(20.0, abs=1e-6) and vv[0] == pytest.approx(6.0, abs=1e-6)


def test_shot_planner_hits_the_target_at_the_requested_speed():
    p0 = np.array([25.0, 2.0, bp.GROUND_Z])
    target = np.array([52.5, -2.8, 1.9])
    vh0, vz0, T = bp.plan_shot(p0, target, 31.5)
    assert np.hypot(vh0, vz0) == pytest.approx(31.5, rel=1e-3)
    s, z, _, _ = bp.air_state(T, vh0, vz0, p0[2])
    assert s == pytest.approx(np.hypot(27.5, 4.8), abs=1e-3) and z == pytest.approx(1.9, abs=1e-3)


def test_post_reflection_reverses_the_normal_velocity():
    v = bp.reflect_on_post(np.array([52.3, 3.72, 1.0]), np.array([20.0, 0.0, 0.0]), np.array([52.44, 3.72]))
    assert v[0] < 0


def test_controller_respects_the_limits():
    P = np.zeros((1, 2))
    V = np.zeros((1, 2))
    T = np.tile(np.array([[[30.0, 0.0]]]), (250, 1, 1))
    pos = simulate(P, V, T, np.array([1.5]), np.array([9.0]), np.array([6.5]), np.array([6.5]))
    sp, at = player_kinematics(pos[:, 0, 0], pos[:, 0, 1])
    assert sp.max() <= 9.0 + 0.05
    assert at.max() <= 6.5 + 0.1 and at.min() >= -6.5 - 0.1
    assert np.hypot(*(pos[-1, 0] - np.array([30.0, 0.0]))) < 0.5


def test_displayed_minute():
    assert displayed_minute(1, 0.0) == "1" and displayed_minute(1, 22 * 60 + 31) == "23"
    assert displayed_minute(1, 46 * 60 + 26) == "45+2" and displayed_minute(2, 48 * 60 + 18) == "90+4"


# --- calibration and replicas -------------------------------------------------------------------


def test_calibration_is_optional(tmp_path):
    cal = load_calibration(tmp_path)
    assert cal.calibrated == []
    assert cal.top_speed_for_pace("W", 50) == pytest.approx(31.9)
    scales = {"attributes": {"pace": {"unit": "km/h", "by_role_group": {"W": {"n": 3, "values": list(np.linspace(28, 34, 99))}}}}}
    (tmp_path / "attribute_scales.json").write_text(json.dumps(scales), encoding="utf-8")
    cal2 = load_calibration(tmp_path)
    assert "pace attribute scale" in cal2.calibrated
    assert cal2.top_speed_for_pace("W", 50) == pytest.approx(31.0, abs=0.05)


def test_replica_has_no_story_and_differs(tmp_path, mini):
    s = generate_replica("SYN-001", 1, tmp_path, period_minutes=3.0)
    assert s["scripted"] is False and s["moments"] == []
    assert s["files"]["events"]["sha256"] != mini["summary"]["files"]["events"]["sha256"]
    types = {e["event_type"] for e in json.loads((tmp_path / "events.json").read_text())["events"]}
    assert "Delete" not in types and "KickOff" in types


def test_replica_match_info_is_the_fixture_with_its_own_id():
    info = json.loads((SYN / "match_info.json").read_text(encoding="utf-8"))
    rep = replica_match_info(info, "SYN-001-r3")
    assert rep["match_id"] == "SYN-001-r3" and info["match_id"] == "SYN-001"  # the fixture's dict is not changed
    assert {k: v for k, v in rep.items() if k != "match_id"} == {k: v for k, v in info.items() if k != "match_id"}
    MatchInfo.model_validate(rep)  # still valid against the match_info schema (no extra fields)


def test_replica_folder_carries_its_own_match_info(tmp_path):
    """A replica folder is a complete synthetic match for the data pipeline (match_info.json, events.json, positions.parquet)."""
    s = generate_replica("SYN-001", 2, tmp_path, period_minutes=3.0)
    info = json.loads((tmp_path / "match_info.json").read_text(encoding="utf-8"))
    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert info["match_id"] == events["match_id"] == "SYN-001-r2"
    fixture = json.loads((SYN / "match_info.json").read_text(encoding="utf-8"))
    assert info["teams"] == fixture["teams"] and info["environment"] == fixture["environment"]
    assert s["files"]["match_info"]["sha256"] == hashlib.sha256((tmp_path / "match_info.json").read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):  # never overwritten without overwrite=True (guidelines 1.9)
        generate_replica("SYN-001", 2, tmp_path, period_minutes=3.0)


def test_committed_syn001_follows_the_story():
    path = SYN / "events.json"
    if not path.exists():
        pytest.skip("SYN-001 has not been generated yet")
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    goals = [(e["team_side"], e["player_id"]) for e in events if (e["sub_type"] or "").endswith("SuccessfulShot")]
    assert goals == [("away", "CAL-09"), ("home", "BRA-05"), ("home", "BRA-08")]
    assert "Delete" not in {e["event_type"] for e in events}
    assert sum(e["event_type"] == "Caution" for e in events) == 1
    assert sum(e["event_type"] == "Substitution" for e in events) == 4
    assert {e["event_type"] for e in events} >= {"Nutmeg", "VideoAssistantAction", "PenaltyNotAwarded"}
    assert (SYN / "positions.parquet").stat().st_size < 50e6
