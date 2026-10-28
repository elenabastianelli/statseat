"""The match engine: possession chains (Markov model), executors and the 25 Hz simulation.

The engine advances from one touch to the next. At each on-ball decision it knows the real
positions of the 22 players, so receivers, interceptors and pressers are chosen from actual
positions and every touch is physically consistent: the receiver runs to the reception point
and reaches it when the ball does. Each action (control and carry, pass, shot, clearance,
duel, loose ball, dead ball) is a *segment*: the ball trajectory and the players' targets are
computed for the whole segment, then the controller of `players.py` moves the players frame
by frame.

Between scripted moments (see `director.py`) actions come from a Markov model over pitch
zones (6 x 3, team-attacking coordinates) and actions, with success rates by zone and length,
adjusted by team style, pressure and the "who is pushing" profile. Event names and attributes
follow the DFL taxonomy (never `Delete`).

Outcomes are written after the simulation. A random draw only decides the *intent* of an action
(where a pass or a shot is aimed, how well it is struck, who goes for a loose ball); every kick
event is created at its kick frame without a result, and the engine logs what physically
happens to the ball (`ball_log`: touches with the player, goals, the post, the ball leaving the
pitch, fouls, offsides). `outcomes.resolve_outcomes` then fills `Evaluation` and `Recipient` of
passes, the result of shots (`SuccessfulShot`, `SavedShot`, `ShotWide`, `BlockedShot`,
`ShotWoodWork`; nested under a restart as `ShotAtGoal/<result>`, as DFL) and the winner of duels
from the first thing that happens after the event.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import ball as bp
from .calibration import Calibration
from .pitch import (BALL_RADIUS_M, BENCH_SIDE_Y_SIGN, DT, FS, GOAL_AREA_DEPTH_M, GOAL_HALF_WIDTH_M,
                    PENALTY_AREA_DEPTH_M, PENALTY_AREA_HALF_WIDTH_M, POST_RADIUS_M, WALL_DISTANCE_M, Pitch,
                    period_start_clock, zone_band, zone_channel)
from .players import PositionModel, Roster, simulate, smooth_noise

SIDES = ("home", "away")
POSS_CODE = {"none": 0, "home": 1, "away": 2}
TEAM_STYLE = {  # v0.1, from the clubs' playing styles in the universe
    "BRA": {"pass": 0.03, "long": 0.8, "cross": 0.9, "press": 1.0, "hold": 1.05, "carry": 1.1, "short_gk": 0.55},
    "CAL": {"pass": -0.02, "long": 1.55, "cross": 1.4, "press": 1.25, "hold": 0.85, "carry": 0.95, "short_gk": 0.25},
}
NEUTRAL_STYLE = {"pass": 0.0, "long": 1.0, "cross": 1.0, "press": 1.0, "hold": 1.0, "carry": 1.0, "short_gk": 0.4}
# Shift of the pass *intent* from the DFL success share of the zone. The written result comes from the
# simulation (contested balls and loose balls won back count), so the intent sits below the target share.
PASS_INTENT_SHIFT = -0.12
RECEIVE_REACH_M = 1.6
CARRY_AHEAD_M = 0.45  # a carried ball is this far ahead of the carrier, in his running direction
GK_REACH_M = 1.9  # a diving goalkeeper: hands about 1.5 m from the tracked centre (the pipeline's touch reach is 2 m)
BLOCK_REACH_M = 1.8  # a sliding or stretching defender  # a player touches the ball only if it passes this close (noise-free, horizontal)
DEAD_MEDIAN_S = {"ThrowIn": 12.0, "GoalKick": 22.0, "CornerKick": 30.0, "FreeKick": 20.0, "FreeKickDanger": 40.0,
                 "KickOff": 62.0, "Offside": 16.0, "Penalty": 95.0}
# Blocked shots (L-3). DFL: 32 of 144 shots blocked (22 %). A shot can be blocked only when an outfield defender
# stands in the shooting lane (`_shooting_lane_defender`); about half the shots have one, so the block intent is
# drawn with BLOCK_GIVEN_LANE when he does and never otherwise.
LANE_ALONG_M = (0.5, 18.0)  # distance of the lane defender from the ball, along the line to the goal
LANE_OFF_M = (0.9, 0.12)  # maximum distance from that line: 0.9 m + 0.12 m per metre along
BLOCK_GIVEN_LANE = 0.60  # 0.45 before shots moved closer to goal (L-3 point 2): fewer lanes are occupied there
BLOCK_MIN_FRAMES = 2  # the block touch comes at least 0.08 s after the kick (guidelines R-SHOT-SPLIT)
# Offside from positions (L-3, IFAB Law 11). A pass is whistled offside only when the receiver, at the kick frame,
# is in the opponents' half and nearer to their goal line than both the ball and the second-last opponent.
# Attackers hold the line (their targets stay onside) except runs in behind that start too early
# (MISTIMED_RUN_SHARE); a passer rarely picks a team-mate he sees offside (OFFSIDE_PICK_WEIGHT). Both are tuned so
# that a match has about 2 offsides, as the six DFL matches (1-3, mean 2.2).
ONSIDE_MARGIN_M = 0.4  # attackers' targets stay this far behind the offside line
MISTIMED_RUN_SHARE = 0.06
OFFSIDE_PICK_WEIGHT = 0.12
OFFSIDE_RESTARTS = (None, "FreeKick")  # no offside from a throw-in, a corner, a goal kick or the kick-off
PENALTY_SPOT_M = 11.0  # IFAB Law 1
PENALTY_XG = 0.79  # fixed xG of a penalty (E-3, Materials/METRICS.md section 9)
PENALTY_FOUL_SHARE = 0.06  # duels in the penalty area that end in a penalty (replicas; DFL: 3 penalties in 6 matches)
# Where shots are taken (L-3, E-2 item 2). DFL: two thirds of the shots from the last 17.5 m (band x5) and 29 % from
# x4 (17.5-35 m), median distance 17.2 m. Our attacks spend more time in x4 than real ones, so the DFL rate per
# action is scaled by band, and in x4 it fades with the distance to goal beyond X4_SHOT_FADE_FROM_M.
SHOT_RATE_SCALE = {5: 0.85, 4: 0.62}  # other bands: SHOT_RATE_SCALE_DEFAULT
SHOT_RATE_SCALE_DEFAULT = 0.55
X4_SHOT_FADE_FROM_M = 22.0
X4_SHOT_FADE_M = 8.0  # e-folding distance: a shot from 30 m is e times less likely than one from 22 m
# Headers (E-6). A header (a header shot, or a headed clearance of a cross) exists only when the player really meets
# the ball: the data pipeline (guidelines v4.2, R-AERIAL and R-TOUCH-NEAREST with its 0.5 m aerial margin) finds a
# header only with the player at the ball at a head-height contact. Before E-6 a cross that never reached the header
# taker still produced a header shot, stamped where the ball had run out over the goal line.
HEADER_REACH_M = 0.6  # horizontal distance tracked centre (torso) to ball at the contact: a stretching, jumping header
HEADER_Z_M = (1.5, 2.7)  # ball-centre height of a header: from a stooping 1.5 m to a jumping 2.7 m
# The post (E-6). The pipeline sees woodwork only when nobody can have touched the ball at the contact (R-WOODWORK:
# path within 0.3 m of the post and every player at least 1 m away within +-0.2 s, or the ball nearer to the post
# than to any player by more than 0.5 m). Before E-6 the scripted 58' rebound flew back into the goal mouth and
# reached the goalkeeper 0.16 s later, so it read as a save. Now the shot meets the outer half of the post (it comes
# back out, away from the goal mouth), every player keeps WOODWORK_CLEAR_M from the ball path around the contact
# and nobody touches the rebound for WOODWORK_FREE_S.
WOODWORK_AIM_OUT_M = 0.06  # the ball's line passes this far outside the post centre (of 0.17 m contact distance)
WOODWORK_CLEAR_M = 2.0  # players' targets stay this far from the ball path from 1 s before to 0.3 s after the contact
WOODWORK_CLEAR_BEFORE_S = 1.0
WOODWORK_CLEAR_AFTER_S = 0.3
WOODWORK_FREE_S = 0.6  # nobody touches the rebound earlier than this after the contact
# Room to kick (E-6). The pipeline gives a kick to the player nearest to the ball within +-0.2 s of it, or within
# 0.3 m of the nearest (0.5 m for an aerial touch) (guidelines v4.2, R-TOUCH-NEAREST). Before E-6, 42 of the 832 kicks
# of SYN-001 had another player nearer to the ball than the kicker (mostly the loser of the duel just before, or a
# presser standing on the ball), so the pipeline gave them to him. Now, before a kick from open play, when another
# player is nearer to the ball than the kicker (less KICK_SPACE_TOL_M; KICK_SPACE_TOL_AERIAL_M for a ball in the air)
# in the KICK_WINDOW_S before the kick or along the first KICK_WINDOW_S of the ball's path, the kicker first takes the
# ball away from him for KICK_SHAPE_S while every other player steps back to KICK_SPACE_M (at most KICK_SHAPE_ROUNDS
# times). The ball itself never jumps: the controller moves the players.
KICK_WINDOW_S = 0.24  # one frame beyond the pipeline's +-0.2 s
# Nobody more than this nearer to the ball than the kicker: 0.2 m of safety below the pipeline's margins (0.3 m on
# the ground, 0.5 m in the air), for the tracking noise (~0.1 m per player).
KICK_SPACE_TOL_M = 0.1
KICK_SPACE_TOL_AERIAL_M = 0.3
AERIAL_MIN_Z_M = 1.5  # above this height a touch is aerial (the pipeline's aerial_min_height_m, R-AERIAL)
KICK_SPACE_M = 1.6  # how far from the ball the others step back while the kicker makes room
KICK_LANE_M = 1.3  # ... and how far from the first metres of the kick's path (the passing or shooting lane)
KICK_LANE_S = 0.4  # those first metres: where the ball is in the first 0.4 s
KICK_SHAPE_S = 0.48  # one touch away from the intruder
KICK_SHAPE_STEP_M = 1.2  # how far the kicker takes the ball in that touch
KICK_SHAPE_ROUNDS = 2
RESTART_SPACE_M = 2.0  # at a restart nobody but the taker stands nearer to the ball (Law 15: 2 m at a throw-in)
INTERCEPT_MIN_S = 0.4  # an interception never comes inside the kick window (the interceptor would own the kick)
BLOCK_BODY_M = 0.7  # a blocking defender's centre stops this far from the ball: he blocks with a leg or the body
CONTEST_OFFSET_M = 0.45  # in a contested ball both players meet it, one on each side (0.7 m behind it before E-6)


def other(side: str) -> str:
    return "away" if side == "home" else "home"


@dataclass
class PeriodBuffers:
    n_max: int
    n_slots: int
    P: np.ndarray = field(init=False)
    B: np.ndarray = field(init=False)
    live: np.ndarray = field(init=False)
    poss: np.ndarray = field(init=False)
    pid: np.ndarray = field(init=False)
    n: int = 0

    def __post_init__(self):
        self.P = np.full((self.n_max, self.n_slots, 2), np.nan)
        self.B = np.full((self.n_max, 3), np.nan)
        self.live = np.zeros(self.n_max, bool)
        self.poss = np.zeros(self.n_max, np.int8)
        self.pid = np.full((self.n_max, self.n_slots), -1, np.int16)


@dataclass
class Restart:
    kind: str  # KickOff | ThrowIn | GoalKick | CornerKick | FreeKick
    side: str
    spot: np.ndarray
    duration_s: float | None = None
    taker: int | None = None
    action: object = None  # callable(engine, taker) for scripted restarts
    extras: list = field(default_factory=list)  # (offset_s, event kwargs)
    celebrate: int | None = None  # scorer slot
    var_check: bool = False
    danger: bool = False
    reason: str = ""
    info: dict = field(default_factory=dict)  # extra DFL attributes of the restart event (penalty: fouler, fouled...)


class PeriodOver(Exception):
    """Raised inside an executor when the final whistle has gone during a dead phase."""


def shot_rate_scale(band: int, dist_goal: float) -> float:
    """Factor on the DFL shot rate per action of the zone: by band, fading with distance in band x4."""
    k = SHOT_RATE_SCALE.get(band, SHOT_RATE_SCALE_DEFAULT)
    if band == 4:
        k *= float(np.exp(-max(0.0, dist_goal - X4_SHOT_FADE_FROM_M) / X4_SHOT_FADE_M))
    return k


class Engine:
    def __init__(self, plan, roster: Roster, cal: Calibration, pitch: Pitch, rngs: dict, universe_ids: list[str]):
        self.plan = plan
        self.roster = roster
        self.cal = cal
        self.pitch = pitch
        self.rng = rngs["events"]
        self.rng_wander = rngs["wander"]
        self.posmodel = PositionModel(cal, pitch.hl, pitch.hw)
        self.object_ids = universe_ids  # index -> player id (for the pid buffer)
        self.pid_index = {p: i for i, p in enumerate(universe_ids)}
        self.events: list[dict] = []
        self.ball_log: list[dict] = []  # what physically happens to the ball (outcomes.resolve_outcomes)
        self.buffers: dict[int, PeriodBuffers] = {}
        self.score = {"home": 0, "away": 0}
        self.cards: dict[str, int] = {}
        self.overrides: dict[int, dict] = {}
        self.moment_log: list[dict] = []
        self.sprint_vmax: dict[int, float] = {}
        self.director = None
        self.allow_random_goals = not plan.scripted
        self.allow_random_cards = not plan.scripted
        self.live_frames = 0
        self.total_frames = 0
        self.turnover_f = -10 ** 9
        self.last_duel_f = -10 ** 9
        self.runs: dict[int, tuple] = {}  # slot -> (target point, until frame, mistimed)
        self.offside_log: list[dict] = []  # every pass to a receiver in an offside position (whistled or not)
        self.kick_room_log: list[dict] = []  # every time a kicker made room before his kick (E-6, R-TOUCH-NEAREST)
        self.side_slots = {s: roster.slots_of(s) for s in SIDES}
        self.team_id = roster.team_ids
        self.style = {s: TEAM_STYLE.get(self.team_id[s], NEUTRAL_STYLE) for s in SIDES}

    # ------------------------------------------------------------------ basics

    @property
    def n(self) -> int:
        return self.roster.n

    def side_of(self, slot: int) -> str:
        return self.roster.slot_side[slot]

    def pid(self, slot: int) -> str:
        return self.roster.slot_player[slot]

    def slot(self, player_id: str) -> int:
        s = self.roster.slot_of_player(player_id)
        if s is None:
            raise KeyError(f"{player_id} is not on the pitch")
        return s

    def att(self, side: str, xy) -> np.ndarray:
        return self.dir[side] * np.asarray(xy, float)

    raw = att  # the rotation by 180 degrees is its own inverse

    def goal_x(self, side: str) -> float:
        """Raw x of the goal that `side` attacks."""
        return self.dir[side] * self.pitch.hl

    def elapsed(self, f: int | None = None) -> float:
        return (self.f if f is None else f) * DT

    def clock(self, f: int | None = None) -> float:
        return period_start_clock(self.period) + self.elapsed(f)

    def momentum(self) -> tuple[float, float]:
        home_push, intensity = self.plan.momentum(self.clock(), self.period)
        if self.director is not None:
            intensity = self.director.intensity(self.clock(), self.period, intensity)
        return home_push, intensity

    def push(self, side: str) -> float:
        hp, _ = self.momentum()
        return hp if side == "home" else -hp

    def bias(self, side: str) -> float:
        """Scripted lead-in bias in favour of `side` (0 = none, 1 = strong)."""
        if self.director is None:
            return 0.0
        b_side, strength = self.director.bias(self.clock(), self.period)
        if b_side is None:
            return 0.0
        return strength if b_side == side else -strength

    def arrays(self):
        a = self.roster.arrays()
        for s, v in self.sprint_vmax.items():
            a["vmax"][s] = v
        return a

    def log_ball(self, kind: str, slot: int | None = None, f: int | None = None, side: str | None = None, xy=None):
        """Log what happens to the ball at frame f: `touch` (slot), `goal`, `post`, `out` (side = last team to
        touch), `foul` (side = the team fouled) or `offside` (side = the attacking team)."""
        f = self.f if f is None else int(f)
        if xy is None:
            xy = self.buf.B[f, :2] if 0 <= f < self.buf.n_max and np.isfinite(self.buf.B[f, 0]) else self.ball[:2]
        self.ball_log.append({"period": self.period, "f": f, "kind": kind,
                              "player_id": self.pid(slot) if slot is not None else None,
                              "side": self.side_of(slot) if slot is not None else side,
                              "gk": slot is not None and self.roster.slot_role[slot] == "GK",
                              "x": float(xy[0]), "y": float(xy[1]), "seq": len(self.ball_log)})

    def duel_event(self, sub: str, with_ball: int, other_slot: int, winner: int, xy=None, attrs: dict | None = None):
        """TacklingGame between `with_ball` and `other_slot`. `winner` is the intent of the draw; the event's winner
        is set after the simulation from the next touch (outcomes.resolve_outcomes)."""
        loser = other_slot if winner == with_ball else with_ball
        a = {"Winner": self.pid(winner), "Loser": self.pid(loser), "WinnerTeam": self.team_id[self.side_of(winner)],
             "Type": sub, "WinnerRole": "withBall" if winner == with_ball else "withoutBall"}
        a.update(attrs or {})
        ev = self.event("TacklingGame", sub, winner, xy=self.ball[:2] if xy is None else xy, attrs=a)
        ev["resolve"] = {"what": "duel", "with_ball": self.pid(with_ball), "other": self.pid(other_slot),
                         "sides": {self.pid(with_ball): self.side_of(with_ball),
                                   self.pid(other_slot): self.side_of(other_slot)}}
        return ev

    def event(self, etype: str, sub: str | None = None, slot: int | None = None, side: str | None = None,
              f: int | None = None, xy=None, attrs: dict | None = None) -> dict:
        f = self.f if f is None else f
        if xy is None:
            xy = self.buf.B[f, :2] if np.isfinite(self.buf.B[f, 0]) else self.ball[:2]
        if slot is not None and side is None:
            side = self.side_of(slot)
        a = {}
        if side in SIDES:
            a["Team"] = self.team_id[side]
        if slot is not None:
            a["Player"] = self.pid(slot)
        a.update(attrs or {})
        ev = {"event_type": etype, "sub_type": sub, "period": self.period, "f": int(f),
              "team_side": side if side in SIDES else "none", "player_id": self.pid(slot) if slot is not None else None,
              "x": float(xy[0]), "y": float(xy[1]), "attributes": a, "seq": len(self.events)}
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------ frames

    def _write(self, pos: np.ndarray, ball: np.ndarray, live, poss: str):
        n = pos.shape[0]
        a, b = self.f + 1, self.f + 1 + n
        if b > self.buf.n_max:
            raise RuntimeError("period buffer overflow")
        if POSS_CODE[poss] != self.buf.poss[self.f] and self.buf.poss[self.f] != 0:
            self.turnover_f = self.f
        self.buf.P[a:b] = pos
        self.buf.B[a:b] = ball
        self.buf.live[a:b] = live
        self.buf.poss[a:b] = POSS_CODE[poss]
        self.buf.pid[a:b] = self.cur_pid
        self.f += n
        self.buf.n = self.f + 1
        self.ball = ball[-1].copy()
        nl = int(np.sum(live)) if not isinstance(live, (bool, np.bool_)) else (n if live else 0)
        self.live_frames += nl
        self.total_frames += n

    def _ball_follow(self, pos_c: np.ndarray, side: str, blend: int = 6) -> np.ndarray:
        """Ball at the feet of a carrier (N, 2): 0.45 m ahead in his running direction."""
        n = pos_c.shape[0]
        prev = np.vstack([self.buf.P[self.f, self._follow_slot][None, :] if self._follow_slot is not None else pos_c[:1],
                          pos_c])
        vel = np.diff(prev, axis=0) * FS
        sp = np.hypot(vel[:, 0], vel[:, 1])
        fwd = np.array([self.dir[side], 0.0])
        u = np.where((sp > 0.6)[:, None], vel / np.maximum(sp, 1e-9)[:, None], fwd[None, :])
        out = np.empty((n, 3))
        out[:, :2] = pos_c + CARRY_AHEAD_M * u
        out[:, 2] = bp.GROUND_Z
        offset = self.ball[:2] - out[0, :2]
        k = min(max(blend, int(math.ceil(float(np.hypot(*offset)) / 0.35)),
                    int(math.ceil(max(self.ball[2] - bp.GROUND_Z, 0.0) / 0.3))), n)
        if k > 0:
            w = 1.0 - np.arange(1, k + 1) / k
            out[:k, :2] += offset[None, :] * w[:, None]
            out[:k, 2] += max(self.ball[2] - bp.GROUND_Z, 0.0) * w
        # a carried ball never leaves the pitch (on the line it is still in play)
        out[:, 0] = np.clip(out[:, 0], -self.pitch.hl, self.pitch.hl)
        out[:, 1] = np.clip(out[:, 1], -self.pitch.hw, self.pitch.hw)
        return out

    def advance(self, n: int, ball_path: np.ndarray | None, targets: np.ndarray, effort: np.ndarray,
                live=True, poss: str | None = None, carrier: int | None = None, arrive: dict | None = None,
                sprint: set | None = None):
        """Simulate n frames. ball_path (n, 3) or None when the ball follows `carrier`."""
        if n <= 0:
            return
        a = self.arrays()
        arrive_k = np.full(self.n, -1)
        for s, k in (arrive or {}).items():
            arrive_k[s] = k
        spr = np.zeros(self.n, bool)
        for s in (sprint or ()):
            spr[s] = True
        pos = simulate(self.P, self.V, targets, effort, a["vmax"], a["a0"], a["dmax"], arrive_k, spr)
        if ball_path is None:
            self._follow_slot = carrier
            ball_path = self._ball_follow(pos[:, carrier], self.side_of(carrier))
        self._write(pos, ball_path, live, poss or self.poss)

    # ------------------------------------------------------------------ targets

    def live_targets(self, ball_xy: np.ndarray, poss: str, press_point: np.ndarray | None = None,
                     n_press: int | None = None, exclude_press: set | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Targets (n, 22, 2) and effort (22,) in live play with the ball path `ball_xy` (n, 2)."""
        n = ball_xy.shape[0]
        T = np.empty((n, self.n, 2))
        for side in SIDES:
            slots = self.side_slots[side]
            ip = np.full(len(slots), side == poss)
            t_att = self.posmodel.targets([self.roster.slot_role[s] for s in slots], ip, self.att(side, ball_xy))
            T[:, slots] = self.att(side, t_att)
        a = self.arrays()
        _, intensity = self.momentum()
        effort = a["work"] * intensity
        if self.f - self.turnover_f < 4 * FS:
            effort = effort * 1.2
        for s, (pt, until, _) in list(self.runs.items()):
            if self.f >= until or self.side_of(s) != poss:
                self.runs.pop(s)
        w = self.wander[self.f + 1:self.f + 1 + n]
        if w.shape[0] < n:
            w = np.concatenate([w, np.repeat(w[-1:], n - w.shape[0], axis=0)]) if w.shape[0] else np.zeros((n, self.n, 2))
        T += w
        # pressing: the nearest outfield opponents of the ball close it down
        dside = other(poss)
        pp = ball_xy[0] if press_point is None else press_point
        cand = [s for s in self.side_slots[dside] if self.roster.slot_role[s] != "GK" and s not in (exclude_press or ())]
        if cand:
            d = np.array([np.hypot(*(self.P[s] - pp)) for s in cand])
            order = np.argsort(d, kind="stable")
            style = self.style[dside]["press"]
            k = n_press if n_press is not None else (2 if (style > 1.1 or intensity > 1.2 or self.bias(dside) > 0.3)
                                                    else 1)
            own_goal = np.array([-self.goal_x(dside), 0.0])
            for j in order[:k]:
                s = cand[j]
                if d[j] > 26.0:
                    continue
                aim = ball_xy if press_point is None else np.repeat(press_point[None, :], n, axis=0)
                u = own_goal - aim
                u /= np.maximum(np.hypot(u[:, 0], u[:, 1]), 1e-9)[:, None]
                T[:, s] = aim + (1.1 if j == order[0] else 3.5) * u
                effort[s] = a["work"][s] * intensity * (1.15 if j == order[0] else 1.0) * style * (1 + 0.3 * max(0, self.bias(dside)))
        T[..., 0] = np.clip(T[..., 0], -self.pitch.hl + 0.3, self.pitch.hl - 0.3)
        T[..., 1] = np.clip(T[..., 1], -self.pitch.hw + 0.3, self.pitch.hw - 0.3)
        # off-ball runs in behind (Markov) and scripted overrides
        early = set()
        for s, (pt, until, mistimed) in self.runs.items():
            T[:, s] = pt
            effort[s] = max(effort[s], 1.5 * a["work"][s])
            if mistimed:
                early.add(s)
        for s, ov in self.overrides.items():
            if "point" in ov:
                T[:, s] = ov["point"]
            effort[s] = ov.get("effort", effort[s])
        self._hold_the_line(T, ball_xy, poss, skip=early)
        return T, effort

    # ------------------------------------------------------------------ offside (IFAB Law 11)

    def offside_line_att(self, side: str, ball_xy=None) -> float:
        """x (attacking coordinates of `side`) beyond which a team-mate of `side` is in an offside position now:
        the farther of the second-last opponent (goalkeeper included) and the ball, and never in his own half."""
        xs = sorted(float(self.att(side, self.P[s])[0]) for s in self.side_slots[other(side)])
        line = xs[-2] if len(xs) >= 2 else self.pitch.hl
        b = self.ball[:2] if ball_xy is None else ball_xy
        return max(line, float(self.att(side, b)[0]), 0.0)

    def offside_margin(self, r: int) -> float:
        """How far (m) player r is beyond the offside line at the current frame (> 0: offside position)."""
        side = self.side_of(r)
        return float(self.att(side, self.P[r])[0]) - self.offside_line_att(side)

    def _hold_the_line(self, T: np.ndarray, ball_xy: np.ndarray, poss: str, skip: set):
        """Attackers of the team in possession keep their targets onside (behind the offside line along the ball
        path), except the slots in `skip` (runs that start too early)."""
        xs = sorted(float(self.att(poss, self.P[s])[0]) for s in self.side_slots[other(poss)])
        line_def = xs[-2] if len(xs) >= 2 else self.pitch.hl
        bx = self.att(poss, ball_xy)[:, 0]
        cap = np.maximum(np.maximum(line_def, bx), 0.0) - ONSIDE_MARGIN_M  # (n,)
        for s in self.side_slots[poss]:
            if s in skip or self.roster.slot_role[s] == "GK":
                continue
            xa = self.dir[poss] * T[:, s, 0]
            T[:, s, 0] = self.dir[poss] * np.minimum(xa, cap)

    # ------------------------------------------------------------------ executors

    def hold(self, c: int, duration_s: float, dest=None, speed: float | None = None, presser: int | None = None,
             presser_arrive: bool = False, arrive_extra: dict | None = None, sprint: set | None = None,
             fallen: set | None = None, room: bool = False, lane: tuple | None = None, room_keep: set | None = None):
        """The carrier keeps the ball (control, carry or dribble) for duration_s. `room`: he is making room for a
        kick (`_make_kick_room`), so every other player but `room_keep` steps back to KICK_SPACE_M from the ball and
        out of `lane` (the first metres of the kick's path, a segment (a, b)) to KICK_LANE_M."""
        side = self.side_of(c)
        n = max(1, int(round(duration_s * FS)))
        # the ball must have time to reach the carrier (at most ~9 m/s)
        n = max(n, int(math.ceil(float(np.hypot(*(self.ball[:2] - self.P[c]))) / 0.35)) + 1)
        p0 = self.P[c].copy()
        if dest is None:
            dest = p0
        dest = self.pitch.clamp(np.asarray(dest, float), margin=-0.8)
        d = dest - p0
        dist = float(np.hypot(*d))
        v = speed if speed is not None else (dist / max(duration_s, 0.2))
        t = DT * np.arange(1, n + 1)
        prog = np.minimum(v * (t + 0.35), dist)
        u = d / dist if dist > 1e-6 else np.zeros(2)
        path = p0[None, :] + prog[:, None] * u[None, :]
        T, effort = self.live_targets(path, side)
        T[:, c] = path
        effort[c] = 2.0
        arrive = dict(arrive_extra or {})
        if presser is not None:
            T[:, presser] = path + 0.6 * (self._own_goal_dir(self.side_of(presser)))[None, :]
            effort[presser] = 2.2
            if presser_arrive:
                arrive[presser] = n
        for s in (fallen or ()):
            T[:, s] = self.P[s]
            effort[s] = 0.05
        if room:  # the ball is CARRY_AHEAD_M ahead of the carrier: keep the others clear of both
            before = T.copy()
            self._keep_clear(T, path, 0, n - 1, KICK_SPACE_M + CARRY_AHEAD_M, exempt={c})
            if lane is not None:
                self._keep_off_segment(T, lane[0], lane[1], KICK_LANE_M, exempt={c} | set(room_keep or ()))
            moved = np.any(np.abs(T - before) > 1e-9, axis=(0, 2))
            effort = np.where(moved, np.maximum(effort, 2.0), effort)
        self.advance(n, None, T, effort, live=True, poss=side, carrier=c, arrive=arrive, sprint=sprint)
        self.carrier = c
        self.poss = side

    def _own_goal_dir(self, side: str) -> np.ndarray:
        return np.array([-self.dir[side], 0.0])

    # ------------------------------------------------------------------ room to kick (E-6, R-TOUCH-NEAREST)

    def _kick_intruders(self, c: int, path: np.ndarray | None) -> list[int]:
        """Players nearer to the ball than the kicker c (less KICK_SPACE_TOL_M, or KICK_SPACE_TOL_AERIAL_M for a ball
        above 1.5 m), each player's closest approach being measured as the pipeline does it: over the live frames
        of the last KICK_WINDOW_S and, when `path` (the planned ball positions after the kick) is given, along its
        first KICK_WINDOW_S with every player moving on at his current velocity."""
        w = int(round(KICK_WINDOW_S * FS))
        f = self.f
        lo = max(0, f - w)
        P, B = self.buf.P[lo:f + 1], self.buf.B[lo:f + 1]
        with np.errstate(invalid="ignore"):
            d = np.hypot(P[..., 0] - B[:, None, 0], P[..., 1] - B[:, None, 1])
        d = np.where(self.buf.live[lo:f + 1, None] & np.isfinite(d), d, np.inf)
        dmin = d.min(axis=0)
        if path is not None and path.shape[0]:
            k = min(w, path.shape[0])
            t = DT * np.arange(1, k + 1)
            Q = self.P[None] + self.V[None] * t[:, None, None]
            dmin = np.minimum(dmin, np.hypot(Q[..., 0] - path[:k, None, 0], Q[..., 1] - path[:k, None, 1]).min(axis=0))
        if not np.isfinite(dmin[c]):
            return []
        tol = KICK_SPACE_TOL_AERIAL_M if float(self.ball[2]) > AERIAL_MIN_Z_M else KICK_SPACE_TOL_M
        return [s for s in range(self.n) if s != c and dmin[s] < dmin[c] - tol]

    def _straight_path(self, aim, speed: float) -> np.ndarray:
        """The first KICK_LANE_S of a ball kicked from where it is now towards `aim` at `speed` (a straight line:
        enough for `_kick_intruders` and the kicking lane of `_make_kick_room`)."""
        b = self.ball[:2]
        d = np.asarray(aim, float)[:2] - b
        dist = float(np.hypot(*d))
        u = d / dist if dist > 1e-6 else np.zeros(2)
        t = DT * np.arange(1, int(round(max(KICK_WINDOW_S, KICK_LANE_S) * FS)) + 1)
        return b[None, :] + u[None, :] * (speed * t)[:, None]

    def _make_kick_room(self, c: int, plan, keep=()) -> bool:
        """Before a kick from open play (E-6, R-TOUCH-NEAREST): while another player is nearer to the ball than the
        kicker (`_kick_intruders`), the kicker takes the ball KICK_SHAPE_STEP_M across his kick, away from that
        player, and every other player but `keep` (the intended receiver) steps back from the ball and out of the
        kicking lane (`hold(..., room=True)`), at most KICK_SHAPE_ROUNDS times. `plan()` returns the planned ball
        positions after the kick from where the ball is now. Returns True when the ball moved (the caller plans its
        kick again)."""
        moved = False
        for _ in range(KICK_SHAPE_ROUNDS):
            path = plan()
            intr = self._kick_intruders(c, path)
            if not intr or self._time_up():
                break
            k = min(int(round(KICK_WINDOW_S * FS)), path.shape[0]) if path is not None else 0
            fwd = path[k - 1, :2] - self.ball[:2] if k else self.att(self.side_of(c), np.array([1.0, 0.0]))
            if float(np.hypot(*fwd)) < 1e-6:
                fwd = self.att(self.side_of(c), np.array([1.0, 0.0]))
            fwd = fwd / float(np.hypot(*fwd))
            perp = np.array([-fwd[1], fwd[0]])
            cen = np.mean(self.P[intr], axis=0) - self.ball[:2]
            step = (-1.0 if float(cen @ perp) > 0 else 1.0) * perp  # across the kick, away from the intruders
            dest = self.pitch.clamp(self.P[c] + KICK_SHAPE_STEP_M * step, margin=-0.8)
            kl = min(int(round(KICK_LANE_S * FS)), path.shape[0]) if path is not None else 0
            lane = (self.ball[:2].copy(), path[kl - 1, :2].copy()) if kl else None
            self.kick_room_log.append({"period": self.period, "f": int(self.f), "kicker": self.pid(c),
                                       "intruders": [self.pid(s) for s in intr]})
            self.hold(c, KICK_SHAPE_S, dest=dest, speed=KICK_SHAPE_STEP_M / KICK_SHAPE_S, room=True, lane=lane,
                      room_keep=set(keep))
            moved = True
        return moved

    def _flat_plan(self, b0: np.ndarray, point: np.ndarray, v_arr: float, vmax_kick: float = 23.0):
        D = float(np.hypot(*(point - b0[:2])))
        v0 = min(bp.ground_v0(D, v_arr), vmax_kick)
        arr = bp.ground_arrival(D, v0)
        if arr is None:  # too slow to get there: kick harder
            v0 = bp.ground_v0(D, 2.5)
            arr = bp.ground_arrival(D, v0)
        T, _ = arr
        n = max(2, int(math.ceil(T / DT)))
        return v0, n

    def _air_plan(self, b0: np.ndarray, point: np.ndarray, z_end: float, T: float | None = None,
                  vmax_kick: float | None = None):
        vmax_kick = vmax_kick or self.cal.ball.max_unscripted_kick_ms
        D = float(np.hypot(*(point - b0[:2])))
        if T is None:
            lo, hi = self.cal.ball.high_pass_air_time_s
            T = float(np.clip(0.75 + D / 22.0, 0.9, hi + 0.2))
        for _ in range(60):
            vh0, vz0 = bp.air_plan(D, T, float(b0[2]), z_end)
            if math.hypot(vh0, vz0) <= vmax_kick:
                break
            T += 0.08
        n = max(2, int(math.ceil(T / DT)))
        vh0, vz0 = bp.air_plan(D, n * DT, float(b0[2]), z_end)
        return vh0, vz0, n

    def ball_path(self, b0: np.ndarray, point: np.ndarray, kind: str, z_end: float = bp.GROUND_Z,
                  v_arr: float | None = None, T: float | None = None, vmax_kick: float | None = None):
        """(n, 3) trajectory from b0 to point with the flight model of `kind`."""
        if kind in ("flat", "through", "low_cross", "nutmeg"):
            lo, hi = self.cal.ball.pass_arrival_ms
            if v_arr is None:
                v_arr = float(self.rng.uniform(lo, hi)) if kind == "flat" else float(self.rng.uniform(3.0, 5.5))
            v0, n = self._flat_plan(b0, point, v_arr)
            path = bp.rolling(np.array([b0[0], b0[1], bp.GROUND_Z]), point, v0, n)
            path[-1, :2] = point
            return path, n
        vh0, vz0, n = self._air_plan(b0, point, z_end, T=T, vmax_kick=vmax_kick)
        path, _ = bp.aerial(b0, point, vh0, vz0, n)
        return path, n

    def reception_point(self, c: int, r: int, kind: str, lead: float | None = None) -> np.ndarray:
        side = self.side_of(r)
        fwd = np.array([self.dir[side], 0.0])
        b0 = self.ball
        pr, vr = self.P[r].copy(), self.V[r].copy()
        lead = float(self.rng.uniform(0.0, 2.5)) if lead is None else lead
        T = 1.0
        pt = pr
        for _ in range(3):
            pt = pr + vr * T * 0.5 + fwd * lead
            pt = self.pitch.clamp(pt, margin=-1.0)
            D = float(np.hypot(*(pt - b0[:2])))
            if kind in ("flat", "through", "low_cross"):
                lo, hi = self.cal.ball.pass_arrival_ms
                arr = bp.ground_arrival(D, bp.ground_v0(D, 0.5 * (lo + hi)))
                T = arr[0] if arr else 1.5
            else:
                T = float(np.clip(0.75 + D / 22.0, 0.9, 3.2))
        return pt

    def _intercept(self, path: np.ndarray, side_def: str, forced: int | None = None, min_frac: float = 0.25):
        """(slot, frame index) of the opponent who can reach the ball path first, or None."""
        n = path.shape[0]
        k0 = max(int(round(INTERCEPT_MIN_S * FS)), int(min_frac * n))  # never inside the kick window (E-6)
        ks = np.arange(k0, n)
        if ks.size == 0:
            return None
        a = self.arrays()
        cands = [forced] if forced is not None else [s for s in self.side_slots[side_def]]
        best = None
        for s in cands:
            low = path[ks, 2] < (2.6 if self.roster.slot_role[s] == "GK" else 2.0)
            d = np.hypot(path[ks, 0] - self.P[s, 0], path[ks, 1] - self.P[s, 1])
            need = d / (0.82 * a["vmax"][s]) + 0.3
            ok = np.nonzero(low & (need <= ks * DT))[0]
            if ok.size:
                k = int(ks[ok[0]])
                if best is None or k < best[1]:
                    best = (s, k)
            elif forced is not None:
                j = int(np.argmin(need - ks * DT + 100 * (~low)))
                best = (s, int(ks[j]))
        return best

    def pass_ball(self, c: int, r: int, kind: str = "flat", outcome: str = "success", point=None,
                  interceptor: int | None = None, sub: str = "Pass", restart: Restart | None = None,
                  z_end: float | None = None, v_arr: float | None = None, T: float | None = None,
                  sprint_receiver: bool = False, attrs: dict | None = None, out_side: str | None = None):
        """A pass (or cross, throw-in, restart kick) from c to r.

        outcome is the intent drawn by the caller: success | intercept | contested | out. The event's
        `Evaluation` is written after the simulation from who touches the ball next. Returns the resulting state.
        """
        side = self.side_of(c)
        b0 = self.ball.copy()
        if point is None:
            point = self.reception_point(c, r, kind)
        point = np.asarray(point, float)
        if z_end is None:
            z_end = bp.GROUND_Z if kind in ("flat", "through", "low_cross") else float(self.rng.choice([bp.GROUND_Z, 1.0]))
        if outcome == "out":
            point = self._out_point(b0[:2], point, out_side)
        if v_arr is None and kind in ("flat", "through", "low_cross", "nutmeg"):
            # drawn here, in the same order as `ball_path` would, so that the kick can be planned again (E-6)
            lo, hi = self.cal.ball.pass_arrival_ms
            v_arr = float(self.rng.uniform(lo, hi)) if kind == "flat" else float(self.rng.uniform(3.0, 5.5))
        if restart is None and self._make_kick_room(
                c, lambda: self.ball_path(self.ball.copy(), point, kind, z_end=z_end, v_arr=v_arr, T=T)[0], keep={r}):
            b0 = self.ball.copy()
        path, n = self.ball_path(b0, point, kind, z_end=z_end, v_arr=v_arr, T=T)
        height = "flat" if kind in ("flat", "through", "low_cross") else "high"
        ev_attrs = {"Evaluation": None, "Height": height, "Recipient": self.pid(r)}
        if attrs:
            ev_attrs.update(attrs)
        # offside is judged at the kick frame, before anybody moves (IFAB Law 11)
        margin = self.offside_margin(r) if (restart.kind if restart else None) in OFFSIDE_RESTARTS else -1.0
        self._kick_event(c, restart, "Play", sub, ev_attrs)
        if margin > 0.0 and outcome in ("success", "contested"):
            busy = self.director is not None and self.director.busy
            self.offside_log.append({"period": self.period, "f": int(self.f), "passer": self.pid(c),
                                     "receiver": self.pid(r), "margin_m": round(margin, 2), "whistled": not busy,
                                     "moment": getattr(self.director, "current_id", None) if busy else None})
            if not busy:  # a scripted sequence never stops here; api.py rejects a scripted goal after an offside
                return self._whistle_offside(c, r, path, n)
        if outcome == "intercept":
            hit = self._intercept(path, other(side), forced=interceptor)
            if hit is None:
                outcome = "contested"
            else:
                s_i, k_i = hit
                full_path = path
                path = path[:k_i]
                T_, eff = self.live_targets(path[:, :2], side, press_point=path[-1, :2])
                T_[:, s_i] = path[-1, :2]
                T_[:, r] = point
                eff[s_i] = 2.0
                self.advance(k_i, path, T_, eff, live=True, poss=side, arrive={s_i: k_i})
                if not self._reaches(s_i):
                    return self._run_on(full_path[:k_i + 2], side, kicker=c)
                self.log_ball("touch", s_i)
                if self.rng.random() < 0.45:
                    self.event("BallClaiming", "InterceptedBall", s_i, xy=self.ball[:2])
                self.carrier, self.poss = s_i, other(side)
                return "intercepted"
        if outcome == "out":
            return self._ball_out_flight(path, side, presser_point=point)
        T_, eff = self.live_targets(path[:, :2], side, press_point=point)
        T_[:, r] = point
        eff[r] = 2.0
        arrive = {r: n}
        spr = {r} if sprint_receiver else None
        dgk = self.roster.gk_slot(other(side))
        p_att = self.att(other(side), point)
        if kind in ("through", "high", "cross") and p_att[0] < -30.0 and abs(p_att[1]) < 22.0 \
                and dgk not in self.overrides:
            # the goalkeeper comes off his line towards the ball
            T_[:, dgk] = self.P[dgk] + 0.9 * (point - self.P[dgk])
            eff[dgk] = 2.0
            spr = (spr or set()) | {dgk}
        if outcome == "contested":
            opp = self._nearest(other(side), point, exclude_gk=not self._in_box(point, other(side)))
            # both meet the ball, one on each side of it (E-6: the winner's touch must be his, R-TOUCH-NEAREST)
            T_[:, opp] = point + CONTEST_OFFSET_M * self._own_goal_dir(other(side))
            T_[:, r] = point - CONTEST_OFFSET_M * self._own_goal_dir(other(side))
            eff[opp] = 2.0
            arrive[opp] = n
        self.advance(n, path, T_, eff, live=True, poss=side, arrive=arrive, sprint=spr)
        if outcome == "contested":
            near_r, near_o = self._reaches(r), self._reaches(opp)
            if not near_r and not near_o:
                return self._run_on(path, side, kicker=c)
            if near_r != near_o:  # only one of them gets there: no duel
                s_t = r if near_r else opp
                self.log_ball("touch", s_t)
                self.carrier, self.poss = s_t, self.side_of(s_t)
                if self.ball[2] > 0.4:
                    self.ball[2] = bp.GROUND_Z
                return "received" if near_r else "intercepted"
            air = self.ball[2] > 1.1
            if (not self._in_box(self.ball[:2], other(side)) and self.rng.random() < 0.04
                    and (self.director is None or not self.director.busy)):
                self.ball[2] = bp.GROUND_Z
                self.log_ball("touch", r)  # he reaches the ball and is fouled
                self.foul(opp, r)
                return "foul"
            win_att = self.rng.random() < (0.45 + 0.1 * self.bias(side))
            winner = r if win_att else opp
            if air and self.ball[2] < HEADER_Z_M[0]:
                air = False  # chest height: a ground duel, nobody heads it (E-6)
            if air:
                winner = self._aerial_winner(r, opp, winner)
                if winner is None:  # nobody can head it: the ball runs on
                    return self._run_on(path, side, kicker=c)
                win_att = winner == r
            self.duel_event("air" if air else "ground", r, opp, winner)
            self.log_ball("touch", winner)
            if air and not win_att:
                return self.clear(winner, header=True)
            self.carrier, self.poss = winner, self.side_of(winner)
            if self.ball[2] > 0.4:
                self.ball[2] = bp.GROUND_Z
            return "contested"
        if not self._reaches(r):
            return self._run_on(path, side, kicker=c)
        self.log_ball("touch", r)
        self.carrier, self.poss = r, side
        return "received"

    def _reaches(self, s: int, reach: float = RECEIVE_REACH_M) -> bool:
        """True when player s is within reach of the ball now (after the simulated frames)."""
        return float(np.hypot(*(self.P[s] - self.ball[:2]))) <= reach

    def _header_contact(self, s: int) -> bool:
        """True when player s can head the ball now: within HEADER_REACH_M of it, at a head height (HEADER_Z_M)."""
        z = float(self.ball[2])
        return (float(np.hypot(*(self.P[s] - self.ball[:2]))) <= HEADER_REACH_M
                and HEADER_Z_M[0] <= z <= HEADER_Z_M[1])

    def _aerial_winner(self, a: int, b: int, drawn: int) -> int | None:
        """The winner of an aerial duel between a and b (E-6): the drawn one when he can head the ball and the touch is
        his (R-TOUCH-NEAREST: not more than KICK_SPACE_TOL_AERIAL_M farther from the ball than the other), else the
        other one if he can, else None (nobody heads it)."""
        d = {s: float(np.hypot(*(self.P[s] - self.ball[:2]))) for s in (a, b)}
        for s in (drawn, b if drawn == a else a):
            if self._header_contact(s) and d[s] <= min(d.values()) + KICK_SPACE_TOL_AERIAL_M:
                return s
        return None

    def _reach_m(self, s: int, t: np.ndarray) -> np.ndarray:
        """Distance player s can cover in t seconds from his current speed towards the ball (acceleration ramp
        a0 (1 - v / vmax), as the controller), with a 0.15 s reaction."""
        a = self.arrays()
        vmax, a0 = float(a["vmax"][s]), float(a["a0"][s])
        tau = vmax / a0
        v0 = min(float(np.hypot(*self.V[s])) * 0.5, vmax)  # only part of his current speed points the right way
        tt = np.maximum(np.asarray(t, float) - 0.15, 0.0)
        return vmax * tt - (vmax - v0) * tau * (1.0 - np.exp(-tt / tau))

    def _run_on(self, path: np.ndarray, last_side: str, kicker: int | None = None) -> str:
        """The intended player did not reach the ball: it carries on (drag, bounces, rolling) as a loose ball,
        and whoever really gets to it first touches it."""
        v = (path[-1] - path[-2]) * FS if path.shape[0] > 1 else np.zeros(3)
        cont, _ = bp.fly(path[-1], v, int(4.0 * FS))
        return self.loose_ball(cont, last_side, kicker=kicker)

    def _kick_event(self, c: int, restart: Restart | None, inner: str, sub: str | None, attrs: dict) -> dict:
        """The event of a kick (pass or shot), created at the kick frame without its result; the kicker's touch is
        logged. A restart nests the action as DFL does (`FreeKick` with sub-type `Play` or `ShotAtGoal`)."""
        if restart is None:
            ev = self.event(inner, sub, c, xy=self.ball[:2], attrs=attrs)
        else:
            a = dict(attrs)
            a.update(self._restart_attrs(restart))
            ev = self.event(restart.kind, inner, c, xy=self.ball[:2], attrs=a)
        ev["resolve"] = {"what": "shot" if inner == "ShotAtGoal" else "pass", "kicker": self.pid(c),
                         "side": self.side_of(c), "nested": restart is not None}
        self.log_ball("touch", c)
        return ev

    def _restart_attrs(self, r: Restart) -> dict:
        a = {}
        if r.kind == "KickOff":
            a["GameSection"] = "firstHalf" if self.period == 1 else "secondHalf"
            left = "home" if self.dir["home"] > 0 else "away"
            a["TeamLeft"], a["TeamRight"] = self.team_id[left], self.team_id[other(left)]
        if r.kind == "CornerKick":
            # side of the corner as seen by the attacking team (facing the goal it attacks)
            a["Side"] = "left" if self.att(r.side, r.spot)[1] > 0 else "right"
        if r.kind == "FreeKick" and r.reason:
            a["Reason"] = r.reason
        if r.kind == "Penalty":
            a.update(r.info)
            if r.taker is not None:
                a["ProspectiveTaker"] = self.pid(r.taker)
        return a

    def _out_point(self, b0: np.ndarray, point: np.ndarray, out_side: str | None) -> np.ndarray:
        """A destination beyond the nearest line (touchline unless told otherwise)."""
        p = np.array(point, float)
        if out_side == "goal_line":
            sx = np.sign(p[0]) if p[0] != 0 else 1.0
            p[0] = sx * (self.pitch.hl + 3.0)
        else:
            sy = np.sign(p[1]) if p[1] != 0 else 1.0
            p[1] = sy * (self.pitch.hw + 3.5)
        return p

    def _ball_out_flight(self, path: np.ndarray, last_side: str, presser_point=None,
                         clear: tuple[int, int, float] | None = None) -> str:
        """Run the ball along `path` until it leaves the pitch; then dead ball and the right restart. `clear` as in
        `loose_ball`."""
        hl, hw = self.pitch.hl + BALL_RADIUS_M, self.pitch.hw + BALL_RADIUS_M
        out = np.nonzero((np.abs(path[:, 0]) > hl) | (np.abs(path[:, 1]) > hw))[0]
        if out.size == 0:  # stays in: the ball stops, nearest player claims
            return self.loose_ball(path, last_side)
        k = int(out[0])
        extra = min(int(0.8 * FS), path.shape[0] - k - 1)
        seg = path[:k + 1 + extra].copy()
        live = np.zeros(seg.shape[0], bool)
        live[:k + 1] = True
        live[k] = False  # the frame beyond the line is dead
        live[:k] = True
        T_, eff = self.live_targets(seg[:, :2], last_side, press_point=presser_point)
        if clear is not None:
            self._keep_clear(T_, seg, clear[0], clear[1], clear[2])
        f0 = self.f
        self.advance(seg.shape[0], seg, T_, eff, live=live, poss=last_side)
        self.log_ball("out", f=f0 + 1 + k, side=last_side, xy=path[k, :2])
        self._set_out_restart(path[k, :2], last_side)
        self.carrier = -1
        return "out"

    def _set_out_restart(self, xy: np.ndarray, last_side: str):
        x, y = float(xy[0]), float(xy[1])
        if abs(y) > self.pitch.hw and abs(x) <= self.pitch.hl:
            spot = np.array([np.clip(x, -self.pitch.hl + 1, self.pitch.hl - 1), np.sign(y) * (self.pitch.hw + 0.3)])
            self.pending = Restart("ThrowIn", other(last_side), spot)
            return
        sx = np.sign(x) if x != 0 else 1.0
        defending = next(s for s in SIDES if self.dir[s] == -sx)
        sy = np.sign(y) if y != 0 else 1.0
        if last_side == defending:
            spot = np.array([sx * (self.pitch.hl - 0.3), sy * (self.pitch.hw - 0.3)])
            self.pending = Restart("CornerKick", other(defending), spot)
        else:
            spot = np.array([sx * (self.pitch.hl - GOAL_AREA_DEPTH_M + 1.0), sy * float(self.rng.uniform(1.0, 7.5))])
            self.pending = Restart("GoalKick", defending, spot)

    def _keep_clear(self, T: np.ndarray, ball: np.ndarray, k_lo: int, k_hi: int, radius: float, exempt=()) -> None:
        """Move the targets T (n, 22, 2) of every player not in `exempt` at least `radius` from the ball positions
        `ball` (n, >= 2) in the frames k_lo..k_hi (clipped to the segment): a player whose target is near the ball
        steps away from it on the side where he stands now (he never walks through the ball to get there). The
        controller then moves him there smoothly (no jump)."""
        lo, hi = max(0, int(k_lo)), min(T.shape[0] - 1, int(k_hi))
        if hi < lo:
            return
        b = ball[lo:hi + 1, :2]
        for s in range(self.n):
            if s in exempt:
                continue
            d = T[lo:hi + 1, s] - b
            r = np.hypot(d[:, 0], d[:, 1])
            close = r < radius
            if not close.any():
                continue
            away = self.P[s] - b
            ra = np.hypot(away[:, 0], away[:, 1])
            u = np.where((ra > 1e-3)[:, None], away / np.maximum(ra, 1e-6)[:, None],
                         np.where((r > 1e-3)[:, None], d / np.maximum(r, 1e-6)[:, None], np.array([[1.0, 0.0]])))
            T[lo:hi + 1, s] = np.where(close[:, None], b + radius * u, T[lo:hi + 1, s])

    def _keep_off_segment(self, T: np.ndarray, a, b, radius: float, exempt=()) -> None:
        """Move the targets T (n, 22, 2) of every player not in `exempt` at least `radius` from the segment a-b (the
        first metres of a kick's path), sideways: a player standing in the lane steps out of it."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        ab = b - a
        L2 = float(ab @ ab)
        if L2 < 1e-9:
            return
        u = ab / math.sqrt(L2)
        perp = np.array([-u[1], u[0]])
        for s in range(self.n):
            if s in exempt:
                continue
            rel = T[:, s] - a
            t = np.clip(rel @ ab / L2, 0.0, 1.0)
            off = rel - t[:, None] * ab[None, :]
            r = np.hypot(off[:, 0], off[:, 1])
            close = r < radius
            if not close.any():
                continue
            side = np.sign(off @ perp)
            side = np.where(side == 0, np.sign(float((self.P[s] - a) @ perp)) or 1.0, side)
            foot = a[None, :] + t[:, None] * ab[None, :]
            T[:, s] = np.where(close[:, None], foot + radius * side[:, None] * perp[None, :], T[:, s])

    def _nearest(self, side: str, xy, exclude: set | None = None, exclude_gk: bool = True) -> int:
        best, bd = None, 1e9
        for s in self.side_slots[side]:
            if exclude and s in exclude:
                continue
            if exclude_gk and self.roster.slot_role[s] == "GK":
                continue
            d = float(np.hypot(*(self.P[s] - xy)))
            if d < bd:
                best, bd = s, d
        return best

    def _in_box(self, xy, defending_side: str) -> bool:
        """True when xy is inside the penalty area that `defending_side` defends."""
        x_att = self.att(other(defending_side), xy)
        return x_att[0] > self.pitch.hl - PENALTY_AREA_DEPTH_M and abs(x_att[1]) < PENALTY_AREA_HALF_WIDTH_M

    def loose_ball(self, path: np.ndarray, last_side: str, kicker: int | None = None, prefer_side: str | None = None,
                   events_claim: bool = True, depth: int = 0, k_from: int = 6,
                   clear: tuple[int, int, float] | None = None) -> str:
        """A ball nobody controls (clearance, deflection, rebound): the first player who can reach it claims it,
        or it leaves the pitch. `clear` = (first, last frame of `path`, radius): in those frames every player keeps
        that far from the ball (the woodwork contact)."""
        hl, hw = self.pitch.hl + BALL_RADIUS_M, self.pitch.hw + BALL_RADIUS_M
        path = self._steer_past_goal(path)  # an unscripted loose ball never ends in a goal
        out = np.nonzero((np.abs(path[:, 0]) > hl) | (np.abs(path[:, 1]) > hw))[0]
        k_out = int(out[0]) if out.size else path.shape[0]
        a = self.arrays()
        best = None
        k_min = max(6, int(k_from))  # nobody touches the ball before k_from (e.g. before it hits the post)
        ks = np.arange(k_min, k_out)
        for s in range(self.n):
            if prefer_side is not None and self.side_of(s) != prefer_side:
                continue
            if s == kicker:
                continue
            is_gk = self.roster.slot_role[s] == "GK"
            low = path[ks, 2] < (2.5 if is_gk else 1.9)
            if is_gk:  # the goalkeeper claims the ball only while it is inside his own penalty area
                d_att = self.dir[other(self.side_of(s))]
                xa, ya = d_att * path[ks, 0], d_att * path[ks, 1]
                low = low & (xa > self.pitch.hl - PENALTY_AREA_DEPTH_M) & (np.abs(ya) < PENALTY_AREA_HALF_WIDTH_M) \
                    & (xa < self.pitch.hl)
                if not low.any():
                    continue
            d = np.hypot(path[ks, 0] - self.P[s, 0], path[ks, 1] - self.P[s, 1])
            need = d / (0.8 * a["vmax"][s]) + 0.25
            ok = np.nonzero(low & (need <= ks * DT))[0]
            if ok.size:
                k = int(ks[ok[0]])
                if best is None or k < best[1] or (k == best[1] and d[ok[0]] < best[2]):
                    best = (s, k, float(d[ok[0]]))
        if best is None and k_out >= path.shape[0]:
            # the ball stops on the pitch: the nearest player walks to it
            stop = path[-1].copy()
            pool = [s for s in range(self.n) if (prefer_side is None or self.side_of(s) == prefer_side)
                    and self.roster.slot_role[s] != "GK"]
            s = min(pool, key=lambda q: float(np.hypot(*(self.P[q] - stop[:2]))))
            extra = int(np.hypot(*(self.P[s] - stop[:2])) / 5.0 * FS) + 5
            path = np.vstack([path, np.repeat(stop[None, :], extra, axis=0)])
            best = (s, path.shape[0] - 1, 0.0)
        if best is None:
            return self._ball_out_flight(path, last_side, clear=clear)
        s, k, _ = best
        seg = path[:k + 1]
        T_, eff = self.live_targets(seg[:, :2], last_side, press_point=seg[-1, :2])
        T_[:, s] = seg[-1, :2]
        eff[s] = 2.0
        arrive = {s: k + 1}
        gk_sprint = {s} if self.roster.slot_role[s] == "GK" else None
        rival = None
        if prefer_side is None:
            opp = self._nearest(other(self.side_of(s)), seg[-1, :2])
            if opp is not None and np.hypot(*(self.P[opp] - seg[-1, :2])) < 0.8 * a["vmax"][opp] * (k + 1) * DT * 0.9:
                rival = opp
                T_[:, opp] = seg[-1, :2] + 0.8 * self._own_goal_dir(self.side_of(opp))
                eff[opp] = 2.0
                arrive[opp] = k + 1
        if clear is not None:
            self._keep_clear(T_, seg, clear[0], clear[1], clear[2])
        self.advance(seg.shape[0], seg, T_, eff, live=True, poss=self.poss, arrive=arrive, sprint=gk_sprint)
        if not self._reaches(s) and not (rival is not None and self._reaches(rival)) and depth < 6:
            # he did not get there in time: the ball rolls on and the chase goes on
            rest = path[k + 1:]
            if rest.shape[0] < 8:
                rest = np.repeat(self.ball[None, :], 8, axis=0) if rest.shape[0] == 0 else \
                    np.vstack([rest, np.repeat(rest[-1:], 8, axis=0)])
            return self.loose_ball(rest, last_side, prefer_side=prefer_side, events_claim=events_claim,
                                   depth=depth + 1)
        if not self._reaches(s) and rival is not None and self._reaches(rival):
            s, rival = rival, None
        if self.ball[2] > 0.5:
            self.ball[2] = bp.GROUND_Z
        if rival is not None and not self._reaches(rival):
            rival = None
        if rival is not None and self.rng.random() < 0.5:
            air = seg[-1, 2] > 1.1
            win = s if self.rng.random() < 0.55 else rival
            ev = self.duel_event("air" if air else "ground", s, rival, win)
            ev["attributes"]["WinnerRole"] = "withoutBall"  # a loose ball: nobody had it
            ev["resolve"]["loose"] = True
            s = win
        elif events_claim and self.rng.random() < 0.3:
            self.event("BallClaiming", "BallClaimed", s, xy=self.ball[:2])
        self.log_ball("touch", s)
        self.carrier, self.poss = s, self.side_of(s)
        return "claimed"

    def clear(self, c: int, header: bool = False, aim=None, out: str | None = None) -> str:
        """Defensive clearance (OtherBallAction, DefensiveClearance): a long high ball, then a loose ball."""
        side = self.side_of(c)
        rel = None
        if aim is None:
            fwd = float(self.rng.uniform(22.0, 45.0)) if not header else float(self.rng.uniform(10.0, 22.0))
            lat = float(self.rng.normal(0.0, 12.0))
            rel = self.att(side, np.array([fwd, lat]))
        if not header:  # room to clear (E-6); a header meets the ball in the air
            self._make_kick_room(c, lambda: self._straight_path(self.ball[:2] + rel if rel is not None else aim, 20.0))
        b0 = self.ball.copy()
        if rel is not None:
            aim = self.pitch.clamp(b0[:2] + rel, margin=4.0 if out else -1.0)
        aim = np.asarray(aim, float)
        if out:
            aim = self._out_point(b0[:2], aim, out)
        D = float(np.hypot(*(aim - b0[:2])))
        z0 = float(b0[2]) if header else bp.GROUND_Z
        lo, hi = self.cal.ball.clearance_speed_ms
        speed = float(self.rng.uniform(*((9.0, 15.0) if header else (lo, hi))))
        T = bp.flight_time_for_speed(D, z0, bp.GROUND_Z, speed)
        T = max(T, 0.6 if header else 1.2)
        vh0, vz0 = bp.air_plan(D, T, z0, bp.GROUND_Z)
        n = max(2, int(math.ceil(T / DT)))
        path, vel = bp.aerial(np.array([b0[0], b0[1], z0]), aim, vh0, vz0, n)
        after, _ = bp.fly(path[-1], np.array([vel[0], vel[1], -vel[2] * bp.BOUNCE_RESTITUTION]), int(4.0 * FS))
        full = np.vstack([path, after])
        self.event("OtherBallAction", "Clearance", c, xy=b0[:2],
                   attrs={"DefensiveClearance": True, "Height": "high", "TypeOfShot": "head" if header else None})
        self.log_ball("touch", c)
        if out:
            return self._ball_out_flight(full, side)
        return self.loose_ball(full, side, kicker=c)

    def duel(self, att: int, dfd: int, winner: str, approach_s: float = 0.9, attrs: dict | None = None,
             foul: bool = False, carry_dest=None) -> str:
        """Ground duel (TacklingGame): the defender closes in on the carrier; winner 'att' or 'def'."""
        side = self.side_of(att)
        gap = float(np.hypot(*(self.P[dfd] - self.P[att])))
        approach_s = max(approach_s, gap / (0.6 * float(self.arrays()["vmax"][dfd])) + 0.6)
        n = max(3, int(approach_s * FS))
        self.last_duel_f = self.f + n
        dest = carry_dest if carry_dest is not None else self.P[att] + self.att(side, np.array([2.0, 0.0])) * 0.6
        self.hold(att, n * DT, dest=dest, speed=2.5, presser=dfd, presser_arrive=True)
        if foul:
            return "foul"
        w = att if winner == "att" else dfd
        a = {"DribbleEvaluation": "successful" if w == att else "unsuccessful"}
        a.update(attrs or {})
        if w == att and not self.plan.scripted and self.rng.random() < 0.08:
            self.event("Nutmeg", None, att, xy=self.ball[:2], attrs={"Opponent": self.pid(dfd)})
        self.duel_event("ground", att, dfd, w, attrs=a)
        self.log_ball("touch", w)
        self.carrier, self.poss = w, self.side_of(w)
        if (w != att and abs(self.ball[1]) > self.pitch.hw - 12.0 and self.rng.random() < 0.45
                and self.director is not None and not self.director.busy):
            # the tackle knocks the ball out of play off the attacker
            sy = np.sign(self.ball[1])
            aim = np.array([self.ball[0] + float(self.rng.normal(0, 3)), sy * (self.pitch.hw + 3.0)])
            path = bp.rolling(self.ball, aim, float(self.rng.uniform(5.0, 8.0)), int(2.5 * FS))
            return self._ball_out_flight(path, side)
        return "won" if w == att else "lost"

    def foul(self, fouler: int, fouled: int, extras: list | None = None, min_dead_s: float | None = None) -> str:
        self.event("Foul", None, fouler, xy=self.ball[:2],
                   attrs={"Fouler": self.pid(fouler), "Fouled": self.pid(fouled),
                          "TeamFouler": self.team_id[self.side_of(fouler)], "TeamFouled": self.team_id[self.side_of(fouled)]})
        self.log_ball("foul", side=self.side_of(fouled))
        spot = self.pitch.clamp(self.ball[:2].copy(), margin=-0.5)
        danger = self._danger(spot, self.side_of(fouled))
        r = Restart("FreeKick", self.side_of(fouled), spot, danger=danger, reason="foul", extras=list(extras or []))
        if min_dead_s:
            r.duration_s = max(min_dead_s, self._dead_duration("FreeKickDanger" if danger else "FreeKick"))
        self.pending = r
        self.carrier = -1
        return "foul"

    def penalty_foul(self, fouler: int, fouled: int, taker: int | None = None, duration_s: float | None = None,
                     extras: list | None = None) -> Restart:
        """A foul inside the penalty area: `Foul`, then a `Penalty` restart from the spot (as DFL: the restart event
        nests the shot, `Penalty` > `ShotAtGoal` > result)."""
        side = self.side_of(fouled)
        self.event("Foul", None, fouler, xy=self.ball[:2],
                   attrs={"Fouler": self.pid(fouler), "Fouled": self.pid(fouled),
                          "TeamFouler": self.team_id[self.side_of(fouler)], "TeamFouled": self.team_id[side]})
        self.log_ball("foul", side=side)
        gx = self.goal_x(side)
        spot = np.array([gx - np.sign(gx) * PENALTY_SPOT_M, 0.0])
        r = Restart("Penalty", side, spot, duration_s=duration_s, taker=taker, reason="foul", extras=list(extras or []))
        r.info = {"CausingPlayer": self.pid(fouler), "FouledPlayer": self.pid(fouled)}
        self.pending = r
        self.carrier = -1
        return r

    def _danger(self, spot, side: str) -> bool:
        g = np.array([self.goal_x(side), 0.0])
        return float(np.hypot(*(spot - g))) < 32.0

    # ------------------------------------------------------------------ shots

    def shoot(self, c: int, outcome: str, target=None, speed: float | None = None, body: str | None = None,
              restart: Restart | None = None, post: str | None = None, save: str | None = None,
              blocker: int | None = None, attrs: dict | None = None) -> str:
        """Shot at goal. outcome is the intent (goal | saved | wide | blocked | woodwork): it chooses the aim and the
        executors that move the goalkeeper or a defender. The event's result is written after the simulation from
        what the ball does (outcomes.resolve_outcomes)."""
        side = self.side_of(c)
        dside = other(side)
        gx = self.goal_x(side)
        sx = np.sign(gx)
        if restart is None and body != "head":  # room to shoot (E-6); a header meets the ball in the air
            aim = np.asarray(target, float)[:2] if target is not None else np.array([gx, 0.0])
            self._make_kick_room(c, lambda: self._straight_path(aim, speed if speed is not None else 22.0))
        b0 = self.ball.copy()
        if b0[2] < bp.GROUND_Z:
            b0[2] = bp.GROUND_Z
        gk = self.roster.gk_slot(dside)
        lo, hi = self.cal.ball.header_speed_ms if body == "head" else self.cal.ball.shot_speed_ms
        if speed is None:
            speed = float(self.rng.uniform(lo, hi))
        if body is None:
            foot = self.roster.info(c).foot
            body = "leftLeg" if foot == "left" else "rightLeg"
        gk_y = float(self.P[gk, 1])
        if target is None:
            if outcome == "goal":
                y = -np.sign(gk_y - b0[1] * 0.2 + 1e-6) * float(self.rng.uniform(1.6, 3.2))
                target = np.array([gx, y, float(self.rng.uniform(0.25, 1.9))])
            elif outcome == "saved":
                y = np.clip(gk_y + float(self.rng.normal(0, 1.0)), -3.2, 3.2)
                target = np.array([gx, y, float(self.rng.uniform(0.3, 1.8))])
            elif outcome == "wide":
                if self.rng.random() < 0.3:
                    target = np.array([gx, float(self.rng.uniform(-3.0, 3.0)), float(self.rng.uniform(2.75, 4.2))])
                else:
                    target = np.array([gx, float(np.sign(self.rng.normal()) * self.rng.uniform(4.3, 8.0)),
                                       float(self.rng.uniform(0.2, 2.0))])
            elif outcome == "woodwork":
                py = (GOAL_HALF_WIDTH_M + POST_RADIUS_M) * (1 if post == "left_att" else -1) * self.dir[side]
                if post is None:
                    py = (GOAL_HALF_WIDTH_M + POST_RADIUS_M) * (np.sign(b0[1]) if b0[1] != 0 else 1.0)
                # the outer half of the post: the ball comes back out, away from the goal mouth and the keeper
                target = np.array([gx, py + np.sign(py) * WOODWORK_AIM_OUT_M, float(self.rng.uniform(0.6, 1.6))])
            else:  # blocked: struck through a lane where a defender stands (he still has to reach it)
                target = np.array([gx, float(self.rng.uniform(-2.5, 2.5)), float(self.rng.uniform(0.3, 1.5))])
                lane = self._shooting_lane_defender(b0[:2], gx, dside)
                if lane is not None:
                    blocker = lane
                    q = self.P[lane]
                    if abs(q[0] - b0[0]) > 0.5:
                        y = b0[1] + (q[1] - b0[1]) * (gx - b0[0]) / (q[0] - b0[0])
                        target[1] = float(np.clip(y, -6.0, 6.0))
        target = np.asarray(target, float)
        vh0, vz0, T = bp.plan_shot(b0, target, speed)
        dist_goal = float(np.hypot(*(b0[:2] - np.array([gx, 0.0]))))
        angle = self._shot_angle(b0[:2], gx)
        a = {"TypeOfShot": body, "DistanceToGoal": round(dist_goal, 1), "InsideBox": bool(self._in_box(b0[:2], dside)),
             "xG": round(self.xg(dist_goal, angle, body == "head", self._opp_in_triangle(b0[:2], gx, dside)), 3),
             "Goalkeeper": self.pid(gk)}
        a.update(attrs or {})
        self._kick_event(c, restart, "ShotAtGoal", None, a)
        n_line = int(math.ceil(T / DT))
        if outcome == "woodwork":
            post_xy = np.array([gx - sx * 0.0, np.sign(target[1]) * (GOAL_HALF_WIDTH_M + POST_RADIUS_M)])
            hit = bp.post_contact(b0, target, vh0, vz0, post_xy, T + 0.3)
            if hit is None:
                outcome = "wide"
            else:
                tc, pc, vc = hit
                k_c = max(1, int(math.floor(tc / DT)))
                path, _ = bp.aerial(b0, target, vh0, vz0, k_c)
                vnew = bp.reflect_on_post(pc, vc, post_xy)
                dt_rest = (k_c + 1) * DT - tc
                p1 = pc + vnew * dt_rest
                after, _ = bp.fly(p1, vnew, int(3.5 * FS))
                full = np.vstack([path, after])
                self.woodwork_frames.append((self.period, self.f, self.f + k_c + 1, post_xy.copy()))
                self.log_ball("post", f=self.f + k_c + 1, side=side, xy=pc[:2])
                # nobody near the ball at the contact, nobody on the rebound before WOODWORK_FREE_S (R-WOODWORK)
                clear = (k_c - int(round(WOODWORK_CLEAR_BEFORE_S * FS)), k_c + 1 + int(round(WOODWORK_CLEAR_AFTER_S * FS)),
                         WOODWORK_CLEAR_M)
                return self.loose_ball(full, side, kicker=c, prefer_side=dside,
                                       k_from=k_c + 1 + int(round(WOODWORK_FREE_S * FS)), clear=clear)
        if outcome == "goal":
            path, vel = bp.aerial(b0, target, vh0, vz0, n_line + 1)
            return self._goal_flight(c, path, vel, target, 0)
        if outcome == "saved":
            path, vel = bp.aerial(b0, target, vh0, vz0, n_line + 1)
            g = self.P[gk].copy()
            d = np.hypot(path[:, 0] - g[0], path[:, 1] - g[1])
            # the ball reaches the goalkeeper (closest approach, always in front of the goal line)
            in_front = np.abs(path[:, 0]) < self.pitch.hl - 0.4
            in_front[0] = False
            d = np.where(in_front, d, np.inf)
            if not np.isfinite(d[: n_line + 1]).any():
                # point-blank: no room for a save in front of the line, the shot is dragged wide instead
                return self._ball_out_flight(self._wide_path(b0, gx, speed), side)
            # the save point: the first point in front of the line the goalkeeper can reach (hands about 1.5 m
            # from his centre), else the closest approach if he is already there
            ks = np.arange(1, n_line + 1)
            # a scripted save (save given) aims at the keeper: he may use his full reach
            margin, factor = (1.4, 1.0) if save is not None else (1.1, 0.85)
            ok = ks[np.isfinite(d[ks]) & (d[ks] - margin <= factor * self._reach_m(gk, ks * DT))]
            if ok.size == 0:  # he cannot get there: the shot is dragged wide instead
                return self._ball_out_flight(self._wide_path(b0, gx, speed), side)
            k_s = int(ok[0]) if d[int(ok[0])] > 1.5 else int(np.argmin(d[: n_line + 1]))
            spt = path[k_s]
            gap = spt[:2] - g
            dist = float(np.hypot(*gap))
            T_, eff = self.live_targets(path[:k_s + 1, :2], side)
            T_[:, gk] = g + (gap * max(dist - 1.0, 0.0) / dist if dist > 1e-6 else 0.0)
            eff[gk] = 2.0
            self.advance(k_s + 1, path[:k_s + 1], T_, eff, live=True, poss=side, arrive={gk: k_s + 1},
                         sprint={gk})
            if not self._reaches(gk, GK_REACH_M):  # late: he keeps chasing the ball frame by frame
                k = k_s + 1
                while k < path.shape[0] and abs(path[k, 0]) <= self.pitch.hl + BALL_RADIUS_M \
                        and not self._reaches(gk, GK_REACH_M):
                    T1, e1 = self.live_targets(path[k:k + 1, :2], side)
                    T1[:, gk] = path[k, :2]
                    e1[gk] = 2.0
                    self.advance(1, path[k:k + 1], T1, e1, live=True, poss=side, sprint={gk})
                    k += 1
                if not self._reaches(gk, GK_REACH_M):  # he never gets there
                    rest, vrest = bp.aerial(b0, target, vh0, vz0, n_line + int(1.0 * FS))
                    rest[:, 2] = np.maximum(rest[:, 2], bp.GROUND_Z)
                    if self._mouth_bound(rest):
                        return self._goal_flight(c, rest, vrest, target, k)
                    return self._ball_out_flight(rest[k:], side)
                spt = self.ball.copy()
            self.log_ball("touch", gk)
            caught = (save == "catch") or (save is None and speed < 24.0 and 0.3 < spt[2] < 2.0 and self.rng.random() < 0.55)
            if caught and save != "parry_corner":
                self.event("BallClaiming", "BallClaimed", gk, xy=self.ball[:2], attrs={"Type": "caught"})
                self.carrier, self.poss = gk, dside
                return "caught"
            vin = np.array([vel[0], vel[1], 0.0])
            if save is None and self.rng.random() < 0.35:
                save = "parry_corner"
            if save == "parry_corner":
                vout = self._behind_the_goal(spt, float(self.rng.uniform(6.0, 10.0)), float(self.rng.uniform(1.0, 3.5)))
            else:
                vout = np.array([-sx * (0.35 * abs(vin[0]) + abs(float(self.rng.normal(0, 2)))),
                                 float(self.rng.normal(0, 5.0)), float(self.rng.uniform(0.5, 3.5))])
            after, _ = bp.fly(spt, vout, int(3.5 * FS))
            return self.loose_ball(after, dside, kicker=gk)
        if outcome == "blocked":
            path, vel = bp.aerial(b0, target, vh0, vz0, n_line)
            if blocker is None:
                blocker = self._nearest(dside, b0[:2] + 0.25 * (target[:2] - b0[:2]))
            # the block: the first point of the flight an outfield defender can really reach (the chosen one first)
            ks = np.arange(BLOCK_MIN_FRAMES, max(BLOCK_MIN_FRAMES + 1, n_line // 2))
            cands = [blocker] + [s for s in self.side_slots[dside] if s != blocker and self.roster.slot_role[s] != "GK"]
            best = None
            for s_b in cands:
                d = np.hypot(path[ks, 0] - self.P[s_b, 0], path[ks, 1] - self.P[s_b, 1])
                ok = ks[(d - 1.3 <= 0.9 * self._reach_m(s_b, ks * DT)) & (path[ks, 2] < 1.9)]
                if ok.size and (best is None or int(ok[0]) < best[1] - 2):
                    best = (s_b, int(ok[0]))
            if best is None:  # nobody can get in the way: the shot is dragged wide instead
                return self._ball_out_flight(self._wide_path(b0, gx, speed), side)
            blocker, k_b = best
            T_, eff = self.live_targets(path[:k_b + 1, :2], side)
            off = self.P[blocker] - path[k_b, :2]  # he blocks with a leg or the body: his centre stays BLOCK_BODY_M off
            off_n = float(np.hypot(*off))
            T_[:, blocker] = path[k_b, :2] + (BLOCK_BODY_M * off / off_n if off_n > 1e-6 else 0.0)
            eff[blocker] = 2.0
            self.advance(k_b + 1, path[:k_b + 1], T_, eff, live=True, poss=side, arrive={blocker: k_b + 1})
            if not self._reaches(blocker, BLOCK_REACH_M):  # he does not get there: the shot goes on past him
                rest, _ = bp.aerial(b0, target, vh0, vz0, n_line + int(1.0 * FS))
                rest[:, 2] = np.maximum(rest[:, 2], bp.GROUND_Z)
                return self._ball_out_flight(self._wide_path(self.ball, gx, speed), side) \
                    if self._mouth_bound(rest) else self._ball_out_flight(rest[k_b + 1:], side)
            self.log_ball("touch", blocker)
            ang = float(self.rng.uniform(0, 2 * math.pi))
            spd = float(self.rng.uniform(3.0, 9.0))
            vout = np.array([math.cos(ang) * spd - sx * 2.0, math.sin(ang) * spd, float(self.rng.uniform(0.0, 3.0))])
            if self.rng.random() < 0.25:  # deflected behind for a corner
                vout = self._behind_the_goal(self.ball, float(self.rng.uniform(8.0, 14.0)), float(self.rng.uniform(0.5, 4.0)))
            else:
                vout = self._away_from_goal_mouth(self.ball, vout)
            after, _ = bp.fly(self.ball, vout, int(3.5 * FS))
            return self.loose_ball(after, dside, kicker=blocker)
        # wide (or a woodwork shot that missed): over the line, goal kick
        n_tot = n_line + int(1.0 * FS)
        path, _ = bp.aerial(b0, target, vh0, vz0, n_tot)
        path[:, 2] = np.maximum(path[:, 2], bp.GROUND_Z)
        leaves = bool(np.any((np.abs(path[:, 0]) > self.pitch.hl + BALL_RADIUS_M)
                             | (np.abs(path[:, 1]) > self.pitch.hw + BALL_RADIUS_M)))
        if not leaves or self._mouth_bound(path):  # e.g. a header struck along the goal line: dragged wide instead
            path = self._wide_path(b0, gx, speed)
        return self._ball_out_flight(path, side)

    def _shooting_lane_defender(self, b: np.ndarray, gx: float, dside: str) -> int | None:
        """The outfield defender in the shooting lane (LANE_ALONG_M from the ball along the line to the goal centre,
        within LANE_OFF_M of it), closest to that line; None when nobody stands there."""
        best = None
        g = np.array([gx, 0.0])
        u = (g - b) / max(float(np.hypot(*(g - b))), 1e-6)
        for s in self.side_slots[dside]:
            if self.roster.slot_role[s] == "GK":
                continue
            v = self.P[s] - b
            along = float(v @ u)
            off = abs(float(v[0] * u[1] - v[1] * u[0]))
            if (LANE_ALONG_M[0] <= along <= LANE_ALONG_M[1] and off <= LANE_OFF_M[0] + LANE_OFF_M[1] * along
                    and (best is None or off < best[0])):
                best = (off, s)
        return None if best is None else best[1]

    def _steer_past_goal(self, path: np.ndarray) -> np.ndarray:
        """A loose ball whose path would enter a goal mouth runs on past the post instead (goal kick or corner):
        its lateral launch speed is changed so that it crosses the goal line at least 2 m outside the post."""
        for attempt in range(4):
            if path.shape[0] < 2 or not self._mouth_bound(path):
                return path
            beyond = np.nonzero(np.abs(path[:, 0]) > self.pitch.hl + BALL_RADIUS_M)[0]
            k = int(beyond[0])
            q = path[k]
            sy = 1.0 if q[1] >= 0 else -1.0
            v0 = (path[1] - path[0]) * FS
            v0[1] += (sy * (GOAL_HALF_WIDTH_M + 2.0 + attempt) - q[1]) / max(k * DT, DT) * (1.0 + 0.3 * attempt)
            path, _ = bp.fly(path[0], v0, path.shape[0])
        if self._mouth_bound(path):  # last resort (a slow ball rolling in): it stops just in front of the line
            k = int(np.nonzero(np.abs(path[:, 0]) > self.pitch.hl + BALL_RADIUS_M)[0][0])
            stop = path[max(k - 1, 0)].copy()
            stop[0] = np.sign(stop[0]) * min(abs(float(stop[0])), self.pitch.hl - 0.3)
            stop[2] = bp.GROUND_Z
            path = path.copy()
            path[max(k - 1, 0):] = stop
        return path

    def _mouth_bound(self, path: np.ndarray) -> bool:
        """True when the path enters a goal mouth (it would be an unscripted goal)."""
        beyond = np.nonzero(np.abs(path[:, 0]) > self.pitch.hl + BALL_RADIUS_M)[0]
        if beyond.size == 0:
            return False
        q = path[int(beyond[0])]
        return abs(q[1]) < GOAL_HALF_WIDTH_M + 0.3 and q[2] < 2.6

    def _goal_flight(self, c: int, path: np.ndarray, vel: np.ndarray, target: np.ndarray, k0: int) -> str:
        """The ball of `path` (from index k0 on) crosses the goal line between the posts: a goal."""
        side = self.side_of(c)
        dside = other(side)
        gx = self.goal_x(side)
        gk = self.roster.gk_slot(dside)
        beyond = np.nonzero(np.abs(path[:, 0]) > self.pitch.hl + BALL_RADIUS_M)[0]
        k_cross = int(beyond[0]) if beyond.size else path.shape[0] - 1
        net, _ = bp.fly(path[k_cross], vel, int(2.0 * FS), net_x=gx)
        full = np.vstack([path[k0:k_cross + 1], net])
        kc = k_cross - k0
        live = np.zeros(full.shape[0], bool)
        live[:kc + 1] = True
        T_, eff = self.live_targets(full[:, :2], side)
        T_[:, gk] = self.P[gk] + np.clip(target[:2] - self.P[gk], -0.9, 0.9) * np.array([0.3, 1.0])
        eff[gk] = 2.0
        self.advance(full.shape[0], full, T_, eff, live=live, poss=side, arrive={gk: min(kc, full.shape[0] - 1)})
        f_cross = self.f - full.shape[0] + kc + 1
        self.score[side] += 1
        self.goal_frames.append((self.period, f_cross, side, self.pid(c)))
        self.log_ball("goal", f=f_cross, side=side, xy=full[kc, :2])
        self.pending = Restart("KickOff", dside, np.array([0.0, 0.0]), celebrate=c)
        self.carrier = -1
        return "goal"

    def _wide_path(self, b0: np.ndarray, gx: float, speed: float) -> np.ndarray:
        """A shot that goes past the post (used when a point-blank save is impossible)."""
        sy = 1.0 if b0[1] >= 0 else -1.0
        target = np.array([gx + np.sign(gx) * 3.0, sy * float(self.rng.uniform(5.0, 7.0)), 0.6])
        vh0, vz0, T = bp.plan_shot(b0, target, speed)
        n = int(math.ceil(T / DT)) + int(0.8 * FS)
        path, _ = bp.aerial(b0, target, vh0, vz0, n)
        path[:, 2] = np.maximum(path[:, 2], bp.GROUND_Z)
        return path

    def _behind_the_goal(self, p: np.ndarray, speed: float, vz: float) -> np.ndarray:
        """Velocity that takes the ball over the goal line outside the posts (deflection for a corner)."""
        sx = 1.0 if p[0] > 0 else -1.0
        sy = 1.0 if p[1] >= 0 else -1.0
        y_aim = float(self.rng.uniform(10.0, 15.0))
        frac = max(self.pitch.hl - abs(float(p[0])), 0.05) / max(self.pitch.hl + 1.0 - abs(float(p[0])), 0.05)
        # close to the goal line (a parried penalty) the ball must still cross it well outside the post
        y_aim = max(y_aim, abs(float(p[1])) + (GOAL_HALF_WIDTH_M + 1.8 - abs(float(p[1]))) / frac)
        aim = np.array([sx * (self.pitch.hl + 1.0), sy * y_aim])
        d = aim - p[:2]
        d /= np.hypot(*d)
        return np.array([d[0] * speed, d[1] * speed, vz])

    def _away_from_goal_mouth(self, p: np.ndarray, v: np.ndarray) -> np.ndarray:
        """Turn a deflection that would roll into the goal so that it passes outside the post."""
        v = np.array(v, float)
        if abs(v[0]) < 1e-6:
            return v
        gx = np.sign(v[0]) * self.pitch.hl
        t = (gx - p[0]) / v[0]
        if t <= 0:
            return v
        y_line = p[1] + v[1] * t
        if abs(y_line) < GOAL_HALF_WIDTH_M + 1.0:
            want = (1.0 if y_line >= 0 else -1.0) * (GOAL_HALF_WIDTH_M + 2.5)
            v[1] = (want - p[1]) / t
        return v

    def _shot_angle(self, xy, gx: float) -> float:
        a1 = math.atan2(GOAL_HALF_WIDTH_M - xy[1], abs(gx - xy[0]) + 1e-6)
        a2 = math.atan2(-GOAL_HALF_WIDTH_M - xy[1], abs(gx - xy[0]) + 1e-6)
        return abs(a1 - a2)

    def _opp_in_triangle(self, xy, gx: float, dside: str) -> int:
        a, b, c = np.asarray(xy, float), np.array([gx, GOAL_HALF_WIDTH_M]), np.array([gx, -GOAL_HALF_WIDTH_M])

        def cross(o, p, q):
            return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
        n = 0
        for s in self.side_slots[dside]:
            if self.roster.slot_role[s] == "GK":
                continue
            pt = self.P[s]
            d1, d2, d3 = cross(a, b, pt), cross(b, c, pt), cross(c, a, pt)
            if not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0)):
                n += 1
        return n

    def xg(self, dist: float, angle: float, header: bool, opponents: int = 0) -> float:
        e = self.cal.events
        z = (e.xg_b0 + e.xg_b_dist * dist + e.xg_b_angle * angle + (e.xg_b_head if header else 0.0)
             + e.xg_b_opp * opponents)
        return 1.0 / (1.0 + math.exp(-z))

    # ------------------------------------------------------------------ dead ball

    def _dead_duration(self, key: str) -> float:
        med = DEAD_MEDIAN_S[key]
        base = med * math.exp(float(self.rng.normal(0, 0.3)))
        _, intensity = self.momentum()
        if intensity > 1.15:
            base *= 0.72
        # keep the ball-in-play share near its target
        if self.total_frames > 300 * FS:
            share = self.live_frames / self.total_frames
            base *= float(np.clip(math.exp(10.0 * (share - self.cal.events.ball_in_play_share)), 0.55, 2.0))
        return float(np.clip(base, 4.0, 120.0))

    def layout(self, r: Restart, taker: int) -> np.ndarray:
        """Static targets (22, 2) for a dead-ball phase before restart r."""
        spot = r.spot
        T = np.empty((self.n, 2))
        for side in SIDES:
            slots = self.side_slots[side]
            ip = np.full(len(slots), side == r.side)
            ball_att = self.att(side, spot)[None, :]
            t_att = self.posmodel.targets([self.roster.slot_role[s] for s in slots], ip, ball_att)[0]
            if r.kind == "KickOff":
                t_att[:, 0] = np.minimum(t_att[:, 0] * 0.7 - 6.0, -1.0)
                t_att[:, 1] *= 0.95
            T[slots] = self.att(side, t_att)
        if r.kind == "KickOff":
            mates = [s for s in self.side_slots[r.side] if s != taker and self.roster.slot_role[s] != "GK"]
            partner = min(mates, key=lambda s: (0.0 if self.roster.slot_role[s] in ("ST", "LS", "RS", "CAM") else 10.0)
                          + float(np.hypot(*T[s])))
            T[partner] = self.att(r.side, np.array([-0.6, 2.5]))
            for side in SIDES:  # outside the centre circle unless kicking off
                for s in self.side_slots[side]:
                    if s in (taker, partner) and side == r.side:
                        continue
                    if np.hypot(*T[s]) < 9.6:
                        T[s] = T[s] / max(np.hypot(*T[s]), 1e-6) * 9.8 if np.hypot(*T[s]) > 0.5 else self.att(side, np.array([-10.0, 0.0]))
        if r.kind == "CornerKick":
            att, dfd = r.side, other(r.side)
            gx = self.goal_x(att)
            box_pts = [(-3.0, -2.0), (-6.0, 1.5), (-9.0, -4.5), (-11.0, 3.5), (-7.5, 6.0), (-13.0, 0.0),
                       (-4.5, 4.0), (-10.0, -7.0)]
            a_slots = [s for s in self.side_slots[att] if s != taker and self.roster.slot_role[s] != "GK"]
            a_slots.sort(key=lambda s: (POS_HEIGHT_RANK.get(self.roster.group_of_slot(s), 9), s))
            for i, s in enumerate(a_slots):
                if i < 6:
                    T[s] = np.array([gx + np.sign(gx) * box_pts[i][0], box_pts[i][1]])
                elif i < 8:
                    T[s] = np.array([gx - np.sign(gx) * 22.0, (-1) ** i * 9.0])
                else:
                    T[s] = np.array([gx - np.sign(gx) * 45.0, (-1) ** i * 12.0])
            d_slots = [s for s in self.side_slots[dfd] if self.roster.slot_role[s] != "GK"]
            for i, s in enumerate(d_slots):
                if i < 8:
                    T[s] = np.array([gx + np.sign(gx) * box_pts[i][0] * 0.95 - np.sign(gx) * 0.6, box_pts[i][1] + 0.7])
                else:
                    T[s] = np.array([gx - np.sign(gx) * (24.0 + 8 * (i - 8)), (-1) ** i * 6.0])
            T[self.roster.gk_slot(dfd)] = np.array([gx - np.sign(gx) * 0.8, 0.5 * np.sign(spot[1])])
        if r.kind == "FreeKick" and r.danger:
            dfd = other(r.side)
            g = np.array([self.goal_x(r.side), 0.0])
            u = (g - spot) / max(np.hypot(*(g - spot)), 1e-6)
            perp = np.array([-u[1], u[0]])
            d_slots = sorted([s for s in self.side_slots[dfd] if self.roster.slot_role[s] != "GK"],
                             key=lambda s: float(np.hypot(*(self.P[s] - spot))))
            for i, s in enumerate(d_slots[:4]):
                T[s] = spot + u * WALL_DISTANCE_M + perp * (i - 1.5) * 0.7
            a_slots = [s for s in self.side_slots[r.side] if s != taker and self.roster.slot_role[s] in
                       ("RCB", "LCB", "CB", "ST", "RS", "LS", "CAM")]
            for i, s in enumerate(a_slots):
                T[s] = g - np.array([np.sign(g[0]) * (8.0 + 2.0 * i), 0.0]) + np.array([0.0, (-1) ** i * 4.0])
        if r.kind == "Penalty":
            # IFAB Law 14: the goalkeeper on the goal line, everybody else outside the penalty area, at least
            # 9.15 m from the spot and behind the ball
            att = r.side
            gk_d = self.roster.gk_slot(other(att))
            T[gk_d] = np.array([self.goal_x(att) - np.sign(self.goal_x(att)) * 0.25, 0.0])
            edge_att = self.pitch.hl - PENALTY_AREA_DEPTH_M - 1.0
            others = [s for s in range(self.n) if s not in (taker, gk_d) and self.roster.slot_role[s] != "GK"]
            others.sort(key=lambda s: (float(self.att(att, T[s])[1]), s))
            for i, s in enumerate(others):
                row = i % 2
                y = -18.0 + 36.0 * i / max(len(others) - 1, 1)
                xa = edge_att - 0.5 - 3.5 * row
                if abs(y) < 9.5:  # outside the penalty arc too
                    xa = min(xa, self.pitch.hl - PENALTY_SPOT_M - math.sqrt(max(WALL_DISTANCE_M ** 2 - y ** 2, 0.0)) - 0.6)
                T[s] = self.att(att, np.array([xa, y]))
            T[taker] = spot - 1.2 * self._toward_play(spot, r)
            return self.pitch.clamp(T, margin=1.2)
        T[taker] = spot + (0.0 if r.kind in ("ThrowIn", "KickOff") else 0.0) - 0.6 * self._toward_play(spot, r)
        return self.pitch.clamp(T, margin=1.2)

    def _toward_play(self, spot, r: Restart) -> np.ndarray:
        if r.kind == "ThrowIn":
            return np.array([0.0, np.sign(spot[1])]) * -0.5
        g = np.array([self.goal_x(r.side), 0.0]) - spot
        return g / max(np.hypot(*g), 1e-6)

    def choose_taker(self, r: Restart) -> int:
        side = r.side
        if r.taker is not None:
            return r.taker
        if r.kind == "GoalKick":
            return self.roster.gk_slot(side)
        if r.kind == "CornerKick":
            p = self.roster.first_available(side, "corners")
            if p:
                return self.slot(p)
        if r.kind == "Penalty":
            p = self.roster.first_available(side, "penalties")
            if p:
                return self.slot(p)
        if r.kind == "FreeKick" and r.danger:
            p = self.roster.first_available(side, "free_kicks")
            if p:
                return self.slot(p)
        if r.kind == "KickOff":
            for role in ("ST", "RS", "LS", "CAM"):
                for s in self.side_slots[side]:
                    if self.roster.slot_role[s] == role:
                        return s
        if r.kind == "FreeKick" and abs(self.att(side, r.spot)[0] + self.pitch.hl) < 20:
            return self.roster.gk_slot(side) if self.rng.random() < 0.5 else self._nearest(side, r.spot)
        return self._nearest(side, r.spot)

    def dead_phase(self, r: Restart, taker: int, duration_s: float):
        """Players reposition, the ball is brought to the spot; substitutions, cards and VAR happen here."""
        n_total = max(1, int(round(duration_s * FS)))
        if self.f + n_total > self.end_f and self.cut_dead_at_end:
            n_total = max(0, self.end_f - self.f)
            self._period_over = True
        layout = self.layout(r, taker)
        b_start = self.ball.copy()
        b_end = np.array([r.spot[0], r.spot[1], 1.9 if r.kind == "ThrowIn" else bp.GROUND_Z])
        dist = float(np.hypot(*(b_end[:2] - b_start[:2])))
        move = min(dist / 6.0 + 0.6, max(n_total * DT - 2.0, 0.1))
        k_move0 = max(0, n_total - int((move + 2.0) * FS))
        k_move1 = max(k_move0 + 1, n_total - int(2.0 * FS))
        # scheduled extras (cards, VAR) and substitutions
        extras = sorted(r.extras, key=lambda e: e[0])
        subs = self.director.subs_for_stoppage(self) if self.director is not None else []
        out_slots = {self.slot(m.player_out): m for m in subs}
        sub_done: set[int] = set()
        celebrate_until = int(min(22.0, 0.4 * n_total * DT) * FS) if r.celebrate is not None else 0
        k = 0
        chunk = FS
        ev_i = 0
        while k < n_total:
            m = min(chunk, n_total - k)
            kk = np.arange(k + 1, k + m + 1)
            # ball path
            w = np.clip((kk - k_move0) / max(k_move1 - k_move0, 1), 0.0, 1.0)
            w = 0.5 - 0.5 * np.cos(np.pi * w)
            bpath = b_start[None, :] * (1 - w)[:, None] + b_end[None, :] * w[:, None]
            if r.kind != "ThrowIn":
                bpath[:, 2] = np.where(w < 1.0, np.maximum(b_start[2] * (1 - w) + bp.GROUND_Z * w, bp.GROUND_Z),
                                       bp.GROUND_Z)
            T = np.repeat(layout[None, :, :], m, axis=0)
            wd = self.wander[self.f + 1:self.f + 1 + m]
            if wd.shape[0] == m and r.kind != "Penalty":  # at a penalty everybody waits on his Law 14 spot
                T += 1.0 * wd
                T[..., 0] = np.clip(T[..., 0], -self.pitch.hl - 0.5, self.pitch.hl + 0.5)
                T[..., 1] = np.clip(T[..., 1], -self.pitch.hw - 0.5, self.pitch.hw + 0.5)
            # nobody but the taker near the restart spot (E-6: the restart kick is his, R-TOUCH-NEAREST; Law 15)
            self._keep_clear(T, np.repeat(b_end[None, :2], m, axis=0), 0, m - 1, RESTART_SPACE_M, exempt={taker})
            a = self.arrays()
            effort = 0.62 * a["work"]
            if k < celebrate_until and r.celebrate is not None:
                cs = r.celebrate
                corner = np.array([np.sign(self.P[cs, 0] if self.P[cs, 0] != 0 else 1) * (self.pitch.hl - 6.0),
                                   np.sign(self.P[cs, 1] if self.P[cs, 1] != 0 else 1) * (self.pitch.hw - 6.0)])
                spot_c = self.P[cs] * 0.5 + corner * 0.5
                for s in self.side_slots[self.side_of(cs)]:
                    if self.roster.slot_role[s] != "GK":
                        T[:, s] = spot_c + 1.2 * np.array([math.cos(s), math.sin(s)])
                        effort[s] = 1.1
            for s, mo in out_slots.items():
                if s in sub_done:
                    continue
                zone = np.array([1.5 * (1 if s % 2 else -1), BENCH_SIDE_Y_SIGN * (self.pitch.hw + 0.6)])
                T[:, s] = zone
                effort[s] = 2.0
            T[:, taker] = layout[taker] if k + m < n_total - FS * 3 or r.kind == "KickOff" else layout[taker]
            pos = simulate(self.P, self.V, T, effort, a["vmax"], a["a0"], a["dmax"])
            self._write(pos, bpath, False, r.side)
            k += m
            # extras due
            while ev_i < len(extras) and extras[ev_i][0] * FS <= k:
                kwargs = dict(extras[ev_i][1])
                if "slot_pid" in kwargs:
                    kwargs["slot"] = self.slot(kwargs.pop("slot_pid"))
                self.event(**kwargs)
                ev_i += 1
            # substitutions that fall due during this stoppage join it (the stoppage lasts at least 30 s more)
            if self.director is not None and not self._period_over:
                for mo in self.director.subs_for_stoppage(self):
                    s_new = self.slot(mo.player_out)
                    if s_new not in out_slots and s_new != taker:
                        out_slots[s_new] = mo
                        n_total = max(n_total, k + 30 * FS)
                        k_move0 = max(0, n_total - int((move + 2.0) * FS))
                        k_move1 = max(k_move0 + 1, n_total - int(2.0 * FS))
                        if self.f + (n_total - k) > self.end_f and self.cut_dead_at_end:
                            n_total = k + max(0, self.end_f - self.f)
            # substitutions: switch when the outgoing player reaches the zone (or late in the stoppage)
            for s, mo in out_slots.items():
                if s in sub_done:
                    continue
                zone = np.array([1.5 * (1 if s % 2 else -1), BENCH_SIDE_Y_SIGN * (self.pitch.hw + 0.6)])
                if np.hypot(*(self.P[s] - zone)) < 1.2 or k >= n_total - 6 * FS:
                    self._substitute(s, mo, zone)
                    sub_done.add(s)
        while ev_i < len(extras):
            kwargs = dict(extras[ev_i][1])
            if "slot_pid" in kwargs:
                kwargs["slot"] = self.slot(kwargs.pop("slot_pid"))
            self.event(**kwargs)
            ev_i += 1
        for s, mo in out_slots.items():
            if s not in sub_done:
                zone = np.array([1.5 * (1 if s % 2 else -1), BENCH_SIDE_Y_SIGN * (self.pitch.hw + 0.6)])
                self._substitute(s, mo, zone)

    def _substitute(self, s: int, mo, zone: np.ndarray):
        side = self.side_of(s)
        out_id = self.pid(s)
        self.event("Substitution", None, s, xy=self.P[s],
                   attrs={"PlayerIn": mo.player_in, "PlayerOut": out_id, "PlayingPosition": self.roster.slot_role[s]})
        self.roster.slot_player[s] = mo.player_in
        self.roster.used_subs[side].append(mo.player_in)
        self.cur_pid[s] = self.pid_index[mo.player_in]
        self.P[s] = zone + np.array([2.2 * (1 if zone[0] < 0 else -1), -BENCH_SIDE_Y_SIGN * 0.1])
        self.V[s] = 0.0
        self.sprint_vmax.pop(s, None)
        self.director.sub_done(mo, self)

    def run_restart(self):
        r = self.pending
        self.pending = None
        taker = self.choose_taker(r)
        leaving = ({self.slot(m.player_out) for m in self.director.subs_for_stoppage(self)}
                   if self.director is not None else set())
        if taker in leaving:  # a player about to be substituted does not take the restart
            taker = self._nearest(r.side, r.spot, exclude=leaving | {self.roster.gk_slot(r.side)})
        if r.duration_s is not None:
            dur = r.duration_s
        elif r.kind == "KickOff" and self.f == 0:
            dur = 0.0
        elif r.kind == "KickOff":
            dur = self._dead_duration("KickOff")
        elif r.kind == "FreeKick":
            dur = self._dead_duration("FreeKickDanger" if r.danger else ("Offside" if r.reason == "offside" else "FreeKick"))
        else:
            dur = self._dead_duration(r.kind)
        if self.director is not None and (r.duration_s is None or self.director.busy) and dur > 0 and r.action is None:
            dur = self.director.max_stoppage(self, r, dur)
        if self.director is not None:
            dur = max(dur, self.director.min_stoppage(self, r))
        if dur > 0:
            self.dead_phase(r, taker, dur)
        if self._period_over:
            return
        self.ball = np.array([r.spot[0], r.spot[1], 1.9 if r.kind == "ThrowIn" else bp.GROUND_Z])
        self.carrier, self.poss = taker, r.side
        # the ball is in play from the kick: the restart event's frame is the first live frame (as in DFL)
        self.buf.live[self.f] = True
        self.buf.poss[self.f] = POSS_CODE[r.side]
        if r.action is not None:
            r.action(self, taker, r)
        elif self.director is not None and self.director.busy:
            self.safe_restart(r, taker)
        else:
            self.restart_action(r, taker)

    def safe_restart(self, r: Restart, taker: int):
        """Short, safe restart used while the director prepares a scripted moment."""
        rcv = self.choose_receiver(taker, max_dist=22.0, pref=12.0)
        kind = "throw" if r.kind == "ThrowIn" else "flat"
        self.pass_ball(taker, rcv, kind, "success", restart=r, z_end=bp.GROUND_Z, T=1.0 if kind == "throw" else None)

    # ------------------------------------------------------------------ Markov model

    def restart_action(self, r: Restart, taker: int):
        side = r.side
        mates = [s for s in self.side_slots[side] if s != taker]
        if r.kind == "KickOff":
            rcv = min((s for s in mates if self.roster.slot_role[s] != "GK"),
                      key=lambda s: float(np.hypot(*(self.P[s] - self.att(side, np.array([-9.0, 0.0]))))))
            self.pass_ball(taker, rcv, "flat", "success", restart=r)
            return
        if r.kind == "ThrowIn":
            rcv = self.choose_receiver(taker, max_dist=24.0, pref=11.0)
            ok = self.rng.random() < 0.78
            self.pass_ball(taker, rcv, "throw", "success" if ok else "contested", restart=r,
                           z_end=float(self.rng.choice([bp.GROUND_Z, 0.9])), T=float(self.rng.uniform(0.7, 1.3)))
            return
        if r.kind == "GoalKick":
            if self.rng.random() < self.style[side]["short_gk"]:
                rcv = min((s for s in mates if self.roster.group_of_slot(s) in ("CB", "DM", "FB")),
                          key=lambda s: float(np.hypot(*(self.P[s] - r.spot))))
                self.pass_ball(taker, rcv, "flat", "success", restart=r)
            else:
                rcv = self.choose_receiver(taker, min_dist=35.0, pref=50.0, forward=1.5)
                self.pass_ball(taker, rcv, "high", "contested" if self.rng.random() < 0.6 else "success", restart=r,
                               z_end=float(self.rng.uniform(0.9, 1.9)))
            return
        if r.kind == "CornerKick":
            if self.rng.random() < 0.12:
                rcv = self.choose_receiver(taker, max_dist=18.0, pref=10.0)
                self.pass_ball(taker, rcv, "flat", "success", restart=r)
                return
            self.cross(taker, restart=r)
            return
        if r.kind == "Penalty":
            self.penalty_kick(taker, r, self.sample_penalty_outcome())
            return
        if r.kind == "FreeKick":
            spot_att = self.att(side, r.spot)
            dist = float(np.hypot(self.pitch.hl - spot_att[0], spot_att[1]))
            if r.danger and dist < 27.0 and abs(spot_att[1]) < 18 and self.rng.random() < 0.5:
                outcome = self.sample_shot_outcome(side, dist, header=False, free_kick=True)
                self.shoot(taker, outcome, restart=r)
                return
            if r.danger:
                self.cross(taker, restart=r)
                return
            rcv = self.choose_receiver(taker)
            self.pass_ball(taker, rcv, "flat" if self.rng.random() < 0.7 else "high", "success", restart=r,
                           z_end=bp.GROUND_Z)
            return

    def sample_penalty_outcome(self) -> str:
        """Intent of an unscripted penalty: a goal with the fixed penalty xG (replicas only), else saved or wide."""
        if not self.plan.scripted and self.allow_random_goals and self.rng.random() < PENALTY_XG:
            return "goal"
        return "saved" if self.rng.random() < 0.6 else "wide"

    def penalty_kick(self, taker: int, r: Restart, outcome: str, speed: float | None = None,
                     save: str | None = None, body: str | None = None) -> str:
        """The kick from the penalty spot. `outcome` is the intent (goal | saved | wide); the written result comes
        from the simulation, nested in the `Penalty` event (`ShotAtGoal/<result>`)."""
        side = r.side
        gx = self.goal_x(side)
        gk = self.roster.gk_slot(other(side))
        gk_y = float(self.P[gk, 1])
        sy = 1.0 if self.rng.random() < 0.5 else -1.0
        if outcome == "goal":
            target = np.array([gx, sy * float(self.rng.uniform(2.2, 3.1)), float(self.rng.uniform(0.3, 1.7))])
            speed = speed or float(self.rng.uniform(20.0, 26.0))
        elif outcome == "saved":
            target = np.array([gx, float(np.clip(gk_y + sy * float(self.rng.uniform(0.9, 1.4)), -3.2, 3.2)),
                               float(self.rng.uniform(0.3, 0.8))])
            speed = speed or float(self.rng.uniform(18.0, 22.0))
        else:  # wide of the post or over the bar
            if self.rng.random() < 0.4:
                target = np.array([gx, sy * float(self.rng.uniform(0.0, 2.5)), float(self.rng.uniform(2.8, 3.6))])
            else:
                target = np.array([gx, sy * float(self.rng.uniform(4.2, 5.5)), float(self.rng.uniform(0.3, 1.2))])
            speed = speed or float(self.rng.uniform(19.0, 25.0))
        attrs = {"xG": PENALTY_XG, "BuildUp": "penaltyKick"}
        return self.shoot(taker, outcome, target=target, speed=speed, restart=r, save=save, body=body, attrs=attrs)

    def cross(self, c: int, restart: Restart | None = None, low: bool = False, outcome: str | None = None,
              receiver: int | None = None, point=None):
        """Cross into the box: aerial duel, goalkeeper claim, out, or a header chance."""
        side = self.side_of(c)
        dside = other(side)
        gx = self.goal_x(side)
        sx = np.sign(gx)
        if point is None:
            point = np.array([gx - sx * float(self.rng.uniform(4.0, 12.0)), float(self.rng.uniform(-8.0, 8.0))])
        pool = [s for s in self.side_slots[side] if s != c and self.roster.slot_role[s] != "GK"]
        r = receiver if receiver is not None else min(pool, key=lambda s: float(np.hypot(*(self.P[s] - point))))
        u = self.rng.random()
        if outcome is None:
            p_in = self.cal.events.cross_success + 0.05 * self.bias(side)
            outcome = ("header" if u < p_in else "gk" if u < p_in + 0.15 else "out" if u < p_in + 0.36 else "cleared")
        kind = "low_cross" if low else "cross"
        z_end = bp.GROUND_Z if low else float(self.rng.uniform(1.8, 2.4))
        sub = "Cross"
        if restart is None:  # room to cross (E-6); every branch below kicks from where the ball is then
            self._make_kick_room(c, lambda: self._straight_path(point, 20.0))
        if outcome == "out":
            self.pass_ball(c, r, kind, "out", point=point, restart=restart, sub=sub, z_end=z_end, out_side="goal_line")
            return
        if outcome == "gk":
            gk = self.roster.gk_slot(dside)
            point = np.array([gx - sx * float(self.rng.uniform(3.0, 6.5)), float(self.rng.uniform(-4.0, 4.0))])
            path, n = self.ball_path(self.ball, point, kind, z_end=2.2)
            self._kick_event(c, restart, "Play", sub, {"Evaluation": None, "Height": "high", "Recipient": self.pid(r)})
            T_, eff = self.live_targets(path[:, :2], side, press_point=point)
            T_[:, gk] = point
            T_[:, r] = point + np.array([-sx * 1.0, 0.5])
            eff[gk] = eff[r] = 2.0
            self.advance(n, path, T_, eff, live=True, poss=side, arrive={gk: n, r: n})
            self.log_ball("touch", gk)
            self.event("BallClaiming", "BallClaimed", gk, xy=self.ball[:2], attrs={"Type": "caught"})
            self.carrier, self.poss = gk, dside
            return
        if outcome == "header":
            res = self.pass_ball(c, r, kind, "success", point=point, restart=restart, sub=sub, z_end=z_end)
            if res != "received" or self.carrier != r:
                return  # the cross did not reach him: the ball physics already went on (loose ball, out of play...)
            if low:
                shot = self.sample_shot_outcome(side, float(np.hypot(*(self.P[r] - np.array([gx, 0.0])))), False)
                self.shoot(r, shot)
            elif self._header_contact(r):
                shot = self.sample_shot_outcome(side, float(np.hypot(*(self.P[r] - np.array([gx, 0.0])))), True)
                self.shoot(r, shot, body="head")
            # else he reached it but not at head height or close enough: he takes it down, play goes on
            return
        # cleared by a defender (header or foot)
        dfd = self._nearest(dside, point)
        path, n = self.ball_path(self.ball, point, kind, z_end=z_end)
        self._kick_event(c, restart, "Play", sub, {"Evaluation": None, "Height": "flat" if low else "high",
                                                   "Recipient": self.pid(r)})
        T_, eff = self.live_targets(path[:, :2], side, press_point=point)
        T_[:, dfd] = point
        T_[:, r] = point + np.array([-sx * 1.2, 0.8])
        eff[dfd] = eff[r] = 2.0
        self.advance(n, path, T_, eff, live=True, poss=side, arrive={dfd: n, r: n})
        if not self._reaches(dfd):  # he does not get there: nobody clears it, the ball runs on (E-6)
            return self._run_on(path, side, kicker=c)
        if z_end > 1.2 and not self._header_contact(dfd):  # within reach but not for a header: he takes it down
            self.log_ball("touch", dfd)
            self.carrier, self.poss = dfd, dside
            return "claimed"
        if z_end > 1.2:
            self.duel_event("air", r, dfd, dfd)  # the defender's clearance below is the next touch
        self.poss = side
        busy = self.director is not None and self.director.busy
        if self.rng.random() < 0.25 and not busy:
            sy = 1.0 if self.ball[1] >= 0 else -1.0
            self.clear(dfd, header=z_end > 1.2, out="goal_line",
                       aim=np.array([gx + sx * 2.5, sy * float(self.rng.uniform(6.0, 22.0))]))
        else:
            self.clear(dfd, header=z_end > 1.2)

    def sample_shot_outcome(self, side: str, dist: float, header: bool, free_kick: bool = False) -> str:
        e = self.cal.events
        if not self.plan.scripted and self.allow_random_goals:
            gx = self.goal_x(side)
            angle = self._shot_angle(self.ball[:2], gx)
            if self.rng.random() < self.xg(dist, angle, header, self._opp_in_triangle(self.ball[:2], gx, other(side))) * (0.7 if free_kick else 1.0):
                return "goal"
        lane = self._shooting_lane_defender(self.ball[:2], self.goal_x(side), other(side))
        if free_kick:  # a direct free kick: the wall is in the lane
            u = self.rng.random()
            return "blocked" if u < 0.3 else "saved" if u < 0.6 else "wide"
        # a shot can only be meant to be blocked when a defender stands in the lane (then BLOCK_GIVEN_LANE)
        if lane is not None and self.rng.random() < BLOCK_GIVEN_LANE:
            return "blocked"
        on = e.shot_on_target
        wide = max(1.0 - e.shot_on_target - e.shot_blocked, 0.05)
        return "saved" if self.rng.random() < on / (on + wide) else "wide"

    def choose_receiver(self, c: int, min_dist: float = 4.0, max_dist: float = 60.0, pref: float = 16.0,
                        forward: float = 0.0, exclude: set | None = None) -> int:
        side = self.side_of(c)
        b = self.ball[:2]
        b_att = self.att(side, b)
        best, weights = [], []
        opp = np.array([self.P[s] for s in self.side_slots[other(side)]])
        push = self.push(side)
        for s in self.side_slots[side]:
            if s == c or (exclude and s in exclude):
                continue
            d = float(np.hypot(*(self.P[s] - b)))
            if d < min_dist or d > max_dist:
                continue
            if self.roster.slot_role[s] == "GK" and b_att[0] > -25:
                continue
            dx = float(self.att(side, self.P[s])[0] - b_att[0])
            open_d = float(np.min(np.hypot(opp[:, 0] - self.P[s, 0], opp[:, 1] - self.P[s, 1])))
            wgt = math.exp(-((d - pref) ** 2) / (2 * 10.0 ** 2))
            wgt *= math.exp(np.clip((0.25 + forward + 0.25 * push + 0.2 * max(0, self.bias(side))) * dx / 10.0, -3, 3))
            wgt *= 1.0 - 0.85 * math.exp(-((open_d / 3.5) ** 2))
            if self.offside_margin(s) > 0.0:  # the passer usually sees a team-mate in an offside position
                if self.director is not None and self.director.busy:
                    continue  # a scripted sequence never passes to an offside team-mate
                wgt *= OFFSIDE_PICK_WEIGHT
            best.append(s)
            weights.append(wgt + 1e-9)
        if not best:
            pool = [s for s in self.side_slots[side] if s != c]
            return min(pool, key=lambda s: float(np.hypot(*(self.P[s] - b))))
        w = np.array(weights)
        return best[int(self.rng.choice(len(best), p=w / w.sum()))]

    def pass_success(self, c: int, r: int, kind: str) -> float:
        side = self.side_of(c)
        e = self.cal.events
        b_att = self.att(side, self.ball[:2])
        key = self.cal.zone_key(*b_att)
        d = float(np.hypot(*(self.P[r] - self.ball[:2])))
        if key is not None and key in self.cal.pass_success_zone:
            p = self.cal.pass_success_zone[key] + PASS_INTENT_SHIFT  # the zone mean includes long balls and crosses
            if kind == "high" or d > 32:
                p -= 0.17
        else:
            p = e.pass_success_by_band[zone_band(self.pitch.zone(*b_att))]
            if kind == "high" or d > 32:
                p -= e.long_pass_penalty
        if kind == "through":
            p = e.through_success
        opp = [s for s in self.side_slots[other(side)]]
        open_d = min(float(np.hypot(*(self.P[s] - self.P[r]))) for s in opp)
        if open_d < 2.5:
            p -= 0.12
        p += self.style[side]["pass"] + 0.05 * self.push(side) + 0.10 * self.bias(side)
        _, intensity = self.momentum()
        p -= 0.06 * max(0.0, intensity - 1.0)
        return float(np.clip(p, 0.2, 0.97))

    def markov_step(self):
        """One on-ball decision of the current carrier."""
        c = self.carrier
        side = self.side_of(c)
        dside = other(side)
        style = self.style[side]
        b_att = self.att(side, self.ball[:2])
        zone = self.pitch.zone(*b_att)
        band, ch = zone_band(zone), zone_channel(zone)
        wide = ch != 1
        is_gk = self.roster.slot_role[c] == "GK"
        opp_d = [(float(np.hypot(*(self.P[s] - self.P[c]))), s) for s in self.side_slots[dside]]
        d_near, s_near = min(opp_d)
        push = self.push(side)
        bias = self.bias(side)
        _, intensity = self.momentum()
        # off-ball runs: a forward, winger or attacking midfielder (full back in the final third) attacks space
        if not is_gk and b_att[0] > -12 and self.rng.random() < 0.075 * intensity * (1 + 0.3 * max(push, 0)):
            run_w = {"ST": 1.0, "AM": 0.9, "W": 0.3, "DM": 0.35, "CM": 0.15 if band >= 3 else 0.0,
                     "FB": 0.25 if band >= 3 else 0.0}
            pool = [s for s in self.side_slots[side] if s != c and s not in self.runs and s not in self.overrides
                    and run_w.get(self.roster.group_of_slot(s), 0.0) > 0]
            if pool:
                w = np.array([run_w[self.roster.group_of_slot(s)] for s in pool])
                s = pool[int(self.rng.choice(len(pool), p=w / w.sum()))]
                s_att = self.att(side, self.P[s])
                goal_y = float(np.clip(s_att[1] * 0.6 + self.rng.normal(0, 5.0), -28, 28))
                pt_att = np.array([min(s_att[0] + float(self.rng.uniform(12.0, 24.0)), 46.0), goal_y])
                mistimed = bool(self.rng.random() < MISTIMED_RUN_SHARE)  # he goes too early: not held onside
                self.runs[s] = (self.raw(side, pt_att), self.f + int(float(self.rng.uniform(2.5, 4.5)) * FS), mistimed)
        # control phase: hold the ball, small carry forward if there is space
        hold_s = float(np.clip(math.exp(self.rng.normal(math.log(1.15 * style["hold"] / intensity), 0.55)), 0.12, 3.5))
        if self.rng.random() < 0.14:
            hold_s = 0.12
        if is_gk:
            hold_s = float(self.rng.uniform(1.5, 4.5))
        space = min(d_near, 12.0)
        fwd = self.att(side, np.array([1.0, 0.0]))
        dest = self.P[c] + fwd * min(space * 0.3, 3.0 * hold_s) + np.array([0.0, float(self.rng.normal(0, 1.0))])
        moved = float(np.hypot(*(dest - self.P[c])))
        if moved > 2.0 and self.rng.random() < 0.5:
            self.event("OtherBallAction", None, c, xy=self.ball[:2])
        self.hold(c, hold_s, dest=dest, speed=min(4.0, 1.0 + space * 0.25))
        if self._time_up():
            return
        b_att = self.att(side, self.ball[:2])
        zone = self.pitch.zone(*b_att)
        band, ch = zone_band(zone), zone_channel(zone)
        wide = ch != 1
        d_near, s_near = min((float(np.hypot(*(self.P[s] - self.P[c]))), s) for s in self.side_slots[dside])
        if is_gk:
            if self.rng.random() < 0.5 + 0.3 * (style["short_gk"] - 0.4):
                rcv = self.choose_receiver(c, max_dist=30.0, pref=18.0)
                self.pass_ball(c, rcv, "flat", "success")
            else:
                rcv = self.choose_receiver(c, min_dist=30.0, pref=48.0, forward=1.5)
                self.pass_ball(c, rcv, "high", "contested" if self.rng.random() < 0.55 else "success",
                               z_end=float(self.rng.uniform(1.0, 1.9)))
            return
        # action probabilities by zone
        dist_goal = float(np.hypot(self.pitch.hl - b_att[0], b_att[1]))
        p = {
            "shot": 0.0, "cross": 0.0,
            "long": [0.20, 0.15, 0.10, 0.07, 0.04, 0.0][band] * style["long"] * (1 - 0.3 * push),
            "carry": [0.06, 0.10, 0.14, 0.15, 0.15, 0.12][band] * style["carry"],
            "clear": [0.07, 0.02, 0.0, 0.0, 0.0, 0.0][band] * (3.0 if d_near < 3.0 else 1.0),
            "dribble": [0.04, 0.05, 0.10, 0.13, 0.17, 0.21][band] if d_near < 3.5 else 0.0,
            "through": [0.0, 0.0, 0.02, 0.05, 0.06, 0.03][band] * (2.0 if any(self.side_of(s) == side for s in self.runs) else 1.0),
        }
        if band == 5 and not wide:
            p["shot"] = 0.36 if dist_goal < 22 else 0.14
        elif band == 4 and not wide:
            p["shot"] = 0.10 if dist_goal < 30 else 0.025
        elif band >= 4 and wide:
            p["shot"] = 0.022
            p["cross"] = (0.48 if band == 5 else 0.19) * style["cross"]
        zkey = self.cal.zone_key(*b_att)
        if zkey is not None and zkey in self.cal.shot_rate_zone:
            p["shot"] = self.cal.shot_rate_zone[zkey] * shot_rate_scale(band, dist_goal)  # DFL rate per annotated action
        p["shot"] *= (1 + 0.35 * push) * (0.3 if bias < -0.2 or bias > 0.2 else 1.0)
        if self.director is not None and self.director.suppress_shots(self.clock(), self.period):
            p["shot"] = 0.0
        p["pass"] = max(0.05, 1.0 - sum(p.values()))
        keys = list(p)
        w = np.array([p[k] for k in keys])
        action = keys[int(self.rng.choice(len(keys), p=w / w.sum()))]
        # pressure: a close opponent can force a duel or a foul
        cooldown = self.f - getattr(self, "last_duel_f", -10**9) < 3 * FS
        if cooldown and action == "dribble":
            action = "pass"
        if (not cooldown and d_near < 1.5 and action in ("pass", "carry", "long", "through")
                and self.rng.random() < 0.08):
            action = "dribble"
        if action == "shot":
            outcome = self.sample_shot_outcome(side, dist_goal, header=False)
            self.shoot(c, outcome)
            return
        if action == "cross":
            self.cross(c, low=self.rng.random() < 0.3)
            return
        if action == "clear":
            self.clear(c, out="touchline" if self.rng.random() < 0.45 else None)
            return
        if action == "carry":
            length = float(self.rng.uniform(4.0, 13.0))
            dest = self.P[c] + self.att(side, np.array([length, float(self.rng.normal(0, 3.0))]))
            speed = float(self.rng.uniform(3.8, 6.5)) * min(intensity, 1.25)
            self.event("OtherBallAction", None, c, xy=self.ball[:2])
            self.hold(c, length / speed, dest=dest, speed=speed, presser=s_near if d_near < 9 else None)
            d2, s2 = min((float(np.hypot(*(self.P[s] - self.P[c]))), s) for s in self.side_slots[dside])
            if d2 < 1.8 and self.rng.random() < 0.2 and self.f - getattr(self, "last_duel_f", -10**9) > 3 * FS:
                self._contest(c, s2)
            return
        if action == "dribble":
            self._contest(c, s_near)
            return
        if action == "through":
            fwd_pool = [s for s in self.runs if self.side_of(s) == side and s != c]
            if not fwd_pool:
                fwd_pool = [s for s in self.side_slots[side] if self.roster.group_of_slot(s) in ("ST", "W", "AM", "FB")
                            and s != c and self.att(side, self.P[s])[0] > b_att[0] - 3]
            if fwd_pool:
                r = fwd_pool[int(self.rng.integers(len(fwd_pool)))]
                self.through_ball(c, r, success=self.rng.random() < self.pass_success(c, r, "through"), sprint=True)
                return
            action = "pass"
        kind = "high" if action == "long" else "flat"
        pref = self.cal.pass_length_zone.get(self.cal.zone_key(*b_att) or "", 15.0)
        r = self.choose_receiver(c, min_dist=24.0 if kind == "high" else 4.0, pref=38.0 if kind == "high" else pref,
                                 forward=0.6 if kind == "high" else 0.0)
        dist = float(np.hypot(*(self.P[r] - self.P[c])))
        if kind == "flat" and dist > 34:
            kind = "high"
        succ = self.rng.random() < self.pass_success(c, r, kind)
        if succ:
            self.pass_ball(c, r, kind, "success", z_end=bp.GROUND_Z if kind == "flat" else float(self.rng.choice([bp.GROUND_Z, 1.0, 1.8])))
            return
        near_line = (abs(self.P[r, 1]) > self.pitch.hw - 6.0)
        u = self.rng.random()
        long_fwd = kind == "high" and self.att(side, self.P[r])[0] > self.pitch.hl - 30
        if long_fwd and u < 0.45:
            self.pass_ball(c, r, kind, "out", out_side="goal_line")
        elif u < (0.6 if near_line else 0.26):
            self.pass_ball(c, r, kind, "out", out_side="touchline")
        elif u < 0.68:
            self.pass_ball(c, r, kind, "intercept")
        else:
            self.pass_ball(c, r, kind, "contested")

    def _contest(self, c: int, s_def: int):
        side = self.side_of(c)
        dside = other(side)
        foul_p = 0.17
        in_box = self._in_box(self.P[c], dside)
        if in_box and not self.plan.scripted and self.rng.random() < PENALTY_FOUL_SHARE:
            # an unscripted penalty (replicas only: SYN-001's penalty is a scripted moment)
            self.duel(c, s_def, "def", foul=True)
            self.penalty_foul(s_def, c)
            return
        if not in_box and self.rng.random() < foul_p:
            self.duel(c, s_def, "def", foul=True)
            extras = []
            if self.allow_random_cards and self.cards.get(self.pid(s_def), 0) == 0 and self.rng.random() < 0.14:
                self.cards[self.pid(s_def)] = 1
                extras = [(float(self.rng.uniform(12, 25)),
                           {"etype": "Caution", "sub": "yellow", "slot_pid": self.pid(s_def),
                            "attrs": {"CardColor": "yellow", "Reason": "foul"}})]
            self.foul(s_def, c, extras=extras, min_dead_s=35.0 if extras else None)
            return
        win_att = self.rng.random() < 0.45 + 0.08 * self.push(side) + 0.15 * self.bias(side)
        self.duel(c, s_def, "att" if win_att else "def")

    def through_ball(self, c: int, r: int, success: bool = True, sprint: bool = False, point=None):
        side = self.side_of(c)
        a = self.arrays()
        if point is None:
            run = self.att(side, np.array([1.0, -0.15 * np.sign(self.att(side, self.P[r])[1])]))
            run /= np.hypot(*run)
            D0 = float(np.hypot(*(self.P[r] - self.ball[:2])))
            T = 1.8 + D0 / 25.0
            point = self.P[r] + run * min(0.7 * a["vmax"][r] * T, 18.0)
            point = self.pitch.clamp(point, margin=-2.0)
        if success:
            self.pass_ball(c, r, "through", "success", point=point, sprint_receiver=sprint)
        else:
            self.pass_ball(c, r, "through", "intercept" if self.rng.random() < 0.6 else "contested", point=point)

    def _whistle_offside(self, c: int, r: int, path: np.ndarray, n: int) -> str:
        """The pass of c goes to r, who was in an offside position at the kick: the assistant's flag goes up as r
        runs to the ball (he becomes involved), free kick to the defending team where he is."""
        side = self.side_of(c)
        k = max(2, int(0.6 * n))
        T_, eff = self.live_targets(path[:k, :2], side)
        T_[:, r] = path[-1, :2]
        eff[r] = 2.0
        self.advance(k, path[:k], T_, eff, live=True, poss=side)
        self.log_ball("offside", side=side)
        self.event("Offside", None, r, xy=self.P[r])
        spot = self.pitch.clamp(self.P[r].copy(), margin=-1.0)
        self.pending = Restart("FreeKick", other(side), spot, reason="offside")
        self.carrier = -1
        return "offside"

    def _time_up(self) -> bool:
        return self.f >= self.end_f

    # ------------------------------------------------------------------ periods

    def play_period(self, period: int, first_side: str, end_elapsed_s: float, n_max: int):
        self.period = period
        self.f = 0
        self.end_f = int(round(end_elapsed_s * FS))
        self.cut_dead_at_end = True
        self._period_over = False
        self.buf = PeriodBuffers(n_max, self.n)
        self.buffers[period] = self.buf
        home_dir = 1.0 if period == 1 else -1.0
        self.dir = {"home": home_dir, "away": -home_dir}
        self.cur_pid = np.array([self.pid_index[p] for p in self.roster.slot_player], np.int16)
        amp = np.array([2.0 if self.roster.slot_role[s] == "GK" else 3.0 for s in range(self.n)])
        self.wander = (smooth_noise(self.rng_wander, (n_max, self.n, 2), 2.5 * FS) * amp[None, :, None]).astype(np.float32)
        self.overrides = {}
        self.runs = {}
        self.turnover_f = -10 ** 9
        self.goal_frames = getattr(self, "goal_frames", [])
        self.woodwork_frames = getattr(self, "woodwork_frames", [])
        # kick-off formation at frame 0 (tracking starts at the kick-off)
        r = Restart("KickOff", first_side, np.array([0.0, 0.0]))
        taker = self.choose_taker(r)
        self.P = self.layout(r, taker) + 0.3 * self.wander[0].astype(float)
        self.P[taker] = np.array([-0.3 * self.dir[first_side], 0.2])
        self.V = np.zeros((self.n, 2))
        self.ball = np.array([0.0, 0.0, bp.GROUND_Z])
        self.buf.P[0] = self.P
        self.buf.B[0] = self.ball
        self.buf.live[0] = True
        self.buf.poss[0] = POSS_CODE[first_side]
        self.buf.pid[0] = self.cur_pid
        self.buf.n = 1
        self.poss = first_side
        self.pending = Restart("KickOff", first_side, np.array([0.0, 0.0]), taker=taker, duration_s=0.0)
        self.carrier = -1
        guard = 0
        while True:
            guard += 1
            if guard > 200000:
                raise RuntimeError("engine did not progress")
            if self._period_over:
                break
            if self.pending is not None:
                if self.f >= self.end_f and self.pending.kind != "KickOff":
                    break
                self.run_restart()
                continue
            if self.f >= self.end_f:
                break
            if self.director is not None and self.director.step(self):
                continue
            if self.carrier is None or self.carrier < 0:
                # nobody has the ball (should not happen): the nearest player takes it
                s = min(range(self.n), key=lambda q: float(np.hypot(*(self.P[q] - self.ball[:2]))))
                self.log_ball("touch", s)
                self.carrier, self.poss = s, self.side_of(s)
            self.markov_step()
        # final whistle
        last = self.f
        self.buf.live[last] = False
        self.event("FinalWhistle", None, None, side="none", f=last, xy=self.buf.B[last, :2],
                   attrs={"GameSection": "firstHalf" if period == 1 else "secondHalf"})
        self.buf.n = last + 1


POS_HEIGHT_RANK = {"CB": 0, "ST": 1, "DM": 2, "CM": 3, "AM": 4, "FB": 5, "W": 6, "GK": 9}
