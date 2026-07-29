"""Proves the format abstraction (Phase 1 of docs/notes/edition_multiformat_plan.md)
actually produces valid Parquet output end-to-end for a real edition (edition09,
whose Edition definition in src/serl_mock/edition.py sets format="parquet"), not
just that edition08's CSV output is unchanged. Format is bound to the edition
(Phase 2) — there is no config override, so the only way to get Parquet output
is to configure edition="09".
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIG, build_manifest


@pytest.fixture(scope="module")
def edition09_output_dir(tmp_path_factory) -> Path:
    config = {**TINY_CONFIG, "edition": "09"}
    cfg_dir = tmp_path_factory.mktemp("edition09_config")
    cfg_path = cfg_dir / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path_factory.mktemp("edition09_output")
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)
    return output_dir


def test_edition_dataset_files_are_parquet(edition09_output_dir: Path):
    assert (edition09_output_dir / "serl_epc_data_edition09.parquet").exists()
    assert (edition09_output_dir / "serl_survey_data_edition09.parquet").exists()
    assert not (edition09_output_dir / "serl_epc_data_edition09.csv").exists()

    hh_dir = edition09_output_dir / "serl_smart_meter_hh_edition09"
    assert (hh_dir / "serl_half_hourly_2021_01_edition09.parquet").exists()
    assert not list(hh_dir.glob("*.csv"))


def test_internal_only_files_stay_csv(edition09_output_dir: Path):
    # household_traits.csv and the exporter list are mock-tool-internal, not
    # part of the SERL-edition-mimicking output, so they ignore the edition's format.
    assert (edition09_output_dir / "mock_internal" / "household_traits.csv").exists()
    assert (edition09_output_dir / "mock_internal" / "puprn_master.csv").exists()


def test_rt_summary_reads_back_correctly_as_parquet(edition09_output_dir: Path):
    # ReadTypeDataQualitySummaryGenerator reads the HH/daily files it just
    # wrote back in to build the summary — this only succeeds if the
    # table_exists/read_table format-awareness in generator_smartmeter.py
    # actually works, not just the write side.
    rt_summary = edition09_output_dir / "serl_smart_meter_rt_summary_edition09.parquet"
    assert rt_summary.exists()
    df = pd.read_parquet(rt_summary)
    assert len(df) > 0


def test_edition09_output_has_same_structure_as_edition08(
    edition09_output_dir: Path, generated_output_dir: Path,
):
    # edition09's reference dictionaries are currently placeholder copies of
    # edition08's (data/reference/edition09/README.md), so the dictionary-driven
    # datasets (survey, covid survey, follow-up survey) should have identical
    # columns/rows to edition08's — this would catch the format work
    # accidentally changing content, not just container format. Dtypes are
    # deliberately NOT compared: CSV is a lossy text format for dtype fidelity
    # (e.g. the nullable "HH" column round-trips as float64 through CSV — text
    # can't distinguish a blank in a nullable int column from a blank in a
    # float column — but Parquet preserves it exactly as Int64). That's an
    # inherent difference between the formats, not a regression, and is
    # exactly why per-edition golden manifests are never diffed against each other.
    edition09_manifest = build_manifest(edition09_output_dir)
    edition08_manifest = build_manifest(generated_output_dir)

    # Compare by basename with the edition suffix stripped, since both differ.
    def normalize(files):
        result = {}
        for rel, shape in files.items():
            key = Path(rel).with_suffix("").as_posix().replace("edition09", "editionNN").replace("edition08", "editionNN")
            result[key] = {"columns": shape.get("columns"), "rows": shape.get("rows")}
        return result

    assert normalize(edition09_manifest["files"]) == normalize(edition08_manifest["files"])
