# src/serl_mock/next_edition.py
"""A mock later edition derived from an earlier mock edition.

Reads a mock Edition08 run ("before", e.g. edition08_gappy) and writes a
later edition ("after") with the same readings except for deliberate,
seeded changes, plus a ground truth of every change. For testing
comparisons across editions (serl-dq's reading comparison and its status
changes: was a problem resolved, introduced or left?). Driven by a config
(config/next_edition/*.yaml, `load_config`); see docs/02_configuration.md.

Each reading has, per value column, a status, as serl-dq's comparison
assigns it (config/smart_meter/compare.yaml `status`), first that applies:
no_reading, empty, out_of_range (negative or above the column's limit),
flagged (flag not 1), ok. The change kinds are defined by the status they
move a reading from and to:

  resolved (a problem before, ok after)
    gap_filled        no_reading -> ok     a full reading where "before" has none
    value_filled      empty -> ok          a stream's value filled, flag 0 -> 1
    value_corrected   out_of_range -> ok   a meter-max value replaced, flag -1 -> 1
  introduced (ok before, a problem after)
    reading_dropped   ok -> no_reading     a reading removed
    value_lost        ok -> empty          a stream's value emptied, flag 1 -> 0
    value_spoiled     ok -> out_of_range   a stream's value set to the meter maximum, flag 1 -> -1
  unchanged status
    value_changed     ok -> ok             a stream's value changed, still plausible, flag stays 1

A flag never changes unless its value does: no flag-only change is planted
(a later edition isn't assumed to recode flags on unchanged readings), so
nothing moves a reading to or from `flagged`. A stream is one meter
channel: its flag and its value column(s) (gas: m3 and Wh together;
`missingness.STREAM_COLUMNS`). Every changed row stays consistent (flags
match values), so "after"'s own checks only see what was planted. Fill values: the same household's same half-hour a week earlier
if ok there, else the household's median of its ok values that month.

Changes only touch on-grid readings with a unique (PUPRN, timestamp), one
change per reading. Kinds run in the order above, each with its own rng
(seed, year, month, kind), never on a reading an earlier kind took. A
source with fewer candidates than requested gets what exists; the
shortfall is printed and recorded in the manifest.

"After" is written in `output_edition`'s format: "08" (CSV, as the source)
or "09" (Parquet, harmonised columns: Wh-only gas, reactive flags without
the "Elect" typo, a `filename` column). Only the half-hourly files and the
BST dates file are written (serl-dq reads it with the hh_only overlay).

Ground truth (`mock_internal/`): `edition_changes.csv`, one row per changed
reading and compared value column, with values, flags and statuses on both
sides and `change` (resolved / introduced / changed_problem / unchanged);
`edition_changes_manifest.json` with settings, counts per kind and month,
shortfall and expected status transitions per column and month.
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
from .missingness import STREAM_COLUMNS
from .paths import REFERENCE_DIR, mock_dir_for, mock_hh_dirname
from .raw_from_edition import STAMP_FMT, find_edition_files
from .utils import write_table

GEN = HHSmartMeterGenerator
BST_FILE = "bst_dates_to_2030.csv"
CHANGES_FILE = "edition_changes.csv"
MANIFEST_FILE = "edition_changes_manifest.json"

# Status limits, as serl-dq's config/smart_meter/compare.yaml `status`
# (plausibility value_ranges, SERL's documentation).
STATUS_LIMITS = {"Elec_act_imp_hh_Wh": 24000, "Elec_act_exp_hh_Wh": 5000, "Gas_hh_Wh": 89765}
# The column a stream's status is read from when picking candidates.
PRIMARY = {s: cols[-1] for s, (_, cols) in STREAM_COLUMNS.items()}   # gas: Gas_hh_Wh
STREAMS = list(STREAM_COLUMNS)
LIMITED_STREAMS = [s for s in STREAMS if PRIMARY[s] in STATUS_LIMITS]
OK_FLAG = "1"

RESOLVED = ["gap_filled", "value_filled", "value_corrected"]
INTRODUCED = ["reading_dropped", "value_lost", "value_spoiled"]
KINDS = RESOLVED + INTRODUCED + ["value_changed"]
# Fixed rng salt per kind, so adding or removing a kind never moves another's draws.
_RNG_SALT = {"gap_filled": 100, "value_filled": 101, "value_corrected": 103, "reading_dropped": 104,
             "value_lost": 105, "value_spoiled": 107, "value_changed": 108}

ED09_RENAME = {"Elect_react_imp_flag": "Elec_react_imp_flag", "Elect_react_exp_flag": "Elec_react_exp_flag"}
ED09_COLUMNS = [
    "PUPRN", "Read_date_effective_local", "Read_date_time_local", "Read_date_time_UTC", "HH",
    "Valid_read_time", "Elec_act_imp_flag", "Elec_react_imp_flag", "Elec_act_exp_flag",
    "Elec_react_exp_flag", "Gas_flag", "Elec_act_imp_hh_Wh", "Elec_react_imp_hh_varh",
    "Elec_act_exp_hh_Wh", "Elec_react_exp_hh_varh", "Gas_hh_Wh",
]
ED09_INT = ED09_COLUMNS[4:5] + ED09_COLUMNS[6:]

CHANGES_COLUMNS = [
    "change", "kind", "file", "PUPRN", "Read_date_time_UTC", "edition_column",
    "value_before", "value_after", "flag_before", "flag_after", "status_before", "status_after",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChangeCounts:
    """How many of each change per month (0 = off)."""
    gap_filled: int = 20
    value_filled: int = 20
    value_corrected: int = 10
    reading_dropped: int = 20
    value_lost: int = 20
    value_spoiled: int = 10
    value_changed: int = 20


@dataclass(frozen=True)
class NextEditionConfig:
    source: str                          # data/mock/<source>/, an Edition08 mock run
    output_label: str                    # data/mock/<output_label>/
    output_edition: str = "09"           # "08" or "09"
    seed: int = 42
    month_start: Optional[str] = None
    month_end: Optional[str] = None
    changes: ChangeCounts = field(default_factory=ChangeCounts)


_TOP_KEYS = {"source", "output_label", "output_edition", "seed", "months", "changes_per_month"}
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _int(value, where: str, minimum: Optional[int] = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        raise ValueError(f"{where} must be an integer" + (f" >= {minimum}" if minimum is not None else "")
                         + f", got {value!r}")
    return value


def parse_config(cfg: dict) -> NextEditionConfig:
    """Validate a next-edition config dict (unknown keys, bad counts, bad
    months and editions raise)."""
    if not isinstance(cfg, dict):
        raise ValueError("next-edition config must be a mapping")
    unknown = set(cfg) - _TOP_KEYS
    if unknown:
        raise ValueError(f"next-edition config: unknown key(s) {sorted(unknown)}; expected {sorted(_TOP_KEYS)}")
    if not cfg.get("source"):
        raise ValueError("next-edition config: `source` (a folder under data/mock/) is required")
    source = str(cfg["source"])
    edition = str(cfg.get("output_edition", "09")).zfill(2)
    if edition not in ("08", "09"):
        raise ValueError(f"next-edition config: output_edition must be \"08\" or \"09\", got {edition!r}")

    months = cfg.get("months") or {}
    if not isinstance(months, dict) or set(months) - {"start", "end"}:
        raise ValueError(f"next-edition config: months must be {{start, end}}, got {months!r}")
    for k in ("start", "end"):
        m = months.get(k)
        if m is not None and not _MONTH_RE.match(str(m)):
            raise ValueError(f"next-edition config: months.{k} must be YYYY-MM or null, got {m!r}")
    start, end = months.get("start"), months.get("end")
    if start and end and str(start) > str(end):
        raise ValueError(f"next-edition config: months.start {start} is after months.end {end}")

    changes = cfg.get("changes_per_month") or {}
    if not isinstance(changes, dict):
        raise ValueError("next-edition config: changes_per_month must be a mapping")
    unknown = set(changes) - set(KINDS)
    if unknown:
        raise ValueError(f"next-edition config: unknown changes_per_month key(s) {sorted(unknown)}; "
                         f"expected {KINDS}")
    counts = ChangeCounts(**{k: _int(v, f"changes_per_month.{k}") for k, v in changes.items()})

    return NextEditionConfig(
        source=source,
        output_label=str(cfg.get("output_label") or f"edition{edition}_from_{source}"),
        output_edition=edition,
        seed=_int(cfg.get("seed", 42), "seed", minimum=None),
        month_start=str(start) if start else None,
        month_end=str(end) if end else None,
        changes=counts,
    )


def load_config(path: Path) -> NextEditionConfig:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

def _flag_col(col: str) -> str:
    for flag, cols in STREAM_COLUMNS.values():
        if col in cols:
            return flag
    raise KeyError(col)


def column_status(df: pd.DataFrame, col: str) -> np.ndarray:
    """Status of every row of `df` (Edition08 columns, strings) in `col`."""
    v = df[col]
    empty = (v == "").to_numpy()
    num = pd.to_numeric(v.where(~(v == "")), errors="coerce").to_numpy(dtype=float)
    limit = STATUS_LIMITS.get(col)
    with np.errstate(invalid="ignore"):
        oor = (num < 0) | ((num > limit) if limit is not None else False)
    flagged = (df[_flag_col(col)] != OK_FLAG).to_numpy()
    return np.select([empty, oor, flagged], ["empty", "out_of_range", "flagged"], "ok")


def value_status(value: Optional[str], flag: Optional[str], col: str) -> str:
    if value is None:
        return "no_reading"
    if value == "":
        return "empty"
    x = float(value)
    limit = STATUS_LIMITS.get(col)
    if x < 0 or (limit is not None and x > limit):
        return "out_of_range"
    return "ok" if flag == OK_FLAG else "flagged"


def change_of(before: str, after: str) -> str:
    if before == after:
        return "unchanged"
    if after == "ok":
        return "resolved"
    if before == "ok":
        return "introduced"
    return "changed_problem"


# ---------------------------------------------------------------------------
# One month
# ---------------------------------------------------------------------------

def _gas_wh(m3: float) -> str:
    return str(int(round(m3 * GEN.GAS_WH_PER_M3)))


class _Month:
    def __init__(self, src: pd.DataFrame, year: int, month: int, cfg: NextEditionConfig):
        self.year, self.month, self.cfg = year, month, cfg
        self.src = src                       # "before", untouched
        self.df = src.copy()                 # becomes "after"
        self.planted: Dict[str, int] = {k: 0 for k in KINDS}
        self.shortfall: Dict[str, int] = {}
        self.changed: List[Tuple[str, Optional[int], Optional[dict]]] = []  # (kind, src row, added row)
        self.dropped: List[int] = []
        self.added: List[dict] = []
        stamps = src["Read_date_time_UTC"]
        on_grid = stamps.str[14:].isin(["00:00Z", "30:00Z"]) & (src["HH"] != "") \
            & (src["Valid_read_time"] == "True")
        key_dup = src.duplicated(["PUPRN", "Read_date_time_UTC"], keep=False)
        self.used = np.array((~on_grid | key_dup).to_numpy(), dtype=bool)  # writable copy
        self.status = {s: column_status(src, PRIMARY[s]) for s in STREAMS}
        self._hh_cache: Dict[str, pd.DataFrame] = {}

    def rng(self, kind: str) -> np.random.Generator:
        return np.random.default_rng([self.cfg.seed, self.year, self.month, _RNG_SALT[kind]])

    def short(self, kind: str, requested: int, planted: int) -> None:
        if planted < requested:
            self.shortfall[kind] = requested - planted

    # -- fills ------------------------------------------------------------
    def _household(self, puprn: str) -> pd.DataFrame:
        if puprn not in self._hh_cache:
            rows = self.src[self.src["PUPRN"] == puprn]
            self._hh_cache[puprn] = rows.assign(
                **{f"_st_{s}": self.status[s][rows.index.to_numpy()] for s in STREAMS})
        return self._hh_cache[puprn]

    def fill(self, puprn: str, stamp: pd.Timestamp, stream: str) -> Dict[str, str]:
        """Plausible values for `stream` (its value columns), from the
        source's ok values."""
        rows = self._household(puprn)
        ok = rows[rows[f"_st_{stream}"] == "ok"]
        week = (stamp - pd.Timedelta(days=7)).strftime(STAMP_FMT)
        prev = ok[ok["Read_date_time_UTC"] == week]
        _, cols = STREAM_COLUMNS[stream]
        if stream == "gas":
            m3 = float(prev["Gas_hh_m3"].iloc[0]) if len(prev) else \
                float(pd.to_numeric(ok["Gas_hh_m3"]).median()) if len(ok) else 0.0
            m3 = round(m3, 4)
            return {"Gas_hh_m3": repr(float(m3)), "Gas_hh_Wh": _gas_wh(m3)}
        col = cols[0]
        x = float(prev[col].iloc[0]) if len(prev) else \
            float(pd.to_numeric(ok[col]).median()) if len(ok) else 0.0
        return {col: str(int(round(x)))}

    # -- picking ------------------------------------------------------------
    def pick_cells(self, kind: str, want: Dict[str, np.ndarray]) -> List[Tuple[int, str]]:
        """Up to the kind's count of (row, stream) cells, one per row, among
        rows not used yet; `want` = stream -> candidate mask."""
        n = getattr(self.cfg.changes, kind)
        if n == 0:
            return []
        free = ~self.used
        streams = [s for s in STREAMS if s in want]
        any_s = np.logical_or.reduce([want[s] for s in streams]) & free
        rows = np.flatnonzero(any_s)
        k = min(n, len(rows))
        self.short(kind, n, k)
        if not k:
            return []
        rng = self.rng(kind)
        cells = []
        for i in np.sort(rng.choice(rows, k, replace=False)):
            options = [s for s in streams if want[s][i]]
            cells.append((int(i), options[int(rng.integers(len(options)))]))
            self.used[int(i)] = True
        return cells

    def set_stream(self, i: int, stream: str, values: Optional[Dict[str, str]], flag: Optional[str]) -> None:
        flag_col, cols = STREAM_COLUMNS[stream]
        if values is not None:
            for c in cols:
                self.df.iat[i, self.df.columns.get_loc(c)] = values[c]
        if flag is not None:
            self.df.iat[i, self.df.columns.get_loc(flag_col)] = flag

    def note(self, kind: str, i: Optional[int] = None, added: Optional[dict] = None) -> None:
        self.changed.append((kind, i, added))
        self.planted[kind] += 1

    # -- kinds ------------------------------------------------------------
    def run(self) -> pd.DataFrame:
        src, st = self.src, self.status
        stamp = lambda i: pd.Timestamp(src.iat[i, src.columns.get_loc("Read_date_time_UTC")])
        puprn = lambda i: src.iat[i, src.columns.get_loc("PUPRN")]
        ok = {s: st[s] == "ok" for s in STREAMS}

        self.gap_filled()

        # A fill must have something to come from: the household has ok
        # values in the stream this month.
        has_ok = {s: src["PUPRN"].isin(src.loc[ok[s], "PUPRN"].unique()).to_numpy() for s in STREAMS}
        for i, s in self.pick_cells("value_filled", {s: (st[s] == "empty") & has_ok[s] for s in STREAMS}):
            self.set_stream(i, s, self.fill(puprn(i), stamp(i), s), OK_FLAG)
            self.note("value_filled", i)

        for i, s in self.pick_cells("value_corrected", {s: st[s] == "out_of_range" for s in STREAMS}):
            self.set_stream(i, s, self.fill(puprn(i), stamp(i), s), OK_FLAG)
            self.note("value_corrected", i)

        all_ok = np.logical_and.reduce([ok[s] for s in STREAMS])
        for i, _ in self.pick_cells("reading_dropped", {STREAMS[0]: all_ok}):
            self.dropped.append(i)
            self.note("reading_dropped", i)

        for i, s in self.pick_cells("value_lost", ok):
            self.set_stream(i, s, {c: "" for c in STREAM_COLUMNS[s][1]}, "0")
            self.note("value_lost", i)

        for i, s in self.pick_cells("value_spoiled", {s: ok[s] for s in LIMITED_STREAMS}):
            if s == "gas":
                vals = {"Gas_hh_m3": repr(GEN.FLAG_THRESHOLDS["gas_meter_max_m3"]),
                        "Gas_hh_Wh": _gas_wh(GEN.FLAG_THRESHOLDS["gas_meter_max_m3"])}
            else:
                vals = {STREAM_COLUMNS[s][1][0]: str(GEN.FLAG_THRESHOLDS["elec_meter_max_Wh"])}
            self.set_stream(i, s, vals, "-1")
            self.note("value_spoiled", i)

        rng = self.rng("value_changed")
        for i, s in self.pick_cells("value_changed", ok):
            _, cols = STREAM_COLUMNS[s]
            if s == "gas":
                m3 = round(float(src.iat[i, src.columns.get_loc("Gas_hh_m3")]) + rng.integers(1, 11) / 100, 4)
                vals = {"Gas_hh_m3": repr(float(m3)), "Gas_hh_Wh": _gas_wh(m3)}
            else:
                old = float(src.iat[i, src.columns.get_loc(cols[0])])
                new = old + rng.integers(1, 51)
                limit = STATUS_LIMITS.get(cols[0])
                if limit is not None and new > limit:
                    new = old - rng.integers(1, 51) if old > 50 else old + 1
                vals = {cols[0]: str(int(new))}
            self.set_stream(i, s, vals, None)
            self.note("value_changed", i)

        out = self.df.assign(_pos=np.arange(len(self.df), dtype=float))
        out = out.drop(index=self.dropped)
        if self.added:
            out = pd.concat([out, pd.DataFrame(self.added)], ignore_index=True)
        return out.sort_values("_pos", kind="stable").drop(columns="_pos").reset_index(drop=True)

    def gap_filled(self) -> None:
        """Full readings at half-hours inside a household's covered range
        this month where the source has none, nor an off-grid reading
        within 30 min."""
        n = self.cfg.changes.gap_filled
        if n == 0:
            return
        src = self.src
        stamps = pd.to_datetime(src["Read_date_time_UTC"], format=STAMP_FMT, errors="coerce", utc=True)
        ok = stamps.notna()
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        minutes = ((stamps[ok] - epoch) // pd.Timedelta(minutes=1)).to_numpy(dtype=np.int64)
        occ = pd.DataFrame({"PUPRN": src.loc[ok, "PUPRN"].to_numpy(), "lo": minutes // 30,
                            "hi": -(-minutes // 30)})
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
        cand = cand.reset_index(drop=True)
        k = min(n, len(cand))
        self.short("gap_filled", n, k)
        if not k:
            return
        text = src["Read_date_time_UTC"].to_numpy(dtype=object)
        for r, i in enumerate(np.sort(self.rng("gap_filled").choice(len(cand), k, replace=False))):
            p = cand.at[i, "PUPRN"]
            t = pd.Timestamp(int(cand.at[i, "slot"]) * 30, unit="m", tz="UTC")
            row = self.full_row(p, t)
            later = np.flatnonzero(text > row["Read_date_time_UTC"])
            row["_pos"] = (later[0] if len(later) else len(src)) - 0.5 + r * 1e-6
            self.added.append(row)
            self.note("gap_filled", added=row)

    def full_row(self, puprn: str, t: pd.Timestamp) -> dict:
        """An Edition08 reading for `puprn` at UTC interval end `t`, every
        stream filled and flagged valid."""
        local = t.tz_convert("Europe/London")
        row = {
            "PUPRN": puprn,
            "Read_date_time_UTC": t.strftime(STAMP_FMT),
            "Read_date_time_local": GEN._fmt_local(local),
            "Read_date_effective_local": GEN._effective_date_local(local),
            "HH": str(GEN._hh_index_or_na(t)),
            "Valid_read_time": "True",
        }
        for s in STREAMS:
            flag_col, _ = STREAM_COLUMNS[s]
            row.update(self.fill(puprn, t, s))
            row[flag_col] = OK_FLAG
        return {c: row.get(c, "") for c in self.src.columns}

    # -- ground truth -------------------------------------------------------
    def records(self, compared: List[str], fname: str) -> List[dict]:
        out = []
        cols = list(self.src.columns)
        for kind, i, added in self.changed:
            before = None if i is None else dict(zip(cols, self.src.iloc[i]))
            if added is not None:
                after = added
            elif i in self.dropped:
                after = None
            else:
                after = dict(zip(cols, self.df.iloc[i]))
            key = (before or after)
            for col in compared:
                flag = _flag_col(col)
                vb = None if before is None else before[col]
                va = None if after is None else after[col]
                fb = None if before is None else before[flag]
                fa = None if after is None else after[flag]
                if (vb, fb) == (va, fa):
                    continue
                sb, sa = value_status(vb, fb, col), value_status(va, fa, col)
                out.append({
                    "change": change_of(sb, sa), "kind": kind, "file": fname, "PUPRN": key["PUPRN"],
                    "Read_date_time_UTC": key["Read_date_time_UTC"], "edition_column": col,
                    "value_before": vb, "value_after": va, "flag_before": fb, "flag_after": fa,
                    "status_before": sb, "status_after": sa,
                })
        return out


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

def to_edition09(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    """Edition08 columns (strings) -> Edition09's layout and types."""
    out = df.rename(columns=ED09_RENAME)[ED09_COLUMNS].copy()
    for c in ED09_INT:
        out[c] = pd.to_numeric(out[c].mask(out[c] == "")).astype("Int64")
    out["Valid_read_time"] = out["Valid_read_time"] == "True"
    out["filename"] = filename
    return out


def compared_columns(edition: str) -> List[str]:
    """Value columns both "before" (Edition08) and "after" have."""
    cols = [c for _, cs in STREAM_COLUMNS.values() for c in cs]
    return [c for c in cols if edition == "08" or c != "Gas_hh_m3"]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def convert(cfg: NextEditionConfig, source_dir: Optional[Path] = None, out_dir: Optional[Path] = None) -> dict:
    """Write "after" for the source run. Folders default to
    data/mock/<cfg.source>/ and data/mock/<cfg.output_label>/. Returns the
    manifest."""
    source_dir = Path(source_dir) if source_dir else mock_dir_for(cfg.source)
    out_dir = Path(out_dir) if out_dir else mock_dir_for(cfg.output_label)
    if source_dir.resolve() == out_dir.resolve():
        raise ValueError("output folder must differ from the source folder")
    files = find_edition_files(source_dir, cfg.month_start, cfg.month_end)
    ed = cfg.output_edition
    hh_dir = out_dir / mock_hh_dirname(ed)
    if hh_dir.exists():
        shutil.rmtree(hh_dir)  # a rerun with fewer months shouldn't leave stale files
    hh_dir.mkdir(parents=True)
    internal = out_dir / "mock_internal"
    internal.mkdir(exist_ok=True)
    bst = source_dir / BST_FILE
    shutil.copyfile(bst if bst.exists() else REFERENCE_DIR / BST_FILE, out_dir / BST_FILE)

    compared = compared_columns(ed)
    records: List[dict] = []
    per_month: Dict[str, dict] = {}
    shortfall: Dict[str, Dict[str, int]] = {}
    for year, month, path in files:
        src = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)
        m = _Month(src, year, month, cfg)
        after = m.run()
        stem = f"serl_half_hourly_{year}_{month:02d}_edition{ed}"
        fmt = "parquet" if ed == "09" else "csv"
        fname = f"{stem}.{fmt}"
        if ed == "09":
            write_table(to_edition09(after, fname), hh_dir / stem, format="parquet")
        else:
            after.to_csv(hh_dir / fname, index=False)
        records += m.records(compared, fname)
        key = f"{year}-{month:02d}"
        per_month[key] = {"rows_before": len(src), "rows_after": len(after), **m.planted}
        if m.shortfall:
            shortfall[key] = m.shortfall
            print(f"[next] WARNING {key}: fewer candidates than requested, not planted: {m.shortfall}")
        print(f"[next] {fname}: {len(src):,} -> {len(after):,} rows")

    changes = pd.DataFrame(records, columns=CHANGES_COLUMNS)
    changes.to_csv(internal / CHANGES_FILE, index=False)
    expected: Dict[str, Dict[str, Dict[str, int]]] = {}
    if len(changes):
        changes["_month"] = changes["file"].str.extract(r"(\d{4})_(\d{2})").agg("-".join, axis=1)
        changes["_t"] = changes["status_before"] + " -> " + changes["status_after"]
        for (month, col, t), n in changes.groupby(["_month", "edition_column", "_t"]).size().items():
            expected.setdefault(month, {}).setdefault(col, {})[t] = int(n)
    manifest = {
        "description": "Mock later edition derived from a mock Edition08 run, with planted, "
                       "seeded changes. Not real SERL data.",
        "source_dir": str(source_dir),
        "config": {**asdict(cfg), "changes": asdict(cfg.changes)},
        "kinds": {"resolved": RESOLVED, "introduced": INTRODUCED, "unchanged_status": ["value_changed"]},
        "status_limits": STATUS_LIMITS,
        "compared_columns": compared,
        "changes_file": f"mock_internal/{CHANGES_FILE}",
        "shortfall": shortfall,
        "totals": {k: sum(m[k] for m in per_month.values()) for k in next(iter(per_month.values()))},
        "expected_status_transitions": expected,
        "per_month": per_month,
    }
    (internal / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
