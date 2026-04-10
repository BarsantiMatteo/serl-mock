# Overview

## What is this project?

`serl-mock` generates synthetic datasets that mimic the structure and naming conventions of the **SERL (Smart Energy Research Lab) Observatory** data releases.  It is intended for:

- **local development** — build and test analysis pipelines without accessing the real Trusted Research Environment (TRE)
- **prototyping** — explore data structures and column layouts before running code on real data
- **CI/testing** — use deterministic, small datasets as fixtures in automated tests

The generated data is **not real** and does not represent actual households.

---

## What is generated?

Running the pipeline produces the following files under `data/mock/`:

| File / folder | Content |
|---|---|
| `puprn_master.csv` | Master list of synthetic household IDs (PUPRNs) |
| `serl_smart_meter_hh_edition08/` | One CSV per calendar month with half-hourly electricity and gas readings |
| `serl_climate_data_edition08/` | One CSV per calendar month with hourly ERA5 weather data |
| `epc_data_edition08.csv` | EPC (Energy Performance Certificate) records |
| `serl_survey_data_edition08.csv` | Household survey responses |
| `serl_participant_summary_data_edition08.csv` | Region and deprivation index per household |
| `serl_follow_up_survey_<year>_data_edition08.csv` | Follow-up survey responses |
| `Elec_<year>_list_of_exporter_puprns_edition08.csv` | Households with electricity export |

All datasets share the same PUPRN list so they can be joined reliably.

---

## Key concepts

### PUPRN
A **PUPRN** (Pseudonymised Unique Property Reference Number) is the household identifier used across all SERL datasets.  In this project PUPRNs are randomly generated 8-character alphanumeric strings.

### Edition
SERL releases data in numbered editions (e.g. Edition 07, Edition 08).  The `edition` setting in `serl_mock.yaml` controls the suffix appended to all output filenames.

### Reproducibility
Every random draw uses a seeded RNG.  Setting the same `seed` in `serl_mock.yaml` always produces identical output files.

---

## Quick start

```bash
# Install dependencies
uv sync

# Generate all mock data (reads config/serl_mock.yaml)
python scripts/generate_mock_data.py

# Skip ERA5 weather download (no CDS credentials needed)
python scripts/generate_mock_data.py --skip-weather
```

Output appears in `data/mock/`.

### Weather data (ERA5)

Step 3 downloads real hourly ERA5 reanalysis data from the
[Copernicus Climate Data Store](https://cds.climate.copernicus.eu/how-to-api)
and converts each monthly file to a SERL-style CSV under `serl_climate_data_edition08/`.

CDS credentials are required. After registering, create `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-api-key>
```

Each month is processed independently: if the NetCDF already exists it is not
re-downloaded; if the CSV already exists it is not re-converted.

---

## Further reading

| Topic | Document |
|---|---|
| Project layout and module roles | [01_structure.md](01_structure.md) |
| How smart-meter values are generated | [03_generation_model.md](03_generation_model.md) |
| All configuration options | [02_configuration.md](02_configuration.md) |
| SERL dataset column reference | [04_metadata.md](04_metadata.md) |
