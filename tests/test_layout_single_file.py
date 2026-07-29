"""Proves the layout abstraction works end-to-end for a non-default layout.
Layout is bound to the edition the same way format is — neither edition08
nor edition09 currently uses "single_file", so there's no YAML config path
to it. HHSmartMeterGenerator and ReadTypeDataQualitySummaryGenerator accept
a `layout`/`hh_layout` constructor override for direct programmatic/test
use, which is what this test exercises instead of going through run_all().
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.serl_mock.generator_household_traits import generate_household_traits, write_household_traits
from src.serl_mock.generator_smartmeter import (
    DailySmartMeterGenerator, HHSmartMeterGenerator, ReadTypeDataQualitySummaryGenerator,
)
from src.serl_mock.ids import make_alphanumeric_ids_ordered, write_puprn_list_csv
from src.serl_mock.layout import get_layout
from tests._shared import TINY_CONFIG


@pytest.fixture(scope="module")
def single_file_output_dir(tmp_path_factory) -> Path:
    cfg_path = tmp_path_factory.mktemp("single_file_config") / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(TINY_CONFIG), encoding="utf-8")

    output_dir = tmp_path_factory.mktemp("single_file_output")
    puprn_csv = output_dir / "puprn_master.csv"
    traits_csv = output_dir / "household_traits.csv"

    puprns = make_alphanumeric_ids_ordered(
        n=TINY_CONFIG["n_households"], seed=TINY_CONFIG["seed"], length=8,
    )
    write_puprn_list_csv(puprns, puprn_csv)
    traits_cfg = TINY_CONFIG["household_traits"]
    traits_df = generate_household_traits(
        puprns=puprns,
        pv_fraction=traits_cfg["pv_fraction"],
        hp_fraction=traits_cfg["hp_fraction"],
        ev_fraction=traits_cfg["ev_fraction"],
        gas_meter_fraction=traits_cfg["gas_meter_fraction"],
        export_meter_fraction=traits_cfg["export_meter_fraction"],
        solar_thermal_fraction=traits_cfg["solar_thermal_fraction"],
        seed=TINY_CONFIG["seed"],
    )
    write_household_traits(traits_df, traits_csv)

    single_file_layout = get_layout("single_file")
    hh_dir = output_dir / "serl_smart_meter_hh_edition08"
    HHSmartMeterGenerator(
        config_path=str(cfg_path), puprn_list_path=str(puprn_csv), traits_path=str(traits_csv),
        layout=single_file_layout,
    ).generate_all(outfolder=hh_dir)

    # Daily layout isn't configurable — generate it the normal way.
    daily_dir = output_dir / "serl_smart_meter_daily_edition08"
    DailySmartMeterGenerator(
        config_path=str(cfg_path), puprn_list_path=str(puprn_csv), traits_path=str(traits_csv),
    ).generate_all(outfolder=daily_dir)

    ReadTypeDataQualitySummaryGenerator(
        config_path=str(cfg_path), puprn_list_path=str(puprn_csv), traits_path=str(traits_csv),
        hh_layout=single_file_layout,
    ).generate_and_write(hh_folder=hh_dir, daily_folder=daily_dir, outfolder=output_dir)

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
        set(pd.read_csv(single_file_output_dir / "puprn_master.csv")["PUPRN"])
    )


def test_rt_summary_reads_back_single_file_layout_correctly(single_file_output_dir: Path):
    # ReadTypeDataQualitySummaryGenerator must find the single combined file,
    # not look for (and fail to find) 12 monthly files.
    rt_summary = single_file_output_dir / "serl_smart_meter_rt_summary_edition08.csv"
    assert rt_summary.exists()
    df = pd.read_csv(rt_summary)
    assert len(df) > 0
