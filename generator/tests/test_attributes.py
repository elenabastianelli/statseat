"""Tests for the attributes 1-99 (task N-4b): the mapping to physical values on small hand-made scale
tables, and the deterministic model on hand-made profiles. No real data: the tables are invented."""

from __future__ import annotations

import math

import pytest

from insidethegame_gen.attributes import (
    ATTRIBUTES,
    floors_for,
    make_attributes,
    physical_value,
    scale_table,
)


def line(lo: float, hi: float) -> dict:
    """A 99-value percentile table rising linearly from lo (p1) to hi (p99)."""
    return {"n": 20, "values": [lo + (hi - lo) * i / 98 for i in range(99)]}


@pytest.fixture()
def scales() -> dict:
    groups = {"GK": line(20, 26), "CB": line(26, 32), "FB": line(27, 33), "DM": line(10, 20), "CM": None,
              "AM": line(20, 30), "W": line(28, 34), "ST": line(27, 33)}
    return {"meta": {"source": "DFL (test)", "matches_used": ["T1"]},
            "attributes": {"pace": {"unit": "km/h", "overall": line(0, 100), "by_role_group": groups},
                           "shot_power": {"unit": "m/s", "overall": line(9, 31)}}}


# --- Mapping ---------------------------------------------------------------------------------


def test_attribute_k_is_the_kth_percentile_of_the_group(scales):
    w = scales["attributes"]["pace"]["by_role_group"]["W"]["values"]
    assert physical_value(scales, "pace", "W", 90) == pytest.approx(w[89])  # 28 + 6 * 89/98
    assert physical_value(scales, "pace", "W", 1) == pytest.approx(28.0)
    assert physical_value(scales, "pace", "W", 99) == pytest.approx(34.0)
    assert physical_value(scales, "pace", "W", 50.5) == pytest.approx((w[49] + w[50]) / 2)
    assert physical_value(scales, "pace", "W", 0) == pytest.approx(28.0)  # clipped to 1..99
    assert physical_value(scales, "pace", "W", 140) == pytest.approx(34.0)


def test_group_without_a_table_uses_the_mean_of_its_neighbours(scales):
    values, source = scale_table(scales, "pace", "CM")
    assert source == "mean of DM and AM"
    assert values[0] == pytest.approx(15.0) and values[98] == pytest.approx(25.0)  # (10 + 20) / 2, (20 + 30) / 2
    groups = scales["attributes"]["pace"]["by_role_group"]
    groups["DM"] = groups["AM"] = None
    values, source = scale_table(scales, "pace", "CM")
    assert source == "overall" and values[49] == pytest.approx(50.0)


def test_goalkeepers_never_borrow_a_table(scales):
    scales["attributes"]["pace"]["by_role_group"]["GK"] = None
    assert scale_table(scales, "pace", "GK") is None
    assert physical_value(scales, "pace", "GK", 50) is None


def test_overall_only_attribute_is_one_scale_for_every_group(scales):
    for g in ("GK", "CB", "W", "ST"):
        assert scale_table(scales, "shot_power", g)[1] == "overall"
        assert physical_value(scales, "shot_power", g, 1) == pytest.approx(9.0)


@pytest.mark.parametrize("bad", [list(range(98)), list(range(99, 0, -1)), [1.0] * 98 + [math.nan], None])
def test_a_broken_table_is_never_used(scales, bad):
    scales["attributes"]["pace"]["by_role_group"]["W"] = {"n": 20, "values": bad}
    values, source = scale_table(scales, "pace", "W")
    assert source == "mean of FB and AM"


# --- The model -------------------------------------------------------------------------------

BASE = {"seed": 2026, "player_id": "TST-07", "group": "W", "quality": 0.75, "style": ["crosser"],
        "strengths": ["crossing"], "age": 26}


def attrs(**change) -> dict[str, int]:
    return make_attributes(**{**BASE, **change})


def test_same_inputs_same_numbers_and_another_player_differs():
    assert attrs() == attrs()
    assert attrs() != attrs(player_id="TST-08")
    assert attrs() != attrs(seed=7)


@pytest.mark.parametrize("quality,age", [(0.0, 17), (1.0, 17), (0.0, 40), (1.0, 40)])
def test_every_value_is_an_integer_in_1_99(quality, age):
    out = attrs(quality=quality, age=age, strengths=["pace", "stamina", "shooting from distance"])
    assert list(out) == list(ATTRIBUTES)
    assert all(isinstance(v, int) and 1 <= v <= 99 for v in out.values())


def test_quality_raises_every_attribute():
    low, high = attrs(quality=0.5), attrs(quality=0.9)
    assert all(high[a] > low[a] for a in ATTRIBUTES)


def test_a_pacy_style_raises_pace_and_acceleration():
    plain, pacy = attrs(style=["crosser"]), attrs(style=["pacy"])
    assert pacy["pace"] > plain["pace"] and pacy["acceleration"] > plain["acceleration"]
    assert pacy["pace"] >= 70  # the style text promises a pacy winger
    assert pacy["shot_power"] == plain["shot_power"]  # nothing else moves


def test_style_effects_depend_on_the_group():
    # "inverted" means a full back who moves into midfield (passing) or a winger who cuts inside to shoot
    fb, w = attrs(group="FB", style=["inverted"], strengths=[]), attrs(group="W", style=["inverted"], strengths=[])
    base_fb, base_w = attrs(group="FB", style=[], strengths=[]), attrs(group="W", style=[], strengths=[])
    assert fb["passing"] - base_fb["passing"] > w["passing"] - base_w["passing"]
    assert w["shot_power"] > base_w["shot_power"] and fb["shot_power"] == base_fb["shot_power"]


def test_a_strength_is_never_contradicted_by_its_number():
    weak = {"quality": 0.2, "age": 38, "style": ["crosser"]}
    assert attrs(**weak, strengths=[])["pace"] < 70
    assert 70 <= attrs(**weak, strengths=["pace"])["pace"] <= 76  # floor 70 plus 0..6
    assert attrs(**weak, strengths=["work rate"])["work_rate"] >= 70


def test_pace_drops_with_age_and_passing_grows_with_experience():
    young, old = attrs(age=26), attrs(age=35)
    assert old["pace"] < young["pace"] and old["acceleration"] < young["acceleration"]
    assert attrs(age=31)["passing"] > attrs(age=22)["passing"]


def test_floors_keep_the_highest_promise_and_say_why():
    floors = floors_for("W", ["pacy"], ["recovery pace", "vision", "dribbling"])
    assert floors == {"pace": (70, 'style "pacy"'), "passing": (60, 'strength "vision"')}
    assert floors_for("W", ["crosser", "pacy"], []) == {}  # only the main style makes a promise
