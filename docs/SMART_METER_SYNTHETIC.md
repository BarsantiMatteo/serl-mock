# SERL sythtic dataset

## Original data organisation

```
data/Edition08/   
├── documentation/    
│   └── ...
│
├── processed_csv/   
│   ├── serl_aggregated_data/     
│   ├── serl_climate_data_edition08/    
│   ├── serl_smart_meter_daily_edition08/    
│   │  
│   └── serl_smart_meter_hh_edition08/   
│       ├── self_half_hourly2019_01.edition08.csv
│       ├── self_half_hourly2019_02.edition08.csv
│       ├── ...
│       └── self_half_hourly2025_03.edition08.csv
│ 
├── bast_dates_to_2030.csv
├── serl_2023_follow_up_survey_edition08.csv
├── serl_covid19_surcey_data_dictionary_edition08.csv
├── serl_covid19_surcey_data_edition08.csv
├── serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv  
├── serl_epc_data_edition08.csv
├── ...
└── serl_tariff_data_edition08.csv
```


## SERL half-hourly smart meter data structure

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





