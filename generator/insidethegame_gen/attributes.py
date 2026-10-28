"""Player attributes on our own 1-99 scale, calibrated on DFL percentiles (task N-4b).

The scale
---------
An attribute is a **percentile inside the player's position group**, measured on the DFL
open data (6 matches, Bassek et al. 2025, CC-BY 4.0). `calibration/attribute_scales.json`
stores, for each attribute and each group, the 1st to 99th percentile of one physical or
technical quantity per player-match. Attribute k of a player in group g therefore means:

    value = attributes[<name>].by_role_group[g].values[k - 1]

so a winger with pace 90 reaches about the DFL p90 top speed of wingers (`physical_value`).

| Attribute | DFL quantity (per player-match unless stated) | Unit |
|---|---|---|
| pace | top speed: maximum of our valid speed | km/h |
| acceleration | peak positive tangential acceleration on valid samples | m/s2 |
| stamina | distance per 90 minutes of measured time | m |
| work_rate | distance above 20 km/h per 90 minutes of measured time | m |
| passing | pass success share (next touch by a team-mate), player-matches with >= 20 passes | share 0-1 |
| shot_power | peak ball speed in the 0.5 s after the kick of a confirmed open-play shot (per shot) | m/s |

**Groups without a table.** The calibration leaves a table empty when it has fewer than 5
player-matches (CM for every attribute, ST for passing). Such a group uses the percentile-wise
mean of its neighbour groups' tables (`NEIGHBOURS`, from the mean positions of guidelines 16.1:
CM between DM and AM, ST next to AM and W), and the `overall` table only when no neighbour has
one. The overall table is a last resort because its low percentiles are goalkeepers' values.
Goalkeepers never borrow another group's table. `shot_power` has an overall table only (it is
measured per shot), so it is the same scale for every group.

Because the scale is relative to the group, compare players of different positions through
the physical value, not through the number.

How the numbers are made
------------------------
Deterministic: every player has his own seeded stream, `random.Random(f"{seed}:attributes:{id}")`,
separate from every other stream of the universe, so adding attributes changes nothing else.

1. A latent score per attribute, in standard-normal units: the player's `quality` (from
   `attribute_profile`), three shared factors (speed, endurance, technique, so that pace and
   acceleration, or stamina and work rate, go together) and an own noise term.
2. Shifts from his story: the main playing style (full effect) and the second one (half
   effect), the strengths listed in his profile, and an age curve (pace and acceleration drop
   after 28, stamina and work rate after 30, passing improves a little with experience).
3. The latent score becomes a percentile through the normal distribution function, rounded to
   an integer in 1..99. Extremes are rare, as the calibration notes ask.
4. Floors: a strength or a main style that names an attribute (for example the strength "pace"
   or the style "pacy") guarantees a minimum value, so the number never contradicts the text
   that Kit Man and the fans read. The validator checks the same floors.

There is no goalkeeper-specific or aerial attribute: the schema declares these six, and the
DFL calibration has no per-player aggregate for saves or headers.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

ATTRIBUTES = ("pace", "acceleration", "stamina", "work_rate", "passing", "shot_power")
SCALES_PATH = Path(__file__).resolve().parents[2] / "calibration" / "attribute_scales.json"
SCALES_FILE = "calibration/attribute_scales.json"

# Physical limits (Materials/PHYSICAL_LIMITS.md): no mapped value may exceed them.
PLAYER_SPEED_MAX_KMH = 38.0
BALL_SPEED_MAX_MS = 211.0 / 3.6
# Plausibility band of player acceleration (R-ACC-BAND): a DFL peak above it is an "≈" value.
PLAYER_ACC_BAND_MAX_MS2 = 7.5

# Neighbour groups whose tables stand in for a group without one (guidelines 16.1 mean positions).
NEIGHBOURS: dict[str, tuple[str, ...]] = {
    "CB": ("FB", "DM"), "FB": ("CB", "W"), "DM": ("CB", "CM"), "CM": ("DM", "AM"),
    "AM": ("CM", "W"), "W": ("FB", "AM"), "ST": ("AM", "W"),
}

# --- Latent model --------------------------------------------------------------------------------

# A first-choice player of median quality (0.75) sits at the DFL median of his group before his
# story is applied. Weights: quality, shared factors and own noise, in standard-normal units.
QUALITY_REF, QUALITY_SD = 0.75, 0.12
WEIGHTS: dict[str, dict[str, float]] = {
    "pace": {"quality": 0.38, "speed": 0.45, "noise": 0.30},
    "acceleration": {"quality": 0.36, "speed": 0.40, "noise": 0.35},
    "stamina": {"quality": 0.36, "endurance": 0.45, "noise": 0.30},
    "work_rate": {"quality": 0.36, "endurance": 0.35, "speed": 0.20, "noise": 0.30},
    "passing": {"quality": 0.40, "technique": 0.40, "noise": 0.30},
    "shot_power": {"quality": 0.36, "technique": 0.30, "noise": 0.40},
}

# Playing styles (season.py STYLES), keyed by (group, style) because "inverted" means two things.
STYLE_EFFECTS: dict[tuple[str, str], dict[str, float]] = {
    ("GK", "shot-stopper"): {},
    ("GK", "sweeper-keeper"): {"pace": 0.35, "acceleration": 0.2, "passing": 0.4},
    ("CB", "aerial"): {"pace": -0.2, "acceleration": -0.15},
    ("CB", "ball-playing"): {"passing": 0.5},
    ("CB", "stopper"): {"acceleration": 0.15, "passing": -0.2},
    ("FB", "overlapping"): {"pace": 0.2, "stamina": 0.35, "work_rate": 0.4},
    ("FB", "defensive"): {"work_rate": -0.2},
    ("FB", "inverted"): {"passing": 0.35},
    ("DM", "ball-winner"): {"acceleration": 0.15, "stamina": 0.3, "work_rate": 0.3},
    ("DM", "deep playmaker"): {"passing": 0.5, "work_rate": -0.15},
    ("CM", "box-to-box"): {"pace": 0.15, "stamina": 0.55, "work_rate": 0.4},
    ("CM", "playmaker"): {"passing": 0.5},
    ("CM", "engine"): {"stamina": 0.6, "work_rate": 0.35},
    ("AM", "creator"): {"passing": 0.5},
    ("AM", "second striker"): {"pace": 0.15, "acceleration": 0.2, "shot_power": 0.35},
    ("W", "pacy"): {"pace": 0.6, "acceleration": 0.5},
    ("W", "inverted"): {"passing": 0.15, "shot_power": 0.35},
    ("W", "crosser"): {"stamina": 0.15, "passing": 0.2},
    ("ST", "poacher"): {"acceleration": 0.35, "shot_power": 0.15},
    ("ST", "target man"): {"pace": -0.4, "acceleration": -0.3, "passing": 0.15, "shot_power": 0.2},
    ("ST", "pressing forward"): {"pace": 0.2, "stamina": 0.4, "work_rate": 0.55},
}
SECOND_STYLE_WEIGHT = 0.5

# Main style -> (attribute, floor): what the style text promises (content/profiles.json "styles").
STYLE_FLOORS: dict[tuple[str, str], tuple[str, int]] = {
    ("GK", "sweeper-keeper"): ("passing", 60),
    ("CB", "ball-playing"): ("passing", 65),
    ("DM", "deep playmaker"): ("passing", 65),
    ("CM", "box-to-box"): ("stamina", 65),
    ("CM", "playmaker"): ("passing", 65),
    ("CM", "engine"): ("stamina", 70),
    ("AM", "creator"): ("passing", 65),
    ("W", "pacy"): ("pace", 70),
    ("ST", "pressing forward"): ("work_rate", 70),
}

# Strengths (content/profiles.json "strengths") -> [(attribute, latent shift, floor or None)].
STRENGTH_EFFECTS: dict[str, list[tuple[str, float, int | None]]] = {
    "pace": [("pace", 0.5, 70), ("acceleration", 0.15, None)],
    "recovery pace": [("pace", 0.4, 65)],
    "pace in behind": [("pace", 0.45, 65), ("acceleration", 0.2, None)],
    "acceleration": [("acceleration", 0.5, 70), ("pace", 0.15, None)],
    "dribbling": [("acceleration", 0.2, None)],
    "ball carrying": [("acceleration", 0.1, None)],
    "stamina": [("stamina", 0.5, 70), ("work_rate", 0.15, None)],
    "work rate": [("work_rate", 0.45, 70), ("stamina", 0.2, None)],
    "pressing": [("work_rate", 0.3, 60)],
    "overlapping runs": [("work_rate", 0.25, 60)],
    "tracking back": [("work_rate", 0.25, 60)],
    "late runs into the box": [("work_rate", 0.15, None)],
    "passing range": [("passing", 0.35, 65)],
    "long passing": [("passing", 0.35, 65)],
    "passing out from the back": [("passing", 0.35, 65)],
    "passing in tight spaces": [("passing", 0.35, 65)],
    "distribution with both feet": [("passing", 0.35, 65)],
    "switching play": [("passing", 0.25, 60)],
    "through balls": [("passing", 0.25, 60)],
    "vision": [("passing", 0.25, 60)],
    "hold-up play": [("passing", 0.2, None)],
    "first touch": [("passing", 0.15, None)],
    "crossing": [("passing", 0.1, None)],
    "set-piece delivery": [("passing", 0.1, None)],
    "shooting from distance": [("shot_power", 0.5, 70)],
    "long kicking": [("shot_power", 0.5, 65)],
    "free kicks": [("shot_power", 0.25, 60)],
    "finishing": [("shot_power", 0.15, None)],
    "penalties": [("shot_power", 0.1, None)],
}
FLOOR_JITTER = 6  # a player lifted to a floor lands on floor + 0..6, so floors do not pile up on one value

# Age curve: attribute -> (latent shift per year, from age, maximum number of years).
AGE_EFFECTS: dict[str, tuple[float, int, int]] = {
    "pace": (-0.07, 28, 20),
    "acceleration": (-0.07, 28, 20),
    "stamina": (-0.05, 30, 20),
    "work_rate": (-0.05, 30, 20),
    "passing": (0.03, 23, 8),
}


def _phi(z: float) -> float:
    """Standard normal distribution function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _age_shift(attribute: str, age: int) -> float:
    if attribute not in AGE_EFFECTS:
        return 0.0
    per_year, start, cap = AGE_EFFECTS[attribute]
    return per_year * min(max(0, age - start), cap)


def floors_for(group: str, style: list[str], strengths: list[str]) -> dict[str, tuple[int, str]]:
    """attribute -> (minimum value, reason) promised by the main style and by the strengths."""
    links: list[tuple[str, int, str]] = []
    if style and (group, style[0]) in STYLE_FLOORS:
        a, f = STYLE_FLOORS[(group, style[0])]
        links.append((a, f, f'style "{style[0]}"'))
    for s in strengths:
        links += [(a, f, f'strength "{s}"') for a, _, f in STRENGTH_EFFECTS.get(s, []) if f is not None]
    out: dict[str, tuple[int, str]] = {}
    for a, f, why in links:
        if f > out.get(a, (0, ""))[0]:
            out[a] = (f, why)
    return out


def make_attributes(seed: int, player_id: str, group: str, quality: float, style: list[str], strengths: list[str],
                    age: int) -> dict[str, int]:
    """The six 1-99 attributes of one player. Same inputs, same numbers."""
    rng = random.Random(f"{seed}:attributes:{player_id}")
    # Every draw happens in a fixed order, whatever the profile, so one rule never shifts another's noise.
    factors = {"speed": rng.gauss(0.0, 1.0), "endurance": rng.gauss(0.0, 1.0), "technique": rng.gauss(0.0, 1.0)}
    noise = {a: rng.gauss(0.0, 1.0) for a in ATTRIBUTES}
    jitter = {a: rng.randint(0, FLOOR_JITTER) for a in ATTRIBUTES}

    z_quality = (quality - QUALITY_REF) / QUALITY_SD
    shift = dict.fromkeys(ATTRIBUTES, 0.0)
    for i, s in enumerate(style[:2]):
        for a, v in STYLE_EFFECTS.get((group, s), {}).items():
            shift[a] += v * (1.0 if i == 0 else SECOND_STYLE_WEIGHT)
    for s in strengths:
        for a, v, _ in STRENGTH_EFFECTS.get(s, []):
            shift[a] += v
    floors = floors_for(group, style, strengths)

    out: dict[str, int] = {}
    for a in ATTRIBUTES:
        w = WEIGHTS[a]
        latent = w["quality"] * z_quality + w["noise"] * noise[a]
        latent += sum(w.get(f, 0.0) * x for f, x in factors.items())
        latent += shift[a] + _age_shift(a, age)
        value = min(99, max(1, int(math.floor(100.0 * _phi(latent) + 0.5))))
        if a in floors and value < floors[a][0]:
            value = min(99, floors[a][0] + jitter[a])
        out[a] = value
    return out


# --- The mapping to physical values ------------------------------------------------------------


def load_scales(path: Path = SCALES_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_table(table) -> bool:
    """99 finite, non-decreasing numbers (p1..p99)."""
    if not isinstance(table, dict) or not isinstance(table.get("values"), list) or len(table["values"]) != 99:
        return False
    vals = table["values"]
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals) and \
        all(b >= a for a, b in zip(vals, vals[1:]))


def scale_table(scales: dict, attribute: str, group: str) -> tuple[list[float], str] | None:
    """(values p1..p99, source) for an attribute and a group. Source is the group itself, "mean of X and Y"
    (neighbour groups), or "overall". None when no rule gives a table (for example a goalkeeper table missing)."""
    block = (scales.get("attributes") or {}).get(attribute) or {}
    by_group = block.get("by_role_group") or {}
    if not by_group:  # measured overall only (shot_power): one scale for every group
        overall = block.get("overall")
        return ([float(v) for v in overall["values"]], "overall") if _is_table(overall) else None
    own = by_group.get(group)
    if _is_table(own):
        return [float(v) for v in own["values"]], group
    if group == "GK":
        return None
    near = [g for g in NEIGHBOURS.get(group, ()) if _is_table(by_group.get(g))]
    if near:
        tables = [by_group[g]["values"] for g in near]
        return [sum(float(t[i]) for t in tables) / len(tables) for i in range(99)], "mean of " + " and ".join(near)
    overall = block.get("overall")
    return ([float(v) for v in overall["values"]], "overall") if _is_table(overall) else None


def physical_value(scales: dict, attribute: str, group: str, k: float) -> float | None:
    """Physical value of attribute k (1-99, linear between integer percentiles) in the unit of the scale."""
    found = scale_table(scales, attribute, group)
    if found is None:
        return None
    values = found[0]
    k = min(99.0, max(1.0, float(k)))
    i = int(math.floor(k)) - 1
    if i >= 98:
        return values[98]
    return values[i] + (k - 1 - i) * (values[i + 1] - values[i])


def describe(scales: dict, attribute: str) -> tuple[str, str]:
    """(metric, unit) of an attribute, as written in the calibration file."""
    block = (scales.get("attributes") or {}).get(attribute) or {}
    return str(block.get("metric", "")), str(block.get("unit", ""))
