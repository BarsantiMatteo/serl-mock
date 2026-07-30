# src/serl_mock/edition.py
"""
Edition definitions — the single source of truth for which parameters are
bound to a SERL edition versus free to vary per scenario.

Config only ever names *which* edition to use (`edition: "09"` in
serl_mock.yaml). The actual output format, smart-meter file layout, and
reference-dictionary source are resolved from the Edition registry below,
not read as independent config keys — so there is no way for a config file
to accidentally combine an edition with a format/layout it doesn't use.
(n_households, seed, date range, household traits, profiles, and patterns
remain freely configurable regardless of edition.)

Adding a new edition means adding an entry to _EDITIONS below — a
deliberate, reviewable code change, not a config-file typo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Edition:
    number: str                    # zero-padded edition number, e.g. "08"
    format: str                    # "csv" or "parquet" — see utils.write_table
    hh_smart_meter_layout: str     # "monthly" or "single_file" — see layout.py
    daily_smart_meter_layout: str  # "yearly" or "single_file" — see layout.py
    dictionary_source_edition: str  # which edition's dictionary filenames to read;
                                     # equal to `number` unless this edition still
                                     # borrows an earlier edition's dictionaries
    smart_meter_schema: str = "legacy"  # "legacy" or "harmonised" — see the
                                         # _SCHEMA_COLUMNS mapping and the
                                         # *_harmonised_* methods in
                                         # generator_smartmeter.py for what each
                                         # variant actually produces


_EDITIONS: Dict[str, Edition] = {
    "08": Edition(
        number="08",
        format="csv",
        hh_smart_meter_layout="monthly",
        daily_smart_meter_layout="yearly",
        dictionary_source_edition="07",  # still uses the real edition07 SERL
                                          # dictionaries; see docs/05_epc_reference.md
        smart_meter_schema="legacy",
    ),
    "09": Edition(
        number="09",
        format="parquet",
        hh_smart_meter_layout="monthly",  # unconfirmed — update once the real
                                           # edition09 layout spec is known
        daily_smart_meter_layout="single_file",  # one combined file at the top
                                                  # level of the run's output,
                                                  # not in a per-year subfolder
        dictionary_source_edition="09",
        smart_meter_schema="harmonised",  # real edition09 HH/daily column
                                           # layout — see docs/04_metadata.md
    ),
}

DEFAULT_EDITION = "08"


def get_edition(number: str) -> Edition:
    """Look up the Edition definition for a given edition number.

    Raises ValueError for an edition with no definition here, rather than
    silently falling back to some default — an undefined edition has no
    correct format/layout/dictionary-source to assume.
    """
    number = str(number).zfill(2)
    try:
        return _EDITIONS[number]
    except KeyError:
        raise ValueError(
            f"Unknown edition {number!r} — no Edition definition exists in "
            f"src/serl_mock/edition.py. Known editions: {sorted(_EDITIONS)}."
        ) from None
