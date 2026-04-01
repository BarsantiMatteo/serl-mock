# src/serl_mock/generator_contextual_data.py
from __future__ import annotations
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
import pandas as pd

from .ids import make_alphanumeric_ids_ordered, load_puprn_list_csv
from .utils import (
    read_config, seed_random, ensure_output_dir,
    with_edition_suffix, write_csv, read_survey_dictionary
)

@dataclass
class OutputNames:
    epc: str = "epc_data"
    survey: str = "serl_survey_data"
    summary: str = "serl_participant_summary_data"
    followup_prefix: str = "serl_follow_up_survey"
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
            epc=fcfg.get("epc", "epc_data"),
            survey=fcfg.get("survey", "serl_survey_data"),
            summary=fcfg.get("summary", "serl_participant_summary_data"),
            followup_prefix=fcfg.get("followup_prefix", "serl_follow_up_survey"),
            exporters_prefix=fcfg.get("exporters_prefix", "Elec"),
        )
        self.edition = str(cfg.get("edition", "")).strip() or None

        # Survey dictionary path
        self.survey_dictionary_path = cfg.get(
            "survey_dictionary_path",
            "docs/documentation/serl_survey_data_dictionary_edition07.csv",
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

    # ---------- File naming ----------
    def _fname(self, basename: str) -> str:
        return with_edition_suffix(basename, self.edition)

    # ---------- EPC ----------
    @staticmethod
    def _epc_fields() -> List[str]:
        # Same EPC header set as your previous script
        return [
            'PUPRN', 'currentEnergyRating', 'POTENTIAL_ENERGY_RATING',
            'CURRENT_ENERGY_EFFICIENCY', 'POTENTIAL_ENERGY_EFFICIENCY',
            'PROPERTY_TYPE', 'BUILT_FORM', 'INSPECTION_DATE', 'LOCAL_AUTHORITY',
            'CONSTITUENCY', 'COUNTY', 'LODGEMENT_DATE', 'TRANSACTION_TYPE',
            'ENVIRONMENT_IMPACT_CURRENT', 'ENVIRONMENT_IMPACT_POTENTIAL',
            'ENERGY_CONSUMPTION_CURRENT', 'ENERGY_CONSUMPTION_POTENTIAL',
            'CO2_EMISSIONS_CURRENT', 'CO2_EMISS_CURR_PER_FLOOR_AREA',
            'CO2_EMISSIONS_POTENTIAL', 'LIGHTING_COST_CURRENT',
            'LIGHTING_COST_POTENTIAL', 'HEATING_COST_CURRENT',
            'HEATING_COST_POTENTIAL', 'HOT_WATER_COST_CURRENT',
            'HOT_WATER_COST_POTENTIAL', 'totalFloorArea', 'ENERGY_TARIFF',
            'MAINS_GAS_FLAG', 'FLOOR_LEVEL', 'FLAT_TOP_STOREY',
            'FLAT_STOREY_COUNT', 'MAIN_HEATING_CONTROLS', 'MULTI_GLAZE_PROPORTION',
            'GLAZED_TYPE', 'GLAZED_AREA', 'EXTENSION_COUNT', 'NUMBER_HABITABLE_ROOMS',
            'NUMBER_HEATED_ROOMS', 'LOW_ENERGY_LIGHTING', 'NUMBER_OPEN_FIREPLACES',
            'HOTWATER_DESCRIPTION', 'HOT_WATER_ENERGY_EFF', 'HOT_WATER_ENV_EFF',
            'FLOOR_DESCRIPTION', 'FLOOR_ENERGY_EFF', 'FLOOR_ENV_EFF',
            'WINDOWS_DESCRIPTION', 'WINDOWS_ENERGY_EFF', 'WINDOWS_ENV_EFF',
            'WALLS_DESCRIPTION', 'WALLS_ENERGY_EFF', 'WALLS_ENV_EFF',
            'SECONDHEAT_DESCRIPTION', 'SHEATING_ENERGY_EFF', 'SHEATING_ENV_EFF',
            'ROOF_DESCRIPTION', 'ROOF_ENERGY_EFF', 'ROOF_ENV_EFF',
            'MAINHEAT_DESCRIPTION', 'MAINHEAT_ENERGY_EFF', 'MAINHEAT_ENV_EFF',
            'MAINHEATCONT_DESCRIPTION', 'MAINHEATC_ENERGY_EFF', 'MAINHEATC_ENV_EFF',
            'LIGHTING_DESCRIPTION', 'LIGHTING_ENERGY_EFF', 'LIGHTING_ENV_EFF',
            'MAIN_FUEL', 'WIND_TURBINE_COUNT', 'HEAT_LOSS_CORRIDOR',
            'UNHEATED_CORRIDOR_LENGTH', 'FLOOR_HEIGHT', 'PHOTO_SUPPLY',
            'SOLAR_WATER_HEATING_FLAG', 'MECHANICAL_VENTILATION',
            'LOCAL_AUTHORITY_LABEL', 'CONSTITUENCY_LABEL', 'POSTTOWN',
            'CONSTRUCTION_AGE_BAND', 'LODGEMENT_DATETIME', 'TENURE',
            'FIXED_LIGHTING_OUTLETS_COUNT', 'LOW_ENERGY_FIXED_LIGHT_COUNT'
        ]

    def generate_epc(self) -> pd.DataFrame:
        fields = self._epc_fields()
        energy_ratings = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        property_types = ['House', 'Flat', 'Maisonette', 'Bungalow']
        built_forms = ['Detached', 'Semi-Detached', 'Mid-Terrace', 'End-Terrace']
        efficiency_ratings = ['Very Good', 'Good', 'Average', 'Poor', 'Very Poor']
        yes_no_flags = ['Y', 'N']
        fuel_types = ['mains gas (not community)', 'electricity', 'oil', 'solid fuel']
        tenure_types = ['Owner-occupied', 'Rented (private)', 'Rented (social)']

        data = []
        rnd = random.Random(self.seed + 100)

        for puprn in self.puprns:
            row = {'PUPRN': puprn}
            for field in fields[1:]:
                if 'RATING' in field and 'EFFICIENCY' not in field:
                    row[field] = np.nan if rnd.random() < 0.3 else rnd.choice(energy_ratings)
                elif 'EFFICIENCY' in field:
                    row[field] = rnd.randint(20, 100)
                elif field == 'PROPERTY_TYPE':
                    row[field] = rnd.choice(property_types)
                elif field == 'BUILT_FORM':
                    row[field] = rnd.choice(built_forms)
                elif 'DATE' in field:
                    base_date = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_date + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%Y-%m-%d')
                elif field == 'totalFloorArea':
                    row[field] = np.nan if rnd.random() < 0.3 else round(rnd.uniform(30.0, 300.0), 1)
                elif field == 'FLOOR_HEIGHT':
                    row[field] = round(rnd.uniform(2.0, 3.5), 2)
                elif 'COST' in field:
                    row[field] = rnd.randint(50, 2000)
                elif 'EMISSIONS' in field:
                    row[field] = round(rnd.uniform(0.5, 10.0), 1)
                elif 'CONSUMPTION' in field:
                    row[field] = rnd.randint(50, 500)
                elif 'ENVIRONMENT_IMPACT' in field:
                    row[field] = rnd.randint(20, 100)
                elif 'FLAG' in field:
                    row[field] = rnd.choice(yes_no_flags)
                elif 'ENERGY_EFF' in field or 'ENV_EFF' in field:
                    row[field] = rnd.choice(efficiency_ratings)
                elif field == 'MAIN_FUEL':
                    row[field] = rnd.choice(fuel_types)
                elif field == 'TENURE':
                    row[field] = rnd.choice(tenure_types)
                elif 'COUNT' in field or 'NUMBER' in field:
                    row[field] = rnd.randint(0, 10)
                elif 'PROPORTION' in field or 'SUPPLY' in field:
                    row[field] = round(rnd.uniform(0.0, 100.0), 1)
                elif 'DESCRIPTION' in field:
                    row[field] = f"Sample {field.lower().replace('_', ' ')}"
                elif 'LABEL' in field or field in ['COUNTY', 'POSTTOWN']:
                    row[field] = f"Sample {field.lower()}"
                elif field == 'CONSTRUCTION_AGE_BAND':
                    age_bands = ['before 1900', '1900-1929', '1930-1949', '1950-1966',
                                 '1967-1975', '1976-1982', '1983-1990', '1991-1995',
                                 '1996-2002', '2003-2006', '2007-2011', '2012 onwards']
                    row[field] = rnd.choice(age_bands)
                elif 'DATETIME' in field:
                    base_dt = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_dt + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%Y-%m-%d %H:%M:%S')
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
            row = {'PUPRN': puprn}
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
            data.append(row)

        return pd.DataFrame(data)

    # ---------- Participant summary ----------
    def generate_participant_summary(self) -> pd.DataFrame:
        fields = ['PUPRN', 'Region', 'IMD_quintile']
        regions = [
            'NORTH EAST','NORTH WEST','YORKSHIRE AND THE HUMBER','EAST MIDLANDS',
            'WEST MIDLANDS','EAST OF ENGLAND','LONDON','SOUTH EAST','SOUTH WEST',
            'SCOTLAND','WALES'
        ]
        data = []
        rnd = random.Random(self.seed + 300)
        for puprn in self.puprns:
            data.append({
                'PUPRN': puprn,
                'Region': rnd.choice(regions),
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
        yes_or_no = ['Yes', 'No', 'No response']
        wfh = ['Always work from home','Sometimes work from home','Never work from home','Not applicable /prefer not to say']
        evs = ['0','1','2','3 or more',"Don't know"]

        rnd = random.Random(self.seed + 400)
        rows = []
        for puprn in self.puprns:
            rows.append({
                'PUPRN': puprn,
                'A1_corr_C': (np.nan if rnd.random() < 0.4 else round(rnd.uniform(15, 25), 1)),
                'A1_err': True if rnd.random() < 0.1 else False,
                'B3_1_yes': (np.nan if rnd.random() < 0.4 else rnd.choice(yes_or_no)),
                'B3_4_yes': (np.nan if rnd.random() < 0.4 else rnd.choice(yes_or_no)),
                'C1': (np.nan if rnd.random() < 0.4 else rnd.choice(incomes)),
                'D4': (np.nan if rnd.random() < 0.4 else rnd.choice(wfh)),
                'D5': (np.nan if rnd.random() < 0.4 else rnd.choice(evs)),
            })
        return pd.DataFrame(rows, columns=fields)

    # ---------- Exporters list ----------
    def generate_list_of_exporters(self) -> pd.DataFrame:
        rnd = random.Random(self.seed + 500)
        exporters = [p for p in self.puprns if rnd.random() < 0.07]
        return pd.DataFrame(exporters, columns=['PUPRN'])

    # ---------- Write all ----------
    def write_all(self, outfolder: "Union[str, os.PathLike]"):

        # EPC
        epc_df = self.generate_epc()
        write_csv(epc_df, str(Path(outfolder) / self._fname(self.names.epc)))

        # SERL survey
        serl_df = self.generate_serl_survey()
        write_csv(serl_df, str(Path(outfolder) / self._fname(self.names.survey)))

        # Participant summary
        summary_df = self.generate_participant_summary()
        write_csv(summary_df, str(Path(outfolder) / self._fname(self.names.summary)))

        # Follow-up survey 
        followup_base = f"{self.names.followup_prefix}_{self.year}_data"
        followup_df = self.generate_follow_up_survey()
        write_csv(followup_df, str(Path(outfolder) / self._fname(followup_base)), encoding="latin-1")

        # Exporters list
        exporters_base = f"{self.names.exporters_prefix}_{self.year}_list_of_exporter_puprns"
        exporters_df = self.generate_list_of_exporters()
        write_csv(exporters_df, str(Path(outfolder) / self._fname(exporters_base)))