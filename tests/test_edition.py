"""Proves the core guarantee behind the Edition object: there is no way for a
config file to set format/layout in conflict with its edition, because
format/layout are resolved from the Edition registry, not read as
independent config keys — a stray `format:`/`layout:` key in a config file
is simply ignored, not a silent source of inconsistency.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock.edition import get_edition
from tests._shared import TINY_CONFIG


def test_edition08_definition():
    ed = get_edition("08")
    assert ed.format == "csv"
    assert ed.hh_smart_meter_layout == "monthly"
    assert ed.dictionary_source_edition == "07"


def test_edition09_definition():
    ed = get_edition("09")
    assert ed.format == "parquet"
    assert ed.dictionary_source_edition == "09"


def test_unknown_edition_raises_a_clear_error():
    with pytest.raises(ValueError, match="Unknown edition"):
        get_edition("99")


def test_stray_format_key_in_an_edition09_config_is_ignored(tmp_path_factory):
    # Someone copies an edition08 config, changes `edition` to "09", but
    # forgets to remove a leftover `format: csv` line (or adds one by
    # mistake). This must NOT produce CSV output for edition09 — format
    # comes from the Edition registry, not this key.
    config = {**TINY_CONFIG, "edition": "09", "format": "csv", "layout": {"hh_smart_meter": "single_file"}}
    cfg_path = tmp_path_factory.mktemp("conflict_config") / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    output_dir = tmp_path_factory.mktemp("conflict_output")
    run_all(skip_weather=True, config_path=cfg_path, output_dir=output_dir)

    # Still parquet, still monthly — the stray config keys had zero effect.
    assert (output_dir / "serl_epc_data_edition09.parquet").exists()
    assert not (output_dir / "serl_epc_data_edition09.csv").exists()
    hh_dir = output_dir / "serl_smart_meter_hh_edition09"
    assert len(list(hh_dir.glob("*.parquet"))) == 12
