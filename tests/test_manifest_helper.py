"""Unit tests for tests/_shared.py::build_manifest.

The real pipeline only produces CSV today (Phase 1 of
docs/notes/edition_multiformat_plan.md hasn't landed yet), so these tests
exercise build_manifest directly against synthetic files rather than through
run_all(). This is what proves the golden-manifest harness will correctly
see Edition09's output once it switches to Parquet, instead of discovering
that gap only after Phase 1 ships.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from tests._shared import build_manifest

SAMPLE_DF = pd.DataFrame({"PUPRN": ["A1", "A2"], "value": [1, 2]})


def test_sees_csv_files(tmp_path: Path):
    SAMPLE_DF.to_csv(tmp_path / "data.csv", index=False)

    manifest = build_manifest(tmp_path)

    assert manifest["files"]["data.csv"] == {
        "columns": ["PUPRN", "value"],
        "dtypes": {"PUPRN": "str", "value": "int64"},
        "rows": 2,
    }


def test_sees_parquet_files(tmp_path: Path):
    SAMPLE_DF.to_parquet(tmp_path / "data.parquet", index=False)

    manifest = build_manifest(tmp_path)

    assert manifest["files"]["data.parquet"] == {
        "columns": ["PUPRN", "value"],
        "dtypes": {"PUPRN": "str", "value": "int64"},
        "rows": 2,
    }


def test_sees_mixed_csv_and_parquet_in_same_run(tmp_path: Path):
    SAMPLE_DF.to_csv(tmp_path / "legacy.csv", index=False)
    SAMPLE_DF.to_parquet(tmp_path / "new_format.parquet", index=False)

    manifest = build_manifest(tmp_path)

    assert set(manifest["files"]) == {"legacy.csv", "new_format.parquet"}


def test_ignores_non_table_files(tmp_path: Path):
    (tmp_path / "README.txt").write_text("not a table", encoding="utf-8")

    manifest = build_manifest(tmp_path)

    assert manifest["files"] == {}
