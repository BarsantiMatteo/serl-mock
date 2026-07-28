"""Shared test fixtures config and manifest helpers.

Used by tests/conftest.py (pytest fixtures) and scripts/update_golden_manifest.py
(manual golden-file regeneration) so both stay in sync on what "the tiny
edition08 fixture" means.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd

# A small, fast, fully deterministic config used for all Phase 0 tests.
# Values are chosen so every trait fraction rounds to a non-zero count out
# of 5 households (e.g. 0.4 * 5 = 2), so trait-alignment checks have
# something to check.
TINY_CONFIG: Dict[str, Any] = {
    "n_households": 5,
    "seed": 123,
    "edition": "08",
    "start_year": 2021,
    "end_year": 2021,
    "household_traits": {
        "pv_fraction": 0.4,
        "hp_fraction": 0.2,
        "ev_fraction": 0.2,
        "gas_meter_fraction": 0.6,
        "export_meter_fraction": 0.4,
        "solar_thermal_fraction": 0.2,
    },
    "profiles": {
        "base_elec_mean_wh": 175,
        "base_elec_std_wh": 50,
        "base_gas_mean_wh": 1500,
        "base_gas_std_wh": 300,
        "gas_fraction": 0.85,
    },
    "patterns": {
        "elec_seasonal_amplitude": 0.3,
        "gas_seasonal_amplitude": 2.0,
        "elec_spike_probability": 0.02,
        "elec_spike_max_wh": 2000.0,
        "summer_hot_water_probability": 0.15,
        "gas_heating_threshold": 0.1,
    },
    "filenames": {
        "epc": "serl_epc_data",
        "covid19_survey": "serl_covid19_survey_data",
        "survey": "serl_survey_data",
        "followup_survey": "serl_2023_follow_up_survey_data",
        "summary": "serl_participant_summary",
    },
}

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def build_manifest(output_dir: Path) -> Dict[str, Any]:
    """Capture the structural shape of every CSV under output_dir.

    Records relative path, columns, dtypes, and row count for each CSV file
    — the properties a Phase 1-3 refactor must not change by accident for an
    edition that already shipped. Deliberately excludes cell values: this is
    a structure snapshot, not a data-content snapshot.
    """
    files: Dict[str, Any] = {}
    for csv_path in sorted(output_dir.rglob("*.csv")):
        rel = csv_path.relative_to(output_dir).as_posix()
        try:
            df = pd.read_csv(csv_path)
        except UnicodeDecodeError:
            # The follow-up survey is written with encoding="latin-1"
            # (generator_contextual_data.py) — retry before giving up.
            df = pd.read_csv(csv_path, encoding="latin-1")
        except Exception as exc:  # placeholder files, e.g. "# placeholder\n"
            files[rel] = {"error": str(exc)}
            continue
        files[rel] = {
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "rows": len(df),
        }
    return {"files": files}


def load_golden_manifest(edition: str) -> Dict[str, Any]:
    path = GOLDEN_DIR / f"edition{edition}_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No golden manifest at {path}. Run "
            f"`uv run python scripts/update_golden_manifest.py` to create it "
            f"after reviewing that the current output is correct."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_golden_manifest(edition: str, manifest: Dict[str, Any]) -> Path:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / f"edition{edition}_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
