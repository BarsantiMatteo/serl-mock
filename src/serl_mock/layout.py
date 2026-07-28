# src/serl_mock/layout.py
"""Pluggable strategies for splitting a smart-meter dataset into physical files.

A layout decides how the (year, month) periods covered by a generation run
are grouped into output files — independent of both the data's schema and
its on-disk format (see docs/notes/edition_multiformat_plan.md). The
generator stays layout-agnostic: it asks the active layout for the month
groups, generates and concatenates each group's data, and writes one file
per group.
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
