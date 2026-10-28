"""Tests of the match engine (S-7): no-look-ahead window, truncation, phases, demo clocks, contract payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.contract import make_fake
from app.contract.models import EventFeed, LiveStats, MatchState, MatchStats, Mode, Phase, Recap
from app.contract.phases import PHASE_ORDER
from app.engine import stats
from app.engine.clock import ReplayClock
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app
from app.engine.sources import SourceNotAvailable, load_match
from app.engine.sources.fake import FAKE_EVENTS, load_fake
from app.engine.sources.synthetic import load_synthetic
from app.engine.window import MatchWindow

from . import engine_fixture as fx

CODES = make_fake.FAKE_TICKET_CODES
SILVER, GOLD, DEMO = (CODES[t] for t in ("silver", "gold", "demo"))
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory) -> Path:
    return fx.build(tmp_path_factory.mktemp("syn"))


@pytest.fixture(scope="module")
def syn(fixture_dir):
    return load_synthetic("SYN-001", fixture_dir)


def payloads(engine: Engine, cursor: float) -> dict:
    """Every payload the engine builds at one instant (without wall-clock fields)."""
    snap = engine.window.at(cursor)
    core = engine.core(snap)
    out = {"state": stats.match_state(snap, NOW), "events": stats.event_feed(snap)}
    if snap.phase in (Phase.first_half, Phase.second_half):
        out["live"] = stats.live_stats(core)
    for m in Mode:  # Data & Statistics is open from kick-off (contract v0.2.5)
        if snap.phase is not Phase.pre_match:
            out[f"stats_{m.value}"] = stats.match_stats(core, m, True, NOW)
    if snap.phase is Phase.full_time:
        out["recap"] = stats.recap(core, Mode.analyst, ["BRA-09", "BRA-27"], {}, {}, True)
    return json.loads(json.dumps(out, default=str))


def cursors(engine: Engine) -> list[float]:
    s = engine.starts
    return [s[Phase.first_half] + 61.0, s[Phase.first_half] + 200.0, s[Phase.half_time] + 20.0,
            s[Phase.second_half] + 125.0, s[Phase.second_half] + 300.0, s[Phase.full_time] + 1.0]


# ------------------------------------------------------------------ truncation (the "no future" test)


def test_truncation_fake(tmp_path: Path) -> None:
    """Statistics at minute t are identical with the whole fake match and with the file cut at minute t."""
    full = Engine(load_fake())
    raw = json.loads(FAKE_EVENTS.read_text())
    for cursor in [600.0, 1500.0, 2790.0, 3665.0 + 1200.0, 3665.0 + 2600.0, 7000.0]:
        snap = full.window.at(cursor)
        cut_events = [e for e in raw["events"] if (e["match_time_s"] if e["period"] == 1 else
                                                   e["match_time_s"] + 2760.0 + 900.0) <= cursor]
        path = tmp_path / f"cut_{int(cursor)}.json"
        path.write_text(json.dumps({**raw, "events": cut_events}))
        cut = Engine(load_fake(path))
        assert payloads(cut, cursor) == payloads(full, cursor), f"look-ahead at cursor {cursor} ({snap.phase})"


def test_truncation_synthetic_with_frames(fixture_dir: Path, syn, tmp_path: Path) -> None:
    """Same with tracking frames: the data pipeline only ever sees the frames released by the window."""
    full = Engine(syn)
    for cursor in cursors(full):
        until = fx.KO1 + pd.Timedelta(seconds=cursor)
        cut = Engine(load_synthetic("SYN-001", fx.cut(fixture_dir, tmp_path / f"cut_{int(cursor)}", until)))
        a, b = payloads(full, cursor), payloads(cut, cursor)
        assert a == b, f"look-ahead at cursor {cursor}"
        if "live" in a:  # the frame-based numbers are in Data & Statistics since v0.2.5
            assert a["stats_analyst"]["scope"] == "live"
            assert a["stats_analyst"]["distance_covered"] is not None and a["stats_analyst"]["top_speed"]


# ------------------------------------------------------------------ the window


def test_window_never_returns_future_data(syn) -> None:
    w = MatchWindow(syn, 5.0)
    for cursor in range(-50, 1100, 7):
        snap = w.at(cursor)
        w.check(snap)
        assert all(e.t + 5.0 <= cursor for e in snap.events)
        assert all(len(f) == 0 or f.t[-1] <= cursor for f in snap.frames.values())
        assert snap.as_of_s >= 0


def test_event_is_never_processed_before_its_frames(syn) -> None:
    w = MatchWindow(syn, 5.0)
    shot = next(e for e in syn.events if e.type == "ShotAtGoal")
    before, after = w.at(shot.t + 4.99), w.at(shot.t + 5.0)
    assert shot not in before.events and shot in after.events
    assert after.frames[1].t[-1] >= shot.t + 5.0 - 1 / fx.FS  # the 5 s of frames after the shot are there
    assert stats.event_metrics(before)["teams"]["home"]["goals"] == 0
    assert stats.event_metrics(after)["teams"]["home"]["goals"] == 1


def test_phase_order_driven_by_kickoff_and_final_whistle(syn) -> None:
    w = MatchWindow(syn, 5.0)
    seen = [w.at(c).phase for c in range(-100, 1100, 3)]
    idx = [PHASE_ORDER.index(p) for p in seen]
    assert idx == sorted(idx) and set(seen) == set(PHASE_ORDER)
    fw1 = next(e for e in syn.events if e.type == "FinalWhistle" and e.period == 1)
    assert w.at(fw1.t + 4.9).phase is Phase.first_half and w.at(fw1.t + 5.0).phase is Phase.half_time
    goal_kickoff = [e for e in syn.events if e.type == "KickOff" and e.period == 1][1]
    assert w.at(goal_kickoff.t + 5.0).phase is Phase.first_half  # a kick-off after a goal changes nothing


def test_as_of_and_scoreboard(syn) -> None:
    w = MatchWindow(syn, 5.0)
    assert w.at(-10).as_of_s == 0 and stats.scoreboard(w.at(-10)) == (0, 0)
    s = w.at(125.0)
    assert s.as_of_s == pytest.approx(125.0) and stats.scoreboard(s) == (3, 0)
    ht = w.at(fx.PERIOD_S + 60)
    assert ht.as_of_s == pytest.approx(fx.PERIOD_S) and stats.scoreboard(ht) == (45, 0)
    s2 = w.at(fx.PERIOD_S + fx.BREAK_S + 61)
    assert s2.as_of_s == pytest.approx(fx.PERIOD_S + 61) and stats.scoreboard(s2) == (47, 0)
    assert w.at(5000).as_of_s == pytest.approx(2 * fx.PERIOD_S)
    assert 2 not in w.at(fx.PERIOD_S + fx.BREAK_S + 4).frames  # second-half frames wait for its kick-off


# ------------------------------------------------------------------ statistics


def test_frame_stats_values(syn) -> None:
    e = Engine(syn)
    core = e.core(e.window.at(e.starts[Phase.half_time] + 1))
    fr = core.fr
    assert fr is not None
    assert all(v["top"] is None or 0 < v["top"]["kmh"] <= 38 for v in fr["players"].values())
    assert fr["teams"]["home"]["sprints"] >= 1 and fr["teams"]["away"]["sprints"] == 0  # only the winger sprints
    # possession by 30 s blocks starting with home: 6 of 12 blocks each, minus the dead ball (100-110 s, home block)
    assert 45 < fr["possession"]["home_pct"] < 60
    assert fr["shot_speeds"] and all(85 <= v["kmh"] <= 211 for v in fr["shot_speeds"].values())
    assert fr["effective_min"] == pytest.approx((fx.PERIOD_S - 10 - 29.4) / 60, abs=0.1)
    ms = MatchStats.model_validate(stats.match_stats(core, Mode.analyst, False, NOW))
    assert ms.distance_covered and ms.speed_zones and ms.peak_intensity
    zones = ms.speed_zones[0]
    assert sum((zones.km_0_7, zones.km_7_15, zones.km_15_20, zones.km_20_25, zones.km_over_25)) <= \
        ms.distance_covered.teams[0].distance.km + 0.02
    assert {u.metric for u in ms.unavailable} >= {"ppda"}
    assert ms.ball_circulation.unit == "km/h" and ms.ball_circulation.teams[0].reliability is not None
    assert ms.distance_covered.teams[0].distance.reliability.level.value in ("high", "medium", "low")
    assert ms.players and all(r.minutes_played >= 0 for r in ms.players) and len(ms.leaders) == 2


def test_fake_has_no_frame_metrics_and_says_why() -> None:
    e = Engine(load_fake())
    core = e.core(e.window.at(e.starts[Phase.half_time] + 1))
    ms = MatchStats.model_validate(stats.match_stats(core, Mode.casual, False, NOW))
    assert ms.distance_covered is None and ms.top_speed == [] and ms.possession is None
    reasons = {u.metric: u.reason for u in ms.unavailable}
    assert "events only" in reasons["distance_covered"] and "xg" not in reasons  # xg is Analyst-only: null in Casual
    assert "events only" in reasons["momentum"] and "events only" in reasons["counts.sprints"]
    assert (ms.score.home, ms.score.away) == (1, 0) and [s.shots for s in ms.shots] == [7, 4]
    feed = stats.event_feed(core.snap)
    assert all("xg" not in e["qualifiers"] and "speed_kmh" not in e["qualifiers"] for e in feed["events"])


def test_determinism_two_replays_identical(fixture_dir: Path) -> None:
    a, b = Engine(load_synthetic("SYN-001", fixture_dir)), Engine(load_synthetic("SYN-001", fixture_dir))
    for c in cursors(a):
        assert payloads(a, c) == payloads(b, c)
    f1, f2 = Engine(load_fake()), Engine(load_fake())
    for c in (100.0, 2800.0, 5000.0, 7000.0):
        assert payloads(f1, c) == payloads(f2, c)


def test_payloads_validate_against_contract(syn) -> None:
    for engine in (Engine(syn), Engine(load_fake())):
        for c in cursors(engine) + [engine.starts[Phase.pre_match]]:
            p = payloads(engine, c)
            MatchState.model_validate(p["state"])
            EventFeed.model_validate(p["events"])
            if "live" in p:
                LiveStats.model_validate(p["live"])
            for k in ("stats_casual", "stats_analyst"):
                if k in p:
                    MatchStats.model_validate(p[k])
            if "recap" in p:
                Recap.model_validate(p["recap"])


# ------------------------------------------------------------------ clocks and the server


def test_replay_clock_speed_pause_and_hold() -> None:
    t = FakeTime()
    c = ReplayClock(10.0, -300.0, True, now=t)
    t.t += 6.0
    assert c.position() == pytest.approx(-240.0)
    c.pause()
    t.t += 100.0
    assert c.position() == pytest.approx(-240.0)
    c.seek(0.0, True, hold_at=50.0)
    t.t += 100.0
    assert c.position() == 50.0 and not c.running


@pytest.fixture()
def server():
    t = FakeTime()
    engine = Engine(load_fake(), EngineConfig(speed=10.0), now=t)
    return TestClient(create_app(engine)), engine, t


def login(client: TestClient, code: str, device: str = "device-0001") -> dict:
    r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": device})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_stadium_clock_advances_and_phases_change(server) -> None:
    client, engine, t = server
    h = login(client, GOLD[0])
    st = client.get("/api/match/state", headers=h).json()
    assert st["phase"] == "pre_match" and st["as_of_s"] == 0
    t.t += 31.0  # 310 match seconds at 10x: past the kick-off and its 5 s buffer
    st = client.get("/api/match/state", headers=h).json()
    assert st["phase"] == "first_half" and st["minute"] == 1 and st["clock_running"]
    t.t += 120.0
    st2 = client.get("/api/match/state", headers=h).json()
    assert st2["minute"] == 21 and st2["as_of_s"] > st["as_of_s"]
    assert client.get("/api/stats/live", headers=h).status_code == 200
    assert client.get("/api/stats", headers=h).json()["scope"] == "live"  # open from kick-off (v0.2.5)
    assert client.get("/api/engine/status").json()["phase"] == "first_half"


def test_demo_jump_only_for_demo_and_per_session(server) -> None:
    client, engine, t = server
    for code in (SILVER[0], GOLD[1]):
        h = login(client, code)
        assert client.post("/api/demo/phase", headers=h, json={"phase": "full_time"}).status_code == 403
        assert client.post("/api/demo/clock", headers=h, json={"action": "pause"}).status_code == 403
    d1, d2, g = login(client, DEMO[0]), login(client, DEMO[1]), login(client, GOLD[2])
    r = client.post("/api/demo/phase", headers=d1, json={"phase": "half_time"})
    assert r.status_code == 200 and r.json()["phase"] == "half_time"
    assert client.get("/api/match/state", headers=d2).json()["phase"] == "pre_match"
    assert client.get("/api/match/state", headers=g).json()["phase"] == "pre_match"
    assert client.get("/api/stats", headers=d1).status_code == 200
    assert client.get("/api/games", headers=d1).json()["games"][0]["available"]  # mock endpoints follow the jump
    assert not client.get("/api/games", headers=d2).json()["games"][0]["available"]
    t.t += 1000.0  # a demo clock holds at half-time
    assert client.get("/api/match/state", headers=d1).json()["phase"] == "half_time"
    client.post("/api/demo/phase", headers=d1, json={"phase": "second_half"})
    m0 = client.get("/api/match/state", headers=d1).json()["minute"]
    t.t += 60.0
    assert client.get("/api/match/state", headers=d1).json()["minute"] == m0 + 10  # runs at 10x during play
    client.post("/api/demo/clock", headers=d1, json={"action": "pause"})
    t.t += 60.0
    assert client.get("/api/match/state", headers=d1).json()["minute"] == m0 + 10


def test_server_tiers_inspector_and_modes(server) -> None:
    client, engine, t = server
    for code, visible in ((DEMO[2], True), (DEMO[1], True), (GOLD[0], False), (SILVER[1], False)):  # DEMO[1]: stadium clock
        h = login(client, code)
        if code == DEMO[2]:
            client.post("/api/demo/phase", headers=h, json={"phase": "full_time"})
        else:
            engine.stadium.seek(engine.starts[Phase.full_time] + 1, False)
        mode = "casual" if code == SILVER[1] else "analyst"
        s = client.get(f"/api/stats?mode={mode}", headers=h)
        assert s.status_code == 200 and (s.json()["inspector"] is not None) is visible
        assert (client.get(f"/api/recap?mode={mode}", headers=h).json()["inspector"] is not None) is visible
    h = login(client, SILVER[2])
    assert client.get("/api/stats?mode=analyst", headers=h).status_code == 403


def test_every_engine_endpoint_validates_in_every_phase(server) -> None:
    client, engine, t = server
    h = login(client, DEMO[0])
    client.put("/api/session/preferences", headers=h, json={"mode": "analyst", "favourite_player_ids": ["BRA-09"],
                                                            "language": "en"})
    for ph in PHASE_ORDER:
        st = client.post("/api/demo/phase", headers=h, json={"phase": ph.value})
        assert st.status_code == 200 and st.json()["phase"] == ph.value
        ev = client.get("/api/match/events", headers=h).json()
        assert all(e["type"] != "Delete" for e in ev["events"]) and ev["as_of_s"] == st.json()["as_of_s"]
        assert client.get("/api/stats/live", headers=h).status_code == (200 if ph in (Phase.first_half, Phase.second_half) else 423)
        for mode in ("casual", "analyst"):
            assert client.get(f"/api/stats?mode={mode}", headers=h).status_code == (423 if ph is Phase.pre_match else 200)
            r = client.get(f"/api/recap?mode={mode}", headers=h)
            assert r.status_code == (200 if ph is Phase.full_time else 423)
        for path in ("/api/session", "/api/match", "/api/highlights", "/api/games"):  # delegated to the mock
            assert client.get(path, headers=h).status_code == 200
    rec = client.get("/api/recap?mode=analyst", headers=h).json()
    assert rec["final_score"] == {"home": 2, "away": 1} and "Ulvestad" in rec["narrative"]
    assert rec["favourite_players"][0]["player_id"] == "BRA-09"
    pdf = client.get("/api/recap/pdf?mode=analyst", headers=h)  # L-2g: recap, statistics and key moments (test_report.py)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


# ------------------------------------------------------------------ sources


def test_synthetic_source_missing_files_or_profile_says_why(tmp_path: Path) -> None:
    with pytest.raises(SourceNotAvailable, match="events.json, positions.parquet missing"):
        (fx.match_dir(tmp_path)).mkdir(parents=True)
        (fx.match_dir(tmp_path) / "match_info.json").write_text("{}")
        load_synthetic("SYN-001", tmp_path)
    root = fx.build(tmp_path / "noprofile")
    (root / "reports/qc/SYN-001/match_profile.json").unlink()
    with pytest.raises(SourceNotAvailable, match="qc SYN-001 --source synthetic"):
        load_synthetic("SYN-001", root)
    with pytest.raises(ValueError):
        load_match("nonsense")


def test_set_piece_shot_counts_as_a_shot() -> None:
    """DFL nests the action of a set piece (FreeKick > ShotAtGoal > SavedShot): the shot must count, and a goal
    scored from a free kick must reach the score."""
    from types import SimpleNamespace

    from app.engine.sources.provider import _events

    def row(sub, **attrs):
        return SimpleNamespace(event_id="E1", event_type="FreeKick", sub_types=sub, player_id="BRA-10", x=30.0, y=2.0,
                               attrs_json=json.dumps({"Team": "BRA", **attrs}))

    fk, shot = _events(row("ShotAtGoal/SuccessfulShot"), "BRA", "CAL", 100.0, 1, (0.0, 0.0), 1.0)
    assert (fk.type, shot.type, shot.event_id, shot.attrs) == ("FreeKick", "ShotAtGoal", "E1-shot", {"outcome": "goal"})
    assert shot.team_side == "home" and shot.player_id == "BRA-10"
    (_, unknown) = _events(row("ShotAtGoal"), "BRA", "CAL", 100.0, 1, (0.0, 0.0), 1.0)
    assert unknown.attrs == {}  # no result in the feed: counted as a shot, not on target, never invented
    (kick,) = _events(row("Play", Evaluation="successfullyCompleted", Recipient="BRA-09"), "BRA", "CAL", 1.0, 1,
                      (0.0, 0.0), 1.0)
    assert kick.attrs == {"outcome": "complete"} and kick.secondary_player_id == "BRA-09"


def test_penalty_counts_as_a_shot() -> None:
    """DFL writes a penalty as `Penalty` > `ShotAtGoal` > result (L-3): it counts as a shot (E-3) and is marked."""
    from types import SimpleNamespace

    from app.engine.sources.provider import _events

    row = SimpleNamespace(event_id="E9", event_type="Penalty", sub_types="ShotAtGoal/SavedShot", player_id="CAL-07",
                          x=41.5, y=0.0, attrs_json=json.dumps({"Team": "CAL", "CausingPlayer": "BRA-04"}))
    pen, shot = _events(row, "BRA", "CAL", 100.0, 2, (0.0, 0.0), 1.0)
    assert (pen.type, shot.type, shot.attrs) == ("Penalty", "ShotAtGoal", {"outcome": "saved", "penalty": True})
    assert shot.team_side == "away" and shot.player_id == "CAL-07"


SYN_FILES = [ROOT / "synthetic/SYN-001" / n for n in ("events.json", "positions.parquet")]


@pytest.mark.skipif(not all(p.exists() for p in SYN_FILES), reason="SYN-001 match files not in this checkout")
def test_real_syn001_story_and_frame_metrics() -> None:
    """The committed SYN-001 (S-4) through the pipeline: the scripted 2-1, every payload valid, frame metrics present."""
    e = Engine(load_match("synthetic:SYN-001"))
    ht = e.window.at(e.starts[Phase.half_time] + 1)
    assert stats.match_state(ht, NOW)["score"] == {"home": 1, "away": 1}
    snap = e.window.at(e.starts[Phase.full_time] + 1)
    e.window.check(snap)
    out = payloads(e, e.starts[Phase.full_time] + 1)
    assert out["state"]["score"] == {"home": 2, "away": 1}
    ms = MatchStats.model_validate(out["stats_analyst"])
    assert ms.distance_covered and ms.possession and ms.ball_circulation and ms.effective_playing_time
    fastest = max(ms.top_speed, key=lambda t: t.kmh)  # the scripted sprint: Peyrol, 34-36.2 km/h (generator cap)
    assert fastest.player_id == "BRA-27" and 34.0 <= fastest.kmh <= 36.3
    hardest = max(ms.shot_speed, key=lambda s: s.kmh)
    assert hardest.player_id == "BRA-08" and hardest.kmh > 95  # the 88' winner
    headers = {s.event_id for s in ms.shot_map.shots if s.is_header}
    assert not headers & {s.event_id for s in ms.shot_speed}  # headers stay out of the shot speed
    assert not {u.metric for u in ms.unavailable} & {"xg", "momentum"}  # filled since E-4
    Recap.model_validate(out["recap"])


DFL_RAW = ROOT / "data/raw/dfl"


@pytest.mark.skipif(not list(DFL_RAW.glob("*J03WPY*.xml")) if DFL_RAW.exists() else True,
                    reason="DFL data not on this machine (local development only)")
def test_dfl_source_window_and_stats() -> None:
    m = load_match("dfl:J03WPY")
    e = Engine(m)
    snap = e.window.at(e.starts[Phase.first_half] + 600)
    e.window.check(snap)
    live = LiveStats.model_validate(stats.live_stats(e.core(snap)))
    assert live.phase is Phase.first_half and live.shots and live.counts
    ms = MatchStats.model_validate(stats.match_stats(e.core(snap), Mode.casual, False, NOW))
    assert ms.scope.value == "live" and ms.distance_covered is not None and ms.top_speed[0].kmh <= 38


def test_live_stats_never_from_a_later_instant(syn) -> None:
    e = Engine(syn, EngineConfig(stats_tick_s=30.0))
    e.core(e.window.at(e.starts[Phase.first_half] + 300.0))  # another session, further ahead, computed first
    early = e.window.at(e.starts[Phase.first_half] + 100.0)
    core = e.live_core(early)
    assert core.snap.as_of_s <= early.as_of_s and core.snap.as_of_s == 90.0  # the 30 s tick before minute 2
    later = e.window.at(e.starts[Phase.first_half] + 200.0)
    assert e.live_core(later).snap.as_of_s <= later.as_of_s


def test_truncation_test_catches_a_leaky_window(fixture_dir: Path, syn, tmp_path: Path, monkeypatch) -> None:
    """Control: a window that leaks 30 s of future frames makes the truncation comparison fail."""
    import dataclasses
    import types

    import numpy as np

    from app.engine import window as wmod

    honest = wmod.MatchWindow.at

    def leaky(self, cursor_t):
        s = honest(self, cursor_t)
        fr = {p: self._frames[p].head(int(np.searchsorted(self._frames[p].t, cursor_t + 30.0, side="right")))
              for p in s.frames}
        return dataclasses.replace(s, frames=types.MappingProxyType(fr))

    monkeypatch.setattr(wmod.MatchWindow, "at", leaky)
    full = Engine(syn)
    cursor = full.starts[Phase.first_half] + 200.0
    cut = Engine(load_synthetic("SYN-001", fx.cut(fixture_dir, tmp_path / "leak", fx.KO1 + pd.Timedelta(seconds=cursor))))
    assert payloads(full, cursor) != payloads(cut, cursor)
