"""Tests for the MasterSERL harmonised-survey mock generator.

See src/serl_mock/generator_contextual_data.py's "MasterSERL harmonised
survey" section for the sampling logic and
data/reference/edition09/serl_master_mapping_edition09.csv for the real
variable/survey-presence mapping it's driven by.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from src.serl_mock.generator_contextual_data import (
    NOT_ASKED_SENTINEL,
    build_harmonised_survey_dataframe as generate_harmonised_survey,
)
from src.serl_mock.utils import read_master_mapping
from tests._shared import TINY_CONFIG_EDITION09

REAL_MAPPING_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "reference" / "edition09" / "serl_master_mapping_edition09.csv"
)

# A tiny synthetic mapping, independent of the real 386-variable reference
# file, so the presence/sentinel logic can be tested in isolation and fast.
_TINY_MAPPING = [
    {"master_var_name": "puprn", "domain": "Other",
     "Sign Up Survey": "PUPRN", "2023 Survey": "PUPRN", "2025 Survey": "puprn"},
    {"master_var_name": "all_three", "domain": "Energy",
     "Sign Up Survey": "A1", "2023 Survey": "B1", "2025 Survey": "Q1"},
    {"master_var_name": "sign_up_only", "domain": "Other",
     "Sign Up Survey": "A2", "2023 Survey": None, "2025 Survey": None},
    {"master_var_name": "twenty_twenty_three_only", "domain": "Other",
     "Sign Up Survey": None, "2023 Survey": "B2", "2025 Survey": None},
    {"master_var_name": "twenty_twenty_five_only", "domain": "Other",
     "Sign Up Survey": None, "2023 Survey": None, "2025 Survey": "Q2"},
]


def test_presence_drives_not_asked_sentinel():
    puprns = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    df = generate_harmonised_survey(puprns, _TINY_MAPPING, seed=1)

    # Meta columns present, in the documented order.
    assert list(df.columns[:6]) == [
        "PUPRN", "survey", "wave", "completed_survey", "date_completed", "date_completed_string",
    ]
    assert set(df.columns[6:]) == {
        "all_three", "sign_up_only", "twenty_twenty_three_only", "twenty_twenty_five_only",
    }

    sign_up_rows = df[df["survey"] == "Sign Up"]
    assert not sign_up_rows.empty
    # Asked in every survey -> never the sentinel.
    assert not (sign_up_rows["all_three"] == NOT_ASKED_SENTINEL).any()
    # Not asked in Sign Up -> always the sentinel on Sign Up rows.
    assert (sign_up_rows["twenty_twenty_three_only"] == NOT_ASKED_SENTINEL).all()
    assert (sign_up_rows["twenty_twenty_five_only"] == NOT_ASKED_SENTINEL).all()
    # Asked in Sign Up -> never the sentinel on Sign Up rows.
    assert not (sign_up_rows["sign_up_only"] == NOT_ASKED_SENTINEL).any()

    twenty_twenty_three_rows = df[df["survey"] == "2023"]
    if not twenty_twenty_three_rows.empty:
        assert (twenty_twenty_three_rows["sign_up_only"] == NOT_ASKED_SENTINEL).all()
        assert not (twenty_twenty_three_rows["twenty_twenty_three_only"] == NOT_ASKED_SENTINEL).any()


def test_every_puprn_has_a_sign_up_row():
    puprns = [f"P{i}" for i in range(30)]
    df = generate_harmonised_survey(puprns, _TINY_MAPPING, seed=7)
    sign_up_puprns = set(df.loc[df["survey"] == "Sign Up", "PUPRN"])
    assert sign_up_puprns == set(puprns)


def test_participation_is_mixed_not_uniform():
    """Not every PUPRN should take every survey occurrence (see
    _decide_participation()'s docstring) - row count per PUPRN should vary."""
    puprns = [f"P{i}" for i in range(200)]
    df = generate_harmonised_survey(puprns, _TINY_MAPPING, seed=3)
    counts = df.groupby("PUPRN").size()
    assert counts.min() >= 1
    # Up to 4: Sign Up + 2023 + both 2025 waves (the rare "both" case - see
    # _decide_participation()'s "35 in error" note in the harmonisation doc).
    assert counts.max() <= 4
    assert counts.nunique() > 1


def test_deterministic_given_same_seed():
    puprns = ["P1", "P2", "P3"]
    df1 = generate_harmonised_survey(puprns, _TINY_MAPPING, seed=42)
    df2 = generate_harmonised_survey(puprns, _TINY_MAPPING, seed=42)
    pd.testing.assert_frame_equal(df1, df2)


def test_real_master_mapping_produces_expected_shape():
    mapping = read_master_mapping(str(REAL_MAPPING_PATH))
    puprns = [f"PUPRN{i:04d}" for i in range(100)]
    df = generate_harmonised_survey(puprns, mapping, seed=42)

    assert df["PUPRN"].nunique() == 100
    assert set(df["survey"].unique()) <= {"Sign Up", "2023", "2025w_1", "2025w_2_3"}
    assert (df.groupby("PUPRN")["survey"].apply(lambda s: "Sign Up" in s.values)).all()
    # 379 harmonised variables (386 mapping rows minus 'puprn', the 5 other
    # META_VARS, and one true duplicate row - see _build_var_index()) plus
    # the 6 meta columns.
    assert df.shape[1] == 379 + 6
    # Known always-asked variable (all three surveys) is never the sentinel.
    assert not (df["heating_gas"] == NOT_ASKED_SENTINEL).any()
    # Known Sign-Up-only variable is the sentinel outside Sign Up rows.
    non_sign_up = df[df["survey"] != "Sign Up"]
    assert (non_sign_up["smart_meter_own"] == NOT_ASKED_SENTINEL).all()


def test_pipeline_writes_harmonised_survey_when_enabled(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {
        **TINY_CONFIG_EDITION09,
        "generate": {
            "hh_smart_meter": False, "daily_smart_meter": False, "rt_summary": False,
            "weather": False, "epc": False, "survey": False, "covid19_survey": False,
            "follow_up_survey": False, "harmonised_survey": True,
            "participant_summary": False, "exporters_list": False,
        },
    }
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_path = fake_mock_root / "edition09" / "masterserl_surveys_edition09.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert df["PUPRN"].nunique() == TINY_CONFIG_EDITION09["n_households"]


def test_pipeline_skips_harmonised_survey_by_default(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {**TINY_CONFIG_EDITION09, "generate": {"weather": False}}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_path = fake_mock_root / "edition09" / "masterserl_surveys_edition09.parquet"
    assert not out_path.exists()
