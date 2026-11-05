"""Mock raw half-hourly files made from mock Edition08
(src/serl_mock/raw_from_edition.py, scripts/generate_raw_data.py).

Converts the session's tiny Edition08 fixture, as it is (complete) and with
gaps punched into it (dropped rows, emptied values), and checks — by
comparing raw against the edition directly — that the differences between
them are exactly the planted ones in the ground truth, each in its
category and direction, and that everything else is the edition's reading
in raw form. Also: shortfall, per-month determinism, config validation.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.serl_mock.paths import mock_hh_dirname
from src.serl_mock.raw_from_edition import (
    GAS_COLUMN, MANIFEST_FILE, RAW_COLUMNS, RAW_TO_EDITION, READINGS_FILE,
    VALUE_COLUMNS, PlantCounts, RawConfig, convert, parse_config,
)

KEY = ["PUPRN", "Read_date_time"]
COUNTS = PlantCounts(changed_values=5, edition_blanked=5, edition_removed=5, raw_missing_readings=4,
                     raw_emptied_values=5, raw_duplicates=3, raw_missing_household_days=1)
COMPLETE_COUNTS = replace(COUNTS, edition_blanked=0, edition_removed=0)
HH_DIR = mock_hh_dirname("08")


def _cfg(counts: PlantCounts, **kw) -> RawConfig:
    return RawConfig(source="unused", output_label="unused", seed=7, plants=counts, **kw)


@pytest.fixture(scope="module")
def gappy_source(generated_output_dir: Path, tmp_path_factory) -> Path:
    """The tiny fixture with ~3 % of rows dropped and ~2 % of each value
    column emptied, as an edition with gaps."""
    out = tmp_path_factory.mktemp("gappy_source")
    (out / HH_DIR).mkdir()
    rng = np.random.default_rng(99)
    ed_values = [RAW_TO_EDITION[c] for c in VALUE_COLUMNS]
    for path in sorted((generated_output_dir / HH_DIR).glob("*.csv")):
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df[rng.random(len(df)) > 0.03].copy()
        for col in ed_values:
            df.loc[rng.random(len(df)) < 0.02, col] = ""
        df.to_csv(out / HH_DIR / path.name, index=False)
    return out


@pytest.fixture(scope="module")
def complete_dir(generated_output_dir: Path, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw_complete")
    convert(_cfg(COMPLETE_COUNTS), generated_output_dir, out)
    return out


@pytest.fixture(scope="module")
def gappy_dir(gappy_source: Path, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw_gappy")
    convert(_cfg(COUNTS), gappy_source, out)
    return out


def _read(paths):
    frames = [pd.read_csv(p, dtype=str, keep_default_na=False).assign(_file=p.name) for p in sorted(paths)]
    return pd.concat(frames, ignore_index=True)


def _load(source: Path, raw_dir: Path):
    edition = _read((source / HH_DIR).glob("*.csv"))
    # Edition readings in raw column names (gas still m3).
    edition = edition.rename(columns={e: r for r, e in RAW_TO_EDITION.items() if e})
    raw = _read(raw_dir.glob("SERL_half_hourly_*.csv"))
    truth = pd.read_csv(raw_dir / "mock_internal" / READINGS_FILE, dtype=str, keep_default_na=False)
    manifest = json.loads((raw_dir / "mock_internal" / MANIFEST_FILE).read_text())
    return edition, raw, truth, manifest


@pytest.fixture(scope="module")
def complete(generated_output_dir, complete_dir):
    return _load(generated_output_dir, complete_dir)


@pytest.fixture(scope="module")
def gappy(gappy_source, gappy_dir):
    return _load(gappy_source, gappy_dir)


def _keys(df) -> set:
    return set(map(tuple, df[KEY].to_numpy()))


def _cells(df) -> set:
    return {(r.PUPRN, r.Read_date_time, r.raw_column) for r in df.itertuples()}


def _same(ed: pd.Series, rv: pd.Series, col: str) -> pd.Series:
    """Edition value (gas m3) equals raw value (gas L), cell by cell."""
    if col != GAS_COLUMN:
        return ed == rv
    same = ed == rv  # both empty
    both = (ed != "") & (rv != "")
    same[both] = np.isclose(pd.to_numeric(ed[both]) * 1000, pd.to_numeric(rv[both]))
    return same


def _check_against_edition(edition, raw, truth, manifest):
    """Every difference between raw and edition is a planted one, in its
    category, and every planted one is a difference."""
    cat = lambda *c: truth[truth["category"].isin(c)]
    missing = manifest["households"]["household_not_in_raw"]
    extra = [e["PUPRN"] for e in manifest["households"]["household_not_in_edition"]]
    ed = edition[~edition["PUPRN"].isin(missing)]
    rw = raw[~raw["PUPRN"].isin(extra)]

    # Readings on one side only.
    assert _keys(ed) - _keys(rw) == _keys(cat("only_in_edition"))
    assert _keys(rw) - _keys(ed) == _keys(cat("removed_in_edition"))
    # Duplicates raw adds.
    already = _keys(ed[ed.duplicated(KEY, keep=False)])
    assert _keys(rw[rw.duplicated(KEY, keep=False)]) - already == _keys(cat("duplicate_in_raw"))
    n_dup = len(cat("duplicate_in_raw"))
    assert len(rw) == len(ed) - len(cat("only_in_edition")) + len(cat("removed_in_edition")) + n_dup

    # Values of readings on both sides.
    m = ed.drop_duplicates(KEY).merge(rw.drop_duplicates(KEY), on=KEY, suffixes=("_ed", "_raw"))
    diffs = set()
    for col in VALUE_COLUMNS:
        same = _same(m[f"{col}_ed"], m[f"{col}_raw"], col)
        diffs |= {(p, t, col) for p, t in m.loc[~same, KEY].to_numpy()}
    assert diffs == _cells(cat("changed", "emptied_in_raw", "emptied_in_edition"))
    for col in ("HH_segment", "Read_date_effective"):
        assert (m[f"{col}_ed"] == m[f"{col}_raw"]).all()

    # Directions, from the files themselves.
    lookup = m.set_index(KEY)
    for r in cat("emptied_in_edition").itertuples():
        row = lookup.loc[(r.PUPRN, r.Read_date_time)]
        assert row[f"{r.raw_column}_ed"] == "" and row[f"{r.raw_column}_raw"] != ""
        assert r.edition_value == "" and r.raw_value == row[f"{r.raw_column}_raw"]
    for r in cat("emptied_in_raw").itertuples():
        row = lookup.loc[(r.PUPRN, r.Read_date_time)]
        assert row[f"{r.raw_column}_ed"] != "" and row[f"{r.raw_column}_raw"] == ""
    for r in cat("changed").itertuples():
        row = lookup.loc[(r.PUPRN, r.Read_date_time)]
        assert row[f"{r.raw_column}_ed"] != "" and row[f"{r.raw_column}_raw"] != ""

    # Households.
    assert set(missing) <= set(edition["PUPRN"]) and not set(missing) & set(raw["PUPRN"])
    assert not set(extra) & set(edition["PUPRN"])
    cols = [c for c in RAW_COLUMNS if c != "PUPRN"]
    for e in manifest["households"]["household_not_in_edition"]:
        ex = raw[raw["PUPRN"] == e["PUPRN"]][cols].reset_index(drop=True)
        do = raw[raw["PUPRN"] == e["copied_from"]][cols].reset_index(drop=True)
        pd.testing.assert_frame_equal(ex, do)

    # Every raw row: effective stamp 1 s before the reading's.
    t = pd.to_datetime(raw["Read_date_time"], format="%Y-%m-%dT%H:%M:%SZ")
    eff = pd.to_datetime(raw["Read_date_time_effective"], format="%Y-%m-%dT%H:%M:%SZ")
    assert ((t - eff) == pd.Timedelta(seconds=1)).all()

    # The manifest's expected counts agree with the readings file.
    for category, months in manifest["expected_counts"].items():
        rows = cat(category)
        for month, per_col in months.items():
            in_month = rows[rows["file"].str.contains(month.replace("-", "_"))]
            for col, n in per_col.items():
                cell = in_month["edition_column"] == col
                assert n == (cell.sum() if cell.any() else (in_month["edition_column"] == "").sum())


def test_files_and_columns(complete_dir, generated_output_dir, complete):
    _, raw, _, _ = complete
    names = sorted(p.name for p in complete_dir.glob("SERL_half_hourly_*.csv"))
    assert len(names) == len(list((generated_output_dir / HH_DIR).glob("*.csv"))) == 12
    assert names[0] == "SERL_half_hourly_2021_01.csv"
    assert (complete_dir / "bst_dates_to_2030.csv").exists()
    assert list(raw.columns[:-1]) == RAW_COLUMNS


def test_complete_source_differences_are_the_planted_ones(complete):
    _check_against_edition(*complete)
    _, _, truth, manifest = complete
    counts = truth["kind"].value_counts()
    assert counts["raw_missing_readings"] == 12 * COUNTS.raw_missing_readings
    assert counts["changed_values"] == 12 * COUNTS.changed_values
    assert counts["raw_emptied_values"] == 12 * COUNTS.raw_emptied_values
    assert counts["raw_duplicates"] == 12 * COUNTS.raw_duplicates
    assert truth.loc[truth["kind"] == "raw_missing_household_days", "file"].nunique() == 12
    assert manifest["shortfall"] == {}


def test_gappy_source_differences_are_the_planted_ones(gappy):
    _check_against_edition(*gappy)
    _, _, truth, manifest = gappy
    assert manifest["shortfall"] == {}
    counts = truth["category"].value_counts()
    assert counts["emptied_in_edition"] == 12 * COUNTS.edition_blanked
    assert counts["removed_in_edition"] == 12 * COUNTS.edition_removed


def test_removed_in_edition_rows_are_whole_readings(gappy, complete):
    """A reading only raw has is a full row, its date and half-hour fields
    as the edition derives them: here the readings were dropped from a
    complete edition, which still has the true ones."""
    _, raw, truth, _ = gappy
    full, _, _, _ = complete
    added = truth[truth["category"] == "removed_in_edition"]
    rows = raw.merge(added[KEY], on=KEY)
    assert len(rows) == len(added)
    orig = full.merge(added[KEY], on=KEY)
    m = rows.merge(orig, on=KEY, suffixes=("_raw", "_full"))
    assert len(m) == len(added)  # every slot was a real half-hour of the complete edition
    for col in ("HH_segment", "Read_date_effective"):
        assert (m[f"{col}_raw"] == m[f"{col}_full"]).all()
    assert (rows[VALUE_COLUMNS[0]] != "").all()


def test_shortfall_is_recorded(generated_output_dir, tmp_path, capsys):
    """A complete source has no NULLs or gaps for the realistic kinds."""
    manifest = convert(_cfg(replace(COMPLETE_COUNTS, edition_blanked=3, edition_removed=2),
                            month_start="2021-02", month_end="2021-03"),
                       generated_output_dir, tmp_path)
    assert manifest["shortfall"] == {m: {"edition_blanked": 3, "edition_removed": 2}
                                     for m in ("2021-02", "2021-03")}
    assert "WARNING 2021-02" in capsys.readouterr().out


def test_months_are_independent(generated_output_dir, complete_dir, tmp_path):
    """One month converted on its own is the same file as in the full run."""
    convert(_cfg(COMPLETE_COUNTS, month_start="2021-03", month_end="2021-03"), generated_output_dir, tmp_path)
    assert sorted(p.name for p in tmp_path.glob("SERL_half_hourly_*.csv")) == ["SERL_half_hourly_2021_03.csv"]
    name = "SERL_half_hourly_2021_03.csv"
    assert (tmp_path / name).read_bytes() == (complete_dir / name).read_bytes()


def test_deterministic(gappy_source, gappy_dir, tmp_path):
    convert(_cfg(COUNTS), gappy_source, tmp_path)
    for p in sorted(gappy_dir.glob("SERL_half_hourly_*.csv")) + [gappy_dir / "mock_internal" / READINGS_FILE]:
        rel = p.relative_to(gappy_dir)
        assert (tmp_path / rel).read_bytes() == p.read_bytes(), rel


def test_parse_config_defaults_and_values():
    cfg = parse_config({"source": "edition08_gappy", "months": {"start": "2024-02", "end": None},
                        "plants_per_month": {"edition_blanked": 0}, "extra_households": 2})
    assert cfg.output_label == "raw_from_edition08_gappy"
    assert cfg.month_start == "2024-02" and cfg.month_end is None
    assert cfg.plants.edition_blanked == 0 and cfg.plants.changed_values == 25
    assert cfg.extra_households == 2 and cfg.missing_households == 1


@pytest.mark.parametrize("cfg, match", [
    ({"source": "x", "sedd": 1}, "unknown key"),
    ({"source": "x", "plants_per_month": {"changed": 3}}, "unknown plants_per_month"),
    ({"source": "x", "plants_per_month": {"changed_values": -1}}, ">= 0"),
    ({"source": "x", "plants_per_month": {"changed_values": 1.5}}, ">= 0"),
    ({"source": "x", "extra_households": -1}, ">= 0"),
    ({"source": "x", "months": {"start": "2024-13"}}, "YYYY-MM"),
    ({"source": "x", "months": {"start": "2024-05", "end": "2024-02"}}, "after"),
    ({"source": "x", "months": {"begin": "2024-05"}}, "start, end"),
    ({"output_label": "y"}, "source"),
])
def test_parse_config_rejects(cfg, match):
    with pytest.raises(ValueError, match=match):
        parse_config(cfg)


def test_shipped_configs_parse():
    root = Path(__file__).resolve().parents[1] / "config" / "raw"
    from src.serl_mock.raw_from_edition import load_config
    cfgs = {p.stem: load_config(p) for p in root.glob("*.yaml")}
    assert cfgs["raw_from_edition08"].plants.edition_blanked == 0
    assert cfgs["raw_from_edition08_gappy"].source == "edition08_gappy"
