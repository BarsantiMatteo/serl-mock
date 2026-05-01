"""
Generate mock SERL smart-meter and contextual data.

Loads settings from config/serl_mock.yaml and produces:

  1. puprn_master.csv               — shared household ID list
  2. Monthly half-hourly CSVs       — realistic electricity and gas time series
                                       with seasonal and intraday patterns
                                       (see src/serl_mock/patterns.py)
  3. Yearly daily CSVs              — daily sums of electricity and gas,
                                       one file per calendar year
  4. ERA5 weather data              — hourly NetCDF files downloaded from the
                                       Copernicus Climate Data Store (CDS API)
                                       and converted to CSV files in the SERL
                                       climate data schema
  5. Contextual datasets            — EPC, survey, participant summary,
                                       follow-up survey, list of exporters
                                       (participant summary includes LSOA and
                                       ERA5 grid_cell per household, derived
                                       from the same grid spec used to download
                                       the weather data)

Output is saved under data/mock/.  The generated data is not real SERL data
and is intended only for pipeline testing and local development outside the
Trusted Research Environment.

Weather download requires CDS API credentials in ~/.cdsapirc (or via the
CDSAPI_URL / CDSAPI_KEY environment variables).  Pass --skip-weather to skip
the download step if credentials are not available.
"""

# --- src-layout shim ---
import argparse
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.paths import CONFIG_DIR, MOCK_DIR, MOCK_HH_DIR, MOCK_DAILY_DIR
from src.serl_mock.ids import make_alphanumeric_ids_ordered, write_puprn_list_csv
from src.serl_mock.generator_smartmeter import HHSmartMeterGenerator, DailySmartMeterGenerator
from src.serl_mock.generator_contextual_data import SERLContextualVariablesGenerator
from src.serl_mock.weather_downloader import WeatherDownloader
from src.serl_mock.utils import read_config



def run_all(skip_weather: bool = False):
    cfg_path = CONFIG_DIR / "serl_mock.yaml"
    cfg = read_config(cfg_path)

    # Ensure target folders exist
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_HH_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_DAILY_DIR.mkdir(parents=True, exist_ok=True)

    print("\nStep 1: Generating PUPRNs")
    puprn_csv = MOCK_DIR / "puprn_master.csv"
    puprns = make_alphanumeric_ids_ordered(
        n=cfg.get("n_households", 100),
        seed=cfg.get("seed", 42),
        length=cfg.get("puprn", {}).get("length", 8)
    )
    write_puprn_list_csv(puprns, puprn_csv)
    print(f"  {len(puprns)} PUPRNs written to {puprn_csv}")

    print(f"\nStep 2: Generating half-hourly smart meter data "
          f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
          f"edition {cfg.get('edition', '08')})")
    gen_sm = HHSmartMeterGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_sm.generate_all(outfolder=MOCK_HH_DIR)

    print(f"\nStep 3: Generating daily smart meter data "
          f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
          f"edition {cfg.get('edition', '08')})")
    gen_daily = DailySmartMeterGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_daily.generate_all(outfolder=MOCK_DAILY_DIR)

    print("\nStep 4: Downloading ERA5 weather data and converting to CSV")
    if skip_weather:
        print("  Skipped (--skip-weather flag set).")
    else:
        dl = WeatherDownloader(config_path=str(cfg_path))
        nc_count = 0
        csv_count = 0
        for year in range(dl.start_year, dl.end_year + 1):
            for month in range(1, 13):
                try:
                    nc_path, csv_path = dl.ensure_month(year, month)
                    if nc_path.exists():
                        nc_count += 1
                    if csv_path.exists():
                        csv_count += 1
                except Exception as exc:
                    print(f"  WARNING: {year}-{month:02d} failed — {exc}")
        print(f"  {nc_count} NetCDF file(s) and {csv_count} CSV file(s) in {dl.output_dir}")

    print("\nStep 5: Generating contextual variables")
    gen_ctx = SERLContextualVariablesGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_ctx.write_all(outfolder=MOCK_DIR)

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate mock SERL datasets.")
    parser.add_argument(
        "--skip-weather",
        action="store_true",
        help="Skip the ERA5 weather data download step (step 3).",
    )
    args = parser.parse_args()
    run_all(skip_weather=args.skip_weather)

