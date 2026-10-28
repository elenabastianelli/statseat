"""Tests for the E-6 rules of the match generator, written so that its data satisfy the v4.2 pipeline rules:

- headers (R-AERIAL, R-TOUCH-NEAREST): a header shot or a headed clearance exists only when the player really meets
  the cross at head height; a cross that does not arrive is a loose ball, never a header stamped at the goal line;
- the post (R-WOODWORK): nobody is near the ball when it hits the post, and nobody touches the rebound at once;
- room to kick (R-TOUCH-NEAREST): nobody is nearer to the ball than the kicker around his kick.

Unit tests use tiny hand-made positions; short scripted matches and replicas check the rest end to end."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest

from insidethegame_gen.match import events as E
from insidethegame_gen.match.api import generate_match
from insidethegame_gen.match.pitch import Pitch


class CrossStub:
    """The Engine attributes that `cross` reads, around one crosser (slot 0), one header taker (slot 1) and one
    defender (slot 2); home attacks +x. `pass_ball`, `advance` and the executors after the cross are recorded."""
    cross = E.Engine.cross
    _header_contact = E.Engine._header_contact
    _reaches = E.Engine._reaches

    def __init__(self, taker_xy, ball_xyz, pass_result="received", defender_xy=(46.0, 4.0)):
        self.P = np.array([[30.0, -30.0], taker_xy, defender_xy], float)
        self.ball = np.array(ball_xyz, float)
        self.sides = ["home", "home", "away"]
        self.side_slots = {"home": [0, 1], "away": [2]}
        self.roster = SimpleNamespace(slot_role=["RW", "ST", "CB"], gk_slot=lambda side: 2)
        self.pitch = Pitch()
        self.dir = {"home": 1.0, "away": -1.0}
        self.rng = np.random.default_rng(3)
        self.cal = SimpleNamespace(events=SimpleNamespace(cross_success=0.3))
        self.director = None
        self.carrier, self.poss = 0, "home"
        self.calls: list = []
        self._pass_result = pass_result

    def side_of(self, s):
        return self.sides[s]

    def goal_x(self, side):
        return self.dir[side] * self.pitch.hl

    def bias(self, side):
        return 0.0

    def pid(self, s):
        return f"P{s}"

    def _make_kick_room(self, c, plan, keep=()):
        return False  # nobody near the crosser in these stubs

    def _straight_path(self, aim, speed):
        return np.zeros((0, 2))

    def pass_ball(self, c, r, kind, outcome, **kw):
        self.calls.append(("pass", r))
        if self._pass_result == "received":
            self.carrier, self.poss = r, self.side_of(r)
        return self._pass_result

    def sample_shot_outcome(self, side, dist, header, free_kick=False):
        return "wide"

    def shoot(self, c, outcome, body=None, **kw):
        self.calls.append(("shoot", c, body))
        return "out"

    # the "cleared" branch
    def ball_path(self, b0, point, kind, z_end=0.11, **kw):
        n = 20
        path = np.linspace(b0, np.array([point[0], point[1], z_end]), n + 1)[1:]
        return path, n

    def _kick_event(self, c, restart, inner, sub, attrs):
        self.calls.append(("kick", c))

    def live_targets(self, ball_xy, poss, press_point=None):
        return np.repeat(self.P[None], ball_xy.shape[0], axis=0), np.ones(len(self.P))

    def _own_goal_dir(self, side):
        return np.array([-self.dir[side], 0.0])

    def _nearest(self, side, xy, exclude=None, exclude_gk=True):
        return 2

    def advance(self, n, path, T, eff, **kw):
        self.ball = path[-1].copy()  # the defender stays where he is (P[2])

    def _run_on(self, path, last_side, kicker=None):
        self.calls.append(("run_on",))
        return "claimed"

    def duel_event(self, *a, **kw):
        self.calls.append(("duel",))

    def log_ball(self, *a, **kw):
        pass

    def clear(self, c, header=False, aim=None, out=None):
        self.calls.append(("clear", c, header))
        return "claimed"


def test_e6_header_contact_needs_reach_and_head_height():
    st = CrossStub(taker_xy=(47.0, 0.3), ball_xyz=(47.0, 0.0, 2.1))
    assert st._header_contact(1)  # 0.3 m, ball at 2.1 m
    st.ball = np.array([47.0, 0.9, 2.1])
    assert not st._header_contact(1)  # 0.6 m is the limit: 0.6 m away it is too far
    st.ball = np.array([47.0, 0.0, 1.2])
    assert not st._header_contact(1)  # chest height: not a header
    st.ball = np.array([47.0, 0.0, 3.0])
    assert not st._header_contact(1)  # above any jump


def test_e6_cross_that_does_not_arrive_gives_no_header_shot():
    """Before E-6 a header shot followed the cross whatever happened to it: now the ball physics decides."""
    st = CrossStub(taker_xy=(47.0, 0.0), ball_xyz=(35.0, -30.0, 0.11), pass_result="claimed")
    st.cross(0, outcome="header", receiver=1, point=np.array([47.0, 0.0]))
    assert ("shoot", 1, "head") not in st.calls and not any(c[0] == "shoot" for c in st.calls)


def test_e6_cross_met_at_head_height_is_a_header_shot():
    st = CrossStub(taker_xy=(47.0, 0.3), ball_xyz=(47.0, 0.0, 2.0))
    st.cross(0, outcome="header", receiver=1, point=np.array([47.0, 0.0]))
    assert ("shoot", 1, "head") in st.calls


def test_e6_cross_reached_but_not_for_a_header_is_taken_down():
    st = CrossStub(taker_xy=(47.0, 1.2), ball_xyz=(47.0, 0.0, 2.0))  # within reach (1.2 m) but not for a header
    st.cross(0, outcome="header", receiver=1, point=np.array([47.0, 0.0]))
    assert not any(c[0] == "shoot" for c in st.calls) and st.carrier == 1


def test_e6_cleared_cross_needs_the_defender_at_the_ball():
    far = CrossStub(taker_xy=(40.0, 0.0), ball_xyz=(35.0, -30.0, 0.11), defender_xy=(40.0, 10.0))
    far.cross(0, outcome="cleared", receiver=1, point=np.array([47.0, 0.0]))
    assert ("run_on",) in far.calls and not any(c[0] == "clear" for c in far.calls)
    near = CrossStub(taker_xy=(40.0, 0.0), ball_xyz=(35.0, -30.0, 0.11), defender_xy=(47.0, 0.2))
    near.cross(0, outcome="cleared", receiver=1, point=np.array([47.0, 0.0]))
    assert ("clear", 2, True) in near.calls  # met at head height: a headed clearance after the aerial duel


class ClearStub:
    _keep_clear = E.Engine._keep_clear

    def __init__(self, P):
        self.P = np.array(P, float)
        self.n = len(self.P)


def test_e6_keep_clear_moves_only_nearby_targets_out_of_the_radius():
    st = ClearStub([(0.0, 0.0), (5.0, 0.0), (0.4, 0.0)])
    ball = np.array([[0.5, 0.0], [0.5, 0.0], [0.5, 0.0]])
    T = np.array([[[0.0, 0.0], [5.0, 0.0], [0.5, 0.0]]] * 3)
    st._keep_clear(T, ball, 1, 2, 2.0, exempt={0})
    assert np.allclose(T[0], [[0.0, 0.0], [5.0, 0.0], [0.5, 0.0]])  # frame 0 is outside the window
    assert np.allclose(T[1:, 0], [0.0, 0.0])  # exempt
    assert np.allclose(T[1:, 1], [5.0, 0.0])  # already far
    d = np.hypot(*(T[1:, 2] - ball[1:]).T)
    assert np.allclose(d, 2.0) and np.all(T[1:, 2, 0] < 0.5)  # stepped away on the side where he stands


# --- the post, end to end -------------------------------------------------------------------------


def post_script() -> dict:
    return {
        "match_id": "SYN-001", "universe_id": "MERIDIA-1.0", "version": 1, "title": "post test", "format": "test",
        "final_score": {"home": 0, "away": 0},
        "kickoff": {"period1_team": "home", "delay_s": 5.0, "half_time_break_s": 900.0},
        "periods": [{"period": 1, "end_clock": "03:30", "stoppage_announced_min": 0},
                    {"period": 2, "end_clock": "46:00", "stoppage_announced_min": 0}],
        "momentum": {"bin_minutes": 5, "home_push": [0.2], "intensity": [1.0]},
        "moments": [{"id": "W1", "type": "woodwork", "period": 1, "clock": "02:40", "minute": "3", "team": "away",
                     "player_id": "CAL-10", "body_part": "rightLeg"}],
    }


@pytest.fixture(scope="module")
def post_match(tmp_path_factory):
    out = tmp_path_factory.mktemp("post")
    s = generate_match(post_script(), 20261026, out)
    events = json.loads((out / "events.json").read_text())["events"]
    table = pq.read_table(out / "positions.parquet").to_pandas()
    return {"summary": s, "events": events, "table": table}


def test_e6_nobody_is_near_the_ball_when_it_hits_the_post(post_match):
    """R-WOODWORK sees woodwork only when every player is at least 1 m from the ball within +-0.2 s of the contact
    (positions here carry the tracking noise, about 0.1 m)."""
    shot = next(e for e in post_match["events"] if e["sub_type"] == "ShotWoodWork")
    t = post_match["table"]
    fr = t[(t.period == shot["period"]) & (t.frame >= shot["true_frame"]) & (t.frame <= shot["true_frame"] + 75)]
    ball = fr[fr.object_id == "BALL"].sort_values("frame")
    x, y = ball.x_m.to_numpy(float), ball.y_m.to_numpy(float)
    vx = np.diff(x)
    flip = int(np.nonzero(np.sign(vx) == -np.sign(vx[0]))[0][0])  # the frame where the ball comes back off the post
    assert abs(abs(x[flip]) - 52.5) < 0.8 and abs(abs(y[flip]) - 3.72) < 0.8
    f_c = int(ball.frame.iloc[flip])
    near = fr[(fr.object_type == "player") & (fr.frame >= f_c - 5) & (fr.frame <= f_c + 5)]
    b = ball.set_index("frame")
    d = np.hypot(near.x_m.to_numpy(float) - b.loc[near.frame, "x_m"].to_numpy(float),
                 near.y_m.to_numpy(float) - b.loc[near.frame, "y_m"].to_numpy(float))
    assert d.min() >= 1.0
    assert abs(y[flip + 5]) > abs(y[flip])  # the outer half of the post: the rebound goes away from the goal mouth
    later = [e for e in post_match["events"] if e["period"] == shot["period"] and e["true_frame"] > shot["true_frame"]]
    assert later and later[0]["true_frame"] - f_c >= E.WOODWORK_FREE_S * 25 - 1  # nobody touches it at once
    assert post_match["summary"]["moments"][0]["actual_minute"] == "3"


# --- room to kick (R-TOUCH-NEAREST) ----------------------------------------------------------------


class KickStub:
    """A kicker (slot 0) with the ball 0.45 m ahead of him, and two other players; the last KICK_WINDOW_S of
    frames are all identical and live."""
    _kick_intruders = E.Engine._kick_intruders
    _straight_path = E.Engine._straight_path
    _aerial_winner = E.Engine._aerial_winner
    _header_contact = E.Engine._header_contact

    def __init__(self, others_xy, ball_z=0.11, velocities=None):
        self.P = np.array([(-0.45, 0.0)] + list(others_xy), float)
        self.V = np.zeros_like(self.P) if velocities is None else np.array(velocities, float)
        self.n = len(self.P)
        self.ball = np.array([0.0, 0.0, ball_z])
        k = int(round(E.KICK_WINDOW_S * 25)) + 1
        self.buf = SimpleNamespace(P=np.repeat(self.P[None], k, axis=0), B=np.repeat(self.ball[None], k, axis=0),
                                   live=np.ones(k, bool))
        self.f = k - 1


def test_e6_kick_intruders_are_players_nearer_to_the_ball_than_the_kicker():
    assert KickStub([(0.2, 0.0), (3.0, 0.0)])._kick_intruders(0, None) == [1]  # 0.20 m against the kicker's 0.45 m
    assert KickStub([(0.37, 0.0), (3.0, 0.0)])._kick_intruders(0, None) == []  # only 0.08 m nearer: within tolerance
    st = KickStub([(0.2, 0.0), (3.0, 0.0)], ball_z=2.0)  # a ball in the air: 0.25 m nearer is within 0.3 m
    assert st._kick_intruders(0, None) == []


def test_e6_kick_intruders_include_the_first_metres_of_the_ball_path():
    st = KickStub([(2.0, 0.1), (2.0, 5.0)])  # one stands in the lane 2 m ahead, one is 5 m to the side
    lane = st._straight_path(np.array([20.0, 0.0]), 15.0)  # 0.6 m per frame: past him within 0.24 s
    assert st._kick_intruders(0, None) == []
    assert st._kick_intruders(0, lane) == [1]
    slow = KickStub([(2.0, 0.1), (2.0, 5.0)])
    assert slow._kick_intruders(0, slow._straight_path(np.array([20.0, 0.0]), 4.0)) == []  # 1 m in 0.24 s: not him


def test_e6_aerial_duel_goes_to_a_player_who_can_head_it_and_owns_the_touch():
    st = KickStub([(0.2, 0.0), (0.7, 0.0)], ball_z=2.0)  # slot 1 at 0.2 m, slot 2 at 0.7 m from the ball
    assert st._aerial_winner(1, 2, drawn=2) == 1  # 0.7 m: no header contact, and 0.5 m farther than slot 1
    assert st._aerial_winner(1, 2, drawn=1) == 1
    st.ball[2] = 3.2
    assert st._aerial_winner(1, 2, drawn=1) is None  # above any jump: nobody heads it, the ball runs on


class InterceptStub:
    _intercept = E.Engine._intercept

    def __init__(self):
        self.P = np.array([[1.2, 0.0]])  # standing on the path, 1.2 m from the kick
        self.side_slots = {"away": [0]}
        self.roster = SimpleNamespace(slot_role=["CB"])

    def arrays(self):
        return {"vmax": np.array([8.0])}


def test_e6_interception_never_comes_inside_the_kick_window():
    """A 16-frame pass past a defender 1.2 m from the kick: before E-6 he took it 4 frames (0.16 s) after the kick."""
    path = np.column_stack([0.6 * np.arange(1, 17), np.zeros(16), np.full(16, 0.11)])
    s, k = InterceptStub()._intercept(path, "away", forced=0)
    assert s == 0 and k >= int(round(E.INTERCEPT_MIN_S * 25))


@pytest.fixture(scope="module")
def replicas(tmp_path_factory):
    """The same short replica with the rule (E-6) and without it (KICK_SHAPE_ROUNDS = 0)."""
    from insidethegame_gen.match.api import generate_replica

    with_room = tmp_path_factory.mktemp("room")
    s = generate_replica("SYN-001", 4, with_room, period_minutes=8.0)
    rounds = E.KICK_SHAPE_ROUNDS
    E.KICK_SHAPE_ROUNDS = 0
    try:
        s0 = generate_replica("SYN-001", 4, tmp_path_factory.mktemp("no_room"), period_minutes=8.0)
    finally:
        E.KICK_SHAPE_ROUNDS = rounds
    events = json.loads((with_room / "events.json").read_text())["events"]
    table = pq.read_table(with_room / "positions.parquet").to_pandas()
    return {"with": s, "without": s0, "events": events, "table": table}


def test_e6_every_kick_belongs_to_its_kicker(replicas):
    """Noise-free self-check of the generator (`outcomes.kick_ownership`, the pipeline's R-TOUCH-NEAREST measure)."""
    ko, ko0 = replicas["with"]["kick_ownership"], replicas["without"]["kick_ownership"]
    assert ko["kicks"] > 100 and ko["made_room"] > 0
    assert len(ko["not_owned"]) <= 0.01 * ko["kicks"]
    assert len(ko0["not_owned"]) > len(ko["not_owned"])  # without the rule more kicks go to another player


def test_e6_nobody_but_the_taker_is_near_the_ball_at_a_restart(replicas):
    t = replicas["table"]
    ball = t[t.object_id == "BALL"].set_index(["period", "frame"])
    for ev in replicas["events"]:
        if ev["event_type"] not in ("ThrowIn", "GoalKick", "CornerKick", "FreeKick"):
            continue
        fr = t[(t.period == ev["period"]) & (t.frame == ev["true_frame"]) & (t.object_type == "player")
               & (t.object_id != ev["player_id"])]
        b = ball.loc[(ev["period"], ev["true_frame"])]
        d = np.hypot(fr.x_m.to_numpy(float) - float(b.x_m), fr.y_m.to_numpy(float) - float(b.y_m))
        assert d.min() >= E.RESTART_SPACE_M - 0.5, ev["event_id"]  # tracking noise ~0.1 m, controller lag

