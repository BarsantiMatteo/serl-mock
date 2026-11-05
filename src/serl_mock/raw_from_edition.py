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

plus, over the whole run, `missing_households` (only in the edition) and
`extra_households` (only in raw: a copy of another household's readings
under a new PUPRN, e.g. a withdrawn household).

The realistic kinds go the way real processing does (raw -> edition drops
or blanks a reading); they need gaps in the source edition: edition_blanked
fills an edition NULL, edition_removed fills a half-hour the edition lacks.
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
step 2 of serl-dq's plan had (all but the two realistic ones) use the same
draws in the same order as then, so a config with the realistic kinds off
reproduces that output byte for byte.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

from .generator_smartmeter import HHSmartMeterGenerator
from .paths import REFERENCE_DIR, mock_dir_for, mock_hh_dirname

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
STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

RAW_FILE_STEM = "SERL_half_hourly"
EDITION_FILE_RE = re.compile(r"^serl_half_hourly_(\d{4})_(\d{2})_edition08\.csv$")
BST_FILE = "bst_dates_to_2030.csv"
READINGS_FILE = "raw_planted_readings.csv"
MANIFEST_FILE = "raw_planted_manifest.json"

READINGS_COLUMNS = [
    "category", "kind", "file", "PUPRN", "Read_date_time", "raw_column", "edition_column",
    "edition_value", "raw_value", "raw_value_in_edition_units",
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
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _count(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} must be an integer >= 0, got {value!r}")
    return value


def parse_config(cfg: dict) -> RawConfig:
    """Validate a raw-delivery config dict (unknown keys, bad counts and
    bad months raise)."""
    if not isinstance(cfg, dict):
        raise ValueError("raw config must be a mapping")
    unknown = set(cfg) - _TOP_KEYS
    if unknown:
        raise ValueError(f"raw config: unknown key(s) {sorted(unknown)}; expected {sorted(_TOP_KEYS)}")
    if not cfg.get("source"):
        raise ValueError("raw config: `source` (a folder under data/mock/) is required")
    source = str(cfg["source"])
    seed = cfg.get("seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"raw config: seed must be an integer, got {seed!r}")

    months = cfg.get("months") or {}
    if not isinstance(months, dict) or set(months) - {"start", "end"}:
        raise ValueError(f"raw config: months must be {{start, end}}, got {months!r}")
    for k in ("start", "end"):
        m = months.get(k)
        if m is not None and not _MONTH_RE.match(str(m)):
            raise ValueError(f"raw config: months.{k} must be YYYY-MM or null, got {m!r}")
    start, end = months.get("start"), months.get("end")
    if start and end and str(start) > str(end):
        raise ValueError(f"raw config: months.start {start} is after months.end {end}")

    plants = cfg.get("plants_per_month") or {}
    if not isinstance(plants, dict):
        raise ValueError("raw config: plants_per_month must be a mapping")
    known = set(PlantCounts.__dataclass_fields__)
    unknown = set(plants) - known
    if unknown:
        raise ValueError(f"raw config: unknown plants_per_month key(s) {sorted(unknown)}; "
                         f"expected {sorted(known)}")
    counts = PlantCounts(**{k: _count(v, f"plants_per_month.{k}") for k, v in plants.items()})

    return RawConfig(
        source=source,
        output_label=str(cfg.get("output_label") or f"raw_from_{source}"),
        seed=seed,
        month_start=str(start) if start else None,
        month_end=str(end) if end else None,
        plants=counts,
        extra_households=_count(cfg.get("extra_households", 1), "extra_households"),
        missing_households=_count(cfg.get("missing_households", 1), "missing_households"),
    )


def load_config(path: Path) -> RawConfig:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})


# ---------------------------------------------------------------------------
# Format conversion
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
               edition_value: Optional[str] = None) -> None:
        if column and edition_value is None:
            edition_value = _edition_units(column, row[column])
        self.records.append({
            "category": CATEGORY[kind], "kind": kind, "file": self.fname, "PUPRN": row["PUPRN"],
            "Read_date_time": row["Read_date_time"], "raw_column": column,
            "edition_column": RAW_TO_EDITION[column] if column else None,
            "edition_value": edition_value, "raw_value": raw_value,
            "raw_value_in_edition_units": _edition_units(column, raw_value) if column else None,
        })
        self.planted[kind] += 1

    def short(self, kind: str, requested: int, planted: int) -> None:
        if planted < requested:
            self.shortfall[kind] = requested - planted

    def plant(self) -> pd.DataFrame:
        raw, counts = self.raw, self.counts
        rng = np.random.default_rng([self.seed, self.year, self.month])

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

        # -- realistic kinds, own rngs --------------------------------------
        self._edition_blanked(eligible_rows=~key_dup & ~raw["PUPRN"].isin(self.donors) & ~remove & ~used,
                              edition_values=values)
        added = self._edition_removed()

        out = pd.concat([raw[~remove], raw.iloc[groups["raw_duplicates"]], self.extra_rows, added],
                        ignore_index=True)
        out = out.sort_values("_pos", kind="stable").drop(columns="_pos")
        return out.reset_index(drop=True)

    # The value raw gets where the edition has none: the same household's
    # same half-hour a week earlier if the edition has it, else the
    # household's median for the column this month.
    def _fill(self, puprn: str, stamp: pd.Timestamp, col: str, hh_rows: pd.DataFrame) -> str:
        week = (stamp - pd.Timedelta(days=7)).strftime(STAMP_FMT)
        prev = hh_rows.loc[hh_rows["Read_date_time"] == week, col]
        prev = prev[prev != ""]
        if len(prev):
            return str(prev.iloc[0])
        vals = pd.to_numeric(hh_rows[col][hh_rows[col] != ""])
        return _fmt_value(col, float(vals.median())) if len(vals) else ""

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
        rng = np.random.default_rng([self.seed, self.year, self.month, 1])
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

    def _edition_removed(self) -> pd.DataFrame:
        n = self.counts.edition_removed
        raw = self.raw
        empty = raw.iloc[:0]
        if n == 0:
            return empty
        rng = np.random.default_rng([self.seed, self.year, self.month, 2])
        # Half-hour slots (UTC interval ends, on :00/:30) inside each
        # household's covered range this month where the edition has no
        # reading — nor an off-grid one within 30 min of it, nor one with a
        # duplicated key.
        stamps = pd.to_datetime(raw["Read_date_time"], format=STAMP_FMT, errors="coerce", utc=True)
        ok = stamps.notna()
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        minutes = ((stamps[ok] - epoch) // pd.Timedelta(minutes=1)).to_numpy(dtype=np.int64)
        lo, hi = minutes // 30, -(-minutes // 30)            # floor / ceil slot
        occ = pd.DataFrame({"PUPRN": raw.loc[ok, "PUPRN"].to_numpy(), "lo": lo, "hi": hi})
        excluded = set(self.households.missing) | self.donors
        occ = occ[~occ["PUPRN"].isin(excluded)]
        if occ.empty:
            self.short("edition_removed", n, 0)
            return empty
        occupied = pd.concat([occ[["PUPRN", "lo"]].rename(columns={"lo": "slot"}),
                              occ[["PUPRN", "hi"]].rename(columns={"hi": "slot"})]).drop_duplicates()
        on_grid = occ[occ["lo"] == occ["hi"]]
        span = on_grid.groupby("PUPRN")["lo"].agg(["min", "max"])
        full = pd.DataFrame({
            "PUPRN": np.repeat(span.index.to_numpy(), (span["max"] - span["min"] + 1).to_numpy()),
            "slot": np.concatenate([np.arange(a, b + 1) for a, b in zip(span["min"], span["max"])])
                    if len(span) else np.array([], dtype=np.int64),
        })
        cand = full.merge(occupied, on=["PUPRN", "slot"], how="left", indicator=True)
        cand = cand[cand["_merge"] == "left_only"][["PUPRN", "slot"]].sort_values(["PUPRN", "slot"])
        cand = cand.reset_index(drop=True)
        new_stamps = pd.to_datetime(cand["slot"] * 30, unit="m", utc=True)
        if len(self.removed_days) and len(cand):
            # Not inside a household-day removed from raw.
            eff = [HHSmartMeterGenerator._effective_date_local(t.tz_convert("Europe/London"))
                   for t in new_stamps]
            days = pd.MultiIndex.from_arrays([cand["PUPRN"], eff])
            keep = ~days.isin(pd.MultiIndex.from_frame(self.removed_days))
            cand, new_stamps = cand[keep].reset_index(drop=True), new_stamps[keep].reset_index(drop=True)
        k = min(n, len(cand))
        self.short("edition_removed", n, k)
        if not k:
            return empty
        picked = np.sort(rng.choice(len(cand), k, replace=False))
        text = raw["Read_date_time"].to_numpy(dtype=object)
        rows = []
        for r, i in enumerate(picked):
            puprn, stamp = cand.at[i, "PUPRN"], new_stamps[i]
            hh_rows = self._household_rows(puprn)
            vals = {c: self._fill(puprn, stamp, c, hh_rows) for c in VALUE_COLUMNS}
            row = raw_row_for(puprn, stamp, vals)
            later = np.flatnonzero(text > row["Read_date_time"])
            row["_pos"] = (later[0] if len(later) else len(raw)) - 0.5 + r * 1e-6
            rows.append(row)
            self.record("edition_removed", row)
        return pd.DataFrame(rows)


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
    if source_dir.resolve() == out_dir.resolve():
        raise ValueError("raw output folder must differ from the source folder")
    files = find_edition_files(source_dir, cfg.month_start, cfg.month_end)
    out_dir.mkdir(parents=True, exist_ok=True)
    internal = out_dir / "mock_internal"
    internal.mkdir(exist_ok=True)
    for old in out_dir.glob(f"{RAW_FILE_STEM}_*.csv"):
        old.unlink()  # a rerun with fewer months shouldn't leave stale files

    bst = source_dir / BST_FILE
    shutil.copyfile(bst if bst.exists() else REFERENCE_DIR / BST_FILE, out_dir / BST_FILE)

    usecols = [c for c in RAW_TO_EDITION.values() if c is not None]
    read = lambda p: pd.read_csv(p, usecols=usecols, dtype=str, keep_default_na=False, na_filter=False)

    households: Optional[Households] = None
    all_records: List[dict] = []
    per_month: Dict[str, dict] = {}
    shortfall: Dict[str, Dict[str, int]] = {}
    for year, month, path in files:
        edition = read(path)
        if households is None:
            households = pick_households(edition, cfg.seed, cfg.missing_households, cfg.extra_households)
        raw = to_raw(edition)
        del edition
        planted, m = plant_month(raw, year, month, cfg.seed, cfg.plants, households)
        planted.to_csv(out_dir / raw_file_name(year, month), index=False)
        all_records += m.records
        key = f"{year}-{month:02d}"
        per_month[key] = {"edition_rows": len(raw), "raw_rows": len(planted), **m.planted,
                          "missing_household_rows": m.missing_rows,
                          "extra_household_rows": len(m.extra_rows)}
        if m.shortfall:
            shortfall[key] = m.shortfall
            print(f"[raw] WARNING {key}: fewer candidates than requested, not planted: {m.shortfall}")
        print(f"[raw] {raw_file_name(year, month)}: {len(raw):,} -> {len(planted):,} rows")

    pd.DataFrame(all_records, columns=READINGS_COLUMNS).to_csv(internal / READINGS_FILE, index=False)
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
        "shortfall": shortfall,
        "totals": {k: sum(m[k] for m in per_month.values()) for k in kinds},
        "expected_counts": _expected_counts(all_records),
        "per_month": per_month,
    }
    (internal / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
