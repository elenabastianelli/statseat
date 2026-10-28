import numpy as np
import pandas as pd

from insidethegame_data.profile import fit_event_transform, sampling_stats

from .synthetic import FS, L, N0, W, trajectories


def test_delete_events_dropped(synth):
    ev = synth["ev"]
    assert ev.n_delete == 1
    assert "Delete" not in set(ev.events["event_type"])
    assert len(ev.events) == ev.n_raw - 1


def test_events_sorted_and_inversions_counted(synth):
    ev = synth["ev"]
    assert ev.n_out_of_order_in_file >= 1
    assert ev.events["time_utc"].is_monotonic_increasing
    assert ev.timezone_offsets == ["+02:00"]


def test_units_are_si(synth):
    f = synth["pos"].frames
    p = f[(f["object_id"] == "P-H3") & (f["half"] == 1)].sort_values("n")
    x, y = trajectories(1)[1]["P-H3"]
    step = np.hypot(np.diff(x), np.diff(y))  # unrounded steps used to write D (cm) and S (km/h)
    assert np.allclose(p["d_m"].to_numpy()[1:], step, atol=1e-4)
    assert np.allclose(p["s_ms"].to_numpy()[1:], step * FS, atol=0.005)


def test_profile_other_pitch_and_corner_origin(synth):
    pr = synth["profile"]
    assert pr["pitch_length_m"] == L and pr["pitch_width_m"] == W
    assert pr["tracking_origin"] == "corner"
    assert pr["centre_spot_in_tracking"] == [L / 2, W / 2]
    assert pr["event_origin"] == "corner"
    assert abs(float(pr["fs_hz"]["1"]) - FS) < 1e-6
    assert pr["frame_ranges"]["per_half"]["1"]["n_first"] == N0[1]
    assert pr["frame_ranges"]["per_half"]["2"]["n_first"] == N0[2]
    assert pr["delete_events"] == 1
    assert pr["timezones"]["tracking"] == ["+00:00"]


def test_direction_per_half(synth):
    d = synth["profile"]["direction_per_half"]
    assert d["1"]["team_left"] == "home" and d["1"]["confirmed"]
    assert d["2"]["team_left"] == "away" and d["2"]["confirmed"]


def test_transform_identity_and_kickoff(synth):
    tr = synth["profile"]["event_to_tracking_transform"]
    assert tr["best"]["sx"] == 1 and tr["best"]["sy"] == 1
    assert tr["best"]["median_dist_m"] < 0.05
    for k in synth["profile"]["kickoff_frames"]:
        assert k["ball_dist_first_live_m"] < 0.1


def test_transform_detects_flipped_y(synth):
    ev = synth["ev"].events.copy()
    ev["y"] = W - ev["y"]
    tr = fit_event_transform(ev, synth["has"], (L / 2, W / 2), (L / 2, W / 2), FS, 1.0, ("Play",))
    assert tr["best"]["sy"] == -1 and tr["best"]["sx"] == 1


def test_sampling_stats_other_rate():
    t0 = pd.Timestamp("2024-01-01T00:00:00Z")
    n = np.arange(100)
    frames = pd.DataFrame({"frameset_idx": 0, "half": 1, "n": n, "t_utc": t0 + pd.to_timedelta(n * 0.1, unit="s")})
    s = sampling_stats(frames, 0.05)[1]
    assert abs(s["fs_hz"] - 10.0) < 1e-6 and s["pct_dt_outside_tol"] == 0.0
