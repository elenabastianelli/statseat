"""Highlights anchored on the physical outcome, walked back to the start of the action (guidelines v4, ``R-HIGHLIGHT``, section 9.11).

Outcomes detected in the tracking: a goal (``R-GOAL``), a woodwork hit (``R-WOODWORK``) and a save (``R-SHOT-OUTCOME`` = saved).
From the anchor frame the clip starts at the later of (a) the last change of possession to the attacking team (provider's
possession column) and (b) the last dead->live restart before the anchor. It ends a few seconds after the anchor.
Timestamps are never trusted for this.
"""

from __future__ import annotations

import numpy as np

from .config import PrepConfig

ATTACK_POSSESSION = {"home": 1, "away": 2}


def action_start(bstatus: np.ndarray, bposs: np.ndarray | None, anchor: int, attack_side: str) -> int:
    """Index where the action leading to ``anchor`` starts (see module docstring)."""
    st = np.asarray(bstatus)[: anchor + 1]
    tr = np.flatnonzero((st[:-1] != 1) & (st[1:] == 1)) + 1
    start = int(tr[-1]) if len(tr) else 0
    if bposs is not None:
        code = ATTACK_POSSESSION[attack_side]
        poss = np.asarray(bposs)[: anchor + 1]
        changes = np.flatnonzero((poss[:-1] != code) & (poss[1:] == code)) + 1
        if len(changes):
            start = max(start, int(changes[-1]))
    return start


def highlights(anchors: list[dict], bstatus: np.ndarray, bposs: np.ndarray | None, fs: float, cfg: PrepConfig) -> list[dict]:
    """``anchors``: dicts with ``kind`` (goal | woodwork | save), ``index`` (anchor frame), ``attack_side`` and ``period``.

    Returns one clip per anchor: start and end indices (end = anchor + ``highlight_after_s``), clipped to the period."""
    out = []
    after = cfg.frames(cfg.highlight_after_s, fs)
    n = len(bstatus)
    for a in sorted(anchors, key=lambda r: (r["period"], r["index"], r["kind"])):
        i = int(a["index"])
        start = action_start(bstatus, bposs, i, a["attack_side"])
        out.append({"period": a["period"], "kind": a["kind"], "attack_side": a["attack_side"], "anchor_index": i,
                    "start_index": start, "end_index": min(n - 1, i + after), "start_to_anchor_s": round((i - start) / fs, 2)})
    return out
