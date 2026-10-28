import numpy as np

from insidethegame_data.segments import long_runs_mask, run_lengths, segment_ids


def test_run_lengths():
    s, ln = run_lengths(np.array([0, 1, 1, 0, 1, 0, 1, 1, 1], dtype=bool))
    assert s.tolist() == [1, 4, 6] and ln.tolist() == [2, 1, 3]


def test_long_runs_mask():
    m = long_runs_mask(np.array([1, 1, 0, 1, 1, 1], dtype=bool), 3)
    assert m.tolist() == [False, False, False, True, True, True]


def test_segments_break_at_framesets_gaps_nan_and_offpitch():
    fs = 25.0
    group = np.array([0, 0, 0, 0, 0, 0, 1, 1, 1])
    n = np.array([1, 2, 3, 5, 6, 7, 1, 2, 3])  # gap between 3 and 5
    t = n / fs
    x = np.array([0, 1, 2, 3, np.nan, 5, 0, 1, 2], dtype=float)
    y = np.zeros(9)
    off = np.array([0, 0, 0, 0, 0, 0, 0, 1, 1], dtype=bool)
    seg = segment_ids(group, n, t, x, y, fs, off)
    assert seg.tolist() == [0, 0, 0, 1, -1, 2, 3, 4, 4]
