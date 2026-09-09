# src/serl_mock/generator_contextual_data.py
from __future__ import annotations
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union, cast

import numpy as np
import pandas as pd

from .ids import (
    make_alphanumeric_ids_ordered,
    load_puprn_list_csv,
)
from .generator_household_traits import load_household_traits
from .paths import MOCK_DIR, reference_dir_for, mock_climate_dirname, LSOA_CODES_PATH
from .edition import get_edition
from .utils import (
    read_config, seed_random, ensure_output_dir,
    with_edition_suffix, write_table, read_survey_dictionary, read_epc_generated_fields,
    read_lsoa_codes, read_master_mapping,
)

@dataclass
class OutputNames:
    epc: str = "serl_epc_data"
    survey: str = "serl_survey_data"
    covid19_survey: str = "serl_covid19_survey_data"
    summary: str = "serl_participant_summary"
    followup_survey: str = "serl_2023_follow_up_survey_data"
    harmonised_survey: str = "masterserl_surveys"
    survey_2025: str = "serl_2025_follow_up_survey_data"
    exporters_prefix: str = "Elec"

# =====================================================================
# MasterSERL harmonised survey (masterserl_surveys_edition<N>.csv) — coding
# templates and generation helpers. See
# docs/documentation/SERL/edition09/serl_survey_harmonisation_documentation.pdf.
#
# Driven by data/reference/edition09/serl_master_mapping_edition09.csv
# (exported from the real SERL "Master Mapping" methodology sheet), which is
# the authoritative list of the ~386 harmonised variables, their domain, and
# — critically — which of the three raw surveys (Sign Up / 2023 / 2025)
# actually asked each one, and under what original column name. A variable
# not asked in a given survey is written as 999999, per the real
# harmonisation methodology.
#
# build_harmonised_survey_dataframe() reads from the three raw survey
# generators below and, further down, generate_serl_survey() /
# generate_follow_up_survey() (passed in as sign_up_df / survey_2023_df /
# survey_2025_df) rather than sampling every field independently, so a
# PUPRN's harmonised answer is *derived from*, and therefore consistent
# with, that same PUPRN's raw-survey row — see _recode_raw_value() for
# exactly how a raw cell becomes a harmonised one. This also means the raw
# generators' own skip logic is inherited for free: a raw cell left blank by
# a skip rule harmonises to a missing code, not a fabricated answer. All
# three raw dataframe parameters are optional — passing None (as the
# existing tests do) falls back to sampling that survey's cells
# independently from the same coding templates, so this still works
# standalone.
#
# Value sampling (independent, or as a fallback when a raw value can't be
# reused — see _recode_raw_value()) is driven by a small library of coding
# templates below (binary Yes/No, 5-point frequency, 9-point "compare to
# last winter" scale, etc.), assigned per variable by TEMPLATE_OVERRIDES with
# a binary Yes/No fallback for anything not explicitly listed — the large
# majority of the ~386 variables genuinely are that kind of "does your
# household have X?" presence flag, so the fallback is correct far more
# often than not, not just a lazy default. A handful of free-form numeric
# fields (occupant counts, hours, temperature) are sampled directly instead
# of from a fixed code list — see NUMERIC_COUNT_VARS / thermostat_C.
#
# Scope (v1): beyond reusing a raw cell verbatim when it's already a legal
# harmonised code (see _recode_raw_value()), no cross-field consistency is
# enforced. The harmonisation documentation's section 3.4 describes several
# further branching rules between related fields (e.g. a "present" flag and
# its matching "added or replaced in last 12 months" flag) that a real value
# recode would need real per-field mapping tables to get right — those
# aren't available for most variables (see the raw 2025 survey section
# below for why), so this remains a documented gap, not a fabrication.
# =====================================================================

# Sentinel distinguishing "no raw dataframe/column to look up at all" (fall
# back to fully independent sampling, the pre-refactor behaviour) from "the
# column exists but this PUPRN's cell in it is genuinely blank" (represented
# as None — see _lookup_raw_value()).
_NO_RAW_COLUMN = object()

# ---------- Coding templates ----------
# Every list below is a set of *harmonised* codes a variable can take, as
# documented in serl_survey_harmonisation_documentation.pdf's Table 8
# appendix. Codes in MISSING_CODE_SET are sampled as a group with low
# combined weight relative to the "real" answer codes — see _sample_value().
MISSING_CODE_SET = {-9, -3, -2, -1}

BINARY_FULL = [-9, -3, -2, -1, 0, 1]           # Not applicable/Prefer not to say/No answer/Don't know/No/Yes
BINARY_SIMPLE = [0, 1]                          # No/Yes only, no missing codes documented
FREQ5_FULL = [-9, -3, -2, -1, 1, 2, 3, 4, 5]    # Always/Very often/Quite often/Not very often/Never
COMPARE9 = [1, 2, 3, 4, 5, 6, 7, 8, 9]           # "compare to last winter" / dishwasher_use(_freq) 9-point scale
ADD_REP_2 = [1, 2]                              # Has been added/replaced in last 12 months / No
TAP_SHOWER = [-9, -3, -2, -1, 1, 2]             # tap_*/shower_* water-heating source flags
MOULD = [-9, -3, -2, -1, 1, 2, 3]               # -/Minor/Substantial
SUMMER3 = [-9, -3, -2, -1, 1, 2, 3]             # summer_* cooling-behaviour Yes/No/cannot-do-this
WORKSTATUS = [-9, -3, -2, -1, 0, 1, 2, 3, 4]    # working-status occupant counts (0/1/2/3/4-or-more)
COMFORT_BRANCH = [-9, -3, -2, -1, 0, 1, 2, 3]   # heating_difficulty_home & the comfort/mould-branch fields
FREQ7 = [1, 2, 3, 4, 5, 6, 7]                   # smart_meter_use / leave_home_to_warm / heat_fuel_cost_affordability
LIKERT0_10 = [-9, -3, -2, -1] + list(range(0, 11))  # life_satisfaction / life_worthwhile_score

_TEMPLATE_GROUPS: Dict[str, List] = {
    "binary_full": BINARY_FULL,
    "binary_simple": BINARY_SIMPLE,
    "freq5_full": FREQ5_FULL,
    "compare9": COMPARE9,
    "add_rep_2": ADD_REP_2,
    "tap_shower": TAP_SHOWER,
    "mould": MOULD,
    "summer3": SUMMER3,
    "workstatus": WORKSTATUS,
    "comfort_branch": COMFORT_BRANCH,
    "freq7": FREQ7,
    "likert0_10": LIKERT0_10,
}

# Per-variable overrides, keyed on the exact master_var_name as it appears in
# serl_master_mapping_edition09.csv (including its handful of upstream
# spreadsheet quirks, e.g. "heating_control_?", "own_rent_house?", the
# leading-space " electric_shower"). Anything not listed here falls back to
# BINARY_FULL, which is the correct code set for the large majority of the
# ~386 variables (mostly "does your household have X?" presence flags).
TEMPLATE_OVERRIDES: Dict[str, List] = {
    # -- Energy: heating-control flags with only 3 real codes (not 6) --
    "heating_timer": [-9, 0, 1],
    "heating_temp_set": [-9, 0, 1],
    "heating_smart_dev": [-9, 0, 1],
    "heating_manually": [-9, 0, 1],
    "heating_no_controls": [-9, 0, 1],
    "heating_control_?": [-9, 0, 1],
    # -- Energy: heating-upgrade checkboxes (Yes/No only) --
    "heating_upgrade_no": BINARY_SIMPLE,
    "heating_upgrade_na": BINARY_SIMPLE,
    "heating_upgrade_GB": BINARY_SIMPLE,
    "heating_upgrade_EL": BINARY_SIMPLE,
    "heating_upgrade_SW": BINARY_SIMPLE,
    "heating_upgrade_FB": BINARY_SIMPLE,
    "heating_upgrade_HP": BINARY_SIMPLE,
    "heating_upgrade_HPU": BINARY_SIMPLE,
    "heating_upgrade_WT": BINARY_SIMPLE,
    "heating_upgrade_other": BINARY_SIMPLE,
    # -- Energy: EV / smart-meter --
    "EV_present": [-2, -1, 1, 2],
    "EV_point": [1, 0, -1, -2, -3],
    "EV_charge_location": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7],
    "EV_charge_time": [-9, -3, -2, -1, 1, 2, 3, 4],
    "no_EVs": [-9, -3, -2, -1, 0, 1, 2, 3],
    "no_EVs_12m": [0, 1, 2, 3, 4, 5],
    "smart_meter_own": [-2, 1, 2],
    "smart_meter_use": FREQ7,
    "smart_display_use_freq": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7, 8],
    # -- Energy: the 5 add_rep_12m fields with only 2 codes (not the usual 6) --
    "EV_point_add_rep_12m": ADD_REP_2,
    "TRVs_add_rep_12m": ADD_REP_2,
    "thermostat_add_rep_12m": ADD_REP_2,
    "time_clock_add_rep_12m": ADD_REP_2,
    "smart_control_add_rep_12m": ADD_REP_2,
    # -- Appliances: standalone heaters --
    "stand_heater_own_power": [-9, -3, -2, -1, 1, 2],
    "stand_heater_use": [-9, -2, -1, 1, 2, 3, 4, 5],
    # -- Appliances: newer/simpler appliance flags (Yes/No only) --
    "microwave": BINARY_SIMPLE,
    "air_fryer": BINARY_SIMPLE,
    "slow_cooker": BINARY_SIMPLE,
    "smart_plug": BINARY_SIMPLE,
    "smart_thermostat": BINARY_SIMPLE,
    "microwave_use": BINARY_SIMPLE,
    "air_fryer_use": BINARY_SIMPLE,
    "slow_cooker_use": BINARY_SIMPLE,
    "smart_plug_use": BINARY_SIMPLE,
    "smart_thermostat_use": BINARY_SIMPLE,
    # -- Behaviour --
    "el_gas_use_beh": [-9, -2, -1, 1, 2, 3, 4],
    "heat_adjust_cold": BINARY_SIMPLE,
    "heat_adjust_baby": BINARY_SIMPLE,
    "heat_adjust_visitors": BINARY_SIMPLE,
    "heat_adjust_pets": BINARY_SIMPLE,
    "heat_adjust_stress": BINARY_SIMPLE,
    "heat_adjust_WFH": BINARY_SIMPLE,
    "heat_adjust_none": BINARY_SIMPLE,
    "heat_adjust_unocc": [-2, 1, 2, 3, 4, 5, 6],
    "switch_off_lights_freq": FREQ5_FULL,
    "clothes_freq": FREQ5_FULL,
    "energy_save_effort": [-9, -3, -2, -1, 1, 2, 3, 4],
    "open_window_cold": FREQ5_FULL,
    "open_window_warm": [-2, -1, 1, 2, 3, 4, 5],
    "boiler_flow_temp": [1, 0, -1, -2, -3],
    "heat_less_hours": [1, 0, -1, -2, -3],
    "vacant_heat_off": FREQ5_FULL,
    "standalone_heatoff": FREQ5_FULL,
    "elect_blanket_use": FREQ5_FULL,
    "vacant_room_heatoff": FREQ5_FULL,
    "occupied_room_heatoff": FREQ5_FULL,
    "full_load_washer": FREQ5_FULL,
    "washer_30C": FREQ5_FULL,
    "no_dryer_use": FREQ5_FULL,
    "standby_off": FREQ5_FULL,
    "curtains_blinds": FREQ5_FULL,
    "shower_no_bath": FREQ5_FULL,
    "short_showers_no_long": FREQ5_FULL,
    "avoid_cooker_oven": FREQ5_FULL,
    "dishwasher_use_freq": COMPARE9,
    "dishwasher_use": COMPARE9,
    "compare_30C": COMPARE9,  # doesn't end in "_compare" but is the same 9-point scale
    "heat_off_unoccupied": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6],
    "summer_windows": SUMMER3,
    "summer_fans": SUMMER3,
    "summer_shutters": SUMMER3,
    "summer_curtains": SUMMER3,
    "summer_blinds": SUMMER3,
    "summer_awning": SUMMER3,
    "summer_aircon": SUMMER3,
    "summer_MVHR": SUMMER3,
    "summer_night_windows": FREQ5_FULL,
    "summer_overheating": [-9, -3, -2, -1, 1, 2, 3, 4],
    "no_diff_use": BINARY_FULL,
    # -- Water --
    "tap_gas": TAP_SHOWER, "tap_electric": TAP_SHOWER, "tap_solar": TAP_SHOWER,
    "tap_other": TAP_SHOWER, "no_tap_heat": TAP_SHOWER, "tap_heat_?": TAP_SHOWER,
    "shower_gas": TAP_SHOWER, "shower_electric": TAP_SHOWER, "shower_solar": TAP_SHOWER,
    "shower_other": TAP_SHOWER, "no_shower_heat": TAP_SHOWER, "shower_heat_?": TAP_SHOWER,
    # -- Housing --
    "accom_type": [-2, 1, 2, 3, 4, 5, 6],
    "building_type": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6],
    "self_contained": [-2, 1, 2],
    "self_contained_accom": [-9, -3, -2, -1, 1, 2],
    "own_or_rent": [-2, 1, 2, 3, 4, 5],
    "own_rent_house?": [-9, -3, -2, -1, 1, 2, 3, 4, 5],
    "house_age": [-2, -1, 1, 2, 3, 4, 5, 6, 7],
    "building_age": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7, 8],
    "no_bathrooms": [0, 1, 2, 3, 4, 5],
    "no_bathrooms_bath_shower": [-9, -3, -2, -1, 0, 1, 2, 3, 4],
    "mould_windows_bathroom": MOULD, "mould_walls": MOULD, "mould_furnishing": MOULD,
    "house_condition": [-2, -1, 0, 1],
    # -- Environment / Comfort branching-question groups --
    "heating_difficulty_home": COMFORT_BRANCH,
    "heating_affordability": COMFORT_BRANCH,
    "no_comfort_answer": COMFORT_BRANCH,
    "none_above_comfort": COMFORT_BRANCH,
    "other_reason_comfort": COMFORT_BRANCH,
    "cold_weather_comfort": COMFORT_BRANCH,
    "thermal_comfort": [-9, -3, -2, -1, 1, 2],
    "leave_home_to_warm": FREQ7,
    # -- Finance --
    "financial_status": [-9, -3, -2, -1, 1, 2, 3, 4, 5],
    "annual_income": [-9, -3, -2, -1] + list(range(1, 12)),
    "payment_method_el": [-9, -3, -2, -1, 1, 2, 3, 4],
    "payment_method_gas": [-9, -3, -2, -1, 1, 2, 3, 4],
    "heat_fuel_cost_affordability": FREQ7,
    "time_dependence_energy_price": [-9, -3, -2, -1, 1, 2, 3],
    # -- Demographics --
    "gender": [-3, -2, 1, 2, 3],
    "age_group": [-2, 1, 2, 3, 4, 5, 6, 7],
    "ethnic_group": [-9, -3, -2, -1] + list(range(1, 20)),
    "current_employment_status": [-3, -2, 1, 2, 3, 4, 5, 6, 7],
    "work_hours_30+": WORKSTATUS,
    "work_hours_30-": WORKSTATUS,
    "no_work_disable": WORKSTATUS,
    "unemployed": WORKSTATUS,
    "student": WORKSTATUS,
    "retired_WFH": WORKSTATUS,
    "work_status_other": WORKSTATUS,
    "none_working": BINARY_SIMPLE,
    "WFH_situation": [1, 2, 3, 4, 5],
    "weekday_occupancy": [-9, -3, -2, -1, 1, 2, 3],
    "weekday_occ_frequency": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7],
    "no_cars_vans": [-9, -3, -2, -1, 0, 1, 2, 3, 4],
    # -- Health --
    "health_condition": [-9, -3, -2, -1, 1, 2, 3, 4, 5, 6],
    "longterm_condition": [-9, -3, -2, -1, 1, 2, 3],
    "hh_others_longterm_condition": [-9, -3, -2, -1, 1, 2, 3, 4],
    "life_satisfaction": LIKERT0_10,
    "life_worthwhile_score": LIKERT0_10,
}

# ---------- Free-form numeric fields (sampled as an int/float, not a code list) ----------
# name -> (missing_codes, low, high) — a value is drawn uniformly from
# [low, high] unless one of the missing_codes is drawn instead (same
# missing/real weighting as _sample_value(), see _sample_numeric()).
NUMERIC_COUNT_VARS: Dict[str, Tuple[List[int], int, int]] = {
    "no_occupants": ([], 1, 6),
    "no_occupants_under_16": ([-9, -3, -2, -1], 0, 4),
    "no_occupants_over_16": ([-9, -3, -2, -1], 1, 5),
    "no_occupants_over_65": ([-9, -3, -2, -1], 0, 3),
    "no_occupants_over_65_all": ([-2], 0, 1),
    "no_rooms": ([-2, -4], 1, 10),
    "no_bedrooms": ([-2, -4], 0, 6),
    "no_house_share": ([-9, -2], 0, 3),
    "degree_holders": ([-9, -3, -2], 0, 4),
    "degree_prefernosay": ([], 0, 4),
    "no_cars_vans_prefernosay": ([-9, -3, -2, -1], 0, 3),
    "heating_hours": ([-9, -3, -2, -1], 0, 24),
    "dk_heating_hours": ([-9, -3, -2, -1], 0, 0),
    "age_group_0_5": ([], 0, 5),
    "age_group_6_15": ([], 0, 5),
    "age_group_0_15": ([], 0, 5),
    "age_group_16_24": ([], 0, 4),
    "age_group_25_44": ([], 0, 4),
    "age_group_45_64": ([], 0, 4),
    "age_group_65_74": ([], 0, 3),
    "age_group_75_84": ([], 0, 2),
    "age_group_85plus": ([], 0, 2),
    **{
        name: ([-9, -3, -2, -1], 0, 3)
        for name in (
            "male_0_15", "male_16_24", "male_25_44", "male_45_64",
            "male_65_74", "male_75_84", "male_85plus",
            "female_0_15", "female_16_24", "female_25_44", "female_45_64",
            "female_65_74", "female_75_84", "female_85plus",
        )
    },
}

# thermostat_C: real-valued temperature, not an integer code list.
THERMOSTAT_C_RANGE = (15.0, 24.5)

# Meta columns handled directly in _build_harmonised_survey_row(), not part
# of the generic per-variable sampling loop (and excluded from
# var_order/presence lookup).
META_VARS = {"puprn", "survey", "completed_survey", "date_completed", "date_completed_string", "wave"}

# Sentinel written for a variable not asked in the row's survey — see
# "Step 4 – Construction of the MasterSERL Dataframe" in the harmonisation doc.
NOT_ASKED_SENTINEL = 999999

# survey occurrence label -> which master-mapping source-survey column
# ('Sign Up Survey' / '2023 Survey' / '2025 Survey') governs whether each
# variable was asked in that occurrence.
_OCCURRENCE_SOURCE_KEY = {
    "Sign Up": "Sign Up Survey",
    "2023": "2023 Survey",
    "2025w_1": "2025 Survey",
    "2025w_2_3": "2025 Survey",
}
_HARMONISED_WAVE_CHOICES = {
    "Sign Up": ["su_Wave1", "su_Wave2", "su_Wave3"],
    "2023": ["23_Wave1", "23_Wave2", "23_Wave3"],
    "2025w_1": ["25_Wave1"],
    "2025w_2_3": ["25_Wave2", "25_Wave3"],
}
# Plausible completion-date windows per occurrence — see "Data Collection" in
# the harmonisation doc (Sign Up 2019-2021, 2023 survey Feb 2023, 2025 survey
# wave 1 Mar-Jun 2025, waves 2-3 Nov 2025-Apr 2026).
_DATE_WINDOWS = {
    "Sign Up": (pd.Timestamp(2019, 9, 1), pd.Timestamp(2021, 3, 31)),
    "2023": (pd.Timestamp(2023, 2, 2), pd.Timestamp(2023, 4, 30)),
    "2025w_1": (pd.Timestamp(2025, 3, 14), pd.Timestamp(2025, 6, 30)),
    "2025w_2_3": (pd.Timestamp(2025, 11, 1), pd.Timestamp(2026, 4, 30)),
}


def _build_var_index(
    master_mapping: List[Dict],
) -> Tuple[List[str], Dict[str, Dict[str, Optional[str]]], Dict[str, str]]:
    """
    From the rows of serl_master_mapping_edition09.csv (see
    utils.read_master_mapping), build:
      - var_order: harmonised variable names in mapping-file order, excluding
        META_VARS and 'puprn' (those are handled directly in
        _build_harmonised_survey_row()).
      - raw_columns: {var_name: {'Sign Up Survey': raw_col_or_None,
        '2023 Survey': raw_col_or_None, '2025 Survey': raw_col_or_None}} —
        the original column name in that raw survey (None if it never asked
        this question — this is also what decides the 999999 sentinel, and
        what _build_harmonised_survey_row() looks up in the corresponding
        raw dataframe). A variable appearing more than once in the mapping
        (e.g. the upstream sheet's duplicate 'no_bedrooms' row) keeps the
        first non-null raw column per source rather than the later row
        silently overwriting it.
      - domain: {var_name: domain} (first non-null domain seen), unused for
        sampling but kept for a future dictionary/domain-grouped output.
    """
    var_order: List[str] = []
    raw_columns: Dict[str, Dict[str, Optional[str]]] = {}
    domain: Dict[str, str] = {}
    for row in master_mapping:
        name = row.get("master_var_name")
        if not name or name in META_VARS or name == "puprn":
            continue
        row_raw = {key: row.get(key) or None for key in ("Sign Up Survey", "2023 Survey", "2025 Survey")}
        if name not in raw_columns:
            var_order.append(name)
            raw_columns[name] = row_raw
        else:
            raw_columns[name] = {
                key: raw_columns[name][key] or row_raw[key] for key in row_raw
            }
        if row.get("domain") and name not in domain:
            domain[name] = row["domain"]
    return var_order, raw_columns, domain


def _sample_value(rnd: random.Random, codes: Sequence) -> object:
    """
    Sample one harmonised code from `codes`, splitting into a "missing" bucket
    (values in MISSING_CODE_SET) and a "real answer" bucket, each split
    uniformly within itself; the missing bucket gets 12% combined weight
    when both are present (mirrors the low-but-nonzero missingness rate
    used throughout the existing raw-survey generators).
    """
    missing = [c for c in codes if c in MISSING_CODE_SET]
    real = [c for c in codes if c not in MISSING_CODE_SET]
    if missing and real:
        return rnd.choice(missing) if rnd.random() < 0.12 else rnd.choice(real)
    return rnd.choice(real or missing)


def _sample_numeric(rnd: random.Random, missing_codes: List[int], low: int, high: int) -> object:
    if missing_codes and rnd.random() < 0.08:
        return rnd.choice(missing_codes)
    return rnd.randint(low, high)


def sample_field_independent(rnd: random.Random, master_var_name: str) -> object:
    """
    Sample one field's value from its own coding template/numeric range,
    with no reference to any raw survey cell. Shared by: this module's
    fallback path (no raw value to reuse — see _recode_raw_value()) and
    the raw 2025 survey builder below (which has no notion of "harmonised
    vs raw" at all — every one of its cells is sampled this way).
    """
    if master_var_name == "thermostat_C":
        low, high = THERMOSTAT_C_RANGE
        return round(rnd.uniform(low, high), 1)
    if master_var_name in NUMERIC_COUNT_VARS:
        missing_codes, low, high = NUMERIC_COUNT_VARS[master_var_name]
        return _sample_numeric(rnd, missing_codes, low, high)
    codes = TEMPLATE_OVERRIDES.get(master_var_name, BINARY_FULL)
    return _sample_value(rnd, codes)


def _decide_participation(rnd: random.Random) -> List[str]:
    """
    Decide which survey occurrences a PUPRN appears in. Every PUPRN has a
    Sign Up row (the root recruitment survey); 2023 and 2025 participation
    are independent add-ons, reflecting that both later surveys were sent
    only to the existing Sign Up pool (see "Data Collection" in the
    harmonisation doc) — so coverage is uneven across PUPRNs rather than
    every PUPRN appearing in all four occurrences. This governs only which
    occurrence *rows exist*; it's independent of _recode_raw_value(), which
    governs how an occurrence's cells are populated once it does exist (the
    three raw generators give every input PUPRN a row regardless).
    """
    occurrences = ["Sign Up"]
    if rnd.random() < 0.5:
        occurrences.append("2023")
    wave25 = rnd.choices(["none", "w1", "w2_3", "both"], weights=[45, 40, 12, 3])[0]
    if wave25 in ("w1", "both"):
        occurrences.append("2025w_1")
    if wave25 in ("w2_3", "both"):
        occurrences.append("2025w_2_3")
    return occurrences


def _index_by_puprn(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    return df.set_index("PUPRN", drop=False)


def _lookup_raw_value(indexed_df: Optional[pd.DataFrame], puprn: str, raw_col: Optional[str]) -> object:
    """
    Return the raw cell value for (puprn, raw_col) in indexed_df, or:
      - _NO_RAW_COLUMN if there's no dataframe, no raw_col, the column
        doesn't exist in it, or the puprn has no row — i.e. there's nothing
        to look up, so the caller should fall back to independent sampling.
      - None if the column and row both exist but the cell itself is blank
        (NaN) — a genuine "this PUPRN didn't answer this question in this
        raw survey" (often from that raw generator's own skip logic).
    """
    if indexed_df is None or not raw_col or raw_col not in indexed_df.columns:
        return _NO_RAW_COLUMN
    try:
        value = indexed_df.at[puprn, raw_col]
    except KeyError:
        return _NO_RAW_COLUMN
    return None if pd.isna(value) else value


def _missing_code_for(rnd: random.Random, master_var_name: str) -> Optional[object]:
    """A harmonised code representing "no response", for a raw cell that's
    genuinely blank — reusing whichever missing code(s) the variable's own
    template/numeric range already defines. Returns None (write a blank
    harmonised cell too) if the variable has no missing-code concept at all
    (e.g. thermostat_C) — deliberately not a fabricated "real" answer."""
    if master_var_name == "thermostat_C":
        return None
    if master_var_name in NUMERIC_COUNT_VARS:
        missing_codes, _low, _high = NUMERIC_COUNT_VARS[master_var_name]
        return rnd.choice(missing_codes) if missing_codes else None
    codes = TEMPLATE_OVERRIDES.get(master_var_name, BINARY_FULL)
    missing = [c for c in codes if c in MISSING_CODE_SET]
    return rnd.choice(missing) if missing else None


def _recode_raw_value(rnd: random.Random, master_var_name: str, raw_value: object) -> object:
    """
    Turn a real (non-blank) raw cell value into a harmonised one for
    master_var_name:
      - thermostat_C / NUMERIC_COUNT_VARS: numeric fields have no fixed code
        list to validate against, so any numeric raw value is reused as-is.
      - Otherwise: a raw value already in MISSING_CODE_SET, or already a
        legal code in the variable's own template, is reused unchanged —
        this covers the common case where a raw survey's own coding for a
        simple Yes/No-style field already happens to match the harmonised
        scheme (see this section's module-level comment above). Anything
        else means the raw survey answered the question with a code we
        can't reliably map (no per-field raw-to-harmonised table is
        available) — sampled from the template's real (non-missing) codes
        instead of a fabricated 1:1 mapping, since we at least know from
        the raw cell that *something* was answered, not blank.
    """
    if master_var_name == "thermostat_C" or master_var_name in NUMERIC_COUNT_VARS:
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return raw_value
        return sample_field_independent(rnd, master_var_name)

    codes = TEMPLATE_OVERRIDES.get(master_var_name, BINARY_FULL)
    if raw_value in MISSING_CODE_SET or raw_value in codes:
        return raw_value
    real_codes = [c for c in codes if c not in MISSING_CODE_SET]
    return rnd.choice(real_codes or codes)


def _build_harmonised_survey_row(
    puprn: str,
    occurrence: str,
    var_order: List[str],
    raw_columns: Dict[str, Dict[str, Optional[str]]],
    raw_dfs: Dict[str, Optional[pd.DataFrame]],
    rnd: random.Random,
) -> Dict[str, object]:
    source_key = _OCCURRENCE_SOURCE_KEY[occurrence]
    indexed_df = raw_dfs.get(source_key)
    start, end = _DATE_WINDOWS[occurrence]
    span_days = max(1, (end - start).days)
    date_completed = start + pd.Timedelta(days=rnd.randint(0, span_days))

    row: Dict[str, object] = {
        "PUPRN": puprn,
        "survey": occurrence,
        "wave": rnd.choice(_HARMONISED_WAVE_CHOICES[occurrence]),
        "completed_survey": True,
        "date_completed": date_completed.strftime("%Y-%m-%d"),
        "date_completed_string": date_completed.strftime("%d %B %Y"),
    }

    for var in var_order:
        raw_col = raw_columns.get(var, {}).get(source_key)
        if not raw_col:
            row[var] = NOT_ASKED_SENTINEL
            continue
        raw_value = _lookup_raw_value(indexed_df, puprn, raw_col)
        if raw_value is _NO_RAW_COLUMN:
            row[var] = sample_field_independent(rnd, var)
        elif raw_value is None:
            missing_code = _missing_code_for(rnd, var)
            row[var] = missing_code if missing_code is not None else None
        else:
            row[var] = _recode_raw_value(rnd, var, raw_value)
    return row


def build_harmonised_survey_dataframe(
    puprns: Sequence[str],
    master_mapping: List[Dict],
    seed: int,
    sign_up_df: Optional[pd.DataFrame] = None,
    survey_2023_df: Optional[pd.DataFrame] = None,
    survey_2025_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Generate the mock MasterSERL harmonised survey dataframe: one row per
    PUPRN per survey occurrence it participated in (see
    _decide_participation()), with `survey` identifying which occurrence
    ('Sign Up', '2023', '2025w_1', or '2025w_2_3') and every harmonised
    variable either NOT_ASKED_SENTINEL (999999, that occurrence's survey
    never asked it — per `master_mapping`), derived from that PUPRN's real
    cell in the matching raw dataframe (see _recode_raw_value()), or —
    when no raw dataframe is supplied for that survey — sampled
    independently from the same coding templates, exactly as before this
    read from the raw generators.

    sign_up_df / survey_2023_df / survey_2025_df should be the output of
    generate_serl_survey() / generate_follow_up_survey() /
    generate_2025_survey() respectively (each keyed on their own 'PUPRN'
    column) for the *same* `puprns`; SERLContextualVariablesGenerator always
    supplies all three. Passing None for one (or all) falls back to
    independent sampling for that survey's cells only.
    """
    var_order, raw_columns, _domain = _build_var_index(master_mapping)
    raw_dfs: Dict[str, Optional[pd.DataFrame]] = {
        "Sign Up Survey": _index_by_puprn(sign_up_df),
        "2023 Survey": _index_by_puprn(survey_2023_df),
        "2025 Survey": _index_by_puprn(survey_2025_df),
    }
    rnd = random.Random(seed + 900)

    rows: List[Dict[str, object]] = []
    for puprn in puprns:
        for occurrence in _decide_participation(rnd):
            rows.append(_build_harmonised_survey_row(puprn, occurrence, var_order, raw_columns, raw_dfs, rnd))

    columns = ["PUPRN", "survey", "wave", "completed_survey", "date_completed", "date_completed_string"] + var_order
    return pd.DataFrame(rows, columns=columns)


# =====================================================================
# Raw 2025 SERL Observatory survey (serl_2025_follow_up_survey_data — named
# to pair with serl_2023_follow_up_survey_data, since both are the same
# kind of post-recruitment survey, distinguished only by year).
#
# Unlike the harmonised survey above, this is meant to look like the *raw*
# per-respondent survey extract SERL would produce from the paper/online
# instrument itself — one row per PUPRN, columns named after the real
# question codes (Q15_2, Q1R1C1, ...), not the harmonised variable names.
#
# Grounded in two real sources:
#   - docs/documentation/SERL/edition09/serl_2025_survey_PaperSurveyFinalCopy.pdf
#     — the actual paper questionnaire (Q0-Q48), giving real question text,
#     real answer options, and real skip logic ("GO TO Qxx").
#   - data/reference/edition09/serl_master_mapping_edition09.csv — maps each
#     question/sub-item's raw code to its harmonised master_var_name (e.g.
#     Q15_2 -> heating_gas), letting this reuse the coding templates above
#     (sample_field_independent() etc.) rather than inventing a second,
#     parallel set of value codes. That reuse is reasonable here
#     specifically because the harmonisation doc states the 2025 survey was
#     already collected as numeric codes (unlike 2023, which was free
#     text) — so its raw and harmonised codings are expected to be close,
#     not because raw and harmonised coding are interchangeable in general.
#
# Scope (v1, matching the harmonised survey above): each field sampled
# independently *except* for the survey's own documented skip logic (see
# _SKIP_GROUPS below), which is enforced — a skipped question is left blank
# (None -> NaN/empty cell), mirroring how the harmonisation doc describes raw
# blank cells being recoded (e.g. its Table 4/6/7). This is simpler than, and
# distinct from, the cross-field *value*-consistency rules in section 3.4
# (e.g. a "present" flag matching its own "added/replaced" flag), which are
# still deferred, same as above.
# =====================================================================

# Each entry: controlling master_var_name -> (predicate over its sampled
# value, [dependent master_var_names to blank when the predicate is True]).
# Transcribed directly from the paper survey's "GO TO Qxx" instructions.
_SKIP_GROUPS: List[Tuple[str, "callable", List[str]]] = [
    # Q23 "can you keep warm?" Yes/Don't know -> skip Q24 (why not warm).
    ("thermal_comfort", lambda v: v in (1, -1), [
        "heating_difficulty_home", "heating_affordability", "no_comfort_answer",
        "none_above_comfort", "other_reason_comfort",
    ]),
    # Q32 "how many cars/vans" == 0 -> skip Q33-Q36 (parking, EVs, charging).
    ("no_cars_vans", lambda v: v == 0, [
        "private_garage_present", "space_car_park_present", "no_allocated_park_space",
        "controlled_street_parking", "street_parking", "other_parking_service",
        "no_EVs", "EV_charge_location", "EV_charge_time",
    ]),
    # Q34 "how many are plug-in electric" == 0 -> skip Q35-Q36.
    ("no_EVs", lambda v: v == 0, ["EV_charge_location", "EV_charge_time"]),
    # Q35 charging location: only "at home" (1, 2) options lead on to Q36;
    # every other option (near home/work/public/elsewhere/don't know) skips it.
    ("EV_charge_location", lambda v: v not in (1, 2), ["EV_charge_time"]),
    # Q40 "do you have a long-term condition" No/Prefer not to say -> skip Q41.
    ("longterm_condition", lambda v: v in (2, 3), [
        "vision", "hearing", "learning_difficulty", "mobility", "breathing_problems",
        "heart_disease", "mental_health_probs", "other_chronic_illness",
        "dk_illness", "prefernosay_illness",
    ]),
    # Q42 "does anyone else" No/Not applicable/Prefer not to say -> skip Q43.
    ("hh_others_longterm_condition", lambda v: v in (2, 3, 4), [
        "hh_others_vision", "hh_others_hearing", "hh_others_learning_difficulty",
        "hh_others_mobility", "hh_others_breathing_problems", "hh_others_heart_disease",
        "hh_others_mental_probs", "hh_others_other_chronic_illness",
        "dk_hh_others_illness", "prefernosay_hh_others_illness",
    ]),
]

_SURVEY_2025_WAVE_CHOICES = ["Wave1", "Wave2", "Wave3"]
_SURVEY_2025_DATE_WINDOW = (pd.Timestamp(2025, 3, 14), pd.Timestamp(2026, 4, 30))
# Real proportions aren't documented; matches the harmonisation doc's
# description of 2025 being predominantly online with a minority postal/
# printed-copy channel (58 paper responses out of wave 1's 1,944, plus the
# wave-1 printed-copy reminder mailing to non-responders).
_COLLECTION_METHOD_WEIGHTS = {"Online": 85, "Postal": 15}

_Q_NUM_RE = re.compile(r"Q(\d+)")


def _q_sort_key(raw_code: str) -> Tuple[int, str]:
    """Order raw columns by questionnaire position (Q1, Q2, ..., Q10, ...)
    rather than the master-mapping file's domain-grouped order, since a real
    raw survey extract's columns follow the questionnaire, not a domain
    taxonomy invented for the harmonised file."""
    m = _Q_NUM_RE.match(raw_code)
    return (int(m.group(1)) if m else 9999, raw_code)


def _build_2025_columns(master_mapping: List[Dict]) -> List[Tuple[str, str]]:
    """Return [(master_var_name, raw_2025_column)] for every variable the
    2025 survey asked (per its 'the corresponding value in the mapping's
    "2025 Survey" column), excluding 'puprn', sorted into questionnaire
    order. Skips the one variable (thermostat_C) whose mapped raw code
    ("Q23AR1 / Q23AR2") doesn't correspond to any question in this specific
    paper copy - see the module-level "thermostat_C" note.
    """
    pairs: List[Tuple[str, str]] = []
    seen: set = set()
    for row in master_mapping:
        name = row.get("master_var_name")
        raw = row.get("2025 Survey")
        if not name or not raw or name == "puprn" or name in seen:
            continue
        if "/" in raw:
            # e.g. thermostat_C -> "Q23AR1 / Q23AR2": not present in this
            # paper copy (may be online-only) - fall back to the generic
            # numeric sampler under its own harmonised name instead of a
            # fabricated raw column name.
            continue
        seen.add(name)
        pairs.append((name, raw))
    pairs.sort(key=lambda pair: _q_sort_key(pair[1]))
    return pairs


def _build_2025_survey_row(puprn: str, columns: List[Tuple[str, str]], rnd: random.Random) -> Dict[str, object]:
    start, end = _SURVEY_2025_DATE_WINDOW
    recorded_date = start + pd.Timedelta(days=rnd.randint(0, max(1, (end - start).days)))

    row: Dict[str, object] = {
        "PUPRN": puprn,
        "Survey_wave": rnd.choice(_SURVEY_2025_WAVE_CHOICES),
        "Recorded_date": recorded_date.strftime("%Y-%m-%d"),
        "Collection_method": rnd.choices(
            list(_COLLECTION_METHOD_WEIGHTS), weights=list(_COLLECTION_METHOD_WEIGHTS.values())
        )[0],
    }

    sampled: Dict[str, object] = {}
    for master_var_name, raw_col in columns:
        sampled[master_var_name] = sample_field_independent(rnd, master_var_name)

    blanked: set = set()
    for controller, predicate, dependents in _SKIP_GROUPS:
        value = sampled.get(controller)
        if value is not None and predicate(value):
            blanked.update(dependents)

    for master_var_name, raw_col in columns:
        row[raw_col] = None if master_var_name in blanked else sampled[master_var_name]
    return row


def build_2025_survey_dataframe(
    puprns: Sequence[str],
    master_mapping: List[Dict],
    seed: int,
) -> pd.DataFrame:
    """
    Generate the mock raw 2025 SERL Observatory survey: one row per input
    PUPRN, columns named after the paper questionnaire's own question codes
    (see _build_2025_columns()), values sampled from the same coding
    templates as the harmonised survey above, with the paper survey's
    documented skip logic enforced (see _SKIP_GROUPS).
    """
    columns = _build_2025_columns(master_mapping)
    rnd = random.Random(seed + 950)

    rows = [_build_2025_survey_row(puprn, columns, rnd) for puprn in puprns]

    column_order = ["PUPRN", "Survey_wave", "Recorded_date", "Collection_method"] + [
        raw_col for _name, raw_col in columns
    ]
    return pd.DataFrame(rows, columns=column_order)


class SERLContextualVariablesGenerator:
    """
    Generates contextual datasets:
      - EPC data
      - SERL survey data
      - Participant summary
      - Follow-up survey
      - List of exporter PUPRNs
    Uses shared config and PUPRN utilities to align with smart-meter HH data.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        n_households: Optional[int] = None,
        year: Optional[int] = None,
        outfolder: Optional[str] = None,
        seed: Optional[int] = None,
        puprn_list_path: Optional[str] = None,
        write_puprn_list: bool = False,
        traits_path: Optional[str] = None,
        climate_dir: Optional[str] = None,
        format: Optional[str] = None,
    ):
        cfg = read_config(config_path)

        # Core parameters
        self.n_households = n_households or cfg.get("n_households", 100)
        self.year = year or cfg.get("year", 2023)

        # RNG
        self.seed = seed if seed is not None else cfg.get("seed", 42)
        seed_random(self.seed)

        # Edition & filenames
        fcfg = cfg.get("filenames", {})
        self.names = OutputNames(
            epc=fcfg.get("epc", "serl_epc_data"),
            survey=fcfg.get("survey", "serl_survey_data"),
            covid19_survey=fcfg.get("covid19_survey", "serl_covid19_survey_data"),
            summary=fcfg.get("summary", "serl_participant_summary"),
            followup_survey=fcfg.get("followup_survey", "serl_2023_follow_up_survey_data"),
            harmonised_survey=fcfg.get("harmonised_survey", "masterserl_surveys"),
            survey_2025=fcfg.get("survey_2025", "serl_2025_follow_up_survey_data"),
            exporters_prefix=fcfg.get("exporters_prefix", "Elec"),
        )
        self.edition = str(cfg.get("edition", "")).strip() or None
        # format is bound to the edition (see edition.py), not an independent
        # config key; the `format` constructor parameter is a programmatic/
        # test-only escape hatch, same as in generator_smartmeter.py.
        edition_def = get_edition(self.edition or "08")
        self.format = format or edition_def.format

        # Household traits (PV/HP/EV) — load from pre-generated CSV
        if not traits_path:
            traits_path = cfg.get("household_traits_path")
        if not traits_path:
            # Try default location in mock_internal
            from .paths import MOCK_INTERNAL_DIR
            traits_path = MOCK_INTERNAL_DIR / "household_traits.csv"
        self.traits_path = str(traits_path)

        # Household-trait fractions are used during household traits generation,
        # not directly in this contextual generator.

        # Survey dictionary paths — default to the reference dictionaries filed
        # under this edition's reference folder (data/reference/edition<N>/),
        # named for whichever edition their content actually matches (see
        # edition_def.dictionary_source_edition — edition08 still uses
        # edition07-named files; edition09 uses its own).
        ref_dir = reference_dir_for(self.edition or "08")
        dict_edition = edition_def.dictionary_source_edition
        self.survey_dictionary_path = cfg.get(
            "survey_dictionary_path",
            str(ref_dir / f"serl_survey_data_dictionary_edition{dict_edition}.csv"),
        )
        self.followup_survey_dictionary_path = cfg.get(
            "followup_survey_dictionary_path",
            str(ref_dir / f"serl_follow_up_survey_data_dictionary_edition{dict_edition}.csv"),
        )
        self.covid19_survey_dictionary_path = cfg.get(
            "covid19_survey_dictionary_path",
            str(ref_dir / f"serl_covid19_survey_data_dictionary_edition{dict_edition}.csv"),
        )
        # MasterSERL harmonised-survey mapping — see this module's
        # "MasterSERL harmonised survey" / "Raw 2025 SERL Observatory
        # survey" sections above. Only populated under
        # data/reference/edition09/ today; generate_harmonised_survey() /
        # generate_2025_survey() are opt-in (generate.harmonised_survey /
        # generate.survey_2025, default False — see
        # scripts/generate_mock_data.py) precisely because it isn't
        # available for every edition.
        self.master_mapping_path = cfg.get(
            "master_mapping_path",
            str(ref_dir / f"serl_master_mapping_edition{dict_edition}.csv"),
        )

        # EPC generated-fields list — which of the official EPC dictionary's
        # fields (data/reference/edition<N>/serl_epc_data_dictionary_edition07.csv,
        # 103 variables) this edition's mock generator actually produces.
        # Not the same file as the dictionary itself: the dictionary is
        # SERL's real documentation (used for cross-reference in
        # docs/05_epc_reference.md); this is our own record of what's
        # implemented, so a future edition can add a field by adding a row
        # here instead of editing generate_epc() to change the field list.
        self.epc_generated_fields_path = cfg.get(
            "epc_generated_fields_path",
            str(ref_dir / "serl_epc_generated_fields.csv"),
        )

        # Which England/Wales LSOA boundary vintage to sample the participant
        # summary's LSOA field from ('2011' or '2021'); Scotland's Data Zones
        # are unaffected — see read_lsoa_codes() in utils.py.
        self.lsoa_vintage = str(cfg.get("lsoa_vintage", "2021"))

        # PUPRN: load from master list or generate deterministically
        self.puprn_list_path = puprn_list_path or cfg.get("puprn_list_path")

        if self.puprn_list_path:
            puprns = load_puprn_list_csv(self.puprn_list_path)
            if len(puprns) < self.n_households:
                raise ValueError("PUPRN list smaller than n_households.")
            self.puprns = puprns[: self.n_households]
        else:
            self.puprns = make_alphanumeric_ids_ordered(self.n_households, length=8, seed=self.seed)

        # Load household traits from CSV
        traits_df = load_household_traits(self.traits_path)
        self._pv_households = set(traits_df[traits_df['has_pv'] == 1].index.tolist())
        self._hp_households = set(traits_df[traits_df['has_hp'] == 1].index.tolist())
        self._ev_households = set(traits_df[traits_df['has_ev'] == 1].index.tolist())
        self._solar_thermal_households = (
            set(traits_df[traits_df['has_solar_thermal'] == 1].index.tolist())
            if 'has_solar_thermal' in traits_df.columns else set()
        )

        # ERA5 grid-cell assignment — used in participant summary.
        # Prefer sampling from grid cells that actually appear in the downloaded
        # climate CSVs so every assigned cell is guaranteed to be joinable.
        # Falls back to the geometric approach if no CSVs exist yet.
        wcfg = cfg.get("weather", {})
        if not climate_dir:
            default_climate_dir = MOCK_DIR / mock_climate_dirname(self.edition or "08")
            climate_dir = wcfg.get("output_dir", str(default_climate_dir))
        climate_dir = Path(climate_dir)
        available_cells = self._read_climate_grid_cells(climate_dir)
        if available_cells:
            rng = np.random.default_rng(self.seed)
            chosen = rng.choice(available_cells, size=len(self.puprns))
            self._grid_cells: Dict[str, str] = {
                puprn: str(cell) for puprn, cell in zip(self.puprns, chosen)
            }
        else:
            area = wcfg.get("area", [60.0, -8.0, 49.0, 2.0])
            grid = wcfg.get("grid", [0.25, 0.25])
            self._grid_cells = self._assign_grid_cells(
                puprns=list(self.puprns),
                area=area,
                grid=grid,
                seed=self.seed,
            )

        # Nation assignment (England & Wales vs Scotland) — shared across EPC
        # generation and the participant summary so a PUPRN's epcVersion and
        # Region never contradict each other. Matches the real edition07
        # split (~91.3% England and Wales / ~8.6% Scotland).
        nation_rnd = random.Random(self.seed + 700)
        self._nation: Dict[str, str] = {
            puprn: nation_rnd.choices(
                ['England and Wales', 'Scotland'], weights=[91.3, 8.6], k=1
            )[0]
            for puprn in self.puprns
        }

    # ---------- Grid-cell assignment ----------
    @staticmethod
    def _read_climate_grid_cells(climate_dir: Path) -> List[str]:
        """Return sorted unique grid_cell values from the first climate CSV found.

        ERA5 grid coverage is identical across months, so reading one file is
        sufficient and avoids scanning every monthly CSV.  Returns [] when no
        CSVs are present (climate data not yet downloaded).
        """
        csv_files = sorted(climate_dir.glob("*.csv"))
        if not csv_files:
            return []
        try:
            df = pd.read_csv(csv_files[0], usecols=["grid_cell"])
            return sorted(df["grid_cell"].dropna().unique().tolist())
        except Exception:
            return []

    @staticmethod
    def _assign_grid_cells(
        puprns: List[str],
        area: List[float],
        grid: List[float],
        seed: int,
    ) -> Dict[str, str]:
        """Geometric fallback: assign each PUPRN to a randomly sampled ERA5 grid cell.

        Used only when climate CSVs are not yet available.  Locations are drawn
        uniformly within *area* and snapped to the nearest grid point.  The
        ``grid_cell`` identifier uses the same zero-padded ``<col>_<row>`` format
        as the SERL climate data schema (e.g. ``03_01``).
        """
        north, west, south, east = (
            float(area[0]), float(area[1]), float(area[2]), float(area[3])
        )
        lat_step, lon_step = float(grid[0]), float(grid[1])
        max_row = math.floor((north - south) / lat_step)
        max_col = math.floor((east - west) / lon_step)

        rng = np.random.default_rng(seed)
        raw_lats = rng.uniform(south, north, size=len(puprns))
        raw_lons = rng.uniform(west, east, size=len(puprns))

        result: Dict[str, str] = {}
        for puprn, raw_lat, raw_lon in zip(puprns, raw_lats, raw_lons):
            row = max(0, min(round((north - float(raw_lat)) / lat_step), max_row))
            col = max(0, min(round((float(raw_lon) - west) / lon_step), max_col))
            result[puprn] = f"{col:02d}_{row:02d}"
        return result

    # ---------- File naming ----------
    def _fname(self, basename: str) -> str:
        return with_edition_suffix(basename, self.edition)

    # ---------- EPC ----------
    def _epc_fields(self) -> List[str]:
        return read_epc_generated_fields(self.epc_generated_fields_path)

    def generate_epc(self) -> pd.DataFrame:
        fields = self._epc_fields()
        energy_ratings = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        property_types = ['House', 'Flat', 'Maisonette', 'Bungalow']
        built_forms = ['Detached', 'Semi-Detached', 'Mid-Terrace', 'End-Terrace',
                       'Enclosed Mid-Terrace', 'Enclosed End-Terrace']
        efficiency_ratings = ['Very Good', 'Good', 'Average', 'Poor', 'Very Poor']
        # floorEnergyEff: mostly a single rating, occasionally "N/A". Multi-
        # element "N/A | N/A" values only occur on Scotland-schema records —
        # per the SERL EPC technical documentation, Scotland reports each
        # element of a multi-part floor separately, joined by "|", whereas
        # England & Wales always reports a single element.
        floor_energy_eff_singles = ['Average', 'Good', 'N/A', 'Poor', 'Very Good', 'Very Poor']
        floor_energy_eff_single_weights = [19, 19, 4, 19, 19, 19]
        floor_energy_eff_combos = ['N/A | N/A', 'N/A | N/A | N/A']
        floor_energy_eff_combo_weights = [0.5, 0.5]
        # roofEnergyEff: same single-vs-Scotland-multi-element convention as
        # floorEnergyEff above. Single ratings dominate; multi-element
        # combinations (including the mixed "N/A | Very Poor" case) only
        # occur for Scotland rows.
        _roof_ratings = ['Average', 'Good', 'Poor', 'Very Good', 'Very Poor']
        roof_energy_eff_singles = list(_roof_ratings)
        roof_energy_eff_single_weights = [17] * len(roof_energy_eff_singles)
        roof_energy_eff_pairs = [
            f"{a} | {b}"
            for i, a in enumerate(_roof_ratings)
            for b in _roof_ratings[i:]
        ]
        roof_energy_eff_triples = [
            f"{a} | {b} | {c}"
            for i, a in enumerate(_roof_ratings)
            for j, b in enumerate(_roof_ratings[i:], start=i)
            for c in _roof_ratings[j:]
        ]
        roof_energy_eff_combos = roof_energy_eff_pairs + roof_energy_eff_triples + ['N/A | Very Poor']
        roof_energy_eff_combo_weights = (
            [1] * len(roof_energy_eff_pairs) + [0.3] * len(roof_energy_eff_triples) + [0.5]
        )
        # glazedArea: England & Wales EPCs describe glazed area with text
        # labels; Scotland EPCs report a numeric band code with different
        # value semantics (0=New dwelling, 1=Typical, 2=More than typical,
        # 4=Much more than typical, 5=Much less than typical), per the
        # Scotland EPC data dictionary.
        glazed_area_ew_values = [
            'Less Than Typical', 'More Than Typical', 'Much Less Than Typical',
            'Much More Than Typical', 'NO DATA!', 'Normal',
        ]
        glazed_area_ew_weights = [6, 6, 3, 3, 2, 70]
        glazed_area_scotland_values = ['0', '1', '2', '4', '5', 'NO DATA!']
        glazed_area_scotland_weights = [2, 70, 10, 5, 5, 2]
        yes_no_flags = ['Y', 'N']
        fuel_types = ['mains gas (not community)', 'electricity', 'oil', 'solid fuel']
        tenure_types = ['owner-occupied', 'rented (private)', 'rented (social)']
        mech_vent_types = ['natural', 'mechanical, extract only',
                           'mechanical, supply and extract', 'NO DATA!']
        heat_loss_types = ['no corridor', 'heated corridor', 'unheated corridor', 'NO DATA!']
        # constructionAgeBand boundaries differ by nation, per each nation's
        # EPC data dictionary (Scotland's bands are offset from E&W's). In the real
        # edition08 SERL EPC data, England & Wales values carry an "England and
        # Wales: " prefix (e.g. "England and Wales: 1950-1966"); Scotland's values
        # do not carry a nation prefix. Both nations can also be NULL, INVALID, or
        # "NO DATA!" instead of a band.
        age_bands_ew = ['England and Wales: before 1900', 'England and Wales: 1900-1929',
                        'England and Wales: 1930-1949', 'England and Wales: 1950-1966',
                        'England and Wales: 1967-1975', 'England and Wales: 1976-1982',
                        'England and Wales: 1983-1990', 'England and Wales: 1991-1995',
                        'England and Wales: 1996-2002', 'England and Wales: 2003-2006',
                        'England and Wales: 2007-2011', 'England and Wales: 2012 onwards',
                        'NULL', 'INVALID', 'NO DATA!']
        age_bands_ew_weights = [8] * 12 + [1, 1, 1]
        age_bands_scotland = ['before 1919', '1919-1929', '1930-1949', '1950-1964',
                              '1965-1975', '1976-1983', '1984-1991', '1992-1998',
                              '1999-2002', '2003-2007', '2008-2011', '2012 onwards',
                              'NULL', 'INVALID', 'NO DATA!']
        age_bands_scotland_weights = [8] * 12 + [1, 1, 1]
        transaction_types = ['marketed sale', 'rental', 'new dwelling',
                             'following green deal', 'assessment for green deal']

        data = []
        rnd = random.Random(self.seed + 100)

        for puprn in self.puprns:
            nation = self._nation[puprn]
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in fields[1:]:
                fl = field.lower()
                if 'rating' in fl and 'efficiency' not in fl:
                    row[field] = np.nan if rnd.random() < 0.3 else rnd.choice(energy_ratings)
                elif 'efficiency' in fl:
                    row[field] = rnd.randint(20, 100)
                elif field == 'propertyType':
                    row[field] = rnd.choice(property_types)
                elif field == 'builtForm':
                    row[field] = rnd.choice(built_forms)
                elif field == 'epcVersion':
                    row[field] = nation
                elif field == 'mechanicalVentilation':
                    row[field] = rnd.choice(mech_vent_types)
                elif field == 'heatLossCorridor':
                    row[field] = rnd.choice(heat_loss_types)
                elif field == 'constructionAgeBand':
                    if nation == 'Scotland':
                        row[field] = rnd.choices(age_bands_scotland, weights=age_bands_scotland_weights, k=1)[0]
                    else:
                        row[field] = rnd.choices(age_bands_ew, weights=age_bands_ew_weights, k=1)[0]
                elif field == 'transactionType':
                    row[field] = rnd.choice(transaction_types)
                elif field == 'mainFuel':
                    row[field] = rnd.choice(fuel_types)
                elif field == 'tenure':
                    row[field] = rnd.choice(tenure_types)
                elif field == 'totalFloorArea':
                    row[field] = np.nan if rnd.random() < 0.3 else round(rnd.uniform(30.0, 300.0), 1)
                elif field == 'floorHeight':
                    row[field] = round(rnd.uniform(2.0, 3.5), 2)
                elif field == 'mainHeatingControls':
                    row[field] = rnd.randint(1000, 9999)
                elif field == 'lowEnergyLighting':
                    row[field] = rnd.randint(0, 100)
                elif field == 'flatTopStorey':
                    row[field] = rnd.choice(yes_no_flags)
                elif field == 'solarWaterHeatingFlag':
                    row[field] = 'Y' if puprn in self._solar_thermal_households else 'N'
                elif field == 'photoSupply':
                    pct = rnd.randint(0, 50)
                    row[field] = f"Array: Roof Area: {pct}%; Connection: not applicable (FGHRS or no PV); |"
                elif 'datetime' in fl:
                    base_dt = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_dt + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y %H:%M')
                elif 'date' in fl:
                    base_date = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_date + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y')
                elif 'emiss' in fl:
                    row[field] = round(rnd.uniform(0.5, 10.0), 1)
                elif 'consumption' in fl:
                    row[field] = rnd.randint(50, 500)
                elif 'environmentimpact' in fl:
                    row[field] = rnd.randint(20, 100)
                elif 'cost' in fl:
                    row[field] = rnd.randint(50, 2000)
                elif 'flag' in fl:
                    row[field] = rnd.choice(yes_no_flags)
                elif field == 'floorEnergyEff':
                    if nation == 'Scotland':
                        values = floor_energy_eff_singles + floor_energy_eff_combos
                        weights = floor_energy_eff_single_weights + floor_energy_eff_combo_weights
                    else:
                        values, weights = floor_energy_eff_singles, floor_energy_eff_single_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif field == 'roofEnergyEff':
                    if nation == 'Scotland':
                        values = roof_energy_eff_singles + roof_energy_eff_combos
                        weights = roof_energy_eff_single_weights + roof_energy_eff_combo_weights
                    else:
                        values, weights = roof_energy_eff_singles, roof_energy_eff_single_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif field == 'glazedArea':
                    if nation == 'Scotland':
                        values, weights = glazed_area_scotland_values, glazed_area_scotland_weights
                    else:
                        values, weights = glazed_area_ew_values, glazed_area_ew_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif 'energyeff' in fl or 'enveff' in fl:
                    row[field] = rnd.choice(efficiency_ratings)
                elif 'count' in fl or 'number' in fl:
                    row[field] = rnd.randint(0, 10)
                elif 'proportion' in fl:
                    row[field] = rnd.randint(0, 100)
                elif 'unheated' in fl:
                    row[field] = round(rnd.uniform(0.0, 10.0), 1)
                elif 'description' in fl:
                    row[field] = f"Sample {field} description"
                else:
                    row[field] = f"Sample_{field}"
            data.append(row)

        return pd.DataFrame(data)

    # ---------- SERL survey ----------
    def generate_serl_survey(self) -> pd.DataFrame:
        survey_fields = read_survey_dictionary(self.survey_dictionary_path)
        survey_versions = ['Wave1', 'Wave2', 'Wave3']
        collection_methods = ['Online', 'Postal']
        languages = ['English', 'Welsh', 'Unknown']
        binary_responses = [0, 1]
        multi_choice_responses = [1, 2, 3, 4, 5]
        missing_codes = [-1, -2, -9]
        # Per-field valid missing codes, derived from serl_survey_data_dictionary.
        # Fields not listed here fall back to missing_codes.
        _field_missing: Dict[str, List] = {
            'A1':    [-2], 'A6': [-2], 'A7': [-9, -2], 'A10': [-2], 'A11': [-2],
            'A13_01': [-2], 'A13_02': [-2],
            'A14':   [-2, -1], 'A1501': [-2, -1], 'A1502': [-2, -1],
            'A401':  [-9], 'A402': [-9], 'A403': [-9],
            'A404':  [-9], 'A405': [-9], 'A406': [-9],
            'B2':    [-2], 'B3': [-9, -2], 'B6': [-2, -4], 'B7': [-2, -1], 'B8': [-2, -1],
            'C301':  [-2], 'C302': [-2], 'C303': [-2],
            'C304':  [-2], 'C305': [-2], 'C306': [-2], 'C307': [-2],
            'C4':    [-2, -1],
            'D1':    [-9, -2], 'D2': [-9, -3, -2], 'D3': [-9, -3, -2],
        }
        # Per-field valid (non-missing) options, derived from serl_survey_data_dictionary.
        # Fields not listed here fall back to multi_choice_responses.
        _field_opts: Dict[str, List] = {
            'A1':    [1, 2],
            'A2':    [1, 2, 3, 4],
            'A5':    [1, 2, 3],
            'A6':    [1, 2],
            'A7':    [1, 2],
            'A8':    [1, 2, 3, 4, 5],
            **{f'A9{i:02d}': [0, 1] for i in range(1, 8)},
            'A10':   [1, 2, 3, 4, 5, 6],
            'A11':   [1, 2],
            'A14':   [1, 2, 3, 4],
            'A1501': [1, 2, 3, 4, 5],
            'A1502': [1, 2, 3, 4, 5],
            **{f'A4{i:02d}': [0, 1] for i in range(1, 7)},
            **{f'A16{i:02d}': [0, 1] for i in range(1, 11)},
            'B2':    [1, 2],
            'B7':    [1, 2],
            'B8':    [1, 2],
            **{f'B10{i:02d}': [0, 1] for i in range(1, 15)},
            'D1':    [1, 2, 3, 4, 5, 6, 7],
            'D2':    [1, 2, 3],
            'D3':    [1, 2, 3, 4, 5, 6, 7],
        }
        # Sum fields derived from binary constituent fields. Computed after those
        # fields are assigned so the sum is always consistent with the row values.
        _C3_individual = ['C301', 'C302', 'C303', 'C304', 'C305', 'C306', 'C307']
        _binary_sum_fields: Dict[str, List[str]] = {
            'A3_sum':         [f'A3{i:02d}' for i in range(1, 11)],
            'A4_sum':         [f'A4{i:02d}' for i in range(1, 7)],
            'A9_sum':         [f'A9{i:02d}' for i in range(1, 8)],
            'A12_Taps_sum':   ['A12_Taps_GB', 'A12_Taps_EH', 'A12_Taps_SWH',
                               'A12_Taps_Other', 'A12_Taps_NA', 'A12_Taps_DK'],
            'A12_Shower_sum': ['A12_Shower_GB', 'A12_Shower_EH', 'A12_Shower_SWH',
                               'A12_Shower_Other', 'A12_Shower_NA', 'A12_Shower_DK'],
            'A16_sum':        [f'A16{i:02d}' for i in range(1, 11)],
            'B10_sum':        [f'B10{i:02d}' for i in range(1, 15)],
            'C3_sum':         _C3_individual,
        }
        # C2 occupant-count fields (14 gender × age-band categories).
        _C2_individual = [
            'C2_Male_0_15',    'C2_Male_16_24',   'C2_Male_25_44',   'C2_Male_45_64',
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_0_15',  'C2_Female_16_24', 'C2_Female_25_44', 'C2_Female_45_64',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_child  = ['C2_Male_0_15', 'C2_Female_0_15']
        _C2_adult  = [
            'C2_Male_16_24',   'C2_Male_25_44',   'C2_Male_45_64',
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_16_24', 'C2_Female_25_44', 'C2_Female_45_64',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_65plus = [
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_individual_skip = set(_C2_individual[1:])
        # Age-band weights from ONS 2021 Census (England & Wales).
        # Bands: 0-15, 16-24, 25-44, 45-64, 65-74, 75-84, 85+
        # Equal male/female split: male slots first, female slots second.
        _C2_age_weights = [18, 10, 26, 26, 11, 7, 2]
        _C2_weights = _C2_age_weights + _C2_age_weights

        data = []
        rnd = random.Random(self.seed + 200)

        for puprn in self.puprns:
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in survey_fields[1:]:
                if field == 'Survey_version':
                    row[field] = rnd.choice(survey_versions)
                elif field == 'Recorded_date':
                    base = pd.Timestamp(2021, 1, 1)
                    row[field] = (base + pd.Timedelta(days=rnd.randint(0, 730))).strftime('%Y-%m-%d')
                elif field == 'Collection_method':
                    row[field] = rnd.choice(collection_methods)
                elif field == 'Language':
                    row[field] = rnd.choice(languages)
                elif field == 'B1':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5, 6])
                elif field == 'B2':
                    row[field] = 2 if rnd.random() < 0.03 else 1  # ~3% not self-contained
                elif field == 'B4':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5])
                elif field == 'B5':
                    row[field] = rnd.choice([2, 3, 4])
                elif field == 'B5_err':
                    row[field] = 0
                elif field == 'B9':
                    row[field] = rnd.choice([-2, -1]) if rnd.random() < 0.1 else rnd.randint(1, 7)
                elif field == 'C1_new':
                    c1 = row.get('C1')
                    if isinstance(c1, int) and c1 > 0:
                        if rnd.random() < 0.01:
                            row[field] = max(1, c1 + rnd.choice([-1, 1]))
                        else:
                            row[field] = c1
                    else:
                        row[field] = rnd.randint(1, 4)  # C1 missing/invalid
                elif field == 'C1':
                    if rnd.random() < 0.05:
                        row[field] = -2
                    else:
                        # Approximate ONS 2021 Census England & Wales distribution
                        row[field] = rnd.choices(
                            [1, 2, 3, 4, 5, 6, 7, 8],
                            weights=[29, 34, 15, 13, 6, 2, 1, 0.3],
                        )[0]
                elif field == 'C5':
                    row[field] = rnd.randint(-2, 2)
                elif field == 'D4':
                    row[field] = rnd.choice([-3, -2, -1]) if rnd.random() < 0.15 else rnd.randint(1, 5)
                elif field in _binary_sum_fields:
                    row[field] = sum(1 for f in _binary_sum_fields[field] if row.get(f) == 1)
                elif field.startswith('A3'):
                    if not field.endswith('_err'):
                        row[field] = rnd.choice([0, 1])
                    else:
                        row[field] = True if rnd.random() < 0.1 else False
                elif field in ('A12_Taps_SWH', 'A12_Shower_SWH'):
                    row[field] = 1 if puprn in self._solar_thermal_households else 0
                elif field.startswith('A12_'):
                    row[field] = rnd.choice([0, 1])
                elif field.startswith('A13_'):
                    row[field] = rnd.choice(_field_missing.get(field, [-2])) if rnd.random() < 0.1 else rnd.choice([1, 2, 3, 4, 5, 6])
                elif field == 'C2_Male_0_15':
                    # Batch-generate all 14 C2 occupant-count fields together so
                    # their sum matches C1_new. A 5% error rate introduces occasional
                    # mismatches, reflected in C2_sum_diff / C2_error below.
                    target = cast(int, row.get('C1_new', 2))
                    if rnd.random() < 0.05:
                        target = max(0, target + rnd.choice([-1, 1]))
                    counts = [0] * 14
                    for slot in rnd.choices(range(14), weights=_C2_weights, k=target):
                        counts[slot] += 1
                    for i, f in enumerate(_C2_individual):
                        row[f] = counts[i]
                elif field in _C2_individual_skip:
                    pass  # already set by C2_Male_0_15 handler
                elif field == 'C2_sum':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_individual)
                elif field == 'C2_sum_diff':
                    row[field] = cast(int, row.get('C2_sum', 0)) - cast(int, row.get('C1_new', 0))
                elif field == 'C2_error':
                    row[field] = cast(int, row.get('C2_sum_diff', 0)) != 0
                elif field == 'C2_tot_child':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_child)
                elif field == 'C2_tot_adult':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_adult)
                elif field == 'C2_tot_65_plus':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_65plus)
                elif field == 'C2_all_65_plus':
                    c2s = cast(int, row.get('C2_sum', 0))
                    row[field] = 1 if c2s > 0 and cast(int, row.get('C2_tot_65_plus', 0)) == c2s else 0
                elif field == 'C301':
                    # Batch-generate all 7 C3 working-status fields. The number
                    # selected sums to C2_tot_adult in ~95% of cases.
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    target = min(n_adults, 7)
                    if rnd.random() < 0.05:
                        target = max(0, min(7, target + rnd.choice([-1, 1])))
                    selected = set(rnd.sample(range(7), target))
                    for i, f in enumerate(_C3_individual):
                        row[f] = 1 if i in selected else 0
                elif field in ('C302', 'C303', 'C304', 'C305', 'C306', 'C307'):
                    pass  # already set by C301 handler
                elif field == 'C3_sum_diff':
                    row[field] = cast(int, row.get('C3_sum', 0)) - cast(int, row.get('C2_tot_adult', 0))
                elif field == 'C3_error':
                    row[field] = cast(int, row.get('C3_sum_diff', 0)) != 0
                elif field == 'None_working':
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    c3s = cast(int, row.get('C3_sum', 0))
                    if n_adults == 0:
                        row[field] = None   # not possible to determine
                    elif c3s == 0:
                        row[field] = 1      # no adults working
                    else:
                        row[field] = 0      # some adults working
                elif field == 'C4':
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    if rnd.random() < 0.05:
                        row[field] = rnd.choice(_field_missing.get('C4', [-2, -1]))
                    else:
                        row[field] = rnd.randint(0, max(0, n_adults))
                elif field == 'C4_error':
                    c4 = row.get('C4')
                    c1_new = cast(int, row.get('C1_new', 0))
                    row[field] = isinstance(c4, int) and c4 >= 0 and c4 > c1_new
                elif field.startswith('C2_'):
                    row[field] = -2 if rnd.random() < 0.05 else rnd.randint(0, 3)
                elif '_text' in field.lower() or '_other' in field.lower():
                    row[field] = f"Sample text for {field}"
                elif field.endswith('_sum') or field.endswith('_diff'):
                    row[field] = rnd.randint(0, 10)
                elif field.endswith('_err') or field.endswith('_edit'):
                    row[field] = rnd.choice([True, False])
                elif field.startswith('A') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.1 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field.startswith('B') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field == 'C6':
                    row[field] = (rnd.choice([-9, -2, -1]) if rnd.random() < 0.1
                                  else rnd.choice([1, 2, 3, 4, 5])) if puprn in self._ev_households else -9
                elif field.startswith('C') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.randint(0, 15)
                elif field.startswith('D') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.15 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field.endswith('01') or field.endswith('02') or field.endswith('03'):
                    row[field] = rnd.choice(binary_responses)
                else:
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.08 else rnd.choice(multi_choice_responses)

            # Device-aware overrides from config fractions.
            # These are applied after generic random generation to keep consistency.
            if 'A1607' in row:
                row['A1607'] = 1 if puprn in self._hp_households else 0
            if 'C5' in row:
                row['C5'] = 1 if puprn in self._ev_households else 2
            if 'C6' in row:
                row['C6'] = rnd.choice([1, 2, 3, 4]) if puprn in self._ev_households else -9
            data.append(row)

        return pd.DataFrame(data)

    # ---------- MasterSERL harmonised survey ----------
    def generate_harmonised_survey(self) -> pd.DataFrame:
        """Generate the harmonised MasterSERL survey dataframe, derived from
        this same run's Sign Up / 2023 / 2025 raw survey dataframes (see
        this module's "MasterSERL harmonised survey" section above for
        exactly how a raw cell becomes a harmonised one) — so a PUPRN's
        harmonised answers are consistent with its raw-survey rows, whether
        or not those raw outputs are also being written this run
        (`generate.survey` / `follow_up_survey` / `survey_2025` don't need
        to be on).
        """
        master_mapping = read_master_mapping(self.master_mapping_path)
        return build_harmonised_survey_dataframe(
            self.puprns, master_mapping, self.seed,
            sign_up_df=self.generate_serl_survey(),
            survey_2023_df=self.generate_follow_up_survey(),
            survey_2025_df=self.generate_2025_survey(),
        )

    # ---------- Raw 2025 SERL Observatory survey ----------
    def generate_2025_survey(self) -> pd.DataFrame:
        """Generate the raw 2025 survey dataframe — see this module's "Raw
        2025 SERL Observatory survey" section above for the real
        question/answer vocabulary (from
        serl_2025_survey_PaperSurveyFinalCopy.pdf) and skip logic it's built
        from. Independent of generate_harmonised_survey() (separate random
        draws — see that method's caveat about the two not being
        cross-consistent) and of the other raw survey outputs.
        """
        master_mapping = read_master_mapping(self.master_mapping_path)
        return build_2025_survey_dataframe(self.puprns, master_mapping, self.seed)

    # ---------- COVID-19 survey ----------
    def generate_covid19_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.covid19_survey_dictionary_path)

        missing_codes = [-1, -2, -9]

        # Fields with a direct option-set lookup.
        # Branching/routing fields (Q1→Q2, Q3→Q3a, Q11a→Q11b, Q12a→Q12b) are
        # handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'Q1':  [1, 2, 3, 4, 5, 6, 7],
            'Q5':  [1, 2, 3, 4, 5],
            'Q7':  [1, 2, 3],
            'Q8b': [1, 2, 3, 4],
            'Q9':  [1, 2, 4],          # 3 not a valid code
            'Q10': [1, 2, 3, 4],
            'Q17': [1, 2, 3, 4],
            'Q18': [1, 4, 5, 6],       # 2 and 3 not valid codes
            'Q19': [1, 2, 3, 4, 5, 6, 7],
            'Q21': [1, 2, 3, 4],
            'Q24': list(range(1, 13)),
            'Q25': list(range(1, 13)),
        }

        rnd = random.Random(self.seed + 600)

        def mm(choices, p: float = 0.05):
            return rnd.choice(missing_codes) if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            _q1 = None
            _q3 = None

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── Q1 (state) / Q2 routed from Q1 ───────────────────────
                elif field == 'Q1':
                    v = mm([1, 2, 3, 4, 5, 6, 7])
                    row[field] = v; _q1 = v
                elif field == 'Q2':
                    # Q1=6 means "I don't have this" → Q2 code 4 = same
                    row[field] = 4 if _q1 == 6 else mm([1, 2, 3, 4, 5])

                # ── Q3 (state) / Q3a only applicable if Q3=1 ─────────────
                elif field == 'Q3':
                    v = rnd.choice([1, 2])
                    row[field] = v; _q3 = v
                elif field.startswith('Q3a_'):
                    row[field] = rnd.choice([0, 1]) if _q3 == 1 else np.nan

                # ── Q4: outdoor-access checkboxes (always answered) ───────
                elif field.startswith('Q4_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q6: window-opening frequency (non-sequential codes) ───
                elif field.startswith('Q6_'):
                    row[field] = mm([7, 14, 15, 16, 17, 18])

                # ── Q11a: "don't have appliance" flags ────────────────────
                elif field.startswith('Q11a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q11b: appliance use vs pre-lockdown ───────────────────
                # Skip (NaN) when the household indicated they don't have the
                # appliance via the corresponding Q11a_2_N_1 flag.
                elif field.startswith('Q11b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q11a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q12a: "don't have device" flags ───────────────────────
                elif field.startswith('Q12a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q12b: device use vs pre-lockdown ──────────────────────
                elif field.startswith('Q12b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q12a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q22a / Q22b: time-of-day checkboxes (0/1) ────────────
                elif field.startswith('Q22a_') or field.startswith('Q22b_'):
                    row[field] = rnd.choice([0, 1])

                # ── RC1/RC2: research consent; Finished: always answered ──
                elif field in ('RC1', 'RC2'):
                    row[field] = rnd.choice([1, 2])
                elif field == 'Finished':
                    row[field] = rnd.choice([0, 1])

                # ── direct option lookup (with small missing probability) ─
                elif field in _field_opts:
                    row[field] = mm(_field_opts[field])

                else:
                    row[field] = np.nan

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Participant summary ----------
    def generate_participant_summary(self) -> pd.DataFrame:
        fields = ['PUPRN', 'Region', 'LSOA', 'grid_cell', 'IMD_quintile']
        # England/Wales regions to choose among when the PUPRN's nation (see
        # self._nation, shared with EPC generation) is "England and Wales" —
        # keeps Region consistent with epcVersion for the same household.
        regions_ew = [
            'NORTH EAST','NORTH WEST','YORKSHIRE AND THE HUMBER','EAST MIDLANDS',
            'WEST MIDLANDS','EAST OF ENGLAND','LONDON','SOUTH EAST','SOUTH WEST',
            'WALES'
        ]
        # LSOA prefix by nation; all other regions are in England (E01)
        _lsoa_prefix = {'WALES': 'W01', 'SCOTLAND': 'S01'}
        # Real ONS LSOA / NRS Data Zone codes, grouped by prefix. Sampled
        # codes are genuine but not tied to the household's actual location —
        # falls back to a synthetic-but-correctly-prefixed code if the
        # reference file is missing.
        lsoa_codes_by_prefix = read_lsoa_codes(LSOA_CODES_PATH, vintage=self.lsoa_vintage)
        data = []
        rnd = random.Random(self.seed + 300)
        for puprn in self.puprns:
            region = 'SCOTLAND' if self._nation[puprn] == 'Scotland' else rnd.choice(regions_ew)
            prefix = _lsoa_prefix.get(region, 'E01')
            codes_for_prefix = lsoa_codes_by_prefix.get(prefix)
            lsoa = rnd.choice(codes_for_prefix) if codes_for_prefix else f"{prefix}{rnd.randint(0, 999999):06d}"
            data.append({
                'PUPRN': puprn,
                'Region': region,
                'LSOA': lsoa,
                'grid_cell': self._grid_cells.get(puprn, ''),
                'IMD_quintile': rnd.randint(1, 5),
            })
        return pd.DataFrame(data, columns=fields)

    # ---------- Follow-up survey ----------
    def generate_follow_up_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.followup_survey_dictionary_path)

        # ── response-option lists (full text, as in actual SERL data) ─────
        YES_NO_NR  = ['Yes', 'No', 'No response']
        FREQ_6     = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable, cannot do this']
        CHANGE_6   = ['A lot more', 'A little more', 'About the same',
                      'A little less', 'A lot less', 'Not applicable, cannot do this']
        FREQ_UNOCC = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable']
        A9_OPTS    = [
            'Yes, some or all have their own source of fuel (e.g., logs, coal, bottled gas etc.)',
            'No, they are all powered by mains gas or electricity',
            'No response',
        ]
        A10_OPTS   = ['Daily', 'Most days', 'Rarely - only if I/we really have to',
                      'Never', 'Varies - depends on temperature or other reasons', "Don't know"]
        A11_OPTS   = ['More often', 'Less often', 'About the same',
                      "I don't have this", 'It is not working', "Don't know"]
        A12_OPTS   = ['A great deal of effort', 'Some effort', 'A little effort',
                      'No effort at all', "Don't know"]
        BATHROOMS  = ['0', '1', '2', '3', '4 or more']
        ADD_REP    = ['Has been added or replaced in the last 12 months', 'No']
        PEOPLE     = ['0 people', '1 person', '2 people', '3 people', '4 or more people']
        INCOMES    = [
            'Below £10,000', '£10,001 to £20,000', '£20,001 to £30,000',
            '£30,001 to £40,000', '£40,001 to £50,000', '£50,001 to £60,000',
            '£60,001 to £70,000', '£70,001 to £80,000', '£80,001 to £90,000',
            '£90,001 to £100,000', 'Above £100,000', 'Prefer not to answer',
        ]
        PAYMENT    = [
            'Direct debit (including online direct debit)',
            'Payment on receipt of bill (by post, telephone, online or at bank/post office)',
            'Pre-payment meter', 'Included in rent', 'Other', "Don't know",
        ]
        C5_OPTS    = ['Very easy', 'Fairly easy', 'Neither easy nor difficult',
                      'Fairly difficult', 'Very difficult', "Don't know"]
        C6_OPTS    = ['Daily', 'Most days', 'Rarely', 'never', "Don't know", 'Prefer not to say']
        WFH        = ['Always work from home', 'Sometimes work from home',
                      'Never work from home', 'Not applicable /prefer not to say']
        E1_OPTS    = ['Living comfortably', 'Doing alright', 'Just about getting by',
                      'Finding it quite difficult', 'Finding it very difficult',
                      "Don't know", 'Prefer not to say']
        MOULD      = ['Minor', 'Substantial', "Don't know"]

        # Fields with a direct option-set lookup (not derivable from name pattern alone).
        # Branching/stateful fields (A8/A9/A10, B5/B6, C3/C4, D1/D2/D3, D5/D6, E2/E3)
        # are handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'A2': YES_NO_NR, 'A3': YES_NO_NR, 'A4': YES_NO_NR,
            'A7': FREQ_UNOCC, 'A11': A11_OPTS, 'A12': A12_OPTS,
            'B1': BATHROOMS,
            'C1': INCOMES, 'C2_electricity': PAYMENT,
            'C5': C5_OPTS, 'C6': C6_OPTS,
            'D4': WFH,
            'E1': E1_OPTS,
            'Collection_method': ['Online', 'Postal'],
        }

        # B2/B3/B4 sub-field probabilities for *_yes generation
        _B2_PROBS = {1:0.03, 2:0.70, 3:0.05, 4:0.05, 5:0.01, 6:0.02,
                     7:0.03, 8:0.01, 9:0.01, 10:0.01, 11:0.01}
        _B3_PROBS = {2:0.05, 5:0.80, 6:0.70, 7:0.60, 8:0.15, 9:0.03,
                     10:0.80, 11:0.70, 12:0.30, 13:0.40, 14:0.30, 15:0.35, 16:0.05}
        _B4_PROBS = {1:0.70, 2:0.50, 3:0.15, 4:0.20, 5:0.85, 6:0.40, 7:0.05}

        _C4_ANSWERS = {
            'C4_1': 'You feel your home is difficult to heat',
            'C4_2': 'You feel it is difficult to afford the fuel to heat your home',
            'C4_3': 'Prefer not to say',
            'C4_4': 'None of the above',
            'C4_5': 'Other reason',
        }

        _D2_DERIVED  = {'D2_ignored', 'D2_min_total'}
        _D2_FIELDS   = ['D2_0_5', 'D2_6-15', 'D2_16-24', 'D2_24_44',
                        'D2_45-64', 'D2_65_74', 'D2_75_85', 'D2_85plus']
        _D2_WEIGHTS  = [0.05, 0.10, 0.09, 0.30, 0.27, 0.11, 0.06, 0.02]
        _D3_WEIGHTS  = [0.35, 0.20, 0.12, 0.05, 0.08, 0.20]   # D3_1..D3_6
        _D2_OVER16   = ['D2_16-24', 'D2_24_44', 'D2_45-64',
                        'D2_65_74', 'D2_75_85', 'D2_85plus']
        _pcount      = {'0 people': 0, '1 person': 1, '2 people': 2,
                        '3 people': 3, '4 or more people': 4}

        def _spread(total: int, weights: list) -> list:
            """Distribute `total` people across len(weights) buckets by sampling."""
            counts = [0] * len(weights)
            for _ in range(total):
                r = rnd.random(); cum = 0.0
                for i, w in enumerate(weights):
                    cum += w
                    if r < cum:
                        counts[i] += 1; break
                else:
                    counts[-1] += 1
            return counts

        rnd = random.Random(self.seed + 400)

        def nr(choices, p: float = 0.08):
            return np.nan if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            has_pv = puprn in self._pv_households
            has_hp = puprn in self._hp_households
            has_ev = puprn in self._ev_households

            # State tracked across fields for branching and derived computations
            _a8 = _a9 = _b5 = _c3 = _c4_chosen = None
            _d1 = None
            _d2_answered = _d3_answered = None
            _d2_vals: dict = {}
            _d3_vals: dict = {}
            _d2_distribution: dict = {}
            _d3_distribution: dict = {}

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── A1: temperature (internally consistent group) ──────────
                elif field == 'A1_units':
                    v = nr(['Celsius', 'Fahrenheit', "Don't know/can't do this"], p=0.12)
                    row[field] = v
                elif field == 'A1_degC':
                    row[field] = (rnd.randint(15, 25)
                                  if row.get('A1_units') == 'Celsius' else np.nan)
                elif field == 'A1_degF':
                    row[field] = (rnd.randint(59, 77)
                                  if row.get('A1_units') == 'Fahrenheit' else np.nan)
                elif field == 'A1_corr_C':
                    u = row.get('A1_units')
                    if u == 'Celsius':    row[field] = float(row['A1_degC'])
                    elif u == 'Fahrenheit': row[field] = round((row['A1_degF'] - 32) * 5/9, 1)
                    else:                  row[field] = np.nan
                elif field == 'A1_edit':
                    row[field] = (row.get('A1_units') == 'Fahrenheit')
                elif field == 'A1_err':
                    row[field] = (row.get('A1_units') not in ('Celsius', 'Fahrenheit')
                                  and rnd.random() < 0.05)

                # ── A5 frequency / A6 change ──────────────────────────────
                elif field.startswith('A5_'):
                    row[field] = nr(FREQ_6)
                elif field.startswith('A6_'):
                    row[field] = nr(CHANGE_6)

                # ── A8/A9/A10: branching on standalone heaters ────────────
                elif field == 'A8':
                    v = nr(YES_NO_NR); row[field] = v; _a8 = v
                elif field == 'A9':
                    if _a8 == 'No':
                        row[field] = 'NA'
                    elif _a8 == 'Yes':
                        v = nr(A9_OPTS); row[field] = v; _a9 = v
                    else:
                        row[field] = 'No response'
                elif field == 'A10':
                    if _a8 in (None, 'No'):
                        row[field] = 'NA'
                    elif _a9 == 'No, they are all powered by mains gas or electricity':
                        row[field] = 'NA'
                    elif _a9 and _a9 != 'No response':
                        row[field] = nr(A10_OPTS)
                    else:
                        row[field] = 'No response'

                # ── B2_N: heating types (HP trait-aligned) ────────────────
                elif field.startswith('B2_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 5 and has_hp:    row[field] = 'Yes'
                    elif n == 2 and has_hp:  row[field] = 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B2_PROBS.get(n, 0.02) else 'No'
                elif field.startswith('B2_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B2_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B2_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B3_N: technologies (PV/EV trait-aligned) ──────────────
                elif field.startswith('B3_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 1:    row[field] = 'Yes' if has_pv else 'No'
                    elif n == 3:  row[field] = 'Yes' if (has_pv and rnd.random() < 0.30) else 'No'
                    elif n == 4:  row[field] = 'Yes' if has_ev else 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B3_PROBS.get(n, 0.10) else 'No'
                elif field.startswith('B3_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B3_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B3_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B4_N: insulation ──────────────────────────────────────
                elif field.startswith('B4_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    row[field] = 'Yes' if rnd.random() < _B4_PROBS.get(n, 0.20) else 'No'
                elif field.startswith('B4_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B4_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B4_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B5 (state) / B6 (branching) ───────────────────────────
                elif field == 'B5':
                    v = nr(['Yes', 'No', "Don't know"]); row[field] = v; _b5 = v
                elif field.startswith('B6_'):
                    if _b5 == 'Yes':                     row[field] = rnd.choice(MOULD)
                    elif _b5 in ('No', "Don't know"):    row[field] = 'NA'
                    else:                                row[field] = 'No response'

                # ── C2_gas: not applicable without a gas supply ───────────
                elif field == 'C2_gas':
                    has_gas = (row.get('B2_2_yes') == 'Yes' or row.get('B2_9_yes') == 'Yes')
                    row[field] = nr(PAYMENT) if has_gas else 'Not applicable / no mains gas'

                # ── C3 (state) / C4 (branching) ───────────────────────────
                elif field == 'C3':
                    v = nr(['Yes', 'No', "Don't know", 'No response'])
                    row[field] = v; _c3 = v
                elif field.startswith('C4_'):
                    if _c3 == 'No':
                        if _c4_chosen is None:
                            _c4_chosen = rnd.choice(list(_C4_ANSWERS))
                        row[field] = (_C4_ANSWERS[field] if field == _c4_chosen else 'No')
                    else:
                        row[field] = 'NA'

                # ── D1 (state) / D1_flag (derived) ───────────────────────
                elif field == 'D1':
                    v = rnd.randint(1, 5) if rnd.random() < 0.92 else np.nan
                    row[field] = v; _d1 = v
                elif field == 'D1_flag':
                    d1_nan = isinstance(_d1, float) and math.isnan(_d1)
                    d2_min = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                              if _d2_vals else None)
                    row[field] = d1_nan or (
                        d2_min is not None and not d1_nan and d2_min != _d1)

                # ── D2 age groups (all-or-nothing answered) ───────────────
                elif field.startswith('D2_') and field not in _D2_DERIVED:
                    if _d2_answered is None:
                        _d2_answered = rnd.random() < 0.88
                        if _d2_answered:
                            d1_nan = _d1 is None or (isinstance(_d1, float) and math.isnan(_d1))
                            total = rnd.randint(1, 5) if d1_nan else int(_d1)
                            counts = _spread(total, _D2_WEIGHTS)
                            _d2_distribution = dict(zip(_D2_FIELDS, counts))
                    if _d2_answered:
                        v = PEOPLE[min(_d2_distribution.get(field, 0), 4)]
                    else:
                        v = 'No response'
                    row[field] = v; _d2_vals[field] = v
                elif field == 'D2_ignored':
                    row[field] = not bool(_d2_answered)
                elif field == 'D2_min_total':
                    row[field] = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                                  if _d2_answered else np.nan)

                # ── D3 working situation (all-or-nothing answered) ────────
                elif field.startswith('D3_') and field.split('_')[1].isdigit():
                    if _d3_answered is None:
                        _d3_answered = rnd.random() < 0.88
                        if _d3_answered:
                            if _d2_distribution:
                                over16 = sum(_d2_distribution.get(f, 0) for f in _D2_OVER16)
                            elif _d1 is not None and not (isinstance(_d1, float) and math.isnan(_d1)):
                                over16 = max(1, round(int(_d1) * 0.75))
                            else:
                                over16 = rnd.randint(1, 4)
                            counts = _spread(over16, _D3_WEIGHTS)
                            _d3_distribution = {f'D3_{i+1}': counts[i] for i in range(6)}
                    if _d3_answered:
                        v = PEOPLE[min(_d3_distribution.get(field, 0), 4)]
                    else:
                        v = 'No response'
                    row[field] = v; _d3_vals[field] = v
                elif field in ('D3_flag_high', 'D3_flag_low'):
                    row[field] = False
                elif field == 'D3_ignored':
                    row[field] = not bool(_d3_answered)
                elif field == 'D3_total_over16':
                    row[field] = (
                        sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                            for i in range(1, 7))
                        if _d3_answered else np.nan)

                # ── D4_flag (derived from D3 + D4) ───────────────────────
                elif field == 'D4_flag':
                    if not _d3_answered:
                        row[field] = False
                    else:
                        working = sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                                      for i in (1, 2, 5))
                        d4 = row.get('D4')
                        d4_answered = not (d4 is None or
                                           isinstance(d4, float) and math.isnan(d4))
                        row[field] = (working == 0 and d4_answered)

                # ── D5/D6: EVs (trait-aligned, D6 ≤ D5) ─────────────────
                elif field == 'D5':
                    row[field] = (rnd.choice(['1', '2', '3 or more']) if has_ev
                                  else rnd.choice(['0', "Don't know"]) if rnd.random() < 0.02
                                  else '0')
                elif field == 'D6':
                    d5_n = {'0':0,'1':1,'2':2,'3 or more':3}.get(row.get('D5','0'), 0)
                    row[field] = ('0' if d5_n == 0
                                  else rnd.choice(['0', '1', '2', '3 or more'])
                                  if d5_n >= 3 else rnd.choice(['0'] + [str(i) for i in range(1, d5_n+1)]))
                elif field == 'D6_err':
                    row[field] = False

                # ── E2/E3: numeric satisfaction scales ────────────────────
                elif field == 'E2':
                    row[field] = nr([str(i) for i in range(11)], p=0.08)
                elif field == 'E3':
                    row[field] = nr([str(i) for i in range(10)] + ['11'], p=0.08)

                # ── fields with a direct option lookup ────────────────────
                elif field in _field_opts:
                    p = 0.35 if field == 'C1' else 0.08
                    row[field] = nr(_field_opts[field], p=p)

                # ── fallback: any unrecognised derived flag ────────────────
                else:
                    row[field] = False

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Exporters list ----------
    def generate_list_of_exporters(self) -> pd.DataFrame:
        exporters = sorted(self._pv_households)
        return pd.DataFrame(exporters, columns=['PUPRN'])

    # ---------- Write all ----------
    def write_all(
        self,
        outfolder: "Union[str, os.PathLike]",
        mock_only_outfolder: "Optional[Union[str, os.PathLike]]" = None,
        *,
        epc: bool = True,
        survey: bool = True,
        covid19_survey: bool = True,
        follow_up_survey: bool = True,
        harmonised_survey: bool = False,
        survey_2025: bool = False,
        participant_summary: bool = True,
        exporters_list: bool = True,
    ):
        """Write the contextual datasets. Each keyword defaults to True (write
        everything, today's behaviour) — set any to False to skip that specific
        dataset, e.g. to regenerate just one contextual dataset without
        touching the others.
        """
        mock_only_dir = Path(mock_only_outfolder) if mock_only_outfolder is not None else Path(outfolder)

        if epc:
            epc_df = self.generate_epc()
            write_table(epc_df, Path(outfolder) / self._fname(self.names.epc), format=self.format)

        if survey:
            serl_df = self.generate_serl_survey()
            write_table(serl_df, Path(outfolder) / self._fname(self.names.survey), format=self.format)

        if covid19_survey:
            covid19_df = self.generate_covid19_survey()
            write_table(covid19_df, Path(outfolder) / self._fname(self.names.covid19_survey), format=self.format)

        if participant_summary:
            summary_df = self.generate_participant_summary()
            write_table(summary_df, Path(outfolder) / self._fname(self.names.summary), format=self.format)

        if follow_up_survey:
            # encoding only applies when format="csv"
            followup_df = self.generate_follow_up_survey()
            write_table(
                followup_df, Path(outfolder) / self._fname(self.names.followup_survey),
                format=self.format, encoding="latin-1",
            )

        if harmonised_survey:
            harmonised_df = self.generate_harmonised_survey()
            write_table(
                harmonised_df, Path(outfolder) / self._fname(self.names.harmonised_survey),
                format=self.format,
            )

        if survey_2025:
            survey_2025_df = self.generate_2025_survey()
            write_table(
                survey_2025_df, Path(outfolder) / self._fname(self.names.survey_2025),
                format=self.format,
            )

        if exporters_list:
            # Mock-only file, not part of the SERL Edition release, so it's
            # always CSV regardless of the active edition's output format.
            exporters_base = f"{self.names.exporters_prefix}_{self.year}_list_of_exporter_puprns"
            exporters_df = self.generate_list_of_exporters()
            write_table(exporters_df, mock_only_dir / self._fname(exporters_base), format="csv")