"""Command line of the match generator.

    cd generator
    .venv/bin/python -m insidethegame_gen.match SYN-001                 # the scripted match
    .venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6    # plus 6 result-free replicas
    .venv/bin/python -m insidethegame_gen.match SYN-001 --replicas 6 --only-replicas

Outputs: synthetic/<match_id>/{events.json, positions.parquet} and synthetic/replicas/<match_id>-r<k>/
{events.json, positions.parquet, match_info.json} (git-ignored; read by the Linesman realism report,
data_pipeline/insidethegame_data/realism.py). Existing files are never overwritten without --overwrite
(guidelines 1.9).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .api import REPO_ROOT, generate_match, generate_replica, realism_report


def _print_summary(summary: dict, report: dict, seconds: float) -> None:
    files = summary["files"]
    print(f"  {summary['match_id']}: score {summary['score']['home']}-{summary['score']['away']}, "
          f"periods {summary['periods'][1]['minutes']} + {summary['periods'][2]['minutes']} min, "
          f"generated in {seconds:.1f} s")
    print(f"    positions.parquet {files['positions']['bytes'] / 1e6:.1f} MB, events.json {files['events']['bytes'] / 1e6:.2f} MB")
    print(f"    ball in play {report['ball_in_play_pct']} %, outfield distance (full match) "
          f"{report['outfield_full_match_distance_km']}, GK {report['goalkeepers_distance_km']} km")
    print(f"    top speed {report['top_speed_kmh']}, teams {json.dumps(report['teams'])}")
    for m in summary.get("moments", []):
        print(f"    {m['id']} {m['type']:<18} planned {m['planned_minute']:>5}' actual {m['actual_minute']:>5}' "
              f"(delta {m['delta_s']:+.1f} s)")
    print(f"    calibration: {summary['calibration']['source']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m insidethegame_gen.match", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match_id", help="match id, e.g. SYN-001 (reads synthetic/<match_id>/script.json)")
    ap.add_argument("--replicas", type=int, default=0, help="number of result-free replicas to generate")
    ap.add_argument("--only-replicas", action="store_true", help="do not regenerate the scripted match")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    args = ap.parse_args(argv)
    root = args.root
    if not args.only_replicas:
        t0 = time.perf_counter()
        out = root / "synthetic" / args.match_id
        summary = generate_match(out / "script.json", None, out, overwrite=args.overwrite, repo_root=root)
        _print_summary(summary, realism_report(out), time.perf_counter() - t0)
    for k in range(1, args.replicas + 1):
        t0 = time.perf_counter()
        out = root / "synthetic" / "replicas" / f"{args.match_id}-r{k}"
        summary = generate_replica(args.match_id, k, out, overwrite=args.overwrite, repo_root=root)
        _print_summary(summary, realism_report(out), time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
