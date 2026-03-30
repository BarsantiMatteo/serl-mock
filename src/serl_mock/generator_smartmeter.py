"""
src/serl_mock/generator_smartmeter.py  (Edition07-aligned revision)
----------------------------------------------------

Changes vs original:
    - UTC month cut-off for HH files (Edition07).
    - Local timestamps labeled BST/GMT using Europe/London conversion.
    - Valid_read_time derived from timestamp; HH is 1–48 or NA if misaligned.
    - Error flags per Edition07 thresholds; -5 for invalid time.
    - Gas_hh_Wh computed with CV = 39.5 MJ/m³ (~10972 Wh/m³).
    - Configurable edition in output filename; defaults to "08".

Sources:
    - Field semantics, time rules (UTC cut-off, BST), flags, thresholds, and gas conversion from
        SERL Smart meter consumption data: Technical documentation, Edition 07.

TODOs:
    - Give a more realistic shape to time series (see Eoghan's work)
    - Daily smart meter data generator
    - Include typical error/missing data in hh
"""

# src/serl_mock/generator_smartmeter.py
from __future__ import annotations
from typing import Optional, List
from pathlib import Path
import os

import numpy as np
import pandas as pd

from .ids import make_alphanumeric_ids_ordered, load_puprn_list_csv, write_puprn_list_csv
from .utils import read_config, seed_random, ensure_output_dir, with_edition_suffix, write_csv

class HHSmartMeterGenerator:
    """
    Half-hourly mock smart meter generator aligned with Edition07:
      - UTC month cut-off
      - Europe/London local labels (BST/GMT)
      - Effective date at midnight rollover
      - HH index 1..48 or NA; Valid_read_time boolean
      - Edition07-like flags and gas Wh conversion
    """

    CALORIFIC_VALUE_MJ_PER_M3 = 39.5
    GAS_WH_PER_M3 = CALORIFIC_VALUE_MJ_PER_M3 * (1000.0 / 3.6)  # ~10972.22 Wh/m3
    
    COLUMNS_SCHEMA = {
        "Elec_act_imp_hh_Wh":     {"min": 0, "max": 2000},
        "Elec_react_imp_hh_varh": {"min": 0, "max": 500},
        "Elec_act_exp_hh_Wh":     {"min": 0, "max": 6000},
        "Elec_react_exp_hh_varh": {"min": 0, "max": 200},
        "Gas_hh_m3":              {"min": 0, "max": 1.0},
        # additional parameters used only by flags (not needed for generation):
        "Elec_act_exp_very_high_threshold": 5000,
        "Gas_very_high_threshold": 8.0,
    }

    def __init__(self, config_path: str,
                 puprn_list_path: Optional[str] = None
        ):
        cfg = read_config(config_path)

        # Core config
        self.n_households = int(cfg["n_households"])
        self.start_year   = int(cfg["start_year"])
        self.end_year     = int(cfg["end_year"])
        self.freq         = "30min"
        self.seed         = int(cfg.get("random_seed", 42))
        seed_random(self.seed)

        self.edition = str(cfg.get("edition", "08"))

        # PUPRN handling
        if puprn_list_path and Path(puprn_list_path).exists():
            puprns = load_puprn_list_csv(puprn_list_path)
            if len(puprns) < self.n_households:
                raise ValueError("PUPRN list smaller than n_households.")
            self.households = puprns[: self.n_households]
        else:
            self.households = make_alphanumeric_ids_ordered(self.n_households, length=8, seed=self.seed)

        # Use the hard-coded schema
        self.columns = self.COLUMNS_SCHEMA
        self.rng = np.random.default_rng(self.seed)


        self.rng = np.random.default_rng(self.seed)

    # ---------- Formatting helpers ----------
    @staticmethod
    def _fmt_utc(ts_utc: pd.Timestamp) -> str:
        return ts_utc.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _fmt_local(ts_local: pd.Timestamp) -> str:
        tz_label = ts_local.tzname() or "GMT"  # "BST" or "GMT"
        return f"{ts_local.strftime('%Y-%m-%d %H:%M:%S')} {tz_label}"

    @staticmethod
    def _effective_date_local(ts_local: pd.Timestamp) -> str:
        if ts_local.hour == 0 and ts_local.minute == 0 and ts_local.second == 0:
            eff = (ts_local - pd.Timedelta(days=1)).date()
        else:
            eff = ts_local.date()
        return eff.strftime("%d/%m/%Y")

    @staticmethod
    def _hh_index_or_na(ts_utc: pd.Timestamp):
        if (ts_utc.second == 0) and (ts_utc.minute in (0, 30)):
            return (ts_utc.hour * 60 + ts_utc.minute) // 30 + 1
        return None

    # ---------- Flags ----------
    @staticmethod
    def _flag_elec_imp_ai(x: float) -> int:
        if pd.isna(x): return 0
        if x >= 16777215: return -1
        if x > 24000: return -2
        return 1

    @staticmethod
    def _flag_elec_exp_ae(x: float) -> int:
        if pd.isna(x): return 0
        if x >= 16777215: return -1
        if x > 5000: return -2
        return 1

    @staticmethod
    def _flag_gas_ai(x_m3: float) -> int:
        if pd.isna(x_m3): return 0
        if x_m3 >= 16777.215: return -1
        if x_m3 > 8.0: return -2
        return 1

    # ---------- Month generation ----------
    def generate_month(self, year: int, month: int) -> pd.DataFrame:
        # UTC month cut-off
        start_utc = pd.Timestamp(year=year, month=month, day=1, hour=0, minute=0, second=0, tz="UTC")
        next_month = (start_utc + pd.offsets.MonthBegin(1))
        end_utc = next_month - pd.Timedelta(minutes=30)  # inclusive 23:30
        idx_utc = pd.date_range(start=start_utc, end=end_utc, freq=self.freq, tz="UTC")

        # Optional: minimal skew to test Valid_read_time & HH
        if len(idx_utc) > 0:
            skew_count = max(1, len(idx_utc) // 2000)  # ~0.05%
            skew_idx = self.rng.choice(len(idx_utc), size=skew_count, replace=False)
            idx_utc = idx_utc.to_series()
            idx_utc.iloc[skew_idx] = idx_utc.iloc[skew_idx] + pd.to_timedelta(60, unit="s")
            idx_utc = idx_utc.sort_values().to_numpy()

        df = pd.DataFrame({
            "timestamp_utc": np.repeat(idx_utc, self.n_households),
            "PUPRN": np.tile(self.households, len(idx_utc))
        })

        ts_utc = pd.to_datetime(df["timestamp_utc"], utc=True)
        ts_local = ts_utc.dt.tz_convert("Europe/London")

        df["Read_date_time_UTC"] = [self._fmt_utc(ts) for ts in df["timestamp_utc"]]
        df["Read_date_time_local"] = [self._fmt_local(ts) for ts in ts_local]
        df["Read_date_effective_local"] = [self._effective_date_local(ts) for ts in ts_local]

        hh_list = [self._hh_index_or_na(ts) for ts in df["timestamp_utc"]]
        df["HH"] = pd.Series(hh_list, dtype="Int64")
        df["Valid_read_time"] = df["HH"].notna()

        # Synthetic measurements
        n = len(df)
        def rint_range(col):
            spec = self.columns[col]
            return self.rng.integers(spec["min"], spec["max"], size=n)
        def runif_range(col):
            spec = self.columns[col]
            return self.rng.uniform(spec["min"], spec["max"], size=n)

        df["Elec_act_imp_hh_Wh"]     = rint_range("Elec_act_imp_hh_Wh")
        df["Elec_react_imp_hh_varh"] = rint_range("Elec_react_imp_hh_varh")
        df["Elec_act_exp_hh_Wh"]     = rint_range("Elec_act_exp_hh_Wh")
        df["Elec_react_exp_hh_varh"] = rint_range("Elec_react_exp_hh_varh")
        df["Gas_hh_m3"]              = runif_range("Gas_hh_m3")
        df["Gas_hh_Wh"]              = (df["Gas_hh_m3"] * self.GAS_WH_PER_M3).round().astype(int)

        # Flags
        df["Elec_act_imp_flag"]  = [self._flag_elec_imp_ai(x) for x in df["Elec_act_imp_hh_Wh"]]
        df["Elec_act_exp_flag"]  = [self._flag_elec_exp_ae(x) for x in df["Elec_act_exp_hh_Wh"]]
        df["Elect_react_imp_flag"] = np.where(df["Valid_read_time"], 1, -5)
        df["Elect_react_exp_flag"] = np.where(df["Valid_read_time"], 1, -5)
        df["Gas_flag"]           = [self._flag_gas_ai(x) for x in df["Gas_hh_m3"]]

        invalid_time_mask = ~df["Valid_read_time"]
        df.loc[invalid_time_mask, ["Elec_act_imp_flag", "Elec_act_exp_flag", "Gas_flag"]] = -5

        return df.drop(columns=["timestamp_utc"])

    # ---------- Write ----------
    def write_month(self, df: pd.DataFrame, year: int, month: int, outfolder: str):
        fname = with_edition_suffix(f"serl_half_hourly_{year}_{month:02d}", self.edition)
        fpath = Path(outfolder) / fname
        write_csv(df, str(fpath))

    def generate_all(self, outfolder: str):
        outfolder = ensure_output_dir(outfolder)

        for year in range(self.start_year, self.end_year + 1):
            for month in range(1, 13):
                print(f"Generating {year}-{month:02d} (UTC month cut-off)...")
                df = self.generate_month(year, month)
                self.write_month(df, year, month, outfolder)


class DailySmartMeterGenerator:
    # To be implemented 
    pass