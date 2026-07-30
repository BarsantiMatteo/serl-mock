"""Proves edition09's daily-smart-meter layout (Edition.daily_smart_meter_layout
= "single_file") actually produces one combined file at the top level of the
run's output — not a per-year file inside its own subfolder like edition08's
"yearly" layout — and that ReadTypeDataQualitySummaryGenerator still finds
and reads it correctly. Uses the real edition09 config (via the shared
generated_output_dir_edition09 fixture), not a constructor override, since
this is a real edition characteristic, not just a hypothetical layout.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from tests._shared import TINY_CONFIG_EDITION09


def test_single_combined_daily_file_at_top_level(generated_output_dir_edition09: Path):
    daily_file = generated_output_dir_edition09 / "serl_smart_meter_daily_edition09.parquet"
    assert daily_file.exists()
    # Not nested in its own subfolder, unlike edition08's serl_smart_meter_daily_edition08/
    assert not (generated_output_dir_edition09 / "serl_smart_meter_daily_edition09").exists()


def test_daily_file_covers_the_whole_configured_range(generated_output_dir_edition09: Path):
    df = pd.read_parquet(generated_output_dir_edition09 / "serl_smart_meter_daily_edition09.parquet")
    n_households = TINY_CONFIG_EDITION09["n_households"]
    start_year = TINY_CONFIG_EDITION09["start_year"]
    end_year = TINY_CONFIG_EDITION09["end_year"]
    assert start_year == end_year == 2021, "test assumes the single configured (non-leap) year"
    assert len(df) == n_households * 365


def test_rt_summary_reads_the_single_daily_file_correctly(generated_output_dir_edition09: Path):
    rt_summary = generated_output_dir_edition09 / "serl_smart_meter_rt_summary_edition09.parquet"
    assert rt_summary.exists()
    df = pd.read_parquet(rt_summary)
    assert len(df) > 0
