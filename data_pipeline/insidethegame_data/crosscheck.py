"""Independent cross-check of our parser against kloppy's sportec loader."""

from __future__ import annotations

import time

from .io_dfl import MatchFiles


def kloppy_crosscheck(mf: MatchFiles, tracking_limit: int = 2000) -> dict:
    """Load events (all) and the first ``tracking_limit`` tracking frames with kloppy."""
    try:
        import kloppy
        from kloppy import sportec
    except ImportError as exc:  # pragma: no cover
        return {"available": False, "error": str(exc)}
    out = {"available": True, "kloppy_version": kloppy.__version__}
    t0 = time.time()
    try:
        ev = sportec.load_event(event_data=str(mf.events), meta_data=str(mf.matchinformation))
        out["events_loaded"] = len(ev.records)
        names = [getattr(e, "event_name", None) for e in ev.records]
        out["kloppy_delete_events_kept"] = sum(n == "Delete" for n in names)
        out["kloppy_synthetic_ball_out"] = sum(n == "ball_out" for n in names)
        out["event_periods"] = len(ev.metadata.periods)
        dims = ev.metadata.pitch_dimensions
        out["event_pitch_length"] = getattr(dims, "pitch_length", None)
        out["event_pitch_width"] = getattr(dims, "pitch_width", None)
    except Exception as exc:  # noqa: BLE001 - report, do not crash QC
        out["events_error"] = f"{type(exc).__name__}: {exc}"
    try:
        tr = sportec.load_tracking(meta_data=str(mf.matchinformation), raw_data=str(mf.positions), limit=tracking_limit)
        out["tracking_frame_rate"] = tr.metadata.frame_rate
        out["tracking_frames_loaded"] = len(tr.records)
        out["tracking_periods"] = len(tr.metadata.periods)
    except Exception as exc:  # noqa: BLE001
        out["tracking_error"] = f"{type(exc).__name__}: {exc}"
    out["seconds"] = round(time.time() - t0, 1)
    return out
