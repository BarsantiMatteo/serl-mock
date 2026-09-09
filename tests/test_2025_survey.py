"""Tests for the raw 2025 SERL Observatory survey mock generator.

See src/serl_mock/generator_contextual_data.py's "Raw 2025 SERL Observatory
survey" section, grounded in the real paper questionnaire
(docs/documentation/SERL/edition09/serl_2025_survey_PaperSurveyFinalCopy.pdf)
and the master mapping's raw column codes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from src.serl_mock.generator_contextual_data import (
    build_2025_survey_dataframe as generate_2025_survey,
)
from src.serl_mock.utils import read_master_mapping
from tests._shared import TINY_CONFIG_EDITION09

REAL_MAPPING_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "reference" / "edition09" / "serl_master_mapping_edition09.csv"
)


def _load_mapping():
    return read_master_mapping(str(REAL_MAPPING_PATH))


def test_one_row_per_puprn_with_real_question_columns():
    mapping = _load_mapping()
    puprns = [f"PUPRN{i:04d}" for i in range(50)]
    df = generate_2025_survey(puprns, mapping, seed=1)

    assert len(df) == 50
    assert df["PUPRN"].nunique() == 50
    assert list(df.columns[:4]) == ["PUPRN", "Survey_wave", "Recorded_date", "Collection_method"]
    # Real question codes from the paper survey, not harmonised variable names.
    assert {"Q15_2", "Q1R1C1", "Q47", "Q48", "Q45"} <= set(df.columns)
    # thermostat_C's mapped raw code isn't a real question in this paper copy -
    # excluded rather than guessed, see _build_2025_columns()'s docstring.
    assert "thermostat_C" not in df.columns


def test_skip_logic_blanks_dependent_questions():
    mapping = _load_mapping()
    puprns = [f"PUPRN{i:04d}" for i in range(500)]
    df = generate_2025_survey(puprns, mapping, seed=7)

    # Q23 (thermal_comfort) Yes(1)/Don't know(-1) -> Q24_1 always blank.
    skip_rows = df[df["Q23"].isin([1, -1])]
    assert not skip_rows.empty
    assert skip_rows["Q24_1"].isna().all()
    # Q23 No(2) -> Q24_1 always answered.
    asked_rows = df[df["Q23"] == 2]
    assert not asked_rows.empty
    assert not asked_rows["Q24_1"].isna().any()

    # Q32 (no_cars_vans) == 0 -> Q33_1 (parking) and Q34 (no_EVs) blank.
    no_car_rows = df[df["Q32"] == 0]
    assert not no_car_rows.empty
    assert no_car_rows["Q33_1"].isna().all()
    assert no_car_rows["Q34"].isna().all()
    has_car_rows = df[df["Q32"] > 0]
    assert not has_car_rows["Q33_1"].isna().any()

    # Q34 (no_EVs) == 0 -> Q35 (EV_charge_location) blank.
    no_ev_rows = df[df["Q34"] == 0]
    if not no_ev_rows.empty:
        assert no_ev_rows["Q35"].isna().all()

    # Q40 (longterm_condition) No(2)/Prefer not to say(3) -> Q41_1 blank.
    no_condition_rows = df[df["Q40"].isin([2, 3])]
    assert not no_condition_rows.empty
    assert no_condition_rows["Q41_1"].isna().all()
    yes_condition_rows = df[df["Q40"] == 1]
    assert not yes_condition_rows.empty
    assert not yes_condition_rows["Q41_1"].isna().any()


def test_deterministic_given_same_seed():
    mapping = _load_mapping()
    puprns = ["P1", "P2", "P3"]
    df1 = generate_2025_survey(puprns, mapping, seed=42)
    df2 = generate_2025_survey(puprns, mapping, seed=42)
    pd.testing.assert_frame_equal(df1, df2)


def test_pipeline_writes_2025_survey_when_enabled(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {
        **TINY_CONFIG_EDITION09,
        "generate": {
            "hh_smart_meter": False, "daily_smart_meter": False, "rt_summary": False,
            "weather": False, "epc": False, "survey": False, "covid19_survey": False,
            "follow_up_survey": False, "harmonised_survey": False, "survey_2025": True,
            "participant_summary": False, "exporters_list": False,
        },
    }
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_path = fake_mock_root / "edition09" / "serl_2025_follow_up_survey_data_edition09.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert len(df) == TINY_CONFIG_EDITION09["n_households"]


def test_pipeline_skips_2025_survey_by_default(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {**TINY_CONFIG_EDITION09, "generate": {"weather": False}}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_path = fake_mock_root / "edition09" / "serl_2025_follow_up_survey_data_edition09.parquet"
    assert not out_path.exists()
