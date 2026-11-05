# src/serl_mock/raw_from_edition.py
"""Mock *raw* half-hourly deliveries made from a mock Edition08 run.

SERL's raw half-hourly readings (before they are wrapped into an edition)
come as monthly CSVs `SERL_half_hourly_YYYY_MM.csv` with fewer, differently
named columns: no local time, validity or flag columns, no Gas_hh_Wh, gas in
litres, and an extra `Read_date_time_effective` (1 s before Read_date_time).
`convert()` rewrites a mock Edition08 run's half-hourly files into that
format and plants known, deterministic differences, so a raw-vs-edition
comparison (serl-dq) has a ground truth to find:

  removed     readings dropped from raw (one household-day per month, plus
              scattered single readings)
  changed     one value of a reading changed
  null        one value of a reading emptied
  duplicated  a reading written twice (exact copy)
  missing household  one household absent from every raw file
  extra household    one household only in raw (a copy of another
                     household's readings under a new PUPRN)

Every planted reading (all kinds but the two household ones, which are
whole households) is listed in `mock_internal/raw_planted_readings.csv`;
`mock_internal/raw_planted_manifest.json` has the counts, the two
households and the settings. Everything else is the edition's reading,
converted: Wh/varh values unchanged, gas litres = m3 * 1000.

Each month draws from its own rng (seed, year, month), so a month's plants
don't depend on which other months are converted.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .paths import REFERENCE_DIR, mock_hh_dirname

# Raw column -> Edition08 column, in raw file order. Read_date_time_effective
# has no Edition column: derived from Read_date_time (minus 1 s).
RAW_TO_EDITION: Dict[str, Optional[str]] = {
    "PUPRN": "PUPRN",
    "Read_date_time": "Read_date_time_UTC",
    "HH_segment": "HH",
    "elec_active_import_profile_hh_wh": "Elec_act_imp_hh_Wh",
    "elec_reactive_import_profile_hh_wh": "Elec_react_imp_hh_varh",
    "elec_active_export_profile_hh_wh": "Elec_act_exp_hh_Wh",
    "elec_reactive_export_profile_hh_wh": "Elec_react_exp_hh_varh",
    "gas_active_import_profile_hh_L": "Gas_hh_m3",
    "Read_date_effective": "Read_date_effective_local",
    "Read_date_time_effective": None,
}
RAW_COLUMNS: List[str] = list(RAW_TO_EDITION)
VALUE_COLUMNS: List[str] = [c for c in RAW_COLUMNS if c.startswith(("elec_", "gas_"))]
GAS_COLUMN = "gas_active_import_profile_hh_L"
LITRES_PER_M3 = 1000.0

RAW_FILE_STEM = "SERL_half_hourly"
EDITION_FILE_RE = re.compile(r"^serl_half_hourly_(\d{4})_(\d{2})_edition08\.csv$")
BST_FILE = "bst_dates_to_2030.csv"
READINGS_FILE = "raw_planted_readings.csv"
MANIFEST_FILE = "raw_planted_manifest.json"

READINGS_COLUMNS = [
    "kind", "file", "PUPRN", "Read_date_time", "raw_column", "edition_column",
    "edition_value", "raw_value", "raw_value_in_edition_units",
]


@dataclass(frozen=True)
class PlantCounts:
    """How many of each planted difference per month."""
    removed_household_days: int = 1   # whole (household, Read_date_effective) blocks
    removed_readings: int = 25        # scattered single readings
    changed_values: int = 25
    null_values: int = 25
    duplicated_readings: int = 5


def _month_rng(seed: int, year: int, month: int) -> np.random.Generator:
    return np.random.default_rng([seed, year, month])


def find_edition_files(source_dir: Path) -> List[Tuple[int, int, Path]]:
    """(year, month, path) of each Edition08 half-hourly file in a mock run's
    output folder, in date order."""
    hh_dir = Path(source_dir) / mock_hh_dirname("08")
    found = []
    for path in sorted(hh_dir.glob("serl_half_hourly_*_edition08.csv")):
        m = EDITION_FILE_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), int(m.group(2)), path))
    if not found:
        raise FileNotFoundError(f"No Edition08 half-hourly CSVs in {hh_dir}")
    return found


def raw_file_name(year: int, month: int) -> str:
    return f"{RAW_FILE_STEM}_{year}_{month:02d}.csv"


def to_raw(edition: pd.DataFrame) -> pd.DataFrame:
    """One month of Edition08 half-hourly readings (all columns read as
    strings, empty = missing) in the raw format, before any planting."""
    raw = pd.DataFrame({raw: edition[ed] for raw, ed in RAW_TO_EDITION.items() if ed is not None})
    gas = edition["Gas_hh_m3"]
    has_gas = gas != ""
    litres = (pd.to_numeric(gas[has_gas]) * LITRES_PER_M3).round(6)
    raw[GAS_COLUMN] = ""
    raw.loc[has_gas, GAS_COLUMN] = litres.map(_fmt_float)
    # Read_date_time_effective: 1 s before Read_date_time (few distinct
    # stamps per month, so convert the uniques only).
    stamps = raw["Read_date_time"]
    uniq = pd.Series(stamps.unique())
    parsed = pd.to_datetime(uniq, format="%Y-%m-%dT%H:%M:%SZ", errors="coerce")
    shifted = (parsed - pd.Timedelta(seconds=1)).dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")
    raw["Read_date_time_effective"] = stamps.map(dict(zip(uniq, shifted)))
    return raw[RAW_COLUMNS].reset_index(drop=True)


def _fmt_float(x: float) -> str:
    return repr(float(x))


def _fmt_like(old: str, new: float) -> str:
    """`new` written like `old`: an integer if `old` is, else a float."""
    if re.fullmatch(r"-?\d+", old):
        return str(int(round(new)))
    return _fmt_float(round(new, 6))


def _edition_units(raw_column: str, raw_value: str) -> str:
    if raw_value == "" or raw_column != GAS_COLUMN:
        return raw_value
    return _fmt_float(round(float(raw_value) / LITRES_PER_M3, 9))


def _new_puprn(rng: np.random.Generator, taken: set) -> str:
    alphabet = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
    while True:
        puprn = "".join(rng.choice(alphabet, 8))
        if puprn not in taken:
            return puprn


def pick_households(first_month: pd.DataFrame, seed: int) -> Tuple[str, str, str]:
    """(missing, donor, extra) PUPRNs: the household left out of raw, the
    household whose readings the extra one copies, and the extra one's
    new PUPRN."""
    rng = np.random.default_rng([seed, 0])
    puprns = sorted(first_month["PUPRN"].unique())
    if len(puprns) < 3:
        raise ValueError("Need at least 3 households to plant a missing and an extra one")
    missing, donor = rng.choice(puprns, 2, replace=False)
    return str(missing), str(donor), _new_puprn(rng, set(puprns))


def plant_month(
    raw: pd.DataFrame,
    year: int,
    month: int,
    seed: int,
    counts: PlantCounts,
    missing: str,
    donor: str,
    extra: str,
) -> Tuple[pd.DataFrame, List[dict], Dict[str, int]]:
    """Plant one month's differences into its raw frame. Returns the planted
    frame, the planted-reading records and per-kind counts (incl. the rows
    dropped for the missing household and added for the extra one)."""
    rng = _month_rng(seed, year, month)
    fname = raw_file_name(year, month)
    records: List[dict] = []

    def record(kind, row, column=None, raw_value=None):
        ed_col = RAW_TO_EDITION[column] if column else None
        ed_val = _edition_units(column, row[column]) if column else None
        records.append({
            "kind": kind, "file": fname, "PUPRN": row["PUPRN"],
            "Read_date_time": row["Read_date_time"], "raw_column": column,
            "edition_column": ed_col, "edition_value": ed_val, "raw_value": raw_value,
            "raw_value_in_edition_units": _edition_units(column, raw_value) if column and raw_value is not None else None,
        })

    # Row position in the edition file: the output keeps the edition's order,
    # a duplicate right after its original, the extra household right after
    # its donor.
    raw = raw.assign(_pos=np.arange(len(raw), dtype=float))
    is_missing = raw["PUPRN"] == missing
    n_missing_rows = int(is_missing.sum())
    extra_rows = raw[raw["PUPRN"] == donor].copy()
    extra_rows["PUPRN"] = extra
    extra_rows["_pos"] += 0.5
    raw = raw[~is_missing].reset_index(drop=True)

    # Target pool: not the donor (its copy should stay identical), and only
    # readings whose (PUPRN, Read_date_time) is unique, so every planted
    # reading is identifiable by its key.
    key_unique = ~raw.duplicated(["PUPRN", "Read_date_time"], keep=False)
    eligible = key_unique & (raw["PUPRN"] != donor)

    # Whole household-days removed.
    remove = np.zeros(len(raw), dtype=bool)
    days = raw.loc[eligible, ["PUPRN", "Read_date_effective"]].drop_duplicates()
    n_days = min(counts.removed_household_days, len(days))
    if n_days:
        chosen = days.iloc[np.sort(rng.choice(len(days), n_days, replace=False))]
        day_mask = raw.set_index(["PUPRN", "Read_date_effective"]).index.isin(
            pd.MultiIndex.from_frame(chosen))
        remove |= day_mask
    for i in np.flatnonzero(remove):
        record("removed_household_day", raw.iloc[i])

    # Scattered single-reading plants, disjoint from each other and the days.
    pool = np.flatnonzero(eligible.to_numpy() & ~remove)
    sizes = [counts.removed_readings, counts.changed_values, counts.null_values, counts.duplicated_readings]
    take = rng.choice(pool, min(sum(sizes), len(pool)), replace=False)
    groups, start = [], 0
    for n in sizes:
        groups.append(np.sort(take[start:start + n]))
        start += n
    rm_idx, chg_idx, null_idx, dup_idx = groups

    for i in rm_idx:
        record("removed", raw.iloc[i])
    remove[rm_idx] = True

    values = raw[VALUE_COLUMNS].to_numpy(dtype=object)
    for kind, idx in (("changed", chg_idx), ("null", null_idx)):
        for i in idx:
            present = [j for j, c in enumerate(VALUE_COLUMNS) if values[i, j] != ""]
            if not present:
                continue  # nothing to change in an all-empty reading
            j = int(rng.choice(present))
            col = VALUE_COLUMNS[j]
            old = values[i, j]
            if kind == "null":
                new = ""
            elif col == GAS_COLUMN:
                new = _fmt_like(old, float(old) + float(rng.integers(10, 1000)) / 10)
            else:
                new = _fmt_like(old, float(old) + float(rng.integers(1, 51)))
            record(kind, raw.iloc[i], col, new)
            raw.iat[i, raw.columns.get_loc(col)] = new

    for i in dup_idx:
        record("duplicated", raw.iloc[i])

    duplicates = raw.iloc[dup_idx]
    out = pd.concat([raw[~remove], duplicates, extra_rows], ignore_index=True)
    out = out.sort_values("_pos", kind="stable").drop(columns="_pos")

    n = {
        "removed_household_day": int(remove.sum()) - len(rm_idx),
        "removed": len(rm_idx),
        "changed": sum(r["kind"] == "changed" for r in records),
        "null": sum(r["kind"] == "null" for r in records),
        "duplicated": len(dup_idx),
        "missing_household_rows": n_missing_rows,
        "extra_household_rows": len(extra_rows),
    }
    return out.reset_index(drop=True), records, n


def convert(
    source_dir: Path,
    out_dir: Path,
    seed: int = 42,
    counts: PlantCounts = PlantCounts(),
) -> dict:
    """Write the raw half-hourly files for a mock Edition08 run into
    `out_dir` (plus the BST dates file, which a raw data version needs, and
    the planted-differences ground truth under mock_internal/). Returns the
    manifest."""
    source_dir, out_dir = Path(source_dir), Path(out_dir)
    files = find_edition_files(source_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    internal = out_dir / "mock_internal"
    internal.mkdir(exist_ok=True)
    for old in out_dir.glob(f"{RAW_FILE_STEM}_*.csv"):
        old.unlink()  # a rerun with fewer months shouldn't leave stale files

    bst = source_dir / BST_FILE
    shutil.copyfile(bst if bst.exists() else REFERENCE_DIR / BST_FILE, out_dir / BST_FILE)

    usecols = [c for c in RAW_TO_EDITION.values() if c is not None]
    read = lambda p: pd.read_csv(p, usecols=usecols, dtype=str, keep_default_na=False, na_filter=False)

    households = None
    all_records: List[dict] = []
    per_month: Dict[str, Dict[str, int]] = {}
    for year, month, path in files:
        edition = read(path)
        if households is None:
            households = pick_households(edition, seed)
        raw = to_raw(edition)
        del edition
        planted, records, n = plant_month(raw, year, month, seed, counts, *households)
        planted.to_csv(out_dir / raw_file_name(year, month), index=False)
        all_records += records
        per_month[f"{year}-{month:02d}"] = {"edition_rows": len(raw), "raw_rows": len(planted), **n}
        print(f"[raw] {raw_file_name(year, month)}: {len(raw):,} -> {len(planted):,} rows")

    pd.DataFrame(all_records, columns=READINGS_COLUMNS).to_csv(internal / READINGS_FILE, index=False)
    missing, donor, extra = households
    kinds = next(iter(per_month.values())).keys()
    manifest = {
        "description": "Mock raw half-hourly readings made from mock Edition08, with planted "
                       "differences. Not real SERL data.",
        "source_dir": str(source_dir),
        "seed": seed,
        "plant_counts_per_month": asdict(counts),
        "missing_household": missing,
        "extra_household": {"PUPRN": extra, "copied_from": donor},
        "column_mapping": {k: v for k, v in RAW_TO_EDITION.items()},
        "conversions": {
            "gas_active_import_profile_hh_L": "Gas_hh_m3 * 1000",
            "Read_date_time_effective": "Read_date_time - 1 s",
        },
        "dropped_edition_columns": ["Read_date_time_local", "Valid_read_time", "Gas_hh_Wh",
                                    "Elec_act_imp_flag", "Elec_act_exp_flag",
                                    "Elect_react_imp_flag", "Elect_react_exp_flag", "Gas_flag"],
        "planted_readings_file": f"mock_internal/{READINGS_FILE}",
        "totals": {k: sum(m[k] for m in per_month.values()) for k in kinds},
        "per_month": per_month,
    }
    (internal / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
