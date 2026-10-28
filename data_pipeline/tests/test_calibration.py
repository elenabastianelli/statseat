"""Calibration aggregates (calibration.py): hand-computed values on tiny arrays and a tiny synthetic match (no real data)."""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pytest

from insidethegame_data import calibration as cal
from insidethegame_data.qc import run as qc_run

from .synthetic_itg import write_synthetic_folder

CFG = cal.CalibrationConfig()


def test_stats_block():
    s = cal.stats([1, 2, 3, 4, 5, None, float("nan")])
    assert s["n"] == 5 and s["mean"] == 3 and s["sd"] == pytest.approx(math.sqrt(2.5))
    assert (s["p10"], s["p25"], s["p50"], s["p75"], s["p90"]) == pytest.approx((1.4, 2, 3, 4, 4.6))
    assert cal.stats([])["n"] == 0 and cal.stats([])["mean"] is None
    assert cal.stats([7.0])["sd"] is None


def test_attribute_scale_round_trip():
    t = cal.percentile_table(np.arange(0.0, 101.0), CFG.percentile_min_n)
    assert t["n"] == 101 and len(t["values"]) == 99
    assert cal.attribute_to_value(t, 50) == pytest.approx(50.0)
    assert cal.attribute_to_value(t, 150) == pytest.approx(99.0)  # clipped to 99
    assert cal.value_to_attribute(t, 72.5) == pytest.approx(72.5)
    assert cal.value_to_attribute(t, -3) == 1.0 and cal.value_to_attribute(t, 1000) == 99.0
    assert cal.percentile_table([1, 2, 3], CFG.percentile_min_n) is None  # too few values


def test_attribute_scales_v4_2_store_21_quantiles_and_interpolate_99_values_flat_in_the_tails():
    v = np.arange(0.0, 201.0)  # 0..200: the quantile at level p is 2 p
    t = cal.quantile_scale_table(v, CFG.percentile_min_n, CFG.attribute_quantile_levels_pct)
    assert t["n"] == 201 and len(t["quantiles"]) == 21 and t["quantile_levels_pct"][0] == 2.5 and t["quantile_levels_pct"][-1] == 97.5
    assert "min" not in t and "max" not in t and min(t["quantiles"]) == pytest.approx(5.0) and max(t["quantiles"]) == pytest.approx(195.0)
    assert len(t["values"]) == 99 and all(b >= a for a, b in zip(t["values"], t["values"][1:]))  # the generator needs 99 non-decreasing
    assert t["values"][0] == t["values"][1] == pytest.approx(5.0)  # attributes 1-2: flat at p2.5 (no minimum)
    assert t["values"][97] == t["values"][98] == pytest.approx(195.0)  # attributes 98-99: flat at p97.5 (no maximum)
    assert t["values"][49] == pytest.approx(100.0) and t["values"][11] == pytest.approx(24.0)  # interpolated between stored levels
    assert cal.attribute_to_value(t, 50) == pytest.approx(100.0)
    assert cal.quantile_scale_table([1, 2, 3], CFG.percentile_min_n, CFG.attribute_quantile_levels_pct) is None


def test_exact_order_statistic_levels_finds_the_quantiles_that_are_one_value():
    levels = CFG.attribute_quantile_levels_pct
    # n = 21: position p * 20 / 100 is whole for every multiple of 5, so 19 of the 21 stored quantiles are single values
    assert len(cal.exact_order_statistic_levels(21, levels)) == 19 and 2.5 not in cal.exact_order_statistic_levels(21, levels)
    assert cal.exact_order_statistic_levels(26, levels) == [20.0, 40.0, 60.0, 80.0]
    assert cal.exact_order_statistic_levels(127, levels) == [50.0]  # the median of an odd n
    assert cal.exact_order_statistic_levels(12, levels) == []  # none for n = 12
    v = np.array([3.0, 7.0, 8.0, 15.0, 21.0, 30.0, 31.0, 40.0, 52.0, 60.0, 77.0])  # n = 11, distinct
    t = cal.quantile_scale_table(v, CFG.percentile_min_n, levels)
    exact = set(cal.exact_order_statistic_levels(len(v), levels))
    for p, q in zip(t["quantile_levels_pct"], t["quantiles"]):
        assert bool(np.isclose(q, v, rtol=0.0, atol=1e-9).any()) == (p in exact)  # the helper agrees with the stored table
    assert cal.exact_order_statistic_levels(0, levels) == []


def test_shot_is_header_uses_the_r_shot_power_test():
    # the pipeline's own shot_header flag wins (bool or the CSV text), whatever the height
    assert cal.shot_is_header(True, 0.2, False) is True and cal.shot_is_header("False", 2.4, True) is False
    # without the flag: ball above aerial_min_height_m (1.5 m) at the kick
    assert cal.shot_is_header(float("nan"), 1.62, False) is True and cal.shot_is_header(None, 1.5, True) is False
    # without ball height: the annotated body part
    assert cal.shot_is_header(None, float("nan"), True) is True and cal.shot_is_header(None, float("nan"), False) is False


def test_shot_speeds_v4_2_exclude_headers_and_keep_soft_shots():
    """One function feeds ball.json shot_speed_ms, the shot_power attribute scale and the realism shot-speed metric."""
    rec = cal.EventRecords()
    rec.shots = [
        {"located": True, "confirmed": True, "header_at_kick": False, "speed": 25.0},  # kicked shot: kept
        {"located": True, "confirmed": True, "header_at_kick": False, "speed": 9.0},  # soft shot: kept
        {"located": True, "confirmed": True, "header_at_kick": True, "speed": 12.0},  # header: excluded
        {"located": True, "confirmed": False, "header_at_kick": False, "speed": 30.0},  # not confirmed: excluded
        {"located": True, "confirmed": True, "header_at_kick": False, "speed": float("nan")},  # no valid speed: excluded
        {"located": False, "head": True},  # not located: excluded
    ]
    assert cal.confirmed_shot_speeds(rec).tolist() == [25.0, 9.0]


def _xg_shots(n_shots=40000, a=-3.0, b=-0.6, seed=0):
    """Synthetic shots (test data only, seeded): visible angle and opponents in the triangle, goals drawn from
    logit = a + 2.84 theta + b n."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(3.0, 30.0, n_shots)
    y = rng.uniform(-15.0, 15.0, n_shots)
    theta = cal.visible_angle(x, y, 7.32)
    n = rng.integers(0, 4, n_shots).astype(float)
    p = 1 / (1 + np.exp(-(a + 2.84 * theta + b * n)))
    return theta, n, (rng.random(n_shots) < p).astype(float)


def test_xg_v4_2_fixed_angle_slope_recovers_a_and_a_negative_b():
    theta, n, y = _xg_shots()
    f = cal.xg_fit(theta, n, y, cal.CalibrationConfig(xg_l2=1e-6))
    assert f["b"] < 0 and f["b"] == pytest.approx(-0.6, abs=0.06) and f["a"] == pytest.approx(-3.0, abs=0.1)
    assert cal.xg_fit(theta, n, y, cal.CalibrationConfig(xg_l2=1e-6)) == f  # deterministic


def test_xg_v4_2_model_file_keeps_the_generator_format_and_the_checks_hold():
    theta, n, y = _xg_shots(n_shots=3000, seed=1)
    rec = cal.EventRecords()
    for i, (th, k, g) in enumerate(zip(theta, n, y)):
        rec.shots.append({"match": f"M{i % 6}", "outcome": "SuccessfulShot" if g else "SavedShot", "head": False, "goal": bool(g), "located": True,
                          "zone": "z", "distance_m": 10.0, "visible_angle_rad": float(th), "opponents_in_triangle": int(k), "confirmed": True,
                          "trajectory_goal": bool(g), "speed": 25.0, "invalid_opponents_near": 0, "player": "P"})
    m, _ = cal.xg_model(rec, CFG)
    assert m["features"] == ["distance_m", "visible_angle_rad", "opponents_in_triangle"]  # the generator reads features, coef, intercept
    assert m["coef"][0] == 0.0 and m["coef"][1] == 2.84 and m["coef"][2] < 0 and m["intercept"] == pytest.approx(-3.0, abs=0.3)
    assert m["penalty_xg"] == 0.79 and m["angle_slope_fixed"] == 2.84 and "Soccermatics" in m["angle_slope_source"]
    assert m["checks"]["b_negative"] and m["checks"]["six_m_central_above_penalty_spot_above_edge_of_box"]
    ex = {e["description"].split(",")[0]: e["xg"] for e in m["examples"] if e["opponents_in_triangle"] == 0}
    assert ex["6 m central (centre of the goal-area line)"] > ex["penalty spot in open play"] > ex["edge of the box"]
    assert m["leave_one_match_out"]["log_loss"] < m["leave_one_match_out"]["baseline_constant_rate_log_loss"]
    assert sum(r["n_shots"] for r in m["calibration_table"]) == 3000


def _player(minutes=10.0, speed=5.0, fs=25.0):
    n = int(minutes * 60 * fs)
    period = np.ones(n, dtype=int)
    frame = 10000 + np.arange(n)
    t = np.arange(n) / fs
    return period, frame, t, np.full(n, speed), np.zeros(n), np.ones(n, bool), n


def test_player_match_metrics_constant_run_and_splits():
    period, frame, t, sp, acc, valid, n = _player()
    live = np.arange(n) % 2 == 0
    own = np.arange(n) < n // 4
    m = cal.player_match_metrics(period, frame, t, sp, acc, valid, live, own, ~own, {1: 0.0}, 25.0, CFG)
    assert m["distance_m"] == pytest.approx(3000.0)  # 5 m/s for 600 s
    assert m["distance_live_m"] + m["distance_dead_m"] == pytest.approx(m["distance_m"])
    assert m["distance_in_poss_m"] + m["distance_out_poss_m"] == pytest.approx(m["distance_m"])
    assert m["distance_in_poss_m"] == pytest.approx(750.0)
    assert m["zones_m"] == pytest.approx([0, 0, 3000.0, 0, 0])  # 18 km/h is in the 15-20 zone
    assert m["top_speed_kmh"] == pytest.approx(18.0) and m["sprints"] == 0
    assert m["per5_m"][0] == pytest.approx(1500.0) and m["per5_m"][1] == pytest.approx(1500.0)
    assert np.isnan(m["per5_m"][2])  # the player was not on the pitch in minutes 10-15


def test_per90_uses_measured_time_only():
    period, frame, t, sp, acc, valid, n = _player()
    valid[: n // 2] = False  # half of the time not measured
    sp[: n // 2] = 50.0  # values in excluded samples must never count
    m = cal.player_match_metrics(period, frame, t, sp, acc, valid, np.ones(n, bool), np.ones(n, bool), np.zeros(n, bool), {1: 0.0}, 25.0, CFG)
    assert m["valid_minutes"] == pytest.approx(5.0)
    assert m["distance_m"] * 90.0 / m["valid_minutes"] == pytest.approx(27000.0)  # 5 m/s over 90 measured minutes
    assert m["top_speed_kmh"] == pytest.approx(18.0)


def test_sprints_need_one_second_of_contiguous_valid_samples():
    period, frame, t, sp, acc, valid, n = _player(minutes=1.0)
    sp[100:130] = 8.0  # 1.2 s above 25 km/h -> a sprint
    sp[300:320] = 8.0  # 0.8 s -> not a sprint
    sp[500:530] = 8.0  # 1.2 s but split by an invalid sample -> two runs of 0.6 s
    valid[515] = False
    m = cal.player_match_metrics(period, frame, t, sp, acc, valid, np.ones(n, bool), np.ones(n, bool), np.zeros(n, bool), {1: 0.0}, 25.0, CFG)
    assert m["sprints"] == 1
    assert cal.count_runs(np.array([True] * 30), np.r_[False, np.ones(29, bool)], 25) == 1
    assert cal.count_runs(np.array([True] * 30), np.r_[False, np.ones(14, bool), False, np.ones(14, bool)], 25) == 0


def test_visible_angle_and_triangle():
    assert cal.visible_angle(11.0, 0.0, 7.32) == pytest.approx(2 * math.atan(3.66 / 11.0))
    assert cal.visible_angle(0.5, 0.0, 7.32) > math.pi / 2  # very close: the posts span more than 90 degrees
    f = cal.shot_features(41.5, 0.0, np.array([47.0, 47.0, 40.0]), np.array([0.0, 5.0, 0.0]), 105.0, 7.32)
    assert f["opponents_in_triangle"] == 1 and f["distance_m"] == pytest.approx(11.0)
    assert cal.shot_features(41.5, 0.0, np.array([]), np.array([]), 105.0, 7.32)["opponents_in_triangle"] == 0


def test_logistic_fit_is_deterministic_and_recovers_coefficients():
    rng = np.random.default_rng(0)  # seeded: test data only
    X = rng.normal(size=(6000, 2))
    p = 1 / (1 + np.exp(-(-1.0 + 1.5 * X[:, 0] - 0.8 * X[:, 1])))
    y = (rng.random(6000) < p).astype(float)
    f1 = cal.fit_logistic_l2(X, y, l2=1e-6)
    f2 = cal.fit_logistic_l2(X, y, l2=1e-6)
    assert np.array_equal(f1["coef"], f2["coef"]) and f1["intercept"] == f2["intercept"]
    assert f1["coef"] == pytest.approx([1.5, -0.8], abs=0.12) and f1["intercept"] == pytest.approx(-1.0, abs=0.1)
    shrunk = cal.fit_logistic_l2(X, y, l2=5000.0)
    assert np.all(np.abs(shrunk["coef"]) < np.abs(f1["coef"]))


def test_xg_function_reads_the_model_file(tmp_path):
    path = tmp_path / "xg_model.json"
    path.write_text(json.dumps({"features": ["distance_m", "visible_angle_rad", "opponents_in_triangle"], "coef": [-0.1, 1.0, -0.5], "intercept": -1.0}))
    z = -1.0 - 0.1 * 11.0 + 1.0 * 0.6 - 0.5 * 2
    assert cal.xg(11.0, 0.6, 2, model_path=path) == pytest.approx(1 / (1 + math.exp(-z)))
    assert cal.xg(6.0, 1.1, 0, model_path=path) > cal.xg(25.0, 0.3, 1, model_path=path)


def _cell(cfg, bx, by):
    ix = int(np.floor((bx + 52.5) / 105 * cfg.pos_grid_nx))
    iy = int(np.floor((by + 34) / 68 * cfg.pos_grid_ny))
    return ix, iy


def test_position_maps_mirror_pooling_and_group_fallback():
    acc = cal.PositionAccumulator(CFG)
    k = 60
    z = np.zeros(k, dtype=int)
    one = np.ones(k)
    # A right back at y = -20 with the ball on the right (y = -10) -> pooled into LB at y = +20, ball y = +10.
    acc.add("RB", z, -10 * one, -20 * one, 0 * one, -10 * one)
    # A left wing back in two ball cells (baseline (-5, 25)): the FB group shift in cell A is (+5, 0).
    acc.add("LWB", z, 0 * one, 25 * one, 20 * one, 0 * one)
    acc.add("LWB", z, -10 * one, 25 * one, -20 * one, 0 * one)
    # A centre back slightly left of centre: pooled with its mirror -> symmetric map, baseline y = 0.
    acc.add("CB", z, -15 * one, 3 * one, 0 * one, 10 * one)
    maps = acc.maps()["maps"]
    lb = maps["LB"]["in_possession"]
    ix, iy = _cell(CFG, 0, 10)
    assert lb["source"][ix][iy] == "role" and lb["mean_y"][ix][iy] == pytest.approx(20.0) and lb["count"][ix][iy] == k
    rb = maps["RB"]["in_possession"]
    assert rb["mean_y"][ix][CFG.pos_grid_ny - 1 - iy] == pytest.approx(-20.0) and rb["baseline"] == pytest.approx([-10.0, -20.0])
    ax, ay = _cell(CFG, 20, 0)
    assert lb["source"][ax][ay] == "group"
    assert (lb["mean_x"][ax][ay], lb["mean_y"][ax][ay]) == pytest.approx((-10 + 5, 20 + 0))  # LB baseline + FB group shift
    far = _cell(CFG, -50, -30)
    assert lb["source"][far[0]][far[1]] == "baseline" and lb["mean_y"][far[0]][far[1]] == pytest.approx(20.0)
    cb = maps["CB"]["in_possession"]
    cx, cy = _cell(CFG, 0, 10)
    assert cb["baseline"][1] == pytest.approx(0.0)
    assert cb["mean_y"][cx][cy] == pytest.approx(3.0) and cb["mean_y"][cx][CFG.pos_grid_ny - 1 - cy] == pytest.approx(-3.0)
    # CM has no sample: built from its group (here: nothing) -> baseline cells only, never an error.
    assert set(np.asarray(maps["CM"]["in_possession"]["source"]).ravel()) <= {"group", "baseline"}


def test_dumps_is_stable_and_compact():
    obj = {"a": [1.234567, 2.0, None], "grid": [[1, 2], [3, 4]], "s": ["role", "group"], "neg0": -0.0}
    s1, s2 = cal.dumps(obj), cal.dumps(obj)
    assert s1 == s2 and '"a": [1.2346, 2.0, null]' in s1 and '"s": ["role", "group"]' in s1 and "[1, 2]" in s1
    assert '"neg0": 0.0' in s1


def test_calibration_end_to_end_on_a_tiny_synthetic_match(tmp_path):
    f = write_synthetic_folder(tmp_path)
    qc_run(f.match_id, repo=f.repo, source="synthetic", log=lambda *_: None)
    # Synthetic matches are never calibration input by default.
    assert cal.available_matches(f.repo) == []
    cfg = cal.CalibrationConfig(min_valid_minutes=0.1)
    out = tmp_path / "cal"
    res = cal.run([f.match_id], repo=f.repo, out_dir=out, cfg=cfg, log=lambda *_: None)
    assert sorted(p.name for p in out.glob("*.json")) == sorted(cal.FILES)
    h1 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("*.json")}
    cal.run([f.match_id], repo=f.repo, out_dir=out, cfg=cfg, log=lambda *_: None)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("*.json")} == h1  # byte-identical
    for name in cal.FILES:
        text = (out / name).read_text()
        assert "HOM-" not in text and "AWY-" not in text  # no person IDs
        doc = json.loads(text)
        meta = doc["meta"]
        for k in ("source", "matches_used", "config_hash", "input_hashes", "library_versions", "pipeline_version", "notes"):
            assert k in meta, (name, k)
        assert "created_utc" not in meta and "10.1038/s41597-025-04505-y" in meta["source"]
    pp = res["physical_profiles.json"]["role_groups"]
    assert set(pp) == set(cal.GROUPS)
    assert set(pp["ST"]) >= {"n_player_matches", "distance_per90_m", "distance_live_per90_m", "distance_dead_per90_m", "distance_in_poss_per90_m",
                             "distance_out_poss_per90_m", "speed_zone_per90_m", "top_speed_kmh", "sprints_per90", "acc_peak_ms2", "distance_per5min_m"}
    assert len(pp["ST"]["distance_per5min_m"]) == 18 and set(pp["ST"]["speed_zone_per90_m"]) == {"0-7", "7-15", "15-20", "20-25", "25+"}
    pm = res["position_maps.json"]
    assert set(pm["maps"]) == set(cal.ROLES) and set(pm["grid"]) >= {"x_edges", "y_edges"}
    assert np.shape(pm["maps"]["LB"]["out_of_possession"]["mean_x"]) == (CFG.pos_grid_nx, CFG.pos_grid_ny)
    em = res["event_model.json"]
    for k in ("per90_per_team", "zones", "pass", "transitions", "shot", "set_pieces_per90_per_team", "possession", "ball_in_play_share",
              "fouls_per90_per_team", "cautions_per90_per_team"):
        assert k in em, k
    assert set(res["ball.json"]) >= {"pass_speed_ms", "long_pass_speed_ms", "shot_speed_ms", "circulation_speed_ms", "air_time_high_pass_s"}
    assert "p99" in res["ball.json"]["pass_speed_ms"]  # read by R-SHOT-POWER
    assert res["xg_model.json"]["model"] == "not fitted"  # one shot is not a model
    assert set(res["attribute_scales.json"]["attributes"]) == {"pace", "acceleration", "stamina", "work_rate", "passing", "shot_power"}
    ap = res["artefact_profile.json"]
    # R-ACC-BAND (v4): acceleration spikes per match and pooled; the limit field keeps true limit episodes only.
    assert set(ap["acc_band"]) == {"rule", "settings", "per_match", "pooled"} and list(ap["acc_band"]["per_match"]) == [f.match_id]
    assert set(ap["acc_band"]["pooled"]) == {"player_hours", "noise", "approx"}
    assert {"limit_episodes_per_player_hour", "ball_limit_episodes", "acc_band_noise_acceleration_runs_per_player_hour",
            "acc_band_approx_deceleration_mean_run_samples"} <= set(ap["per_match_stats"])


def test_r_shot_power_threshold_is_read_from_ball_json_p99_or_falls_back_to_the_config(tmp_path):
    from insidethegame_data.config import DEFAULT_CONFIG
    from insidethegame_data.qc import pass_speed_p99

    assert pass_speed_p99(tmp_path, DEFAULT_CONFIG) == (94.86, "configuration fallback pass_speed_p99_kmh (calibration/ball.json has no pass_speed_ms.p99)")
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "ball.json").write_text(json.dumps({"pass_speed_ms": {"p90": 22.0}}))
    assert pass_speed_p99(tmp_path, DEFAULT_CONFIG)[0] == 94.86  # the v3 calibration has no p99
    (tmp_path / "calibration" / "ball.json").write_text(json.dumps({"pass_speed_ms": {"p90": 22.0, "p99": 26.5}}))
    assert pass_speed_p99(tmp_path, DEFAULT_CONFIG) == (95.4, "calibration/ball.json pass_speed_ms.p99")


def test_ball_model_writes_the_99th_percentile_of_pass_speeds():
    rec = cal.EventRecords()
    for i in range(1, 101):
        rec.passes.append({"match": "M", "located": True, "high_annotated": False, "evaluation": None, "zone": None, "length": 10.0, "success": True,
                           "angle": 0.0, "speed": float(i), "air": np.nan, "airborne": None, "side": "home"})
    out = cal.ball_model(rec, CFG)
    assert out["pass_speed_ms"]["p99"] == pytest.approx(np.percentile(np.arange(1, 101), 99))


def _band_match(match_id: str, player_samples: int, by_class_and_sign: dict, fs: float = 25.0):
    from types import SimpleNamespace

    return SimpleNamespace(match_id=match_id, fs=fs, qc_results={
        "mask_summary": {"player_samples": player_samples},
        "config": {"player_acc_min_ms2": -8.5, "player_acc_max_ms2": 7.5, "acc_noise_max_s": 0.2, "acc_window_samples": 13},
        "acc_band": {"by_class_and_sign": by_class_and_sign}})


def test_r_acc_band_artefact_profile_runs_per_player_hour_by_class_and_sign_per_match_and_pooled():
    # Match A: 90,000 player samples at 25 Hz = 1 player-hour; match B: 2 player-hours.
    a = _band_match("A", 90_000, {"noise": {"acceleration": {"runs": 3, "samples": 9}, "deceleration": {"runs": 1, "samples": 5}},
                                  "approx": {"acceleration": {"runs": 0, "samples": 0}, "deceleration": {"runs": 2, "samples": 14}}})
    b = _band_match("B", 180_000, {"noise": {"acceleration": {"runs": 1, "samples": 3}, "deceleration": {"runs": 3, "samples": 12}},
                                   "approx": {"acceleration": {"runs": 2, "samples": 13}, "deceleration": {"runs": 0, "samples": 0}}})
    out = cal.acc_band_profile([a, b])
    assert out["settings"] == {"band_ms2": [-8.5, 7.5], "noise_max_s": 0.2, "noise_max_samples": 5, "acc_window_samples": 13, "fs_hz": 25.0}
    pa = out["per_match"]["A"]
    assert pa["player_hours"] == pytest.approx(1.0)
    assert pa["noise"]["acceleration"] == {"runs": 3, "samples": 9, "runs_per_player_hour": pytest.approx(3.0), "mean_run_samples": pytest.approx(3.0)}
    assert pa["approx"]["acceleration"]["mean_run_samples"] is None  # no run: no mean length
    assert pa["noise"]["all"]["runs"] == 4 and pa["noise"]["all"]["mean_run_samples"] == pytest.approx(3.5)
    assert out["per_match"]["B"]["noise"]["deceleration"]["runs_per_player_hour"] == pytest.approx(1.5)
    pooled = out["pooled"]
    assert pooled["player_hours"] == pytest.approx(3.0)
    # Pooled = total runs / total player-hours; mean length = total samples / total runs.
    assert pooled["noise"]["acceleration"]["runs_per_player_hour"] == pytest.approx(4 / 3)
    assert pooled["noise"]["deceleration"]["mean_run_samples"] == pytest.approx(17 / 4)
    assert pooled["approx"]["acceleration"]["runs"] == 2 and pooled["approx"]["deceleration"]["mean_run_samples"] == pytest.approx(7.0)
    assert pooled["approx"]["all"]["runs_per_player_hour"] == pytest.approx(4 / 3)
    # A v3 report has no acc_band block: it contributes nothing, and with no v4 report the block is empty.
    v3 = _band_match("C", 90_000, {})
    del v3.qc_results["acc_band"]
    assert cal.acc_band_profile([v3]) == {} and cal.acc_band_profile([a, v3])["pooled"]["player_hours"] == pytest.approx(1.0)
