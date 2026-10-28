"""English mapping (section 16), format detection and unit inference (section 2)."""

import numpy as np
import pandas as pd

from insidethegame_data.formats import detect_format, infer_coordinate_units, infer_speed_units, load_generic_csv
from insidethegame_data.mapping import (english_lineup, event_type_counts, map_event_type, map_formation, map_position, map_shot_outcome, map_value,
                                        snake_case)


def test_r_map_en_v4_2_event_types_outside_the_23_type_list_are_mapped():
    extra = ("GoalDisallowed", "BallDeflection", "OtherPlayerAction", "PlayerNotSentOff", "PossessionLossBeforeGoal", "Run", "SitterPrevented")
    for code in extra:
        r = map_event_type(code)
        assert not r["name"].startswith("UNMAPPED_") and r["dfl_list"] is False, code
    assert map_event_type("GoalDisallowed")["name"] == "goal_disallowed"
    assert map_event_type("ShotAtGoal") == {"source_code": "ShotAtGoal", "name": "shot_at_goal", "dfl_list": True}
    assert map_event_type("Nonsense")["name"] == "UNMAPPED_Nonsense" and map_event_type(None)["name"] == "UNMAPPED_NONE"
    c = event_type_counts(["Play", "Play", "GoalDisallowed", "Run", "Nonsense"])
    assert c["by_type"]["play"] == 2 and c["outside_dfl_list"] == {"goal_disallowed": 1, "run": 1} and c["unmapped"] == {"Nonsense": 1}


def test_german_codes_mapped_to_english():
    assert map_position("TW")["role"] == "GK"
    assert map_position("HL")["role"] == "LCM" and map_position("HR")["role_group"] == "CM"
    assert map_position("DLM")["role"] == "LWB" and map_position("DRM")["role_group"] == "FB"
    assert map_position("IVZ")["role"] == "CB"
    assert map_position("XYZ")["role"] == "UNMAPPED_XYZ" and map_position("XYZ")["source_code"] == "XYZ"
    assert map_position(None)["role"] == "UNMAPPED_NONE"
    assert map_position("LWB")["role"] == "LWB" and map_position("RCM")["role_group"] == "CM"  # English codes accepted


def test_formations_and_values():
    assert map_formation("4-4-2 Double-6 offensive Outliner")["formation"] == "4-4-2 (double pivot, attacking wide midfielders)"
    assert map_formation("3-3-2-2")["formation"] == "3-3-2-2"
    assert map_formation("Raute")["formation"] == "UNMAPPED_Raute"
    assert map_value("Deutschland") == "Germany" and map_value("Heimtrikot") == "home kit"
    assert snake_case("ShotAtGoal") == "shot_at_goal"


def test_lineup_flags_unmapped():
    lu = pd.DataFrame({"person_id": ["a", "b"], "playing_position": ["STZ", None]})
    out = english_lineup(lu)
    assert out["role"].tolist() == ["ST", "UNMAPPED_NONE"] and out["unmapped"].tolist() == [False, True]


def test_unit_inference():
    assert infer_coordinate_units(np.array([-52.5, 52.5]), np.array([-34, 34]))["units"] == "m"
    assert infer_coordinate_units(np.array([0, 10500]), np.array([0, 6800]))["scale_to_m"] == 0.01
    assert infer_coordinate_units(np.array([0, 1.0]), np.array([0, 1.0]))["units"] == "normalised"
    assert infer_speed_units(33.6)["units"] == "km/h" and infer_speed_units(9.3)["units"] == "m/s"


def test_detect_format(tmp_path):
    xml = tmp_path / "p.xml"
    xml.write_text('<?xml version="1.0"?><PutDataRequest><Positions></Positions></PutDataRequest>')
    assert detect_format(xml)["provider"] == "DFL/Sportec" and detect_format(xml)["content"] == "positions"
    csv = tmp_path / "t.csv"
    csv.write_text("period,frame,time_s,object_id,team,x,y\n1,1,0.0,a,H,10,5\n")
    assert detect_format(csv)["adapter"] == "generic_csv"


def test_second_format_loads_into_same_schema(tmp_path, synth):
    csv = tmp_path / "t.csv"
    rows = ["period,frame,time_s,object_id,team,x,y,ball_status"]
    for k in range(5):
        rows.append(f"1,{k},{k * 0.04:.2f},p1,H,{1000 + k * 10},{500}, ")
        rows.append(f"1,{k},{k * 0.04:.2f},ball,BALL,{5000},{3400},live")
    csv.write_text("\n".join(rows) + "\n")
    df = load_generic_csv(csv)
    assert list(df.columns) == list(synth["pos"].frames.columns)
    assert df.attrs["units"]["units"] == "cm" and abs(df["x"].max() - 50.0) < 1e-9
    assert df.loc[df["is_ball"], "ball_status"].eq(1.0).all()


def test_r_map_en_dfl_shot_outcomes_are_mapped_to_english():
    got = {c: (map_shot_outcome(c)["outcome"], map_shot_outcome(c)["on_target"]) for c in
           ("SuccessfulShot", "SavedShot", "BlockedShot", "ShotWide", "ShotWoodWork", "OtherShot")}
    assert got == {"SuccessfulShot": ("goal", True), "SavedShot": ("saved", True), "BlockedShot": ("blocked", False), "ShotWide": ("wide", False),
                   "ShotWoodWork": ("woodwork", False), "OtherShot": ("wide", False)}
    assert map_shot_outcome("saved")["outcome"] == "saved"  # synthetic data is English natively
    assert map_shot_outcome("")["outcome"] == "UNMAPPED_NONE" and map_shot_outcome("Mystery")["outcome"] == "UNMAPPED_Mystery"
