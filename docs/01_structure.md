# Project Structure

```
serl-mock/
│
├── config/
│   ├── serl_mock.yaml               # Default config (edition08) — select a different one
│   │                                # with --config (see 02_configuration.md)
│   ├── serl_mock_edition08.yaml     # Example: explicit edition08 config
│   └── serl_mock_edition09.yaml     # Example: edition09 config (format/layout differ
│                                    # automatically — see src/serl_mock/edition.py)
│
├── data/
│   ├── reference/                  # Tracked input files (data dictionaries, BST dates, bank holidays)
│   │   ├── bst_dates_to_2030.csv                     # transversal — not edition-specific
│   │   ├── uk_bank_holidays_england_wales_scotland.csv  # transversal
│   │   ├── edition08/
│   │   │   ├── serl_survey_data_dictionary_edition07.csv
│   │   │   ├── serl_covid19_survey_data_dictionary_edition07.csv
│   │   │   ├── serl_follow_up_survey_data_dictionary_edition07.csv
│   │   │   ├── serl_epc_data_dictionary_edition07.csv   # real SERL doc — cross-reference only
│   │   │   └── serl_epc_generated_fields.csv            # which of those fields we generate
│   │   └── edition09/                                # placeholder — copies of edition08's
│   │       ├── README.md                              # dictionaries; see this file
│   │       ├── serl_survey_data_dictionary_edition09.csv
│   │       ├── serl_covid19_survey_data_dictionary_edition09.csv
│   │       ├── serl_follow_up_survey_data_dictionary_edition09.csv
│   │       └── serl_epc_generated_fields.csv
│   └── mock/                       # All generated output lands here (gitignored)
│       └── edition08/                                # <output_label>/, defaults to edition<N>
│           ├── bst_dates_to_2030.csv
│           ├── serl_survey_data_dictionary_edition08.csv
│           ├── serl_covid19_survey_data_dictionary_edition08.csv
│           ├── serl_tariff_data_edition08.csv                                              # placeholder
│           ├── serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv  # placeholder
│           ├── serl_epc_data_edition08.csv
│           ├── serl_survey_data_edition08.csv
│           ├── serl_covid19_survey_data_edition08.csv
│           ├── serl_participant_summary_edition08.csv
│           ├── serl_2023_follow_up_survey_data_edition08.csv
│           ├── serl_smart_meter_rt_summary_edition08.csv
│           ├── serl_smart_meter_hh_edition08/
│           │   ├── serl_half_hourly_2019_01_edition08.csv
│           │   └── ...
│           ├── serl_smart_meter_daily_edition08/
│           │   ├── serl_smart_meter_daily_2019_edition08.csv
│           │   └── ...
│           ├── serl_climate_data_edition08/
│           │   ├── serl_climate_data_2019_01_edition08.nc   # raw ERA5 download
│           │   ├── serl_climate_data_2019_01_edition08.csv  # SERL-format CSV
│           │   └── ...
│           ├── serl_aggregated_data/                        # placeholder folder mirroring the TRE layout
│           └── mock_internal/
│               ├── puprn_master.csv
│               ├── household_traits.csv
│               └── Elec_2023_list_of_exporter_puprns_edition08.csv
│
├── docs/
│   ├── 00_overview.md              # What the project does and quick-start
│   ├── 01_structure.md             # This file
│   ├── 02_configuration.md         # serl_mock.yaml reference
│   ├── 03_generation_model.md      # How smart-meter values are generated
│   ├── 04_metadata.md              # SERL dataset column reference
│   ├── 05_epc_reference.md         # EPC quirks and region differences (E&W vs Scotland)
│   ├── 06_testing.md               # Running and reading the automated test suite
│   └── notes/                      # Working notes and plans — not reference docs
│       ├── working_notes.md        # Working notes and TODOs
│       └── edition_multiformat_plan.md  # Plan for multi-edition & multi-format support
│
├── notebooks/
│   └── explore_mock_data.ipynb     # Example notebook for exploring generated output
│
├── scripts/
│   ├── generate_mock_data.py       # Entry point: runs the full pipeline
│   ├── generate_bank_holidays_csv.py  # One-off: fetches UK bank holidays from gov.uk
│   └── update_golden_manifest.py   # Regenerates tests/golden/*.json baselines (manual only)
│
├── src/
│   └── serl_mock/                  # Core library package
│       ├── __init__.py
│       ├── paths.py
│       ├── edition.py
│       ├── ids.py
│       ├── utils.py
│       ├── layout.py
│       ├── profiles.py
│       ├── patterns.py
│       ├── generator_household_traits.py
│       ├── generator_smartmeter.py
│       ├── generator_contextual_data.py
│       └── weather_downloader.py
│
├── tests/                          # Automated test suite — see docs/06_testing.md
│   ├── conftest.py
│   ├── _shared.py
│   ├── golden/
│   │   └── edition08_manifest.json
│   ├── test_household_traits.py
│   ├── test_pipeline_smoke.py
│   ├── test_golden_manifest.py
│   ├── test_manifest_helper.py
│   ├── test_format_parquet.py
│   ├── test_layout_single_file.py
│   ├── test_edition.py
│   └── test_reference_dictionary_resolution.py
│
├── pyproject.toml
└── README.md
```

---

## Module descriptions

### `scripts/generate_mock_data.py`
The **entry point** for the full generation pipeline.  Run with `uv run python scripts/generate_mock_data.py`.  Accepts `--skip-weather` to bypass the ERA5 download step, and `--survey-only` to regenerate only the contextual datasets (step 5) from a previous full run.

The pipeline runs these steps in order:

| Step | What it does |
|---|---|
| 0 | Copies `bst_dates_to_2030.csv` as-is, and copies the survey and COVID-19 survey data dictionaries into `data/mock/` with the edition suffix applied |
| 0b | Creates empty placeholder files for datasets not yet generated (`serl_tariff_data_editionXX.csv`, the aggregated-statistics CSV) |
| 1 | Generates PUPRNs → `mock_internal/puprn_master.csv`; assigns household traits (PV, HP, EV, solar thermal, gas meter, export meter) → `mock_internal/household_traits.csv` |
| 2 | Generates monthly half-hourly smart-meter CSVs → `serl_smart_meter_hh_edition08/` |
| 3 | Generates yearly daily smart-meter CSVs → `serl_smart_meter_daily_edition08/` |
| 3b | Generates read-type data quality summary → `serl_smart_meter_rt_summary_edition08.csv` |
| 4 | Downloads ERA5 weather data via CDS API and converts to CSV → `serl_climate_data_edition08/` (skipped with `--skip-weather`; falls back to a warning and an empty output folder if CDS credentials are unavailable) |
| 5 | Generates contextual datasets (EPC, SERL survey, COVID-19 survey, participant summary, follow-up survey, exporter list) |

Skipping steps 1–4 with `--survey-only` requires `mock_internal/puprn_master.csv` and `mock_internal/household_traits.csv` to already exist from a prior full run.

### `scripts/generate_bank_holidays_csv.py`
A one-off utility that fetches the official UK bank holidays JSON from gov.uk and writes `data/reference/uk_bank_holidays_england_wales_scotland.csv`.  Run this locally if the reference file needs updating; the output is committed to the repo.

### `src/serl_mock/paths.py`
Defines `Path` constants for `CONFIG_DIR`, `DATA_DIR`, `MOCK_DIR`, `MOCK_INTERNAL_DIR`,
`MOCK_AGGREGATED_DIR`, and `REFERENCE_DIR` relative to the project root, plus helpers for
paths that vary by edition (so a run's subfolders actually match its own edition):
- `mock_dir_for(output_label)` — `MOCK_DIR / output_label`, e.g. `data/mock/edition08/`
- `reference_dir_for(edition)` — `REFERENCE_DIR / f"edition{edition}"`, e.g. `data/reference/edition08/`
- `mock_hh_dirname(edition)` / `mock_daily_dirname(edition)` / `mock_climate_dirname(edition)` —
  the per-edition smart-meter/climate subfolder *names* (not full paths)

### `src/serl_mock/edition.py`
The single source of truth for which parameters are bound to a SERL edition (format,
smart-meter layout, reference-dictionary source) versus free to vary per scenario. Defines the
`Edition` dataclass and a `_EDITIONS` registry (currently `"08"` and `"09"`); `get_edition(number)`
resolves one, raising `ValueError` for anything not registered. See
[02_configuration.md](02_configuration.md#edition-bound-parameters--format-layout-reference-dictionaries).
Every generator resolves `format`/`layout`/`dictionary_source_edition` through this instead of
reading them as independent config keys — this is what guarantees a config can't accidentally
combine an edition with a format/layout it doesn't use.

### `src/serl_mock/ids.py`
Utilities for PUPRN identifiers:
- `make_alphanumeric_ids_ordered` — generates a deterministic list of unique IDs
- `load_puprn_list_csv` / `write_puprn_list_csv` — read/write the master CSV
- `select_household_subset` — deterministically selects a fraction of households

### `src/serl_mock/utils.py`
Shared helpers:
- `read_config` — loads a YAML or JSON config file
- `seed_random` — seeds both Python `random` and NumPy RNGs
- `write_table` / `read_table` / `table_exists` — format-aware table I/O; dispatch to
  CSV or Parquet based on a `format` argument and append the matching extension. This is
  the single write/read path for every dataset — see
  [notes/edition_multiformat_plan.md](notes/edition_multiformat_plan.md)
- `with_edition_suffix` — builds Edition-stamped filename stems (no extension — that's
  `write_table`'s job)
- `read_survey_dictionary` — loads variable names from the SERL survey data dictionary

### `src/serl_mock/layout.py`
Pluggable strategies for splitting the half-hourly smart-meter dataset into physical files,
independent of format and schema:
- `MonthlyLayout` (default) — one file per calendar month, today's behaviour
- `SingleFileLayout` — one combined file for the whole `start_year`–`end_year` range
- `get_layout(name)` — resolves a layout by name (the active edition's
  `hh_smart_meter_layout`, from `edition.py` — not an independent config key)

`HHSmartMeterGenerator` asks its layout for month groupings rather than hardcoding "one file
per month"; `ReadTypeDataQualitySummaryGenerator` uses the same layout to find the HH files
back when building the rt-summary, so the two stay in sync regardless of which layout is active.

### `src/serl_mock/profiles.py`
Defines **per-household consumption parameters**.  Each PUPRN is assigned a `HouseholdProfile` (baseline electricity Wh, baseline gas Wh, noise scale) drawn once at initialisation.  Adjusting `profiles:` in `serl_mock.yaml` shifts the population without touching any code.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/patterns.py`
Pure NumPy functions that return **time-based multiplier arrays**:
- `elec_seasonal_mult` / `gas_seasonal_mult` — cosine-based seasonal curves
- `elec_daily_mult` / `gas_daily_mult` — intraday demand profiles

Replacing any function here changes the shape of the generated time series without touching the generator.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/generator_household_traits.py`
`generate_household_traits` assigns device ownership and meter availability to each PUPRN once, writing `mock_internal/household_traits.csv`.  All downstream generators (smart-meter, contextual data) read from this file so traits are perfectly consistent across outputs.

Traits assigned: `has_pv`, `has_hp`, `has_ev`, `has_solar_thermal`, `has_gas_meter`, `has_export_meter` (all 0/1).

### `src/serl_mock/generator_smartmeter.py`
Contains three generators:

- `HHSmartMeterGenerator` — produces Edition 08-aligned half-hourly data; reads household traits from `mock_internal/household_traits.csv`
- `DailySmartMeterGenerator` — aggregates HH output to daily totals (always one file per year — not yet layout-configurable)
- `ReadTypeDataQualitySummaryGenerator` — builds the read-type data quality summary

`HHSmartMeterGenerator`:
- Reads configuration and instantiates household profiles at `__init__` time
- `generate_month(year, month)` builds the full T × H DataFrame vectorised — unaffected by format/layout
- Applies Edition 08 timestamp rules (UTC cut-off, BST/GMT labels, HH index, `Valid_read_time`)
- Computes Edition 08-style error flags with `np.where` (no Python loops)
- `generate_all(outfolder)` asks `self.layout` (see `layout.py`) how to group months into files, then writes each group via `write_table` using `self.format`

### `src/serl_mock/generator_contextual_data.py`
`SERLContextualVariablesGenerator` produces the contextual CSV files: EPC, SERL survey, COVID-19 survey, participant summary, follow-up survey, and the exporter PUPRN list.  It reads household traits from `mock_internal/household_traits.csv` to ensure device-ownership fields (PV, HP, EV, solar thermal) are consistent with the smart-meter outputs, and shares a single England & Wales / Scotland nation assignment between the EPC and participant-summary generators so `epcVersion` and `Region` never contradict each other.

- EPC field list comes from `data/reference/edition<N>/serl_epc_generated_fields.csv` (via `read_epc_generated_fields`); each field's values are then generated using category lists and numeric ranges hardcoded in `generate_epc()`, with nation-specific value vocabularies for fields that differ between England & Wales and Scotland (see [05_epc_reference.md](05_epc_reference.md))
- SERL survey, COVID-19 survey, and follow-up survey data are all driven by their respective SERL data dictionaries in `data/reference/edition<N>/`
- Participant summary `grid_cell` values are sampled from cells already present in the downloaded climate CSVs when available, falling back to a geometric assignment over the configured weather bounding box otherwise
- `write_all(outfolder, mock_only_outfolder)` writes all contextual files in one call; the exporter list is written to `mock_only_outfolder` (`mock_internal/`)

### `src/serl_mock/weather_downloader.py`
`WeatherDownloader` retrieves and converts ERA5 reanalysis data:
- `download_month(year, month)` — downloads a single month from the CDS API; skips if the NetCDF file already exists
- `convert_month_to_csv(year, month)` — converts the NetCDF to a SERL-format hourly CSV; skips if the CSV already exists
- `ensure_month(year, month)` — combines both steps in one call
- `ensure_all()` — runs `ensure_month` for every month in the configured date range
