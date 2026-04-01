# Project Structure

```
serl-mock/
│
├── config/
│   └── serl_mock.yaml              # Single configuration file for the whole pipeline
│
├── data/
│   └── mock/                       # All generated output lands here
│       ├── puprn_master.csv
│       ├── epc_data_edition08.csv
│       ├── serl_survey_data_edition08.csv
│       ├── serl_participant_summary_data_edition08.csv
│       ├── serl_follow_up_survey_2023_data_edition08.csv
│       ├── Elec_2023_list_of_exporter_puprns_edition08.csv
│       └── serl_smart_meter_hh_edition08/
│           ├── serl_half_hourly_2019_01_edition08.csv
│           └── ...
│
├── docs/
│   ├── overview.md                 # What the project does and quick-start
│   ├── structure.md                # This file
│   ├── generation_model.md         # How smart-meter values are generated
│   ├── configuration.md            # serl_mock.yaml reference
│   ├── metadata.md                 # SERL dataset column reference
│   └── documentation/              # Official SERL PDF documentation (read-only)
│
├── scripts/
│   └── generate_mock_data.py       # Entry point: runs the full pipeline
│
├── src/
│   └── serl_mock/                  # Core library package
│       ├── __init__.py
│       ├── paths.py
│       ├── ids.py
│       ├── utils.py
│       ├── profiles.py
│       ├── patterns.py
│       ├── generator_smartmeter.py
│       └── generator_contextual_data.py
│
├── main.py                         # Thin wrapper — calls scripts/generate_mock_data.py
├── pyproject.toml
└── README.md
```

---

## Module descriptions

### `scripts/generate_mock_data.py`
The **entry point** for the full generation pipeline.  Runs three sequential steps:

1. Generate and write `puprn_master.csv`
2. Instantiate `HHSmartMeterGenerator` and write monthly HH CSVs
3. Instantiate `SERLContextualVariablesGenerator` and write contextual CSVs

### `src/serl_mock/paths.py`
Defines `Path` constants for `CONFIG_DIR`, `DATA_DIR`, `MOCK_DIR`, and `MOCK_HH_DIR` relative to the project root.  Import these instead of hard-coding paths anywhere else.

### `src/serl_mock/ids.py`
Utilities for PUPRN identifiers:
- `make_alphanumeric_ids_ordered` — generates a deterministic list of unique IDs
- `load_puprn_list_csv` / `write_puprn_list_csv` — read/write the master CSV

### `src/serl_mock/utils.py`
Shared helpers:
- `read_config` — loads a YAML or JSON config file
- `seed_random` — seeds both Python `random` and NumPy RNGs
- `write_csv` — thin wrapper around `DataFrame.to_csv`
- `with_edition_suffix` — builds Edition-stamped filenames
- `read_survey_dictionary` — loads variable names from the SERL survey data dictionary

### `src/serl_mock/profiles.py`
Defines **per-household consumption parameters**.  Each PUPRN is assigned a `HouseholdProfile` (baseline electricity Wh, baseline gas Wh, noise scale, `has_gas` flag) drawn once at initialisation.  Adjusting `profiles:` in `serl_mock.yaml` shifts the population without touching any code.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/patterns.py`
Pure NumPy functions that return **time-based multiplier arrays**:
- `elec_seasonal_mult` / `gas_seasonal_mult` — cosine-based seasonal curves
- `elec_daily_mult` / `gas_daily_mult` — intraday demand profiles

Replacing any function here changes the shape of the generated time series without touching the generator.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/generator_smartmeter.py`
`HHSmartMeterGenerator` produces Edition 07/08-aligned half-hourly CSVs:
- Reads configuration and instantiates household profiles at `__init__` time
- `generate_month(year, month)` builds the full T × H DataFrame vectorised
- Applies Edition 07 timestamp rules (UTC cut-off, BST/GMT labels, HH index, `Valid_read_time`)
- Computes Edition 07-style error flags with `np.where` (no Python loops)

### `src/serl_mock/generator_contextual_data.py`
`SERLContextualVariablesGenerator` produces the contextual CSV files:
- EPC records generated field-by-field using category lists and numeric ranges
- Survey data driven by the SERL survey data dictionary (falls back to a minimal set if the dictionary file is absent)
- `write_all(outfolder)` writes all five contextual files in one call
