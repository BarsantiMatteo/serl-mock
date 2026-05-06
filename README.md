# serl-mock

A lightweight Python package that generates mock datasets matching the structure
and naming conventions of the **SERL (Smart Energy Research Lab) Observatory** data Edition08 release.

Use it to build and test analysis pipelines locally, without needing access to the real data in the Data Safe Haven (DSH) or Trusted Research Environment (TRE).

---

## What is generated

Running the pipeline writes outputs under `data/mock/` with this structure:

```text
data/mock/
	bst_dates_to_2030.csv
	serl_survey_data_dictionary_edition08.csv
	serl_covid19_survey_data_dictionary_edition08.csv
	serl_tariff_data_edition08.csv
	serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv

	serl_epc_data_edition08.csv
	serl_survey_data_edition08.csv
	serl_covid19_survey_data_edition08.csv
	serl_participant_summary_edition08.csv
	serl_2023_follow_up_survey_data_edition08.csv
	serl_smart_meter_rt_summary_edition08.csv

	serl_smart_meter_hh_edition08/
		serl_half_hourly_<YYYY>_<MM>_edition08.csv

	serl_smart_meter_daily_edition08/
		serl_smart_meter_daily_<YYYY>_edition08.csv

	serl_climate_data_edition08/
		serl_climate_data_<YYYY>_<MM>_edition08.nc
		serl_climate_data_<YYYY>_<MM>_edition08.csv

	serl_aggregated_data/

	mock_internal/
		puprn_master.csv
		Elec_2023_list_of_exporter_puprns_edition08.csv
```

Notes:

- `serl_smart_meter_rt_summary_edition08.csv` is the read-type data quality summary (one row per PUPRN and read type).
- `mock_internal/` contains helper/mock-only files, including the master PUPRN list used to align all datasets.
- `serl_climate_data_edition08/` is populated only when weather download is enabled and CDS access is configured.
- Filename suffixes (`edition08`, year values) come from `config/serl_mock.yaml` and generator defaults.

### Consistency caveat for dummy data

This project generates synthetic data in multiple generator modules. As with most mock systems,
some combinations of values across datasets may not be fully realistic or perfectly coherent in
all edge cases.

The code does enforce consistency for key links used in downstream testing, including:

- Shared PUPRN identity across outputs.
- Deterministic household trait assignment from `mock_internal/household_traits.csv`.
- PV ownership consistency across contextual fields and exporter list generation.
- Meter-trait consistency for gas/export availability in smart-meter and read-type summary outputs.

---

## Quick start

Use this path if you want to generate mock smart-meter and contextual datasets without downloading weather data.

1. Clone the repository:

```bash
git clone https://github.com/BarsantiMatteo/serl-mock.git
cd serl-mock
```

2. Install uv (if not already installed):

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

3. Sync the project environment:

```bash
uv sync
```

4. Run the generator:

```bash
uv run python scripts/generate_mock_data.py --skip-weather
```

This command generates all mock datasets except ERA5 weather files.

All settings (number of households, time period, random seed, consumption parameters)
are controlled by a single configuration file:

**[config/serl_mock.yaml](config/serl_mock.yaml)**

Key options you can adjust before running the generator:

| Setting | Description | Default |
|---|---|---|
| `n_households` | Number of synthetic households to generate | `100` |
| `seed` | Random seed for reproducible outputs | `42` |
| `edition` | Dataset edition suffix used in output filenames | `"08"` |
| `start_year` / `end_year` | Time period for smart-meter data | `2019` / `2019` |
| `household_traits.pv_fraction` | Share of households with PV | `0.15` |
| `household_traits.hp_fraction` | Share of households with a heat pump | `0.07` |
| `household_traits.ev_fraction` | Share of households with an EV | `0.10` |
| `household_traits.gas_meter_fraction` | Share of households with a gas meter | `0.85` |
| `household_traits.export_meter_fraction` | Share of households with an export meter | `0.15` |
| `profiles.base_elec_mean_wh` | Mean baseline electricity per half-hour period (Wh) | `175` |
| `profiles.base_gas_mean_wh` | Mean baseline gas at peak heating demand (Wh) | `1500` |
| `patterns.elec_seasonal_amplitude` | Seasonal swing around annual mean electricity | `0.3` |
| `patterns.gas_seasonal_amplitude` | Winter gas heating multiplier | `2.0` |
| `weather.*` | ERA5/CDS download settings (dataset, variables, bounding box) | see file |

See [docs/02_configuration.md](docs/02_configuration.md) for a full description of every option.

### Weather data (ERA5 via CDS API)

If you also want weather data, the same generator can download ERA5 hourly reanalysis data from the
[Copernicus Climate Data Store](https://cds.climate.copernicus.eu/how-to-api) and convert it to SERL-style monthly CSV files.

Before running with weather enabled, complete these one-time steps:

1. Register for a free CDS account.
2. Accept the ERA5 dataset licence terms in the CDS portal.
3. Create `~/.cdsapirc` with your credentials:

```
url: https://cds.climate.copernicus.eu/api
key: <your-api-key>
```

Then run the full pipeline:

```bash
uv run python scripts/generate_mock_data.py
```

If CDS setup is not available yet, continue using:

```bash
uv run python scripts/generate_mock_data.py --skip-weather
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
