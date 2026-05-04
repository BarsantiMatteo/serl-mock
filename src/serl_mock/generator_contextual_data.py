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
    select_household_subset,
    select_pv_households,
)
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

        devices_cfg = cfg.get("devices", {})
        pv_cfg = devices_cfg.get("pv", cfg.get("pv", {}))
        hp_cfg = devices_cfg.get("hp", cfg.get("hp", {}))
        ev_cfg = devices_cfg.get("ev", cfg.get("ev", {}))

        pv_fraction = float(devices_cfg.get("pv_fraction", pv_cfg.get("fraction", 0.07)))
        hp_fraction = float(devices_cfg.get("hp_fraction", hp_cfg.get("fraction", 0.0)))
        ev_fraction = float(devices_cfg.get("ev_fraction", ev_cfg.get("fraction", 0.0)))

        default_n_pv = int(round(self.n_households * pv_fraction))
        default_n_hp = int(round(self.n_households * hp_fraction))
        default_n_ev = int(round(self.n_households * ev_fraction))

        explicit_n_pv = devices_cfg.get("pv_households", pv_cfg.get("households", pv_cfg.get("n_households")))
        explicit_n_hp = devices_cfg.get("hp_households", hp_cfg.get("households", hp_cfg.get("n_households")))
        explicit_n_ev = devices_cfg.get("ev_households", ev_cfg.get("households", ev_cfg.get("n_households")))

        self.n_pv_households = int(default_n_pv if explicit_n_pv is None else explicit_n_pv)
        self.n_hp_households = int(default_n_hp if explicit_n_hp is None else explicit_n_hp)
        self.n_ev_households = int(default_n_ev if explicit_n_ev is None else explicit_n_ev)

        # Survey dictionary path
        self.survey_dictionary_path = cfg.get(
            "survey_dictionary_path",
            "data/reference/serl_survey_data_dictionary_edition07.csv",
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

        self._pv_households = select_pv_households(
            puprns=list(self.puprns),
            n_pv=self.n_pv_households,
            seed=self.seed,
        )
        self._hp_households = select_household_subset(
            puprns=list(self.puprns),
            n_selected=self.n_hp_households,
            seed=self.seed,
            seed_offset=700,
            label="hp",
        )
        self._ev_households = select_household_subset(
            puprns=list(self.puprns),
            n_selected=self.n_ev_households,
            seed=self.seed,
            seed_offset=800,
            label="ev",
        )

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
                    row[field] = rnd.randint(-2, 7)
                elif field == 'C1_new':
                    row[field] = rnd.choice([1, 2, 3])
                elif field == 'C5':
                    row[field] = rnd.randint(-2, 2)
                elif field == 'D4':
                    row[field] = rnd.randint(-3, 5)
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
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.1 else rnd.choice(multi_choice_responses)
                elif field.startswith('B') and field[1:].isdigit():
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.05 else rnd.choice(multi_choice_responses)
                elif field.startswith('C') and field[1:].isdigit():
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.05 else rnd.randint(0, 15)
                elif field.startswith('D') and field[1:].isdigit():
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.15 else rnd.choice(multi_choice_responses)
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
    def generate_covid19_survey(self) -> pd.DataFrame:
        rnd = random.Random(self.seed + 600)
        missing_codes = [-1, -2, -9]

        def maybe_missing(choices, p=0.05):
            return rnd.choice(missing_codes) if rnd.random() < p else rnd.choice(choices)

        data = []
        for puprn in self.puprns:
            row: dict = {'PUPRN': puprn}

            # Q1: Smart meter IHD usage frequency during lockdown (1=most days … 7=don't know)
            row['Q1'] = maybe_missing([1, 2, 3, 4, 5, 6, 7])

            # Q2: IHD usage vs. pre-lockdown (skip if Q1=6 "I don't have this")
            row['Q2'] = 4 if row['Q1'] == 6 else maybe_missing([1, 2, 3, 4, 5])

            # Q3: Switched energy tariff/supplier during lockdown
            q3 = rnd.choice([1, 2])
            row['Q3'] = q3

            # Q3a: Motivations for switching (multi-select binary; only if Q3=1)
            for i in range(1, 8):
                row[f'Q3a_{i}'] = rnd.choice([0, 1]) if q3 == 1 else np.nan

            # Q4: Outdoor/green-space access (multi-select binary)
            for i in range(1, 7):
                row[f'Q4_{i}'] = rnd.choice([0, 1])

            # Q5: Energy conservation effort (1=great deal … 5=don't know)
            row['Q5'] = maybe_missing([1, 2, 3, 4, 5])

            # Q6: Window-opening frequency on cold/warm days
            # Response codes: 7=always, 14=very often, 15=quite often, 16=not very often, 17=never, 18=don't know
            for i in range(1, 3):
                row[f'Q6_{i}'] = maybe_missing([7, 14, 15, 16, 17, 18])

            # Q7: Damp/condensation/mould (1=yes, 2=no, 3=don't know)
            row['Q7'] = rnd.choice([1, 2, 3])

            # Q8b: Heating hours vs. pre-lockdown (1=more … 4=N/A)
            row['Q8b'] = maybe_missing([1, 2, 3, 4])

            # Q9: Thermostat unit (1=Celsius, 2=Fahrenheit, 4=can't remember/N/A)
            row['Q9'] = maybe_missing([1, 2, 4])

            # Q10: Number of heated rooms vs. pre-lockdown
            row['Q10'] = maybe_missing([1, 2, 3, 4])

            # Q11a: "Don't have this appliance" flags
            # Appliances: 1=baths, 2=showers, 3=washing machine, 4=tumble dryer, 5=dishwasher, 6=cooker/oven/grill
            for idx in range(1, 7):
                row[f'Q11a_2_{idx}_1'] = rnd.choice([0, 1])

            # Q11b: Appliance usage frequency vs. pre-lockdown (1=more … 4=don't know)
            for i in range(1, 7):
                row[f'Q11b_{i}'] = maybe_missing([1, 2, 3, 4])

            # Q12a: "Don't have this device" flags
            # Devices: 1=TV, 2=laptop/computer/tablet, 3=electric gym equipment
            for idx in range(1, 4):
                row[f'Q12a_2_{idx}_1'] = rnd.choice([0, 1])

            # Q12b: Device usage duration vs. pre-lockdown (1=more … 4=don't know)
            for i in range(1, 4):
                row[f'Q12b_{i}'] = maybe_missing([1, 2, 3, 4])

            # Q22a: Time-of-day during lockdown for 9 activities × 6 slots (multi-select binary)
            # Activities: 1=baths, 2=showers, 3=washing machine, 4=tumble dryer,
            #             5=dishwasher, 6=oven/cooker/grill, 7=TV, 8=laptop/tablet, 9=gym equipment
            # Slots: 1=4am-7:59am, 2=8am-11:59am, 3=12pm-3:59pm,
            #        4=4pm-7:59pm, 5=8pm-11:59pm, 6=12am-3:59am
            for act in range(1, 10):
                for slot in range(1, 7):
                    row[f'Q22a_1_{act}_{slot}'] = rnd.choice([0, 1])
                row[f'Q22a_2_{act}_1'] = rnd.choice([0, 1])  # "don't know" flag

            # Q22b: Time-of-day before lockdown for the same 9 activities × 6 slots
            for act in range(1, 10):
                for slot in range(1, 7):
                    row[f'Q22b_1_{act}_{slot}'] = rnd.choice([0, 1])
                row[f'Q22b_2_{act}_1'] = rnd.choice([0, 1])  # "don't know" flag

            # Q17: Increased time at home before formal lockdown start
            row['Q17'] = maybe_missing([1, 2, 3, 4])

            # Q18: Expect to continue spending more time at home (codes: 1,4,5,6)
            row['Q18'] = maybe_missing([1, 4, 5, 6])

            # Q19: Financial wellbeing (1=living comfortably … 7=prefer not to say)
            row['Q19'] = maybe_missing([1, 2, 3, 4, 5, 6, 7])

            # Q21: Easing of restrictions caused substantial household changes
            row['Q21'] = maybe_missing([1, 2, 3, 4])

            # Q24: Usual travel-to-work mode BEFORE lockdown (1=car driver … 12=N/A)
            row['Q24'] = maybe_missing(list(range(1, 13)))

            # Q25: Usual travel-to-work mode DURING lockdown (1=car driver … 12=N/A)
            row['Q25'] = maybe_missing(list(range(1, 13)))

            # RC1/RC2: Research consent
            row['RC1'] = rnd.choice([1, 2])
            row['RC2'] = rnd.choice([1, 2])

            # Finished indicator (0=FALSE, 1=TRUE)
            row['Finished'] = rnd.choice([0, 1])

            data.append(row)

        return pd.DataFrame(data)

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
    def generate_follow_up_survey(self) -> pd.DataFrame:
        fields = ['PUPRN','A1_corr_C','A1_err','B3_1_yes','B3_4_yes','C1','D4','D5']
        incomes = [
            'Below £10,000','£10,001 to £20,000','£20,001 to £30,000','£30,001 to £40,000',
            '£40,001 to £50,000','£50,001 to £60,000','£60,001 to £70,000','£70,001 to £80,000',
            '£80,001 to £90,000','£90,0001 to £100,000','Above £100,000','Prefer not to answer'
        ]
        wfh = ['Always work from home','Sometimes work from home','Never work from home','Not applicable /prefer not to say']

        rnd = random.Random(self.seed + 400)
        rows = []
        for puprn in self.puprns:
            has_pv = puprn in self._pv_households
            has_ev = puprn in self._ev_households
            rows.append({
                'PUPRN': puprn,
                'A1_corr_C': (np.nan if rnd.random() < 0.4 else round(rnd.uniform(15, 25), 1)),
                'A1_err': True if rnd.random() < 0.1 else False,
                'B3_1_yes': ('Yes' if has_pv else 'No'),
                'B3_4_yes': ('Yes' if has_ev else 'No'),
                'C1': (np.nan if rnd.random() < 0.4 else rnd.choice(incomes)),
                'D4': (np.nan if rnd.random() < 0.4 else rnd.choice(wfh)),
                'D5': (rnd.choice(['1', '2', '3 or more']) if has_ev else '0'),
            })
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