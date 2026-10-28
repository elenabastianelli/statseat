"""Players of the match generator: roster slots, physical parameters, role mean positions,
targets and the velocity/acceleration-limited controller.

- Each of the 22 slots (11 home, 11 away) holds the player currently in that formation slot.
- Physical parameters: the top speed and the acceleration capacity of each player come from his
  1-99 `pace` and `acceleration` attributes through `attributes.physical_value` (the DFL table of
  his position group, CM = mean of DM and AM). Without attributes (an old universe) pace is
  estimated from the `attribute_profile`. Acceleration follows a0 * (1 - v / vmax).
- Targets: the mean position of the role given the ball position and possession, in
  team-attacking coordinates (defaults reproduce the DFL mean positions of guidelines 16.1;
  `calibration/position_maps.json` replaces them when present), plus overlays chosen by the
  engine (receiving runs, pressing, goalkeeper, set pieces, scripted runs).
- Controller: desired speed grows with the distance to the target, first-order velocity
  response, tangential acceleration within [-braking, a0 (1 - v / vmax)], limited lateral
  acceleration, hard speed cap. All bounds stay inside Materials/PHYSICAL_LIMITS.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter1d

from ..attributes import physical_value
from ..schema import POSITION_GROUP
from .calibration import UNSCRIPTED_VMAX_MS, Calibration
from .pitch import DT, FS

# Role mean positions in team-attacking coordinates (+x attack, +y the team's left) with the ball
# on the centre spot: (in possession, out of possession).
ROLE_BASE: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "GK": ((-37.0, 0.0), (-41.0, 0.0)),
    "RCB": ((-14.0, -11.0), (-20.0, -8.0)), "LCB": ((-14.0, 11.0), (-20.0, 8.0)), "CB": ((-15.0, 0.0), (-21.0, 0.0)),
    "RB": ((-2.0, -24.0), (-17.0, -17.0)), "LB": ((-2.0, 24.0), (-17.0, 17.0)),
    "RWB": ((2.0, -27.0), (-15.0, -22.0)), "LWB": ((2.0, 27.0), (-15.0, 22.0)),
    "CDM": ((-3.0, 0.0), (-11.0, 0.0)), "RDM": ((-2.0, -7.0), (-10.0, -6.0)), "LDM": ((-2.0, 7.0), (-10.0, 6.0)),
    "RCM": ((6.0, -10.0), (-6.0, -8.0)), "LCM": ((6.0, 10.0), (-6.0, 8.0)),
    "RM": ((10.0, -23.0), (-8.0, -17.0)), "LM": ((10.0, 23.0), (-8.0, 17.0)),
    "CAM": ((12.0, 0.0), (-2.0, 0.0)),
    "RW": ((15.0, -19.0), (-5.0, -13.0)), "LW": ((15.0, 19.0), (-5.0, 13.0)),
    "ST": ((21.0, 0.0), (4.0, 0.0)), "RS": ((19.0, -6.0), (2.0, -6.0)), "LS": ((19.0, 6.0), (2.0, 6.0)),
}
# How much the role follows the ball (x, y), and the x range of the role, by group.
GROUP_FOLLOW = {"GK": (0.2, 0.1), "CB": (0.52, 0.28), "FB": (0.55, 0.30), "DM": (0.58, 0.42), "CM": (0.62, 0.45),
                "AM": (0.62, 0.45), "W": (0.58, 0.28), "ST": (0.52, 0.36)}
GROUP_XRANGE = {"GK": (-51.5, -18.0), "CB": (-49.0, 22.0), "FB": (-49.0, 42.0), "DM": (-47.0, 32.0),
                "CM": (-45.0, 43.0), "AM": (-38.0, 46.0), "W": (-42.0, 48.0), "ST": (-28.0, 48.0)}
# Team spread (L-3, realism report E-2): the calibrated maps are *mean* positions, so a team that stands on them is
# more compact than a real one (team length in possession 29.7 m against 34-37 m in DFL, width 39.5 against
# 44.5-51; out of possession 27.7 against 31-34.5 and 32.4 against 36-40). The outfield targets are stretched
# around the team's outfield centroid: (length factor, width factor) by phase.
TEAM_SPREAD = {"in_possession": (1.22, 1.20), "out_of_possession": (1.16, 1.15)}
GROUP_WORK = {"GK": 1.15, "CB": 1.0, "FB": 0.98, "DM": 1.3, "CM": 1.36, "AM": 1.75, "W": 1.2, "ST": 1.2}

# Controller constants (defaults v0.1, tuned on the realism targets of the plan).
GAIN = 0.9  # 1/s: desired speed slope near the target
C0 = 1.4  # m/s: speed cap at the target
C1 = 0.27  # 1/s: speed cap growth with distance
TAU = 0.45  # s: velocity response time
A_LAT = 4.5  # m/s^2: lateral (turning) acceleration limit
SEP_M = 1.1  # players closer than this push each other apart (no overlaps: DFL ~57 episodes < 0.3 m per match)
SEP_GAIN = 5.0  # 1/s


@dataclass
class PlayerInfo:
    player_id: str
    side: str  # home | away
    shirt: int
    natural_position: str
    group: str
    foot: str
    styles: list[str]
    quality: float
    tier: str
    pace: float
    top_speed_kmh: float
    vmax: float
    a0: float
    dmax: float
    work: float
    pace_source: str


@dataclass
class Roster:
    players: dict[str, PlayerInfo]
    slot_role: list[str]  # formation slot code per slot (fixed)
    slot_side: list[str]
    slot_player: list[str]  # current player per slot
    bench: dict[str, list[str]]
    set_pieces: dict[str, dict[str, list[str]]]
    team_ids: dict[str, str]
    formation: dict[str, str]
    used_subs: dict[str, list[str]] = field(default_factory=lambda: {"home": [], "away": []})

    @property
    def n(self) -> int:
        return len(self.slot_role)

    def slots_of(self, side: str) -> list[int]:
        return [i for i, s in enumerate(self.slot_side) if s == side]

    def gk_slot(self, side: str) -> int:
        return next(i for i in self.slots_of(side) if self.slot_role[i] == "GK")

    def slot_of_player(self, player_id: str) -> int | None:
        return next((i for i, p in enumerate(self.slot_player) if p == player_id), None)

    def group_of_slot(self, slot: int) -> str:
        return POSITION_GROUP[self.slot_role[slot]]

    def info(self, slot: int) -> PlayerInfo:
        return self.players[self.slot_player[slot]]

    def arrays(self) -> dict[str, np.ndarray]:
        """Per-slot physical arrays of the players currently on the pitch."""
        infos = [self.players[p] for p in self.slot_player]
        return {"vmax": np.array([i.vmax for i in infos]), "a0": np.array([i.a0 for i in infos]),
                "dmax": np.array([i.dmax for i in infos]), "work": np.array([i.work for i in infos])}

    def first_available(self, side: str, kind: str) -> str | None:
        on = set(self.slot_player)
        return next((p for p in self.set_pieces[side].get(kind, []) if p in on), None)


def estimate_pace(rng: np.random.Generator, profile: dict, group: str) -> float:
    """1-99 pace estimated from the universe attribute profile (until V-E2 fills `attributes`)."""
    styles = profile.get("style", []) or []
    pace = 50.0 + 60.0 * (float(profile.get("quality", 0.7)) - 0.70)
    pace += 22.0 if "pacy" in styles else 0.0
    pace += 6.0 if ("overlapping" in styles or "pressing forward" in styles or "box-to-box" in styles) else 0.0
    pace -= 9.0 if ("target man" in styles or "stopper" in styles) else 0.0
    pace += {"starter": 2.0, "rotation": 0.0, "prospect": 3.0}.get(profile.get("tier", ""), 0.0)
    pace += float(rng.normal(0.0, 7.0))
    return float(np.clip(pace, 4.0, 97.0))


def build_roster(universe: dict, match_info: dict, cal: Calibration, rng: np.random.Generator) -> Roster:
    by_id = {p["id"]: p for p in universe["players"]}
    details = {c["club_id"]: c for c in universe.get("club_details", [])}
    players: dict[str, PlayerInfo] = {}
    slot_role, slot_side, slot_player = [], [], []
    bench: dict[str, list[str]] = {}
    set_pieces: dict[str, dict[str, list[str]]] = {}
    team_ids, formation = {}, {}
    for team in match_info["teams"]:
        side = "home" if team["role"] == "home" else "away"
        team_ids[side] = team["team_id"]
        formation[side] = team["formation"]
        set_pieces[side] = details.get(team["team_id"], {}).get("set_pieces", {"penalties": [], "free_kicks": [],
                                                                                 "corners": []})
        bench[side] = []
        for entry in sorted(team["lineup"], key=lambda e: e["player_id"]):
            p = by_id[entry["player_id"]]
            group = p.get("position_group") or POSITION_GROUP[p["position"]]
            attrs = p.get("attributes") or {}
            scales = cal.attribute_scales
            top = acc = None
            if attrs.get("pace") is not None and scales:
                pace, source = float(attrs["pace"]), "attribute"
                top = physical_value(scales, "pace", group, pace)
                if attrs.get("acceleration") is not None:
                    acc = physical_value(scales, "acceleration", group, float(attrs["acceleration"]))
            else:
                pace, source = estimate_pace(rng, p.get("attribute_profile", {}), group), "estimated_from_profile"
            if top is None:
                top = cal.top_speed_for_pace(group, pace)
            vmax = min(top / 3.6 + 0.06, UNSCRIPTED_VMAX_MS)
            if acc is None:  # no acceleration attribute: from the group scale, correlated with pace
                acc_pace = float(np.clip(pace + rng.normal(0.0, 15.0), 1.0, 99.0))
                acc = cal.acc_for_pace(group, acc_pace)
            a0 = float(np.clip(acc * 0.95, 4.8, 7.2))
            dmax = float(np.clip(6.3 + rng.normal(0, 0.25), 5.8, 7.0))
            styles = list(p.get("attribute_profile", {}).get("style", []) or [])
            work = GROUP_WORK[group] * cal.work_rate(group)
            work *= 1.06 if ("engine" in styles or "box-to-box" in styles or "pressing forward" in styles) else 1.0
            work *= 0.96 if "target man" in styles else 1.0
            players[p["id"]] = PlayerInfo(
                player_id=p["id"], side=side, shirt=entry["shirt_number"], natural_position=p["position"], group=group,
                foot=p.get("preferred_foot", "right"), styles=styles,
                quality=float(p.get("attribute_profile", {}).get("quality", 0.7)),
                tier=p.get("attribute_profile", {}).get("tier", "rotation"), pace=round(pace, 1),
                top_speed_kmh=round(top, 2), vmax=vmax, a0=a0, dmax=dmax, work=work, pace_source=source)
        for entry in team["lineup"]:  # starters in line-up order
            if entry["starting"]:
                slot_role.append(entry["playing_position"])
                slot_side.append(side)
                slot_player.append(entry["player_id"])
            else:
                bench[side].append(entry["player_id"])
    return Roster(players, slot_role, slot_side, slot_player, bench, set_pieces, team_ids, formation)


# --- Role mean positions -------------------------------------------------------------------


class PositionModel:
    """Mean position of each role given the ball (team-attacking coordinates) and possession."""

    def __init__(self, cal: Calibration, half_length: float, half_width: float):
        self.hl, self.hw = half_length, half_width
        self.maps = cal.position_maps
        self._grid = None
        if self.maps:
            try:
                g = self.maps["grid"]
                self._grid = (np.asarray(g["x_edges"], float), np.asarray(g["y_edges"], float))
            except (KeyError, TypeError, ValueError):
                self.maps = None

    def _cell_table(self, role: str, group: str, phase: str):
        """(mean_x, mean_y, count) arrays [nx, ny] from the calibration map, or None."""
        if not self.maps or self._grid is None:
            return None
        nx, ny = len(self._grid[0]) - 1, len(self._grid[1]) - 1
        block = (self.maps.get("maps", {}).get(role, {}) or {}).get(phase)
        if block is None:
            block = (self.maps.get("fallback", {}).get(group, {}) or {}).get(phase)
        if block is None:
            return None
        try:
            if isinstance(block, dict) and "mean_x" in block:
                mx = np.asarray(block["mean_x"], float).reshape(nx, ny)
                my = np.asarray(block["mean_y"], float).reshape(nx, ny)
                cnt = np.asarray(block.get("count", np.full((nx, ny), 99)), float).reshape(nx, ny)
                return mx, my, cnt
            cells = block.get("cells", block) if isinstance(block, dict) else block
            mx, my, cnt = np.full((nx, ny), np.nan), np.full((nx, ny), np.nan), np.zeros((nx, ny))
            for c in cells:
                i, j = int(c.get("ix", c.get("i"))), int(c.get("iy", c.get("j")))
                mx[i, j], my[i, j], cnt[i, j] = c["mean_x"], c["mean_y"], c.get("count", 99)
            return mx, my, cnt
        except (KeyError, TypeError, ValueError):
            return None

    def targets(self, roles: list[str], ip: np.ndarray, ball_att: np.ndarray) -> np.ndarray:
        """(N, S, 2) role targets for S slots of one team; ip[s] True when the team has the ball."""
        n, s = ball_att.shape[0], len(roles)
        out = np.empty((n, s, 2))
        bx = np.clip(ball_att[:, 0], -self.hl, self.hl)
        by = np.clip(ball_att[:, 1], -self.hw, self.hw)
        for j, role in enumerate(roles):
            group = POSITION_GROUP[role]
            (ipx, ipy), (opx, opy) = ROLE_BASE[role]
            fx, fy = GROUP_FOLLOW[group]
            if ip[j]:
                x = ipx + fx * bx
                y = ipy + fy * by
            else:
                x = opx + fx * bx * 1.05
                y = opy * (0.9 - 0.15 * np.abs(by) / self.hw) + fy * by * 1.2
            if group == "GK":
                x, y = self._goalkeeper(bx, by, ipx if ip[j] else opx)
            lo, hi = GROUP_XRANGE[group]
            out[:, j, 0] = np.clip(x, lo, hi)
            out[:, j, 1] = np.clip(y, -self.hw + 1.5, self.hw - 1.5)
            table = None if group == "GK" else self._cell_table(role, group, "in_possession" if ip[j] else "out_of_possession")
            if table is not None:
                mx, my, cnt = table
                cx, cy = self._interp(mx, bx, by), self._interp(my, bx, by)
                w = np.clip(self._interp(cnt, bx, by) / 20.0, 0.0, 1.0)
                ok = np.isfinite(cx) & np.isfinite(cy)
                out[ok, j, 0] = w[ok] * cx[ok] + (1 - w[ok]) * out[ok, j, 0]
                out[ok, j, 1] = w[ok] * cy[ok] + (1 - w[ok]) * out[ok, j, 1]
        return self._spread(out, roles, ip)

    def _spread(self, out: np.ndarray, roles: list[str], ip: np.ndarray) -> np.ndarray:
        """Stretch the outfield targets around their centroid (TEAM_SPREAD), then keep them on the pitch."""
        field = np.array([POSITION_GROUP[r] != "GK" for r in roles])
        if field.sum() < 2:
            return out
        fx, fy = TEAM_SPREAD["in_possession" if bool(np.all(ip)) else "out_of_possession"]
        c = out[:, field].mean(axis=1, keepdims=True)  # (N, 1, 2)
        out[:, field, 0] = c[..., 0] + fx * (out[:, field, 0] - c[..., 0])
        out[:, field, 1] = c[..., 1] + fy * (out[:, field, 1] - c[..., 1])
        out[..., 0] = np.clip(out[..., 0], -self.hl + 1.0, self.hl - 1.0)
        out[..., 1] = np.clip(out[..., 1], -self.hw + 1.5, self.hw - 1.5)
        return out

    def _interp(self, table: np.ndarray, bx: np.ndarray, by: np.ndarray) -> np.ndarray:
        xe, ye = self._grid
        xc, yc = 0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1])
        fx = np.clip(np.interp(bx, xc, np.arange(len(xc))), 0, len(xc) - 1)
        fy = np.clip(np.interp(by, yc, np.arange(len(yc))), 0, len(yc) - 1)
        i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        i1, j1 = np.minimum(i0 + 1, len(xc) - 1), np.minimum(j0 + 1, len(yc) - 1)
        tx, ty = fx - i0, fy - j0
        t = np.nan_to_num(table, nan=np.nanmean(table) if np.isfinite(table).any() else 0.0)
        return ((1 - tx) * (1 - ty) * t[i0, j0] + tx * (1 - ty) * t[i1, j0] + (1 - tx) * ty * t[i0, j1]
                + tx * ty * t[i1, j1])

    def _goalkeeper(self, bx: np.ndarray, by: np.ndarray, base_x: float) -> tuple[np.ndarray, np.ndarray]:
        """Goalkeeper: on the ball-goal line when the ball is in his half, a sweeper position otherwise."""
        gx = -self.hl
        dx, dy = bx - gx, by
        dist = np.hypot(dx, dy) + 1e-9
        d = np.clip(1.5 + 0.11 * dist, 1.5, 9.0)
        line_x, line_y = gx + d * dx / dist, d * dy / dist
        sweep_x, sweep_y = base_x + 0.13 * bx, 0.08 * by
        w = np.clip((bx + 22.0) / 22.0, 0.0, 1.0)
        return (1 - w) * line_x + w * sweep_x, (1 - w) * line_y + w * sweep_y


# --- Controller ----------------------------------------------------------------------------


def simulate(P: np.ndarray, V: np.ndarray, T: np.ndarray, effort: np.ndarray, vmax: np.ndarray, a0: np.ndarray,
             dmax: np.ndarray, arrive_k: np.ndarray | None = None, sprint: np.ndarray | None = None) -> np.ndarray:
    """Move the players towards the per-frame targets T (N, S, 2). Updates P and V in place and
    returns the positions of the N frames.

    arrive_k[s] >= 0: slot s must reach its target at frame arrive_k[s] (arrival mode);
    sprint[s]: slot s runs at full speed towards the target until it is close.
    """
    n, s = T.shape[0], P.shape[0]
    out = np.empty((n, s, 2))
    has_arr = arrive_k is not None and bool(np.any(arrive_k >= 0))
    arr_mask = (arrive_k >= 0) if has_arr else None
    has_spr = sprint is not None and bool(np.any(sprint))
    eff = effort
    for k in range(n):
        err = T[k] - P
        dist = np.sqrt(err[:, 0] ** 2 + err[:, 1] ** 2) + 1e-9
        cap = np.minimum(vmax, (C0 + C1 * dist) * eff)
        vmag = cap * np.tanh(GAIN * dist / cap)
        if has_arr:
            trem = (arrive_k - k) * DT
            m = arr_mask & (trem > 0)
            if m.any():
                need = dist / np.maximum(trem, 0.2) * 1.12 + 0.25 * (dist > 0.6)
                vmag = np.where(m, np.minimum(vmax, need), vmag)
        if has_spr:
            vmag = np.where(sprint & (dist > 2.5), vmax, vmag)
        # never faster than what still allows stopping at the target (no overshoot past the lines)
        vmag = np.minimum(vmag, np.sqrt(2.0 * 0.75 * dmax * dist) + 0.4)
        vdes = err * (vmag / dist)[:, None]
        dx = P[:, None, 0] - P[None, :, 0]
        dy = P[:, None, 1] - P[None, :, 1]
        d2 = dx * dx + dy * dy
        np.fill_diagonal(d2, 1e9)
        if (d2 < SEP_M * SEP_M).any():
            d = np.sqrt(d2)
            w = np.where(d < SEP_M, SEP_GAIN * (SEP_M - d) / np.maximum(d, 0.05), 0.0)
            vdes[:, 0] += np.sum(w * dx, axis=1)
            vdes[:, 1] += np.sum(w * dy, axis=1)
        acc = (vdes - V) / TAU
        sp = np.sqrt(V[:, 0] ** 2 + V[:, 1] ** 2)
        am = np.sqrt(acc[:, 0] ** 2 + acc[:, 1] ** 2) + 1e-12
        moving = sp > 0.3
        vhat = np.where(moving[:, None], V / np.maximum(sp, 1e-9)[:, None], acc / am[:, None])
        at = acc[:, 0] * vhat[:, 0] + acc[:, 1] * vhat[:, 1]
        an = acc - at[:, None] * vhat
        at = np.clip(at, -dmax, a0 * np.maximum(1.0 - sp / vmax, 0.04))
        anm = np.sqrt(an[:, 0] ** 2 + an[:, 1] ** 2) + 1e-12
        an *= np.minimum(1.0, A_LAT / anm)[:, None]
        acc = at[:, None] * vhat + an
        V += acc * DT
        sp = np.sqrt(V[:, 0] ** 2 + V[:, 1] ** 2) + 1e-12
        V *= np.minimum(1.0, vmax / sp)[:, None]
        P += V * DT
        out[k] = P
    return out


# --- Noise ---------------------------------------------------------------------------------


def smooth_noise(rng: np.random.Generator, shape: tuple, sigma_frames: float) -> np.ndarray:
    """Gaussian-filtered white noise along axis 0, scaled to unit standard deviation."""
    white = rng.standard_normal(shape)
    out = gaussian_filter1d(white, sigma_frames, axis=0, mode="reflect")
    return out * math.sqrt(2.0 * math.sqrt(math.pi) * sigma_frames)


WANDER_SIGMA_S = 3.0
MEASUREMENT_SIGMA_M = 0.10
MEASUREMENT_CORR_S = 0.8


def measurement_noise(rng: np.random.Generator, xy: np.ndarray) -> np.ndarray:
    """Optical-tracking-like error for one track (F, 2): smooth in time (~0.8 s), sigma ~0.1 m,
    slightly larger at high speed."""
    f = xy.shape[0]
    if f < 3:
        return np.zeros_like(xy)
    v = np.zeros(f)
    v[1:] = np.hypot(*np.diff(xy, axis=0).T) * FS
    v = gaussian_filter1d(v, 5)
    amp = MEASUREMENT_SIGMA_M * (1.0 + 0.6 * np.clip(v, 0, 10) / 9.0)
    return smooth_noise(rng, (f, 2), MEASUREMENT_CORR_S * FS) * amp[:, None]
