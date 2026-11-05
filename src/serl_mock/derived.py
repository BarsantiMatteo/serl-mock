# src/serl_mock/derived.py
"""Shared parts of the generators that derive data from an existing mock
Edition08 run: the raw half-hourly deliveries (`raw_from_edition.py`) and
the later edition (`next_edition.py`).

- finding the source run's half-hourly files and copying the BST dates file;
- config validation helpers (unknown keys, counts, months);
- seeded rngs per (seed, year, month[, salt]);
- half-hour slots: which ones each household has, and the gaps inside its
  covered range (no reading there, nor an off-grid one within 30 min);
- fill values: the same half-hour a week earlier, else a median;
- where a new row goes so a file keeps the edition's order.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from .paths import REFERENCE_DIR, mock_hh_dirname

STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
BST_FILE = "bst_dates_to_2030.csv"
EDITION_FILE_RE = re.compile(r"^serl_half_hourly_(\d{4})_(\d{2})_edition08\.csv$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


# ---------------------------------------------------------------------------
# Source files
# ---------------------------------------------------------------------------

def find_edition_files(source_dir: Path, start: Optional[str] = None,
                       end: Optional[str] = None) -> List[Tuple[int, int, Path]]:
    """(year, month, path) of each Edition08 half-hourly file in a mock run's
    output folder within [start, end] (YYYY-MM, None = open), in date order."""
    hh_dir = Path(source_dir) / mock_hh_dirname("08")
    found = []
    for path in sorted(hh_dir.glob("serl_half_hourly_*_edition08.csv")):
        m = EDITION_FILE_RE.match(path.name)
        if not m:
            continue
        ym = f"{m.group(1)}-{m.group(2)}"
        if (start and ym < start) or (end and ym > end):
            continue
        found.append((int(m.group(1)), int(m.group(2)), path))
    if not found:
        raise FileNotFoundError(f"No Edition08 half-hourly CSVs in {hh_dir}"
                                + (f" for months {start or '...'} to {end or '...'}" if start or end else ""))
    return found


def copy_bst(source_dir: Path, out_dir: Path) -> None:
    """The BST dates file into `out_dir`: the source run's copy, else the
    reference one (a derived version needs it for serl-dq's calendar)."""
    bst = Path(source_dir) / BST_FILE
    shutil.copyfile(bst if bst.exists() else REFERENCE_DIR / BST_FILE, Path(out_dir) / BST_FILE)


def check_folders(source_dir: Path, out_dir: Path) -> None:
    if Path(source_dir).resolve() == Path(out_dir).resolve():
        raise ValueError("output folder must differ from the source folder")


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def check_keys(cfg, allowed: Iterable[str], where: str) -> None:
    """`cfg` is a mapping with no key outside `allowed`."""
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} must be a mapping")
    unknown = set(cfg) - set(allowed)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}; expected {sorted(allowed)}")


def require_source(cfg: dict, where: str) -> str:
    if not cfg.get("source"):
        raise ValueError(f"{where}: `source` (a folder under data/mock/) is required")
    return str(cfg["source"])


def int_value(value, where: str, minimum: Optional[int] = 0) -> int:
    """An int (not a bool), at least `minimum` (None = any)."""
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        raise ValueError(f"{where} must be an integer" + (f" >= {minimum}" if minimum is not None else "")
                         + f", got {value!r}")
    return value


def parse_counts(counts, cls, key: str, where: str):
    """`counts` (mapping of kind -> count >= 0) as dataclass `cls`; omitted
    kinds take the dataclass defaults."""
    counts = counts or {}
    if not isinstance(counts, dict):
        raise ValueError(f"{where}: {key} must be a mapping")
    known = set(cls.__dataclass_fields__)
    unknown = set(counts) - known
    if unknown:
        raise ValueError(f"{where}: unknown {key} key(s) {sorted(unknown)}; expected {sorted(known)}")
    return cls(**{k: int_value(v, f"{key}.{k}") for k, v in counts.items()})


def parse_months(months, where: str) -> Tuple[Optional[str], Optional[str]]:
    """`months: {start, end}` (YYYY-MM or null, start <= end) -> (start, end)."""
    months = months or {}
    if not isinstance(months, dict) or set(months) - {"start", "end"}:
        raise ValueError(f"{where}: months must be {{start, end}}, got {months!r}")
    for k in ("start", "end"):
        m = months.get(k)
        if m is not None and not _MONTH_RE.match(str(m)):
            raise ValueError(f"{where}: months.{k} must be YYYY-MM or null, got {m!r}")
    start, end = months.get("start"), months.get("end")
    if start and end and str(start) > str(end):
        raise ValueError(f"{where}: months.start {start} is after months.end {end}")
    return (str(start) if start else None, str(end) if end else None)


# ---------------------------------------------------------------------------
# Rngs and shortfall
# ---------------------------------------------------------------------------

def month_rng(seed: int, year: int, month: int, *salt: int) -> np.random.Generator:
    """One month's rng; `salt` keeps one kind's draws apart from another's."""
    return np.random.default_rng([seed, year, month, *salt])


def note_shortfall(shortfall: dict, kind: str, requested: int, planted: int) -> None:
    if planted < requested:
        shortfall[kind] = requested - planted


# ---------------------------------------------------------------------------
# Half-hour slots and gaps
# ---------------------------------------------------------------------------

def stamp_slots(puprns: pd.Series, stamps: pd.Series) -> pd.DataFrame:
    """Each reading's household, the half-hour slots (minutes since the
    epoch / 30) at or before (`lo`) and at or after (`hi`) its stamp, equal
    for an on-grid stamp, and its row position; unparseable stamps left out."""
    t = pd.to_datetime(stamps, format=STAMP_FMT, errors="coerce", utc=True)
    ok = t.notna()
    minutes = ((t[ok] - _EPOCH) // pd.Timedelta(minutes=1)).to_numpy(dtype=np.int64)
    return pd.DataFrame({"PUPRN": puprns[ok].to_numpy(), "lo": minutes // 30,
                         "hi": -(-minutes // 30), "row": np.flatnonzero(ok.to_numpy())})


def gap_slots(occ: pd.DataFrame) -> pd.DataFrame:
    """(PUPRN, slot) of every half-hour inside each household's covered
    range (first to last on-grid reading) with no reading, nor an off-grid
    one within 30 min; sorted by PUPRN, slot. `occ` from stamp_slots()."""
    occupied = pd.concat([occ[["PUPRN", "lo"]].rename(columns={"lo": "slot"}),
                          occ[["PUPRN", "hi"]].rename(columns={"hi": "slot"})]).drop_duplicates()
    span = occ[occ["lo"] == occ["hi"]].groupby("PUPRN")["lo"].agg(["min", "max"])
    full = pd.DataFrame({
        "PUPRN": np.repeat(span.index.to_numpy(), (span["max"] - span["min"] + 1).to_numpy()),
        "slot": np.concatenate([np.arange(a, b + 1) for a, b in zip(span["min"], span["max"])])
                if len(span) else np.array([], dtype=np.int64),
    })
    cand = full.merge(occupied, on=["PUPRN", "slot"], how="left", indicator=True)
    cand = cand[cand["_merge"] == "left_only"][["PUPRN", "slot"]].sort_values(["PUPRN", "slot"])
    return cand.reset_index(drop=True)


def slot_stamps(slots: pd.Series) -> pd.Series:
    """UTC timestamps (interval ends) of half-hour slots."""
    return pd.to_datetime(slots * 30, unit="m", utc=True)


# ---------------------------------------------------------------------------
# Fill values and positions
# ---------------------------------------------------------------------------

def week_earlier(rows: pd.DataFrame, time_col: str, col: str, stamp: pd.Timestamp) -> Optional[str]:
    """`col` of the reading in `rows` stamped a week before `stamp`, if it
    has a value."""
    week = (stamp - pd.Timedelta(days=7)).strftime(STAMP_FMT)
    prev = rows.loc[rows[time_col] == week, col]
    prev = prev[prev != ""]
    return str(prev.iloc[0]) if len(prev) else None


def median_of(rows: pd.DataFrame, col: str) -> Optional[float]:
    vals = pd.to_numeric(rows[col][rows[col] != ""])
    return float(vals.median()) if len(vals) else None


def insert_position(stamp_text: np.ndarray, stamp: str, n_rows: int, r: int) -> float:
    """Row position for a new reading stamped `stamp`: just before the first
    row stamped later (the edition's files run in time order); `r` keeps
    several new rows at one position in a fixed order."""
    later = np.flatnonzero(stamp_text > stamp)
    return (later[0] if len(later) else n_rows) - 0.5 + r * 1e-6
