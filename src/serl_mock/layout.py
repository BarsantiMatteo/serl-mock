# src/serl_mock/layout.py
"""Pluggable strategies for splitting a smart-meter dataset into physical files.

A layout decides how the (year, month) periods covered by a generation run
are grouped into output files — independent of both the data's schema and
its on-disk format. The generator stays layout-agnostic: it asks the active
layout for the month groups, generates and concatenates each group's data,
and writes one file per group.
"""
from __future__ import annotations

from typing import List, Tuple

MonthKey = Tuple[int, int]  # (year, month)


class Layout:
    """Base class — decides how (year, month) periods group into output files."""

    name = "layout"

    def month_groups(self, start_year: int, end_year: int) -> List[List[MonthKey]]:
        raise NotImplementedError

    def filename_stem(self, base_name: str, group: List[MonthKey]) -> str:
        raise NotImplementedError


class MonthlyLayout(Layout):
    """One output file per calendar month — today's behaviour."""

    name = "monthly"

    def month_groups(self, start_year: int, end_year: int) -> List[List[MonthKey]]:
        return [
            [(year, month)]
            for year in range(start_year, end_year + 1)
            for month in range(1, 13)
        ]

    def filename_stem(self, base_name: str, group: List[MonthKey]) -> str:
        (year, month), = group
        return f"{base_name}_{year}_{month:02d}"


class SingleFileLayout(Layout):
    """One combined output file for the whole configured date range.

    Note this holds the entire generated range in memory before writing —
    an inherent cost of choosing "one file", not a limitation of this
    implementation specifically.
    """

    name = "single_file"

    def month_groups(self, start_year: int, end_year: int) -> List[List[MonthKey]]:
        return [[
            (year, month)
            for year in range(start_year, end_year + 1)
            for month in range(1, 13)
        ]]

    def filename_stem(self, base_name: str, group: List[MonthKey]) -> str:
        return base_name


_LAYOUTS = {cls.name: cls() for cls in (MonthlyLayout, SingleFileLayout)}


def get_layout(name: str) -> Layout:
    try:
        return _LAYOUTS[name]
    except KeyError:
        raise ValueError(f"Unknown layout {name!r}; expected one of {sorted(_LAYOUTS)}") from None


# ---------------------------------------------------------------------------
# Daily smart-meter layouts — grouped by year, not (year, month). Kept as a
# separate small hierarchy rather than forcing DailySmartMeterGenerator's
# per-year granularity into the month-based Layout interface above.
# ---------------------------------------------------------------------------

YearKey = int


class DailyLayout:
    """Base class — decides how years group into daily-smart-meter output files."""

    name = "daily_layout"
    # Whether this layout's file(s) belong in their own dataset subfolder
    # (e.g. serl_smart_meter_daily_edition08/) or directly in the run's main
    # output folder. A single combined file doesn't need a folder to itself.
    uses_subfolder = True

    def year_groups(self, start_year: int, end_year: int) -> List[List[YearKey]]:
        raise NotImplementedError

    def filename_stem(self, base_name: str, group: List[YearKey]) -> str:
        raise NotImplementedError


class DailyYearlyLayout(DailyLayout):
    """One output file per calendar year — today's behaviour."""

    name = "yearly"
    uses_subfolder = True

    def year_groups(self, start_year: int, end_year: int) -> List[List[YearKey]]:
        return [[year] for year in range(start_year, end_year + 1)]

    def filename_stem(self, base_name: str, group: List[YearKey]) -> str:
        (year,) = group
        return f"{base_name}_{year}"


class DailySingleFileLayout(DailyLayout):
    """One combined output file for the whole configured date range, written
    directly into the run's main output folder rather than its own subfolder.
    """

    name = "single_file"
    uses_subfolder = False

    def year_groups(self, start_year: int, end_year: int) -> List[List[YearKey]]:
        return [list(range(start_year, end_year + 1))]

    def filename_stem(self, base_name: str, group: List[YearKey]) -> str:
        return base_name


_DAILY_LAYOUTS = {cls.name: cls() for cls in (DailyYearlyLayout, DailySingleFileLayout)}


def get_daily_layout(name: str) -> DailyLayout:
    try:
        return _DAILY_LAYOUTS[name]
    except KeyError:
        raise ValueError(f"Unknown daily layout {name!r}; expected one of {sorted(_DAILY_LAYOUTS)}") from None
