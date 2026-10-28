"""Linesman realism report (realism.py, realism_metrics.py): hand-worked values and a tiny synthetic match (no real data)."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from scipy import stats as sstats

from insidethegame_data import calibration as cal
from insidethegame_data import qc
from insidethegame_data import realism as rl
from insidethegame_data import realism_metrics as rm

from .synthetic_itg import write_synthetic_folder

REPO = Path(__file__).resolve().parents[2]
CFG = rm.RealismConfig()


# --- KS distance against the CDF from the 21 coarse quantiles of the reference ----------------------------------

LEVELS = [p / 100 for p in rm.QUANTILE_LEVELS_PCT]  # p2.5, p5, p10, ..., p90, p95, p97.5


def test_linesman_reference_levels_are_21_without_minimum_or_maximum():
    assert rm.QUANTILE_LEVELS_PCT == (2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0,
                                      90.0, 95.0, 97.5)
    assert CFG.quantile_levels_pct == rm.QUANTILE_LEVELS_PCT
    assert rm.RealismConfig.from_dict(json.loads(json.dumps(CFG.to_dict()))) == CFG  # round trip through a JSON meta block


def test_linesman_ks_with_coarse_quantiles_equals_scipy_inside_the_stored_range():
    q = np.asarray(LEVELS)  # the quantiles of U(0, 1) at the 21 levels: the CDF is exact between p2.5 and p97.5
    sample = np.random.default_rng(7).uniform(0.1, 0.9, 300)  # seeded: test data only
    # The supremum is reached inside [p2.5, p97.5], where the coarse CDF is the true one: D is exact.
    assert rl.ks_distance(sample, q, LEVELS) == pytest.approx(sstats.kstest(sample, "uniform").statistic, abs=1e-12)


def test_linesman_ks_with_coarse_quantiles_differs_by_at_most_0_025_in_the_tails():
    # Uniform reference: the interpolation is exact between p2.5 and p97.5, so only the tails (0 below p2.5, 1 from
    # p97.5, where the true CDF is within 0.025) can change D.
    q = np.asarray(LEVELS)
    rng = np.random.default_rng(11)  # seeded: test data only
    for sample in (rng.uniform(0.0, 1.0, 400), rng.uniform(0.0, 0.05, 50), rng.uniform(0.96, 1.0, 50), np.array([0.01, 0.99])):
        d_true = sstats.kstest(sample, "uniform").statistic
        assert abs(rl.ks_distance(sample, q, LEVELS) - d_true) <= 0.025 + 1e-12


def test_linesman_ks_with_coarse_quantiles_differs_by_at_most_the_level_spacing():
    # A curved reference (exponential): between two stored levels the linear CDF can differ from the true one by up to
    # their spacing (0.05), so D does too; in the tails by up to 0.025.
    q = np.asarray(sstats.expon.ppf(LEVELS))
    rng = np.random.default_rng(13)  # seeded: test data only
    for sample in (rng.exponential(1.0, 500), rng.exponential(1.4, 300), rng.uniform(0.0, 3.0, 200)):
        d_true = sstats.kstest(sample, "expon").statistic
        assert abs(rl.ks_distance(sample, q, LEVELS) - d_true) <= 0.05 + 1e-12


def test_linesman_coarse_cdf_is_zero_below_p2_5_and_one_from_p97_5():
    q = np.linspace(10.0, 30.0, 21)
    knots = rl.reference_cdf(q, LEVELS)
    assert rl.cdf_at(9.99, knots)[0] == 0.0 and rl.cdf_at(10.0, knots, left=True)[0] == 0.0
    assert rl.cdf_at(10.0, knots)[0] == pytest.approx(0.025)  # the lower tail's mass sits on p2.5
    assert rl.cdf_at(30.0, knots, left=True)[0] == pytest.approx(0.975) and rl.cdf_at(30.0, knots)[0] == 1.0
    assert rl.cdf_at(30.01, knots)[0] == 1.0
    assert rl.cdf_at(20.0, knots)[0] == pytest.approx(0.5)  # p50
    with pytest.raises(ValueError):
        rl.reference_cdf(q, LEVELS[:-1])


def test_linesman_ks_hand_worked_values():
    q = np.asarray(LEVELS)
    assert rl.ks_distance([0.25, 0.75], q, LEVELS) == pytest.approx(0.25)  # F_n jumps by 0.5 where F = 0.25 and 0.75
    assert rl.ks_distance([2.0, 3.0], q, LEVELS) == pytest.approx(1.0)  # entirely above the reference
    assert rl.ks_distance([-1.0], q, LEVELS) == pytest.approx(1.0)  # entirely below
    assert rl.ks_distance([], q, LEVELS) is None
    assert rl.ks_distance(np.array([np.nan, 0.5]), q, LEVELS) == pytest.approx(0.5)  # NaN dropped: one value at the median
    assert rl.ks_distance([0.25, 0.75], np.linspace(0.0, 1.0, 101)) == pytest.approx(0.25)  # default: evenly spaced levels 0..1


def test_linesman_ks_handles_atoms_in_the_reference():
    zeros = np.zeros(21)  # a reference that is always 0 (e.g. goalkeeper sprints)
    assert rl.ks_distance([0.0, 0.0, 0.0], zeros, LEVELS) == 0.0
    assert rl.ks_distance([0.0, 1.0], zeros, LEVELS) == pytest.approx(0.5)  # F(0) = 1, F_n(0) = 0.5
    knots = rl.reference_cdf(zeros, LEVELS)
    assert rl.cdf_at(0.0, knots)[0] == 1.0 and rl.cdf_at(0.0, knots, left=True)[0] == 0.0
    # 30 % zeros then a uniform part: the CDF jumps at 0 (to p25, the last stored level at 0), then rises linearly.
    data = np.r_[np.zeros(30), np.linspace(1.0, 2.0, 70)]
    q = np.percentile(data, rm.QUANTILE_LEVELS_PCT)
    knots = rl.reference_cdf(q, LEVELS)
    assert rl.cdf_at(0.0, knots, left=True)[0] == 0.0 and rl.cdf_at(0.0, knots)[0] == pytest.approx(0.25)
    assert rl.ks_distance(data, q, LEVELS) <= 0.06  # the same data: D small (the atom is 0.30, stored up to p25)
    assert rl.ks_distance(np.linspace(1.0, 2.0, 70), q, LEVELS) > 0.25  # the zeros are missing


def test_linesman_quantile_of_and_noise_level():
    q = list(np.linspace(10.0, 30.0, 21))
    assert rl.quantile_of(q, 0.5, LEVELS) == pytest.approx(20.0) and rl.quantile_of(q, 0.1, LEVELS) == pytest.approx(12.0)
    assert rl.quantile_of(q, 0.01, LEVELS) == pytest.approx(10.0)  # below p2.5: clamped to the lowest stored quantile
    assert rl.quantile_of(list(np.linspace(10.0, 20.0, 101)), 0.1) == pytest.approx(11.0)  # default levels
    assert rl.quantile_of(None, 0.5) is None
    assert rl.ks_noise(100, 100, CFG) == pytest.approx(1.358 * math.sqrt(0.02))
    assert rl.ks_noise(0, 10, CFG) is None


# --- median rule and traffic light --------------------------------------------------------------------------------

def test_linesman_median_rule():
    rng = [10.0, 20.0]  # width 10: "just outside" = within 1.0
    assert rl.median_position(15.0, rng, 0.1) == "inside" and rl.median_position(10.0, rng, 0.1) == "inside"
    assert rl.median_position(20.9, rng, 0.1) == "just_outside" and rl.median_position(9.0, rng, 0.1) == "just_outside"
    assert rl.median_position(21.2, rng, 0.1) == "far_outside" and rl.median_position(8.5, rng, 0.1) == "far_outside"
    assert rl.median_position(0.0, [0.0, 0.0], 0.1) == "inside" and rl.median_position(0.1, [0.0, 0.0], 0.1) == "far_outside"
    assert rl.median_position(None, rng, 0.1) is None and rl.median_position(1.0, None, 0.1) is None


@pytest.mark.parametrize("d, position, light", [
    (0.10, "inside", "green"), (0.15, "inside", "green"), (0.16, "inside", "yellow"), (0.30, "inside", "yellow"),
    (0.31, "inside", "red"), (0.10, "just_outside", "yellow"), (0.25, "just_outside", "yellow"), (0.10, "far_outside", "red"),
    (None, "inside", "green"), (None, "just_outside", "yellow"), (None, "far_outside", "red"), (0.05, None, None),
])
def test_linesman_traffic_light(d, position, light):
    assert rl.traffic_light(d, position, CFG) == light


def test_linesman_compare_metric_range_only_has_no_ks():
    ref = {"group": "events", "name": "fouls_per90", "role_group": None, "unit": "count", "observation": "team-match",
           "comparison": "range_only", "n": 12, "n_matches": 6, "mean": 11.0, "match_median_range": [8.0, 15.0],
           "quantiles": list(np.linspace(5.0, 18.0, 21))}
    m = rl.compare_metric(ref, np.array([9.0, 10.0, 25.0]), np.array([9.0, 10.0]), CFG, LEVELS)
    assert m["synthetic"]["ks_d"] is None and m["synthetic"]["median"] == 10.0 and m["light"] == "green"
    assert m["syn_match"]["median"] == 9.5 and m["dfl"]["median"] == pytest.approx(11.5)


# --- per player-match extras -------------------------------------------------------------------------------------

def test_linesman_rolling_peak_and_effort_counts():
    fs = 25.0
    n = int(12 * 60 * fs)  # 12 minutes in one period
    period, frame = np.ones(n, dtype=int), 10000 + np.arange(n)
    speed = np.full(n, 2.0)
    speed[int(3 * 60 * fs):int(8 * 60 * fs)] = 4.0  # a 5-minute spell at 4 m/s
    valid = np.ones(n, bool)
    assert rm.rolling_peak(period, frame, speed, valid, fs, 300.0, 0.9) == pytest.approx(1200.0)
    valid[int(4 * 60 * fs):int(4.5 * 60 * fs)] = False  # 30 s not measured inside the spell (10 %): scaled to measured time
    assert rm.rolling_peak(period, frame, speed, valid, fs, 300.0, 0.9) == pytest.approx(1200.0)
    assert np.isnan(rm.rolling_peak(period[:100], frame[:100], speed[:100], valid[:100], fs, 300.0, 0.9))  # shorter than a window
    acc = np.zeros(n)
    acc[100:115] = 3.5  # 0.6 s above +3 m/s2: one acceleration
    acc[300:310] = 3.5  # 0.4 s: too short
    acc[500:515] = -4.0  # 0.6 s below -3 m/s2: one deceleration
    acc[700:715] = 3.5
    acc[707] = np.nan  # a noise sample (R-ACC-BAND) breaks the run into 0.28 s pieces
    assert rm.effort_counts(period, frame, acc, np.ones(n, bool), fs, 3.0, 0.5) == (1, 1)


def test_linesman_woodwork_distance():
    d, part = rl.woodwork_distance([52.5, 52.5, 0.0], [3.66, 0.0, 0.0], [1.0, 2.44, 0.0], 105.0, 7.32, 2.44)
    assert d[0] == pytest.approx(0.0) and part[0] == 0  # on a post
    assert d[1] == pytest.approx(0.0) and part[1] == 1  # on the crossbar
    assert d[2] == pytest.approx(math.hypot(52.5, 2.44)) and part[2] == 1  # from the centre spot the crossbar is nearer than a post
    d2, _ = rl.woodwork_distance([51.93], [3.71], [np.nan], 105.0, 7.32, 2.44)  # without height: the posts in 2-D
    assert d2[0] == pytest.approx(math.hypot(0.57, 0.05))


# --- qc folder override (synthetic match outside synthetic/<id>/) ------------------------------------------------

def test_qc_synthetic_dir_override(tmp_path):
    f = write_synthetic_folder(tmp_path / "elsewhere")
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "work" / "reports" / "qc"
    qc.run(f.match_id, repo=repo, source="synthetic", synthetic_dir=f.folder, reports_dir=out, log=lambda *_: None, acc_window_test=False)
    assert (out / f.match_id / "match_profile.json").exists()
    assert (out / "frames" / f.match_id / cal.FRAMES_VERSION / "tracking_canonical.parquet").exists()
    with pytest.raises(FileNotFoundError):  # without the override the default folder repo/synthetic/<id> is read
        qc.run(f.match_id, repo=repo, source="synthetic", reports_dir=tmp_path / "other", log=lambda *_: None, acc_window_test=False)
    with pytest.raises(SystemExit):  # the option needs --source synthetic
        qc.main([f.match_id, "--synthetic-dir", str(f.folder)])


# --- end to end on a tiny synthetic match ------------------------------------------------------------------------

def _digest(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_linesman_end_to_end_on_a_tiny_synthetic_match(tmp_path):
    # Two "matches": the fixture and a replica in synthetic/replicas/ (same content, its own id).
    f = write_synthetic_folder(tmp_path)
    r = write_synthetic_folder(tmp_path / "tmp_replica", match_id=f"{f.match_id}-r1")
    (tmp_path / "synthetic" / "replicas").mkdir(parents=True)
    shutil.move(str(r.folder), str(tmp_path / "synthetic" / "replicas" / r.match_id))
    work = tmp_path / "work"
    # The reference, from the same tiny match prepared by the pipeline (calibration CLI path, --only).
    qc.run(f.match_id, repo=tmp_path, source="synthetic", reports_dir=work / "reports" / "qc", log=lambda *_: None, acc_window_test=False)
    cal_out = tmp_path / "cal"
    ccfg = cal.CalibrationConfig(min_valid_minutes=0.1)
    cal.run([f.match_id], repo=work, out_dir=cal_out, cfg=ccfg, log=lambda *_: None, only=["realism_reference.json", "position_maps.json"])
    assert sorted(p.name for p in cal_out.glob("*.json")) == ["position_maps.json", "realism_reference.json"]  # --only
    ref_path = cal_out / "realism_reference.json"
    ref = json.loads(ref_path.read_text())
    assert ref["quantile_levels_pct"] == list(rm.QUANTILE_LEVELS_PCT) and ref["meta"]["realism_config"] == CFG.to_dict()
    assert ref["metrics"]["shots.shot_share_goal"]["quantiles"] is not None and len(ref["metrics"]["shots.shot_share_goal"]["quantiles"]) == 21

    out = tmp_path / "realism"
    res = rl.realism_report(f.match_id, 1, repo=tmp_path, work_dir=work, reference_path=ref_path, position_maps_path=cal_out / "position_maps.json",
                            out_dir=out, log=lambda *_: None)
    assert res["meta"]["synthetic_matches"] == [f.match_id, r.match_id]
    assert set(res["metrics"]) == {m.key for m in rm.metric_catalogue(ccfg)}
    assert {m["light"] for m in res["metrics"].values()} <= {"green", "yellow", "red", None}
    assert sum(res["summary"]["lights"].values()) == sum(m["light"] is not None for m in res["metrics"].values())
    # The replica is the same match: every comparison of the shots and events that both sides have is green.
    assert res["metrics"]["shots.shots_per_match"]["light"] == "green"
    assert res["metrics"]["ball_play.ball_in_play_share"]["light"] == "green"
    assert res["meta"]["same_preparation_config"] is True
    files = _digest(out)
    assert {"realism.json", "README.md"} <= set(files) and len([k for k in files if k.startswith("figures/")]) == 7
    assert json.loads((out / "realism.json").read_text()) == res
    text = (out / "realism.json").read_text() + (out / "README.md").read_text()
    assert "HOM-" not in text and "AWY-" not in text  # no person IDs
    assert "## What to improve in the generator" in (out / "README.md").read_text()
    # Deterministic: a second run (reusing the prepared matches) gives byte-identical files.
    rl.realism_report(f.match_id, 1, repo=tmp_path, work_dir=work, reference_path=ref_path, position_maps_path=cal_out / "position_maps.json",
                      out_dir=out, log=lambda *_: None)
    assert _digest(out) == files
    # Without --no-qc a missing match is prepared; with it, it fails.
    with pytest.raises(FileNotFoundError):
        rl.realism_report(f.match_id, 2, repo=tmp_path, work_dir=work, reference_path=ref_path, out_dir=tmp_path / "x", run_qc=False, log=lambda *_: None)


def test_linesman_prepares_a_regenerated_match_again(tmp_path):
    """A prepared match is reused only while its input files are the ones its preparation hashed (qc_results.json
    input_files): a regenerated file makes it stale, it is prepared again, and --no-qc refuses the stale one."""
    f = write_synthetic_folder(tmp_path)
    work = tmp_path / "work"
    quiet = lambda *_: None  # noqa: E731
    assert rl.ensure_prepared(f.match_id, f.folder, work, repo=tmp_path, log=quiet) == "prepared"
    assert rl.stale_inputs(f.match_id, f.folder, work) == []
    assert rl.ensure_prepared(f.match_id, f.folder, work, repo=tmp_path, log=quiet) == "reused"
    ev = f.folder / "events.json"
    ev.write_text(ev.read_text() + "\n")  # same events, new bytes: what a regeneration looks like to the cache
    assert rl.stale_inputs(f.match_id, f.folder, work) == ["events.json"]
    with pytest.raises(RuntimeError, match="events.json"):
        rl.ensure_prepared(f.match_id, f.folder, work, repo=tmp_path, run_qc=False, log=quiet)
    assert rl.ensure_prepared(f.match_id, f.folder, work, repo=tmp_path, log=quiet) == "prepared again"
    assert rl.stale_inputs(f.match_id, f.folder, work) == []
    assert rl.ensure_prepared(f.match_id, f.folder, work, repo=tmp_path, log=quiet) == "reused"
    # a preparation that recorded no hashes cannot prove what it was made from
    qcr = work / "reports" / "qc" / f.match_id / "qc_results.json"
    qcr.write_text(json.dumps({k: v for k, v in json.loads(qcr.read_text()).items() if k != "input_files"}))
    assert rl.stale_inputs(f.match_id, f.folder, work) == ["qc_results.json"]


def test_realism_reference_has_no_person_or_object_ids_on_a_tiny_match(tmp_path):
    f = write_synthetic_folder(tmp_path)
    work = tmp_path / "work"
    qc.run(f.match_id, repo=tmp_path, source="synthetic", reports_dir=work / "reports" / "qc", log=lambda *_: None, acc_window_test=False)
    res = cal.run([f.match_id], repo=work, out_dir=tmp_path / "cal", cfg=cal.CalibrationConfig(min_valid_minutes=0.1), log=lambda *_: None,
                  only=["realism_reference.json"])
    text = (tmp_path / "cal" / "realism_reference.json").read_text()
    assert "HOM-" not in text and "AWY-" not in text and "BALL" not in text
    ref = res["realism_reference.json"]
    body = json.dumps({k: v for k, v in ref.items() if k != "meta"})
    assert f.match_id not in body  # match ids only in meta
    # No minimum or maximum is stored: with a constant-free metric the lowest and highest stored quantiles lie strictly
    # inside the range of the values (here the ball speed samples of the match).
    assert ref["quantile_levels_pct"] == list(rm.QUANTILE_LEVELS_PCT)
    assert all(m["quantiles"] is None or len(m["quantiles"]) == 21 for m in ref["metrics"].values())
    work_md = cal.load_match(work, f.match_id)
    speeds = rm.match_samples(work_md, cal.CalibrationConfig(min_valid_minutes=0.1))["ball_play.ball_speed_live_kmh"]
    q = ref["metrics"]["ball_play.ball_speed_live_kmh"]["quantiles"]
    assert speeds.min() < q[0] and q[-1] < speeds.max()


def test_committed_realism_reference_has_no_person_or_object_ids():
    path = REPO / "calibration" / "realism_reference.json"
    if not path.exists():
        pytest.skip("calibration/realism_reference.json has not been built")
    text = path.read_text()
    for pattern in ("DFL-OBJ-", "DFL-CLU-", "DFL-COM-", "DFL-MAT-"):
        assert pattern not in text, pattern
    doc = json.loads(text)
    body = json.dumps({k: v for k, v in doc.items() if k != "meta"})
    for mid in doc["meta"]["matches_used"]:
        assert mid not in body  # match ids only in meta
    # Privacy: exactly the 21 coarse levels, no minimum (p0) and no maximum (p100).
    assert doc["quantile_levels_pct"] == list(rm.QUANTILE_LEVELS_PCT)
    assert 0.0 not in doc["quantile_levels_pct"] and 100.0 not in doc["quantile_levels_pct"]
    assert doc["meta"]["realism_config"]["quantile_levels_pct"] == list(rm.QUANTILE_LEVELS_PCT)
    for m in doc["metrics"].values():
        assert m["quantiles"] is None or len(m["quantiles"]) == 21
        assert set(m) == {"group", "name", "role_group", "unit", "observation", "comparison", "definition", "n", "n_matches", "mean",
                          "match_median_range", "quantiles"}
