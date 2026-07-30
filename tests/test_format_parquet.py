"""Proves the format abstraction actually produces valid Parquet output
end-to-end for a real edition (edition09, whose Edition definition in
src/serl_mock/edition.py sets format="parquet"), not just that edition08's
CSV output is unchanged. Format is bound to the edition — there is no config
override, so the only way to get Parquet output is to configure edition="09".

Uses the shared generated_output_dir_edition09 fixture (tests/conftest.py) so
the pipeline only runs once per test session, shared with
test_golden_manifest.py's edition09 golden-manifest test.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from tests._shared import build_manifest


def test_edition_dataset_files_are_parquet(generated_output_dir_edition09: Path):
    assert (generated_output_dir_edition09 / "serl_epc_data_edition09.parquet").exists()
    assert (generated_output_dir_edition09 / "serl_survey_data_edition09.parquet").exists()
    assert not (generated_output_dir_edition09 / "serl_epc_data_edition09.csv").exists()

    hh_dir = generated_output_dir_edition09 / "serl_smart_meter_hh_edition09"
    assert (hh_dir / "serl_half_hourly_2021_01_edition09.parquet").exists()
    assert not list(hh_dir.glob("*.csv"))


def test_internal_only_files_stay_csv(generated_output_dir_edition09: Path):
    # household_traits.csv and the exporter list are mock-tool-internal, not
    # part of the SERL-edition-mimicking output, so they ignore the edition's format.
    assert (generated_output_dir_edition09 / "mock_internal" / "household_traits.csv").exists()
    assert (generated_output_dir_edition09 / "mock_internal" / "puprn_master.csv").exists()


def test_rt_summary_reads_back_correctly_as_parquet(generated_output_dir_edition09: Path):
    # ReadTypeDataQualitySummaryGenerator reads the HH/daily files it just
    # wrote back in to build the summary — this only succeeds if the
    # table_exists/read_table format-awareness in generator_smartmeter.py
    # actually works, not just the write side.
    rt_summary = generated_output_dir_edition09 / "serl_smart_meter_rt_summary_edition09.parquet"
    assert rt_summary.exists()
    df = pd.read_parquet(rt_summary)
    assert len(df) > 0


def test_edition09_output_has_same_structure_as_edition08(
    generated_output_dir_edition09: Path, generated_output_dir: Path,
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
    #
    # Daily smart-meter files are excluded: edition09 deliberately uses a
    # different daily-smart-meter layout ("single_file", no subfolder) than
    # edition08 ("yearly", in its own subfolder) — see
    # tests/test_daily_layout_edition09.py for that comparison instead.
    edition09_manifest = build_manifest(generated_output_dir_edition09)
    edition08_manifest = build_manifest(generated_output_dir)

    # Compare by basename with the edition suffix stripped, since both differ.
    def normalize(files):
        result = {}
        for rel, shape in files.items():
            if "smart_meter_daily" in rel:
                continue
            key = Path(rel).with_suffix("").as_posix().replace("edition09", "editionNN").replace("edition08", "editionNN")
            result[key] = {"columns": shape.get("columns"), "rows": shape.get("rows")}
        return result

    assert normalize(edition09_manifest["files"]) == normalize(edition08_manifest["files"])
