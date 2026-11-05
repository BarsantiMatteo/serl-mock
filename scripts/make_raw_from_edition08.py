"""
Make mock *raw* half-hourly deliveries from a mock Edition08 run.

Rewrites the run's half-hourly files into SERL's raw format
(SERL_half_hourly_YYYY_MM.csv: renamed columns, no flags / local time /
validity / Gas_hh_Wh, gas in litres, plus Read_date_time_effective) and
plants known differences — removed readings, changed and emptied values,
duplicated readings, one household missing and one extra — so a raw vs
edition comparison has a ground truth. See src/serl_mock/raw_from_edition.py.

  uv run python scripts/make_raw_from_edition08.py
  uv run python scripts/make_raw_from_edition08.py --source data/mock/edition08 --out data/mock/raw_x

Writes <out>/SERL_half_hourly_*.csv, <out>/bst_dates_to_2030.csv and the
ground truth <out>/mock_internal/raw_planted_{manifest.json,readings.csv}.
Not real SERL data.
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.paths import mock_dir_for
from src.serl_mock.raw_from_edition import PlantCounts, convert


def main(argv=None) -> None:
    defaults = PlantCounts()
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--source", type=Path, default=mock_dir_for("edition08_with_2025survey"),
                   help="mock Edition08 run folder (default: data/mock/edition08_with_2025survey)")
    p.add_argument("--out", type=Path, default=mock_dir_for("raw_from_edition08"),
                   help="output folder (default: data/mock/raw_from_edition08)")
    p.add_argument("--seed", type=int, default=42)
    for name, value in vars(defaults).items():
        p.add_argument(f"--{name.replace('_', '-')}", type=int, default=value,
                       help=f"per month (default {value})")
    args = p.parse_args(argv)
    counts = PlantCounts(**{k: getattr(args, k) for k in vars(defaults)})
    manifest = convert(args.source, args.out, seed=args.seed, counts=counts)
    print(f"[raw] wrote {args.out}")
    print(f"[raw] missing household {manifest['missing_household']}, extra "
          f"{manifest['extra_household']['PUPRN']} (copy of {manifest['extra_household']['copied_from']})")
    for kind, n in manifest["totals"].items():
        print(f"[raw]   {kind}: {n:,}")


if __name__ == "__main__":
    main()
