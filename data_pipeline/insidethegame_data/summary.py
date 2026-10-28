"""Cross-match comparison of the preparation reports (guidelines v4.2, section 13; aggregates only).

Usage (from data_pipeline/):
    .venv/bin/python -m insidethegame_data.summary            # every reports/qc/<id>/ with a qc_results.json
    .venv/bin/python -m insidethegame_data.summary J03WMX J03WPY
    .venv/bin/python -m insidethegame_data.summary --reports-dir /tmp/scratch/qc

Writes ``<reports-dir>/SUMMARY.md`` (default ``reports/qc/SUMMARY.md``). It reads only the
aggregate files (``qc_results.json``, ``match_profile.json``), contains no wall-clock time,
and keeps the block between ``<!-- analyst-notes:start -->`` and ``<!-- analyst-notes:end -->``.
It contains the acceleration window sensitivity table (``R-ACC-WINDOW-TEST``).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from .config import GUIDELINES_VERSION
from .report import ATTRIBUTION, NOTES_END, NOTES_START, _kv, _table, review_list, score_text

REPO = Path(__file__).resolve().parents[2]
DEFAULT_NOTES = "_No analyst notes yet._"


def _load(qc_dir: Path, mid: str) -> tuple[dict, dict]:
    return json.loads((qc_dir / mid / "qc_results.json").read_text()), json.loads((qc_dir / mid / "match_profile.json").read_text())


def available_matches(qc_dir: Path) -> list[str]:
    """DFL matches prepared with the current guidelines version (synthetic matches have their own reports)."""
    out = []
    for d in sorted(Path(qc_dir).iterdir()):
        if not ((d / "qc_results.json").exists() and (d / "match_profile.json").exists()):
            continue
        r, p = _load(qc_dir, d.name)
        if str(p.get("provider", "")).startswith("DFL") and r.get("guidelines_version") == GUIDELINES_VERSION:
            out.append(d.name)
    return out


def _f(v, fmt):
    return "-" if v is None else format(v, fmt)


def match_row(r: dict, p: dict) -> dict:
    ms, cat, el = r["mask_summary"], r["catalogue"], r["time_alignment"]["event_location"]
    causes: dict[str, int] = {}
    for c in cat["by_type_and_cause"]:
        if c["anomaly_type"] == "jump":
            causes[c["suspected_cause"]] = causes.get(c["suspected_cause"], 0) + int(c["n"])
    pe = el.get("play_events", {})
    loc = pe.get("located", {})
    g = r["gaps"]
    frozen_runs = sum(g[k].get("runs", 0) for k in ("frozen", "reentry", "exit", "track_head"))
    return {
        "match": r["match_id"],
        "valid_pct": 100.0 * ms["player_valid"] / ms["player_samples"],
        "ball_valid_pct": 100.0 * ms["ball_valid"] / ms["ball_samples"],
        "jumps": cat["jumps"]["episodes"], "jump_max_m": cat["jumps"]["max_m"], "jump_causes": causes,
        "limit_episodes": sum(p_["n"] for p_ in cat["by_type_and_cause"] if p_["anomaly_type"] == "limit"),
        "overlaps": sum(p_["n"] for p_ in cat["by_type_and_cause"] if p_["anomaly_type"] == "overlap"),
        "swaps": len(r["swaps"]), "swaps_corrected": sum(1 for s in r["swaps"] if s["corrected"]),
        "frozen_runs": frozen_runs,
        "edges": [f"{e['axis']}={e['value_m']:+g} m ({e['samples']:,})" for e in p["tracking_area_edges"]["edges"]],
        "parking": [f"{v['axis']}={v['value_m']:+g} m ({v['players']} players, {v['samples']:,})" for v in p.get("parking_values", {}).get("parking_values", [])],
        "edge_runs": len(r["edge_runs"]), "parked_runs": len(r.get("parked_runs", [])),
        "gaps_estimated": g["all"].get("estimated_runs", 0), "gaps_discarded": g["all"].get("discarded_runs", 0),
        "offsets": {h: (v["event_clock_offset_s"], v["event_offset_dispersion_s"], v["search_window_s"]) for h, v in p["event_clock_offset"].items()},
        "tracking_start": {h: v["tracking_start_offset_s"] for h, v in p["kickoff_per_period"].items()},
        "subs": r["substitutions"],
        "referees_tracked": p["objects"].get("referees_tracked", False),
        "events_tz": ",".join(p["timezones"]["events"]),
        "located": {k: (v["located"], v["events"]) for k, v in loc.items()},
        "goals": pe.get("goals", {}), "shots": pe.get("shots", {}), "passes": pe.get("passes", {}), "clearances": pe.get("clearances", {}),
        "reasons": pe.get("not_confirmed_reasons", {}),
        "shot_stats": r.get("shot_statistics", {}).get("teams", {}),
        "power": el.get("shot_power_threshold", {}),
        "woodwork_contacts": pe.get("sync_log", {}).get("woodwork_contacts", 0),
        "late_windows": pe.get("sync_log", {}).get("late_annotation_windows", 0),
        "acc_band": r.get("acc_band", {}).get("by_class_and_sign", {}),
        "acc_window": r.get("acc_window_sensitivity", {}),
        "jump_context": cat.get("unknown_jump_context", {}),
        "provider_sync": el.get("provider_sync_comparison", {}),
        "peaks": r["valid_peaks"],
        "review": review_list(r),
        "formations": "; ".join(f"{k} {v['formation']}" for k, v in sorted(p["formations"].items())),
    }


def _sens_rows(rows: list[dict]) -> list[list]:
    """``R-ACC-WINDOW-TEST`` table: one row per match and window, plus the totals over all matches."""
    out, tot = [], {}
    for r in rows:
        aw = r["acc_window"]
        if not aw.get("available"):
            continue
        for w in aw["windows"]:
            e = w["exceedances"]
            out.append([r["match"], w["window_samples"], f"{e['noise']['acceleration']['runs']} / {e['noise']['deceleration']['runs']}", w["noise_samples"],
                        f"{e['approx']['acceleration']['runs']} / {e['approx']['deceleration']['runs']}", w["approx_samples"],
                        f"{_f(w['acc_p0_1_ms2'], '.2f')} / {_f(w['acc_p99_9_ms2'], '+.2f')}", f"{_f(w['acc_min_ms2'], '.2f')} / {_f(w['acc_max_ms2'], '+.2f')}",
                        f"{_f(w['diagnostic_max_speed_change_kmh']['median_abs'], '.2f')} / {_f(w['diagnostic_max_speed_change_kmh']['max_abs'], '.2f')}",
                        f"{_f(w['diagnostic_distance_change_pct']['median_abs'], '.3f')} / {_f(w['diagnostic_distance_change_pct']['max_abs'], '.3f')}"])
            t = tot.setdefault(w["window_samples"], {"na": 0, "nd": 0, "ns": 0, "aa": 0, "ad": 0, "as": 0, "p01": [], "p999": [], "mn": [], "mx": [], "dv": [], "dd": []})
            t["na"] += e["noise"]["acceleration"]["runs"]
            t["nd"] += e["noise"]["deceleration"]["runs"]
            t["ns"] += w["noise_samples"]
            t["aa"] += e["approx"]["acceleration"]["runs"]
            t["ad"] += e["approx"]["deceleration"]["runs"]
            t["as"] += w["approx_samples"]
            for k, key in (("p01", "acc_p0_1_ms2"), ("p999", "acc_p99_9_ms2"), ("mn", "acc_min_ms2"), ("mx", "acc_max_ms2")):
                if w[key] is not None:
                    t[k].append(w[key])
            t["dv"] += [abs(p["max_speed_change_kmh"]) for p in w["per_player"] if p["max_speed_change_kmh"] is not None]
            t["dd"] += [abs(p["distance_change_pct"]) for p in w["per_player"] if p["distance_change_pct"] is not None]
    for ws, t in sorted(tot.items()):
        rng = lambda v, f: f"{_f(min(v), f)} .. {_f(max(v), f)}" if v else "-"  # noqa: E731
        out.append(["**all**", ws, f"{t['na']} / {t['nd']}", t["ns"], f"{t['aa']} / {t['ad']}", t["as"],
                    f"{rng(t['p01'], '.2f')} / {rng(t['p999'], '+.2f')}",
                    f"{_f(min(t['mn']) if t['mn'] else None, '.2f')} / {_f(max(t['mx']) if t['mx'] else None, '+.2f')}",
                    f"{_f(float(np.median(t['dv'])) if t['dv'] else None, '.2f')} / {_f(max(t['dv']) if t['dv'] else None, '.2f')}",
                    f"{_f(float(np.median(t['dd'])) if t['dd'] else None, '.3f')} / {_f(max(t['dd']) if t['dd'] else None, '.3f')}"])
    return out


def write_summary(qc_dir: Path, match_ids: list[str]) -> Path:
    loaded = [_load(qc_dir, m) for m in match_ids]
    old = [m for m, (r, _) in zip(match_ids, loaded) if r.get("guidelines_version") != GUIDELINES_VERSION]
    if old:
        raise ValueError(f"prepared with another guidelines version (re-run insidethegame_data.qc first): {old}")
    rows = [match_row(r, p) for r, p in loaded]
    out = qc_dir / "SUMMARY.md"
    notes = DEFAULT_NOTES
    if out.exists():
        m = re.search(re.escape(NOTES_START) + r"\n(.*?)\n" + re.escape(NOTES_END), out.read_text(), re.S)
        if m:
            notes = m.group(1)
    L: list[str] = []
    a = L.append
    a("# Data preparation - comparison across matches\n")
    a(f"> {ATTRIBUTION}\n")
    a("Generated by `.venv/bin/python -m insidethegame_data.summary` (run from `data_pipeline/`) from the per-match aggregate files "
      f"`reports/qc/<id>/qc_results.json` and `match_profile.json`. Guidelines: `docs/DATA_ANALYSIS_GUIDELINES.md` {GUIDELINES_VERSION}. "
      "Every number is per match and discovered on that match; nothing is graded pass/warn/fail. Rule IDs say where each number comes from.\n")

    a("## 1. Events at a glance (section 9)\n")
    tab = []
    tot: dict[str, int] = {}

    def add(key, v):
        tot[key] = tot.get(key, 0) + int(v)
        return v

    for r in rows:
        g, s, p, c = r["goals"], r["shots"], r["passes"], r["clearances"]
        cb = g.get("crossings_by_result", {})
        h = s.get("headers", {})
        n_sp = add("gsp", sum(n for t, n in (g.get("annotated_goals_by_event_type") or {}).items() if t != "ShotAtGoal"))
        gbc = g.get("goals_by_confirmation", {})
        tab.append([r["match"], f"{add('g', cb.get('goal', 0))} of {add('ga', g.get('annotated_goals', 0))}" + (f" ({n_sp} set piece)" if n_sp else "")
                    + (f"; {add('ge', gbc.get('end_of_period', 0))} by the end of the period" if gbc.get("end_of_period") else ""),
                    f"{add('dg', cb.get('disallowed_goal', 0))} / {add('ug', cb.get('unconfirmed', 0))} / {add('pc', cb.get('play_continued', 0))}",
                    f"{add('sl', s.get('located', 0))} / {add('sa', s.get('annotated', 0))}", f"{add('sc', s.get('confirmed', 0))} / {s.get('annotated', 0)}",
                    f"{add('hl', h.get('located', 0))} / {add('ha', h.get('annotated', 0))} ({add('hr', h.get('located_by_aerial_signature', 0))})",
                    add("sk", s.get("soft_kicks_located", 0)), add("bf", s.get("blocked_within_0_2_s", 0)),
                    f"{add('ww', r.get('woodwork_contacts', 0))} / {add('wo', s.get('observed_outcome', {}).get('woodwork', 0))}", add("lw", r.get("late_windows", 0)),
                    f"{add('pc_', p.get('confirmed', 0))} / {add('pa', p.get('annotated', 0))}", f"{add('cc', c.get('confirmed', 0))} / {add('ca', c.get('annotated', 0))}"])
    if len(rows) > 1:
        tab.append(["**all**", f"{tot['g']} of {tot['ga']}" + (f" ({tot['gsp']} set piece)" if tot.get('gsp') else "") + (f"; {tot['ge']} by the end of the period" if tot.get("ge") else ""),
                    f"{tot['dg']} / {tot['ug']} / {tot['pc']}", f"{tot['sl']} / {tot['sa']}", f"{tot['sc']} / {tot['sa']}",
                    f"{tot['hl']} / {tot['ha']} ({tot['hr']})", tot["sk"], tot["bf"], f"{tot['ww']} / {tot['wo']}", tot["lw"], f"{tot['pc_']} / {tot['pa']}", f"{tot['cc']} / {tot['ca']}"])
    a(_table(tab, ["Match", "Goals confirmed (`R-GOAL`)", "Disallowed / unconfirmed / play continued", "Shots located", "Shots confirmed (`R-SHOT-CHECK`)",
                   "Headers located (by `R-AERIAL`)", "Soft kicks (`R-SHOT-SOFT`)", "Blocks < 0.2 s (`R-SHOT-SPLIT`)",
                   "Woodwork contacts / shots observed woodwork (`R-WOODWORK`)", "Windows extended (`R-LATE-ANNOTATION`)", "Passes confirmed (`R-PASS-KICK`)",
                   "Clearances confirmed (`R-CLEARANCE`)"]) + "\n")
    a("Goals are confirmed by the next restart (a kickoff) or, with no restart and no VAR, disallowed-goal, foul or offside event before the final whistle, "
      "by the end of the period (v4.1); a crossing followed by another restart is a disallowed goal. Annotated goals include goals scored "
      "directly from a set piece (penalty, direct free kick: DFL nests the shot in the restart event); they are matched to the crossing at the restart's kick "
      "and stay out of the open-play shot checks and of xG. Shots are confirmed by `R-SHOT-CHECK` "
      "(direction over the whole free flight, team-mate check, then an observed outcome). Passes are confirmed by their kick; their outcome is kept separately. "
      "Clearances need at least 4 of the 7 conditions.\n")

    a("### Shots: observed outcome and labels\n")
    outs = ("goal", "disallowed_goal", "saved", "blocked", "wide", "woodwork", "none")
    tab = []
    for r in rows:
        oo = r["shots"].get("observed_outcome", {})
        lab = {k: sum(v.get(k, 0) for v in r["shot_stats"].values()) for k in ("shots", "on_target", "goals", "saved", "blocked", "wide", "woodwork")}
        tab.append([r["match"]] + [oo.get(o, 0) for o in outs] + [_kv(r["reasons"].get("shot", {})),
                    f"{lab['shots']} / {lab['on_target']} ({lab['goals']} + {lab['saved']}) / {lab['woodwork']}", _f(r["power"].get("kmh"), ".1f")])
    a(_table(tab, ["Match"] + [f"Observed {o}" for o in outs] + ["Not confirmed: reasons", "Labels: shots / on target (goals + saved) / woodwork (`R-SHOT-COUNTS`)",
                                                                "Power threshold (km/h, `R-SHOT-POWER`)"]) + "\n")

    a("### Passes and clearances\n")
    a(_table([[r["match"], _kv(r["passes"].get("pass_outcome", {})), score_text(r["clearances"].get("score_distribution", {})), _kv(r["clearances"].get("condition_met", {}))] for r in rows],
             ["Match", "Pass outcome (`R-PASS-OUTCOME`)", "Clearances by conditions met", "Each condition met (C1-C7)"]) + "\n")

    a("## 2. Validity, anomalies, gaps\n")
    a(_table([[r["match"], f"{r['valid_pct']:.2f} %", f"{r['ball_valid_pct']:.1f} %", r["jumps"], f"{r['jump_max_m']:.1f}" if r["jump_max_m"] is not None else "-",
               r["swaps"], r["swaps_corrected"], r["frozen_runs"], ", ".join(r["edges"]) or "none", r["edge_runs"], ", ".join(r["parking"]) or "none", r["parked_runs"],
               f"{r['gaps_estimated']} / {r['gaps_discarded']}", r["limit_episodes"], r["overlaps"]] for r in rows],
             ["Match", "Valid player samples", "Valid ball samples", "Jump episodes", "Max jump (m)", "Mutual swaps", "Restored (`R-SWAP-RESTORE`)", "Frozen runs (>= 1 s)",
              "Tracking-area edges (`R-EDGE`)", "Edge runs", "Parking values (`R-PARKING`)", "Parked runs", "Gaps estimated / discarded", "Speed-limit episodes", "Overlap episodes"]) + "\n")
    causes = sorted({c for r in rows for c in r["jump_causes"]})
    a("**Jump episodes by suspected cause** (every one is excluded with its window, whatever the cause):\n")
    totc = {c: sum(r["jump_causes"].get(c, 0) for r in rows) for c in causes}
    all_j = sum(totc.values())
    a(_table([[r["match"]] + [r["jump_causes"].get(c, 0) for c in causes] + [sum(r["jump_causes"].values())] for r in rows]
             + [["**all**"] + [f"**{totc[c]}** ({100 * totc[c] / all_j:.0f} %)" if all_j else "0" for c in causes] + [f"**{all_j}**"]],
             ["Match"] + causes + ["Total"]) + "\n")
    a("**Unknown jumps (`R-JUMP-CONTEXT`)**, never a reason to change the exclusion:\n")
    a(_table([[r["match"], r["jump_context"].get("unknown_jumps", 0), r["jump_context"].get("in_live_play", 0), r["jump_context"].get("in_dead_ball", 0),
               r["jump_context"].get("with_events_within_window", 0), r["jump_context"].get("with_the_jumping_player_in_an_event", 0),
               _kv(dict(list(r["jump_context"].get("event_types_nearby", {}).items())[:4]))] for r in rows],
             ["Match", "Unknown jumps", "Live play", "Dead ball", "With a provider event within +-2 s", "Jumping player in the event", "Event types nearby (top 4)"]) + "\n")

    a("## 3. Acceleration band and window (`R-ACC-BAND`, `R-ACC-WINDOW-TEST`)\n")
    a("The band -8.5..+7.5 m/s2 never excludes a sample: noise (<= 0.2 s) is not used, approx (> 0.2 s) is reported with '≈'.\n")
    tab = []
    for r in rows:
        b = r["acc_band"]
        if b:
            tab.append([r["match"], f"{b['noise']['acceleration']['runs']} / {b['noise']['deceleration']['runs']}", b["noise"]["acceleration"]["samples"] + b["noise"]["deceleration"]["samples"],
                        f"{b['approx']['acceleration']['runs']} / {b['approx']['deceleration']['runs']}", b["approx"]["acceleration"]["samples"] + b["approx"]["deceleration"]["samples"]])
    a(_table(tab, ["Match", "Noise runs acc / dec", "Noise samples", "Approx runs acc / dec", "Approx samples"]) + "\n")
    a("### Acceleration window sensitivity\n")
    a("Acceleration recomputed on the same cleaned positions with windows of 13, 19 and 25 samples (speed always at the speed window). The mask, maximum speeds and "
      "distances used in results do not depend on this window. The last two columns are a **diagnostic only**: speed and distance recomputed with the same window, "
      "change per player against the speed window (median / maximum absolute change). The **all** rows give the range of the per-match p0.1 and p99.9 and the overall min / max.\n")
    sens = _sens_rows(rows)
    a((_table(sens, ["Match", "Window (samples)", "Noise runs acc / dec", "Noise samples", "Approx runs acc / dec", "Approx samples", "p0.1 / p99.9 (m/s2)",
                     "min / max (m/s2)", "Max speed change median / max (km/h)", "Distance change median / max (%)"]) if sens else "Not computed.") + "\n")

    a("## 4. Substitutions, objects, time\n")

    def med_in(subs):
        v = [s["in_invalid_s"] for s in subs if s.get("in_invalid_s") is not None]
        return float(np.median(v)) if v else None

    a(_table([[r["match"], len(r["subs"]), sum(1 for s in r["subs"] if s.get("inheritance")), sum(1 for s in r["subs"] if s.get("reacquisition_jump")),
               sum(1 for s in r["subs"] if s.get("outgoing_tracked_in_period") is False), _f(med_in(r["subs"]), ".1f"),
               "yes" if r["referees_tracked"] else "no", r["events_tz"],
               "; ".join(f"P{h}: {v:+.2f} s" for h, v in r["tracking_start"].items()),
               "; ".join(f"P{h}: {o:+.2f} s (MAD {_f(d, '.2f')}, window {w:.0f} s)" for h, (o, d, w) in r["offsets"].items()), r["formations"]] for r in rows],
             ["Match", "Substitutions", "With inheritance (`R-SUB-INHERIT`)", "With a re-acquisition jump", "Half-time (outgoing not tracked)", "Incoming invalid, median (s)",
              "Referees tracked", "Event time zone", "Tracking start after kickoff", "Event clock offset (dispersion)", "Formations"]) + "\n")

    a("## 5. Valid peaks (after the mask)\n")
    a(_table([[r["match"], f"{r['peaks']['player_speed_max_kmh']:.1f}", f"{r['peaks']['player_speed_p99_9_kmh']:.1f}",
               f"{_f(r['peaks']['player_acc_tan_min_ms2'], '.2f')} / {_f(r['peaks']['player_acc_tan_max_ms2'], '+.2f')}",
               _f(r["peaks"]["ball_live_speed_max_kmh"], ".1f"),
               (f"{r['provider_sync']['within_0_2_s']} / {r['provider_sync']['n']} (median {r['provider_sync']['abs_median_s']:.2f} s)" if r["provider_sync"].get("n") else "-")] for r in rows],
             ["Match", "Player speed max (km/h)", "p99.9 (km/h)", "Tangential acc min / max (m/s2, measured + approx)", "Live ball speed max (km/h)",
              "Located frame vs provider within 0.2 s (tests only)"]) + "\n")

    a("## 6. To review\n")
    for r in rows:
        a(f"**{r['match']}**\n")
        a(("\n".join(f"- {x}" for x in r["review"]) if r["review"] else "- nothing to review") + "\n")

    a("## 7. Analyst notes\n")
    a(NOTES_START + "\n" + notes + "\n" + NOTES_END + "\n")
    out.write_text("\n".join(L))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Compare the preparation reports of several matches (guidelines v4.2).")
    ap.add_argument("match_ids", nargs="*")
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--reports-dir", type=Path, default=None, help="folder of the per-match report folders (default reports/qc)")
    a = ap.parse_args(argv)
    qc_dir = a.reports_dir or a.repo / "reports" / "qc"
    ids = a.match_ids or available_matches(qc_dir)
    if not ids:
        raise SystemExit(f"no DFL match prepared with guidelines {GUIDELINES_VERSION} in {qc_dir}")
    print(write_summary(qc_dir, ids))


if __name__ == "__main__":
    main()
