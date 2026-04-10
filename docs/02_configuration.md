# Configuration Reference

All settings live in a single file: `config/serl_mock.yaml`.

---

## Global settings

```yaml
n_households: 100   # Number of synthetic households to generate
seed: 42            # Master random seed — same seed → identical output
edition: "08"       # Appended to all output filenames as _edition<N>
```

---

## Time period

```yaml
start_year: 2019
end_year:   2019
```

One monthly HH CSV is written for every month in `[start_year, end_year]` inclusive.  Setting `end_year: 2023` produces 5 × 12 = 60 files.

---

## Household profiles

Controls the **population-level** consumption parameters.  Each household draws its own values from these distributions at initialisation.

```yaml
profiles:
  base_elec_mean_wh: 175    # Mean baseline electricity per HH period (Wh)
  base_elec_std_wh:   50    # Std of per-household baseline electricity
  base_gas_mean_wh: 1500    # Mean baseline gas at peak heating demand (Wh)
  base_gas_std_wh:   300    # Std of per-household baseline gas
  gas_fraction:     0.85    # Fraction of households with a gas meter (0–1)
```

> To shift the whole population to higher consumption, increase `base_elec_mean_wh` or `base_gas_mean_wh`.  
> To make households more similar to each other, decrease the `_std` values.

---

## Generation patterns

Controls the **shape** of seasonal and intraday curves and random noise behaviour.

```yaml
patterns:
  elec_seasonal_amplitude:      0.3    # ±30% swing around annual electricity mean
  gas_seasonal_amplitude:       2.0    # Winter gas heating multiplier (0 in summer)
  elec_spike_probability:      0.02    # Per-reading probability of an appliance spike
  elec_spike_max_wh:         2000.0    # Maximum extra Wh added by a spike
  summer_hot_water_probability: 0.15   # Probability of non-zero gas outside heating season
  gas_heating_threshold:        0.1    # gas_seasonal value below which "summer" rules apply
```

> To produce flatter consumption (no seasonal swing), set `elec_seasonal_amplitude: 0` and `gas_seasonal_amplitude: 0`.  
> To remove appliance spikes, set `elec_spike_probability: 0`.  
> To model an all-electric population, set `gas_fraction: 0`.

---

## Filenames (optional)

Contextual output filenames default to SERL Edition 08 naming.  They can be overridden via the `filenames:` key if needed:

```yaml
filenames:
  epc:              epc_data
  survey:           serl_survey_data
  summary:          serl_participant_summary_data
  followup_prefix:  serl_follow_up_survey
  exporters_prefix: Elec
```

---

## Adding a new setting

1. Add the key and a default value to `serl_mock.yaml`.
2. Read it in the relevant `__init__` method with `cfg.get("your_key", default)`.
3. Document it here.

---

## Weather settings

Controls the ERA5 download and CSV conversion performed in Step 3.

```yaml
weather:
  dataset:      "reanalysis-era5-single-levels"  # CDS dataset identifier
  product_type: "reanalysis"
  output_format: "netcdf"   # "netcdf" (.nc) or "grib"

  # ERA5 variables — aligned with the SERL climate data schema
  variables:
    - "2m_temperature"
    - "surface_solar_radiation_downwards"
    - "total_precipitation"
    - "10m_u_component_of_wind"
    - "10m_v_component_of_wind"

  # UK bounding box [north, west, south, east] in decimal degrees
  area: [60.0, -8.0, 49.0, 2.0]

  # Spatial resolution [lat_step, lon_step] in degrees
  grid: [0.25, 0.25]

  # Hourly download (ERA5 native resolution)
  time_step_hours: 1

  # Override date range (defaults to top-level start_year / end_year)
  # start_year: 2019
  # end_year:   2019

  # Override output directory (defaults to data/mock/serl_climate_data_edition08/)
  # output_dir: "data/mock/serl_climate_data_edition08"
```

> **CDS credentials required.** Register at https://cds.climate.copernicus.eu and add `~/.cdsapirc`:
> ```
> url: https://cds.climate.copernicus.eu/api
> key: <your-api-key>
> ```
> Use `--skip-weather` to bypass the download step if credentials are unavailable.

### Idempotent execution

- If a `.nc` file already exists for a month, it is **not re-downloaded**.
- If a `.csv` file already exists for a month, it is **not re-converted**.
- Re-running the pipeline is safe; only missing files are produced.
