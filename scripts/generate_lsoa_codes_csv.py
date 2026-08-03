"""
Fetch real ONS LSOA (England & Wales, 2011 and 2021 vintages) and NRS Data
Zone (Scotland) codes and write
data/reference/lsoa_codes_england_wales_scotland.csv.

The mock LSOA field uses these as a pool to sample from — codes are real
and correctly prefixed per nation, but a given household's code is not
guaranteed to correspond to its actual assigned region. England/Wales rows
carry a `year` (2011 or 2021) so the generator can pick a vintage via the
`lsoa_vintage` config setting; Scotland only has one Data Zone vintage in
current use, so its rows are included for either setting (see
read_lsoa_codes() in src/serl_mock/utils.py).

Run this locally whenever the reference file needs updating:

    uv run python scripts/generate_lsoa_codes_csv.py

The output is committed to the repo and read by the mock pipeline.
"""

import csv
import urllib.request
from pathlib import Path

# ONS Open Geography Portal: "Lower layer Super Output Areas (December 2011)
# Names and Codes in EW" — LSOA11CD/LSOA11NM only.
ONS_LSOA_EW_2011_URL = "https://open-geography-portalx-ons.hub.arcgis.com/api/download/v1/items/a5b7042782fe4ee99b8477ddf7bfe585/csv?layers=0"

# ONS Open Geography Portal: "Lower layer Super Output Areas (December 2021)
# Names and Codes in England and Wales (V3)" — LSOA21CD/LSOA21NM only.
ONS_LSOA_EW_2021_URL = "https://geoportal.statistics.gov.uk/datasets/0f80c523f3cd4d0fab5111572f84a2fb_0.csv"

# statistics.gov.scot: "2011 Data Zone Lookup" — DZ2011_Code/DZ2011_Name only.
NRS_DATAZONE_SCOTLAND_URL = (
    "https://statistics.gov.scot/downloads/file?"
    "id=50d30936-6de2-4ae0-a131-c2105aa74647%2FDataZone2011lookup_2024-12-16.csv"
)

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "reference"
    / "lsoa_codes_england_wales_scotland.csv"
)


def fetch_csv_rows(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return list(csv.DictReader(text.splitlines()))


def main() -> None:
    print(f"Fetching England & Wales LSOA 2011 codes from {ONS_LSOA_EW_2011_URL} ...")
    ew_2011_rows = fetch_csv_rows(ONS_LSOA_EW_2011_URL)
    ew_2011_codes = [
        {"code": r["LSOA11CD"], "name": r["LSOA11NM"], "year": "2011"} for r in ew_2011_rows
    ]

    print(f"Fetching England & Wales LSOA 2021 codes from {ONS_LSOA_EW_2021_URL} ...")
    ew_2021_rows = fetch_csv_rows(ONS_LSOA_EW_2021_URL)
    ew_2021_codes = [
        {"code": r["LSOA21CD"], "name": r["LSOA21NM"], "year": "2021"} for r in ew_2021_rows
    ]

    print(f"Fetching Scotland Data Zone codes from {NRS_DATAZONE_SCOTLAND_URL} ...")
    scot_rows = fetch_csv_rows(NRS_DATAZONE_SCOTLAND_URL)
    scot_codes = [
        {"code": r["DZ2011_Code"], "name": r["DZ2011_Name"], "year": "2011"} for r in scot_rows
    ]

    rows = sorted(ew_2011_codes + ew_2021_codes + scot_codes, key=lambda r: (r["code"], r["year"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "name", "year"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
