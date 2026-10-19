"""HH smart-meter timestamp conventions (edition08 tiny fixture, year 2021).

SERL stamps each half-hourly reading at the END of the half-hour it measures
(smart-meter documentation: a midnight read "pertains to the previous day").
These tests pin the consequences:

  - a month's file holds stamps from 00:30 on the 1st to 00:00 on the 1st of
    the next month, so the year covers exactly its own local days;
  - HH is the half-hour's position within its effective local day
    (1 = 00:00-00:30, ... 46/48/50), consistent with Read_date_effective_local,
    never repeated on the autumn clock-change day.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.serl_mock.generator_smartmeter import HHSmartMeterGenerator

hh_index = HHSmartMeterGenerator._hh_index_or_na


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.mark.parametrize("stamp, expected", [
    ("2021-01-05 00:30", 1),     # 00:00-00:30 GMT
    ("2021-01-05 12:00", 24),    # 11:30-12:00
    ("2021-01-06 00:00", 48),    # 23:30-00:00, last half-hour of 5 Jan
    ("2021-03-28 01:00", 2),     # spring forward: 00:30-01:00 GMT
    ("2021-03-28 01:30", 3),     # 02:00-02:30 BST (01:00-02:00 local skipped)
    ("2021-03-28 23:00", 46),    # 23:30-00:00 BST, last of a 46-slot day
    ("2021-07-01 00:00", 2),     # 00:30-01:00 BST (stamp is 01:00 BST)
    ("2021-10-31 01:00", 4),     # fall back: 01:30-02:00 BST
    ("2021-10-31 01:30", 5),     # 01:00-01:30 GMT (second 01:00)
    ("2021-11-01 00:00", 50),    # 23:30-00:00 GMT, last of a 50-slot day
    ("2021-01-05 00:31", None),  # off the half-hour grid
])
def test_hh_index(stamp, expected):
    assert hh_index(_utc(stamp)) == expected


@pytest.mark.parametrize("stamp, expected", [
    ("2021-01-05 00:00", "04/01/2021"),   # midnight read -> previous day
    ("2021-01-05 00:30", "05/01/2021"),
    ("2021-03-28 23:00", "28/03/2021"),   # 00:00 BST after the 23-hour spring day
    ("2021-11-01 00:00", "31/10/2021"),   # 00:00 GMT after the 25-hour autumn day
    ("2021-06-01 23:00", "01/06/2021"),   # 00:00 BST
])
def test_effective_date(stamp, expected):
    local = _utc(stamp).tz_convert("Europe/London")
    assert HHSmartMeterGenerator._effective_date_local(local) == expected


def _read_hh(output_dir: Path) -> pd.DataFrame:
    files = sorted((output_dir / "serl_smart_meter_hh_edition08").glob("serl_half_hourly_*.csv"))
    assert files, "no HH files generated"
    parts = []
    for f in files:
        df = pd.read_csv(f, usecols=["PUPRN", "Read_date_time_UTC", "Read_date_effective_local",
                                     "HH", "Valid_read_time"])
        parts_ = f.stem.split("_")  # serl_half_hourly_YYYY_MM_editionNN
        df["file_month"] = parts_[3] + "-" + parts_[4]
        parts.append(df)
    hh = pd.concat(parts, ignore_index=True)
    hh["ts"] = pd.to_datetime(hh["Read_date_time_UTC"], utc=True)
    hh["eff"] = pd.to_datetime(hh["Read_date_effective_local"], format="%d/%m/%Y")
    return hh


@pytest.fixture(scope="module")
def hh(generated_output_dir: Path) -> pd.DataFrame:
    return _read_hh(generated_output_dir)


def test_month_files_hold_their_own_half_hours(hh):
    for month, g in hh.groupby("file_month"):
        start = pd.Timestamp(f"{month}-01", tz="UTC")
        nxt = start + pd.offsets.MonthBegin(1)
        assert g["ts"].min() > start, month               # no 00:00-on-the-1st stamp
        assert g["ts"].max() >= nxt, month                # ends with next month's 00:00 stamp
        assert g["ts"].max() < nxt + pd.Timedelta(minutes=30), month


def test_effective_dates_cover_exactly_the_year(hh):
    assert hh["eff"].min() == pd.Timestamp("2021-01-01")
    assert hh["eff"].max() == pd.Timestamp("2021-12-31")


@pytest.mark.parametrize("day, n", [("2021-03-28", 46), ("2021-06-15", 48), ("2021-10-31", 50)])
def test_hh_positions_per_effective_day(hh, day, n):
    g = hh[hh["eff"] == pd.Timestamp(day)]
    valid = g[g["Valid_read_time"]]
    assert not valid.duplicated(["PUPRN", "HH"]).any()
    assert set(valid["HH"].astype(int)) <= set(range(1, n + 1))
    # every slot is present except those whose stamp was skewed off the grid
    n_skewed = g.loc[~g["Valid_read_time"], "ts"].nunique()
    assert valid["HH"].nunique() + n_skewed == n
