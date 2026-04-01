# src/serl_mock/utils.py
from __future__ import annotations
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

PathLike = Union[str, os.PathLike]

import numpy as np
import pandas as pd

# YAML is optional; import if available
try:
    import yaml
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False

# ---------- Config ----------
def read_config(path: Optional[PathLike]) -> Dict[str, Any]:
    """
    Load JSON or YAML configuration. 
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    if p.suffix.lower() in (".yaml", ".yml"):
        if not _HAS_YAML:
            raise RuntimeError("PyYAML not installed but YAML config provided.")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
    return data

# ---------- RNG ----------
def seed_random(seed: int) -> None:
    """
    Seed Python's and NumPy's RNGs for reproducibility.
    """
    random.seed(seed)
    np.random.seed(seed)

# ---------- I/O ----------
def ensure_output_dir(path: PathLike) -> str:
    os.makedirs(path, exist_ok=True)
    return str(path)

def with_edition_suffix(basename: str, edition: Optional[str]) -> str:
    """
    Add '_edition{edition}.csv' if edition is a non-empty string; else append '.csv'.
    """
    return f"{basename}_edition{edition}.csv" if edition else f"{basename}.csv"

def write_csv(df: pd.DataFrame, path: PathLike, encoding: str = "utf-8") -> None:
    df.to_csv(path, index=False, encoding=encoding)

# ---------- Survey dictionary ----------
def read_survey_dictionary(path: str) -> List[str]:
    """
    Read a SERL survey data dictionary CSV and return a list of unique variable names.
    Ensures 'PUPRN' is first. Falls back to a minimal default if not found.
    """
    try:
        df = pd.read_csv(path)
        variables = df["Variable"].unique().tolist()
        if "PUPRN" not in variables:
            variables.insert(0, "PUPRN")
        return variables
    except Exception:
        # Minimal fallback (from your dev script)
        return [
            "PUPRN", "Survey_version", "Recorded_date", "Collection_method",
            "Language", "A1", "A2", "B1", "B4", "B5", "B5_err", "C1", "C1_new", "D1", "D2", "D4"
        ]