"""Proves the config file's `generate:` section can skip individual outputs
independently of the others, and that an unknown key under `generate:` is
rejected loudly rather than silently ignored.
"""
from __future__ import annotations

import pytest
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from src.serl_mock.paths import mock_hh_dirname, mock_daily_dirname
from tests._shared import TINY_CONFIG


def test_generate_epc_false_skips_only_epc(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {**TINY_CONFIG, "generate": {"epc": False}}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_dir = fake_mock_root / "edition08"
    assert not (out_dir / "serl_epc_data_edition08.csv").exists()
    # Other contextual outputs and smart-meter data are unaffected.
    assert (out_dir / "serl_survey_data_edition08.csv").exists()
    assert any((out_dir / mock_hh_dirname("08")).glob("*.csv"))
    assert any((out_dir / mock_daily_dirname("08")).glob("*.csv"))


def test_generate_smart_meter_flags_skip_their_folders(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {
        **TINY_CONFIG,
        "generate": {"hh_smart_meter": False, "daily_smart_meter": False, "rt_summary": False},
    }
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    out_dir = fake_mock_root / "edition08"
    assert not any((out_dir / mock_hh_dirname("08")).glob("*.csv"))
    assert not any((out_dir / mock_daily_dirname("08")).glob("*.csv"))
    # Contextual data (unaffected by these flags) still gets produced.
    assert (out_dir / "serl_epc_data_edition08.csv").exists()


def test_unknown_generate_key_raises(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {**TINY_CONFIG, "generate": {"survvey": False}}  # typo
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown key"):
        run_all(skip_weather=True, config_path=cfg_path)
