"""Proves the generator resolves reference dictionaries by BOTH the active
edition's reference folder AND the dictionary's own edition-numbered filename
(edition.Edition.dictionary_source_edition) — not just the folder. Without
this, an edition whose dictionaries are correctly named for itself (e.g.
edition09 shipping serl_survey_data_dictionary_edition09.csv, unlike
edition08 which still borrows edition07-named files) would silently fail to
be found.
"""
from __future__ import annotations

import pandas as pd
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from src.serl_mock.edition import get_edition
from tests._shared import TINY_CONFIG


def test_edition08_resolves_to_the_known_edition07_named_dictionary():
    assert get_edition("08").dictionary_source_edition == "07"


def test_edition09_resolves_to_its_own_name():
    assert get_edition("09").dictionary_source_edition == "09"


def test_generator_uses_editions_own_correctly_named_dictionary(tmp_path, monkeypatch):
    # edition09 ships its own accurately-named dictionary, unlike edition08's
    # borrowed edition07-named ones.
    fake_reference_root = tmp_path / "reference"
    edition09_dir = fake_reference_root / "edition09"
    edition09_dir.mkdir(parents=True)
    pd.DataFrame({
        "Variable": ["PUPRN", "ZZZ_TEST_MARKER"],
        "Value": ["", ""],
        "ValueDescription": ["", ""],
        "QuestionOrMeaning": ["", ""],
        "FreeText": [False, False],
        "Type": ["", ""],
    }).to_csv(edition09_dir / "serl_survey_data_dictionary_edition09.csv", index=False)
    # write_all() always generates EPC too, which needs a generated-fields
    # list — unrelated to what this test is checking, so keep it minimal.
    pd.DataFrame({"Variable": ["PUPRN", "builtForm"]}).to_csv(
        edition09_dir / "serl_epc_generated_fields.csv", index=False,
    )

    monkeypatch.setattr(paths_module, "REFERENCE_DIR", fake_reference_root)

    # edition09 -> format="parquet" per the Edition registry.
    config = {**TINY_CONFIG, "edition": "09"}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path / "mock_output"
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)

    survey = pd.read_parquet(output_dir / "serl_survey_data_edition09.parquet")
    assert "ZZZ_TEST_MARKER" in survey.columns
