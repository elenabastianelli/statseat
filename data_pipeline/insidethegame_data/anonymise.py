"""Anonymous labels for the reports of real (provider) data (guidelines v4.2, section 13, ``R-ANON-LABELS``).

Every file written to ``reports/qc/<match>/`` and ``reports/qc/SUMMARY.md`` for a real match refers to people by an
anonymous label instead of the provider's object ID (for DFL, ``DFL-OBJ-...``):

* a player is ``<team side> <role group> <n>``, for example ``home CB 1``: the role group comes from the reviewed
  position mapping (section 16.1); ``n`` counts within the team and the role group, in the order of the object IDs, so
  the labels are deterministic. Players without a position code (unused substitutes) get the group ``unassigned``;
* a tracked object that belongs to no team (for example a referee) is ``<team label> <n>``, for example ``referee 1``.

The table that links labels and IDs is written **only** to the git-ignored frame-level folder
(``reports/qc/frames/<match>/id_labels.json``), so that a reviewer can find an episode again on the local machine.
Frame-level outputs keep the IDs (they never leave the machine). Synthetic matches have fictional IDs and are not
anonymised.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ID_LABELS_FILE = "id_labels.json"
UNASSIGNED_GROUP = "unassigned"


def build_labels(lineup: pd.DataFrame, other_objects: dict[str, str] | None = None) -> dict[str, dict]:
    """object ID -> ``{"label", "team_side", "role_group", "role"}``.

    ``lineup``: columns ``person_id``, ``team_role`` (home / away), ``role_group`` and ``role`` (English, section 16).
    ``other_objects``: tracked objects outside the line-up -> their team label (for example ``referee``)."""
    out: dict[str, dict] = {}
    rows = []
    for r in lineup.itertuples(index=False):
        group = str(r.role_group) if isinstance(r.role_group, str) and not r.role_group.startswith("UNMAPPED_") else UNASSIGNED_GROUP
        rows.append((str(r.team_role), group, str(r.person_id), str(r.role) if isinstance(r.role, str) else None))
    counters: dict[tuple[str, str], int] = {}
    for side, group, pid, role in sorted(rows, key=lambda t: (t[0], t[1], t[2])):
        if pid in out:
            continue
        counters[(side, group)] = counters.get((side, group), 0) + 1
        out[pid] = {"label": f"{side} {group} {counters[(side, group)]}", "team_side": side, "role_group": group, "role": role}
    others: dict[str, int] = {}
    for pid, team in sorted((other_objects or {}).items(), key=lambda kv: (str(kv[1]), str(kv[0]))):
        if pid in out:
            continue
        t = str(team).lower() if team else "object"
        others[t] = others.get(t, 0) + 1
        out[pid] = {"label": f"{t} {others[t]}", "team_side": "none", "role_group": None, "role": None}
    return out


def id_map(labels: dict[str, dict]) -> dict[str, str]:
    """object ID -> label."""
    return {pid: v["label"] for pid, v in labels.items()}


def _pattern(mapping: dict[str, str]) -> re.Pattern | None:
    if not mapping:
        return None
    return re.compile("|".join(re.escape(k) for k in sorted(mapping, key=lambda k: (-len(k), k))))


def anonymise_text(text: str, mapping: dict[str, str]) -> str:
    """Replace every object ID inside ``text`` by its label (longest IDs first, so an ID that is the prefix of another
    is never cut)."""
    pat = _pattern(mapping)
    return text if pat is None else pat.sub(lambda m: mapping[m.group(0)], text)


def anonymise(obj, mapping: dict[str, str]):
    """A copy of ``obj`` (dicts, lists, tuples, strings, DataFrames) with every object ID, in keys, values and inside
    strings, replaced by its label. Other values are returned unchanged."""
    pat = _pattern(mapping)
    if pat is None:
        return obj

    def sub(o):
        if isinstance(o, str):
            return pat.sub(lambda m: mapping[m.group(0)], o)
        if isinstance(o, dict):
            return {sub(k) if isinstance(k, str) else k: sub(v) for k, v in o.items()}
        if isinstance(o, list):
            return [sub(v) for v in o]
        if isinstance(o, tuple):
            return tuple(sub(v) for v in o)
        if isinstance(o, pd.DataFrame):
            df = o.copy()
            for c in df.columns:
                if df[c].dtype == object:
                    df[c] = df[c].map(lambda v: sub(v) if isinstance(v, (str, dict, list, tuple)) else v)
            return df
        return o

    return sub(obj)


def write_id_labels(path: Path, match_id: str, labels: dict[str, dict]) -> None:
    """Write the label <-> ID table (git-ignored frame-level folder only)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"label": v["label"], "object_id": pid, "team_side": v["team_side"], "role_group": v["role_group"], "role": v["role"]}
            for pid, v in sorted(labels.items(), key=lambda kv: kv[1]["label"])]
    doc = {"match_id": match_id, "rule": "R-ANON-LABELS (guidelines v4.2, section 13)",
           "note": "Local only (git-ignored): links the anonymous labels of the reports to the provider object IDs. Never commit or share.",
           "labels": rows}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load_label_map(frames_root: Path, match_id: str) -> dict[str, str]:
    """label -> object ID from ``<frames_root>/<match_id>/id_labels.json`` (empty when the match was not anonymised)."""
    p = Path(frames_root) / match_id / ID_LABELS_FILE
    if not p.exists():
        return {}
    return {r["label"]: r["object_id"] for r in json.loads(p.read_text())["labels"]}
