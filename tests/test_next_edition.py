"""Mock later edition derived from a mock Edition08 run
(src/serl_mock/next_edition.py, scripts/generate_next_edition.py).

Punches problems into the session's tiny Edition08 fixture (dropped rows,
empty values with flag 0, flagged values, meter-max reads), derives a later
edition in both output formats, and checks by comparing the two editions'
files directly (statuses computed here, not by the module) that the
differences are exactly the planted ones, each moving between the statuses
its kind says. Also: identity configs, shortfall, determinism, config
validation, consistency of filled-in readings.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.serl_mock.missingness import STREAM_COLUMNS
from src.serl_mock.next_edition import (
    CHANGES_FILE, ED09_RENAME, MANIFEST_FILE, ChangeCounts, NextEditionConfig, convert, parse_config,
)
from src.serl_mock.paths import mock_hh_dirname

KEY = ["PUPRN", "Read_date_time_UTC"]
COUNTS = ChangeCounts(gap_filled=4, value_filled=4, value_corrected=2, reading_dropped=4,
                      value_lost=4, value_spoiled=2, value_changed=4)
ZERO = ChangeCounts(**{k: 0 for k in ChangeCounts.__dataclass_fields__})
LIMITS = {"Elec_act_imp_hh_Wh": 24000, "Elec_act_exp_hh_Wh": 5000, "Gas_hh_Wh": 89765}
FROM_TO = {
    "gap_filled": ("no_reading", "ok"), "value_filled": ("empty", "ok"),
    "value_corrected": ("out_of_range", "ok"),
    "reading_dropped": ("ok", "no_reading"), "value_lost": ("ok", "empty"),
    "value_spoiled": ("ok", "out_of_range"),
    "value_changed": ("ok", "ok"),
}
FLAG_OF = {c: f for f, cs in STREAM_COLUMNS.values() for c in cs}


def _cfg(counts=COUNTS, edition="09", **kw) -> NextEditionConfig:
    return NextEditionConfig(source="unused", output_label="unused", output_edition=edition, seed=5,
                             changes=counts, **kw)


@pytest.fixture(scope="module")
def problem_source(generated_output_dir: Path, tmp_path_factory) -> Path:
    """The tiny fixture with ~3 % rows dropped, ~2 % of each stream empty
    (flag 0), ~1 % flagged (-2, value kept), ~0.5 % meter-max reads on
    import and gas (flag -1)."""
    out = tmp_path_factory.mktemp("problem_source")
    (out / mock_hh_dirname("08")).mkdir()
    rng = np.random.default_rng(11)
    for path in sorted((generated_output_dir / mock_hh_dirname("08")).glob("*.csv")):
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df[rng.random(len(df)) > 0.03].copy()
        on_grid = (df["HH"] != "").to_numpy()
        for s, (flag, cols) in STREAM_COLUMNS.items():
            empty = (rng.random(len(df)) < 0.02) & on_grid
            df.loc[empty, list(cols)] = ""
            df.loc[empty, flag] = "0"
            flagged = (rng.random(len(df)) < 0.01) & on_grid & ~empty
            df.loc[flagged, flag] = "-2"
            if s in ("elec_imp", "gas"):
                mx = (rng.random(len(df)) < 0.005) & on_grid & ~empty & ~flagged
                df.loc[mx, flag] = "-1"
                if s == "gas":
                    df.loc[mx, "Gas_hh_m3"] = "16777.215"
                    df.loc[mx, "Gas_hh_Wh"] = "184083000"
                else:
                    df.loc[mx, "Elec_act_imp_hh_Wh"] = "16777215"
        df.to_csv(out / mock_hh_dirname("08") / path.name, index=False)
    return out


def _read_after(out: Path, edition: str) -> pd.DataFrame:
    """"After" back in Edition08 names, as strings."""
    hh = out / mock_hh_dirname(edition)
    if edition == "08":
        return pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(hh.glob("*.csv"))],
                         ignore_index=True)
    df = pd.concat([pd.read_parquet(p) for p in sorted(hh.glob("*.parquet"))], ignore_index=True)
    df = df.rename(columns={v: k for k, v in ED09_RENAME.items()}).drop(columns="filename")
    for c in df.columns:
        if c == "Valid_read_time":
            df[c] = df[c].map({True: "True", False: "False"})
        elif str(df[c].dtype) == "Int64":
            df[c] = df[c].astype(object).where(df[c].notna(), "").map(str)
    return df


def _status(value, flag, col) -> str:
    if value is None:
        return "no_reading"
    if value == "":
        return "empty"
    x = float(value)
    if x < 0 or x > LIMITS.get(col, np.inf):
        return "out_of_range"
    return "ok" if flag == "1" else "flagged"


def _differences(before: pd.DataFrame, after: pd.DataFrame, columns) -> pd.DataFrame:
    """Every (reading, column) whose value or flag differs, with statuses."""
    m = before.merge(after, on=KEY, how="outer", suffixes=("_b", "_a"), indicator=True)
    rows = []
    for r in m.to_dict("records"):
        for col in columns:
            f = FLAG_OF[col]
            vb = None if r["_merge"] == "right_only" else r[f"{col}_b"]
            va = None if r["_merge"] == "left_only" else r[f"{col}_a"]
            fb = None if r["_merge"] == "right_only" else r[f"{f}_b"]
            fa = None if r["_merge"] == "left_only" else r[f"{f}_a"]
            if (vb, fb) != (va, fa):
                rows.append({"PUPRN": r["PUPRN"], "Read_date_time_UTC": r["Read_date_time_UTC"],
                             "edition_column": col, "status_before": _status(vb, fb, col),
                             "status_after": _status(va, fa, col)})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module", params=["09", "08"])
def derived(request, problem_source, tmp_path_factory):
    edition = request.param
    out = tmp_path_factory.mktemp(f"next_{edition}")
    manifest = convert(_cfg(edition=edition), problem_source, out)
    before = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False)
                        for p in sorted((problem_source / mock_hh_dirname("08")).glob("*.csv"))],
                       ignore_index=True)
    truth = pd.read_csv(out / "mock_internal" / CHANGES_FILE, dtype=str, keep_default_na=False)
    return edition, out, before, _read_after(out, edition), truth, manifest


def test_differences_are_exactly_the_planted_ones(derived):
    edition, _, before, after, truth, manifest = derived
    cols = manifest["compared_columns"]
    assert ("Gas_hh_m3" in cols) == (edition == "08")
    found = _differences(before, after, cols)
    key = ["PUPRN", "Read_date_time_UTC", "edition_column", "status_before", "status_after"]
    a = found[key].sort_values(key).reset_index(drop=True)
    b = truth[key].sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
    assert manifest["shortfall"] == {}


def test_each_kind_moves_between_its_statuses(derived):
    _, _, _, _, truth, manifest = derived
    for kind, (sb, sa) in FROM_TO.items():
        rows = truth[truth["kind"] == kind]
        assert len(rows), kind
        # value_lost on gas and similar stream changes touch every column of
        # the stream; the primary column always moves as the kind says.
        assert ((rows["status_before"] == sb) & (rows["status_after"] == sa)).any(), kind
        if kind in ("gap_filled", "reading_dropped"):
            assert (rows["status_before"] == sb).all() and (rows["status_after"] == sa).all()
    readings = truth.drop_duplicates(["kind", "PUPRN", "Read_date_time_UTC"])["kind"].value_counts()
    for kind in FROM_TO:
        assert readings[kind] == 12 * getattr(COUNTS, kind), kind
    change = dict(zip(truth["kind"], truth["change"]))
    assert change["gap_filled"] == "resolved" and change["value_lost"] == "introduced"
    assert change["value_changed"] == "unchanged"


def test_flags_only_change_with_their_value(derived):
    """No flag-only change: wherever a flag differs, the value differs too.
    (A move to or from `flagged` only happens with a value change, e.g. a
    gas meter-max read is `flagged` in Gas_hh_m3, which has no limit.)"""
    _, _, before, after, truth, manifest = derived
    both = truth[(truth["status_before"] != "no_reading") & (truth["status_after"] != "no_reading")]
    flag_changed = both["flag_before"] != both["flag_after"]
    assert (both.loc[flag_changed, "value_before"] != both.loc[flag_changed, "value_after"]).all()
    assert (both["value_before"] != both["value_after"]).all()
    # and from the files: no reading in both whose value is the same but its flag differs
    m = before.merge(after, on=KEY, suffixes=("_b", "_a"))
    for col in manifest["compared_columns"]:
        f = FLAG_OF[col]
        assert not ((m[f"{col}_b"] == m[f"{col}_a"]) & (m[f"{f}_b"] != m[f"{f}_a"])).any(), col


def test_one_change_per_reading(derived):
    _, _, _, _, truth, _ = derived
    per_reading = truth.groupby(["PUPRN", "Read_date_time_UTC"])["kind"].nunique()
    assert (per_reading == 1).all()


def test_filled_readings_are_consistent(derived, generated_output_dir):
    """A gap filled is a full valid reading whose date and half-hour fields
    are the ones the generator gave that half-hour (the complete fixture)."""
    _, _, _, after, truth, _ = derived
    filled = truth[truth["kind"] == "gap_filled"][KEY].drop_duplicates()
    full = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False)
                      for p in sorted((generated_output_dir / mock_hh_dirname("08")).glob("*.csv"))],
                     ignore_index=True)
    m = after.merge(filled, on=KEY).merge(full, on=KEY, suffixes=("", "_full"))
    assert len(m) == len(filled)
    for c in ("HH", "Read_date_effective_local", "Read_date_time_local", "Valid_read_time"):
        assert (m[c] == m[f"{c}_full"]).all(), c
    flags = [f for f, _ in STREAM_COLUMNS.values() if f in m.columns]
    assert (m[flags] == "1").all(axis=None)


def test_expected_transitions_match_the_changes(derived):
    _, _, _, _, truth, manifest = derived
    total = sum(n for per_col in manifest["expected_status_transitions"].values()
                for ts in per_col.values() for n in ts.values())
    assert total == len(truth)


def test_identity_configs(problem_source, tmp_path):
    convert(_cfg(ZERO, edition="08"), problem_source, tmp_path / "08")
    for p in sorted((problem_source / mock_hh_dirname("08")).glob("*.csv")):
        assert (tmp_path / "08" / mock_hh_dirname("08") / p.name).read_bytes() == p.read_bytes()
    convert(_cfg(ZERO, edition="09"), problem_source, tmp_path / "09")
    before = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False)
                        for p in sorted((problem_source / mock_hh_dirname("08")).glob("*.csv"))],
                       ignore_index=True)
    after = _read_after(tmp_path / "09", "09")
    assert _differences(before, after, ["Elec_act_imp_hh_Wh", "Gas_hh_Wh"]).empty
    assert len(after) == len(before)


def test_shortfall_on_a_complete_source(generated_output_dir, tmp_path, capsys):
    manifest = convert(_cfg(replace(ZERO, value_filled=2, value_corrected=1, gap_filled=1),
                            month_start="2021-02", month_end="2021-02"),
                       generated_output_dir, tmp_path)
    assert manifest["shortfall"] == {"2021-02": {"value_filled": 2, "value_corrected": 1, "gap_filled": 1}}
    assert "WARNING 2021-02" in capsys.readouterr().out


def test_deterministic_and_months_independent(problem_source, tmp_path):
    convert(_cfg(edition="08"), problem_source, tmp_path / "all")
    convert(_cfg(edition="08", month_start="2021-05", month_end="2021-05"), problem_source, tmp_path / "one")
    name = "serl_half_hourly_2021_05_edition08.csv"
    hh = mock_hh_dirname("08")
    assert (tmp_path / "one" / hh / name).read_bytes() == (tmp_path / "all" / hh / name).read_bytes()


@pytest.mark.parametrize("cfg, match", [
    ({"source": "x", "colour": 1}, "unknown key"),
    ({"source": "x", "changes_per_month": {"gap_fill": 1}}, "unknown changes_per_month"),
    ({"source": "x", "changes_per_month": {"gap_filled": -1}}, ">= 0"),
    ({"source": "x", "output_edition": "07"}, "output_edition"),
    ({"source": "x", "flag_raised_code": -2}, "unknown key"),
    ({"source": "x", "changes_per_month": {"flag_cleared": 1}}, "unknown changes_per_month"),
    ({"source": "x", "months": {"start": "2024-13"}}, "YYYY-MM"),
    ({"output_label": "y"}, "source"),
])
def test_parse_config_rejects(cfg, match):
    with pytest.raises(ValueError, match=match):
        parse_config(cfg)


def test_shipped_config_parses():
    from src.serl_mock.next_edition import load_config
    root = Path(__file__).resolve().parents[1] / "config" / "next_edition"
    cfg = load_config(root / "edition09_from_edition08_gappy.yaml")
    assert cfg.source == "edition08_gappy" and cfg.output_edition == "09"
    assert cfg.output_label == "edition09_revised"
