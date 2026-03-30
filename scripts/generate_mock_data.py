"""
Generate mock SERL half‑hourly smart‑meter data.

This script loads the configuration from config/smartmeter_schema.json
and produces monthly CSV files (2019–2025) with the same structure and
column names as the SERL Edition 08 half‑hourly dataset. The output is
saved under data/mock/serl_smart_meter_hh_edition08/.

The mock data is not intended to be realistic — it is only for
testing pipelines, code development, and running workflows outside the
DSH environment.
"""

# --- src-layout shim ---
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.paths import CONFIG_DIR, MOCK_DIR, MOCK_HH_DIR
from src.serl_mock.ids import make_alphanumeric_ids_ordered, write_puprn_list_csv
from src.serl_mock.generator_smartmeter import HHSmartMeterGenerator
from src.serl_mock.generator_contextual_data import SERLContextualVariablesGenerator
from src.serl_mock.utils import read_config



def run_all():
    cfg_path = CONFIG_DIR / "serl_mock.yaml"
    cfg = read_config(cfg_path)

    # Ensure target folders exist
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_HH_DIR.mkdir(parents=True, exist_ok=True)

    print("\nStep 1: Generating PUPRNs")
    puprn_csv = MOCK_DIR / "puprn_master.csv"
    puprns = make_alphanumeric_ids_ordered(
        n=cfg.get("n_households", 100),
        seed=cfg.get("seed", 42),
        length=cfg.get("puprn", {}).get("length", 8)
    )
    write_puprn_list_csv(puprns, puprn_csv)
    
    print("\nStep 2: Generating smart meter data")
    gen_sm = HHSmartMeterGenerator(
        config_path=str(cfg_path), 
        puprn_list_path=puprn_csv
    )
    gen_sm.generate_all(outfolder=MOCK_HH_DIR)
    
    print("\nStep 3: Generating contextual variables") 
    gen_ctx = SERLContextualVariablesGenerator(
        config_path=str(cfg_path),
        puprn_list_path=puprn_csv
    )
    gen_ctx.write_all(outfolder=MOCK_DIR)

if __name__ == "__main__":
    run_all()