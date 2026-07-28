"""Proves the layout abstraction (Phase 1 of docs/notes/edition_multiformat_plan.md)
works end-to-end for a non-default layout, not just that the default "monthly"
layout is unchanged (that's already covered by test_golden_manifest.py).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIG


@pytest.fixture(scope="module")
def single_file_output_dir(tmp_path_factory) -> Path:
    config = {**TINY_CONFIG, "layout": {"hh_smart_meter": "single_file"}}
    cfg_dir = tmp_path_factory.mktemp("single_file_config")
    cfg_path = cfg_dir / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path_factory.mktemp("single_file_output")
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)
    return output_dir


def test_one_combined_file_instead_of_monthly(single_file_output_dir: Path):
    hh_dir = single_file_output_dir / "serl_smart_meter_hh_edition08"
    csv_files = list(hh_dir.glob("*.csv"))
    assert [p.name for p in csv_files] == ["serl_half_hourly_edition08.csv"]


def test_combined_file_has_all_months_of_data(single_file_output_dir: Path):
    hh_dir = single_file_output_dir / "serl_smart_meter_hh_edition08"
    combined = pd.read_csv(hh_dir / "serl_half_hourly_edition08.csv")

    # 5 households x 8760 half-hours in 2021 (non-leap year) = 43800 rows.
    assert len(combined) == 5 * 365 * 48
    assert set(combined["PUPRN"].unique()).issubset(
        set(pd.read_csv(single_file_output_dir / "mock_internal" / "puprn_master.csv")["PUPRN"])
    )


def test_rt_summary_reads_back_single_file_layout_correctly(single_file_output_dir: Path):
    # ReadTypeDataQualitySummaryGenerator must find the single combined file,
    # not look for (and fail to find) 12 monthly files.
    rt_summary = single_file_output_dir / "serl_smart_meter_rt_summary_edition08.csv"
    assert rt_summary.exists()
    df = pd.read_csv(rt_summary)
    assert len(df) > 0
