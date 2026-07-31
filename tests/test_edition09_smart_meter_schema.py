"""Proves edition09's real HH/daily smart-meter column schema ("harmonised" in
src/serl_mock/edition.py) is actually produced, and that edition08's ("legacy")
output is completely unaffected by the branch that builds it.

Explicit column-membership assertions, rather than a golden-manifest diff,
so a failure here says exactly which column is wrong instead of dumping the
whole manifest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pandas as pd


def _hh_columns(output_dir: Path, edition: str) -> Tuple[list, Path]:
    hh_dir = output_dir / f"serl_smart_meter_hh_edition{edition}"
    hh_file = sorted(hh_dir.glob(f"*.{ 'parquet' if edition == '09' else 'csv' }"))[0]
    df = pd.read_parquet(hh_file) if edition == "09" else pd.read_csv(hh_file)
    return list(df.columns), hh_file


def _daily_columns(output_dir: Path, edition: str) -> Tuple[list, Path]:
    if edition == "09":
        daily_file = output_dir / "smart_meter_daily_edition09.parquet"
        df = pd.read_parquet(daily_file)
    else:
        daily_dir = output_dir / f"serl_smart_meter_daily_edition{edition}"
        daily_file = sorted(daily_dir.glob("*.csv"))[0]
        df = pd.read_csv(daily_file)
    return list(df.columns), daily_file


def test_edition09_hh_uses_harmonised_columns(generated_output_dir_edition09: Path):
    columns, hh_file = _hh_columns(generated_output_dir_edition09, "09")

    assert "Gas_hh_m3" not in columns
    assert "Elect_react_imp_flag" not in columns
    assert "Elect_react_exp_flag" not in columns
    assert "Elec_react_imp_flag" in columns
    assert "Elec_react_exp_flag" in columns
    assert "Gas_hh_Wh" in columns
    assert columns[-1] == "filename"

    df = pd.read_parquet(hh_file)
    assert (df["filename"] == hh_file.name).all()


def test_edition09_daily_uses_harmonised_columns(generated_output_dir_edition09: Path):
    columns, daily_file = _daily_columns(generated_output_dir_edition09, "09")

    for absent in ("Gas_d_m3", "Gas_hh_sum_m3", "Valid_read_time"):
        assert absent not in columns, f"{absent} should not exist in the harmonised daily schema"

    for present in (
        "Gas_d_Wh",
        "Elec_act_imp_hh_sum_Wh",
        "Elec_net_act_imp_d_Wh_recommended",
        "Gas_d_Wh_recommended",
        "Read_date_time_UTC_elec",
        "Read_date_time_UTC_gas",
        "Read_date_time_elec",
        "Valid_read_time_gas",
    ):
        assert present in columns, f"{present} missing from the harmonised daily schema"

    assert columns[-1] == "filename"

    df = pd.read_parquet(daily_file)
    assert (df["filename"] == daily_file.name).all()
    # Timestamp columns are genuine timezone-aware timestamps, not text.
    assert str(df["Read_date_time_UTC_elec"].dtype) == "datetime64[us, UTC]"
    assert str(df["Read_date_time_elec"].dtype) == "datetime64[us, Europe/London]"
    # "_recommended" is a best-available fallback — never NaN even though the
    # strict gated fields (Elec_act_imp_d_Wh, Gas_d_Wh) can be, for an
    # incomplete day.
    assert df["Elec_net_act_imp_d_Wh_recommended"].notna().all()
    assert df["Gas_d_Wh_recommended"].notna().all()
    # Net = import minus (non-negative) export, so it can never exceed the
    # gross import figure for the same (complete) day.
    complete = df["Elec_act_imp_d_Wh"].notna()
    assert (df.loc[complete, "Elec_net_act_imp_d_Wh_recommended"] <= df.loc[complete, "Elec_act_imp_d_Wh"]).all()


def test_edition09_rt_summary_has_gas_rows(generated_output_dir_edition09: Path):
    rt_file = generated_output_dir_edition09 / "serl_smart_meter_rt_summary_edition09.parquet"
    df = pd.read_parquet(rt_file)

    gpf_ai = df[(df["deviceType"] == "GPF") & (df["readType"] == "AI")]
    gpf_dl = df[(df["deviceType"] == "GPF") & (df["readType"] == "DL")]
    assert len(gpf_ai) > 0
    assert len(gpf_dl) > 0


def test_edition08_smart_meter_columns_are_unaffected(generated_output_dir: Path):
    hh_columns, _ = _hh_columns(generated_output_dir, "08")
    daily_columns, _ = _daily_columns(generated_output_dir, "08")

    assert "Gas_hh_m3" in hh_columns
    assert "Elect_react_imp_flag" in hh_columns
    assert "filename" not in hh_columns

    assert "Gas_d_m3" in daily_columns
    assert "Valid_read_time" in daily_columns
    assert "filename" not in daily_columns
