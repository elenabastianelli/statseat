"""Tiny synthetic DFL-format match for tests (no real data).

Deliberately different from the real DFL matches so nothing can be hard-coded:
pitch 100 x 60 m, tracking with the origin at a corner (x in [0, 100], y in
[0, 60]), first frames N=1000 (half 1) and N=5000 (half 2).

Injected artefacts:
* an identity swap in half 1: players H2 and A5 run past each other 0.9 m apart
  and their labels are swapped from t = 3 s onwards;
* a 2 m jump of player H3 in half 2 at frame index 300;
* one ``Delete`` event and one event written out of time order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

FS = 25.0
L, W = 100.0, 60.0
N0 = {1: 1000, 2: 5000}
DURATION_S = 30.0
T0 = {1: datetime(2023, 1, 1, 15, 0, 0, tzinfo=timezone.utc), 2: datetime(2023, 1, 1, 16, 0, 0, tzinfo=timezone.utc)}
HOME, AWAY = "T-HOME", "T-AWAY"
PLAYERS = [  # person_id, team, position, starting
    ("P-H1", HOME, "TW", True), ("P-H2", HOME, "ZO", True), ("P-H3", HOME, "IVZ", True),
    ("P-A4", AWAY, "TW", True), ("P-A5", AWAY, "STZ", True), ("P-A6", AWAY, "IVZ", True),
    ("P-H9", HOME, "TW", False),
]
SWAP_T = 3.0
JUMP_IDX = 300
DEAD_FRAMES = 25  # ball dead for the first second of each half


@dataclass
class SynthMatch:
    raw_dir: Path
    match_id: str


def _iso(dt: datetime, tz_hours: int = 0) -> str:
    local = dt.astimezone(timezone(timedelta(hours=tz_hours)))
    s = local.isoformat(timespec="milliseconds")
    return s


def trajectories(half: int) -> tuple[np.ndarray, dict[str, tuple[np.ndarray, np.ndarray]], tuple[np.ndarray, np.ndarray, np.ndarray]]:
    n = int(DURATION_S * FS)
    t = np.arange(n) / FS
    w = 1.0 / 3.0
    pos = {
        "P-H1": (5 + 0.5 * np.sin(t), np.full(n, 30.0)),
        "P-A4": (95 - 0.5 * np.sin(t), np.full(n, 30.0)),
        "P-H2": (55 + 15 * np.sin(w * (t - SWAP_T)), np.full(n, 20.0)),
        "P-A5": (55 - 15 * np.sin(w * (t - SWAP_T)), np.full(n, 20.9)),
        "P-H3": (30 + 3 * np.sin(0.3 * t), 45 + 3 * np.cos(0.3 * t)),
        "P-A6": (75 + 3 * np.sin(0.25 * t), np.full(n, 45.0)),
    }
    if half == 1:
        k = int(SWAP_T * FS)
        h2x, h2y = pos["P-H2"][0].copy(), pos["P-H2"][1].copy()
        a5x, a5y = pos["P-A5"][0].copy(), pos["P-A5"][1].copy()
        h2x[k:], a5x[k:] = pos["P-A5"][0][k:], pos["P-H2"][0][k:]
        h2y[k:], a5y[k:] = pos["P-A5"][1][k:], pos["P-H2"][1][k:]
        pos["P-H2"], pos["P-A5"] = (h2x, h2y), (a5x, a5y)
    else:
        # Sides switch: mirror x; then inject a 2 m jump for H3.
        pos = {p: (L - x, y) for p, (x, y) in pos.items()}
        x3 = pos["P-H3"][0].copy()
        x3[JUMP_IDX:] += 2.0
        pos["P-H3"] = (x3, pos["P-H3"][1])
    bx = 50 + 20 * np.sin(0.2 * np.clip(t - 1.0, 0, None))
    by = 30 + 10 * np.sin(0.13 * np.clip(t - 1.0, 0, None))
    status = np.ones(n)
    status[:DEAD_FRAMES] = 0
    return t, pos, (bx, by, status)


def _matchinfo() -> str:
    players = "\n".join(
        f'<Team TeamId="{team}" Role="{"home" if team == HOME else "guest"}"><Players>'
        + "".join(
            f'<Player PersonId="{pid}" ShirtNumber="{i + 1}" Starting="{"true" if st else "false"}" PlayingPosition="{pp}" />'
            for i, (pid, tm, pp, st) in enumerate(PLAYERS) if tm == team
        )
        + "</Players></Team>"
        for team in (HOME, AWAY)
    )
    total = int(DURATION_S * 1000)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PutDataRequest><MatchInformation>
<General MatchId="DFL-MAT-SYN001" CompetitionId="DFL-COM-TEST" Season="2099/2100" HomeTeamId="{HOME}" GuestTeamId="{AWAY}" KickoffTime="{_iso(T0[1])}" />
<Environment PitchX="{L:.2f}" PitchY="{W:.2f}" />
<Teams>{players}</Teams>
<OtherGameInformation TotalTimeFirstHalf="{total}" TotalTimeSecondHalf="{total}" PlayingTimeFirstHalf="{total - 1000}" PlayingTimeSecondHalf="{total - 1000}" />
</MatchInformation></PutDataRequest>
"""


def _positions() -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<PutDataRequest><Positions>",
           f'<MetaData MatchId="DFL-MAT-SYN001" Type="pitch-size"><PitchSize X="{L:.2f}" Y="{W:.2f}" /></MetaData>']
    for half, section in ((1, "firstHalf"), (2, "secondHalf")):
        t, pos, (bx, by, st) = trajectories(half)
        times = [_iso(T0[half] + timedelta(seconds=float(s))) for s in t]
        for pid, team, _, starting in PLAYERS:
            if not starting:
                continue
            x, y = pos[pid]
            out.append(f'<FrameSet GameSection="{section}" MatchId="DFL-MAT-SYN001" TeamId="{team}" PersonId="{pid}">')
            d = np.r_[0, np.hypot(np.diff(x), np.diff(y))]
            for i in range(len(t)):
                out.append(f'<Frame N="{N0[half] + i}" T="{times[i]}" X="{x[i]:.2f}" Y="{y[i]:.2f}" D="{100 * d[i]:.2f}" S="{3.6 * d[i] * FS:.2f}" A="0.00" M="1"/>')
            out.append("</FrameSet>")
        out.append(f'<FrameSet GameSection="{section}" MatchId="DFL-MAT-SYN001" TeamId="BALL" PersonId="DFL-OBJ-BALL">')
        for i in range(len(t)):
            out.append(f'<Frame N="{N0[half] + i}" T="{times[i]}" X="{bx[i]:.2f}" Y="{by[i]:.2f}" Z="0.10" D="0" S="0" A="0" M="1" BallPossession="1" BallStatus="{int(st[i])}"/>')
        out.append("</FrameSet>")
    out.append("</Positions></PutDataRequest>")
    return "\n".join(out)


def _events() -> str:
    ev, eid = [], 1
    for half, section in ((1, "firstHalf"), (2, "secondHalf")):
        t, _, (bx, by, _) = trajectories(half)
        left, right = (HOME, AWAY) if half == 1 else (AWAY, HOME)
        t_ko = T0[half] + timedelta(seconds=DEAD_FRAMES / FS)
        ev.append((t_ko, f'<Event EventId="{eid}" EventTime="{_iso(t_ko, 2)}" X-Position="50.00" Y-Position="30.00"><KickOff TeamLeft="{left}" TeamRight="{right}" GameSection="{section}"><Play Team="{left}" Player="P-H2"/></KickOff></Event>'))
        eid += 1
        for k in range(DEAD_FRAMES + 25, len(t) - 25, 50):
            te = T0[half] + timedelta(seconds=float(t[k]))
            ev.append((te, f'<Event EventId="{eid}" EventTime="{_iso(te, 2)}" X-Position="{bx[k]:.2f}" Y-Position="{by[k]:.2f}"><Play Team="{HOME}" Player="P-H3"><Pass/></Play></Event>'))
            eid += 1
        te = T0[half] + timedelta(seconds=DURATION_S - 0.1)
        ev.append((te, f'<Event EventId="{eid}" EventTime="{_iso(te, 2)}"><FinalWhistle GameSection="{section}"/></Event>'))
        eid += 1
    ev.sort(key=lambda e: e[0])
    body = [e[1] for e in ev]
    # One Delete placeholder and one out-of-order event.
    body.insert(3, f'<Event EventId="{eid}" EventTime="{_iso(T0[1] + timedelta(seconds=5), 2)}" X-Position="10.00" Y-Position="10.00"><Delete/></Event>')
    body.insert(6, body.pop(1))
    return '<?xml version="1.0" encoding="UTF-8"?>\n<PutDataRequest>\n' + "\n".join(body) + "\n</PutDataRequest>\n"


def write_synthetic_match(root: Path) -> SynthMatch:
    raw = Path(root) / "data" / "raw" / "dfl"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "DFL_02_01_matchinformation_DFL-COM-TEST_DFL-MAT-SYN001.xml").write_text(_matchinfo())
    (raw / "DFL_03_02_events_raw_DFL-COM-TEST_DFL-MAT-SYN001.xml").write_text(_events())
    (raw / "DFL_04_03_positions_raw_observed_DFL-COM-TEST_DFL-MAT-SYN001.xml").write_text(_positions())
    return SynthMatch(raw_dir=raw, match_id="SYN001")
