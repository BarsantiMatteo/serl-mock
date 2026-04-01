# src/serl_mock/profiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class HouseholdProfile:
    """
    Per-household consumption parameters drawn once at initialisation.

    All 'base' values represent a single half-hour period at a unit seasonal
    and unit daily multiplier value (i.e. the mid-season, mid-day baseline).
    Actual generated values are scaled by the seasonal and daily pattern
    multipliers from patterns.py.
    """

    puprn: str
    base_elec_wh: float    # Baseline electricity consumption per HH period (Wh)
    base_gas_wh: float     # Baseline gas consumption at peak heating demand (Wh)
    elec_variance: float   # Noise scale: std = mean * elec_variance
    has_gas: bool          # Whether the property has a gas meter


def generate_profiles(
    puprns: List[str],
    rng: np.random.Generator,
    base_elec_mean_wh: float = 175.0,
    base_elec_std_wh: float = 50.0,
    base_gas_mean_wh: float = 1500.0,
    base_gas_std_wh: float = 300.0,
    gas_fraction: float = 0.85,
) -> Dict[str, HouseholdProfile]:
    """
    Draw one HouseholdProfile per PUPRN using the shared RNG.

    All parameters correspond to keys in the 'profiles' section of
    serl_mock.yaml and can be overridden there.

    Parameters
    ----------
    puprns:
        Ordered list of PUPRN identifiers.
    rng:
        NumPy Generator — shares the global seed so results are reproducible.
    base_elec_mean_wh, base_elec_std_wh:
        Mean and std of the Gaussian from which each household's baseline
        electricity consumption is drawn (Wh per HH period).
    base_gas_mean_wh, base_gas_std_wh:
        Mean and std for baseline gas consumption (Wh per HH period at peak
        heating demand).
    gas_fraction:
        Fraction of households with a gas meter (0–1).

    Returns
    -------
    Dict mapping each PUPRN to its HouseholdProfile.
    """
    n = len(puprns)
    base_elec = np.maximum(50.0,  rng.normal(base_elec_mean_wh, base_elec_std_wh, size=n))
    base_gas  = np.maximum(200.0, rng.normal(base_gas_mean_wh,  base_gas_std_wh,  size=n))
    elec_var  = rng.uniform(0.4, 0.8, size=n)
    has_gas   = rng.random(size=n) < gas_fraction

    return {
        puprn: HouseholdProfile(
            puprn=puprn,
            base_elec_wh=float(base_elec[i]),
            base_gas_wh=float(base_gas[i]),
            elec_variance=float(elec_var[i]),
            has_gas=bool(has_gas[i]),
        )
        for i, puprn in enumerate(puprns)
    }
