"""
Generate mock SERL smart-meter and contextual data.

Loads settings from config/serl_mock.yaml by default — pass --config to use a
different saved config file (e.g. config/serl_mock_edition09.yaml). The
config's `edition` setting determines output format, smart-meter file
layout, and which reference dictionaries are used — see
src/serl_mock/edition.py; these are not independent config keys, so a config
file can't accidentally combine an edition with a format/layout it doesn't
use. Produces:

  1. puprn_master.csv               — shared household ID list
     household_traits.csv           — household traits (PV/HP/EV + meter types)
                                       (always CSV — mock-tool-internal, not
                                       part of any real SERL edition)
  2. Half-hourly smart-meter data   — realistic electricity and gas time series
                                       with seasonal and intraday patterns
                                       (see src/serl_mock/patterns.py); split
                                       into files per the edition's smart-meter
                                       layout (monthly by default) and written
                                       in the edition's format (csv or parquet)
  3. Daily smart-meter data         — daily sums of electricity and gas,
                                       split into files per the edition's
                                       daily-smart-meter layout (yearly by
                                       default, in its own subfolder; or a
                                       single combined file at the top level
                                       of the run's output) and written in
                                       the edition's format
  4. ERA5 weather data              — hourly NetCDF files downloaded from the
                                       Copernicus Climate Data Store (CDS API)
                                       and converted to CSV files in the SERL
                                       climate data schema (always CSV — not
                                       yet wired to the edition's format)
  5. Contextual datasets            — EPC, survey, participant summary,
                                       follow-up survey, list of exporters, in
                                       the edition's format (participant
                                       summary includes LSOA and ERA5
                                       grid_cell per household, derived from
                                       the same grid spec used to download
                                       the weather data)

Output is saved under data/mock/<output_label>/, where output_label defaults
to edition<N> (e.g. data/mock/edition08/) so different editions or scenarios
never collide. The generated data is not real SERL data and is intended only
for pipeline testing and local development outside the Trusted Research
Environment.

Weather download requires CDS API credentials in ~/.cdsapirc (or via the
CDSAPI_URL / CDSAPI_KEY environment variables).  Pass --skip-weather to skip
the download step if credentials are not available.
"""

# --- src-layout shim ---
import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.paths import (
    CONFIG_DIR, MOCK_INTERNAL_DIR, MOCK_AGGREGATED_DIR, REFERENCE_DIR,
    reference_dir_for, mock_dir_for, mock_hh_dirname, mock_daily_dirname, mock_climate_dirname,
)
from src.serl_mock.edition import get_edition
from src.serl_mock.layout import get_daily_layout
from src.serl_mock.ids import make_alphanumeric_ids_ordered, write_puprn_list_csv, load_puprn_list_csv
from src.serl_mock.generator_smartmeter import HHSmartMeterGenerator, DailySmartMeterGenerator, ReadTypeDataQualitySummaryGenerator
from src.serl_mock.generator_contextual_data import SERLContextualVariablesGenerator
from src.serl_mock.generator_household_traits import generate_household_traits, write_household_traits
from src.serl_mock.weather_downloader import WeatherDownloader
from src.serl_mock.utils import read_config



def run_all(
    skip_weather: bool = False,
    survey_only: bool = False,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
):
    """Run the full mock-data pipeline.

    config_path / output_dir let callers (tests, `--survey-only` re-runs
    against a scratch location, etc.) redirect the pipeline away from the
    real config/serl_mock.yaml and data/mock/ tree. Both default to the
    real locations so normal CLI usage is unaffected.
    """
    cfg_path = Path(config_path) if config_path is not None else (CONFIG_DIR / "serl_mock.yaml")
    cfg = read_config(cfg_path)

    edition = str(cfg.get("edition", "08")).zfill(2)
    edition_def = get_edition(edition)  # raises if `edition` has no Edition definition
    output_label = str(cfg.get("output_label") or f"edition{edition}")

    # Output folders — each run's output is nested under data/mock/<output_label>/
    # (defaulting to "edition<N>") so different editions, or differently-configured
    # runs of the same edition, don't collide. `output_dir` overrides this entirely
    # (used by tests to redirect into a scratch location).
    mock_dir = Path(output_dir) if output_dir is not None else mock_dir_for(output_label)
    mock_hh_dir = mock_dir / mock_hh_dirname(edition)
    # Daily smart-meter data only gets its own subfolder if its layout uses
    # one (e.g. "yearly") — a "single_file" layout writes directly into
    # mock_dir, since one file doesn't need a folder to itself.
    daily_layout = get_daily_layout(edition_def.daily_smart_meter_layout)
    mock_daily_dir = mock_dir / mock_daily_dirname(edition) if daily_layout.uses_subfolder else mock_dir
    mock_climate_dir = mock_dir / mock_climate_dirname(edition)
    mock_internal_dir = mock_dir / MOCK_INTERNAL_DIR.name
    mock_aggregated_dir = mock_dir / MOCK_AGGREGATED_DIR.name

    # Ensure target folders exist
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_hh_dir.mkdir(parents=True, exist_ok=True)
    mock_daily_dir.mkdir(parents=True, exist_ok=True)
    mock_internal_dir.mkdir(parents=True, exist_ok=True)
    mock_aggregated_dir.mkdir(parents=True, exist_ok=True)

    print("\nStep 0: Copying reference files to mock folder")
    # Transversal files (not edition-specific) — copied as-is from the reference root.
    for fname in ["bst_dates_to_2030.csv"]:
        src = REFERENCE_DIR / fname
        if src.exists():
            shutil.copy2(src, mock_dir / fname)
            print(f"  Copied {fname}")
    # Data dictionaries: edition-specific, sourced from data/reference/edition<N>/,
    # named for whichever edition their content actually matches (see
    # edition_def.dictionary_source_edition), copied with the suffix updated to
    # the active edition.
    edition_reference_dir = reference_dir_for(edition)
    dict_source_edition = edition_def.dictionary_source_edition
    dict_renames = {
        f"serl_survey_data_dictionary_edition{dict_source_edition}.csv":
            f"serl_survey_data_dictionary_edition{edition}.csv",
        f"serl_covid19_survey_data_dictionary_edition{dict_source_edition}.csv":
            f"serl_covid19_survey_data_dictionary_edition{edition}.csv",
    }
    for src_name, dst_name in dict_renames.items():
        src = edition_reference_dir / src_name
        if src.exists():
            shutil.copy2(src, mock_dir / dst_name)
            print(f"  Copied {src_name} -> {dst_name}")

    print("\nStep 0b: Creating placeholder files")
    placeholders = [
        f"serl_tariff_data_edition{edition}.csv",
        "serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv",
    ]
    for fname in placeholders:
        p = mock_dir / fname
        if not p.exists():
            p.write_text("# placeholder\n", encoding="utf-8")
            print(f"  Created {fname}")

    puprn_csv = mock_internal_dir / "puprn_master.csv"
    traits_csv = mock_internal_dir / "household_traits.csv"

    if survey_only:
        if not puprn_csv.exists() or not traits_csv.exists():
            sys.exit(
                "ERROR: --survey-only requires a previous full run. "
                f"Missing: {', '.join(str(p) for p in [puprn_csv, traits_csv] if not p.exists())}"
            )
        print("  Skipping steps 1-4 (--survey-only).")
    else:
        print("\nStep 1: Generating PUPRNs and household device traits")
        puprns = make_alphanumeric_ids_ordered(
            n=cfg.get("n_households", 100),
            seed=cfg.get("seed", 42),
            length=cfg.get("puprn", {}).get("length", 8)
        )
        write_puprn_list_csv(puprns, puprn_csv)
        print(f"  {len(puprns)} PUPRNs written to {puprn_csv}")

        traits_cfg = cfg.get("household_traits", {})
        traits_df = generate_household_traits(
            puprns=puprns,
            pv_fraction=float(traits_cfg.get("pv_fraction", 0.07)),
            hp_fraction=float(traits_cfg.get("hp_fraction", 0.0)),
            ev_fraction=float(traits_cfg.get("ev_fraction", 0.0)),
            gas_meter_fraction=float(traits_cfg.get("gas_meter_fraction", 0.85)),
            export_meter_fraction=float(traits_cfg.get("export_meter_fraction", 0.15)),
            solar_thermal_fraction=float(traits_cfg.get("solar_thermal_fraction", 0.0)),
            seed=cfg.get("seed", 42),
        )
        write_household_traits(traits_df, traits_csv)
        print(f"  Household traits written to {traits_csv}")
        print(f"    PV households: {(traits_df['has_pv'] == 1).sum()}")
        print(f"    HP households: {(traits_df['has_hp'] == 1).sum()}")
        print(f"    EV households: {(traits_df['has_ev'] == 1).sum()}")
        print(f"    Solar thermal households: {(traits_df['has_solar_thermal'] == 1).sum()}")
        print(f"    Gas meter households: {(traits_df['has_gas_meter'] == 1).sum()}")
        print(f"    Export meter households: {(traits_df['has_export_meter'] == 1).sum()}")

        print(f"\nStep 2: Generating half-hourly smart meter data "
              f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
              f"edition {cfg.get('edition', '08')})")
        gen_sm = HHSmartMeterGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
            traits_path=str(traits_csv),
        )
        gen_sm.generate_all(outfolder=mock_hh_dir)

        print(f"\nStep 3: Generating daily smart meter data "
              f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
              f"edition {cfg.get('edition', '08')})")
        gen_daily = DailySmartMeterGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
            traits_path=str(traits_csv),
        )
        gen_daily.generate_all(outfolder=mock_daily_dir)

        print(f"\nStep 3b: Generating read-type data quality summary "
              f"(edition {cfg.get('edition', '08')})")
        gen_rt = ReadTypeDataQualitySummaryGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
            traits_path=str(traits_csv),
        )
        gen_rt.generate_and_write(
            hh_folder=mock_hh_dir,
            daily_folder=mock_daily_dir,
            outfolder=mock_dir,
        )

    print("\nStep 4: Downloading ERA5 weather data and converting to CSV")
    if survey_only or skip_weather:
        print("  Skipped.")
    else:
        try:
            dl = WeatherDownloader(config_path=str(cfg_path), output_dir=str(mock_climate_dir))
            dl._get_client()  # fail fast: surface credential / connectivity errors now
        except Exception as exc:
            print(f"  WARNING: CDS API unavailable — {exc}")
            print(f"  Skipping weather download; placeholder folder created at {mock_climate_dir}")
            mock_climate_dir.mkdir(parents=True, exist_ok=True)
        else:
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
        traits_path=str(traits_csv),
        climate_dir=str(mock_climate_dir),
    )
    gen_ctx.write_all(outfolder=mock_dir, mock_only_outfolder=mock_internal_dir)

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate mock SERL datasets.")
    parser.add_argument(
        "--skip-weather",
        action="store_true",
        help="Skip the ERA5 weather data download step (step 4).",
    )
    parser.add_argument(
        "--survey-only",
        action="store_true",
        help="Regenerate only the survey/contextual data (step 5). Requires a prior full run.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to a config YAML to use instead of config/serl_mock.yaml — e.g. a "
            "saved per-edition config such as config/serl_mock_edition09.yaml."
        ),
    )
    args = parser.parse_args()
    run_all(skip_weather=args.skip_weather, survey_only=args.survey_only, config_path=args.config)

