"""
Generate mock SERL smart-meter and contextual data.

Loads settings from config/serl_mock.yaml and produces:

  1. puprn_master.csv               — shared household ID list
  2. Monthly half-hourly CSVs       — realistic electricity and gas time series
                                       with seasonal and intraday patterns
                                       (see src/serl_mock/patterns.py)
  3. Contextual datasets            — EPC, survey, participant summary,
                                       follow-up survey, list of exporters

Output is saved under data/mock/.  The generated data is not real SERL data
and is intended only for pipeline testing and local development outside the
Trusted Research Environment.
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
    print(f"  {len(puprns)} PUPRNs written to {puprn_csv}")

    print(f"\nStep 2: Generating smart meter data "
          f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
          f"edition {cfg.get('edition', '08')})")
    gen_sm = HHSmartMeterGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_sm.generate_all(outfolder=MOCK_HH_DIR)

    print("\nStep 3: Generating contextual variables")
    gen_ctx = SERLContextualVariablesGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_ctx.write_all(outfolder=MOCK_DIR)
    print("\nDone.")

if __name__ == "__main__":
    run_all()

