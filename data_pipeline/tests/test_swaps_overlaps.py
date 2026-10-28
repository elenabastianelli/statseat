"""Identity swaps (R-SWAP, R-SWAP-RESTORE) and overlaps (R-OVERLAP-CROWD) in prepare.py (guidelines v4, section 5.5)."""

import numpy as np

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.prepare import CODE, detect_mutual_swaps, jump_matrix, prepare_half

from .arrays import FS, make_half

PITCH = (100.0, 60.0)


def _crossing(n=400):
    t = np.arange(n) / FS
    ax, ay = 2 * t, np.zeros(n)
    bx, by = 20 - 2 * t, np.full(n, 0.8)
    return ax, ay, bx, by


def test_r_swap_restore_injected_swap_is_corrected_and_windowed():
    ax, ay, bx, by = _crossing()
    x = np.column_stack([ax, bx]).copy()
    y = np.column_stack([ay, by]).copy()
    x[125:200, [0, 1]] = x[125:200, [1, 0]]
    y[125:200, [0, 1]] = y[125:200, [1, 0]]  # labels wrong between 5 s and 8 s
    hr = prepare_half(make_half([x[:, 0], x[:, 1]], [y[:, 0], y[:, 1]]), [], [], PITCH, CFG)
    assert np.allclose(hr.x[:, 0], ax) and np.allclose(hr.y[:, 1], by)  # labels restored
    assert hr.swaps and hr.swaps[0]["corrected"]
    w = int(FS)
    for c in (0, 1):
        assert (hr.reason[125 - w:125 + w + 1, c] == CODE["swap"]).all()
        assert (hr.reason[200 - w:200 + w + 1, c] == CODE["swap"]).all()
        assert hr.valid[160, c]  # between the two windows the corrected track is valid


def test_r_swap_restore_unpaired_swap_is_windowed_only():
    ax, ay, bx, by = _crossing()
    x = np.column_stack([ax, bx]).copy()
    y = np.column_stack([ay, by]).copy()
    x[125:, [0, 1]] = x[125:, [1, 0]]
    y[125:, [0, 1]] = y[125:, [1, 0]]
    hr = prepare_half(make_half([x[:, 0], x[:, 1]], [y[:, 0], y[:, 1]]), [], [], PITCH, CFG)
    assert hr.swaps and not hr.swaps[0]["corrected"]
    assert (hr.reason[125, :] == CODE["swap"]).all()


def test_r_swap_mutual_swap_detector_unit():
    t = np.arange(20)
    x = np.stack([0.2 * t, 4.0 - 0.2 * t], axis=1)
    y = np.stack([np.zeros(20), np.full(20, 0.9)], axis=1)
    x[10:, [0, 1]] = x[10:, [1, 0]]
    y[10:, [0, 1]] = y[10:, [1, 0]]
    jm = jump_matrix(x, y, 0.42)
    assert detect_mutual_swaps(x, y, jm, 1.0) == [(10, 0, 1)]


def test_r_overlap_crowd_overlap_creates_window_for_both_players():
    n = 400
    t = np.arange(n) / FS
    ax = 1.0 * t
    off = np.r_[np.full(100, 3.0), np.linspace(3.0, 0.2, 75), np.full(10, 0.2), np.linspace(0.2, 3.0, 75), np.full(140, 3.0)]
    hr = prepare_half(make_half([ax, ax.copy()], [np.zeros(n), off]), [], [], PITCH, CFG)
    close = np.flatnonzero(off < CFG.overlap_distance_m)
    w = int(FS)
    for c in (0, 1):
        assert (hr.reason[close[0] - w:close[-1] + w + 1, c] == CODE["overlap"]).all()
        assert hr.valid[close[0] - w - 1, c]
    assert any(e["anomaly_type"] == "overlap" for e in hr.catalogue)


def _parallel_runners(n: int, swap_at: int, correct_at: int):
    """Two players run side by side 0.8 m apart at 0.05 m/s; their labels are swapped between two frames."""
    t = np.arange(n) / FS
    ax, ay = 0.05 * t, np.zeros(n)
    bx, by = 0.05 * t, np.full(n, 0.8)
    x = np.column_stack([ax, bx]).copy()
    y = np.column_stack([ay, by]).copy()
    x[swap_at:correct_at, [0, 1]] = x[swap_at:correct_at, [1, 0]]
    y[swap_at:correct_at, [0, 1]] = y[swap_at:correct_at, [1, 0]]
    return ax, ay, x, y


def test_r_swap_restore_correction_100_s_later_restores_the_labels():
    k = 125
    j = k + int(100 * FS)
    ax, ay, x, y = _parallel_runners(j + 200, k, j)
    hr = prepare_half(make_half([x[:, 0], x[:, 1]], [y[:, 0], y[:, 1]]), [], [], PITCH, CFG)
    assert [s["corrected"] for s in hr.swaps] == [True] and np.allclose(hr.y[:, 0], ay)
    w = int(FS)
    for c in (0, 1):
        assert (hr.reason[k - w:k + w + 1, c] == CODE["swap"]).all() and (hr.reason[j - w:j + w + 1, c] == CODE["swap"]).all()


def test_r_swap_restore_correction_130_s_later_is_windowed_only():
    k = 125
    j = k + int(130 * FS)
    ax, ay, x, y = _parallel_runners(j + 200, k, j)
    hr = prepare_half(make_half([x[:, 0], x[:, 1]], [y[:, 0], y[:, 1]]), [], [], PITCH, CFG)
    assert [s["corrected"] for s in hr.swaps] == [False, False]
    assert np.allclose(hr.y[k + 100, 0], 0.8)  # labels not restored between the two swaps
    w = int(FS)
    for c in (0, 1):
        assert (hr.reason[k - w:k + w + 1, c] == CODE["swap"]).all() and (hr.reason[j - w:j + w + 1, c] == CODE["swap"]).all()
