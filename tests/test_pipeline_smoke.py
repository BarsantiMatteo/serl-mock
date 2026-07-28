"""Basic smoke test that the pipeline runs end-to-end against the tiny config
and produces the expected set of files. Column/dtype/value-domain detail is
covered by the per-dataset tests and the golden-manifest test.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def test_expected_files_are_created(generated_output_dir: Path):
    expected_relative_paths = [
        "mock_internal/puprn_master.csv",
        "mock_internal/household_traits.csv",
        "serl_epc_data_edition08.csv",
        "serl_survey_data_edition08.csv",
        "serl_covid19_survey_data_edition08.csv",
        "serl_participant_summary_edition08.csv",
        "serl_2023_follow_up_survey_data_edition08.csv",
        "serl_smart_meter_rt_summary_edition08.csv",
    ]
    for rel_path in expected_relative_paths:
        assert (generated_output_dir / rel_path).exists(), f"missing {rel_path}"

    hh_dir = generated_output_dir / "serl_smart_meter_hh_edition08"
    daily_dir = generated_output_dir / "serl_smart_meter_daily_edition08"
    assert sorted(p.name for p in hh_dir.glob("*.csv")) == [
        f"serl_half_hourly_2021_{month:02d}_edition08.csv" for month in range(1, 13)
    ]
    assert sorted(p.name for p in daily_dir.glob("*.csv")) == [
        "serl_smart_meter_daily_2021_edition08.csv"
    ]


def test_shared_puprns_across_datasets(generated_output_dir: Path):
    puprn_master = pd.read_csv(generated_output_dir / "mock_internal" / "puprn_master.csv")
    puprns = set(puprn_master["PUPRN"])
    assert len(puprns) == 5

    traits = pd.read_csv(generated_output_dir / "mock_internal" / "household_traits.csv")
    assert set(traits["PUPRN"]) == puprns

    epc = pd.read_csv(generated_output_dir / "serl_epc_data_edition08.csv")
    assert set(epc["PUPRN"]) == puprns

    survey = pd.read_csv(generated_output_dir / "serl_survey_data_edition08.csv")
    assert set(survey["PUPRN"]) == puprns
