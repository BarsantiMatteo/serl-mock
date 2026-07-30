# Overview

## What is this project?

`serl-mock` generates mock datasets that mimic the structure and naming conventions of the **SERL (Smart Energy Research Lab) Observatory** data releases.  It is intended for:

- **local development** — build and test analysis pipelines without accessing the real Trusted Research Environment (TRE)
- **prototyping** — explore data structures and column layouts before running code on real data
- **CI/testing** — use deterministic, small datasets as fixtures in automated tests

The generated data is **not real** and does not represent actual households.

---

## What is generated?

Running the pipeline produces the following files under `data/mock/<output_label>/`, where
`output_label` defaults to `edition<N>` (e.g. `data/mock/edition08/`). Filenames below show the
default edition08 config (CSV, monthly smart-meter files) — see
[02_configuration.md](02_configuration.md#edition-bound-parameters--format-layout-reference-dictionaries)
for how edition09 (Parquet) differs:

| File / folder | Content |
|---|---|
| `mock_internal/puprn_master.csv` | Master list of synthetic household IDs (PUPRNs) |
| `mock_internal/household_traits.csv` | Per-household device/meter traits (PV, HP, EV, solar thermal, gas meter, export meter) |
| `serl_smart_meter_hh_edition08/` | One CSV per calendar month with half-hourly electricity and gas readings |
| `serl_smart_meter_daily_edition08/` | One CSV per calendar year with daily electricity and gas readings (edition09: a single combined `serl_smart_meter_daily_edition09.parquet` file at the top level instead — no subfolder) |
| `serl_smart_meter_rt_summary_edition08.csv` | Read-type data quality summary (one row per PUPRN/read type) |
| `serl_climate_data_edition08/` | One CSV per calendar month with hourly ERA5 weather data (only when weather download is enabled) |
| `serl_epc_data_edition08.csv` | EPC (Energy Performance Certificate) records |
| `serl_survey_data_edition08.csv` | Household survey responses |
| `serl_covid19_survey_data_edition08.csv` | COVID-19 lockdown follow-up survey responses |
| `serl_participant_summary_edition08.csv` | Region, LSOA, ERA5 grid cell and deprivation index per household |
| `serl_2023_follow_up_survey_data_edition08.csv` | Follow-up survey responses |
| `serl_tariff_data_edition08.csv` | Placeholder file (not yet generated) |
| `serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv` | Placeholder file (not yet generated) |
| `mock_internal/Elec_2023_list_of_exporter_puprns_edition08.csv` | Households with electricity export |

All datasets share the same PUPRN list so they can be joined reliably.

## Consistency caveat for mock outputs

The mock pipeline is built from multiple generators. While it aims to be coherent, some
cross-dataset combinations may still be mock simplifications rather than fully realistic
joint behaviour.

Important consistency guarantees are already implemented:

- Shared PUPRNs across all generated datasets.
- Deterministic household trait assignment from `mock_internal/household_traits.csv`.
- PV ownership alignment across contextual outputs (EPC, follow-up survey) and exporter list generation.
- Meter-trait alignment for gas and electricity export availability in smart-meter and rt-summary outputs.
- Solar-thermal trait alignment between `household_traits.csv` and the EPC / survey solar-water-heating fields.
- Nation assignment (England & Wales vs Scotland) shared between EPC records and participant summary `Region`, so a household's `epcVersion` and `Region` never contradict each other.
- ERA5 `grid_cell` values in the participant summary are sampled from cells that actually exist in the downloaded climate CSVs when available, so they are always joinable.

---

## Key concepts

### PUPRN
A **PUPRN** (Pseudonymised Unique Property Reference Number) is the household identifier used across all SERL datasets.  In this project PUPRNs are randomly generated 8-character alphanumeric strings.

### Edition
SERL releases data in numbered editions (e.g. Edition 07, Edition 08). The `edition` setting in `serl_mock.yaml` is the single thing that determines: the suffix appended to output filenames; (via `output_label`, defaulting to `edition<N>`) which folder under `data/mock/` a run's output lands in, so different editions never overwrite each other; the output file format and smart-meter file layout, resolved from [`src/serl_mock/edition.py`](../src/serl_mock/edition.py) rather than separate config keys, so a config can't accidentally combine an edition with a format/layout it doesn't use; and which `data/reference/edition<N>/` dictionaries are read. See [02_configuration.md](02_configuration.md#edition-bound-parameters--format-layout-reference-dictionaries).

### Reproducibility
Every random draw uses a seeded RNG.  Setting the same `seed` in `serl_mock.yaml` always produces identical output files.

---

## Further reading

| Topic | Document |
|---|---|
| Project layout and module roles | [01_structure.md](01_structure.md) |
| Running and reading the automated test suite | [06_testing.md](06_testing.md) |
| How smart-meter values are generated | [03_generation_model.md](03_generation_model.md) |
| All configuration options | [02_configuration.md](02_configuration.md) |
| SERL dataset column reference | [04_metadata.md](04_metadata.md) |
| EPC quirks and region differences (England & Wales vs Scotland) | [05_epc_reference.md](05_epc_reference.md) |