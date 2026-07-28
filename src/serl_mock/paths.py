# src/serl_mock/paths.py

from pathlib import Path

# Resolve project root automatically (directory containing pyproject.toml)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
MOCK_DIR = DATA_DIR / "mock"

# These four constants hardcode the literal "edition08" subfolder/file name —
# a real run's actual output lands under mock_dir_for(output_label) (e.g.
# data/mock/edition08/), and generate_mock_data.py only reuses these
# constants' *names* (MOCK_HH_DIR.name, etc.), reattaching them under the
# active run's own output folder. They do NOT yet vary if edition != "08" —
# making them edition-aware is still open (see Phase 2 in
# docs/notes/edition_multiformat_plan.md).

# Target folder for monthly half-hourly mock files
MOCK_HH_DIR = MOCK_DIR / "serl_smart_meter_hh_edition08"

# Target folder for yearly daily mock files
MOCK_DAILY_DIR = MOCK_DIR / "serl_smart_meter_daily_edition08"

# Target folder for ERA5 climate data files
MOCK_CLIMATE_DIR = MOCK_DIR / "serl_climate_data_edition08"

# Target folder for mock-only files (not part of any SERL Edition release)
MOCK_INTERNAL_DIR = MOCK_DIR / "mock_internal"

# Placeholder folder mirroring the serl_aggregated_data/ directory in the TRE
MOCK_AGGREGATED_DIR = MOCK_DIR / "serl_aggregated_data"

# Static reference files committed to the repo — mirrored into a run's output
# folder (data/mock/<output_label>/) at generation time. Transversal files
# (not edition-specific, e.g. bank holidays) live directly here; edition-
# specific reference files (data dictionaries) live under reference_dir_for(edition).
REFERENCE_DIR = DATA_DIR / "reference"


def reference_dir_for(edition: str) -> Path:
    """Reference files (data dictionaries) specific to a given edition."""
    return REFERENCE_DIR / f"edition{edition}"


# Some editions' reference dictionaries are sourced from an earlier edition's
# real SERL documentation rather than their own — e.g. edition08's dictionary
# files are still the real edition07 SERL documentation, with edition08
# deviations only noted in prose (see docs/05_epc_reference.md). Maps
# edition -> the edition number its dictionary filenames actually carry.
# Editions not listed here are assumed to have their own accurately-named
# dictionaries (e.g. edition09's, once real ones are added).
DICTIONARY_SOURCE_EDITION = {
    "08": "07",
}


def dictionary_source_edition(edition: str) -> str:
    """Which edition number a given edition's dictionary filenames use."""
    return DICTIONARY_SOURCE_EDITION.get(edition, edition)


def mock_dir_for(output_label: str) -> Path:
    """Root output folder for a given run's mock data (e.g. data/mock/edition08/)."""
    return MOCK_DIR / output_label