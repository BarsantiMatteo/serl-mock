from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_mock_data import run_all
from tests._shared import TINY_CONFIG, TINY_CONFIG_EDITION09


@pytest.fixture(scope="session")
def tiny_config_path(tmp_path_factory) -> Path:
    """Write the shared tiny fixture config (edition08) to a scratch file."""
    cfg_dir = tmp_path_factory.mktemp("config")
    cfg_path = cfg_dir / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(TINY_CONFIG), encoding="utf-8")
    return cfg_path


@pytest.fixture(scope="session")
def generated_output_dir(tmp_path_factory, tiny_config_path: Path) -> Path:
    """Run the full pipeline once per test session against the tiny edition08 config.

    skip_weather is always True: weather download hits a real external API
    and requires CDS credentials, so it is out of scope for these tests.
    """
    output_dir = tmp_path_factory.mktemp("mock_output")
    run_all(skip_weather=True, config_path=tiny_config_path, output_dir=output_dir)
    return output_dir


@pytest.fixture(scope="session")
def tiny_config_path_edition09(tmp_path_factory) -> Path:
    """Write the shared tiny fixture config (edition09) to a scratch file."""
    cfg_dir = tmp_path_factory.mktemp("config_edition09")
    cfg_path = cfg_dir / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(TINY_CONFIG_EDITION09), encoding="utf-8")
    return cfg_path


@pytest.fixture(scope="session")
def generated_output_dir_edition09(tmp_path_factory, tiny_config_path_edition09: Path) -> Path:
    """Run the full pipeline once per test session against the tiny edition09 config."""
    output_dir = tmp_path_factory.mktemp("mock_output_edition09")
    run_all(skip_weather=True, config_path=tiny_config_path_edition09, output_dir=output_dir)
    return output_dir
