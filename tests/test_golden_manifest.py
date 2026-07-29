"""Structural regression test: compares freshly generated output for each
edition against its own committed golden manifest
(tests/golden/edition<N>_manifest.json).

This is the tripwire that fails loudly if a change made while adding
format/layout/schema support alters a committed edition's file list,
columns, dtypes, or row counts.
Manifests are never diffed against each other (see build_manifest/_shared.py):
edition09 legitimately having different files/columns/format than edition08
is expected, not a regression.

If a change is *intentional*, regenerate the golden file with
`uv run python scripts/update_golden_manifest.py` and review the diff before
committing it — do not silence this test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from tests._shared import build_manifest, load_golden_manifest


def _assert_matches_golden_manifest(output_dir: Path, edition: str) -> None:
    actual = build_manifest(output_dir)
    expected = load_golden_manifest(edition)

    expected_files: Dict[str, Any] = expected["files"]
    actual_files: Dict[str, Any] = actual["files"]

    missing = sorted(set(expected_files) - set(actual_files))
    unexpected = sorted(set(actual_files) - set(expected_files))
    assert not missing, f"files present in golden manifest but not generated: {missing}"
    assert not unexpected, (
        f"files generated but not in golden manifest: {unexpected} "
        f"(new edition{edition} output — update the golden manifest if intentional)"
    )

    mismatches = {}
    for rel_path, expected_shape in expected_files.items():
        actual_shape = actual_files[rel_path]
        if actual_shape != expected_shape:
            mismatches[rel_path] = {"expected": expected_shape, "actual": actual_shape}
    assert not mismatches, f"output shape changed for: {mismatches}"


def test_edition08_output_matches_golden_manifest(generated_output_dir: Path):
    _assert_matches_golden_manifest(generated_output_dir, "08")


def test_edition09_output_matches_golden_manifest(generated_output_dir_edition09: Path):
    _assert_matches_golden_manifest(generated_output_dir_edition09, "09")
