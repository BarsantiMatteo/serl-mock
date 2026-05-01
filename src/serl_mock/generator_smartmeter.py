# src/serl_mock/generator_smartmeter.py
"""
Half-hourly smart meter data generator — Edition 07/08 aligned.

Generation model
----------------
For each household h and half-hour period t:

    elec(h, t) ~ Normal( base_elec(h) * elec_seasonal(doy(t)) * elec_daily(hour(t)),
                         base_elec(h) * elec_seasonal * elec_daily * variance(h) )
                 + occasional appliance spike

    gas(h, t)  ~ Normal( base_gas(h) * gas_seasonal(doy(t)) * gas_daily(hour(t)),
                         gas_heat_mean * 0.4 )     [heating season]
               | Uniform(50, 300) with prob summer_hw_prob  [non-heating season]
               * has_gas(h)

Seasonal and daily multiplier functions live in patterns.py.
Per-household baseline parameters live in profiles.py.
Both can be replaced or extended independently of the generator logic.

Edition 07 compliance
---------------------
    - UTC month cut-off for HH files.
    - Europe/London local timestamps, BST/GMT label.
    - Effective date at UTC midnight rollover.
    - HH index 1–48 or NA when timestamp is not on a 30-min boundary.
    - Edition 07-aligned error flags; -5 for invalid read time.
    - Gas Wh from CV = 39.5 MJ/m³ (~10 972 Wh/m³).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from .ids import make_alphanumeric_ids_ordered, load_puprn_list_csv
from .utils import read_config, seed_random, ensure_output_dir, with_edition_suffix, write_csv
from .profiles import generate_profiles
from .patterns import (
    elec_seasonal_mult, elec_daily_mult,
    gas_seasonal_mult,  gas_daily_mult,
)


class HHSmartMeterGenerator:
    """
    Half-hourly mock smart meter generator.

    Profiles (per-household baselines) and patterns (seasonal/daily
    multipliers) are decoupled so either can be swapped independently.
    All generation parameters are configurable via serl_mock.yaml.
    """

    CALORIFIC_VALUE_MJ_PER_M3 = 39.5
    GAS_WH_PER_M3 = CALORIFIC_VALUE_MJ_PER_M3 * (1000.0 / 3.6)  # ~10 972.22 Wh/m³

    FLAG_THRESHOLDS = {
        "elec_meter_max_Wh":     16_777_215,
        "elec_imp_very_high_Wh": 24_000,
        "elec_exp_very_high_Wh": 5_000,
        "gas_meter_max_m3":      16_777.215,
        "gas_very_high_m3":      8.0,
    }

    def __init__(self, config_path: str, puprn_list_path: Optional[str] = None):
        cfg = read_config(config_path)

        # Core config
        self.n_households = int(cfg["n_households"])
        self.start_year   = int(cfg["start_year"])
        self.end_year     = int(cfg["end_year"])
        self.freq         = "30min"
        self.seed         = int(cfg.get("seed", 42))
        seed_random(self.seed)

        self.edition = str(cfg.get("edition", "08"))

        # PUPRN handling
        if puprn_list_path and Path(puprn_list_path).exists():
            puprns = load_puprn_list_csv(puprn_list_path)
            if len(puprns) < self.n_households:
                raise ValueError("PUPRN list smaller than n_households.")
            self.households = puprns[: self.n_households]
        else:
            self.households = make_alphanumeric_ids_ordered(
                self.n_households, length=8, seed=self.seed
            )

        self.rng = np.random.default_rng(self.seed)

        # Pattern parameters (overridable via 'patterns:' in serl_mock.yaml)
        pat = cfg.get("patterns", {})
        self._elec_seasonal_amp  = float(pat.get("elec_seasonal_amplitude",       0.3))
        self._gas_seasonal_amp   = float(pat.get("gas_seasonal_amplitude",        2.0))
        self._elec_spike_prob    = float(pat.get("elec_spike_probability",        0.02))
        self._elec_spike_max_wh  = float(pat.get("elec_spike_max_wh",         2000.0))
        self._summer_hw_prob     = float(pat.get("summer_hot_water_probability",  0.15))
        self._gas_heat_threshold = float(pat.get("gas_heating_threshold",         0.1))

        # Household profiles (overridable via 'profiles:' in serl_mock.yaml)
        prof = cfg.get("profiles", {})
        self._profiles = generate_profiles(
            puprns=self.households,
            rng=self.rng,
            base_elec_mean_wh=float(prof.get("base_elec_mean_wh", 175.0)),
            base_elec_std_wh= float(prof.get("base_elec_std_wh",   50.0)),
            base_gas_mean_wh= float(prof.get("base_gas_mean_wh", 1500.0)),
            base_gas_std_wh=  float(prof.get("base_gas_std_wh",   300.0)),
            gas_fraction=     float(prof.get("gas_fraction",        0.85)),
        )

        # Pre-compute ordered arrays for vectorised generation
        self._base_elec = np.array([self._profiles[p].base_elec_wh for p in self.households])
        self._base_gas  = np.array([self._profiles[p].base_gas_wh  for p in self.households])
        self._elec_var  = np.array([self._profiles[p].elec_variance for p in self.households])
        self._has_gas   = np.array([self._profiles[p].has_gas       for p in self.households],
                                   dtype=float)  # 0.0 or 1.0 for vectorised masking

    # ---------- Timestamp formatting helpers ----------

    @staticmethod
    def _fmt_utc(ts: pd.Timestamp) -> str:
        return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _fmt_local(ts: pd.Timestamp) -> str:
        return f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {ts.tzname() or 'GMT'}"

    @staticmethod
    def _effective_date_local(ts: pd.Timestamp) -> str:
        eff = (ts - pd.Timedelta(days=1)).date() if (ts.hour == ts.minute == ts.second == 0) else ts.date()
        return eff.strftime("%d/%m/%Y")

    @staticmethod
    def _hh_index_or_na(ts: pd.Timestamp):
        if ts.second == 0 and ts.minute in (0, 30):
            return (ts.hour * 60 + ts.minute) // 30 + 1
        return None

    # ---------- Month generation ----------

    def generate_month(self, year: int, month: int) -> pd.DataFrame:
        """Generate all households for a single calendar month (UTC cut-off)."""

        # UTC timestamps for the month
        start_utc = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        end_utc   = start_utc + pd.offsets.MonthBegin(1) - pd.Timedelta(minutes=30)
        idx_utc   = pd.date_range(start=start_utc, end=end_utc, freq=self.freq, tz="UTC")

        # Minimal skew (~0.05 %) to exercise Valid_read_time and HH index logic
        if len(idx_utc) > 0:
            skew_count = max(1, len(idx_utc) // 2000)
            skew_pos   = self.rng.choice(len(idx_utc), size=skew_count, replace=False)
            idx_series = idx_utc.to_series()
            idx_series.iloc[skew_pos] += pd.to_timedelta(60, unit="s")
            idx_utc = idx_series.sort_values().to_numpy()

        T = len(idx_utc)
        H = self.n_households
        N = T * H

        # Per-timestamp series (T values) — used for patterns and formatting
        ts_utc_s   = pd.to_datetime(pd.Series(idx_utc), utc=True)
        ts_local_s = ts_utc_s.dt.tz_convert("Europe/London")

        doy        = np.asarray(ts_utc_s.dt.day_of_year, dtype=float)
        hour_float = np.asarray(ts_local_s.dt.hour + ts_local_s.dt.minute / 60.0, dtype=float)

        # Seasonal and daily multipliers (T values)
        e_seas = elec_seasonal_mult(doy,        self._elec_seasonal_amp)
        g_seas = gas_seasonal_mult( doy,        self._gas_seasonal_amp)
        e_day  = elec_daily_mult(   hour_float)
        g_day  = gas_daily_mult(    hour_float)

        # Expand to N = T*H: each timestamp value repeated H times
        e_seas_n = np.repeat(e_seas, H)
        g_seas_n = np.repeat(g_seas, H)
        e_day_n  = np.repeat(e_day,  H)
        g_day_n  = np.repeat(g_day,  H)

        # Per-household arrays tiled T times (H values → T*H)
        base_elec_n = np.tile(self._base_elec, T)
        base_gas_n  = np.tile(self._base_gas,  T)
        elec_var_n  = np.tile(self._elec_var,  T)
        has_gas_n   = np.tile(self._has_gas,   T)

        # --- Electricity (active import) ---
        elec_mean = base_elec_n * e_seas_n * e_day_n
        elec_wh   = np.maximum(
            0.0,
            self.rng.normal(elec_mean, np.maximum(elec_mean * elec_var_n, 1.0)),
        )
        # Occasional appliance spike (2 % of readings by default)
        spike_mask = self.rng.random(N) < self._elec_spike_prob
        elec_wh   += spike_mask * self.rng.uniform(500.0, self._elec_spike_max_wh, N)

        # Reactive import: simplified power-factor model (~15 % of active)
        elec_react_imp = np.maximum(
            0.0,
            self.rng.normal(elec_wh * 0.15, np.maximum(elec_wh * 0.05, 1.0)),
        )
        # Export and reactive export: minimal (no solar profile yet)
        elec_exp_wh    = self.rng.integers(0, 50, size=N).astype(float)
        elec_react_exp = self.rng.integers(0, 30, size=N).astype(float)

        # --- Gas ---
        is_heating    = g_seas_n > self._gas_heat_threshold
        gas_heat_mean = base_gas_n * g_seas_n * g_day_n
        gas_heat_wh   = np.maximum(
            0.0,
            self.rng.normal(gas_heat_mean, np.maximum(gas_heat_mean * 0.4, 1.0)),
        )
        # Summer hot-water draw (flat probability, no heating boiler)
        summer_hw_wh = (
            self.rng.uniform(50.0, 300.0, N)
            * (self.rng.random(N) < self._summer_hw_prob)
        )
        gas_wh = np.where(is_heating, gas_heat_wh, summer_hw_wh) * has_gas_n
        gas_m3 = gas_wh / self.GAS_WH_PER_M3

        # --- Build DataFrame ---
        df = pd.DataFrame({
            "timestamp_utc": np.repeat(idx_utc, H),
            "PUPRN":         np.tile(self.households, T),
        })

        # Format timestamps once at T level, then repeat — avoids T*H Python iterations
        utc_fmt   = [self._fmt_utc(ts)             for ts in ts_utc_s]
        local_fmt = [self._fmt_local(ts)            for ts in ts_local_s]
        eff_fmt   = [self._effective_date_local(ts) for ts in ts_local_s]
        hh_vals   = [self._hh_index_or_na(ts)       for ts in ts_utc_s]

        df["Read_date_time_UTC"]        = np.repeat(utc_fmt,   H)
        df["Read_date_time_local"]      = np.repeat(local_fmt, H)
        df["Read_date_effective_local"] = np.repeat(eff_fmt,   H)
        df["HH"]                        = pd.array(hh_vals, dtype="Int64").repeat(H)
        df["Valid_read_time"]           = df["HH"].notna()
        invalid                         = ~df["Valid_read_time"].to_numpy(dtype=bool)

        # --- Energy columns ---
        df["Elec_act_imp_hh_Wh"]     = np.round(elec_wh).astype(int)
        df["Elec_react_imp_hh_varh"] = np.round(elec_react_imp).astype(int)
        df["Elec_act_exp_hh_Wh"]     = np.round(elec_exp_wh).astype(int)
        df["Elec_react_exp_hh_varh"] = np.round(elec_react_exp).astype(int)
        df["Gas_hh_m3"]              = np.round(gas_m3, 4)
        df["Gas_hh_Wh"]              = np.round(gas_wh).astype(int)

        # --- Vectorised flags (last np.where wins → meter_max overrides very_high) ---
        v = self.FLAG_THRESHOLDS

        def _flag_elec_imp(wh: np.ndarray) -> np.ndarray:
            f = np.ones(N, dtype=int)
            f = np.where(wh >  v["elec_imp_very_high_Wh"], -2, f)
            f = np.where(wh >= v["elec_meter_max_Wh"],     -1, f)
            f = np.where(invalid, -5, f)
            return f

        def _flag_elec_exp(wh: np.ndarray) -> np.ndarray:
            f = np.ones(N, dtype=int)
            f = np.where(wh >  v["elec_exp_very_high_Wh"], -2, f)
            f = np.where(wh >= v["elec_meter_max_Wh"],     -1, f)
            f = np.where(invalid, -5, f)
            return f

        def _flag_gas(m3: np.ndarray) -> np.ndarray:
            f = np.ones(N, dtype=int)
            f = np.where(m3 >  v["gas_very_high_m3"],  -2, f)
            f = np.where(m3 >= v["gas_meter_max_m3"],  -1, f)
            f = np.where(invalid, -5, f)
            return f

        df["Elec_act_imp_flag"]    = _flag_elec_imp(elec_wh)
        df["Elec_act_exp_flag"]    = _flag_elec_exp(elec_exp_wh)
        df["Elect_react_imp_flag"] = np.where(invalid, -5, 1)
        df["Elect_react_exp_flag"] = np.where(invalid, -5, 1)
        df["Gas_flag"]             = _flag_gas(gas_m3)

        return df.drop(columns=["timestamp_utc"])

    # ---------- Write ----------

    def write_month(self, df: pd.DataFrame, year: int, month: int, outfolder: str):
        fname = with_edition_suffix(f"serl_half_hourly_{year}_{month:02d}", self.edition)
        write_csv(df, str(Path(outfolder) / fname))

    def generate_all(self, outfolder: "Union[str, os.PathLike]"):
        outfolder = ensure_output_dir(outfolder)
        for year in range(self.start_year, self.end_year + 1):
            for month in range(1, 13):
                print(f"  {year}-{month:02d} ...")
                df = self.generate_month(year, month)
                self.write_month(df, year, month, outfolder)


class DailySmartMeterGenerator:
    """
    Daily smart meter data generator.

    Wraps HHSmartMeterGenerator: generates half-hourly data month by month,
    aggregates to daily level by summing valid HH reads, and writes one CSV
    per calendar year.
    """

    DAILY_ELEC_VERY_HIGH_WH = 100_000  # ~4× HH threshold × 48 HH
    DAILY_GAS_VERY_HIGH_M3  = 200.0

    def __init__(self, config_path: str, puprn_list_path: Optional[str] = None):
        self._hh      = HHSmartMeterGenerator(config_path, puprn_list_path)
        self.start_year = self._hh.start_year
        self.end_year   = self._hh.end_year
        self.edition    = self._hh.edition

    @staticmethod
    def _expected_hh(local_date) -> int:
        """Return expected HH count for a local date: 46 (spring-forward), 50 (fall-back), 48 otherwise."""
        ts = pd.Timestamp(str(local_date), tz="Europe/London")
        ts_next = ts + pd.DateOffset(days=1)
        return int((ts_next.tz_convert("UTC") - ts.tz_convert("UTC")).total_seconds() / 1800)

    def _aggregate_to_daily(self, hh_df: pd.DataFrame) -> pd.DataFrame:
        df = hh_df.copy()

        # Parse local effective date (HH format is dd/mm/yyyy)
        df["_date"]  = pd.to_datetime(df["Read_date_effective_local"], format="%d/%m/%Y").dt.date
        df["_valid"] = df["Valid_read_time"].astype(bool)

        # Energy from valid reads only (invalid skewed timestamps contribute 0)
        df["_e_wh"] = np.where(df["_valid"], df["Elec_act_imp_hh_Wh"].astype(float), 0.0)
        df["_g_m3"] = np.where(df["_valid"], df["Gas_hh_m3"].astype(float), 0.0)

        grp = df.groupby(["PUPRN", "_date"])
        agg = grp.agg(
            _valid_n=("_valid", "sum"),
            _e_sum=("_e_wh",  "sum"),
            _g_sum=("_g_m3",  "sum"),
        ).reset_index()

        # DST-aware expected HH count (46 / 48 / 50)
        exp_map = {d: self._expected_hh(d) for d in agg["_date"].unique()}
        agg["_exp"]  = agg["_date"].map(exp_map)
        agg["_full"] = agg["_valid_n"] >= agg["_exp"]

        # HH sums — NA / NaN when day is incomplete
        agg["Elec_act_imp_hh_sum_Wh"] = (
            agg["_e_sum"].round().astype("Int64").where(agg["_full"])
        )
        agg["Gas_hh_sum_m3"] = agg["_g_sum"].round(4).where(agg["_full"])

        # Primary daily reads equal HH sums in mock data
        agg["Elec_act_imp_d_Wh"]              = agg["Elec_act_imp_hh_sum_Wh"]
        agg["Unit_correct_elec_act_imp_d_Wh"] = agg["Elec_act_imp_d_Wh"]
        agg["Gas_d_m3"]                        = agg["Gas_hh_sum_m3"]

        # Validity and match codes
        agg["Valid_hh_sum_or_daily_elec"] = agg["_full"]
        agg["Valid_hh_sum_or_daily_gas"]  = agg["_full"]
        agg["Elec_sum_match"] = np.where(agg["_full"], 1, 0)
        agg["Gas_sum_match"]  = np.where(agg["_full"], 1, 0)

        # Date columns — daily read is assumed at UTC midnight → UTC date = eff_date + 1
        eff = pd.to_datetime(agg["_date"])
        agg["Read_date_effective_local"] = eff.dt.strftime("%Y-%m-%d")
        agg["Read_date_time_UTC"]        = (eff + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
        agg["Valid_read_time"]           = True

        # Energy flags on daily values
        e_d = agg["Elec_act_imp_d_Wh"].fillna(0).astype(float).to_numpy()
        g_d = agg["Gas_d_m3"].fillna(0.0).to_numpy(dtype=float)
        v   = HHSmartMeterGenerator.FLAG_THRESHOLDS

        e_flag = np.ones(len(agg), dtype=int)
        e_flag = np.where(e_d >  self.DAILY_ELEC_VERY_HIGH_WH,  -2, e_flag)
        e_flag = np.where(e_d >= v["elec_meter_max_Wh"],         -1, e_flag)
        agg["Elec_act_imp_flag"] = e_flag

        g_flag = np.ones(len(agg), dtype=int)
        g_flag = np.where(g_d >  self.DAILY_GAS_VERY_HIGH_M3,   -2, g_flag)
        g_flag = np.where(g_d >= v["gas_meter_max_m3"],          -1, g_flag)
        agg["Gas_flag"] = g_flag

        return agg[[
            "PUPRN",
            "Read_date_effective_local",
            "Read_date_time_UTC",
            "Valid_read_time",
            "Elec_act_imp_flag",
            "Valid_hh_sum_or_daily_elec",
            "Elec_sum_match",
            "Gas_flag",
            "Valid_hh_sum_or_daily_gas",
            "Gas_sum_match",
            "Elec_act_imp_d_Wh",
            "Unit_correct_elec_act_imp_d_Wh",
            "Elec_act_imp_hh_sum_Wh",
            "Gas_d_m3",
            "Gas_hh_sum_m3",
        ]]

    def generate_year(self, year: int) -> pd.DataFrame:
        """Generate and aggregate all 12 months of HH data for one calendar year."""
        chunks = [self._hh.generate_month(year, m) for m in range(1, 13)]
        daily  = self._aggregate_to_daily(pd.concat(chunks, ignore_index=True))
        # Drop boundary dates that belong to an adjacent year
        return daily[
            daily["Read_date_effective_local"].str.startswith(str(year))
        ].reset_index(drop=True)

    def write_year(self, df: pd.DataFrame, year: int, outfolder: str):
        fname = with_edition_suffix(f"serl_daily_{year}", self.edition)
        write_csv(df, str(Path(outfolder) / fname))

    def generate_all(self, outfolder: "Union[str, os.PathLike]"):
        outfolder = ensure_output_dir(outfolder)
        for year in range(self.start_year, self.end_year + 1):
            print(f"  {year} (daily) ...")
            df = self.generate_year(year)
            self.write_year(df, year, outfolder)