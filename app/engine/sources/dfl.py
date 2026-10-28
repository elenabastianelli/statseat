"""MATCH_SOURCE=dfl:<match_id>: real DFL/Sportec data, **local development only** (never in the app or the video).
Reuses the data pipeline (`data_pipeline/insidethegame_data`): the XML loaders (`io_dfl`), the tidy rules (`tidy`),
the Parquet cache in `data/interim/dfl/<id>/` and the committed match profile `reports/qc/<id>/match_profile.json`.
Needs the three XML files in `data/raw/dfl/` (git-ignored) and one run of `python -m insidethegame_data.qc <id>`.
Nothing derived from DFL frames is written. The event mapping is shared with SYN-001 (`provider.py`); payload team
ids are the contract's: home -> BRA, away -> CAL.
"""

from __future__ import annotations

from pathlib import Path

from ..pipeline import ROOT
from .base import MatchData, SourceNotAvailable
from .provider import match_from_pipeline, read_profile


def load_dfl(match_id: str, root: Path = ROOT) -> MatchData:
    from ..pipeline import DEFAULT_CONFIG  # noqa: F401  (pipeline on sys.path)
    from insidethegame_data.io_dfl import find_match_files, load_events, load_matchinformation, load_positions_cached
    from insidethegame_data.tidy import deduplicate, sort_rows

    raw = root / "data" / "raw" / "dfl"
    try:
        mf = find_match_files(raw, match_id)
    except FileNotFoundError as exc:
        raise SourceNotAvailable(f"dfl:{match_id}: DFL files not found in {raw} (local development only, never "
                                 f"committed): {exc}") from None
    profile = read_profile(root, match_id, f"cd data_pipeline && .venv/bin/python -m insidethegame_data.qc {match_id}")
    info = load_matchinformation(mf.matchinformation)
    ev = load_events(mf.events).events  # Delete dropped by the loader
    pos = load_positions_cached(mf.positions, root / "data" / "interim" / "dfl" / match_id)
    frames, _ = sort_rows(pos.frames)
    frames, _ = deduplicate(frames)
    return match_from_pipeline(
        f"dfl:{match_id}", info, ev, frames, profile,
        (f"DFL/Sportec {match_id} (CC-BY 4.0, local development only); payload ids mapped home->BRA, away->CAL",))
