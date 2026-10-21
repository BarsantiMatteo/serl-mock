# src/serl_mock/missingness.py
"""
Synthetic missingness for the half-hourly smart-meter data.

Real SERL data has gaps: households whose meters stop reporting for days,
scattered missing half-hours, days when many households drop out at once,
late starts, export or gas reads that fail while import is fine, invalid
read times, duplicate and off-grid stamps. The base generator produces
none of these, so analysis code developed on it never sees them. This
module plants them, as configured under `missingness:` in the config
(see docs/02_configuration.md), and records what it planted.

Off by default (`missingness.enabled: false`): with it off, nothing here
runs and the output is unchanged.

How it plugs in
---------------
HHSmartMeterGenerator builds a MissingnessPlanner in __init__ and calls
`apply(df, ...)` at the end of generate_month(), after the flags and before
the edition09 reshape. DailySmartMeterGenerator builds its own
HHSmartMeterGenerator with the same config, so it plants exactly the same
gaps and the daily data stays consistent with the HH files.

Determinism
-----------
Every random draw uses its own np.random.Generator seeded from
(seed, _SEED_SALT, pattern index, purpose[, year, month]). Nothing here
touches the base generator's rng, so switching missingness on or off
doesn't change any other generated value, and the HH and daily passes
plant identical gaps.

Patterns
--------
Each pattern picks households (`households: {share, traits}`), then
half-hours (`type`), optionally only inside a local clock `time_window`,
then changes the affected rows (`effect`):

  type        outage      runs at random positions, per household
              scattered   single half-hours, each household with its own
                          log-uniform rate in [low, high]
              event       a fixed local date-time range, same for everyone
              late_start  a run from the start of the period
              early_end   a run up to the end of the period

  effect      drop_row      the row is not written: every stream absent
              missing       the streams' flag 0 and value empty, as SERL
                            records a missing read
              flag          the streams' flag set to `code` (optionally
                            `null_value: true` empties the value too)
              null_value    the streams' value emptied, flag kept
              max_read      the streams' flag -1 and value the meter's
                            maximum (16777215 Wh, 16777.215 m3), as SERL
                            records a 'max read'
              invalid_time  Valid_read_time FALSE
              duplicate     the row written twice
              off_grid      the stamp moved `minutes` off :00/:30, handled
                            like the base generator's skewed stamps
                            (HH NA, Valid_read_time FALSE, flags -5)

Patterns run in order. A later pattern only targets rows still on their
half-hour (not dropped or moved off-grid by an earlier one), and never the
base generator's own skewed stamps.

Ground truth
------------
`ground_truth()` returns one row per planted run (consecutive half-hours of
one pattern in one household), with UTC half-hour START times
(`start_utc` inclusive, `end_utc` exclusive), so a consumer keyed on the
half-hour start can compare directly. run_all writes it to
mock_internal/missingness_ground_truth.csv, and a per-pattern summary to
mock_internal/missingness_patterns.csv.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

PATTERN_TYPES = ("outage", "scattered", "event", "late_start", "early_end")
ROW_EFFECTS = ("drop_row", "invalid_time", "duplicate", "off_grid")
STREAM_EFFECTS = ("missing", "flag", "null_value", "max_read")

# Logical stream -> (flag column, value columns), in the legacy column names
# generate_month() builds before any edition09 reshape.
STREAM_COLUMNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "elec_imp":       ("Elec_act_imp_flag",    ("Elec_act_imp_hh_Wh",)),
    "elec_exp":       ("Elec_act_exp_flag",    ("Elec_act_exp_hh_Wh",)),
    "elec_react_imp": ("Elect_react_imp_flag", ("Elec_react_imp_hh_varh",)),
    "elec_react_exp": ("Elect_react_exp_flag", ("Elec_react_exp_hh_varh",)),
    "gas":            ("Gas_flag",             ("Gas_hh_m3", "Gas_hh_Wh")),
}
ALL_FLAG_COLUMNS = tuple(flag for flag, _ in STREAM_COLUMNS.values())

_SEED_SALT = 7_919          # keeps these streams apart from any other seeded use
_PURPOSE = {"households": 0, "rates": 1, "runs": 2, "scattered": 3}
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[03]0$")
_HALF_HOUR = pd.Timedelta(minutes=30)
_TZ = "Europe/London"


# ---------- Config ----------

@dataclass(frozen=True)
class Effect:
    type: str
    code: Optional[int] = None
    null_value: bool = False
    minutes: Optional[int] = None

    @property
    def label(self) -> str:
        if self.type == "flag":
            return f"flag={self.code}" + ("+null_value" if self.null_value else "")
        if self.type == "off_grid":
            return f"off_grid+{self.minutes}min"
        return self.type


@dataclass(frozen=True)
class Pattern:
    name: str
    type: str
    effect: Effect
    share: float = 1.0
    traits: dict = field(default_factory=dict)
    time_window: Optional[tuple[str, str]] = None
    streams: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)


def _err(msg: str) -> ValueError:
    return ValueError(f"[missingness] {msg}")


def _bands(v, where: str) -> list[tuple[float, float, float]]:
    if not isinstance(v, list) or not v:
        raise _err(f"{where}: expected [[min_days, max_days, weight], ...]")
    out = []
    for b in v:
        if not (isinstance(b, list) and len(b) == 3 and all(isinstance(x, (int, float)) for x in b)):
            raise _err(f"{where}: bad band {b!r}")
        lo, hi, w = (float(x) for x in b)
        if not (0 < lo <= hi and w > 0):
            raise _err(f"{where}: band {b!r} needs 0 < min <= max and weight > 0")
        out.append((lo, hi, w))
    return out


def _effect(v, where: str) -> Effect:
    if isinstance(v, str):
        v = {"type": v}
    if not isinstance(v, dict) or v.get("type") not in ROW_EFFECTS + STREAM_EFFECTS:
        raise _err(f"{where}: effect must be one of {ROW_EFFECTS + STREAM_EFFECTS}, got {v!r}")
    t = v["type"]
    if t == "flag" and not isinstance(v.get("code"), int):
        raise _err(f"{where}: flag effect needs an integer `code`")
    if t == "off_grid" and not (isinstance(v.get("minutes"), int) and v["minutes"] % 30 != 0):
        raise _err(f"{where}: off_grid effect needs integer `minutes`, not a multiple of 30")
    return Effect(type=t, code=v.get("code"), null_value=bool(v.get("null_value", False)),
                  minutes=v.get("minutes"))


def parse_patterns(cfg: Optional[dict], trait_columns: Optional[set[str]] = None) -> list[Pattern]:
    """Validate the `missingness:` config block. Returns [] when it's absent
    or `enabled` is not true. Disabled patterns are skipped."""
    cfg = cfg or {}
    if not cfg.get("enabled", False):
        return []
    patterns, seen = [], set()
    for i, p in enumerate(cfg.get("patterns") or []):
        name = p.get("name")
        if not (isinstance(name, str) and _NAME.match(name)) or name in seen:
            raise _err(f"patterns[{i}]: `name` must be a unique identifier, got {name!r}")
        seen.add(name)
        where = f"pattern {name}"
        if not p.get("enabled", True):
            continue
        ptype = p.get("type")
        if ptype not in PATTERN_TYPES:
            raise _err(f"{where}: type must be one of {PATTERN_TYPES}, got {ptype!r}")

        hh = p.get("households") or {}
        share = float(hh.get("share", 1.0))
        if not 0 <= share <= 1:
            raise _err(f"{where}: households.share must be in [0, 1]")
        traits = dict(hh.get("traits") or {})
        for col, val in traits.items():
            if trait_columns is not None and col not in trait_columns:
                raise _err(f"{where}: unknown household trait {col!r} (have {sorted(trait_columns)})")
            if val not in (0, 1, True, False):
                raise _err(f"{where}: households.traits.{col} must be true/false (or 1/0)")

        tw = p.get("time_window")
        if tw is not None:
            if not (isinstance(tw, dict) and all(_CLOCK.match(str(tw.get(k, ""))) for k in ("start", "end"))):
                raise _err(f"{where}: time_window needs start/end as HH:00 or HH:30")
            tw = (tw["start"], tw["end"])

        effect = _effect(p.get("effect"), where)
        streams = tuple(p.get("streams") or ())
        if effect.type in STREAM_EFFECTS:
            if not streams:
                raise _err(f"{where}: effect {effect.type} needs `streams` (any of {sorted(STREAM_COLUMNS)})")
            unknown = set(streams) - set(STREAM_COLUMNS)
            if unknown:
                raise _err(f"{where}: unknown streams {sorted(unknown)} (have {sorted(STREAM_COLUMNS)})")
        elif streams:
            raise _err(f"{where}: effect {effect.type} changes the whole row; drop `streams`")

        params: dict[str, Any] = {}
        if ptype == "outage":
            n = p.get("per_household", [1, 1])
            if not (isinstance(n, list) and len(n) == 2 and all(isinstance(x, int) for x in n) and 0 <= n[0] <= n[1]):
                raise _err(f"{where}: per_household must be [min, max] integers")
            params["per_household"] = (n[0], n[1])
            params["length_days"] = _bands(p.get("length_days"), f"{where}.length_days")
        elif ptype in ("late_start", "early_end"):
            params["length_days"] = _bands(p.get("length_days"), f"{where}.length_days")
        elif ptype == "scattered":
            pr = p.get("prob_per_slot") or {}
            lo = float(pr.get("low", 0))
            hi = float(pr.get("high", lo))
            if not 0 < lo <= hi <= 1:
                raise _err(f"{where}: prob_per_slot needs 0 < low <= high <= 1")
            params["prob_per_slot"] = (lo, hi)
        elif ptype == "event":
            try:
                start = datetime.fromisoformat(str(p["start"]))
                end = datetime.fromisoformat(str(p["end"]))
            except (KeyError, ValueError) as e:
                raise _err(f"{where}: event needs start/end as 'YYYY-MM-DD HH:MM' (local time)") from e
            if start >= end:
                raise _err(f"{where}: event start must be before end")
            params["start"], params["end"] = start, end

        patterns.append(Pattern(name=name, type=ptype, effect=effect, share=share, traits=traits,
                                time_window=tw, streams=streams, params=params))
    return patterns


# ---------- Planner ----------

class MissingnessPlanner:
    """Plans and applies the configured patterns for one generator.

    The period is every half-hour from 00:00 UTC on 1 Jan `start_year` to
    the end of `end_year`, indexed 0..n_slots-1 by half-hour START. Run
    patterns (outage, event, late_start, early_end) are drawn once here;
    scattered ones per month in apply().
    """

    def __init__(self, patterns: list[Pattern], households: list[str], traits: pd.DataFrame,
                 start_year: int, end_year: int, seed: int):
        self.patterns = patterns
        self.households = list(households)
        self.seed = int(seed)
        self.period_start = pd.Timestamp(year=start_year, month=1, day=1, tz="UTC")
        period_end = pd.Timestamp(year=end_year + 1, month=1, day=1, tz="UTC")
        self.n_slots = int((period_end - self.period_start) / _HALF_HOUR)
        H = len(self.households)

        self.selected: list[np.ndarray] = []      # per pattern: bool (H,)
        self.rates: list[Optional[np.ndarray]] = []
        self.runs: list[list[tuple[int, int, int]]] = []   # per pattern: (h, start_slot, n_slots)
        for k, p in enumerate(patterns):
            sel = self._rng(k, "households").random(H) < p.share
            for col, val in p.traits.items():
                sel &= traits.loc[self.households, col].astype(int).to_numpy() == int(bool(val))
            self.selected.append(sel)
            self.rates.append(self._draw_rates(k, p, H) if p.type == "scattered" else None)
            self.runs.append(self._draw_runs(k, p, sel) if p.type != "scattered" else [])

        self._records: list[pd.DataFrame] = []

    # -- randomness --

    def _rng(self, k: int, purpose: str, *extra: int) -> np.random.Generator:
        return np.random.default_rng([self.seed, _SEED_SALT, k, _PURPOSE[purpose], *extra])

    def _draw_rates(self, k: int, p: Pattern, H: int) -> np.ndarray:
        lo, hi = p.params["prob_per_slot"]
        u = self._rng(k, "rates").random(H)
        return np.exp(math.log(lo) + u * math.log(hi / lo))   # log-uniform in [lo, hi]

    def _draw_length(self, rng: np.random.Generator, bands) -> int:
        weights = np.array([w for _, _, w in bands])
        lo, hi, _ = bands[rng.choice(len(bands), p=weights / weights.sum())]
        return max(1, int(round(rng.uniform(lo, hi) * 48)))

    def _draw_runs(self, k: int, p: Pattern, selected: np.ndarray) -> list[tuple[int, int, int]]:
        rng = self._rng(k, "runs")
        runs = []
        if p.type == "event":
            start = self._local_to_slot(p.params["start"])
            end = self._local_to_slot(p.params["end"])
            start, end = max(start, 0), min(end, self.n_slots)
            if end > start:
                runs = [(h, start, end - start) for h in np.flatnonzero(selected)]
            return runs
        for h in np.flatnonzero(selected):     # draws only for selected households, in order
            if p.type == "outage":
                n_min, n_max = p.params["per_household"]
                for _ in range(int(rng.integers(n_min, n_max + 1))):
                    length = min(self._draw_length(rng, p.params["length_days"]), self.n_slots)
                    start = int(rng.integers(0, self.n_slots - length + 1))
                    runs.append((int(h), start, length))
            else:
                length = min(self._draw_length(rng, p.params["length_days"]), self.n_slots)
                start = 0 if p.type == "late_start" else self.n_slots - length
                runs.append((int(h), start, length))
        return runs

    def _local_to_slot(self, local: datetime) -> int:
        ts = pd.Timestamp(local).tz_localize(_TZ, ambiguous=True, nonexistent="shift_forward")
        return int(np.ceil((ts.tz_convert("UTC") - self.period_start) / _HALF_HOUR))

    # -- applying --

    def apply(self, df: pd.DataFrame, stamps_utc: pd.Series, year: int, month: int,
              fmt) -> pd.DataFrame:
        """Apply every pattern to one month's frame.

        `df` is generate_month()'s legacy-schema frame, time-major (row =
        t * H + h), still holding `timestamp_utc`. `stamps_utc` holds the T
        end-of-half-hour stamps (tz-aware UTC), skewed ones included. `fmt`
        is the generator, for its timestamp formatting helpers (off_grid).
        Returns the changed frame and records what was planted.
        """
        H = len(self.households)
        T = len(stamps_utc)
        N = T * H
        stamps = pd.DatetimeIndex(stamps_utc)
        starts = stamps - _HALF_HOUR
        on_grid_t = np.asarray((stamps.second == 0) & np.isin(stamps.minute, (0, 30)))
        slot_t = np.full(T, -1, dtype=np.int64)
        slot_t[on_grid_t] = np.asarray((starts[on_grid_t] - self.period_start) / _HALF_HOUR, dtype=np.int64)
        local_tod = starts.tz_convert(_TZ).strftime("%H:%M")
        h_n = np.tile(np.arange(H), T)
        t_n = np.repeat(np.arange(T), H)

        alive = np.repeat(on_grid_t, H)      # rows still on their half-hour
        keep = np.ones(N, dtype=bool)
        duplicates = []
        ongrid_t_idx = np.flatnonzero(on_grid_t)
        ongrid_slots = slot_t[on_grid_t]

        for k, p in enumerate(self.patterns):
            target = np.zeros(N, dtype=bool)
            if p.type == "scattered":
                u = self._rng(k, "scattered", year, month).random(N)
                target = (u < self.rates[k][h_n]) & self.selected[k][h_n]
            else:
                for h, s, n in self.runs[k]:
                    lo, hi = np.searchsorted(ongrid_slots, [s, s + n])
                    if hi > lo:
                        target[ongrid_t_idx[lo:hi] * H + h] = True
            if p.time_window:
                in_window = np.asarray((local_tod >= p.time_window[0]) & (local_tod < p.time_window[1]))
                target &= in_window[t_n]
            target &= alive
            if not target.any():
                continue

            df = self._apply_effect(df, p, target, fmt)
            if p.effect.type == "drop_row":
                keep &= ~target
                alive &= ~target
            elif p.effect.type == "off_grid":
                alive &= ~target
            elif p.effect.type == "duplicate":
                duplicates.append(df.loc[target].copy())

            self._records.append(pd.DataFrame({
                "pattern_index": k,
                "h": h_n[target],
                "slot": slot_t[t_n[target]],
            }))

        if not keep.all() or duplicates:
            df = df.loc[keep]
            if duplicates:
                df = pd.concat([df, *duplicates]).sort_values("timestamp_utc", kind="stable")
            df = df.reset_index(drop=True)
        return df

    @staticmethod
    def _null(df: pd.DataFrame, cols, mask: np.ndarray) -> None:
        for c in cols:
            if pd.api.types.is_integer_dtype(df[c]) and not isinstance(df[c].dtype, pd.Int64Dtype):
                df[c] = df[c].astype("Int64")
            df.loc[mask, c] = pd.NA if isinstance(df[c].dtype, pd.Int64Dtype) else np.nan

    @staticmethod
    def _max_value(col: str, fmt) -> float:
        """The meter-max read (all 1s in 32 bits) in `col`'s units, from the
        generator's own flag thresholds."""
        v = fmt.FLAG_THRESHOLDS
        if col == "Gas_hh_m3":
            return v["gas_meter_max_m3"]
        if col == "Gas_hh_Wh":
            return round(v["gas_meter_max_m3"] * fmt.GAS_WH_PER_M3)
        return v["elec_meter_max_Wh"]

    def _apply_effect(self, df: pd.DataFrame, p: Pattern, mask: np.ndarray, fmt) -> pd.DataFrame:
        e = p.effect
        if e.type in STREAM_EFFECTS:
            for s in p.streams:
                flag_col, value_cols = STREAM_COLUMNS[s]
                if e.type == "missing":
                    df.loc[mask, flag_col] = 0
                elif e.type == "flag":
                    df.loc[mask, flag_col] = e.code
                elif e.type == "max_read":
                    df.loc[mask, flag_col] = -1
                    for c in value_cols:
                        df.loc[mask, c] = self._max_value(c, fmt)
                if e.type in ("missing", "null_value") or e.null_value:
                    self._null(df, value_cols, mask)
        elif e.type == "invalid_time":
            df.loc[mask, "Valid_read_time"] = False
        elif e.type == "off_grid":
            # timestamp_utc itself is left as is: it only orders duplicate rows.
            moved = pd.DatetimeIndex(pd.to_datetime(df.loc[mask, "timestamp_utc"], utc=True)) \
                + pd.Timedelta(minutes=e.minutes)
            local = moved.tz_convert(_TZ)
            df.loc[mask, "Read_date_time_UTC"] = [fmt._fmt_utc(ts) for ts in moved]
            df.loc[mask, "Read_date_time_local"] = [fmt._fmt_local(ts) for ts in local]
            df.loc[mask, "Read_date_effective_local"] = [fmt._effective_date_local(ts) for ts in local]
            df.loc[mask, "HH"] = pd.NA
            df.loc[mask, "Valid_read_time"] = False
            for c in ALL_FLAG_COLUMNS:
                df.loc[mask, c] = -5
        return df

    # -- ground truth --

    def ground_truth(self) -> pd.DataFrame:
        """One row per planted run, in UTC half-hour START times."""
        cols = ["pattern", "type", "effect", "streams", "PUPRN", "start_utc", "end_utc", "n_slots"]
        if not self._records:
            return pd.DataFrame(columns=cols)
        rec = pd.concat(self._records, ignore_index=True).drop_duplicates()
        rec = rec.sort_values(["pattern_index", "h", "slot"], kind="stable")
        island = rec["slot"] - rec.groupby(["pattern_index", "h"]).cumcount()
        runs = (rec.assign(island=island)
                   .groupby(["pattern_index", "h", "island"], sort=False)
                   .agg(first=("slot", "min"), n_slots=("slot", "size"))
                   .reset_index())
        out = pd.DataFrame({
            "pattern":  [self.patterns[k].name for k in runs["pattern_index"]],
            "type":     [self.patterns[k].type for k in runs["pattern_index"]],
            "effect":   [self.patterns[k].effect.label for k in runs["pattern_index"]],
            "streams":  [",".join(self.patterns[k].streams) or "all" for k in runs["pattern_index"]],
            "PUPRN":    [self.households[h] for h in runs["h"]],
            "start_utc": self.period_start + runs["first"].to_numpy() * _HALF_HOUR,
            "end_utc":   self.period_start + (runs["first"] + runs["n_slots"]).to_numpy() * _HALF_HOUR,
            "n_slots":  runs["n_slots"].to_numpy(),
        })
        for c in ("start_utc", "end_utc"):
            out[c] = pd.DatetimeIndex(out[c]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        return out[cols]

    def pattern_summary(self) -> pd.DataFrame:
        gt = self.ground_truth()
        rows = []
        for p in self.patterns:
            g = gt[gt["pattern"] == p.name]
            rows.append({
                "pattern": p.name, "type": p.type, "effect": p.effect.label,
                "streams": ",".join(p.streams) or "all",
                "n_households": int(g["PUPRN"].nunique()), "n_slots": int(g["n_slots"].sum()),
                "config": json.dumps({**asdict(p), "effect": asdict(p.effect)}, default=str),
            })
        return pd.DataFrame(rows)
