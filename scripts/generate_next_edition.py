"""
Generate a mock later edition derived from a mock Edition08 run.

Same readings as the source except for planted, seeded changes, each
defined by the status it moves a reading from and to (resolved:
gap_filled, value_filled, value_corrected; introduced: reading_dropped,
value_lost, value_spoiled; value_changed). A flag only changes with its
value.
For testing comparisons across editions. See src/serl_mock/next_edition.py
and docs/02_configuration.md.

  uv run python scripts/generate_next_edition.py --config config/next_edition/edition09_from_edition08_gappy.yaml

Writes data/mock/<output_label>/ (half-hourly files in the output
edition's format, bst_dates_to_2030.csv) and the ground truth
mock_internal/edition_changes.csv and edition_changes_manifest.json.
Not real SERL data.
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.next_edition import convert, load_config


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--config", type=Path, required=True, help="config/next_edition/<name>.yaml")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="write here instead of data/mock/<output_label>/")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    manifest = convert(cfg, out_dir=args.output_dir)
    print(f"[next] wrote {args.output_dir or cfg.output_label} (edition {cfg.output_edition})")
    for kind, n in manifest["totals"].items():
        print(f"[next]   {kind}: {n:,}")
    if manifest["shortfall"]:
        print(f"[next] shortfall (requested but no candidates) in {len(manifest['shortfall'])} month(s); "
              f"see the manifest")


if __name__ == "__main__":
    main()
