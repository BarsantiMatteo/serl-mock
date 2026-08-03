# src/serl_mock/paths.py

from pathlib import Path

# Resolve project root automatically (directory containing pyproject.toml)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
MOCK_DIR = DATA_DIR / "mock"

# Subfolder *names* (not full paths) for the per-edition smart-meter/climate
# datasets — parameterized by edition so a run's subfolders actually match
# its own edition instead of always saying "edition08". Reattach these under
# a run's own output folder, e.g. mock_dir_for(output_label) / mock_hh_dirname(edition).
def mock_hh_dirname(edition: str) -> str:
    return f"serl_smart_meter_hh_edition{edition}"


def mock_daily_dirname(edition: str) -> str:
    return f"serl_smart_meter_daily_edition{edition}"


def mock_climate_dirname(edition: str) -> str:
    return f"serl_climate_data_edition{edition}"


# Target folder for mock-only files (not part of any SERL Edition release)
MOCK_INTERNAL_DIR = MOCK_DIR / "mock_internal"

# Placeholder folder mirroring the serl_aggregated_data/ directory in the TRE
MOCK_AGGREGATED_DIR = MOCK_DIR / "serl_aggregated_data"

# Static reference files committed to the repo — mirrored into a run's output
# folder (data/mock/<output_label>/) at generation time. Transversal files
# (not edition-specific, e.g. bank holidays) live directly here; edition-
# specific reference files (data dictionaries) live under reference_dir_for(edition).
REFERENCE_DIR = DATA_DIR / "reference"

# Real ONS LSOA (England/Wales) and NRS Data Zone (Scotland) codes, used as a
# sampling pool for the participant summary's LSOA field — see
# scripts/generate_lsoa_codes_csv.py.
LSOA_CODES_PATH = REFERENCE_DIR / "lsoa_codes_england_wales_scotland.csv"


def reference_dir_for(edition: str) -> Path:
    """Reference files (data dictionaries) specific to a given edition.

    Note the dictionary *filenames* inside may not carry this same edition
    number — see edition.Edition.dictionary_source_edition.
    """
    return REFERENCE_DIR / f"edition{edition}"


def mock_dir_for(output_label: str) -> Path:
    """Root output folder for a given run's mock data (e.g. data/mock/edition08/)."""
    return MOCK_DIR / output_label