# src/serl_mock/patterns.py
"""
Time-based multiplier functions for realistic smart-meter data generation.

Each function takes a NumPy array of time values and returns an array of
multiplicative scaling factors.  The generator applies:

    value ~ Normal( base_household * seasonal(doy) * daily(hour), noise )

To swap in improved rules in the future, replace or extend any function here
(or add new ones) and update the generator to call them.  The signatures
(array-in, array-out) are the only contract.

Reference patterns taken from:
    scripts/generate_dummy_data.py  — generate_module1_data()
"""
from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Electricity
# ---------------------------------------------------------------------------

def elec_seasonal_mult(day_of_year: np.ndarray, amplitude: float = 0.3) -> np.ndarray:
    """
    Electricity seasonal multiplier (vectorised).

    Higher consumption in winter due to lighting and supplemental electric
    heating.  Uses a cosine so peak is at ~15 January (day 15) and trough
    at ~mid-July.

    Multiplier range: [1 - amplitude, 1 + amplitude]

    Parameters
    ----------
    day_of_year : array of float, 1–366
    amplitude   : seasonal swing (default 0.3 → ±30 % around the annual mean)
    """
    return 1.0 + amplitude * np.cos(2.0 * np.pi * (day_of_year - 15.0) / 365.0)


def elec_daily_mult(hour_float: np.ndarray) -> np.ndarray:
    """
    Electricity intraday multiplier (vectorised).

    Typical UK weekday demand profile:

      Overnight  00–07 : 0.5×   (minimal baseload)
      Morning    07–09 : 1.4×   (breakfast, lights)
      Daytime    09–17 : 0.9×   (many occupants away)
      Evening    17–21 : 1.6×   (return home, cooking, TV)
      Late eve   21–23 : 1.2×   (wind-down)

    Parameters
    ----------
    hour_float : array of float, 0–24  (local time, UTC+BST/GMT)
    """
    h = hour_float
    return np.select(
        [
            (h >= 7)  & (h < 9),
            (h >= 9)  & (h < 17),
            (h >= 17) & (h < 21),
            (h >= 21) & (h < 23),
        ],
        [1.4, 0.9, 1.6, 1.2],
        default=0.5,
    )


# ---------------------------------------------------------------------------
# Gas
# ---------------------------------------------------------------------------

def gas_seasonal_mult(day_of_year: np.ndarray, amplitude: float = 2.0) -> np.ndarray:
    """
    Gas space-heating seasonal demand multiplier (vectorised).

    Clipped to zero in summer so gas demand represents heating only.
    During the heating season the multiplier rises from 0 (spring/autumn
    threshold) to ~amplitude at mid-winter peak.

    Parameters
    ----------
    day_of_year : array of float, 1–366
    amplitude   : winter peak multiplier above zero (default 2.0)
    """
    raw = np.cos(2.0 * np.pi * (day_of_year - 15.0) / 365.0)
    return np.maximum(0.0, raw) * amplitude


def gas_daily_mult(hour_float: np.ndarray) -> np.ndarray:
    """
    Gas intraday multiplier during the heating season (vectorised).

    Represents a typical two-period heating schedule with thermostat setback:

      Overnight  22–06 : 0.8×   (setback thermostat)
      Morning    06–09 : 1.5×   (morning ramp-up)
      Daytime    09–17 : 0.6×   (occupants away / mild demand)
      Evening    17–22 : 1.3×   (return home)

    Parameters
    ----------
    hour_float : array of float, 0–24  (local time)
    """
    h = hour_float
    return np.select(
        [
            (h >= 6)  & (h < 9),
            (h >= 9)  & (h < 17),
            (h >= 17) & (h < 22),
        ],
        [1.5, 0.6, 1.3],
        default=0.8,
    )
