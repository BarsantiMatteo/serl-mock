# src/serl_mock/generator_contextual_data.py
from __future__ import annotations
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

from .ids import (
    make_alphanumeric_ids_ordered,
    load_puprn_list_csv,
)
from .generator_household_traits import load_household_traits
from .utils import (
    read_config, seed_random, ensure_output_dir,
    with_edition_suffix, write_csv, read_survey_dictionary
)

@dataclass
class OutputNames:
    epc: str = "serl_epc_data"
    survey: str = "serl_survey_data"
    covid19_survey: str = "serl_covid19_survey_data"
    summary: str = "serl_participant_summary"
    followup_survey: str = "serl_2023_follow_up_survey_data"
    exporters_prefix: str = "Elec"

class SERLContextualVariablesGenerator:
    """
    Generates contextual datasets:
      - EPC data
      - SERL survey data
      - Participant summary
      - Follow-up survey
      - List of exporter PUPRNs
    Uses shared config and PUPRN utilities to align with smart-meter HH data.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        n_households: Optional[int] = None,
        year: Optional[int] = None,
        outfolder: Optional[str] = None,
        seed: Optional[int] = None,
        puprn_list_path: Optional[str] = None,
        write_puprn_list: bool = False,
    ):
        cfg = read_config(config_path)

        # Core parameters
        self.n_households = n_households or cfg.get("n_households", 100)
        self.year = year or cfg.get("year", 2023)

        # RNG
        self.seed = seed if seed is not None else cfg.get("seed", 42)
        seed_random(self.seed)

        # Edition & filenames
        fcfg = cfg.get("filenames", {})
        self.names = OutputNames(
            epc=fcfg.get("epc", "serl_epc_data"),
            survey=fcfg.get("survey", "serl_survey_data"),
            covid19_survey=fcfg.get("covid19_survey", "serl_covid19_survey_data"),
            summary=fcfg.get("summary", "serl_participant_summary"),
            followup_survey=fcfg.get("followup_survey", "serl_2023_follow_up_survey_data"),
            exporters_prefix=fcfg.get("exporters_prefix", "Elec"),
        )
        self.edition = str(cfg.get("edition", "")).strip() or None

        # Household traits (PV/HP/EV) — load from pre-generated CSV
        traits_path = cfg.get("household_traits_path")
        if not traits_path:
            # Try default location in mock_internal
            from .paths import MOCK_INTERNAL_DIR
            traits_path = MOCK_INTERNAL_DIR / "household_traits.csv"
        self.traits_path = str(traits_path)

        # Household-trait fractions are used during household traits generation,
        # not directly in this contextual generator.

        # Survey dictionary paths
        self.survey_dictionary_path = cfg.get(
            "survey_dictionary_path",
            "data/reference/serl_survey_data_dictionary_edition07.csv",
        )
        self.followup_survey_dictionary_path = cfg.get(
            "followup_survey_dictionary_path",
            "data/reference/serl_follow_up_survey_data_dictionary_edition07.csv",
        )
        self.covid19_survey_dictionary_path = cfg.get(
            "covid19_survey_dictionary_path",
            "data/reference/serl_covid19_survey_data_dictionary_edition07.csv",
        )

        # PUPRN: load from master list or generate deterministically
        self.puprn_list_path = puprn_list_path or cfg.get("puprn_list_path")

        if self.puprn_list_path:
            puprns = load_puprn_list_csv(self.puprn_list_path)
            if len(puprns) < self.n_households:
                raise ValueError("PUPRN list smaller than n_households.")
            self.puprns = puprns[: self.n_households]
        else:
            self.puprns = make_alphanumeric_ids_ordered(self.n_households, length=8, seed=self.seed)

        # Load household traits from CSV
        traits_df = load_household_traits(self.traits_path)
        self._pv_households = set(traits_df[traits_df['has_pv'] == 1].index.tolist())
        self._hp_households = set(traits_df[traits_df['has_hp'] == 1].index.tolist())
        self._ev_households = set(traits_df[traits_df['has_ev'] == 1].index.tolist())

        # ERA5 grid-cell assignment — used in participant summary.
        # Each PUPRN gets a random location within the weather bounding box,
        # snapped to the nearest grid point.  This uses the same area/grid
        # settings as WeatherDownloader, so every assigned cell is guaranteed
        # to exist in the downloaded ERA5 files.
        wcfg = cfg.get("weather", {})
        area = wcfg.get("area", [60.0, -8.0, 49.0, 2.0])
        grid = wcfg.get("grid", [0.25, 0.25])
        self._grid_cells: Dict[str, str] = self._assign_grid_cells(
            puprns=list(self.puprns),
            area=area,
            grid=grid,
            seed=self.seed,
        )

    # ---------- Grid-cell assignment ----------
    @staticmethod
    def _assign_grid_cells(
        puprns: List[str],
        area: List[float],
        grid: List[float],
        seed: int,
    ) -> Dict[str, str]:
        """Randomly assign each PUPRN to the nearest ERA5 grid cell.

        Locations are drawn uniformly within *area* and snapped to the grid
        defined by *grid*.  The resulting ``grid_cell`` identifier uses the
        format ``<col>_<row>`` (0-based, origin at the NW corner of *area*),
        consistent with the SERL climate data schema.
        """
        north, west, south, east = (
            float(area[0]), float(area[1]), float(area[2]), float(area[3])
        )
        lat_step, lon_step = float(grid[0]), float(grid[1])
        max_row = math.floor((north - south) / lat_step)
        max_col = math.floor((east - west) / lon_step)

        rng = np.random.default_rng(seed)
        raw_lats = rng.uniform(south, north, size=len(puprns))
        raw_lons = rng.uniform(west, east, size=len(puprns))

        result: Dict[str, str] = {}
        for puprn, raw_lat, raw_lon in zip(puprns, raw_lats, raw_lons):
            row = max(0, min(round((north - float(raw_lat)) / lat_step), max_row))
            col = max(0, min(round((float(raw_lon) - west) / lon_step), max_col))
            result[puprn] = f"{col}_{row}"
        return result

    # ---------- File naming ----------
    def _fname(self, basename: str) -> str:
        return with_edition_suffix(basename, self.edition)

    # ---------- EPC ----------
    @staticmethod
    def _epc_fields() -> List[str]:
        return [
            'PUPRN', 'builtForm', 'co2EmissCurrPerFloorArea', 'co2EmissionsCurrent',
            'co2EmissionsPotential', 'constituency', 'constructionAgeBand',
            'currentEnergyEfficiency', 'currentEnergyRating', 'energyConsumptionCurrent',
            'energyConsumptionPotential', 'energyTariff', 'environmentImpactCurrent',
            'environmentImpactPotential', 'extensionCount', 'fixedLightingOutletsCount',
            'flatStoreyCount', 'flatTopStorey', 'floorDescription', 'floorEnergyEff',
            'floorEnvEff', 'floorHeight', 'floorLevel', 'glazedArea', 'glazedType',
            'heatingCostCurrent', 'heatingCostPotential', 'heatLossCorridor',
            'hotWaterCostCurrent', 'hotWaterCostPotential', 'hotwaterDescription',
            'hotWaterEnergyEff', 'hotWaterEnvEff', 'inspectionDate', 'lightingCostCurrent',
            'lightingCostPotential', 'lightingDescription', 'lightingEnergyEff',
            'lightingEnvEff', 'localAuthority', 'lodgementDate', 'lodgementDatetime',
            'lowEnergyFixedLightCount', 'lowEnergyLighting', 'mainFuel',
            'mainheatcEnergyEff', 'mainheatcEnvEff', 'mainheatcontDescription',
            'mainheatDescription', 'mainheatEnergyEff', 'mainheatEnvEff',
            'mainHeatingControls', 'mainsGasFlag', 'mechanicalVentilation',
            'multiGlazeProportion', 'numberHabitableRooms', 'numberHeatedRooms',
            'numberOpenFireplaces', 'photoSupply', 'potentialEnergyEfficiency',
            'potentialEnergyRating', 'propertyType', 'roofDescription', 'roofEnergyEff',
            'roofEnvEff', 'secondheatDescription', 'sheatingEnergyEff', 'sheatingEnvEff',
            'solarWaterHeatingFlag', 'tenure', 'totalFloorArea', 'transactionType',
            'unheatedCorridorLength', 'wallsDescription', 'wallsEnergyEff', 'wallsEnvEff',
            'windowsDescription', 'windowsEnergyEff', 'windowsEnvEff', 'windTurbineCount',
            'epcVersion',
        ]

    def generate_epc(self) -> pd.DataFrame:
        fields = self._epc_fields()
        energy_ratings = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        property_types = ['House', 'Flat', 'Maisonette', 'Bungalow']
        built_forms = ['Detached', 'Semi-Detached', 'Mid-Terrace', 'End-Terrace',
                       'Enclosed Mid-Terrace', 'Enclosed End-Terrace']
        efficiency_ratings = ['Very Good', 'Good', 'Average', 'Poor', 'Very Poor']
        yes_no_flags = ['Y', 'N']
        fuel_types = ['mains gas (not community)', 'electricity', 'oil', 'solid fuel']
        tenure_types = ['owner-occupied', 'rented (private)', 'rented (social)']
        mech_vent_types = ['natural', 'mechanical, extract only',
                           'mechanical, supply and extract', 'NO DATA!']
        heat_loss_types = ['no corridor', 'heated corridor', 'unheated corridor', 'NO DATA!']
        age_bands = ['before 1900', '1900-1929', '1930-1949', '1950-1966',
                     '1967-1975', '1976-1982', '1983-1990', '1991-1995',
                     '1996-2002', '2003-2006', '2007-2011', '2012 onwards']
        transaction_types = ['marketed sale', 'rental', 'new dwelling',
                             'following green deal', 'assessment for green deal']

        data = []
        rnd = random.Random(self.seed + 100)

        for puprn in self.puprns:
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in fields[1:]:
                fl = field.lower()
                if 'rating' in fl and 'efficiency' not in fl:
                    row[field] = np.nan if rnd.random() < 0.3 else rnd.choice(energy_ratings)
                elif 'efficiency' in fl:
                    row[field] = rnd.randint(20, 100)
                elif field == 'propertyType':
                    row[field] = rnd.choice(property_types)
                elif field == 'builtForm':
                    row[field] = rnd.choice(built_forms)
                elif field == 'epcVersion':
                    row[field] = 'England and Wales'
                elif field == 'mechanicalVentilation':
                    row[field] = rnd.choice(mech_vent_types)
                elif field == 'heatLossCorridor':
                    row[field] = rnd.choice(heat_loss_types)
                elif field == 'constructionAgeBand':
                    row[field] = rnd.choice(age_bands)
                elif field == 'transactionType':
                    row[field] = rnd.choice(transaction_types)
                elif field == 'mainFuel':
                    row[field] = rnd.choice(fuel_types)
                elif field == 'tenure':
                    row[field] = rnd.choice(tenure_types)
                elif field == 'totalFloorArea':
                    row[field] = np.nan if rnd.random() < 0.3 else round(rnd.uniform(30.0, 300.0), 1)
                elif field == 'floorHeight':
                    row[field] = round(rnd.uniform(2.0, 3.5), 2)
                elif field == 'mainHeatingControls':
                    row[field] = rnd.randint(1000, 9999)
                elif field == 'lowEnergyLighting':
                    row[field] = rnd.randint(0, 100)
                elif field == 'flatTopStorey':
                    row[field] = rnd.choice(yes_no_flags)
                elif field == 'photoSupply':
                    pct = rnd.randint(0, 50)
                    row[field] = f"Array: Roof Area: {pct}%; Connection: not applicable (FGHRS or no PV); |"
                elif 'datetime' in fl:
                    base_dt = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_dt + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y %H:%M')
                elif 'date' in fl:
                    base_date = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_date + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y')
                elif 'emiss' in fl:
                    row[field] = round(rnd.uniform(0.5, 10.0), 1)
                elif 'consumption' in fl:
                    row[field] = rnd.randint(50, 500)
                elif 'environmentimpact' in fl:
                    row[field] = rnd.randint(20, 100)
                elif 'cost' in fl:
                    row[field] = rnd.randint(50, 2000)
                elif 'flag' in fl:
                    row[field] = rnd.choice(yes_no_flags)
                elif 'energyeff' in fl or 'enveff' in fl:
                    row[field] = rnd.choice(efficiency_ratings)
                elif 'count' in fl or 'number' in fl:
                    row[field] = rnd.randint(0, 10)
                elif 'proportion' in fl:
                    row[field] = rnd.randint(0, 100)
                elif 'unheated' in fl:
                    row[field] = round(rnd.uniform(0.0, 10.0), 1)
                elif 'description' in fl:
                    row[field] = f"Sample {field} description"
                else:
                    row[field] = f"Sample_{field}"
            data.append(row)

        return pd.DataFrame(data)

    # ---------- SERL survey ----------
    def generate_serl_survey(self) -> pd.DataFrame:
        survey_fields = read_survey_dictionary(self.survey_dictionary_path)
        survey_versions = ['Wave1', 'Wave2', 'Wave3']
        collection_methods = ['Online', 'Postal']
        languages = ['English', 'Welsh', 'Unknown']
        binary_responses = [0, 1]
        multi_choice_responses = [1, 2, 3, 4, 5]
        missing_codes = [-1, -2, -9]
        # Per-field valid missing codes, derived from serl_survey_data_dictionary.
        # Fields not listed here fall back to missing_codes.
        _field_missing: Dict[str, List] = {
            'A1':    [-2], 'A6': [-2], 'A7': [-9, -2], 'A10': [-2], 'A11': [-2],
            'A14':   [-2, -1], 'A1501': [-2, -1], 'A1502': [-2, -1],
            'A401':  [-9], 'A402': [-9], 'A403': [-9],
            'A404':  [-9], 'A405': [-9], 'A406': [-9],
            'B2':    [-2], 'B3': [-9, -2], 'B6': [-2, -4], 'B7': [-2, -1], 'B8': [-2, -1],
            'C1':    [-2],
            'C301':  [-2], 'C302': [-2], 'C303': [-2],
            'C304':  [-2], 'C305': [-2], 'C306': [-2], 'C307': [-2],
            'C4':    [-2, -1],
            'D1':    [-9, -2], 'D2': [-9, -3, -2], 'D3': [-9, -3, -2],
        }

        data = []
        rnd = random.Random(self.seed + 200)

        for puprn in self.puprns:
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in survey_fields[1:]:
                if field == 'Survey_version':
                    row[field] = rnd.choice(survey_versions)
                elif field == 'Recorded_date':
                    base = pd.Timestamp(2021, 1, 1)
                    row[field] = (base + pd.Timedelta(days=rnd.randint(0, 730))).strftime('%Y-%m-%d')
                elif field == 'Collection_method':
                    row[field] = rnd.choice(collection_methods)
                elif field == 'Language':
                    row[field] = rnd.choice(languages)
                elif field == 'B1':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5, 6])
                elif field == 'B4':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5])
                elif field == 'B5':
                    row[field] = rnd.choice([2, 3, 4])
                elif field == 'B5_err':
                    row[field] = 0
                elif field == 'B9':
                    row[field] = rnd.choice([-2, -1]) if rnd.random() < 0.1 else rnd.randint(1, 7)
                elif field == 'C1_new':
                    row[field] = rnd.choice([1, 2, 3])
                elif field == 'C5':
                    row[field] = rnd.randint(-2, 2)
                elif field == 'D4':
                    row[field] = rnd.choice([-3, -2, -1]) if rnd.random() < 0.15 else rnd.randint(1, 5)
                elif field.startswith('A3'):
                    if not field.endswith('_err'):
                        row[field] = rnd.choice([0, 1])
                    else:
                        row[field] = True if rnd.random() < 0.1 else False
                elif '_text' in field.lower() or '_other' in field.lower():
                    row[field] = f"Sample text for {field}"
                elif field.endswith('_sum') or field.endswith('_diff'):
                    row[field] = rnd.randint(0, 10)
                elif field.endswith('_err') or field.endswith('_edit'):
                    row[field] = rnd.choice([True, False])
                elif field.startswith('A') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.1 else rnd.choice(multi_choice_responses)
                elif field.startswith('B') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.choice(multi_choice_responses)
                elif field.startswith('C') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.randint(0, 15)
                elif field.startswith('D') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.15 else rnd.choice(multi_choice_responses)
                elif field.endswith('01') or field.endswith('02') or field.endswith('03'):
                    row[field] = rnd.choice(binary_responses)
                else:
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.08 else rnd.choice(multi_choice_responses)

            # Device-aware overrides from config fractions.
            # These are applied after generic random generation to keep consistency.
            if 'A1607' in row:
                row['A1607'] = 1 if puprn in self._hp_households else 0
            if 'C5' in row:
                row['C5'] = 1 if puprn in self._ev_households else 2
            if 'C6' in row:
                row['C6'] = rnd.choice([1, 2, 3, 4]) if puprn in self._ev_households else -9
            data.append(row)

        return pd.DataFrame(data)

    # ---------- COVID-19 survey ----------
    def generate_covid19_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.covid19_survey_dictionary_path)

        missing_codes = [-1, -2, -9]

        # Fields with a direct option-set lookup.
        # Branching/routing fields (Q1→Q2, Q3→Q3a, Q11a→Q11b, Q12a→Q12b) are
        # handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'Q1':  [1, 2, 3, 4, 5, 6, 7],
            'Q5':  [1, 2, 3, 4, 5],
            'Q7':  [1, 2, 3],
            'Q8b': [1, 2, 3, 4],
            'Q9':  [1, 2, 4],          # 3 not a valid code
            'Q10': [1, 2, 3, 4],
            'Q17': [1, 2, 3, 4],
            'Q18': [1, 4, 5, 6],       # 2 and 3 not valid codes
            'Q19': [1, 2, 3, 4, 5, 6, 7],
            'Q21': [1, 2, 3, 4],
            'Q24': list(range(1, 13)),
            'Q25': list(range(1, 13)),
        }

        rnd = random.Random(self.seed + 600)

        def mm(choices, p: float = 0.05):
            return rnd.choice(missing_codes) if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            _q1 = None
            _q3 = None

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── Q1 (state) / Q2 routed from Q1 ───────────────────────
                elif field == 'Q1':
                    v = mm([1, 2, 3, 4, 5, 6, 7])
                    row[field] = v; _q1 = v
                elif field == 'Q2':
                    # Q1=6 means "I don't have this" → Q2 code 4 = same
                    row[field] = 4 if _q1 == 6 else mm([1, 2, 3, 4, 5])

                # ── Q3 (state) / Q3a only applicable if Q3=1 ─────────────
                elif field == 'Q3':
                    v = rnd.choice([1, 2])
                    row[field] = v; _q3 = v
                elif field.startswith('Q3a_'):
                    row[field] = rnd.choice([0, 1]) if _q3 == 1 else np.nan

                # ── Q4: outdoor-access checkboxes (always answered) ───────
                elif field.startswith('Q4_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q6: window-opening frequency (non-sequential codes) ───
                elif field.startswith('Q6_'):
                    row[field] = mm([7, 14, 15, 16, 17, 18])

                # ── Q11a: "don't have appliance" flags ────────────────────
                elif field.startswith('Q11a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q11b: appliance use vs pre-lockdown ───────────────────
                # Skip (NaN) when the household indicated they don't have the
                # appliance via the corresponding Q11a_2_N_1 flag.
                elif field.startswith('Q11b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q11a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q12a: "don't have device" flags ───────────────────────
                elif field.startswith('Q12a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q12b: device use vs pre-lockdown ──────────────────────
                elif field.startswith('Q12b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q12a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q22a / Q22b: time-of-day checkboxes (0/1) ────────────
                elif field.startswith('Q22a_') or field.startswith('Q22b_'):
                    row[field] = rnd.choice([0, 1])

                # ── RC1/RC2: research consent; Finished: always answered ──
                elif field in ('RC1', 'RC2'):
                    row[field] = rnd.choice([1, 2])
                elif field == 'Finished':
                    row[field] = rnd.choice([0, 1])

                # ── direct option lookup (with small missing probability) ─
                elif field in _field_opts:
                    row[field] = mm(_field_opts[field])

                else:
                    row[field] = np.nan

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Participant summary ----------
    def generate_participant_summary(self) -> pd.DataFrame:
        fields = ['PUPRN', 'Region', 'LSOA', 'grid_cell', 'IMD_quintile']
        regions = [
            'NORTH EAST','NORTH WEST','YORKSHIRE AND THE HUMBER','EAST MIDLANDS',
            'WEST MIDLANDS','EAST OF ENGLAND','LONDON','SOUTH EAST','SOUTH WEST',
            'SCOTLAND','WALES'
        ]
        # LSOA prefix by nation; all other regions are in England (E01)
        _lsoa_prefix = {'WALES': 'W01', 'SCOTLAND': 'S01'}
        data = []
        rnd = random.Random(self.seed + 300)
        for puprn in self.puprns:
            region = rnd.choice(regions)
            prefix = _lsoa_prefix.get(region, 'E01')
            lsoa = f"{prefix}{rnd.randint(0, 999999):06d}"
            data.append({
                'PUPRN': puprn,
                'Region': region,
                'LSOA': lsoa,
                'grid_cell': self._grid_cells.get(puprn, ''),
                'IMD_quintile': rnd.randint(1, 5),
            })
        return pd.DataFrame(data, columns=fields)

    # ---------- Follow-up survey ----------
    def generate_follow_up_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.followup_survey_dictionary_path)

        # ── response-option lists (full text, as in actual SERL data) ─────
        YES_NO_NR  = ['Yes', 'No', 'No response']
        FREQ_6     = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable, cannot do this']
        CHANGE_6   = ['A lot more', 'A little more', 'About the same',
                      'A little less', 'A lot less', 'Not applicable, cannot do this']
        FREQ_UNOCC = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable']
        A9_OPTS    = [
            'Yes, some or all have their own source of fuel (e.g., logs, coal, bottled gas etc.)',
            'No, they are all powered by mains gas or electricity',
            'No response',
        ]
        A10_OPTS   = ['Daily', 'Most days', 'Rarely - only if I/we really have to',
                      'Never', 'Varies - depends on temperature or other reasons', "Don't know"]
        A11_OPTS   = ['More often', 'Less often', 'About the same',
                      "I don't have this", 'It is not working', "Don't know"]
        A12_OPTS   = ['A great deal of effort', 'Some effort', 'A little effort',
                      'No effort at all', "Don't know"]
        BATHROOMS  = ['0', '1', '2', '3', '4 or more']
        ADD_REP    = ['Has been added or replaced in the last 12 months', 'No']
        PEOPLE     = ['0 people', '1 person', '2 people', '3 people', '4 or more people']
        INCOMES    = [
            'Below £10,000', '£10,001 to £20,000', '£20,001 to £30,000',
            '£30,001 to £40,000', '£40,001 to £50,000', '£50,001 to £60,000',
            '£60,001 to £70,000', '£70,001 to £80,000', '£80,001 to £90,000',
            '£90,0001 to £100,000', 'Above £100,000', 'Prefer not to answer',
        ]
        PAYMENT    = [
            'Direct debit (including online direct debit)',
            'Payment on receipt of bill (by post, telephone, online or at bank/post office)',
            'Pre-payment meter', 'Included in rent', 'Other', "Don't know",
        ]
        C5_OPTS    = ['Very easy', 'Fairly easy', 'Neither easy nor difficult',
                      'Fairly difficult', 'Very difficult', "Don't know"]
        C6_OPTS    = ['Daily', 'Most days', 'Rarely', 'never', "Don't know", 'Prefer not to say']
        WFH        = ['Always work from home', 'Sometimes work from home',
                      'Never work from home', 'Not applicable /prefer not to say']
        E1_OPTS    = ['Living comfortably', 'Doing alright', 'Just about getting by',
                      'Finding it quite difficult', 'Finding it very difficult',
                      "Don't know", 'Prefer not to say']
        MOULD      = ['Minor', 'Substantial', "Don't know"]

        # Fields with a direct option-set lookup (not derivable from name pattern alone).
        # Branching/stateful fields (A8/A9/A10, B5/B6, C3/C4, D1/D2/D3, D5/D6, E2/E3)
        # are handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'A2': YES_NO_NR, 'A3': YES_NO_NR, 'A4': YES_NO_NR,
            'A7': FREQ_UNOCC, 'A11': A11_OPTS, 'A12': A12_OPTS,
            'B1': BATHROOMS,
            'C1': INCOMES, 'C2_electricity': PAYMENT,
            'C5': C5_OPTS, 'C6': C6_OPTS,
            'D4': WFH,
            'E1': E1_OPTS,
            'Collection_method': ['Online', 'Postal'],
        }

        # B2/B3/B4 sub-field probabilities for *_yes generation
        _B2_PROBS = {1:0.03, 2:0.70, 3:0.05, 4:0.05, 5:0.01, 6:0.02,
                     7:0.03, 8:0.01, 9:0.01, 10:0.01, 11:0.01}
        _B3_PROBS = {2:0.05, 5:0.80, 6:0.70, 7:0.60, 8:0.15, 9:0.03,
                     10:0.80, 11:0.70, 12:0.30, 13:0.40, 14:0.30, 15:0.35, 16:0.05}
        _B4_PROBS = {1:0.70, 2:0.50, 3:0.15, 4:0.20, 5:0.85, 6:0.40, 7:0.05}

        _C4_ANSWERS = {
            'C4_1': 'You feel your home is difficult to heat',
            'C4_2': 'You feel it is difficult to afford the fuel to heat your home',
            'C4_3': 'Prefer not to say',
            'C4_4': 'None of the above',
            'C4_5': 'Other reason',
        }

        _D2_DERIVED = {'D2_ignored', 'D2_min_total'}
        _pcount     = {'0 people': 0, '1 person': 1, '2 people': 2,
                       '3 people': 3, '4 or more people': 4}

        rnd = random.Random(self.seed + 400)

        def nr(choices, p: float = 0.08):
            return np.nan if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            has_pv = puprn in self._pv_households
            has_hp = puprn in self._hp_households
            has_ev = puprn in self._ev_households

            # State tracked across fields for branching and derived computations
            _a8 = _a9 = _b5 = _c3 = _c4_chosen = None
            _d1 = None
            _d2_answered = _d3_answered = None
            _d2_vals: dict = {}
            _d3_vals: dict = {}

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── A1: temperature (internally consistent group) ──────────
                elif field == 'A1_units':
                    v = nr(['Celsius', 'Fahrenheit', "Don't know/can't do this"], p=0.12)
                    row[field] = v
                elif field == 'A1_degC':
                    row[field] = (rnd.randint(15, 25)
                                  if row.get('A1_units') == 'Celsius' else np.nan)
                elif field == 'A1_degF':
                    row[field] = (rnd.randint(59, 77)
                                  if row.get('A1_units') == 'Fahrenheit' else np.nan)
                elif field == 'A1_corr_C':
                    u = row.get('A1_units')
                    if u == 'Celsius':    row[field] = float(row['A1_degC'])
                    elif u == 'Fahrenheit': row[field] = round((row['A1_degF'] - 32) * 5/9, 1)
                    else:                  row[field] = np.nan
                elif field == 'A1_edit':
                    row[field] = (row.get('A1_units') == 'Fahrenheit')
                elif field == 'A1_err':
                    row[field] = (row.get('A1_units') not in ('Celsius', 'Fahrenheit')
                                  and rnd.random() < 0.05)

                # ── A5 frequency / A6 change ──────────────────────────────
                elif field.startswith('A5_'):
                    row[field] = nr(FREQ_6)
                elif field.startswith('A6_'):
                    row[field] = nr(CHANGE_6)

                # ── A8/A9/A10: branching on standalone heaters ────────────
                elif field == 'A8':
                    v = nr(YES_NO_NR); row[field] = v; _a8 = v
                elif field == 'A9':
                    if _a8 == 'No':
                        row[field] = 'NA'
                    elif _a8 == 'Yes':
                        v = nr(A9_OPTS); row[field] = v; _a9 = v
                    else:
                        row[field] = 'No response'
                elif field == 'A10':
                    if _a8 in (None, 'No'):
                        row[field] = 'NA'
                    elif _a9 == 'No, they are all powered by mains gas or electricity':
                        row[field] = 'NA'
                    elif _a9 and _a9 != 'No response':
                        row[field] = nr(A10_OPTS)
                    else:
                        row[field] = 'No response'

                # ── B2_N: heating types (HP trait-aligned) ────────────────
                elif field.startswith('B2_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 5 and has_hp:    row[field] = 'Yes'
                    elif n == 2 and has_hp:  row[field] = 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B2_PROBS.get(n, 0.02) else 'No'
                elif field.startswith('B2_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B2_{n}_yes') == 'Yes' else 'No')
                elif field.startswith('B2_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B3_N: technologies (PV/EV trait-aligned) ──────────────
                elif field.startswith('B3_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 1:    row[field] = 'Yes' if has_pv else 'No'
                    elif n == 3:  row[field] = 'Yes' if (has_pv and rnd.random() < 0.30) else 'No'
                    elif n == 4:  row[field] = 'Yes' if has_ev else 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B3_PROBS.get(n, 0.10) else 'No'
                elif field.startswith('B3_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B3_{n}_yes') == 'Yes' else 'No')
                elif field.startswith('B3_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B4_N: insulation ──────────────────────────────────────
                elif field.startswith('B4_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    row[field] = 'Yes' if rnd.random() < _B4_PROBS.get(n, 0.20) else 'No'
                elif field.startswith('B4_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B4_{n}_yes') == 'Yes' else 'No')
                elif field.startswith('B4_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B5 (state) / B6 (branching) ───────────────────────────
                elif field == 'B5':
                    v = nr(['Yes', 'No', "Don't know"]); row[field] = v; _b5 = v
                elif field.startswith('B6_'):
                    if _b5 == 'Yes':                     row[field] = rnd.choice(MOULD + ['No'])
                    elif _b5 in ('No', "Don't know"):    row[field] = 'NA'
                    else:                                row[field] = 'No response'

                # ── C2_gas: not applicable without a gas supply ───────────
                elif field == 'C2_gas':
                    has_gas = (row.get('B2_2_yes') == 'Yes' or row.get('B2_9_yes') == 'Yes')
                    row[field] = nr(PAYMENT) if has_gas else 'Not applicable / no mains gas'

                # ── C3 (state) / C4 (branching) ───────────────────────────
                elif field == 'C3':
                    v = nr(['Yes', 'No', "Don't know", 'No response'])
                    row[field] = v; _c3 = v
                elif field.startswith('C4_'):
                    if _c3 == 'No':
                        if _c4_chosen is None:
                            _c4_chosen = rnd.choice(list(_C4_ANSWERS))
                        row[field] = (_C4_ANSWERS[field] if field == _c4_chosen else 'No')
                    else:
                        row[field] = 'NA'

                # ── D1 (state) / D1_flag (derived) ───────────────────────
                elif field == 'D1':
                    v = rnd.randint(1, 5) if rnd.random() < 0.92 else np.nan
                    row[field] = v; _d1 = v
                elif field == 'D1_flag':
                    d1_nan = isinstance(_d1, float) and math.isnan(_d1)
                    d2_min = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                              if _d2_vals else None)
                    row[field] = d1_nan or (
                        d2_min is not None and not d1_nan and d2_min != _d1)

                # ── D2 age groups (all-or-nothing answered) ───────────────
                elif field.startswith('D2_') and field not in _D2_DERIVED:
                    if _d2_answered is None:
                        _d2_answered = rnd.random() < 0.88
                    v = rnd.choice(PEOPLE) if _d2_answered else 'No response'
                    row[field] = v; _d2_vals[field] = v
                elif field == 'D2_ignored':
                    row[field] = not bool(_d2_answered)
                elif field == 'D2_min_total':
                    row[field] = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                                  if _d2_answered else np.nan)

                # ── D3 working situation (all-or-nothing answered) ────────
                elif field.startswith('D3_') and field.split('_')[1].isdigit():
                    if _d3_answered is None:
                        _d3_answered = rnd.random() < 0.88
                    v = rnd.choice(PEOPLE) if _d3_answered else 'No response'
                    row[field] = v; _d3_vals[field] = v
                elif field in ('D3_flag_high', 'D3_flag_low'):
                    row[field] = False
                elif field == 'D3_ignored':
                    row[field] = not bool(_d3_answered)
                elif field == 'D3_total_over16':
                    row[field] = (
                        sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                            for i in range(1, 7))
                        if _d3_answered else np.nan)

                # ── D4_flag (derived from D3 + D4) ───────────────────────
                elif field == 'D4_flag':
                    if not _d3_answered:
                        row[field] = False
                    else:
                        working = sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                                      for i in (1, 2, 5))
                        d4 = row.get('D4')
                        d4_answered = not (d4 is None or
                                           isinstance(d4, float) and math.isnan(d4))
                        row[field] = (working == 0 and d4_answered)

                # ── D5/D6: EVs (trait-aligned, D6 ≤ D5) ─────────────────
                elif field == 'D5':
                    row[field] = rnd.choice(['1', '2', '3 or more']) if has_ev else '0'
                elif field == 'D6':
                    d5_n = {'0':0,'1':1,'2':2,'3 or more':3}.get(row.get('D5','0'), 0)
                    row[field] = '0' if d5_n == 0 else rnd.choice(['0', '1'])
                elif field == 'D6_err':
                    row[field] = False

                # ── E2/E3: numeric satisfaction scales ────────────────────
                elif field == 'E2':
                    row[field] = nr([str(i) for i in range(11)], p=0.08)
                elif field == 'E3':
                    row[field] = nr([str(i) for i in range(10)] + ['11'], p=0.08)

                # ── fields with a direct option lookup ────────────────────
                elif field in _field_opts:
                    p = 0.35 if field == 'C1' else 0.08
                    row[field] = nr(_field_opts[field], p=p)

                # ── fallback: any unrecognised derived flag ────────────────
                else:
                    row[field] = False

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Exporters list ----------
    def generate_list_of_exporters(self) -> pd.DataFrame:
        exporters = sorted(self._pv_households)
        return pd.DataFrame(exporters, columns=['PUPRN'])

    # ---------- Write all ----------
    def write_all(self, outfolder: "Union[str, os.PathLike]", mock_only_outfolder: "Optional[Union[str, os.PathLike]]" = None):

        mock_only_dir = Path(mock_only_outfolder) if mock_only_outfolder is not None else Path(outfolder)

        # EPC
        epc_df = self.generate_epc()
        write_csv(epc_df, str(Path(outfolder) / self._fname(self.names.epc)))

        # SERL survey
        serl_df = self.generate_serl_survey()
        write_csv(serl_df, str(Path(outfolder) / self._fname(self.names.survey)))

        # COVID-19 survey
        covid19_df = self.generate_covid19_survey()
        write_csv(covid19_df, str(Path(outfolder) / self._fname(self.names.covid19_survey)))

        # Participant summary
        summary_df = self.generate_participant_summary()
        write_csv(summary_df, str(Path(outfolder) / self._fname(self.names.summary)))

        # Follow-up survey
        followup_df = self.generate_follow_up_survey()
        write_csv(followup_df, str(Path(outfolder) / self._fname(self.names.followup_survey)), encoding="latin-1")

        # Exporters list — mock-only file, not part of the SERL Edition release
        exporters_base = f"{self.names.exporters_prefix}_{self.year}_list_of_exporter_puprns"
        exporters_df = self.generate_list_of_exporters()
        write_csv(exporters_df, str(mock_only_dir / self._fname(exporters_base)))