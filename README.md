# serl-mock

A lightweight Python package that generates synthetic datasets matching the structure
and naming conventions of the **SERL (Smart Energy Research Lab) Observatory** data releases.

Use it to build and test analysis pipelines locally, without needing access to the real data in
the Data Safe Haven (DSH) or Trusted Research Environment (TRE).

---

## What is generated

Running the pipeline writes the following files to `data/mock/`:

| Output | Description |
|---|---|
| `puprn_master.csv` | Shared list of synthetic household IDs |
| `serl_smart_meter_hh_edition08/` | Monthly half-hourly electricity and gas CSVs |
| `serl_climate_data_edition08/` | Monthly hourly ERA5 weather CSVs (one per calendar month) |
| `epc_data_edition08.csv` | EPC records |
| `serl_survey_data_edition08.csv` | Survey responses |
| `serl_participant_summary_data_edition08.csv` | Region and deprivation index |
| `serl_follow_up_survey_<year>_data_edition08.csv` | Follow-up survey |
| `Elec_<year>_list_of_exporter_puprns_edition08.csv` | Electricity export list |

All datasets share the same PUPRN list and can be joined directly.

---

## Quick start

```bash
uv sync
python scripts/generate_mock_data.py
```

All settings (number of households, time period, random seed, consumption parameters)
are controlled by a single file:

```
config/serl_mock.yaml
```

### Weather data (ERA5 via CDS API)

Step 3 of the pipeline downloads real hourly ERA5 reanalysis data from the
[Copernicus Climate Data Store](https://cds.climate.copernicus.eu/how-to-api)
and converts each monthly file to a SERL-style CSV.

This requires a free CDS account. Once registered, create `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-api-key>
```

If credentials are not available, skip the download step:

```bash
python scripts/generate_mock_data.py --skip-weather
```


---

## Documentation

| Topic | File |
|---|---|
| What the project does, key concepts | [docs/00_overview.md](docs/00_overview.md) |
| Project layout and module descriptions | [docs/01_structure.md](docs/01_structure.md) |
| How smart-meter values are generated | [docs/03_generation_model.md](docs/03_generation_model.md) |
| All configuration options | [docs/02_configuration.md](docs/02_configuration.md) |
| SERL dataset column reference | [docs/04_metadata.md](docs/04_metadata.md) |

---

## Generated data is not real

The output files are for **pipeline testing and local development only**. They do not
represent real households or real consumption, and should never be used for analysis
or reporting.
