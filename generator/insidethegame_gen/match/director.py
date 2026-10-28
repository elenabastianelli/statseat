"""The director: makes the scripted moments happen inside the simulated match.

Each story moment has
- a lead-in window (about 40 s) that biases possession and territory towards the team that
  needs the ball (towards the opponent for a counter-attack) and moves the key players to
  where they will be needed;
- a trigger time = scripted clock minus the expected duration of its sequence. At the first
  live decision after the trigger, the director runs the sequence with the engine's executors
  (so the physics and the events stay consistent), forcing actors and outcomes.
Substitutions wait for the next stoppage within about 35 s of their clock; if none comes,
the team in possession puts the ball out for a throw-in.

The actual frame of each key event is logged; `api.generate_match` checks that every moment
lands in the scripted displayed minute.
"""

from __future__ import annotations

import math

import numpy as np

from . import ball as bp
from .events import Engine, Restart, other
from .pitch import DT, FS, GOAL_HALF_WIDTH_M, clock_minute, parse_clock, period_start_clock
from .script import Moment

LEAD_S = {"nutmeg": 7.0, "goal:counter_attack": 11.0, "goal:corner": 41.0, "goal:long_range": 5.0,
          "goal:open_play": 8.0, "caution": 21.0, "woodwork": 5.5, "sprint": 6.5, "var_penalty_check": 4.5,
          "save": 5.5, "penalty": 100.0}
PENALTY_DEAD_S = (60.0, 140.0)  # stoppage between the foul in the box and the kick (DFL: 92-165 s, VAR included)
BIAS_WINDOW_S = 40.0
PRE_S = 25.0  # the sequence starts with this much "filler" possession, so the key event lands on time
SUB_WINDOW_BEFORE_S = 35.0
SUB_FORCE_AFTER_S = -22.0  # force a stoppage 22 s before the scripted clock (never before its minute starts)


def _key(mo: Moment) -> str:
    return f"goal:{mo.build_up}" if mo.type == "goal" else mo.type


def save_aim_y(ball_xy, gk_xy, gx: float, offset_m: float = 1.5) -> float:
    """Goal-line y of a scripted save: the ball passes offset_m from the keeper *where he stands* (not on the goal
    line). From an angle, a target offset_m from him on the line can fly past out of his reach (a wide shot)."""
    gy = float(gk_xy[1])
    want = gy + (offset_m if gy <= 0 else -offset_m)
    bx, by, gkx = float(ball_xy[0]), float(ball_xy[1]), float(gk_xy[0])
    if abs(gkx - bx) > 0.5:
        want = by + (want - by) * (gx - bx) / (gkx - bx)
    return float(np.clip(want, -GOAL_HALF_WIDTH_M + 0.4, GOAL_HALF_WIDTH_M - 0.4))


class Director:
    def __init__(self, moments: list[Moment], sprint_vmax: float | None = None):
        self.moments = moments
        self.done: set[str] = set()
        self.log: dict[str, dict] = {}
        self.sprint_vmax = sprint_vmax
        self.restore: list[tuple[int, float]] = []
        self.busy = False
        self.current_core = None
        self.current_id = None  # id of the scripted moment being played (offside log)
        self._sprint_side = None
        self.story = []
        for mo in moments:
            if mo.type in ("substitution",):
                continue
            if mo.type == "intense_spell":
                if mo.sprint_player_id and mo.sprint_clock:
                    self.story.append((mo, parse_clock(mo.sprint_clock), "sprint"))
                continue
            self.story.append((mo, mo.clock_s, _key(mo)))

    # ------------------------------------------------------------------ helpers for the engine

    def _core(self, anchor: float, key: str) -> float:
        """Clock at which the core of the sequence starts."""
        return anchor - LEAD_S.get(key, 6.0)

    def _trigger(self, anchor: float, key: str) -> float:
        """Clock at which the director takes over (filler possession first)."""
        return self._core(anchor, key) - PRE_S

    def _start_side(self, mo: Moment, key: str) -> str:
        if key in ("goal:counter_attack", "caution"):
            return other(mo.team)
        if key == "sprint":
            return self._sprint_side
        return mo.team

    def max_stoppage(self, e: Engine, r: Restart, dur: float) -> float:
        """Shorten a stoppage that would still be running when a scripted sequence must start."""
        clock = e.clock()
        if self.busy and self.current_core is not None:
            return min(dur, max(4.0, self.current_core - 3.0 - clock)) if clock < self.current_core else min(dur, 6.0)
        for mo, anchor, key in self.story:
            if mo.period != e.period or mo.id + key in self.done:
                continue
            core = self._core(anchor, key)
            if clock < core and clock + dur > core - 6.0:
                dur = min(dur, max(4.0, core - 6.0 - clock))
        return dur

    def intensity(self, clock: float, period: int, base: float) -> float:
        for mo in self.moments:
            if mo.type == "intense_spell" and mo.period == period and mo.clock_s <= clock < parse_clock(mo.until_clock):
                return max(base, mo.intensity or 1.3)
        return base

    def bias(self, clock: float, period: int):
        best = (None, 0.0)
        for mo, anchor, key in self.story:
            if mo.period != period or mo.id + key in self.done:
                continue
            trig = self._trigger(anchor, key)
            if trig - BIAS_WINDOW_S <= clock <= trig + 2.0:
                side = self._start_side(mo, key)
                strength = 0.55
                if side is not None and strength > best[1]:
                    best = (side, strength)
        return best

    def suppress_shots(self, clock: float, period: int) -> bool:
        for mo, anchor, key in self.story:
            if mo.period == period and key.startswith(("goal", "woodwork", "save", "penalty")):
                trig = self._trigger(anchor, key)
                if trig - BIAS_WINDOW_S <= clock <= anchor + 4.0:
                    return True
        return False

    def subs_for_stoppage(self, e: Engine) -> list[Moment]:
        clock = e.clock()
        return [mo for mo in self.moments if mo.type == "substitution" and mo.period == e.period and mo.id not in self.done
                and clock >= max(mo.clock_s - SUB_WINDOW_BEFORE_S, 60.0 * math.floor(mo.clock_s / 60.0) + 2.0)]

    def min_stoppage(self, e: Engine, r: Restart) -> float:
        subs = self.subs_for_stoppage(e)
        if not subs:
            return 0.0
        zone_y = -e.pitch.hw
        far = max(float(np.hypot(e.P[e.slot(m.player_out)][0], e.P[e.slot(m.player_out)][1] - zone_y)) for m in subs)
        return 30.0 + far / 2.2

    def sub_done(self, mo: Moment, e: Engine):
        self.done.add(mo.id)
        self._log(mo, e, e.f)

    def _shoot_logged(self, e: Engine, mo: Moment, c: int, outcome: str, **kw) -> str:
        """`e.shoot`, with the moment logged at the frame of the shot it really makes: since E-6 the shooter may
        first make room for his kick (R-TOUCH-NEAREST), so the kick can come a little after the call."""
        n0 = len(e.events)
        res = e.shoot(c, outcome, **kw)
        shot = next((ev for ev in e.events[n0:] if (ev.get("resolve") or {}).get("what") == "shot"), None)
        self._log(mo, e, shot["f"] if shot is not None else e.f)
        return res

    def _log(self, mo: Moment, e: Engine, f: int, what: str | None = None):
        clock = period_start_clock(e.period) + f * DT
        self.log[mo.id] = {"id": mo.id, "type": mo.type, "period": e.period, "frame": int(f),
                           "planned_clock": mo.clock if what is None else what,
                           "planned_minute": clock_minute(parse_clock(mo.clock if what is None else what), mo.period),
                           "actual_clock_s": round(clock, 2), "actual_minute": clock_minute(clock, e.period),
                           "delta_s": round(clock - parse_clock(mo.clock if what is None else what), 2)}

    # ------------------------------------------------------------------ the step

    def step(self, e: Engine) -> bool:
        clock = e.clock()
        # restore a sprinter's normal top speed once he has slowed down
        keep = []
        for s, v in self.restore:
            if float(np.hypot(*e.V[s])) < v - 0.3:
                e.sprint_vmax.pop(s, None)
            else:
                keep.append((s, v))
        self.restore = keep
        # a substitution is due and no stoppage came: put the ball out
        for mo in self.moments:
            if (mo.type == "substitution" and mo.period == e.period and mo.id not in self.done
                    and clock > max(mo.clock_s + SUB_FORCE_AFTER_S, 60.0 * math.floor(mo.clock_s / 60.0) + 3.0)):
                c = e.carrier
                r = e.choose_receiver(c)
                e.pass_ball(c, r, "flat", "out", out_side="touchline")
                return True
        # pre-positioning of the key players during the lead-in
        for mo, anchor, key in self.story:
            if mo.period != e.period or mo.id + key in self.done:
                continue
            trig = self._trigger(anchor, key)
            if trig - 15.0 <= clock < trig:
                self._preposition(e, mo, key)
        # a story moment is due
        for mo, anchor, key in self.story:
            if mo.period != e.period or mo.id + key in self.done:
                continue
            if clock >= self._trigger(anchor, key):
                self.done.add(mo.id + key)
                self.busy = True
                self.current_id = mo.id
                self.current_core = self._core(anchor, key)
                try:
                    keep_out = {e.roster.slot_of_player(mo.sprint_player_id)} if key == "sprint" else set()
                    self._filler(e, self._start_side(mo, key), self._core(anchor, key), exclude=keep_out)
                    getattr(self, "_run_" + key.replace(":", "_"))(e, mo)
                finally:
                    self.busy = False
                    self.current_id = None
                    self.current_core = None
                    e.overrides = {}
                return True
        return False

    # ------------------------------------------------------------------ building blocks

    def _preposition(self, e: Engine, mo: Moment, key: str):
        spots = {
            "goal:counter_attack": [(mo.player_id, (8.0, 3.0)), (mo.assist_id, (-2.0, -22.0))],
            "goal:long_range": [(mo.player_id, (22.0, 2.0))],
            "woodwork": [(mo.player_id, (30.0, 6.0))],
            "save": [(mo.player_id, (37.0, -4.0)), (mo.goalkeeper_id, (-50.0, 0.0))],  # the keeper stays in goal
            "var_penalty_check": [(mo.player_id, (38.0, 6.0))],
            "penalty": [(mo.fouled_id, (37.0, -7.0))],
            "nutmeg": [(mo.player_id, (20.0, 22.0))],
            "sprint": [(mo.sprint_player_id, (-1.0, -24.0))],
            "caution": [(mo.opponent_id, (4.0, 3.0))],
        }.get(key, [])
        for pid, xy_att in spots:
            if pid is None:
                continue
            s = e.roster.slot_of_player(pid)
            if s is None or s == e.carrier:
                continue
            side = e.side_of(s)
            if key == "sprint" and s in e.overrides:
                continue
            e.overrides[s] = {"point": e.raw(side, np.array(xy_att)), "effort": 1.25}

    def _filler(self, e: Engine, side: str, until: float, exclude: set | None = None):
        """Safe possession for `side` (short passes, no shot) until the core of the sequence is due."""
        if e.clock() >= until - 1.0:
            return
        self._ensure(e, side)
        guard = 0
        while e.clock() < until - 1.0 and guard < 60:
            guard += 1
            if e.pending is not None:
                e.run_restart()
                self._ensure(e, side)
                continue
            c = e.carrier
            rem = until - e.clock()
            fwd = e.att(side, np.array([float(e.rng.uniform(0.5, 2.5)), float(e.rng.normal(0, 1.0))]))
            e.hold(c, float(min(max(rem - 0.5, 0.2), e.rng.uniform(0.8, 1.8))), dest=e.P[c] + fwd, speed=2.0)
            if e.clock() >= until - 1.0:
                break
            r = e.choose_receiver(c, max_dist=26.0, pref=13.0, exclude=exclude)
            e.pass_ball(c, r, "flat", "success")

    def _ensure(self, e: Engine, side: str, prefer: int | None = None):
        """Make `side` have the ball (a won duel or an interception)."""
        if e.pending is not None:
            e.run_restart()
        c = e.carrier
        if c is None or c < 0:
            c = min(range(e.n), key=lambda q: float(np.hypot(*(e.P[q] - e.ball[:2]))))
            e.log_ball("touch", c)
            e.carrier, e.poss = c, e.side_of(c)
        if e.side_of(c) == side:
            return
        if e.roster.slot_role[c] == "GK":
            r = e.choose_receiver(c)
            e.pass_ball(c, r, "high", "intercept", interceptor=e._nearest(side, e.P[r]))
            if e.carrier >= 0 and e.side_of(e.carrier) == side:
                return
            c = e.carrier
        w = prefer if prefer is not None else e._nearest(side, e.ball[:2])
        e.duel(c, w, "def", approach_s=max(0.5, min(2.0, float(np.hypot(*(e.P[w] - e.P[c]))) / 6.0)))

    def _deliver(self, e: Engine, target: int, xy_att, kind: str | None = None):
        """Get the ball to `target` near xy_att (attacking coordinates of his team)."""
        side = e.side_of(target)
        for _ in range(5):
            if e.pending is not None or e.carrier is None or e.carrier < 0 or e.side_of(e.carrier) != side:
                self._ensure(e, side)  # the last pass went out or was lost: win the ball back first
            c = e.carrier
            if c == target:
                return
            want = e.raw(side, np.array(xy_att, float))
            D = float(np.hypot(*(want - e.ball[:2])))
            if D > 42.0:
                mid = 0.5 * (e.ball[:2] + want)
                pool = [s for s in e.side_slots[side] if s not in (c, target) and e.roster.slot_role[s] != "GK"]
                onside = [s for s in pool if e.offside_margin(s) <= 0.0]  # never a pass to an offside team-mate
                m = min(onside or pool, key=lambda s: float(np.hypot(*(e.P[s] - mid))))
                e.pass_ball(c, m, "flat" if float(np.hypot(*(e.P[m] - e.ball[:2]))) < 30 else "high", "success",
                            z_end=bp.GROUND_Z)
                e.hold(m, 0.5)
                continue
            for _ in range(8):  # the carrier waits until the target is back onside (he holds the line)
                if e.offside_margin(target) <= 0.0:
                    break
                e.hold(c, 0.4)
            k = kind or ("flat" if D < 30.0 else "high")
            T = (0.35 + D / 12.0) if k == "flat" else (0.75 + D / 22.0)
            reach = 0.7 * e.arrays()["vmax"][target] * T
            d = want - e.P[target]
            dist = float(np.hypot(*d))
            point = e.P[target] + (d / dist * min(dist, reach) if dist > 1e-6 else 0.0)
            point = e.pitch.clamp(point, margin=-1.5)
            e.pass_ball(c, target, k, "success", point=point, z_end=bp.GROUND_Z)
            if e.carrier == target:
                return

    # ------------------------------------------------------------------ sequences

    def _run_nutmeg(self, e: Engine, mo: Moment):
        side = mo.team
        kw, dr = e.slot(mo.player_id), e.slot(mo.opponent_id)
        self._ensure(e, side)
        y_side = 1.0 if e.att(side, e.P[kw])[1] >= 0 else -1.0
        self._deliver(e, kw, (24.0, 22.0 * y_side))
        to_def = e.P[dr] - e.P[kw]
        e.hold(kw, 1.1, dest=e.P[kw] + 0.5 * to_def, speed=3.0, presser=dr, presser_arrive=True)
        # the nutmeg: ball between the defender's legs to a point behind him
        u = e.P[dr] - e.ball[:2]
        dist = float(np.hypot(*u))
        u = u / dist if dist > 1e-6 else e.att(side, np.array([1.0, 0.0]))
        point = e.pitch.clamp(e.P[dr] + u * 3.8, margin=-1.0)
        path, n = e.ball_path(e.ball, point, "nutmeg", v_arr=2.2)
        self._log(mo, e, e.f)
        e.event("Nutmeg", None, kw, xy=e.ball[:2], attrs={"Opponent": e.pid(dr)})
        e.duel_event("ground", kw, dr, kw, attrs={"DribbleEvaluation": "successful"})
        e.log_ball("touch", kw)  # the nutmeg itself: a touch by Kinsella-Ward
        T, eff = e.live_targets(path[:, :2], side, exclude_press={dr})
        side_step = np.array([-u[1], u[0]]) * 1.6
        T[: n // 2, kw] = e.P[dr] + side_step
        T[n // 2:, kw] = point
        T[:, dr] = e.P[dr] - u * 0.5
        eff[kw], eff[dr] = 2.0, 0.35
        e.advance(n, path, T, eff, live=True, poss=side, arrive={kw: n})
        e.log_ball("touch", kw)
        e.carrier, e.poss = kw, side
        e.hold(kw, 1.0, dest=e.P[kw] + e.att(side, np.array([3.0, -2.0 * y_side])), speed=4.5)
        e.cross(kw, outcome="cleared")

    def _run_goal_counter_attack(self, e: Engine, mo: Moment):
        side, opp = mo.team, other(mo.team)
        f_slot = e.slot(mo.interceptor_id) if mo.interceptor_id else None
        vd, bw = e.slot(mo.assist_id), e.slot(mo.player_id)
        c = e.carrier
        if c is not None and c >= 0 and e.side_of(c) == opp:
            pool = [s for s in e.side_slots[opp] if s != c and e.roster.slot_role[s] != "GK"]
            r = min(pool, key=lambda s: float(np.hypot(*(e.P[s] - e.P[f_slot]))))
            res = e.pass_ball(c, r, "flat", "intercept", interceptor=f_slot)
            if res != "intercepted":
                self._ensure(e, side, prefer=f_slot)
        else:
            self._ensure(e, side, prefer=f_slot)
        start = e.carrier
        e.overrides[bw] = {"point": e.raw(side, np.array([34.0, 4.0])), "effort": 1.7}
        e.hold(start, 0.45)
        # through ball into the run of the wide player
        b_att = e.att(side, e.ball[:2])
        vd_att = e.att(side, e.P[vd])
        target_att = np.array([min(max(vd_att[0], b_att[0]) + 20.0, 33.0), -24.0 if vd_att[1] < 0 else 24.0])
        point = self._reachable(e, vd, e.raw(side, target_att), "through")
        e.pass_ball(start, vd, "through", "success", point=point, sprint_receiver=True)
        # carry towards the box, the striker attacks the far side
        e.overrides[bw] = {"point": e.raw(side, np.array([42.0, 1.5 * (-1 if target_att[1] < 0 else 1)])), "effort": 2.0}
        dest = e.raw(side, np.array([43.5, -13.0 if target_att[1] < 0 else 13.0]))
        dist = float(np.hypot(*(dest - e.P[vd])))
        e.hold(vd, dist / 6.8, dest=dest, speed=6.8)
        # low cross into the striker's run
        bw_pt = self._reachable(e, bw, e.raw(side, np.array([45.0, 0.5 * (-1 if target_att[1] < 0 else 1)])), "low_cross")
        e.pass_ball(vd, bw, "low_cross", "success", point=bw_pt, sub="Cross", v_arr=7.0)
        self._shoot_logged(e, mo, bw, "goal", body=mo.body_part, speed=21.5)

    def _reachable(self, e: Engine, s: int, want: np.ndarray, kind: str) -> np.ndarray:
        """Point near `want` that player s can reach by the time the ball gets there."""
        point = np.asarray(want, float)
        for _ in range(4):
            D = float(np.hypot(*(point - e.ball[:2])))
            if kind in ("flat", "through", "low_cross"):
                v_arr = 4.0 if kind == "through" else 6.5
                arr = bp.ground_arrival(D, min(bp.ground_v0(D, v_arr), 23.0))
                T = arr[0] if arr else 2.0
            else:
                T = 0.75 + D / 22.0
            reach = 0.85 * e.arrays()["vmax"][s] * max(T - 0.5, 0.2)
            d = np.asarray(want) - e.P[s]
            dist = float(np.hypot(*d))
            point = e.P[s] + (d / dist * min(dist, reach) if dist > 1e-6 else 0.0)
        return e.pitch.clamp(point, margin=-1.5)

    def _run_caution(self, e: Engine, mo: Moment):
        fouled_side = other(mo.team)
        w, sh = e.slot(mo.player_id), e.slot(mo.opponent_id)
        self._ensure(e, fouled_side)
        y = float(np.clip(e.att(fouled_side, e.P[sh])[1], -12, 12))
        self._deliver(e, sh, (5.0, y))
        e.duel(sh, w, "def", approach_s=1.1, foul=True)
        card_offset = 18.5
        self._log(mo, e, e.f + int(card_offset * FS))
        e.foul(w, sh, extras=[(card_offset, {"etype": "Caution", "sub": "yellow", "slot_pid": mo.player_id,
                                             "attrs": {"CardColor": "yellow", "Reason": "foul"}})], min_dead_s=46.0)
        e.cards[mo.player_id] = 1

    def _run_goal_corner(self, e: Engine, mo: Moment):
        side, opp = mo.team, other(mo.team)
        taker, scorer = e.slot(mo.assist_id), e.slot(mo.player_id)
        self._ensure(e, side)
        wide_roles = ("RW", "RM", "RB", "RWB")
        pool = [s for s in e.side_slots[side] if e.roster.slot_role[s] in wide_roles and s != taker]
        crosser = pool[0] if pool else e.choose_receiver(e.carrier)
        if e.carrier != crosser:
            self._deliver(e, crosser, (35.0, -25.0))
        e.hold(crosser, 0.7, dest=e.P[crosser] + e.att(side, np.array([2.5, 0.5])), speed=3.5)
        # the cross is headed out for a corner on the right
        point = e.raw(side, np.array([45.5, -5.0]))
        dfd = e._nearest(opp, point)
        e._make_kick_room(crosser, lambda: e._straight_path(point, 20.0))  # the cross is his (E-6, R-TOUCH-NEAREST)
        path, n = e.ball_path(e.ball, point, "cross", z_end=2.1)
        e._kick_event(crosser, None, "Play", "Cross", {"Evaluation": None, "Height": "high", "Recipient": e.pid(scorer)})
        T, eff = e.live_targets(path[:, :2], side, press_point=point)
        T[:, dfd] = point
        eff[dfd] = 2.0
        e.advance(n, path, T, eff, live=True, poss=side, arrive={dfd: n})
        e.clear(dfd, header=True, aim=e.raw(side, np.array([55.5, -31.0])), out="goal_line")
        r = e.pending
        if r is None or r.kind != "CornerKick":
            r = Restart("CornerKick", side, np.array([e.goal_x(side) - np.sign(e.goal_x(side)) * 0.3,
                                                       e.raw(side, np.array([0.0, -33.7]))[1]]))
            e.pending = r
        # the corner set-up time adapts so that the header lands on the scripted clock
        r.taker, r.duration_s = taker, float(np.clip(mo.clock_s - e.clock() - 2.2, 14.0, 40.0))

        def corner_goal(eng: Engine, tk: int, rr: Restart):
            gx = eng.goal_x(side)
            hp = eng.raw(side, np.array([46.8, -0.8]))
            hp = self._reachable(eng, scorer, hp, "cross") if np.hypot(*(eng.P[scorer] - hp)) > 6 else hp
            eng.pass_ball(tk, scorer, "cross", "success", point=hp, restart=rr, sub="Cross", z_end=2.35, T=1.5)
            if not eng._header_contact(scorer):  # a scripted header is a real one (E-6): never a phantom
                raise RuntimeError(f"{mo.id}: the corner does not reach {mo.player_id} for the header "
                                   f"({float(np.hypot(*(eng.P[scorer] - eng.ball[:2]))):.2f} m, ball "
                                   f"{float(eng.ball[2]):.2f} m high)")
            gk = eng.roster.gk_slot(opp)
            y = -np.sign(eng.P[gk, 1] + 1e-6) * 2.6
            self._shoot_logged(eng, mo, scorer, "goal", body="head", speed=13.5, target=np.array([gx, y, 0.9]))

        r.action = corner_goal

    def _run_woodwork(self, e: Engine, mo: Moment):
        side = mo.team
        sh = e.slot(mo.player_id)
        self._ensure(e, side)
        self._deliver(e, sh, (31.0, 6.0))
        e.hold(sh, 0.6, dest=e.P[sh] + e.att(side, np.array([1.8, -0.4])), speed=3.0)
        self._shoot_logged(e, mo, sh, "woodwork", speed=24.5, body=mo.body_part)
        if e.carrier is not None and e.carrier >= 0 and e.pending is None:
            e.clear(e.carrier)

    def _run_sprint(self, e: Engine, mo: Moment):
        sp = e.slot(mo.sprint_player_id)
        side = e.side_of(sp)
        self._ensure(e, side)
        # the ball goes back to a deep team-mate, the winger waits around halfway
        e.overrides[sp] = {"point": e.raw(side, np.array([-1.0, -24.0 if e.att(side, e.P[sp])[1] <= 0 else 24.0])),
                           "effort": 1.2}
        e.overrides[sp]["effort"] = 1.6
        deep = [s for s in e.side_slots[side] if e.roster.group_of_slot(s) == "CB"]
        for _ in range(2):  # back to the deepest centre back (via a team-mate if far)
            c = e.carrier
            r = min(deep, key=lambda s: float(e.att(side, e.P[s])[0]))
            if c == r:
                break
            if float(np.hypot(*(e.P[r] - e.ball[:2]))) > 34.0:
                mid = 0.5 * (e.P[r] + e.ball[:2])
                pool = [s for s in e.side_slots[side] if s not in (c, sp) and e.roster.slot_role[s] != "GK"]
                r = min(pool, key=lambda s: float(np.hypot(*(e.P[s] - mid))))
            e.pass_ball(c, r, "flat", "success")
        c = e.carrier
        e.hold(c, 2.0, dest=e.P[c] + e.att(side, np.array([2.0, 0.0])), speed=1.5)
        vmax = self.sprint_vmax
        e.sprint_vmax[sp] = vmax
        a0 = e.arrays()["a0"][sp]
        tau = vmax / a0
        y_dir = -1.0 if e.att(side, e.P[sp])[1] <= 0 else 1.0
        run = e.att(side, np.array([1.0, 0.04 * y_dir]))
        run /= np.hypot(*run)
        best = None
        for v_arr in np.linspace(1.5, 6.0, 19):
            for L in np.linspace(30.0, 52.0, 45):
                pt = e.P[sp] + run * L
                pa = e.att(side, pt)
                if pa[0] > 48.0 or abs(pa[1]) > 32.0:
                    continue
                D = float(np.hypot(*(pt - e.ball[:2])))
                v0 = bp.ground_v0(D, v_arr)
                if v0 > 24.0:
                    continue
                arr = bp.ground_arrival(D, v0)
                if arr is None:
                    continue
                t_run = self._sprint_time(L, vmax, tau)
                err = abs(arr[0] - t_run)
                score = err + 0.03 * abs(L - 47.0)
                if best is None or score < best[0]:
                    best = (score, pt, v_arr)
        if best is None:
            pt, v_arr = e.P[sp] + run * 30.0, 3.0
        else:
            _, pt, v_arr = best
        e.overrides.pop(sp, None)
        D = float(np.hypot(*(pt - e.ball[:2])))
        L = float(np.hypot(*(pt - e.P[sp])))
        start_att, ball_att = e.att(side, e.P[sp]), e.att(side, e.ball[:2])
        f0 = e.f
        e.pass_ball(c, sp, "through", "success", point=pt, v_arr=float(v_arr), sprint_receiver=True)
        self._log(mo, e, f0 + int(0.75 * (e.f - f0)), what=mo.sprint_clock)
        self.log[mo.id]["sprint_plan"] = {"run_m": round(L, 1), "pass_m": round(D, 1), "flight_s": round((e.f - f0) * DT, 2),
                                          "start_x_att": round(float(start_att[0]), 1), "ball_x_att": round(float(ball_att[0]), 1),
                                          "a0": round(float(e.arrays()["a0"][sp]), 2),
                                          "vmax_kmh": round(vmax * 3.6, 2)}
        self.restore.append((sp, e.roster.info(sp).vmax))
        y_dir2 = -1.0 if e.att(side, e.P[sp])[1] <= 0 else 1.0
        e.hold(sp, 1.0, dest=e.P[sp] + e.att(side, np.array([3.0, 2.0 * -y_dir2])), speed=5.0)
        e.cross(sp, outcome="cleared")

    @staticmethod
    def _sprint_time(L: float, vmax: float, tau: float) -> float:
        lo, hi = 0.0, 20.0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            d = vmax * (mid - tau * (1 - math.exp(-mid / tau)))
            lo, hi = (mid, hi) if d < L else (lo, mid)
        return 0.5 * (lo + hi)

    def _run_var_penalty_check(self, e: Engine, mo: Moment):
        side, opp = mo.team, other(mo.team)
        u, av = e.slot(mo.player_id), e.slot(mo.opponent_id)
        self._ensure(e, side)
        self._deliver(e, u, (40.5, 5.5))
        self._log(mo, e, e.f + int(0.9 * FS))
        e.duel(u, av, "def", approach_s=0.9, attrs={"PossibleFoul": True})
        e.overrides[u] = {"point": e.P[u].copy(), "effort": 0.05}
        e.hold(av, 0.5)
        e.clear(av, out="touchline", aim=e.raw(opp, np.array([-25.0, 40.0 * (1 if e.att(opp, e.P[av])[1] >= 0 else -1)])))
        r = e.pending
        if r is not None:
            r.duration_s = 68.0
            r.extras = [(6.5, {"etype": "VideoAssistantAction", "sub": "penaltyCheck", "side": "none",
                               "attrs": {"Incident": "penalty", "PlayerInvolved": e.pid(u), "Decision": "checkInProgress"}}),
                        (51.0, {"etype": "PenaltyNotAwarded", "sub": None, "slot_pid": e.pid(u),
                                "attrs": {"Opponent": e.pid(av), "AfterVAR": True, "Decision": "noPenalty"}})]
        e.overrides.pop(u, None)

    def _run_penalty(self, e: Engine, mo: Moment):
        """Foul in the box on `fouled_id` by `opponent_id`, about 95 s of stoppage, then the kick from the spot by
        `player_id` with the scripted intent (`penalty_outcome`); the stoppage adapts so that the kick lands on the
        scripted clock."""
        side, opp = mo.team, other(mo.team)
        fd, fo, tk = e.slot(mo.fouled_id), e.slot(mo.opponent_id), e.slot(mo.player_id)
        self._ensure(e, side)
        self._deliver(e, fd, (39.0, -7.0))
        inside = e.raw(side, np.array([41.5, -6.0]))
        if not e._in_box(e.ball[:2], opp) or float(e.att(side, e.ball[:2])[0]) < 39.0:
            # he carries the ball into the penalty area first
            dist = float(np.hypot(*(inside - e.P[fd])))
            e.hold(fd, max(dist / 5.5, 0.4), dest=inside, speed=5.5)
        e.duel(fd, fo, "def", approach_s=1.0, foul=True, carry_dest=e.P[fd] + e.att(side, np.array([1.0, 0.3])))
        if not e._in_box(e.ball[:2], opp):  # the ball must be in the box when the foul is given
            raise RuntimeError(f"{mo.id}: the foul for the penalty is outside the penalty area")
        dur = float(np.clip(mo.clock_s - e.clock(), *PENALTY_DEAD_S))
        r = e.penalty_foul(fo, fd, taker=tk, duration_s=dur)
        outcome = mo.penalty_outcome or "saved"

        def kick(eng: Engine, taker: int, rr):
            self._log(mo, eng, eng.f)
            eng.penalty_kick(taker, rr, outcome, save="parry_corner" if outcome == "saved" else None,
                             body=mo.body_part)

        r.action = kick

    def _run_save(self, e: Engine, mo: Moment):
        side, opp = mo.team, other(mo.team)
        sh = e.slot(mo.player_id)
        gk = e.slot(mo.goalkeeper_id)
        e.overrides[gk] = {"point": e.raw(opp, np.array([-50.0, 0.0])), "effort": 1.6}  # in goal for the save
        self._ensure(e, side)
        self._deliver(e, sh, (40.0, -4.5))
        e.hold(sh, 0.45, dest=e.P[sh] + e.att(side, np.array([1.2, 0.3])), speed=2.5)
        gx = e.goal_x(side)
        y = save_aim_y(e.ball[:2], e.P[gk], gx)
        self._shoot_logged(e, mo, sh, "saved", target=np.array([gx, y, 0.55]), speed=23.5, save="parry_corner")

    def _run_goal_long_range(self, e: Engine, mo: Moment):
        side = mo.team
        sh = e.slot(mo.player_id)
        self._ensure(e, side)
        self._deliver(e, sh, (25.0, 1.5))
        e.hold(sh, 0.7, dest=e.P[sh] + e.att(side, np.array([1.6, 0.0])), speed=2.3)
        gx = e.goal_x(side)
        gk = e.roster.gk_slot(other(side))
        y = -np.sign(e.P[gk, 1] + 1e-6) * 2.85
        self._shoot_logged(e, mo, sh, "goal", target=np.array([gx, y, 1.85]), speed=mo.shot_speed_ms or 31.5,
                           body=mo.body_part)

    def _run_goal_open_play(self, e: Engine, mo: Moment):
        side = mo.team
        sh = e.slot(mo.player_id)
        self._ensure(e, side)
        self._deliver(e, sh, (38.0, 3.0))
        e.hold(sh, 0.5)
        self._shoot_logged(e, mo, sh, "goal", body=mo.body_part)
