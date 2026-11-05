"""
Generate mock *raw* half-hourly deliveries from a mock Edition08 run.

Rewrites the source run's half-hourly files into SERL's raw format
(SERL_half_hourly_YYYY_MM.csv: renamed columns, no flags / local time /
validity / Gas_hh_Wh, gas in litres, plus Read_date_time_effective) and
plants known differences between raw and edition, as the config says.
See src/serl_mock/raw_from_edition.py and docs/02_configuration.md.

  uv run python scripts/generate_raw_data.py --config config/raw/raw_from_edition08.yaml
  uv run python scripts/generate_raw_data.py --config config/raw/raw_from_edition08_gappy.yaml

Writes data/mock/<output_label>/SERL_half_hourly_*.csv, bst_dates_to_2030.csv
and the ground truth mock_internal/raw_planted_{manifest.json,readings.csv}.
Not real SERL data.
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.raw_from_edition import convert, load_config


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--config", type=Path, required=True, help="config/raw/<name>.yaml")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="write here instead of data/mock/<output_label>/")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    manifest = convert(cfg, out_dir=args.output_dir)
    h = manifest["households"]
    print(f"[raw] wrote {args.output_dir or cfg.output_label}")
    print(f"[raw] households not in raw: {h['household_not_in_raw']}; not in edition: "
          + ", ".join(f"{e['PUPRN']} (copy of {e['copied_from']})" for e in h["household_not_in_edition"]))
    for kind, n in manifest["totals"].items():
        print(f"[raw]   {kind}: {n:,}")
    if manifest["shortfall"]:
        print(f"[raw] shortfall (requested but no candidates) in {len(manifest['shortfall'])} month(s); "
              f"see the manifest")


if __name__ == "__main__":
    main()
