"""MATCH_SOURCE=synthetic:<match_id>: our synthetic match (task S-4), read like any provider through the data pipeline.

Files in `synthetic/<match_id>/`, in the generator's format (`synthetic/SYN-001/README.md`): `match_info.json`,
`events.json` (DFL types and attribute names in English, `team_side`, set pieces nested in `sub_type`, annotated
about 1 s after the true frame) and `positions.parquet` (long tracking, one row per object per frame). The pipeline
adapter `insidethegame_data.io_synthetic` turns them into the same tables as DFL, and the committed match profile
`reports/qc/<match_id>/match_profile.json` (written by `python -m insidethegame_data.qc <match_id> --source
synthetic`) gives the transform, kick-offs and event clock offset. From there the mapping is the DFL one
(`provider.py`). The generator's `true_frame` is never used (tests of the generator only).
"""

from __future__ import annotations

from pathlib import Path

from ..pipeline import ROOT
from .base import MatchData, SourceNotAvailable
from .provider import match_from_pipeline, read_profile

FILES = ("match_info.json", "events.json", "positions.parquet")


def load_synthetic(match_id: str, root: Path = ROOT) -> MatchData:
    from ..pipeline import DEFAULT_CONFIG  # noqa: F401  (pipeline on sys.path)
    from insidethegame_data.io_synthetic import load_synthetic_match
    from insidethegame_data.tidy import deduplicate, sort_rows

    folder = root / "synthetic" / match_id
    missing = [n for n in FILES if not (folder / n).is_file()]
    if missing:
        raise SourceNotAvailable(f"synthetic:{match_id}: {', '.join(missing)} missing in {folder} (written by the "
                                 "match generator, task S-4); until then use MATCH_SOURCE=fake")
    profile = read_profile(root, match_id, f"cd data_pipeline && .venv/bin/python -m insidethegame_data.qc {match_id} "
                                           "--source synthetic")
    _, info, ev, pos, log = load_synthetic_match(folder, match_id)
    frames, _ = sort_rows(pos.frames)
    frames, _ = deduplicate(frames)
    return match_from_pipeline(f"synthetic:{match_id}", info, ev.events, frames, profile,
                               (f"synthetic match {match_id}: generator files read by the data pipeline "
                                "(io_synthetic), like any provider",), match_id=info.match_id)
