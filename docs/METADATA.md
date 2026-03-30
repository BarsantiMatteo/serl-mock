# SERL data Structure

## Half‑Hourly Electricity & Gas Data
**File format:** `self_half_hourly_<year>_<month>_edition08.csv`

| Column                       | Type               | Example                           |
|------------------------------|--------------------|-----------------------------------|
| PUPRN                        | text               | ABCD1234                          |
| Read_date_effective_local    | date               | 13/01/2019                        |
| Read_date_time_local         | datetime (local)   | 2019‑01‑13 00:30:00 GMT           |
| Read_date_time_UTC           | datetime (UTC)     | 2019‑01‑13T00:30:00Z              |
| HH                           | int                | 1–48                              |
| Valid_read_time              | boolean            | True / False                      |
| Elec_act_imp_flag            | int                | 1 / 2                             |
| Elect_react_imp_flag         | int                | 1 / 2                             |
| Elec_act_exp_flag            | int                | 1 / 2                             |
| Elect_react_exp_flag         | int                | 1 / 2                             |
| Gas_flag                     | int                | 1 / 2                             |
| Elec_act_imp_hh_Wh           | int                | consumption (Wh)                  |
| Elec_react_imp_hh_varh       | int                | reactive consumption (varh)       |
| Elec_act_exp_hh_Wh           | int                | export (Wh)                       |
| Elec_react_exp_hh_varh       | int                | reactive export (varh)            |
| Gas_hh_m3                    | float              | gas volume (m³)                   |
| Gas_hh_Wh                    | int                | gas energy (Wh)                   |
---

## Changing Clock Dates
**File:** `bst_dates_2020.csv`

| Column                       | Type               | Description                       |
|------------------------------|--------------------|-----------------------------------|
| Read_date_effective_local    | date               | 13/01/2019                        |
| type                         | text               | start / end of BST                |
| n_hh                         | int                | Expected half‑hours (46/50)       |

---

## Daily Smart Meter Data
**File:** `self_smart_meter_daily_<year>_edition08.csv`

| Column                       | Type               | Example                           |
|------------------------------|--------------------|-----------------------------------|
| PUPRN                        | text               | ABCD1234                          |
| Read_date_effective_local    | date               | 13/01/2019                        |
| Read_date_time_UTC           | datetime (UTC)     | 13/01/2019 00:00:00               |
| Valid_read_time              | boolean            | True / False                      |
| Elec_act_imp_flag            | int                | 1 / 2                             |
| Valid_hh_sum_or_daily_elec   | boolean            | True / False                      |
| Elec_sum_match               | int                | 0  ??                             |
| Gas_flag                     | int                | 1 / 2                             |
| Valid_hh_sum_or_daily_gas    | boolean            | True / False                      |
| Gas_sum_match                | int                | 0  ??                             |
| Elec_act_imp_d_Wh            | Int                | ??                                |
| Unit_correct_elec_act_imp_d_Wh | Int              | ??                                |
| Elec_act_imp_hh_sum_Wh       | float              | ??                                |
| Gas_d_m3                     | float              | gas volume (m³)                   |
| Gas_hh_sum_m3                | float              | gas volume (m³)                   |
| Gas_d_kWh                    | float              | gas energy (kWh)                  |
| possible_kWh                 | float              | Derive estimates                  |

---

## Climate Data
**File:** `self_climate_data_<year>_<month>_edition08.csv`

| Column                       | Type               | Description                       |
|------------------------------|--------------------|-----------------------------------|
| grid_cell                    | text               | Spatial cell ID (e.g. 12_34)      |
| analysis_date                | date               | 13/01/2019                        |
| date_time_utc                | datetime (UTC)     | 2019‑01‑13T00:30:00Z              |
| 2m_temperature_K             | float              | Air temperature (K)               |
| Surface_solar_radiation_downwards | float         | Solar radiation                   |
| ...                          | ...                | ...                               |
