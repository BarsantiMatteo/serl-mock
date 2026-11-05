# src/serl_mock/raw_from_edition.py
"""Mock *raw* half-hourly deliveries made from a mock Edition08 run.

SERL's raw half-hourly readings (before they are wrapped into an edition)
come as monthly CSVs `SERL_half_hourly_YYYY_MM.csv` with fewer, differently
named columns: no local time, validity or flag columns, no Gas_hh_Wh, gas in
litres, and an extra `Read_date_time_effective` (1 s before Read_date_time).
`convert()` rewrites a mock Edition08 run's half-hourly files into that
format and plants known, deterministic differences between raw and edition,
so a raw-vs-edition comparison (serl-dq) has a ground truth to find. It is
driven by a config (config/raw/*.yaml, `load_config`); see
docs/02_configuration.md.

Planted kinds (`plants_per_month`; counts per month, 0 = off):

  changed_values             one value differs between raw and edition
  edition_blanked            raw has a value where the edition's is NULL   (realistic)
  edition_removed            raw has a reading the edition doesn't         (realistic)
  raw_missing_readings       the edition has a reading raw doesn't         (anomaly)
  raw_missing_household_days ditto, a whole household-day                  (anomaly)
  raw_emptied_values         raw NULL where the edition has a value        (anomaly)
  raw_duplicates             a reading written twice in raw (exact copy)
  off_grid_edition_empty     raw has a reading 1-14 min off its half-hour; the
                             edition dropped it and has an empty row (every
                             value NULL) at the half-hour                  (realistic)
  off_grid_edition_skip      ditto, the edition has no row at the half-hour (realistic)

A third way an edition can treat an off-grid raw reading — keep it at its
stamp — is in the source already (mock editions have off-grid stamps);
those are copied to raw and listed in `mock_internal/raw_off_grid_in_both.csv`.

plus, over the whole run, `missing_households` (only in the edition) and
`extra_households` (only in raw: a copy of another household's readings
under a new PUPRN, e.g. a withdrawn household).

The realistic kinds go the way real processing does (raw -> edition drops
or blanks a reading); they need gaps in the source edition: edition_blanked
fills an edition NULL, edition_removed and off_grid_edition_skip fill a
half-hour the edition lacks, off_grid_edition_empty replaces an all-NULL
edition row.
A source with fewer candidates than requested gets what exists; the
shortfall is printed and recorded in the manifest.

Ground truth: `mock_internal/raw_planted_readings.csv`, one row per planted
reading (value-level kinds: per reading and value column), with `category`
in serl-dq's comparison categories (A = raw, B = edition; CATEGORY);
`mock_internal/raw_planted_manifest.json` has the counts per category x
month x edition column, the households, any shortfall and the settings.
Everything not planted is the edition's reading, converted: Wh/varh values
unchanged, gas litres = m3 * 1000; the edition's own gaps and NULLs are
copied as they are, so they are identical on both sides.

Each month draws from its own rngs (seed, year, month[, kind]), so a
month's plants don't depend on which other months are converted. The kinds
step 2 of serl-dq's plan had (all but the realistic ones) use the same
draws in the same order as then, so a config with the realistic kinds off
reproduces that output byte for byte. The realistic kinds run after them,
in a fixed order, each on its own rng and never on what an earlier kind
took, so turning a later kind on leaves the earlier kinds' plants as they
were.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

from .derived import (
    STAMP_FMT, check_folders, check_keys, copy_bst, find_edition_files, gap_slots, insert_position,
    int_value, median_of, month_rng, note_shortfall, parse_counts, parse_months, require_source,
    slot_stamps, stamp_slots, week_earlier,
)
from .generator_smartmeter import HHSmartMeterGenerator
from .paths import mock_dir_for

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
EDITION_VALUE_COLUMNS: List[str] = [RAW_TO_EDITION[c] for c in VALUE_COLUMNS]
GAS_COLUMN = "gas_active_import_profile_hh_L"
LITRES_PER_M3 = 1000.0

RAW_FILE_STEM = "SERL_half_hourly"
READINGS_FILE = "raw_planted_readings.csv"
MANIFEST_FILE = "raw_planted_manifest.json"
OFF_GRID_FILE = "raw_off_grid_in_both.csv"
OFF_GRID_MAX_MINUTES = 14   # off-grid plants stay nearest to their own half-hour

# edition_read_date_time: the edition's timestamp for the reading when it
# differs from raw's (the off-grid kinds: the half-hour the edition has
# empty or lacks); empty otherwise.
READINGS_COLUMNS = [
    "category", "kind", "file", "PUPRN", "Read_date_time", "raw_column", "edition_column",
    "edition_value", "raw_value", "raw_value_in_edition_units", "edition_read_date_time",
]

# Planted kind -> serl-dq comparison category (A = raw, B = edition).
CATEGORY = {
    "changed_values": "changed",
    "edition_blanked": "emptied_in_edition",
    "edition_removed": "removed_in_edition",
    "raw_missing_readings": "only_in_edition",
    "raw_missing_household_days": "only_in_edition",
    "raw_emptied_values": "emptied_in_raw",
    "raw_duplicates": "duplicate_in_raw",
    "off_grid_edition_empty": "off_grid_in_raw_empty_in_edition",
    "off_grid_edition_skip": "off_grid_in_raw_not_in_edition",
}
# Kinds planted on one value of a reading; the others are whole readings.
VALUE_KINDS = {"changed_values", "edition_blanked", "raw_emptied_values"}
HOUSEHOLD_CATEGORY = {"extra": "household_not_in_edition", "missing": "household_not_in_raw"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlantCounts:
    """How many of each planted difference per month (0 = off)."""
    changed_values: int = 25
    edition_blanked: int = 25
    edition_removed: int = 25
    raw_missing_readings: int = 25
    raw_emptied_values: int = 25
    raw_duplicates: int = 5
    raw_missing_household_days: int = 1
    off_grid_edition_empty: int = 25
    off_grid_edition_skip: int = 25


@dataclass(frozen=True)
class RawConfig:
    source: str                         # data/mock/<source>/, an Edition08 mock run
    output_label: str                   # data/mock/<output_label>/
    seed: int = 42
    month_start: Optional[str] = None   # YYYY-MM, inclusive; None = the source's first
    month_end: Optional[str] = None     # YYYY-MM, inclusive; None = the source's last
    plants: PlantCounts = field(default_factory=PlantCounts)
    extra_households: int = 1
    missing_households: int = 1


_TOP_KEYS = {"source", "output_label", "seed", "months", "plants_per_month",
             "extra_households", "missing_households"}
_WHERE = "raw config"


def parse_config(cfg: dict) -> RawConfig:
    """Validate a raw-delivery config dict (unknown keys, bad counts and
    bad months raise)."""
    check_keys(cfg, _TOP_KEYS, _WHERE)
    source = require_source(cfg, _WHERE)
    start, end = parse_months(cfg.get("months"), _WHERE)
    return RawConfig(
        source=source,
        output_label=str(cfg.get("output_label") or f"raw_from_{source}"),
        seed=int_value(cfg.get("seed", 42), f"{_WHERE}: seed", minimum=None),
        month_start=start,
        month_end=end,
        plants=parse_counts(cfg.get("plants_per_month"), PlantCounts, "plants_per_month", _WHERE),
        extra_households=int_value(cfg.get("extra_households", 1), "extra_households"),
        missing_households=int_value(cfg.get("missing_households", 1), "missing_households"),
    )


def load_config(path: Path) -> RawConfig:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})


# ---------------------------------------------------------------------------
# Format conversion
# ---------------------------------------------------------------------------

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
    parsed = pd.to_datetime(uniq, format=STAMP_FMT, errors="coerce")
    shifted = (parsed - pd.Timedelta(seconds=1)).dt.strftime(STAMP_FMT).fillna("")
    raw["Read_date_time_effective"] = stamps.map(dict(zip(uniq, shifted)))
    return raw[RAW_COLUMNS].reset_index(drop=True)


def _fmt_float(x: float) -> str:
    return repr(float(x))


def _fmt_like(old: str, new: float) -> str:
    """`new` written like `old`: an integer if `old` is, else a float."""
    if re.fullmatch(r"-?\d+", old):
        return str(int(round(new)))
    return _fmt_float(round(new, 6))


def _fmt_value(raw_column: str, x: float) -> str:
    """A filled-in value in raw units: gas litres to 0.1 L (m3 to 4
    decimals, as the edition), electricity a whole Wh/varh."""
    if raw_column == GAS_COLUMN:
        return _fmt_float(round(x, 1))
    return str(int(round(x)))


def _edition_units(raw_column: Optional[str], raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None or raw_value == "" or raw_column != GAS_COLUMN:
        return raw_value
    return _fmt_float(round(float(raw_value) / LITRES_PER_M3, 9))


def raw_row_for(puprn: str, stamp: pd.Timestamp, values: Dict[str, str]) -> Dict[str, str]:
    """A full raw row for `puprn` at UTC interval-end `stamp`, its date and
    half-hour fields derived as the edition derives them."""
    local = stamp.tz_convert("Europe/London")
    hh = HHSmartMeterGenerator._hh_index_or_na(stamp)
    row = {
        "PUPRN": puprn,
        "Read_date_time": stamp.strftime(STAMP_FMT),
        "HH_segment": "" if hh is None else str(hh),
        "Read_date_effective": HHSmartMeterGenerator._effective_date_local(local),
        "Read_date_time_effective": (stamp - pd.Timedelta(seconds=1)).strftime(STAMP_FMT),
    }
    row.update({c: values.get(c, "") for c in VALUE_COLUMNS})
    return {c: row[c] for c in RAW_COLUMNS}


# ---------------------------------------------------------------------------
# Planting
# ---------------------------------------------------------------------------

def _new_puprn(rng: np.random.Generator, taken: set) -> str:
    alphabet = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
    while True:
        puprn = "".join(rng.choice(alphabet, 8))
        if puprn not in taken:
            return puprn


@dataclass(frozen=True)
class Households:
    missing: Tuple[str, ...]                 # only in the edition
    extra: Tuple[Tuple[str, str], ...]       # (new PUPRN, donor PUPRN), only in raw


def pick_households(first_month: pd.DataFrame, seed: int, n_missing: int, n_extra: int) -> Households:
    """The households left out of raw, and the extra households' new PUPRNs
    with the household each copies (its donor). All distinct."""
    rng = np.random.default_rng([seed, 0])
    puprns = sorted(first_month["PUPRN"].unique())
    if n_missing + n_extra > len(puprns) - 1:
        raise ValueError(f"{n_missing} missing + {n_extra} extra households need more than "
                         f"the source's {len(puprns)} households")
    chosen = [str(p) for p in rng.choice(puprns, n_missing + n_extra, replace=False)]
    taken = set(puprns)
    extra = []
    for donor in chosen[n_missing:]:
        new = _new_puprn(rng, taken)
        taken.add(new)
        extra.append((new, donor))
    return Households(missing=tuple(chosen[:n_missing]), extra=tuple(extra))


class _Month:
    """Planting state for one month's raw frame."""

    def __init__(self, raw: pd.DataFrame, year: int, month: int, seed: int,
                 counts: PlantCounts, households: Households):
        self.year, self.month, self.seed = year, month, seed
        self.counts = counts
        self.fname = raw_file_name(year, month)
        self.records: List[dict] = []
        self.planted: Dict[str, int] = {k: 0 for k in CATEGORY}
        self.shortfall: Dict[str, int] = {}
        self.households = households

        # Row position in the edition file: the output keeps the edition's
        # order, a duplicate right after its original, an extra household
        # right after its donor.
        raw = raw.assign(_pos=np.arange(len(raw), dtype=float))
        missing = set(households.missing)
        is_missing = raw["PUPRN"].isin(missing)
        self.missing_rows = int(is_missing.sum())
        extras = []
        for new, donor in households.extra:
            rows = raw[raw["PUPRN"] == donor].copy()
            rows["PUPRN"] = new
            rows["_pos"] += 0.5
            extras.append(rows)
        self.extra_rows = pd.concat(extras, ignore_index=True) if extras else raw.iloc[:0]
        self.raw = raw[~is_missing].reset_index(drop=True)
        self.donors = {d for _, d in households.extra}

    def record(self, kind: str, row, column: Optional[str] = None, raw_value: Optional[str] = None,
               edition_value: Optional[str] = None, edition_time: Optional[str] = None) -> None:
        if column and edition_value is None:
            edition_value = _edition_units(column, row[column])
        self.records.append({
            "category": CATEGORY[kind], "kind": kind, "file": self.fname, "PUPRN": row["PUPRN"],
            "Read_date_time": row["Read_date_time"], "raw_column": column,
            "edition_column": RAW_TO_EDITION[column] if column else None,
            "edition_value": edition_value, "raw_value": raw_value,
            "raw_value_in_edition_units": _edition_units(column, raw_value) if column else None,
            "edition_read_date_time": edition_time,
        })
        self.planted[kind] += 1

    def short(self, kind: str, requested: int, planted: int) -> None:
        note_shortfall(self.shortfall, kind, requested, planted)

    def plant(self) -> pd.DataFrame:
        raw, counts = self.raw, self.counts
        rng = month_rng(self.seed, self.year, self.month)

        # Targets: not a donor (its copy should stay identical), only
        # readings whose (PUPRN, Read_date_time) is unique so every planted
        # reading is identifiable by its key, and with at least one value.
        key_dup = raw.duplicated(["PUPRN", "Read_date_time"], keep=False)
        has_value = (raw[VALUE_COLUMNS] != "").any(axis=1)
        eligible = ~key_dup & ~raw["PUPRN"].isin(self.donors) & has_value

        # -- kinds kept from step 2, same draws in the same order ----------
        remove = np.zeros(len(raw), dtype=bool)
        day_key = ["PUPRN", "Read_date_effective"]
        bad_days = raw.loc[key_dup, day_key].drop_duplicates()
        days = raw.loc[eligible, day_key].drop_duplicates()
        if len(bad_days):
            days = days[~pd.MultiIndex.from_frame(days).isin(pd.MultiIndex.from_frame(bad_days))]
        n_days = min(counts.raw_missing_household_days, len(days))
        self.short("raw_missing_household_days", counts.raw_missing_household_days, n_days)
        self.removed_days = days.iloc[:0]
        if n_days:
            self.removed_days = days.iloc[np.sort(rng.choice(len(days), n_days, replace=False))]
            remove |= raw.set_index(day_key).index.isin(pd.MultiIndex.from_frame(self.removed_days))
        for i in np.flatnonzero(remove):
            self.record("raw_missing_household_days", raw.iloc[i])

        pool = np.flatnonzero(eligible.to_numpy() & ~remove)
        kinds = ["raw_missing_readings", "changed_values", "raw_emptied_values", "raw_duplicates"]
        sizes = [getattr(counts, k) for k in kinds]
        take = rng.choice(pool, min(sum(sizes), len(pool)), replace=False)
        groups, start = {}, 0
        for k, n in zip(kinds, sizes):
            groups[k] = np.sort(take[start:start + n])
            self.short(k, n, len(groups[k]))
            start += n

        for i in groups["raw_missing_readings"]:
            self.record("raw_missing_readings", raw.iloc[i])
        remove[groups["raw_missing_readings"]] = True

        values = raw[VALUE_COLUMNS].to_numpy(dtype=object)
        for kind in ("changed_values", "raw_emptied_values"):
            for i in groups[kind]:
                present = [j for j, c in enumerate(VALUE_COLUMNS) if values[i, j] != ""]
                j = int(rng.choice(present))
                col = VALUE_COLUMNS[j]
                old = values[i, j]
                if kind == "raw_emptied_values":
                    new = ""
                elif col == GAS_COLUMN:
                    new = _fmt_like(old, float(old) + float(rng.integers(10, 1000)) / 10)
                else:
                    new = _fmt_like(old, float(old) + float(rng.integers(1, 51)))
                self.record(kind, raw.iloc[i], col, new)
                raw.iat[i, raw.columns.get_loc(col)] = new

        for i in groups["raw_duplicates"]:
            self.record("raw_duplicates", raw.iloc[i])

        used = np.zeros(len(raw), dtype=bool)
        used[take] = True

        # -- realistic kinds, own rngs, in this order -------------------------
        self._stamp_text = raw["Read_date_time"].to_numpy(dtype=object)
        free = (~key_dup & ~raw["PUPRN"].isin(self.donors)).to_numpy() & ~remove & ~used
        self.blanked_rows: set = set()
        self._edition_blanked(eligible_rows=pd.Series(free), edition_values=values)
        free[list(self.blanked_rows)] = False
        added = self._edition_removed()
        dropped, off_empty = self._off_grid_edition_empty(eligible_rows=free)
        remove[dropped] = True
        off_skip = self._off_grid_edition_skip()

        out = pd.concat([raw[~remove], raw.iloc[groups["raw_duplicates"]], self.extra_rows, added,
                         off_empty, off_skip], ignore_index=True)
        out = out.sort_values("_pos", kind="stable").drop(columns="_pos")
        return out.reset_index(drop=True)

    # The value raw gets where the edition has none: the same household's
    # same half-hour a week earlier if the edition has it, else the
    # household's median for the column this month.
    def _fill(self, puprn: str, stamp: pd.Timestamp, col: str, hh_rows: pd.DataFrame) -> str:
        prev = week_earlier(hh_rows, "Read_date_time", col, stamp)
        if prev is not None:
            return prev
        median = median_of(hh_rows, col)
        return _fmt_value(col, median) if median is not None else ""

    def _household_rows(self, puprn: str) -> pd.DataFrame:
        cache = self.__dict__.setdefault("_hh_cache", {})
        if puprn not in cache:
            cache[puprn] = self.edition_values_frame[self.edition_values_frame["PUPRN"] == puprn]
        return cache[puprn]

    def _edition_blanked(self, eligible_rows: pd.Series, edition_values: np.ndarray) -> None:
        n = self.counts.edition_blanked
        raw = self.raw
        # Edition (pre-plant) values, for the fills.
        self.edition_values_frame = raw[["PUPRN", "Read_date_time"]].assign(
            **{c: edition_values[:, j] for j, c in enumerate(VALUE_COLUMNS)})
        if n == 0:
            return
        rng = month_rng(self.seed, self.year, self.month, 1)
        ok = eligible_rows.to_numpy()
        cells = []
        for j, col in enumerate(VALUE_COLUMNS):
            empty = edition_values[:, j] == ""
            # Only households with some value in the column this month: a
            # fill must have something to come from.
            has_any = raw.loc[~empty, "PUPRN"].unique()
            rows = np.flatnonzero(ok & empty & raw["PUPRN"].isin(has_any).to_numpy())
            cells += [(int(i), j) for i in rows]
        k = min(n, len(cells))
        self.short("edition_blanked", n, k)
        if not k:
            return
        picked = sorted(cells[i] for i in rng.choice(len(cells), k, replace=False))
        for i, j in picked:
            col = VALUE_COLUMNS[j]
            row = raw.iloc[i]
            stamp = pd.Timestamp(row["Read_date_time"])
            new = self._fill(row["PUPRN"], stamp, col, self._household_rows(row["PUPRN"]))
            self.record("edition_blanked", row, col, new, edition_value="")
            raw.iat[i, raw.columns.get_loc(col)] = new
            self.blanked_rows.add(i)

    def _slots(self) -> pd.DataFrame:
        """Each edition reading's household and (floor, ceil) half-hour
        slot, as minutes since the epoch / 30 (equal for an on-grid stamp),
        with its row index; not the missing households or donors."""
        if hasattr(self, "_slots_cache"):
            return self._slots_cache
        occ = stamp_slots(self.raw["PUPRN"], self.raw["Read_date_time"])
        excluded = set(self.households.missing) | self.donors
        self._slots_cache = occ[~occ["PUPRN"].isin(excluded)].reset_index(drop=True)
        return self._slots_cache

    def _gap_slots(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Half-hour slots (UTC interval ends, on :00/:30) inside each
        household's covered range this month where the edition has no
        reading, nor an off-grid one within 30 min of it, and not inside a
        household-day removed from raw: (PUPRN, slot) and the slots'
        stamps, in a fixed order."""
        if hasattr(self, "_gap_cache"):
            return self._gap_cache
        cand = gap_slots(self._slots())
        new_stamps = slot_stamps(cand["slot"])
        if len(self.removed_days) and len(cand):
            eff = [HHSmartMeterGenerator._effective_date_local(t.tz_convert("Europe/London"))
                   for t in new_stamps]
            days = pd.MultiIndex.from_arrays([cand["PUPRN"], eff])
            keep = ~days.isin(pd.MultiIndex.from_frame(self.removed_days))
            cand, new_stamps = cand[keep].reset_index(drop=True), new_stamps[keep].reset_index(drop=True)
        self._gap_cache = (cand, new_stamps)
        return self._gap_cache

    def _new_row(self, puprn: str, stamp: pd.Timestamp, slot_stamp: pd.Timestamp, r: int) -> dict:
        """A raw row at `stamp` (on- or off-grid), values filled for the
        half-hour ending `slot_stamp`, positioned in the edition's order."""
        hh_rows = self._household_rows(puprn)
        vals = {c: self._fill(puprn, slot_stamp, c, hh_rows) for c in VALUE_COLUMNS}
        row = raw_row_for(puprn, stamp, vals)
        row["_pos"] = insert_position(self._stamp_text, row["Read_date_time"], len(self.raw), r)
        return row

    def _off_grid_stamp(self, rng: np.random.Generator, slot_stamp: pd.Timestamp) -> pd.Timestamp:
        """`slot_stamp` moved 1-14 min either way (so its nearest half-hour
        is still the slot), kept inside this month's file (stamps after
        00:00 on the 1st, up to 00:00 on the next 1st)."""
        k = int(rng.integers(1, OFF_GRID_MAX_MINUTES + 1)) * int(rng.choice([-1, 1]))
        start = pd.Timestamp(year=self.year, month=self.month, day=1, tz="UTC")
        end = start + pd.offsets.MonthBegin(1)
        stamp = slot_stamp + pd.Timedelta(minutes=k)
        if not (start < stamp <= end):
            stamp = slot_stamp - pd.Timedelta(minutes=k)
        return stamp

    def _edition_removed(self) -> pd.DataFrame:
        n = self.counts.edition_removed
        raw = self.raw
        empty = raw.iloc[:0]
        self.removed_slots: set = set()
        if n == 0:
            return empty
        rng = month_rng(self.seed, self.year, self.month, 2)
        cand, new_stamps = self._gap_slots()
        k = min(n, len(cand))
        self.short("edition_removed", n, k)
        if not k:
            return empty
        picked = np.sort(rng.choice(len(cand), k, replace=False))
        rows = []
        for r, i in enumerate(picked):
            puprn, stamp = cand.at[i, "PUPRN"], new_stamps[i]
            row = self._new_row(puprn, stamp, stamp, r)
            rows.append(row)
            self.record("edition_removed", row)
            self.removed_slots.add(int(i))
        return pd.DataFrame(rows)

    def _off_grid_edition_skip(self) -> pd.DataFrame:
        """Raw has an off-grid reading; the edition has nothing at its
        half-hour (dropped it, wrote no row)."""
        n = self.counts.off_grid_edition_skip
        if n == 0:
            return self.raw.iloc[:0]
        rng = month_rng(self.seed, self.year, self.month, 4)
        cand, slot_stamps = self._gap_slots()
        free = np.array([i for i in range(len(cand)) if i not in self.removed_slots], dtype=np.int64)
        k = min(n, len(free))
        self.short("off_grid_edition_skip", n, k)
        if not k:
            return self.raw.iloc[:0]
        rows = []
        for r, i in enumerate(np.sort(rng.choice(free, k, replace=False))):
            puprn, slot_stamp = cand.at[i, "PUPRN"], slot_stamps[i]
            row = self._new_row(puprn, self._off_grid_stamp(rng, slot_stamp), slot_stamp, 1000 + r)
            rows.append(row)
            self.record("off_grid_edition_skip", row, edition_time=slot_stamp.strftime(STAMP_FMT))
        return pd.DataFrame(rows)

    def _off_grid_edition_empty(self, eligible_rows: np.ndarray) -> Tuple[np.ndarray, pd.DataFrame]:
        """Raw has an off-grid reading; the edition has an empty row (every
        value NULL) at its half-hour instead. Returns the edition rows raw
        drops and the off-grid rows raw gets."""
        n = self.counts.off_grid_edition_empty
        raw = self.raw
        none = (np.array([], dtype=np.int64), raw.iloc[:0])
        if n == 0:
            return none
        rng = month_rng(self.seed, self.year, self.month, 3)
        occ = self._slots()
        # On-grid, every value empty, and no off-grid reading of the
        # household within 30 min (whose floor slot is the slot or the one
        # before), so the new off-grid stamp is the slot's only neighbour.
        all_empty = (raw[VALUE_COLUMNS] == "").all(axis=1).to_numpy()
        grid = occ[(occ["lo"] == occ["hi"]) & eligible_rows[occ["row"]] & all_empty[occ["row"]]]
        off = occ[occ["lo"] != occ["hi"]]
        near = set(zip(off["PUPRN"], off["lo"])) | set(zip(off["PUPRN"], off["lo"] + 1))
        grid = grid[np.array([(p, s) not in near for p, s in zip(grid["PUPRN"], grid["lo"])], dtype=bool)]
        # A fill must have something to come from.
        has_any = set(raw.loc[(raw[VALUE_COLUMNS] != "").any(axis=1), "PUPRN"])
        grid = grid[grid["PUPRN"].isin(has_any)].sort_values("row").reset_index(drop=True)
        k = min(n, len(grid))
        self.short("off_grid_edition_empty", n, k)
        if not k:
            return none
        picked = np.sort(rng.choice(len(grid), k, replace=False))
        rows, dropped = [], []
        for r, i in enumerate(picked):
            idx = int(grid.at[i, "row"])
            puprn = raw.at[idx, "PUPRN"]
            slot_stamp = pd.Timestamp(raw.at[idx, "Read_date_time"])
            row = self._new_row(puprn, self._off_grid_stamp(rng, slot_stamp), slot_stamp, 2000 + r)
            row["_pos"] = raw.at[idx, "_pos"]  # where the edition's empty row was
            rows.append(row)
            dropped.append(idx)
            self.record("off_grid_edition_empty", row, edition_time=raw.at[idx, "Read_date_time"])
        return np.array(dropped, dtype=np.int64), pd.DataFrame(rows)

    def off_grid_in_both(self, out: pd.DataFrame) -> pd.DataFrame:
        """Off-grid readings the edition kept as they are (in the source, so
        on both sides at the same stamp): PUPRN, Read_date_time."""
        key = ["PUPRN", "Read_date_time"]
        ed = self.raw.loc[~self.raw["Read_date_time"].str[14:].isin(["00:00Z", "30:00Z"]), key]
        rw = out.loc[~out["Read_date_time"].str[14:].isin(["00:00Z", "30:00Z"]), key].drop_duplicates()
        return ed.drop_duplicates().merge(rw, on=key).assign(file=self.fname)


def plant_month(raw: pd.DataFrame, year: int, month: int, seed: int, counts: PlantCounts,
                households: Households) -> Tuple[pd.DataFrame, "_Month"]:
    """Plant one month's differences into its raw frame. Returns the planted
    frame and the month's state (records, planted counts, shortfall)."""
    m = _Month(raw, year, month, seed, counts, households)
    return m.plant(), m


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _expected_counts(records: List[dict]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """category -> month -> edition column -> readings. A whole-reading kind
    counts once under every value column (the reading carries them all)."""
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for r in records:
        month = re.search(r"(\d{4})_(\d{2})", r["file"]).expand(r"\1-\2")
        cols = [r["edition_column"]] if r["edition_column"] else EDITION_VALUE_COLUMNS
        per = out.setdefault(r["category"], {}).setdefault(month, {})
        for c in cols:
            per[c] = per.get(c, 0) + 1
    return out


def convert(cfg: RawConfig, source_dir: Optional[Path] = None, out_dir: Optional[Path] = None) -> dict:
    """Write the raw half-hourly files for a mock Edition08 run (plus the BST
    dates file, which a raw data version needs, and the planted-differences
    ground truth under mock_internal/). Folders default to
    data/mock/<cfg.source>/ and data/mock/<cfg.output_label>/. Returns the
    manifest."""
    source_dir = Path(source_dir) if source_dir else mock_dir_for(cfg.source)
    out_dir = Path(out_dir) if out_dir else mock_dir_for(cfg.output_label)
    check_folders(source_dir, out_dir)
    files = find_edition_files(source_dir, cfg.month_start, cfg.month_end)
    out_dir.mkdir(parents=True, exist_ok=True)
    internal = out_dir / "mock_internal"
    internal.mkdir(exist_ok=True)
    for old in out_dir.glob(f"{RAW_FILE_STEM}_*.csv"):
        old.unlink()  # a rerun with fewer months shouldn't leave stale files
    copy_bst(source_dir, out_dir)

    usecols = [c for c in RAW_TO_EDITION.values() if c is not None]
    read = lambda p: pd.read_csv(p, usecols=usecols, dtype=str, keep_default_na=False, na_filter=False)

    households: Optional[Households] = None
    all_records: List[dict] = []
    per_month: Dict[str, dict] = {}
    shortfall: Dict[str, Dict[str, int]] = {}
    off_grid_kept: List[pd.DataFrame] = []
    for year, month, path in files:
        edition = read(path)
        if households is None:
            households = pick_households(edition, cfg.seed, cfg.missing_households, cfg.extra_households)
        raw = to_raw(edition)
        del edition
        planted, m = plant_month(raw, year, month, cfg.seed, cfg.plants, households)
        planted.to_csv(out_dir / raw_file_name(year, month), index=False)
        all_records += m.records
        kept = m.off_grid_in_both(planted)
        off_grid_kept.append(kept)
        key = f"{year}-{month:02d}"
        per_month[key] = {"edition_rows": len(raw), "raw_rows": len(planted), **m.planted,
                          "missing_household_rows": m.missing_rows,
                          "extra_household_rows": len(m.extra_rows),
                          "off_grid_in_both": len(kept)}
        if m.shortfall:
            shortfall[key] = m.shortfall
            print(f"[raw] WARNING {key}: fewer candidates than requested, not planted: {m.shortfall}")
        print(f"[raw] {raw_file_name(year, month)}: {len(raw):,} -> {len(planted):,} rows")

    pd.DataFrame(all_records, columns=READINGS_COLUMNS).to_csv(internal / READINGS_FILE, index=False)
    pd.concat(off_grid_kept, ignore_index=True)[["file", "PUPRN", "Read_date_time"]] \
        .to_csv(internal / OFF_GRID_FILE, index=False)
    kinds = next(iter(per_month.values())).keys()
    manifest = {
        "description": "Mock raw half-hourly readings made from a mock Edition08 run, with planted "
                       "differences. Not real SERL data.",
        "source_dir": str(source_dir),
        "config": {**asdict(cfg), "plants": asdict(cfg.plants)},
        "categories": {"per_kind": CATEGORY, "households": HOUSEHOLD_CATEGORY,
                       "note": "A = raw, B = edition. Whole-reading kinds count once under every "
                               "value column in expected_counts."},
        "households": {
            "household_not_in_raw": list(households.missing),
            "household_not_in_edition": [{"PUPRN": n, "copied_from": d} for n, d in households.extra],
        },
        "column_mapping": dict(RAW_TO_EDITION),
        "conversions": {
            "gas_active_import_profile_hh_L": "Gas_hh_m3 * 1000",
            "Read_date_time_effective": "Read_date_time - 1 s",
        },
        "dropped_edition_columns": ["Read_date_time_local", "Valid_read_time", "Gas_hh_Wh",
                                    "Elec_act_imp_flag", "Elec_act_exp_flag",
                                    "Elect_react_imp_flag", "Elect_react_exp_flag", "Gas_flag"],
        "planted_readings_file": f"mock_internal/{READINGS_FILE}",
        "off_grid_in_both_file": f"mock_internal/{OFF_GRID_FILE}",
        "off_grid_scenarios": {
            "edition keeps it at its stamp": "off_grid_in_both (copied from the source, not planted)",
            "edition drops it, empty row at the half-hour": CATEGORY["off_grid_edition_empty"],
            "edition drops it, no row at the half-hour": CATEGORY["off_grid_edition_skip"],
        },
        "shortfall": shortfall,
        "totals": {k: sum(m[k] for m in per_month.values()) for k in kinds},
        "expected_counts": _expected_counts(all_records),
        "per_month": per_month,
    }
    (internal / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
