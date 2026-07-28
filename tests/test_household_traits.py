"""Unit tests for generate_household_traits() — fast, no full pipeline run.

These exercise the trait-generation logic directly rather than via
generated_output_dir, since it doesn't depend on config file I/O or any
other generator.
"""
from __future__ import annotations

from src.serl_mock.generator_household_traits import generate_household_traits

PUPRNS = [f"HH{i:04d}" for i in range(20)]

EXPECTED_COLUMNS = [
    "PUPRN",
    "has_pv",
    "has_hp",
    "has_ev",
    "has_solar_thermal",
    "has_gas_meter",
    "has_export_meter",
]


def _generate(**overrides):
    kwargs = dict(
        puprns=PUPRNS,
        pv_fraction=0.4,
        hp_fraction=0.2,
        ev_fraction=0.1,
        gas_meter_fraction=0.6,
        export_meter_fraction=0.3,
        solar_thermal_fraction=0.05,
        seed=42,
    )
    kwargs.update(overrides)
    return generate_household_traits(**kwargs)


def test_columns_and_dtypes():
    df = _generate()
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == len(PUPRNS)
    assert set(df["PUPRN"]) == set(PUPRNS)
    for col in EXPECTED_COLUMNS[1:]:
        assert set(df[col].unique()) <= {0, 1}


def test_trait_counts_match_fractions():
    df = _generate()
    assert df["has_pv"].sum() == round(len(PUPRNS) * 0.4)
    assert df["has_hp"].sum() == round(len(PUPRNS) * 0.2)
    assert df["has_ev"].sum() == round(len(PUPRNS) * 0.1)
    assert df["has_gas_meter"].sum() == round(len(PUPRNS) * 0.6)
    assert df["has_export_meter"].sum() == round(len(PUPRNS) * 0.3)
    assert df["has_solar_thermal"].sum() == round(len(PUPRNS) * 0.05)


def test_deterministic_given_same_seed():
    first = _generate(seed=7)
    second = _generate(seed=7)
    assert first.equals(second)


def test_different_seed_can_change_assignment():
    first = _generate(seed=1)
    second = _generate(seed=2)
    # Not a hard guarantee for every column, but with 20 households and a
    # 40% PV fraction it would be a suspicious coincidence for the exact
    # same subset to be chosen under a different seed.
    assert not first["has_pv"].equals(second["has_pv"])
