"""R-SORT, R-DEDUP, R-RESAMPLE (guidelines v4, section 5.1): tidy.py."""

import numpy as np
import pandas as pd

from insidethegame_data.tidy import deduplicate, resample_if_irregular, resample_track, sampling_irregularity, sort_rows

T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _frames(n, x, t=None, obj="P1", fsi=0):
    n = np.asarray(n)
    t = n * 0.04 if t is None else np.asarray(t, float)
    return pd.DataFrame({"frameset_idx": fsi, "half": 1, "object_id": obj, "team_id": "T", "is_ball": False, "n": n,
                         "t_utc": T0 + pd.to_timedelta(t, unit="s"), "x": np.asarray(x, float), "y": 0.0, "z": np.nan,
                         "d_m": np.nan, "s_ms": np.nan, "a_ms2": np.nan, "minute": 1.0, "ball_possession": np.nan, "ball_status": np.nan})


def test_r_sort_unsorted_input_is_sorted():
    f = _frames([1, 2, 4, 3, 5], [0.1, 0.2, 0.4, 0.3, 0.5])
    out, log = sort_rows(f)
    assert out["n"].tolist() == [1, 2, 3, 4, 5] and log["count"] == 1


def test_r_dedup_duplicates_keep_the_one_closest_to_neighbours():
    f = _frames([1, 2, 2, 3], [0.0, 5.0, 0.1, 0.2])  # the duplicate at N=2 differs; 0.1 fits the neighbours
    f, _ = sort_rows(f)
    out, log = deduplicate(f)
    assert log["count"] == 1
    assert out["n"].tolist() == [1, 2, 3] and out.loc[out["n"] == 2, "x"].item() == 0.1


def test_r_dedup_identical_duplicates_keep_first():
    f = _frames([1, 2, 2, 3], [0.0, 0.1, 0.1, 0.2])
    out, log = deduplicate(f)
    assert len(out) == 3 and log["count"] == 1


def test_r_resample_irregular_track():
    t = np.array([0.0, 0.04, 0.09, 0.12, 0.16, 0.40])  # one late sample and a long hole
    grid, res, gaps = resample_track(t, {"x": t * 10}, 25.0)
    assert len(grid) == 11 and np.allclose(grid, np.arange(11) * 0.04)
    assert np.allclose(res["x"][:5], grid[:5] * 10)
    assert np.isnan(res["x"][6:10]).all() and gaps == 5


def test_r_resample_regular_input_is_not_resampled():
    f = _frames(np.arange(1, 51), np.arange(50) * 0.1)
    assert sampling_irregularity(f, 0.05)[1]["n_irregular"] == 0
    out, log = resample_if_irregular(f, {1: 25.0}, 0.05)
    assert out is f and log["count"] == 0


def test_r_resample_irregular_input_is_resampled_to_the_grid():
    t = np.arange(50) * 0.04
    t[10] += 0.01
    f = _frames(np.arange(50), t * 2.0, t=t)
    out, log = resample_if_irregular(f, {1: 25.0}, 0.05)
    assert log["count"] > 0
    dt = np.diff((out["t_utc"] - T0).dt.total_seconds())
    assert np.allclose(dt, 0.04)
