"""Bridge to the deterministic data pipeline (`data_pipeline/insidethegame_data`, guidelines v3).

The engine imports the pipeline code instead of copying it. `data_pipeline/` is a plain folder with its own venv, not
an installed package, so this module appends it to `sys.path` once (lowest priority, nothing is shadowed). Anything
that runs the engine (the app venv, the Docker image of D-5) needs the `data_pipeline/` folder next to `app/`; the
libraries it needs are pinned in `app/requirements.txt` with the same versions as `data_pipeline/requirements.txt`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_PIPELINE = ROOT / "data_pipeline"
if str(DATA_PIPELINE) not in sys.path:
    sys.path.append(str(DATA_PIPELINE))

from insidethegame_data import metrics  # noqa: E402  (the E-4 match metrics: xG, sprints, intensity, momentum...)
from insidethegame_data.config import DEFAULT_CONFIG, PrepConfig  # noqa: E402
from insidethegame_data.edges import discover_edges  # noqa: E402
from insidethegame_data.events_sync import SyncPeriod, event_kind  # noqa: E402
from insidethegame_data.mapping import map_position  # noqa: E402
from insidethegame_data.metrics import player_summary  # noqa: E402
from insidethegame_data.prepare import CODE, HalfData, HalfResult, prepare_half  # noqa: E402
from insidethegame_data.tables import build_half_arrays  # noqa: E402
from insidethegame_data.touches import ball_kinematics, touch_table  # noqa: E402

__all__ = ["ROOT", "DEFAULT_CONFIG", "PrepConfig", "discover_edges", "map_position", "metrics", "player_summary",
           "HalfData", "HalfResult", "prepare_half", "build_half_arrays", "SyncPeriod", "event_kind", "CODE",
           "ball_kinematics", "touch_table"]
