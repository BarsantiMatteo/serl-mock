"""Proves the generator resolves reference dictionaries by BOTH the active
edition's reference folder AND the dictionary's own edition-numbered filename
(paths.dictionary_source_edition) — not just the folder. Without this, an
edition whose dictionaries are correctly named for itself (e.g. a future
edition09 shipping serl_survey_data_dictionary_edition09.csv, unlike
edition08 which still borrows edition07-named files) would silently fail to
be found.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from tests._shared import TINY_CONFIG


def test_edition08_resolves_to_the_known_edition07_named_dictionary():
    assert paths_module.dictionary_source_edition("08") == "07"


def test_an_edition_without_an_override_resolves_to_its_own_name():
    assert paths_module.dictionary_source_edition("09") == "09"
    assert paths_module.dictionary_source_edition("10") == "10"


def test_generator_uses_editions_own_correctly_named_dictionary(tmp_path, monkeypatch):
    # Simulate edition09 shipping its own accurately-named dictionary, unlike
    # edition08's borrowed edition07-named ones.
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

    monkeypatch.setattr(paths_module, "REFERENCE_DIR", fake_reference_root)

    config = {**TINY_CONFIG, "edition": "09"}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path / "mock_output"
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)

    survey = pd.read_csv(output_dir / "serl_survey_data_edition09.csv")
    assert "ZZZ_TEST_MARKER" in survey.columns
