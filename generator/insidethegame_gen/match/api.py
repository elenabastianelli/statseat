"""Public functions of the match generator (designed to be called as tools by an agent later).

- `generate_match(script, seed, out_dir)`: a scripted match (SYN-001 or a test script).
- `generate_replica(match_id, replica, out_dir)`: the same fixture and squads, no story (Markov only);
  the replica folder also gets its own `match_info.json`, so the data pipeline reads it like any match.
- `realism_report(match_dir)`: basic sanity statistics of the written files.

Determinism: one integer seed per match, derived from the universe seed, the match id and the
replica index (sha256, never Python's salted hash); separate PCG64 streams per component; no
wall-clock value in any output file. Two runs give byte-identical files.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .calibration import SCRIPTED_SPRINT_MAX_MS, SPRINT_MARGIN_MS, load_calibration
from .director import Director
from .events import Engine
from .outcomes import check_outcomes, kick_ownership, resolve_outcomes
from .pitch import FS, Pitch, clock_minute, parse_clock, period_start_clock
from .players import build_roster
from .report import realism_report
from .script import Moment, Script, load_script
from .writer import build_events, file_info, write_events, write_positions

MATCH_GENERATOR_VERSION = "match-0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[3]

__all__ = ["generate_match", "generate_replica", "replica_match_info", "realism_report", "derive_seed", "MATCH_GENERATOR_VERSION"]


def derive_seed(universe_seed: int, match_id: str, replica: int = 0) -> int:
    digest = hashlib.sha256(f"{universe_seed}:{match_id}:{replica}".encode()).hexdigest()
    return int(digest[:15], 16)


@dataclass
class Plan:
    match_id: str
    universe_id: str
    scripted: bool
    seed: int
    period_end_s: dict
    first_side: str
    kickoff_utc_ns: int
    half_time_break_s: float
    home_push: list
    intensity_bins: list
    stoppage_push: dict = field(default_factory=dict)
    stoppage_intensity: dict = field(default_factory=dict)
    moments: list = field(default_factory=list)
    bin_s: float = 300.0

    def momentum(self, clock_s: float, period: int) -> tuple[float, float]:
        elapsed = clock_s - period_start_clock(period)
        if elapsed >= 45 * 60 and str(period) in self.stoppage_push:
            return self.stoppage_push[str(period)], self.stoppage_intensity.get(str(period), 1.0)
        i = int(min(clock_s, 90 * 60 - 1) // self.bin_s)
        i = min(i, len(self.home_push) - 1)
        return float(self.home_push[i]), float(self.intensity_bins[min(i, len(self.intensity_bins) - 1)])


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _kickoff_ns(planned_iso: str, delay_s: float) -> int:
    dt = datetime.fromisoformat(planned_iso).astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    whole = int((dt - epoch).total_seconds())
    return whole * 1_000_000_000 + int(round(delay_s * 1000)) * 1_000_000


def _rngs(seed: int) -> dict:
    names = ["events", "params", "wander", "noise", "annotation", "plan"]
    children = np.random.SeedSequence(seed).spawn(len(names))
    return {n: np.random.Generator(np.random.PCG64(c)) for n, c in zip(names, children)}


def _context(repo_root: Path, match_id: str, universe=None, match_info=None):
    universe = universe or _load_json(repo_root / "synthetic" / "universe" / "universe.json")
    match_info = match_info or _load_json(repo_root / "synthetic" / match_id / "match_info.json")
    return universe, match_info


def _object_ids(match_info: dict) -> list[str]:
    ids = []
    for team in match_info["teams"]:
        ids += [e["player_id"] for e in team["lineup"]]
    return sorted(ids)


def _run(plan: Plan, universe: dict, match_info: dict, out_dir: Path, calibration_dir: Path | None,
         overwrite: bool, strict: bool = True) -> dict:
    out_dir = Path(out_dir)
    targets = [out_dir / "positions.parquet", out_dir / "events.json"]
    existing = [p for p in targets if p.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite {', '.join(map(str, existing))} (pass overwrite=True / --overwrite)")
    cal = load_calibration(calibration_dir)
    rngs = _rngs(plan.seed)
    pitch = Pitch(float(match_info["environment"]["pitch_x_m"]), float(match_info["environment"]["pitch_y_m"]))
    roster = build_roster(universe, match_info, cal, rngs["params"])
    # the scripted sprinter must be the fastest of the match
    sprint_vmax = None
    sprinter_side = None
    sprinter_id = None
    for mo in plan.moments:
        if mo.type == "intense_spell" and mo.sprint_player_id:
            others = [p.vmax for pid, p in roster.players.items() if pid != mo.sprint_player_id]
            sprint_vmax = min(max(max(others) + SPRINT_MARGIN_MS, roster.players[mo.sprint_player_id].vmax),
                              SCRIPTED_SPRINT_MAX_MS)
            sprinter_side = roster.players[mo.sprint_player_id].side
            sprinter_id = mo.sprint_player_id
    engine = Engine(plan, roster, cal, pitch, rngs, _object_ids(match_info))
    director = Director(plan.moments, sprint_vmax=sprint_vmax)
    director._sprint_side = sprinter_side
    engine.director = director
    first = plan.first_side
    for period in (1, 2):
        end = plan.period_end_s[period]
        n_max = int((end + 240.0) * FS)
        engine.play_period(period, first if period == 1 else ("away" if first == "home" else "home"), end, n_max)
    # kick-off times
    n1 = engine.buffers[1].n
    kickoff_ns = {1: plan.kickoff_utc_ns}
    kickoff_ns[2] = plan.kickoff_utc_ns + (n1 - 1) * (1_000_000_000 // FS) + int(round(plan.half_time_break_s * 1000)) * 1_000_000
    # results of passes, shots and duels from what happened in the simulation
    resolve_outcomes(engine)
    # checks before writing
    problems = []
    outcome_problems = check_outcomes(engine)
    if plan.scripted:
        for mo in plan.moments:
            if mo.type == "intense_spell":
                continue
            log = director.log.get(mo.id)
            if log is None:
                problems.append(f"moment {mo.id} ({mo.type}) did not happen")
            elif log["actual_minute"] != log["planned_minute"]:
                problems.append(f"moment {mo.id} ({mo.type}) happened in minute {log['actual_minute']} instead of "
                                f"{log['planned_minute']} (delta {log['delta_s']} s)")
        want = (plan_score := {"home": 0, "away": 0})
        for mo in plan.moments:
            if mo.type == "goal" or (mo.type == "penalty" and mo.penalty_outcome == "goal"):
                plan_score[mo.team] += 1
        if engine.score != want:
            problems.append(f"score {engine.score} differs from the script {want}")
        expected = {"goal": "SuccessfulShot", "woodwork": "ShotWoodWork", "save": "SavedShot"}
        penalty_result = {"goal": "SuccessfulShot", "saved": "SavedShot", "wide": "ShotWide"}
        for mo in plan.moments:
            log = director.log.get(mo.id)
            want_result = penalty_result.get(mo.penalty_outcome) if mo.type == "penalty" else expected.get(mo.type)
            if want_result is None or log is None:
                continue
            shot = next((ev for ev in engine.events if ev["period"] == log["period"] and ev["f"] == log["frame"]
                         and ev["player_id"] == mo.player_id and (ev.get("resolve") or {}).get("what") == "shot"), None)
            got = None if shot is None else shot["resolve"]["result"]
            if got != want_result:
                problems.append(f"moment {mo.id} ({mo.type}): the shot of {mo.player_id} ended as {got}")
            if mo.type == "penalty" and (shot is None or shot["event_type"] != "Penalty"):
                problems.append(f"moment {mo.id}: the penalty kick is not written as a DFL Penalty event")
        if sprinter_id is not None:
            tops = _top_speeds(engine)
            fastest = max(tops, key=tops.get)
            runner_up = max(v for k, v in tops.items() if k != sprinter_id)
            if fastest != sprinter_id or tops[sprinter_id] - runner_up < 0.15:
                problems.append(f"the scripted sprinter {sprinter_id} is not clearly the fastest "
                                f"({tops[sprinter_id] * 3.6:.1f} km/h; fastest {fastest} {tops[fastest] * 3.6:.1f} km/h)")
    for p, buf in engine.buffers.items():
        B, live = buf.B[:buf.n], buf.live[:buf.n]
        mouth = live & (np.abs(B[:, 0]) > pitch.hl + 0.11) & (np.abs(B[:, 1]) < 3.66) & (B[:, 2] < 2.44)
        first = np.nonzero(mouth & ~np.r_[False, mouth[:-1]])[0]
        goal_f = {f for (gp, f, _, _) in engine.goal_frames if gp == p}
        stray = [int(f) for f in first if int(f) not in goal_f]
        if stray:
            problems.append(f"period {p}: the live ball enters a goal without a goal at frames {stray[:5]}")
    problems += offside_goal_problems(engine)
    if problems and strict:
        raise RuntimeError("generated match does not follow the script:\n- " + "\n- ".join(problems))
    if outcome_problems:
        raise RuntimeError("written outcomes do not match the simulation:\n- " + "\n- ".join(outcome_problems[:20]))
    events = build_events(engine, kickoff_ns, plan.match_id, rngs["annotation"])
    write_positions(engine, kickoff_ns, out_dir / "positions.parquet", rngs["noise"])
    write_events(events, out_dir / "events.json", plan.match_id, plan.universe_id, MATCH_GENERATOR_VERSION, plan.seed)
    return {
        "match_id": plan.match_id, "seed": plan.seed, "scripted": plan.scripted,
        "score": dict(engine.score),
        "periods": {p: {"frames": engine.buffers[p].n, "minutes": round(engine.buffers[p].n / FS / 60, 2),
                        "end_minute": clock_minute(period_start_clock(p) + (engine.buffers[p].n - 1) / FS, p)}
                    for p in (1, 2)},
        "moments": [director.log[k] for k in sorted(director.log, key=lambda k: (director.log[k]["period"],
                                                                                    director.log[k]["actual_clock_s"]))],
        "script_problems": problems,
        "offside_positions": list(engine.offside_log),
        "kick_ownership": kick_ownership(engine),  # E-6 self-check (R-TOUCH-NEAREST) on the noise-free tracks
        "outcome_problems": outcome_problems,
        "calibration": cal.summary(),
        "players": {pid: {"pace": p.pace, "pace_source": p.pace_source, "top_speed_kmh_target": p.top_speed_kmh}
                    for pid, p in sorted(roster.players.items())},
        "sprint_top_speed_kmh": round(sprint_vmax * 3.6, 2) if sprint_vmax else None,
        "files": {"positions": file_info(out_dir / "positions.parquet"), "events": file_info(out_dir / "events.json")},
    }


def offside_goal_problems(engine) -> list[str]:
    """No goal from an offside position (L-3): a pass to a receiver in an offside position that was not whistled
    (only possible inside a scripted sequence) must not be followed by a goal of that team before the next restart."""
    out = []
    restarts = {(ev["period"], ev["f"]) for ev in engine.events
                if ev["event_type"] in ("KickOff", "ThrowIn", "GoalKick", "CornerKick", "FreeKick", "Penalty")}
    for o in engine.offside_log:
        if o["whistled"]:
            continue
        side = engine.roster.players[o["receiver"]].side
        nxt = [f for (p, f) in restarts if p == o["period"] and f > o["f"]]
        until = min(nxt) if nxt else 10 ** 9
        for (p, f, s, scorer) in engine.goal_frames:
            if p == o["period"] and s == side and o["f"] < f <= until:
                out.append(f"goal by {scorer} (period {p}, frame {f}) after a pass to {o['receiver']} in an offside "
                           f"position ({o['margin_m']} m, moment {o['moment']})")
    return out


def _top_speeds(engine) -> dict[str, float]:
    """Top speed (m/s, Savitzky-Golay on the noise-free tracks) of every player."""
    from .report import player_kinematics
    tops: dict[str, float] = {}
    for p, buf in engine.buffers.items():
        for s in range(buf.P.shape[1]):
            pid = buf.pid[:buf.n, s]
            for code in np.unique(pid):
                m = pid == code
                sp, _ = player_kinematics(buf.P[:buf.n, s, 0][m], buf.P[:buf.n, s, 1][m])
                key = engine.object_ids[int(code)]
                tops[key] = max(tops.get(key, 0.0), float(sp.max()) if sp.size else 0.0)
    return tops


def generate_match(script_path_or_dict, seed: int | None, out_dir, *, universe: dict | None = None,
                   match_info: dict | None = None, calibration_dir=None, overwrite: bool = False,
                   repo_root: Path = REPO_ROOT, strict: bool = True) -> dict:
    """Generate a scripted match. `seed=None` derives it from the universe seed and the match id."""
    src = script_path_or_dict if isinstance(script_path_or_dict, dict) else _load_json(Path(script_path_or_dict))
    match_id = src["match_id"]
    universe, match_info = _context(repo_root, match_id.split("-r")[0] if "-r" in match_id else match_id, universe,
                                    match_info)
    script: Script = load_script(src, match_info)
    if seed is None:
        seed = derive_seed(int(universe["meta"]["seed"]), script.match_id, 0)
    plan = Plan(match_id=script.match_id, universe_id=script.universe_id, scripted=True, seed=int(seed),
                period_end_s={1: script.period_end_s(1) - period_start_clock(1),
                              2: script.period_end_s(2) - period_start_clock(2)},
                first_side=script.kickoff.period1_team,
                kickoff_utc_ns=_kickoff_ns(match_info["planned_kickoff"], script.kickoff.delay_s),
                half_time_break_s=script.kickoff.half_time_break_s,
                home_push=list(script.momentum.home_push), intensity_bins=list(script.momentum.intensity),
                stoppage_push=dict(script.momentum.stoppage_home_push),
                stoppage_intensity=dict(script.momentum.stoppage_intensity),
                moments=list(script.moments), bin_s=60.0 * script.momentum.bin_minutes)
    cal_dir = calibration_dir if calibration_dir is not None else repo_root / "calibration"
    return _run(plan, universe, match_info, Path(out_dir), cal_dir, overwrite, strict=strict)


def replica_plan(match_info: dict, universe: dict, match_id: str, replica: int, period_minutes: float = 45.0) -> Plan:
    """A result-free plan: same squads, random stoppage time, kick-off team, momentum and substitutions."""
    seed = derive_seed(int(universe["meta"]["seed"]), match_id, replica)
    rng = _rngs(seed)["plan"]
    base = period_minutes * 60.0
    p1 = base + (float(rng.integers(1, 4)) - 1.0) * 60.0 + float(rng.uniform(5, 50))
    p2 = base + (float(rng.integers(3, 6)) - 1.0) * 60.0 + float(rng.uniform(5, 50))
    bins = max(1, int(math.ceil(2 * period_minutes / 5.0)))
    walk = np.cumsum(rng.normal(0, 0.18, bins))
    home_push = list(np.round(np.clip(walk - walk.mean() + rng.normal(0.05, 0.1), -0.7, 0.7), 2))
    intensity = list(np.round(np.clip(1.0 + rng.normal(0, 0.06, bins), 0.85, 1.2), 2))
    moments = []
    if period_minutes >= 30:
        lineup = {("home" if t["role"] == "home" else "away"): t for t in match_info["teams"]}
        k = 0
        for side, team in sorted(lineup.items()):
            starters = [e for e in team["lineup"] if e["starting"] and e["playing_position"] != "GK"]
            bench = [e for e in team["lineup"] if not e["starting"] and e["playing_position"] != "GK"]
            n_subs = int(rng.integers(3, 6))
            n_windows = int(rng.integers(1, 4))
            clocks = sorted(float(c) for c in rng.uniform(55 * 60, 86 * 60, n_windows))
            from ..schema import POSITION_GROUP
            used_in, used_out = set(), set()
            for j in range(n_subs):
                clock = clocks[j % n_windows]
                outs = [e for e in starters if e["player_id"] not in used_out]
                out = outs[int(rng.integers(len(outs)))]
                grp = POSITION_GROUP[out["playing_position"]]
                cands = [e for e in bench if e["player_id"] not in used_in]
                same = [e for e in cands if POSITION_GROUP.get(e["playing_position"]) == grp]
                pick = (same or cands)[int(rng.integers(len(same or cands)))]
                used_in.add(pick["player_id"])
                used_out.add(out["player_id"])
                k += 1
                moments.append(Moment(id=f"R{k:02d}", type="substitution", period=2,
                                      clock=f"{int(clock // 60):02d}:{clock % 60:04.1f}", team=side,
                                      player_out=out["player_id"], player_in=pick["player_id"]))
        moments.sort(key=lambda m: (m.period, m.clock_s))
    return Plan(match_id=f"{match_id}-r{replica}", universe_id=match_info.get("universe_id", ""), scripted=False,
                seed=seed, period_end_s={1: p1, 2: p2}, first_side="home" if rng.random() < 0.5 else "away",
                kickoff_utc_ns=_kickoff_ns(match_info["planned_kickoff"], float(rng.uniform(2, 40))),
                half_time_break_s=float(rng.uniform(870, 990)), home_push=home_push, intensity_bins=intensity,
                moments=moments)


def replica_match_info(match_info: dict, replica_id: str) -> dict:
    """The replica's own ``match_info.json``: the fixture's, with the replica's match id.

    ``replica_plan`` changes no field of the match information: the squads, line-ups, officials, venue,
    pitch and planned kick-off are the fixture's (the actual kick-off delay, the kick-off team, stoppage
    time and substitutions live in the tracking and the events). ``result`` stays as in the fixture.
    """
    return {**match_info, "match_id": replica_id}


def generate_replica(match_id: str, replica: int, out_dir, *, universe: dict | None = None,
                     match_info: dict | None = None, calibration_dir=None, overwrite: bool = False,
                     repo_root: Path = REPO_ROOT, period_minutes: float = 45.0) -> dict:
    """Same fixture and squads, no scripted story: Markov chains only, own seed.

    Writes ``positions.parquet``, ``events.json`` and the replica's own ``match_info.json``, so a replica
    folder is a complete synthetic match that the data pipeline reads like ``synthetic/<match_id>/``.
    """
    universe, match_info = _context(repo_root, match_id, universe, match_info)
    plan = replica_plan(match_info, universe, match_id, replica, period_minutes)
    out_dir = Path(out_dir)
    info_path = out_dir / "match_info.json"
    if info_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {info_path} (pass overwrite=True / --overwrite)")
    cal_dir = calibration_dir if calibration_dir is not None else repo_root / "calibration"
    summary = _run(plan, universe, match_info, out_dir, cal_dir, overwrite, strict=False)
    info_path.write_text(json.dumps(replica_match_info(match_info, plan.match_id), indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    summary["files"]["match_info"] = file_info(info_path)
    return summary
