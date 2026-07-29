"""Regenerate the committed golden manifest(s) used by tests/test_golden_manifest.py.

Run this deliberately, after reviewing the diff, whenever an edition's
output is *intended* to change shape (new/renamed columns, different dtype,
different row count for the same fixture config). It is never run
automatically — a silent regeneration would defeat the point of the golden
file, which is to force a human decision whenever a committed edition's
output shape changes. See docs/notes/edition_multiformat_plan.md, Phase 0.

Usage:
    uv run python scripts/update_golden_manifest.py            # all known editions
    uv run python scripts/update_golden_manifest.py --edition 09   # just one
"""
import argparse
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIGS_BY_EDITION, build_manifest, write_golden_manifest


def update_one(edition: str) -> None:
    config = TINY_CONFIGS_BY_EDITION[edition]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_path = tmp_path / "serl_mock.yaml"
        cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

        output_dir = tmp_path / "mock_output"
        run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)

        manifest = build_manifest(output_dir)
        golden_path = write_golden_manifest(edition, manifest)
        print(f"Wrote {golden_path} ({len(manifest['files'])} files captured).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edition",
        choices=sorted(TINY_CONFIGS_BY_EDITION),
        default=None,
        help="Regenerate only this edition's manifest (default: all known editions).",
    )
    args = parser.parse_args()

    editions = [args.edition] if args.edition else sorted(TINY_CONFIGS_BY_EDITION)
    for edition in editions:
        update_one(edition)

    print("Review the diff before committing — these are the new baselines "
          "for the regenerated editions' output shape.")


if __name__ == "__main__":
    main()
