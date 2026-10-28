"""English mapping of provider codes (guidelines v4.2, section 16, ``R-MAP-EN``).

Codes are mapped only through the reviewed file ``reference/dfl_to_english.yaml``.
Unknown or missing codes keep the original in ``source_code`` and get
``UNMAPPED_<code>`` (or ``UNMAPPED_NONE``) so that the mapping file can be updated.
Event types (v4.2) are mapped too, including the DFL types outside the 23-type list.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

MAPPING_FILE = Path(__file__).resolve().parents[1] / "reference" / "dfl_to_english.yaml"
_NUMERIC_FORMATION = re.compile(r"^\d(-\d){2,4}$")


@lru_cache(maxsize=4)
def load_mapping(path: str | None = None) -> dict:
    with open(path or MAPPING_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def map_position(code: str | None, mapping: dict | None = None) -> dict:
    m = (mapping or load_mapping())["playing_positions"]
    if code is None or code == "" or (isinstance(code, float) and pd.isna(code)):
        return {"source_code": None, "role": "UNMAPPED_NONE", "role_label": "UNMAPPED_NONE", "role_group": "UNMAPPED_NONE"}
    hit = m.get(code)
    if hit is None:
        # Synthetic data is English natively (guidelines section 12): English role codes pass through.
        english = {v["role"]: v for v in m.values()}
        if code in english:
            v = english[code]
            return {"source_code": code, "role": code, "role_label": v["label"], "role_group": v["group"]}
        return {"source_code": code, "role": f"UNMAPPED_{code}", "role_label": f"UNMAPPED_{code}", "role_group": f"UNMAPPED_{code}"}
    return {"source_code": code, "role": hit["role"], "role_label": hit["label"], "role_group": hit["group"]}


def map_formation(value: str | None, mapping: dict | None = None) -> dict:
    m = (mapping or load_mapping())["formations"]
    if value is None or value == "":
        return {"source_formation": None, "formation": "UNMAPPED_NONE"}
    if value in m:
        return {"source_formation": value, "formation": m[value]}
    if _NUMERIC_FORMATION.match(value):
        return {"source_formation": value, "formation": value}  # plain numeric formations are kept as they are
    return {"source_formation": value, "formation": f"UNMAPPED_{value}"}


def map_value(value: str | None, mapping: dict | None = None) -> str:
    m = (mapping or load_mapping())["values"]
    if value is None:
        return "UNMAPPED_NONE"
    return m.get(value, value)


def map_shot_outcome(sub_types: str | None, mapping: dict | None = None) -> dict:
    """Section 16.3: DFL ``ShotAtGoal`` sub-type (``sub_types``, '/'-joined) -> English outcome and on-target flag.

    English outcomes (synthetic data) pass through. Unknown or missing values give ``UNMAPPED_<code>``
    (``UNMAPPED_NONE``) so that the mapping file can be updated."""
    m = (mapping or load_mapping())["shot_outcomes"]
    english = {v["outcome"]: v for v in m.values()}
    if sub_types is None or (isinstance(sub_types, float) and pd.isna(sub_types)) or str(sub_types) == "":
        return {"source_code": None, "outcome": "UNMAPPED_NONE", "on_target": False}
    for code in str(sub_types).split("/"):
        if code in m:
            return {"source_code": code, "outcome": m[code]["outcome"], "on_target": bool(m[code]["on_target"])}
        if code in english:
            return {"source_code": code, "outcome": code, "on_target": bool(english[code]["on_target"])}
    return {"source_code": str(sub_types), "outcome": f"UNMAPPED_{sub_types}", "on_target": False}


def map_event_type(code: str | None, mapping: dict | None = None) -> dict:
    """Section 16.4 (v4.2): provider event type -> English snake_case name and whether it is in the DFL 23-type list.

    Unknown or missing types give ``UNMAPPED_<code>`` (``UNMAPPED_NONE``) so that the mapping file can be updated."""
    m = (mapping or load_mapping()).get("event_types", {})
    if code is None or (isinstance(code, float) and pd.isna(code)) or str(code) == "":
        return {"source_code": None, "name": "UNMAPPED_NONE", "dfl_list": False}
    hit = m.get(str(code))
    if hit is None:
        return {"source_code": str(code), "name": f"UNMAPPED_{code}", "dfl_list": False}
    return {"source_code": str(code), "name": hit["name"], "dfl_list": bool(hit["dfl_list"])}


def event_type_counts(event_types, mapping: dict | None = None) -> dict:
    """``R-MAP-EN`` log for event types: counts per English name, the types outside the DFL 23-type list, and the
    unmapped ones (aggregates only)."""
    counts: dict[str, int] = {}
    outside: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    for code in event_types:
        r = map_event_type(code, mapping)
        counts[r["name"]] = counts.get(r["name"], 0) + 1
        if r["name"].startswith("UNMAPPED_"):
            unmapped[str(code)] = unmapped.get(str(code), 0) + 1
        elif not r["dfl_list"]:
            outside[r["name"]] = outside.get(r["name"], 0) + 1
    return {"by_type": dict(sorted(counts.items())), "outside_dfl_list": dict(sorted(outside.items())), "unmapped": dict(sorted(unmapped.items()))}


def english_lineup(lineup: pd.DataFrame, mapping: dict | None = None) -> pd.DataFrame:
    """Add English role columns to a DFL line-up table; return it with an ``unmapped`` flag."""
    rows = [map_position(c, mapping) for c in lineup["playing_position"].tolist()]
    out = lineup.copy()
    for k in ("role", "role_label", "role_group"):
        out[k] = [r[k] for r in rows]
    out["unmapped"] = out["role"].str.startswith("UNMAPPED_")
    return out


def snake_case(name: str) -> str:
    """DFL event types are English already; our outputs use snake_case (``ShotAtGoal`` -> ``shot_at_goal``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() if name else name
