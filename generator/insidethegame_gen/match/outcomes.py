"""Outcomes written after the simulation, and the checks that they match the trajectories.

The engine creates every kick event (pass, cross, throw-in, restart kick, shot) at its kick frame without a
result, and logs what physically happens to the ball (`Engine.ball_log`): touches with the player, goals, the
post, the ball leaving the pitch, fouls and offsides. `resolve_outcomes` reads the first entry after each event:

- pass (`Play`, or a restart with sub-type `Play`): next touch by a team-mate -> `successfullyCompleted` and
  `Recipient` = that team-mate; anything else (opponent, ball out, offside, foul, end of the period) ->
  `unsuccessful`, `Recipient` = the intended team-mate, as DFL;
- shot: ball in the goal -> `SuccessfulShot`; touch by the defending goalkeeper -> `SavedShot`; by another
  defender -> `BlockedShot`; post or bar -> `ShotWoodWork`; ball out of play -> `ShotWide`. A shot from a
  restart (free kick) is written as DFL nests it: `FreeKick` with sub-type `ShotAtGoal/<result>`;
- duel (`TacklingGame`): the winner is the player whose team touches the ball next (or gets the restart).

`check_outcomes` then verifies the results against the noise-free trajectories: a successful pass ends at a
team-mate's feet, a saved shot at the goalkeeper's, a goal crosses the line, a wide shot leaves over the goal line,
woodwork really hits a post, and every free-kick shot has a result. `api.py` stops before writing if any fails.
"""

from __future__ import annotations

import bisect

import numpy as np

SHOT_RESULTS = ("SuccessfulShot", "SavedShot", "ShotWide", "BlockedShot", "ShotWoodWork")
RECEPTION_MAX_M = 2.0  # ball to receiver, goalkeeper or blocker at the touch frame, noise-free (the pipeline's touch reach)
OTHER = {"home": "away", "away": "home"}


def _index(log: list[dict]) -> dict[int, tuple[list, list]]:
    by_period: dict[int, list[dict]] = {}
    for e in log:
        by_period.setdefault(e["period"], []).append(e)
    out = {}
    for p, entries in by_period.items():
        entries.sort(key=lambda e: (e["f"], e["seq"]))
        out[p] = ([e["f"] for e in entries], entries)
    return out


def _after(index, period: int, f: int, strict: bool = True):
    """Entries of the period after frame f (strictly after, or from f on)."""
    if period not in index:
        return []
    frames, entries = index[period]
    i = bisect.bisect_right(frames, f) if strict else bisect.bisect_left(frames, f)
    return entries[i:]


def shot_result(entry: dict | None, shooter_side: str) -> str:
    if entry is None:
        return "ShotWide"
    kind = entry["kind"]
    if kind == "goal":
        return "SuccessfulShot"
    if kind == "post":
        return "ShotWoodWork"
    if kind == "touch" and entry["side"] != shooter_side:
        return "SavedShot" if entry["gk"] else "BlockedShot"
    if kind == "touch":
        return "OtherShot"  # a team-mate touches first: never expected, reported by check_outcomes
    return "ShotWide"  # out of play (or nothing before the whistle)


def resolve_outcomes(engine) -> None:
    """Fill the results of the engine's events from its ball log (in place)."""
    index = _index(engine.ball_log)
    for ev in engine.events:
        res = ev.get("resolve")
        if res is None:
            continue
        a = ev["attributes"]
        if res["what"] == "pass":
            nxt = _after(index, ev["period"], ev["f"])
            first = nxt[0] if nxt else None
            ok = (first is not None and first["kind"] == "touch" and first["side"] == res["side"]
                  and first["player_id"] != res["kicker"])
            a["Evaluation"] = "successfullyCompleted" if ok else "unsuccessful"
            if ok:
                a["Recipient"] = first["player_id"]
            res["next"] = first
        elif res["what"] == "shot":
            nxt = _after(index, ev["period"], ev["f"])
            first = nxt[0] if nxt else None
            result = shot_result(first, res["side"])
            ev["sub_type"] = f"ShotAtGoal/{result}" if res["nested"] else result
            res["next"] = first
            res["result"] = result
        elif res["what"] == "duel":
            sides = res["sides"]
            winner = None
            for entry in _after(index, ev["period"], ev["f"], strict=False):
                if entry["kind"] == "touch":
                    side = entry["side"]
                elif entry["kind"] == "out":
                    side = OTHER[entry["side"]]  # the restart goes to the other team
                elif entry["kind"] == "foul":
                    side = entry["side"]
                else:
                    continue
                winner = next((p for p, s in sides.items() if s == side), None)
                res["next"] = entry
                break
            if winner is None:  # nothing happens before the whistle: keep the drawn winner
                winner = a["Winner"]
            loser = next(p for p in sides if p != winner)
            team = engine.team_id[sides[winner]]
            a["Winner"], a["Loser"], a["WinnerTeam"] = winner, loser, team
            if not res.get("loose"):
                a["WinnerRole"] = "withBall" if winner == res["with_ball"] else "withoutBall"
            if "DribbleEvaluation" in a:
                a["DribbleEvaluation"] = "successful" if winner == res["with_ball"] else "unsuccessful"
            ev["player_id"], ev["team_side"] = winner, sides[winner]
            a["Team"], a["Player"] = team, winner


def _player_xy(engine, period: int, f: int, player_id: str):
    buf = engine.buffers[period]
    code = engine.pid_index[player_id]
    slots = np.nonzero(buf.pid[f] == code)[0]
    return None if slots.size == 0 else buf.P[f, int(slots[0])]


def _distance(engine, period: int, f: int, player_id: str) -> float:
    xy = _player_xy(engine, period, f, player_id)
    if xy is None:
        return float("inf")
    b = engine.buffers[period].B[f]
    return float(np.hypot(*(xy - b[:2])))


def check_outcomes(engine) -> list[str]:
    """Problems where a written result does not match the simulated trajectory (empty = all consistent)."""
    problems = []
    hl = engine.pitch.hl
    goals = {(p, f) for (p, f, _, _) in engine.goal_frames}
    posts = {(p, f) for (p, _, f, _) in engine.woodwork_frames}
    for ev in engine.events:
        res = ev.get("resolve")
        if res is None:
            continue
        where = f"{ev['event_type']} {ev['sub_type']} by {ev['player_id']} (period {ev['period']}, frame {ev['f']})"
        first = res.get("next")
        if res["what"] == "pass" and ev["attributes"]["Evaluation"] == "successfullyCompleted":
            if first is None or first["kind"] != "touch" or first["side"] != res["side"]:
                problems.append(f"{where}: successful but the next touch is not a team-mate's")
            elif _distance(engine, ev["period"], first["f"], first["player_id"]) > RECEPTION_MAX_M:
                problems.append(f"{where}: successful but {first['player_id']} is "
                                f"{_distance(engine, ev['period'], first['f'], first['player_id']):.1f} m from the ball")
        elif res["what"] == "shot":
            result = res["result"]
            if res["nested"] and "/" not in (ev["sub_type"] or ""):
                problems.append(f"{where}: restart shot without a result")
            if result == "OtherShot":
                problems.append(f"{where}: a team-mate touches the shot first")
            elif result == "SuccessfulShot" and (ev["period"], first["f"]) not in goals:
                problems.append(f"{where}: goal without a goal-line crossing")
            elif result in ("SavedShot", "BlockedShot"):
                d = _distance(engine, ev["period"], first["f"], first["player_id"])
                if d > RECEPTION_MAX_M:
                    problems.append(f"{where}: {first['player_id']} is {d:.1f} m from the ball at the save/block")
                if result == "SavedShot" and not first["gk"]:
                    problems.append(f"{where}: saved by an outfield player")
            elif result == "ShotWoodWork" and (ev["period"], first["f"]) not in posts:
                problems.append(f"{where}: woodwork without a post contact")
            elif result == "ShotWide" and first is not None and first["kind"] == "out" and abs(first["x"]) < hl - 0.5:
                problems.append(f"{where}: wide but the ball leaves over the touchline")
    return problems


KICK_WINDOW_FRAMES = 5  # +-0.2 s at 25 Hz: the window of the pipeline's closest approaches (R-TOUCH-NEAREST)
KICK_MARGIN_M, KICK_MARGIN_AERIAL_M = 0.3, 0.5  # the pipeline's margins (guidelines v4.2, section 9.5)


def kick_ownership(engine) -> dict:
    """E-6 self-check on the noise-free tracks: every kick (pass, shot, restart kick, clearance) whose kicker is not
    the nearest player to the ball, or within the R-TOUCH-NEAREST margin of the nearest, over the live frames within
    +-0.2 s of the kick frame. Diagnostic only (the generation summary reports it)."""
    lost = []
    kicks = 0
    for ev in engine.events:
        kick = (ev.get("resolve") or {}).get("what") in ("pass", "shot") or ev["attributes"].get("DefensiveClearance")
        if not kick or ev["player_id"] is None:
            continue
        kicks += 1
        buf = engine.buffers[ev["period"]]
        f = ev["f"]
        lo, hi = max(0, f - KICK_WINDOW_FRAMES), min(buf.n - 1, f + KICK_WINDOW_FRAMES)
        live = buf.live[lo:hi + 1]
        if not live.any():
            continue
        d = np.hypot(buf.P[lo:hi + 1, :, 0] - buf.B[lo:hi + 1, None, 0], buf.P[lo:hi + 1, :, 1] - buf.B[lo:hi + 1, None, 1])
        d = np.where(live[:, None], d, np.inf).min(axis=0)
        code = engine.pid_index[ev["player_id"]]
        slots = np.nonzero(buf.pid[f] == code)[0]
        if slots.size == 0:
            continue
        k = int(slots[0])
        margin = KICK_MARGIN_AERIAL_M if buf.B[f, 2] > 1.5 else KICK_MARGIN_M
        others = np.where(np.arange(d.size) == k, np.inf, d)
        j = int(np.argmin(others))
        if d[k] > others[j] + margin:
            lost.append({"period": ev["period"], "f": f, "event": ev["event_type"], "kicker": ev["player_id"],
                         "kicker_m": round(float(d[k]), 2), "nearer": engine.object_ids[int(buf.pid[f, j])],
                         "nearer_m": round(float(others[j]), 2)})
    return {"kicks": kicks, "not_owned": lost, "made_room": len(engine.kick_room_log)}
