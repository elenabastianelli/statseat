"""Ball physics of the match generator.

Trajectories are sampled at 25 Hz: the kick happens at frame 0 (ball at the kicker's
foot) and a trajectory returns the positions of frames 1..N. Coordinates are canonical
(centre origin, metres); z is the height of the ball centre, so a ball on the grass has
z = BALL_RADIUS_M (0.11 m).

Models (literature defaults; the speeds can be calibrated through `calibration/ball.json`):
- rolling ball: dv/dt = -a_roll - c v^2, closed form;
- ball in the air: horizontal dv/dt = -c v^2 and a vertical parabola, closed form;
- free flight after bounces, posts, parries and deflections: numerical integration with
  quadratic drag, gravity, bounce restitution and rolling resistance.
c = rho * Cd * A / (2 m) = 1.2 * 0.25 * 0.038 / (2 * 0.43) ~ 0.013 1/m.
"""

from __future__ import annotations

import math

import numpy as np

from .pitch import BALL_RADIUS_M, CROSSBAR_M, DT, GOAL_HALF_WIDTH_M, POST_RADIUS_M

G = 9.81
C_DRAG = 0.013
ROLL_DECEL = 0.65
BOUNCE_RESTITUTION = 0.55
BOUNCE_FRICTION = 0.78
GROUND_Z = BALL_RADIUS_M
NET_DEPTH_M = 1.8


# --- Rolling ball -------------------------------------------------------------------


def ground_v0(distance: float, v_arrive: float, a: float = ROLL_DECEL, c: float = C_DRAG) -> float:
    """Initial speed of a rolling ball that reaches `distance` with speed `v_arrive`."""
    k2 = a / c
    return math.sqrt((v_arrive ** 2 + k2) * math.exp(2.0 * c * distance) - k2)


def ground_arrival(distance: float, v0: float, a: float = ROLL_DECEL, c: float = C_DRAG) -> tuple[float, float] | None:
    """(time, speed) when a rolling ball kicked at v0 reaches `distance`; None if it stops before."""
    k2 = a / c
    v2 = (v0 ** 2 + k2) * math.exp(-2.0 * c * distance) - k2
    if v2 <= 0.0:
        return None
    k = math.sqrt(c / a)
    w = math.sqrt(a * c)
    return (math.atan(v0 * k) - math.atan(math.sqrt(v2) * k)) / w, math.sqrt(v2)


def ground_path(t: np.ndarray, v0: float, a: float = ROLL_DECEL, c: float = C_DRAG) -> tuple[np.ndarray, np.ndarray]:
    """Distance and speed of a rolling ball at times t (it stops and stays)."""
    k = math.sqrt(c / a)
    w = math.sqrt(a * c)
    phi0 = math.atan(v0 * k)
    tt = np.minimum(t, phi0 / w)
    s = np.log(np.cos(phi0 - w * tt) / math.cos(phi0)) / c
    v = np.tan(phi0 - w * tt) / k
    return s, v


def rolling(p0: np.ndarray, target_xy: np.ndarray, v0: float, n: int) -> np.ndarray:
    """N frames of a ball rolling from p0 towards target_xy (it may stop short or roll past)."""
    d = np.asarray(target_xy, float)[:2] - p0[:2]
    dist = float(np.hypot(*d))
    u = d / dist if dist > 1e-9 else np.array([1.0, 0.0])
    t = DT * np.arange(1, n + 1)
    s, _ = ground_path(t, v0)
    out = np.empty((n, 3))
    out[:, 0] = p0[0] + u[0] * s
    out[:, 1] = p0[1] + u[1] * s
    out[:, 2] = GROUND_Z
    return out


# --- Ball in the air ------------------------------------------------------------------


def air_plan(distance: float, flight_time: float, z0: float, z1: float, c: float = C_DRAG) -> tuple[float, float]:
    """Horizontal and vertical launch speed so that the ball covers `distance` in `flight_time`
    and is at height z1 at the end."""
    vh0 = (math.exp(c * distance) - 1.0) / (c * flight_time)
    vz0 = (z1 - z0 + 0.5 * G * flight_time ** 2) / flight_time
    return vh0, vz0


def air_state(t: np.ndarray | float, vh0: float, vz0: float, z0: float, c: float = C_DRAG):
    """Horizontal distance, height, horizontal speed and vertical speed at times t."""
    s = np.log1p(c * vh0 * t) / c
    z = z0 + vz0 * t - 0.5 * G * np.square(t)
    vh = vh0 / (1.0 + c * vh0 * t)
    vz = vz0 - G * t
    return s, z, vh, vz


def flight_time_for_speed(distance: float, z0: float, z1: float, speed: float) -> float:
    """Shortest flight time whose launch speed equals `speed` (fast, flat branch); if the speed is
    too low to reach the point, the time of the slowest possible launch is returned."""
    def launch(T: float) -> float:
        vh, vz = air_plan(distance, T, z0, z1)
        return math.hypot(vh, vz)

    grid = np.linspace(0.04, 6.0, 300)
    speeds = np.array([launch(T) for T in grid])
    i_min = int(np.argmin(speeds))
    if speeds[i_min] >= speed:
        return float(grid[i_min])
    lo, hi = 0.02, float(grid[i_min])
    if launch(lo) < speed:
        return lo
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if launch(mid) > speed:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def aerial(p0: np.ndarray, target_xy: np.ndarray, vh0: float, vz0: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    """N frames of a ball in the air from p0 towards target_xy (no ground contact handling) and the
    velocity at the last frame."""
    d = np.asarray(target_xy, float)[:2] - p0[:2]
    dist = float(np.hypot(*d))
    u = d / dist if dist > 1e-9 else np.array([1.0, 0.0])
    t = DT * np.arange(1, n + 1)
    s, z, vh, vz = air_state(t, vh0, vz0, p0[2])
    out = np.empty((n, 3))
    out[:, 0] = p0[0] + u[0] * s
    out[:, 1] = p0[1] + u[1] * s
    out[:, 2] = z
    vel = np.array([u[0] * vh[-1], u[1] * vh[-1], vz[-1]]) if n else np.zeros(3)
    return out, vel


# --- Free flight (bounces, deflections, rebounds) ----------------------------------


def fly(p0, v0, n: int, substeps: int = 8, net_x: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Numerical free flight for N frames: drag, gravity, bounces and rolling.

    `net_x` (signed goal-line x) makes a goal net catch the ball: once the ball is more than
    NET_DEPTH_M behind that line it stops and drops.
    """
    x, y, z = (float(v) for v in p0)
    vx, vy, vz = (float(v) for v in v0)
    h = DT / substeps
    out = np.empty((n, 3))
    for k in range(n):
        for _ in range(substeps):
            on_ground = z <= GROUND_Z + 1e-6 and abs(vz) < 0.4
            if on_ground:
                z, vz = GROUND_Z, 0.0
                vh = math.hypot(vx, vy)
                if vh > 0.0:
                    new = max(vh - (ROLL_DECEL + C_DRAG * vh * vh) * h, 0.0)
                    vx, vy = vx * new / vh, vy * new / vh
            else:
                sp = math.sqrt(vx * vx + vy * vy + vz * vz)
                vx -= C_DRAG * sp * vx * h
                vy -= C_DRAG * sp * vy * h
                vz -= (C_DRAG * sp * vz + G) * h
            x += vx * h
            y += vy * h
            z += vz * h
            if z < GROUND_Z:
                z = GROUND_Z
                if vz < -0.4:
                    vz = -vz * BOUNCE_RESTITUTION
                    vx *= BOUNCE_FRICTION
                    vy *= BOUNCE_FRICTION
                else:
                    vz = 0.0
            if net_x is not None and abs(x) > abs(net_x) + NET_DEPTH_M and x * net_x > 0:
                x = math.copysign(abs(net_x) + NET_DEPTH_M, net_x)
                vx, vy = 0.0, vy * 0.2
                vz = min(vz, 0.0)
        out[k] = (x, y, z)
    return out, np.array([vx, vy, vz])


# --- Shots ---------------------------------------------------------------------------------


def plan_shot(p0: np.ndarray, target: np.ndarray, speed: float) -> tuple[float, float, float]:
    """Launch (vh0, vz0) and time to reach the 3-D target point at the given launch speed."""
    dist = float(np.hypot(*(np.asarray(target[:2]) - p0[:2])))
    T = flight_time_for_speed(dist, float(p0[2]), float(target[2]), speed)
    vh0, vz0 = air_plan(dist, T, float(p0[2]), float(target[2]))
    return vh0, vz0, T


def post_contact(p0: np.ndarray, target: np.ndarray, vh0: float, vz0: float, post_xy: np.ndarray,
                 t_max: float) -> tuple[float, np.ndarray, np.ndarray] | None:
    """First contact of the planned shot with a goal post (1 ms resolution): (time, position, velocity)."""
    d = np.asarray(target[:2], float) - p0[:2]
    u = d / np.hypot(*d)
    t = np.arange(0.0, t_max, 0.001)
    s, z, vh, vz = air_state(t, vh0, vz0, float(p0[2]))
    px = p0[0] + u[0] * s
    py = p0[1] + u[1] * s
    dist = np.hypot(px - post_xy[0], py - post_xy[1])
    hit = np.nonzero((dist <= BALL_RADIUS_M + POST_RADIUS_M) & (z <= CROSSBAR_M))[0]
    if hit.size == 0:
        return None
    i = int(hit[0])
    return float(t[i]), np.array([px[i], py[i], z[i]]), np.array([u[0] * vh[i], u[1] * vh[i], vz[i]])


def reflect_on_post(pos: np.ndarray, vel: np.ndarray, post_xy: np.ndarray, restitution: float = 0.6) -> np.ndarray:
    """Velocity after hitting a vertical cylinder (the post): the normal component is reversed and damped."""
    n = np.array([pos[0] - post_xy[0], pos[1] - post_xy[1]])
    n /= np.hypot(*n)
    vn = vel[0] * n[0] + vel[1] * n[1]
    out = vel.copy()
    if vn < 0:
        out[0] -= (1.0 + restitution) * vn * n[0]
        out[1] -= (1.0 + restitution) * vn * n[1]
        out[:2] *= 0.9
    return out


def goal_mouth_ok(y: float, z: float) -> bool:
    """True when the whole ball is between the posts and under the bar."""
    return abs(y) <= GOAL_HALF_WIDTH_M - BALL_RADIUS_M and z <= CROSSBAR_M - BALL_RADIUS_M
