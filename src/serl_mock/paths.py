# src/serl_mock/paths.py

from pathlib import Path

# Resolve project root automatically (directory containing pyproject.toml)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "Edition08"
MOCK_DIR = DATA_DIR / "mock"

# Target folder for monthly half-hourly mock files
MOCK_HH_DIR = MOCK_DIR / "serl_smart_meter_hh_edition08"