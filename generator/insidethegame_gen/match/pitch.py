"""Pitch geometry, the 25 Hz clock and coordinate helpers of the match generator.

Reference system (guidelines section 4, canonical): origin at the centre spot, metres,
x along the length (-L/2 .. +L/2), y along the width (-W/2 .. +W/2), right-handed.
For the fictional Lanternworks Stadium we fix +x towards the Harbour Stand (south goal)
and +y towards the Lamplighters Stand (east touchline). The Main Stand, with the dugouts
and the substitution zone, is on the touchline y = -W/2.

"Attacking coordinates" (x_att, y_att) rotate the pitch by 180 degrees for a team that
attacks towards -x, so that the team always attacks +x; +y_att is then the team's left.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FS = 25  # Hz
DT = 1.0 / FS
FRAME0 = {1: 10000, 2: 100000}  # first frame number of each period (DFL-like)
GOAL_HALF_WIDTH_M = 3.66
CROSSBAR_M = 2.44
POST_RADIUS_M = 0.06
BALL_RADIUS_M = 0.11
PENALTY_AREA_DEPTH_M = 16.5
PENALTY_AREA_HALF_WIDTH_M = 20.16
GOAL_AREA_DEPTH_M = 5.5
GOAL_AREA_HALF_WIDTH_M = 9.16
WALL_DISTANCE_M = 9.15
BENCH_SIDE_Y_SIGN = -1.0  # Main Stand touchline (dugouts, substitution zone)

N_XBANDS = 6
N_CHANNELS = 3
N_ZONES = N_XBANDS * N_CHANNELS


@dataclass(frozen=True)
class Pitch:
    length: float = 105.0
    width: float = 68.0

    @property
    def hl(self) -> float:
        return self.length / 2.0

    @property
    def hw(self) -> float:
        return self.width / 2.0

    def zone(self, x_att: float, y_att: float) -> int:
        """Zone index in attacking coordinates: 6 bands along x (own goal -> opponent goal) x 3 channels
        (right wing, centre, left wing; the centre channel is as wide as the penalty area)."""
        xb = int(min(N_XBANDS - 1, max(0, math.floor((x_att + self.hl) / (self.length / N_XBANDS)))))
        if y_att < -PENALTY_AREA_HALF_WIDTH_M:
            ch = 0
        elif y_att > PENALTY_AREA_HALF_WIDTH_M:
            ch = 2
        else:
            ch = 1
        return xb * N_CHANNELS + ch

    def inside(self, x: float, y: float, margin: float = 0.0) -> bool:
        return abs(x) <= self.hl + margin and abs(y) <= self.hw + margin

    def clamp(self, xy: np.ndarray, margin: float = -0.5) -> np.ndarray:
        """Clamp points to the pitch (a negative margin keeps them inside the lines)."""
        out = np.array(xy, dtype=float, copy=True)
        out[..., 0] = np.clip(out[..., 0], -self.hl - margin, self.hl + margin)
        out[..., 1] = np.clip(out[..., 1], -self.hw - margin, self.hw + margin)
        return out


def zone_band(zone: int) -> int:
    return zone // N_CHANNELS


def zone_channel(zone: int) -> int:
    return zone % N_CHANNELS


# --- Match clock -------------------------------------------------------------------


def parse_clock(text: str) -> float:
    """'MM:SS' or 'MM:SS.s' match clock (period 2 starts at 45:00) -> seconds since the match start."""
    mm, ss = text.split(":")
    return int(mm) * 60.0 + float(ss)


def format_clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{seconds - 60 * int(seconds // 60):04.1f}"


def period_start_clock(period: int) -> float:
    return 0.0 if period == 1 else 45 * 60.0


def displayed_minute(period: int, elapsed_s: float) -> str:
    """Football minute as shown on a scoreboard: 0:00-0:59 is 1', stoppage time is '45+2'."""
    base = 0 if period == 1 else 45
    minute = base + int(elapsed_s // 60) + 1
    limit = 45 if period == 1 else 90
    return f"{limit}+{minute - limit}" if minute > limit else str(minute)


def clock_minute(clock_s: float, period: int) -> str:
    return displayed_minute(period, clock_s - period_start_clock(period))
