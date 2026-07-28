"""Structural regression test: compares freshly generated edition08 output
against the committed golden manifest (tests/golden/edition08_manifest.json).

This is the tripwire for Phases 1-3 of docs/notes/edition_multiformat_plan.md —
it fails loudly if a change made while adding format/layout/schema support
alters edition08's committed file list, columns, dtypes, or row counts.

If a change is *intentional*, regenerate the golden file with
`uv run python scripts/update_golden_manifest.py` and review the diff before
committing it — do not silence this test.
"""
from __future__ import annotations

from pathlib import Path

from tests._shared import build_manifest, load_golden_manifest


def test_edition08_output_matches_golden_manifest(generated_output_dir: Path):
    actual = build_manifest(generated_output_dir)
    expected = load_golden_manifest("08")

    expected_files = expected["files"]
    actual_files = actual["files"]

    missing = sorted(set(expected_files) - set(actual_files))
    unexpected = sorted(set(actual_files) - set(expected_files))
    assert not missing, f"files present in golden manifest but not generated: {missing}"
    assert not unexpected, (
        f"files generated but not in golden manifest: {unexpected} "
        f"(new edition08 output — update the golden manifest if intentional)"
    )

    mismatches = {}
    for rel_path, expected_shape in expected_files.items():
        actual_shape = actual_files[rel_path]
        if actual_shape != expected_shape:
            mismatches[rel_path] = {"expected": expected_shape, "actual": actual_shape}
    assert not mismatches, f"output shape changed for: {mismatches}"
