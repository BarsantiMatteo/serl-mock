"""Mock raw half-hourly files made from mock Edition08
(src/serl_mock/raw_from_edition.py).

Converts the session's tiny Edition08 fixture and checks, by comparing raw
against the edition directly, that the differences between them are
exactly the planted ones listed in the ground truth, and that everything
else is the edition's reading in raw form.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.serl_mock.raw_from_edition import (
    GAS_COLUMN, MANIFEST_FILE, RAW_COLUMNS, RAW_TO_EDITION, READINGS_FILE,
    VALUE_COLUMNS, PlantCounts, convert,
)

KEY = ["PUPRN", "Read_date_time"]
COUNTS = PlantCounts(removed_household_days=1, removed_readings=4, changed_values=5,
                     null_values=5, duplicated_readings=3)


@pytest.fixture(scope="module")
def raw_dir(generated_output_dir: Path, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw_from_edition08")
    convert(generated_output_dir, out, seed=7, counts=COUNTS)
    return out


def _read(path_glob):
    paths = sorted(path_glob)
    frames = [pd.read_csv(p, dtype=str, keep_default_na=False).assign(_file=p.name) for p in paths]
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def frames(generated_output_dir: Path, raw_dir: Path):
    edition = _read((generated_output_dir / "serl_smart_meter_hh_edition08").glob("*.csv"))
    # Edition readings in raw column names (gas still m3).
    edition = edition.rename(columns={e: r for r, e in RAW_TO_EDITION.items() if e})
    raw = _read(raw_dir.glob("SERL_half_hourly_*.csv"))
    truth = pd.read_csv(raw_dir / "mock_internal" / READINGS_FILE, dtype=str, keep_default_na=False)
    manifest = json.loads((raw_dir / "mock_internal" / MANIFEST_FILE).read_text())
    return edition, raw, truth, manifest


def _keys(df) -> set:
    return set(map(tuple, df[KEY].to_numpy()))


def test_files_and_columns(raw_dir, generated_output_dir, frames):
    _, raw, _, _ = frames
    n_months = len(list((generated_output_dir / "serl_smart_meter_hh_edition08").glob("*.csv")))
    names = sorted(p.name for p in raw_dir.glob("SERL_half_hourly_*.csv"))
    assert len(names) == n_months == 12
    assert names[0] == "SERL_half_hourly_2021_01.csv"
    assert (raw_dir / "bst_dates_to_2030.csv").exists()
    assert list(raw.columns[:-1]) == RAW_COLUMNS


def test_households(frames):
    edition, raw, _, manifest = frames
    missing = manifest["missing_household"]
    extra, donor = manifest["extra_household"]["PUPRN"], manifest["extra_household"]["copied_from"]
    assert missing in set(edition["PUPRN"]) and missing not in set(raw["PUPRN"])
    assert extra not in set(edition["PUPRN"])
    assert set(raw["PUPRN"]) == set(edition["PUPRN"]) - {missing} | {extra}
    # The extra household is the donor's readings, untouched.
    cols = [c for c in RAW_COLUMNS if c != "PUPRN"]
    ex = raw[raw["PUPRN"] == extra][cols].reset_index(drop=True)
    do = raw[raw["PUPRN"] == donor][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(ex, do)
    assert manifest["totals"]["extra_household_rows"] == len(ex)
    assert manifest["totals"]["missing_household_rows"] == int((edition["PUPRN"] == missing).sum())


def test_removed_and_duplicated_readings(frames):
    edition, raw, truth, manifest = frames
    extra = manifest["extra_household"]["PUPRN"]
    ed = edition[edition["PUPRN"] != manifest["missing_household"]]
    rw = raw[raw["PUPRN"] != extra]
    removed = truth[truth["kind"].isin(["removed", "removed_household_day"])]
    assert _keys(ed) - _keys(rw) == _keys(removed)
    assert len(removed) == len(_keys(removed))
    already_dup = _keys(ed[ed.duplicated(KEY, keep=False)])
    new_dup = _keys(rw[rw.duplicated(KEY, keep=False)]) - already_dup
    assert new_dup == _keys(truth[truth["kind"] == "duplicated"])
    assert len(rw) == len(ed) - len(removed) + (truth["kind"] == "duplicated").sum()
    # One household-day per month, scattered readings as configured.
    assert (truth["kind"] == "removed").sum() == 12 * COUNTS.removed_readings
    days = truth[truth["kind"] == "removed_household_day"]
    assert days["file"].nunique() == 12


def test_values_differ_only_where_planted(frames):
    edition, raw, truth, _ = frames
    rw = raw.drop_duplicates(KEY)
    m = edition.merge(rw, on=KEY, suffixes=("_ed", "_raw"))
    diffs = set()
    for col in VALUE_COLUMNS:
        ed, rv = m[f"{col}_ed"], m[f"{col}_raw"]
        if col == GAS_COLUMN:
            both = (ed != "") & (rv != "")
            same = (ed == rv)
            same[both] = np.isclose(pd.to_numeric(ed[both]) * 1000, pd.to_numeric(rv[both]))
        else:
            same = ed == rv
        diffs |= {(p, t, col) for p, t in m.loc[~same, KEY].to_numpy()}
    planted = truth[truth["kind"].isin(["changed", "null"])]
    assert diffs == {(r.PUPRN, r.Read_date_time, r.raw_column) for r in planted.itertuples()}
    assert (planted["kind"] == "changed").sum() == 12 * COUNTS.changed_values
    assert (planted["kind"] == "null").sum() == 12 * COUNTS.null_values
    assert (planted.loc[planted["kind"] == "null", "raw_value"] == "").all()
    assert (planted["edition_value"] != "").all()
    # Other columns carried over unchanged.
    for col in ("HH_segment", "Read_date_effective"):
        assert (m[f"{col}_ed"] == m[f"{col}_raw"]).all()


def test_read_date_time_effective(frames):
    _, raw, _, _ = frames
    t = pd.to_datetime(raw["Read_date_time"], format="%Y-%m-%dT%H:%M:%SZ")
    eff = pd.to_datetime(raw["Read_date_time_effective"], format="%Y-%m-%dT%H:%M:%SZ")
    assert ((t - eff) == pd.Timedelta(seconds=1)).all()


def test_deterministic(generated_output_dir, raw_dir, tmp_path):
    convert(generated_output_dir, tmp_path, seed=7, counts=COUNTS)
    for p in sorted(raw_dir.glob("SERL_half_hourly_*.csv")) + [raw_dir / "mock_internal" / READINGS_FILE]:
        rel = p.relative_to(raw_dir)
        assert (tmp_path / rel).read_bytes() == p.read_bytes(), rel
