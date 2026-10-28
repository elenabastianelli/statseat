"""Metrics of task E-4 in the engine (Materials/METRICS.md, decisions of 25 October), on the small synthetic fixture of
`engine_fixture.py`, sometimes with a few hand-made changes to its frames or events. One test per metric."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app.contract.models import MatchStats, Mode, Phase
from app.engine import frame_stats, stats
from app.engine.engine import Engine
from app.engine.sources.fake import load_fake
from app.engine.sources.synthetic import load_synthetic

from . import engine_fixture as fx

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
M = frame_stats.metrics


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    return load_synthetic("SYN-001", fx.build(tmp_path_factory.mktemp("syn_e4")))


def at(md, phase: Phase, after_s: float = 1.0):
    e = Engine(md)
    return e.core(e.window.at(e.starts[phase] + after_s))


def payload(core, mode: Mode, favourites=None) -> dict:
    p = stats.match_stats(core, mode, True, NOW, favourites or [])
    MatchStats.model_validate(p)
    return p


def with_frames(md, period: int, edit):
    """A copy of the match with `edit(pf, x, y)` applied to copies of one period's player positions."""
    pf = md.frames[period]
    x, y = pf.x.copy(), pf.y.copy()
    edit(pf, x, y)
    return dataclasses.replace(md, frames={**md.frames, period: dataclasses.replace(pf, x=x, y=y)})


def with_event(md, event_id: str, **changes):
    return dataclasses.replace(md, events=tuple(dataclasses.replace(e, **changes) if e.event_id == event_id else e
                                                for e in md.events))


def col_of(md, pid: str) -> int:
    return md.frames[1].pids.index(pid)


def home_shot(md):
    return next(e for e in md.events if e.type == "ShotAtGoal" and e.team_side == "home")


def located_shot_scene(md):
    """The home shot at SHOT_T (ball from (40, 0) towards +x at 25 m/s): the shooter carries the ball and stays at
    (39.7, 0), so his last frame within 1.5 m of the ball is SHOT_T + 0.04 s (ball at x = 41); an away outfield player
    stands in the shot triangle and the away goalkeeper too (he must not count)."""
    shooter = home_shot(md).player_id
    lineup = {p.player_id: p for p in md.lineup}
    away = [p.player_id for p in md.lineup if p.team_side == "away" and p.starting]
    gk = next(pid for pid in away if lineup[pid].position == "GK")
    defender = next(pid for pid in away if lineup[pid].position != "GK")

    def edit(pf, x, y):
        t = pf.t
        run = (t >= fx.SHOT_T - 2.0) & (t <= fx.SHOT_T + 3.0)
        c = pf.pids.index(shooter)
        x[run, c] = np.where(t[run] < fx.SHOT_T, pf.bx[run] - 0.3, 39.7)
        y[run, c] = np.where(t[run] < fx.SHOT_T, pf.by[run], 0.0)
        for pid, x0 in ((defender, 48.0), (gk, 51.0)):  # slow, never frozen paths for the whole period
            c = pf.pids.index(pid)
            x[:, c] = x0 + 0.3 * np.sin(0.5 * t)
            y[:, c] = 0.3 + 0.2 * np.cos(0.5 * t)

    return with_frames(md, 1, edit), defender, gk


# ------------------------------------------------------------------ block 1: xG and the located shot map


def test_xg_one_located_frame_for_shot_map_xg_and_shot_speed(syn) -> None:
    md, defender, gk = located_shot_scene(syn)
    core = at(md, Phase.half_time)
    e = home_shot(md)
    v = core.fr["shots"][e.event_id]
    i = v["index"]
    pf, hr = core.snap.frames[1], frame_stats.prepare(core.snap)[0].hr
    assert v["located"] and pf.t[i] == pytest.approx(fx.SHOT_T + 0.04)
    assert (v["x"], v["y"]) == pytest.approx((41.0, 0.0))  # the ball at the located frame, not the annotated (40, 0)
    assert v["features"]["opponents_in_triangle"] == 1  # the defender counts, the goalkeeper does not
    c = pf.pids.index(defender)
    want = M.shot_xg(41.0, 0.0, [hr.x[i, c]], [hr.y[i, c]], 105.0, 7.32)["xg"]
    assert v["xg"] == pytest.approx(want)
    p = payload(core, Mode.analyst)
    shot = next(s for s in p["shot_map"]["shots"] if s["event_id"] == e.event_id)
    assert (shot["x"], shot["y"], shot["xg"]) == (41.0, 0.0, round(want, 2))
    sel = (pf.t >= pf.t[i]) & (pf.t <= pf.t[i] + 0.5) & hr.ball_valid  # the shot speed starts at the same frame
    assert shot["speed_kmh"] == round(float(np.nanmax(hr.ball_speed[sel])) * 3.6, 1)


def test_xg_fallback_annotated_position_and_fixed_penalty(syn) -> None:
    core = at(syn, Phase.full_time)
    e = home_shot(syn)
    v = core.fr["shots"][e.event_id]
    assert not v["located"] and (v["x"], v["y"]) == (40.0, 0.0)  # the shooter is never near the ball: annotated
    p = payload(core, Mode.analyst)
    xg = next(m for m in p["inspector"]["methods"] if m["metric"] == "xg")
    assert xg["parameters"]["shots_annotated_fallback"] == 2 and "2 at their annotated position" in xg["method"]
    pen = at(with_event(syn, e.event_id, attrs={"outcome": "goal", "penalty": True}), Phase.full_time)
    assert pen.fr["shots"][e.event_id]["xg"] == 0.79


def test_xg_cumulative_goals_vs_xg_and_the_fan_version(syn) -> None:
    core = at(syn, Phase.full_time)
    a, c = payload(core, Mode.analyst, ["BRA-27"]), payload(core, Mode.casual, ["BRA-27"])
    shots = {s["event_id"]: s for s in a["shot_map"]["shots"]}
    assert all(0.0 <= s["xg"] <= 1.0 for s in shots.values())
    for team, series in zip(a["xg"]["teams"], a["xg"]["cumulative"]):
        own = [s for s in a["shot_map"]["shots"] if s["team_id"] == team["team_id"]]
        assert series["points"][0] == {"minute": 0, "value": 0.0} and len(series["points"]) == len(own) + 1
        assert series["points"][-1]["value"] == team["value"] == pytest.approx(sum(s["xg"] for s in own))
    goals = {t["team_id"]: t["goals"] for t in a["shots"]}
    assert a["goals_vs_xg"] == c["goals_vs_xg"] == [{"team_id": t["team_id"], "goals": goals[t["team_id"]],
                                                     "xg": t["value"]} for t in a["xg"]["teams"]]
    assert c["xg"] is None and all(s["xg"] is None for s in c["shot_map"]["shots"])
    assert c["player_focus"][0]["xg"] is None and a["player_focus"][0]["xg"] == shots[home_shot(syn).event_id]["xg"]
    row = next(r for r in a["players"] if r["player_id"] == "BRA-27")
    assert row["xg"] == a["player_focus"][0]["xg"]
    assert not {"xg", "goals_vs_xg"} & {u["metric"] for u in a["unavailable"] + c["unavailable"]}


def test_xg_without_frames_is_null_with_the_reason() -> None:
    core = at(load_fake(), Phase.full_time)
    p = payload(core, Mode.analyst)
    reasons = {u["metric"]: u["reason"] for u in p["unavailable"]}
    assert p["xg"] is None and p["goals_vs_xg"] == [] and all(s["xg"] is None for s in p["shot_map"]["shots"])
    assert "events only" in reasons["xg"] and "events only" in reasons["goals_vs_xg"]


# ------------------------------------------------------------------ block 2: corrections between code and decisions


def test_goalkeepers_are_out_of_the_team_totals(syn) -> None:
    core = at(syn, Phase.full_time)
    gks = frame_stats.gk_ids(core.snap)  # by line-up position: the fixture's substitute is a bench goalkeeper too
    assert gks == {p.player_id for p in core.snap.lineup if p.position == "GK"} and len(gks & set(core.fr["players"])) == 3
    for side in ("home", "away"):
        out = [v for pid, v in core.fr["players"].items() if v["side"] == side and pid not in gks]
        gk = [v for pid, v in core.fr["players"].items() if v["side"] == side and pid in gks]
        t = core.fr["teams"][side]
        assert t["dist_m"] == pytest.approx(sum(v["dist_m"] for v in out)) and gk[0]["dist_m"] > 0
        assert t["sprints"] == sum(v["sprints"] for v in out)
        assert sum(t["zones_m"]) <= t["dist_m"] + 1e-6
    p = payload(core, Mode.analyst)
    assert {r["player_id"] for r in p["players"]} >= gks & set(core.fr["players"])  # keepers who played stay as players


def test_peak_intensity_rolling_window_series_and_players(syn) -> None:
    early = at(syn, Phase.first_half, 200.0)
    p = payload(early, Mode.casual)
    peak = next(u for u in p["unavailable"] if u["metric"] == "peak_intensity")
    assert p["peak_intensity"] is None and peak["available_from_minute"] == 5
    core = at(syn, Phase.half_time)
    a, c = payload(core, Mode.analyst), payload(core, Mode.casual)
    for s in a["peak_intensity"]["series"]:
        assert [q["minute"] for q in s["points"]] == [5, 6]  # one point per minute, from the first full window
    for t, s in zip(a["peak_intensity"]["teams"], a["peak_intensity"]["series"]):
        assert t["end_minute"] - t["start_minute"] == 5 and t["metres_per_minute"] >= max(q["value"] for q in s["points"])
    assert c["peak_intensity"]["series"] == a["peak_intensity"]["series"] and c["peak_intensity"]["players"] is None
    rows = {r["player_id"]: r for r in a["players"]}
    for pk in a["peak_intensity"]["players"]:
        assert rows[pk["player_id"]]["peak_m_per_min"] == pk["metres_per_minute"] > 0
    assert not {"peak_intensity", "peak_intensity.series"} & {u["metric"] for u in a["unavailable"]}


def pass_scene(md):
    """A complete home pass of the first half whose passer stands at the ball where it changes direction (a kick) and
    whose recipient stands where it changes direction 2 s later (the reception): a located, successful pass."""
    pf = md.frames[1]
    e = next(e for e in md.events if e.type == "Play" and e.period == 1 and e.team_side == "home"
             and e.attrs.get("outcome") == "complete" and e.t > 30.0 and e.secondary_player_id != e.player_id)
    w = 2.0 * np.ceil(e.t / 2.0)  # the ball's next waypoint (it moves between waypoints every 2 s)
    iw, ir = (int(np.argmin(np.abs(pf.t - v))) for v in (w, w + 2.0))

    def edit(pf, x, y):
        t = pf.t
        for pid, i in ((e.player_id, iw), (e.secondary_player_id, ir)):
            c = pf.pids.index(pid)
            x[:, c] = pf.bx[i] + 0.3 + 0.05 * np.sin(0.3 * t)
            y[:, c] = pf.by[i] + 0.05 * np.cos(0.3 * t)

    return with_frames(md, 1, edit), e, float(np.hypot(pf.bx[ir] - pf.bx[iw], pf.by[ir] - pf.by[iw]))


def test_pass_length_of_a_located_successful_pass_only_once_its_reception_is_released(syn) -> None:
    md, e, length = pass_scene(syn)
    core = at(md, Phase.half_time)
    got = core.fr["passes"]["lengths"][e.event_id]
    assert got["length_m"] == pytest.approx(length, abs=0.5) and got["side"] == "home"
    p = payload(core, Mode.analyst)
    home = p["passes"][0]
    lengths = [v["length_m"] for v in core.fr["passes"]["lengths"].values() if v["side"] == "home"]
    assert home["avg_length_m"] == round(float(np.mean(lengths)), 1)
    assert home["long_balls"] == sum(v >= 32.0 for v in lengths)
    row = next(r for r in p["players"] if r["player_id"] == e.player_id)
    assert row["long_balls"] == int(length >= 32.0)
    assert all(x["avg_length_m"] is None and x["long_balls"] is None for x in payload(core, Mode.casual)["passes"])
    soon = at(md, Phase.first_half, e.t + 6.0)  # released, but its 10 s reception search is not: waits
    assert e.event_id not in soon.fr["passes"]["lengths"] and soon.fr["passes"]["counts"]["waiting"] >= 1


def test_ball_circulation_leaves_out_the_2_s_after_shots_and_clearances(syn, monkeypatch) -> None:
    from types import SimpleNamespace

    from app.engine.sources.provider import _events

    row = SimpleNamespace(event_id="C1", event_type="OtherBallAction", sub_types="", player_id="CAL-04", x=None, y=None,
                          attrs_json='{"Team": "CAL", "DefensiveClearance": true}')
    (clearance,) = _events(row, "BRA", "CAL", 50.0, 1, (0.0, 0.0), 1.0)
    assert clearance.attrs == {"clearance": True}  # the pipeline's clearance, kept by the adapter
    md = dataclasses.replace(syn, events=tuple(sorted(syn.events + (dataclasses.replace(clearance, team_side="away"),),
                                                      key=lambda e: (e.t, e.event_id))))
    core = at(md, Phase.full_time)
    assert core.fr["circulation"]["excluded_kicks"] == 3  # two shots and the clearance
    base = core.fr["circulation"]["values"]
    monkeypatch.setattr(frame_stats, "CFG", dataclasses.replace(frame_stats.CFG, circulation_exclude_after_s=10_000.0))
    late = at(md, Phase.full_time).fr["circulation"]["values"]  # everything after the first kick of a half left out
    assert late != base


def test_shots_on_target_are_goals_and_saves_never_woodwork(syn) -> None:
    from insidethegame_data.mapping import map_shot_outcome

    e = home_shot(syn)
    core = at(with_event(syn, e.event_id, attrs={"outcome": "woodwork"}), Phase.full_time)
    p = payload(core, Mode.casual)
    home = p["shots"][0]
    assert (home["shots"], home["on_target"], home["goals"]) == (1, 0, 0) and p["counts"][0]["woodwork"] == 1
    assert stats.ON_TARGET == {"goal", "saved"} and map_shot_outcome("ShotWoodWork") == \
        {**map_shot_outcome("ShotWoodWork"), "outcome": "woodwork", "on_target": False}


# ------------------------------------------------------------------ block 3: momentum


def test_momentum_one_point_per_played_minute_within_minus_1_plus_1(syn) -> None:
    core = at(syn, Phase.full_time)
    a, c = payload(core, Mode.analyst), payload(core, Mode.casual)
    m = a["momentum"]
    assert c["momentum"] == m and [q["minute"] for q in m["series"]] == [1, 2, 3, 4, 5, 6, 46, 47, 48, 49, 50, 51]
    assert all(-1.0 <= q["value"] <= 1.0 for q in m["series"]) and m["current"] == m["series"][-1]["value"]
    # The window ending at 2' of the first half (no shot yet): territory only, recomputed here from the frames.
    pf = core.snap.frames[1]
    hr = frame_stats.prepare(core.snap)[0].hr
    clock = frame_stats.period_clock(core.snap, pf)
    win = (clock > 0.0) & (clock <= 120.0) & (pf.bstatus == 1) & hr.ball_valid
    tilt = {s: float((win & (pf.bposs == code) & (frame_stats.attack_sign(s, 1) * pf.bx > 105.0 / 6)).sum())
            for s, code in (("home", 1.0), ("away", 2.0))}
    want = (tilt["home"] - tilt["away"]) / (tilt["home"] + tilt["away"])
    assert m["series"][1]["value"] == round(want, 2)
    # The home shot at 200 s (3' 20") is in the windows ending at 4', 5' and 6': danger is all home's.
    for q in m["series"][3:6]:
        assert q["value"] >= 0.0
    assert not {"momentum"} & {u["metric"] for u in a["unavailable"] + c["unavailable"]}
    fake = payload(at(load_fake(), Phase.full_time), Mode.casual)
    assert fake["momentum"] is None and "events only" in {u["metric"]: u["reason"] for u in fake["unavailable"]}["momentum"]


# ------------------------------------------------------------------ block 4: acceleration load, recoveries, counters


def test_acceleration_load_teams_series_and_player_focus(syn) -> None:
    core = at(syn, Phase.full_time)
    gks = frame_stats.gk_ids(core.snap)
    a, c = payload(core, Mode.analyst, ["BRA-27"]), payload(core, Mode.casual, ["BRA-27"])
    load = core.fr["load"]
    for t, side in zip(a["acceleration_load"]["teams"], ("home", "away")):
        want = sum(v["kmh"] for pid, v in load["players"].items() if pid not in gks and core.fr["players"][pid]["side"] == side)
        assert t["total_kmh"] == round(want, 1) > 0 and t["reliability"]["level"] == "high"
        assert t["per_minute_kmh"] == round(want / (core.snap.as_of_s / 60.0), 1)
    assert [[q["minute"] for q in s["points"]] for s in a["acceleration_load"]["series"]] == [[5, 50], [5, 50]]
    assert c["acceleration_load"]["series"] is None and c["acceleration_load"]["teams"] == a["acceleration_load"]["teams"]
    fa, fc = a["player_focus"][0], c["player_focus"][0]
    assert fa["acceleration_load_kmh"] == fc["acceleration_load_kmh"] == round(load["players"]["BRA-27"]["kmh"], 1)
    assert [q["minute"] for q in fa["acceleration_load_series"]] == [5, 50] and fc["acceleration_load_series"] is None
    row = next(r for r in a["players"] if r["player_id"] == "BRA-27")
    assert row["acceleration_load_kmh"] == fa["acceleration_load_kmh"]
    zones = fa["speed_zones"]
    assert zones["player_id"] == "BRA-27" and zones["team_id"] == "BRA" and fc["speed_zones"] is None
    assert sum(v for k, v in zones.items() if k.startswith("km_")) <= fa["distance"]["km"] + 0.03
    assert not {"acceleration_load"} & {u["metric"] for u in a["unavailable"] + c["unavailable"]}


def with_ball(md, period: int, edit):
    pf = md.frames[period]
    bx, by = pf.bx.copy(), pf.by.copy()
    edit(pf, bx, by)
    return dataclasses.replace(md, frames={**md.frames, period: dataclasses.replace(pf, bx=bx, by=by)})


def add_events(md, *events):
    return dataclasses.replace(md, events=tuple(sorted(md.events + events, key=lambda e: (e.t, e.event_id))))


def test_tackles_won_recoveries_checked_on_possession_and_a_counter_attack(syn) -> None:
    from app.engine.sources.base import Event

    home = [p.player_id for p in syn.lineup if p.team_side == "home" and p.starting and p.position != "GK"]
    shot = home_shot(syn)
    # A home ball claim at 180 s, when the fixture's possession passes from away to home (30 s blocks), with the ball
    # in the home half there; the home shot moved to 194 s: a recovery in the own half and a shot 14 s later.
    claim = Event(event_id="FX-REC", type="BallClaiming", period=1, t=180.0, team_side="home", player_id=home[3])
    md = add_events(with_event(syn, shot.event_id, t=194.0), claim)
    md = with_ball(md, 1, lambda pf, bx, by: bx.__setitem__(int(np.searchsorted(pf.t, 180.0)), -10.0))
    core = at(md, Phase.full_time)
    assert [r["event"].event_id for r in core.fr["recoveries"]] == ["FX-REC"]  # the fixture's own events: no change
    a, c = payload(core, Mode.analyst), payload(core, Mode.casual)
    assert [x["ball_recoveries"] for x in a["counts"]] == [1, 0]
    assert [x["tackles"] for x in a["counts"]] == [0, 1]  # the duel without a role counts as won by its winner
    assert a["counter_attacks"]["teams"] == [{"team_id": "BRA", "count": 1}, {"team_id": "CAL", "count": 0}]
    assert a["counter_attacks"]["items"] == [{"team_id": "BRA", "minute": 4, "recovery_event_id": "FX-REC",
                                              "shot_event_id": shot.event_id}]
    assert c["counter_attacks"]["items"] is None and c["counter_attacks"]["teams"] == a["counter_attacks"]["teams"]
    far = add_events(syn, claim)  # the shot stays at 200 s: 20 s after the recovery, no counter-attack
    assert at(far, Phase.full_time).fr["counter_attacks"] == []
    assert not {"counter_attacks"} & {u["metric"] for u in a["unavailable"]}


def offside_scene(md, attacker_x: float, extra=()):
    """The home pass at 17 s (P1): the ball waits at (0, 0) and leaves at 15 m/s along +x at 17 s, with the passer
    0.3 m behind it (a located kick); the away goalkeeper at x = 50, one away defender at x = 20, the other away
    players at x <= -10 and a home attacker at `attacker_x`; an official Offside of that attacker 1.5 s later."""
    from app.engine.sources.base import Event

    ps = next(e for e in md.events if e.type == "Play" and e.period == 1 and abs(e.t - 17.0) < 1e-6)
    lineup = {p.player_id: p for p in md.lineup}
    away = [p.player_id for p in md.lineup if p.team_side == "away" and p.starting]
    attacker = next(p.player_id for p in md.lineup if p.team_side == "home" and p.starting and p.position != "GK"
                    and p.player_id not in (ps.player_id, ps.secondary_player_id))

    def ball(pf, bx, by):
        t = pf.t
        w = (t >= 15.0) & (t <= 19.0)
        bx[w], by[w] = np.where(t[w] < 17.0, 0.0, 15.0 * (t[w] - 17.0)), 0.0

    def players(pf, x, y):
        t = pf.t
        wobble = 0.05 * np.sin(0.3 * t)
        put = {ps.player_id: (-0.3, 0.0), attacker: (attacker_x, 5.0)}
        defenders = [pid for pid in away if lineup[pid].position != "GK"]
        put[next(pid for pid in away if lineup[pid].position == "GK")] = (50.0, 0.0)
        put[defenders[0]] = (20.0, -5.0)
        put.update({pid: (-10.0 - 2.0 * k, -25.0 + 5.0 * k) for k, pid in enumerate(defenders[1:])})
        for pid, (x0, y0) in put.items():
            c = pf.pids.index(pid)
            x[:, c], y[:, c] = x0 + wobble, y0 + wobble

    out = with_frames(with_ball(md, 1, ball), 1, players)
    off = Event(event_id="FX-OFF", type="Offside", period=1, t=18.5, team_side="home", player_id=attacker)
    return add_events(out, off, *extra), ps, attacker


def test_offside_check_verdicts_with_the_2_m_band_and_law_11(syn) -> None:
    from app.engine import offside

    assert offside.verdict(23.0, [50.0, 20.0, -10.0], 0.0)[:2] == ("offside", 3.0)
    assert offside.verdict(21.0, [50.0, 20.0], 0.0)[0] == offside.TOO_CLOSE
    assert offside.verdict(17.0, [50.0, 20.0], 0.0)[0] == "onside"
    assert offside.verdict(23.0, [50.0, 20.0], 22.0)[0] == offside.TOO_CLOSE  # the ball ahead of the defender sets it
    assert offside.verdict(-5.0, [50.0, -20.0], -30.0)[0] == "onside"  # in his own half
    assert offside.verdict(23.0, [50.0], 0.0)[0] is None  # fewer than two opponents
    for x, want in ((23.0, "offside"), (21.0, offside.TOO_CLOSE), (17.0, "onside")):
        md, ps, attacker = offside_scene(syn, x)
        snap = Engine(md).window.at(Engine(md).starts[Phase.half_time] + 1.0)
        r = offside.offside_check(snap, "FX-OFF")
        assert (r["case"], r["verdict"], r["pass_event_id"], r["attacker_id"]) == ("offside", want, ps.event_id, attacker)
        assert r["margin_m"] == pytest.approx(x - 20.0, abs=0.15) and r["minute"] == 1 and r["frame"] is not None


def test_offside_check_only_on_released_data_and_says_why_when_it_cannot(syn) -> None:
    from app.engine import offside
    from app.engine.sources.base import Event

    md, _, _ = offside_scene(syn, 23.0)
    e = Engine(md)
    early = e.window.at(20.0)  # feed time 20 s: the offside at 18.5 s is released only at 23.5 s
    assert "not among the released events" in offside.offside_check(early, "FX-OFF")["reason"]
    throw = Event(event_id="FX-THROW", type="ThrowIn", period=1, t=18.0, team_side="home", player_id="BRA-02")
    md2, _, _ = offside_scene(syn, 23.0, extra=(throw,))
    r = offside.offside_check(Engine(md2).window.at(e.starts[Phase.half_time] + 1.0), "FX-OFF")
    assert r["verdict"] == "onside" and "throw-in" in r["reason"] and r["pass_event_id"] == "FX-THROW"
    ht = e.window.at(e.starts[Phase.half_time] + 1.0)
    assert offside.offside_check(ht, home_shot(md).event_id)["case"] == "goal"
    assert offside.offside_check(ht, "nope")["verdict"] is None
    foul = next(x for x in ht.events if x.type == "Foul")
    assert offside.offside_check(ht, foul.event_id)["reason"] == "only official offsides and goals are checked"


def test_offside_check_takes_the_last_ball_played_by_a_team_mate_not_an_earlier_pass(syn) -> None:
    """The offside position counts when a team-mate last plays the ball (Law 11): after the home pass at 17 s the away
    team plays the ball, then a home team-mate flicks it on (an `OtherBallAction`) and the attacker touches it himself:
    the flick-on is the checked moment (two DFL calls were like this), never the earlier pass or the attacker's touch."""
    from app.engine import offside
    from app.engine.sources.base import Event

    md, ps, attacker = offside_scene(syn, 23.0)
    mate = next(p.player_id for p in md.lineup if p.team_side == "home" and p.starting and p.position != "GK"
                and p.player_id not in (ps.player_id, ps.secondary_player_id, attacker))

    def put(pf, x, y):  # the team-mate stands on the ball's path at x = 15 m, reached at 18 s
        c = pf.pids.index(mate)
        x[:, c], y[:, c] = 15.0 + 0.05 * np.sin(0.3 * pf.t), 0.5 + 0.05 * np.cos(0.3 * pf.t)

    away = next(p.player_id for p in md.lineup if p.team_side == "away" and p.starting and p.position != "GK")
    md = add_events(with_frames(md, 1, put), Event("FX-OPP", "Play", 1, 17.5, "away", away),
                    Event("FX-FLICK", "OtherBallAction", 1, 18.0, "home", mate),
                    Event("FX-OWN", "OtherBallAction", 1, 18.3, "home", attacker))
    e = Engine(md)
    r = offside.offside_check(e.window.at(e.starts[Phase.half_time] + 1.0), "FX-OFF")
    assert (r["pass_event_id"], r["attacker_id"], r["verdict"]) == ("FX-FLICK", attacker, "offside")
    assert r["margin_m"] == pytest.approx(3.0, abs=0.3)  # attacker 23 m, second-last opponent 20 m, ball about 16 m


# ------------------------------------------------------------------ block 5: zone map, defensive shape, PPDA


def test_zone_maps_per_team_and_favourite_for_analyzer_only(syn) -> None:
    core = at(syn, Phase.full_time)
    a, c = payload(core, Mode.analyst, ["BRA-27"]), payload(core, Mode.casual, ["BRA-27"])
    assert [z["team_id"] for z in a["zone_map"]] == ["BRA", "CAL"] and c["zone_map"] is None
    for z in a["zone_map"] + [a["player_focus"][0]["zone_map"]]:
        assert (z["rows"], z["cols"]) == (17, 21) and len(z["cells_pct"]) == 17 and len(z["cells_pct"][0]) == 21
        assert sum(map(sum, z["cells_pct"])) == pytest.approx(100.0, abs=0.1)
    assert a["player_focus"][0]["zone_map"]["player_id"] == "BRA-27" and c["player_focus"][0]["zone_map"] is None
    xs, ys = [], []  # the home team: its outfield players' valid samples in live play, attacking +x
    gks = frame_stats.gk_ids(core.snap)
    for p in frame_stats.prepare(core.snap):
        s = frame_stats.attack_sign("home", p.pf.period)
        for col, pid in enumerate(p.pf.pids):
            m = p.hr.valid[:, col] & (p.pf.bstatus == 1) & np.isfinite(p.hr.x[:, col])
            if p.pf.team_side[col] == "home" and pid not in gks:
                xs.append(s * p.hr.x[m, col])
                ys.append(s * p.hr.y[m, col])
    want = M.zone_map(np.concatenate(xs), np.concatenate(ys), 105.0, 68.0, frame_stats.CFG)
    assert np.allclose(np.array(a["zone_map"][0]["cells_pct"]), np.round(want, 2))


def test_defensive_line_and_team_length_per_five_minutes(syn) -> None:
    core = at(syn, Phase.full_time)
    a, c = payload(core, Mode.analyst), payload(core, Mode.casual)
    assert c["defensive_shape"] is None
    for d in a["defensive_shape"]:
        assert [q["minute"] for q in d["defensive_line_m"]] == [5, 50] == [q["minute"] for q in d["team_length_m"]]
        assert all(0.0 < q["value"] < 105.0 for q in d["defensive_line_m"] + d["team_length_m"])
    early = payload(at(syn, Phase.first_half, 200.0), Mode.analyst)
    shape = next(u for u in early["unavailable"] if u["metric"] == "defensive_shape")
    assert early["defensive_shape"] is None and shape["available_from_minute"] == 5


def test_ppda_from_located_opponent_passes_and_defensive_actions_in_the_pressing_zone(syn) -> None:
    md, _, _ = pass_scene(syn)  # one located home pass at least
    core = at(md, Phase.full_time)
    v = core.fr["ppda"]
    a, c = payload(core, Mode.analyst), payload(core, Mode.casual)
    assert c["ppda"] is None
    got = {x["team_id"]: x["value"] for x in a["ppda"] or []}
    for side, tid in (("home", "BRA"), ("away", "CAL")):
        if v[side]["actions"]:
            assert got[tid] == round(v[side]["passes"] / v[side]["actions"], 1)
        else:  # no defensive action in its pressing zone: no value, and the reason is listed
            assert tid not in got and any(u["metric"] in (f"ppda.{tid}", "ppda") for u in a["unavailable"])
    kicks = [k for k in core.fr["passes"]["kicks"].values() if k["side"] == "home"]
    assert kicks  # the scene's pass is located
    in_zone = sum(frame_stats.attack_sign("away", k["period"]) * core.snap.frames[k["period"]].bx[k["index"]] >= -0.1 * 105.0
                  for k in kicks)
    assert v["away"]["passes"] == in_zone  # home passes in the away team's attacking 60 %
    m = next(x for x in a["inspector"]["methods"] if x["metric"] == "ppda")
    assert f"BRA {v['home']['passes']} passes / {v['home']['actions']} actions" in m["method"]
