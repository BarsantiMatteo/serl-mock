"""Regenerate the committed golden manifest(s) used by tests/test_golden_manifest.py.

Run this deliberately, after reviewing the diff, whenever an edition's
output is *intended* to change shape (new/renamed columns, different dtype,
different row count for the same fixture config). It is never run
automatically — a silent regeneration would defeat the point of the golden
file, which is to force a human decision whenever a committed edition's
output shape changes. See docs/notes/edition_multiformat_plan.md, Phase 0.

Usage:
    uv run python scripts/update_golden_manifest.py
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIG, build_manifest, write_golden_manifest


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_path = tmp_path / "serl_mock.yaml"
        cfg_path.write_text(yaml.safe_dump(TINY_CONFIG), encoding="utf-8")

        output_dir = tmp_path / "mock_output"
        run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)

        manifest = build_manifest(output_dir)
        edition = str(TINY_CONFIG["edition"])
        golden_path = write_golden_manifest(edition, manifest)
        print(f"Wrote {golden_path} ({len(manifest['files'])} files captured).")
        print("Review the diff before committing — this is the new baseline "
              "for edition{} output shape.".format(edition))


if __name__ == "__main__":
    main()
