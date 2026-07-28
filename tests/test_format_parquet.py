"""Proves the format abstraction (Phase 1 of docs/notes/edition_multiformat_plan.md)
actually produces valid Parquet output end-to-end, not just that CSV output is
unchanged. Uses its own fixtures (not the shared CSV-based ones in conftest.py)
since only this test needs format="parquet".
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIG, build_manifest


@pytest.fixture(scope="module")
def parquet_output_dir(tmp_path_factory) -> Path:
    config = {**TINY_CONFIG, "format": "parquet"}
    cfg_dir = tmp_path_factory.mktemp("parquet_config")
    cfg_path = cfg_dir / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path_factory.mktemp("parquet_output")
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)
    return output_dir


def test_edition_dataset_files_are_parquet(parquet_output_dir: Path):
    assert (parquet_output_dir / "serl_epc_data_edition08.parquet").exists()
    assert (parquet_output_dir / "serl_survey_data_edition08.parquet").exists()
    assert not (parquet_output_dir / "serl_epc_data_edition08.csv").exists()

    hh_dir = parquet_output_dir / "serl_smart_meter_hh_edition08"
    assert (hh_dir / "serl_half_hourly_2021_01_edition08.parquet").exists()
    assert not list(hh_dir.glob("*.csv"))


def test_internal_only_files_stay_csv(parquet_output_dir: Path):
    # household_traits.csv and the exporter list are mock-tool-internal, not
    # part of the SERL-edition-mimicking output, so they ignore `format`.
    assert (parquet_output_dir / "mock_internal" / "household_traits.csv").exists()
    assert (parquet_output_dir / "mock_internal" / "puprn_master.csv").exists()


def test_rt_summary_reads_back_correctly_as_parquet(parquet_output_dir: Path):
    # ReadTypeDataQualitySummaryGenerator reads the HH/daily files it just
    # wrote back in to build the summary — this only succeeds if the
    # table_exists/read_table format-awareness in generator_smartmeter.py
    # actually works, not just the write side.
    rt_summary = parquet_output_dir / "serl_smart_meter_rt_summary_edition08.parquet"
    assert rt_summary.exists()
    df = pd.read_parquet(rt_summary)
    assert len(df) > 0


def test_parquet_output_matches_csv_structure(parquet_output_dir: Path, generated_output_dir: Path):
    # Same fixture config, only the format differs — columns and row counts
    # should be identical either way. Dtypes are deliberately NOT compared:
    # CSV is a lossy text format for dtype fidelity (e.g. the nullable "HH"
    # column round-trips as float64 through CSV — text can't distinguish a
    # blank in a nullable int column from a blank in a float column — but
    # Parquet preserves it exactly as Int64). That's an inherent difference
    # between the formats, not a regression, and is exactly why per-edition
    # golden manifests are never diffed against each other.
    parquet_manifest = build_manifest(parquet_output_dir)
    csv_manifest = build_manifest(generated_output_dir)

    # Compare by basename (without extension) since the file suffix differs.
    def strip_ext(files):
        return {
            Path(rel).with_suffix(""): {"columns": shape.get("columns"), "rows": shape.get("rows")}
            for rel, shape in files.items()
        }

    assert strip_ext(parquet_manifest["files"]) == strip_ext(csv_manifest["files"])
