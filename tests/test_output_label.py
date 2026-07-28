"""Proves the default output nesting — data/mock/<output_label>/, defaulting to
data/mock/edition<N>/ — actually happens when run_all() is called WITHOUT an
output_dir override. Every other test passes output_dir explicitly to stay
isolated from the real data/mock/ tree, so none of them exercise this default
branch; this test does, by monkeypatching paths.MOCK_DIR to a scratch location
instead.
"""
from __future__ import annotations

import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from tests._shared import TINY_CONFIG


def test_default_output_is_nested_under_edition_label(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(TINY_CONFIG), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)  # no output_dir override

    assert (fake_mock_root / "edition08" / "serl_epc_data_edition08.csv").exists()
    assert not (fake_mock_root / "serl_epc_data_edition08.csv").exists()


def test_output_label_can_be_overridden(tmp_path, monkeypatch):
    fake_mock_root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", fake_mock_root)

    config = {**TINY_CONFIG, "output_label": "edition08_custom_run"}
    cfg_path = tmp_path / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    run_all(skip_weather=True, config_path=cfg_path)

    assert (fake_mock_root / "edition08_custom_run" / "serl_epc_data_edition08.csv").exists()
    assert not (fake_mock_root / "edition08").exists()
