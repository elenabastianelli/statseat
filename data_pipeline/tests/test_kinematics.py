"""Savitzky-Golay kinematics (guidelines v4, section 7.2: R-SG-SPEED, R-SG-ACC)."""

import numpy as np
import pytest

from insidethegame_data.config import QCConfig
from insidethegame_data.kinematics import butterworth_kinematics, savgol_kinematics, step_displacement

FS = 25.0


def test_r_sg_speed_window_from_fs():
    cfg = QCConfig()
    assert cfg.savgol_window(25.0) == 13  # 0.52 s at 25 Hz (guidelines 7.2)
    assert cfg.savgol_window(10.0) == 5
    assert cfg.savgol_window(20.0) == 11
    assert cfg.savgol_window(2.0) == 3  # never shorter than polyorder + 1 (odd)


def test_r_sg_speed_straight_run_8ms():
    t = np.arange(250) / FS
    k = savgol_kinematics(8.0 * t, np.zeros_like(t), FS, 13, 2)
    assert np.allclose(k["speed"], 8.0, atol=0.05)
    assert np.allclose(k["acc"], 0.0, atol=0.05)


@pytest.mark.parametrize("acc_window", [13, 19, 25])
def test_r_sg_acc_constant_acceleration_is_recovered_with_13_19_and_25_samples(acc_window):
    t = np.arange(250) / FS
    x = 0.5 * 3.0 * t**2
    k = savgol_kinematics(x * 0.6, x * 0.8, FS, 13, 2, acc_window=acc_window)
    assert np.allclose(k["acc_tan"][5:], 3.0, atol=0.1) and np.allclose(k["speed"], 3.0 * t, atol=0.05)


def test_r_sg_acc_window_from_fs_and_short_segments_have_speed_but_no_acceleration():
    from dataclasses import replace

    assert [replace(QCConfig(), acc_window_fraction_of_fs=f).acc_window(25.0) for f in (0.5, 0.75, 1.0)] == [13, 19, 25]
    t = np.arange(20) / FS
    k = savgol_kinematics(2.0 * t, np.zeros(20), FS, 13, 2, acc_window=25)
    assert k["filtered"].all() and np.allclose(k["speed"], 2.0, atol=0.01)
    assert not k["acc_filtered"].any() and np.isnan(k["acc_tan"]).all()


def test_constant_acceleration_3ms2():
    t = np.arange(250) / FS
    x = 0.5 * 3.0 * t**2
    k = savgol_kinematics(x * 0.6, x * 0.8, FS, 13, 2)  # diagonal direction
    assert np.allclose(k["acc"], 3.0, atol=0.1)
    assert np.allclose(k["acc_tan"][5:], 3.0, atol=0.1)  # speed grows, tangential = +3
    assert np.allclose(k["speed"], 3.0 * t, atol=0.05)


def test_short_segment_not_filtered():
    k = savgol_kinematics(np.arange(5.0), np.zeros(5), FS, 13, 2)
    assert not k["filtered"].any()
    assert np.isnan(k["speed"]).all()


def test_butterworth_straight_run():
    t = np.arange(500) / FS
    k = butterworth_kinematics(8.0 * t, np.zeros_like(t), FS, 2.0, 4)
    assert np.allclose(k["speed"][50:-50], 8.0, atol=0.05)


def test_step_displacement():
    d = step_displacement(np.array([0.0, 3.0, 3.0]), np.array([0.0, 4.0, 4.0]))
    assert np.isnan(d[0]) and d[1] == 5.0 and d[2] == 0.0
