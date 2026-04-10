# src/serl_mock/weather_downloader.py
"""
Download ERA5 reanalysis weather data via the Copernicus Climate Data Store (CDS) API.

The downloaded variables match the SERL climate data schema described in
docs/04_metadata.md:

    grid_cell                           — ERA5 grid cell identifier
    PUPRN                               — linked household ID (added downstream)
    analysis_date                       — local calendar date
    date_time_utc                       — UTC timestamp
    2m_temperature_K                    — 2-m air temperature (K)
    surface_solar_radiation_downwards   — accumulated downward surface solar (J/m²)
    total_precipitation                 — accumulated total precipitation (m)
    10m_u_component_of_wind             — eastward wind at 10 m (m/s)
    10m_v_component_of_wind             — northward wind at 10 m (m/s)

Usage
-----
Instantiate :class:`WeatherDownloader` with a config path (or pass keyword
arguments directly), then call :meth:`download_month` for a single month, or
:meth:`download_all` to iterate over every month in the configured date range.
The raw GRIB/NetCDF files returned by the CDS API are saved under
``data/mock/serl_climate_data_edition08/`` by default.

CDS API credentials
-------------------
The ``cdsapi`` library reads credentials from ``~/.cdsapirc``.  You can also
set the ``CDSAPI_URL`` and ``CDSAPI_KEY`` environment variables.

For setup instructions see:
    https://cds.climate.copernicus.eu/how-to-api
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cdsapi
import pandas as pd

from .utils import read_config
from .paths import CONFIG_DIR, MOCK_CLIMATE_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CDS variable names as expected by the ERA5 hourly dataset
# ---------------------------------------------------------------------------
_DEFAULT_VARIABLES: List[str] = [
    "2m_temperature",
    "surface_solar_radiation_downwards",
    "total_precipitation",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]

# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------

class WeatherDownloader:
    """Download ERA5 reanalysis data for the SERL mock dataset time range.

    Parameters
    ----------
    config_path:
        Path to ``serl_mock.yaml`` (or similar YAML/JSON config).  The
        ``weather`` section of the config is used to populate all defaults.
        Explicit keyword arguments override config values.
    dataset:
        CDS dataset name (default: ``"reanalysis-era5-single-levels"``).
    variables:
        List of ERA5 variable names to retrieve.
    area:
        Bounding box as ``[north, west, south, east]`` in decimal degrees.
    start_year / end_year:
        First and last year to download (inclusive).  Falls back to the
        top-level ``start_year`` / ``end_year`` keys if omitted from the
        ``weather`` section.
    product_type:
        ERA5 product type (``"reanalysis"`` by default).
    grid:
        Spatial resolution as ``[lat_step, lon_step]`` in degrees.
    time_step:
        Temporal resolution in hours (1 for hourly, matching ERA5 default).
    output_format:
        File format requested from the CDS API (``"netcdf"`` or ``"grib"``).
    output_dir:
        Folder where downloaded files are saved.  Defaults to
        ``data/mock/serl_climate_data_edition08/``.
    edition:
        Dataset edition string used in output filenames.
    """

    def __init__(
        self,
        config_path: Optional[Union[str, Path]] = None,
        *,
        dataset: Optional[str] = None,
        variables: Optional[List[str]] = None,
        area: Optional[List[float]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        product_type: Optional[str] = None,
        grid: Optional[List[float]] = None,
        time_step: Optional[int] = None,
        output_format: Optional[str] = None,
        output_dir: Optional[Union[str, Path]] = None,
        edition: Optional[str] = None,
    ) -> None:
        cfg = read_config(config_path or (CONFIG_DIR / "serl_mock.yaml"))
        wcfg: Dict[str, Any] = cfg.get("weather", {})

        self.dataset = dataset or wcfg.get("dataset", "reanalysis-era5-single-levels")
        self.variables = variables or wcfg.get("variables", _DEFAULT_VARIABLES)
        self.area = area or wcfg.get("area", [60.0, -8.0, 49.0, 2.0])  # UK bounding box
        self.product_type = product_type or wcfg.get("product_type", "reanalysis")
        self.grid = grid or wcfg.get("grid", [0.25, 0.25])
        self.time_step = time_step or wcfg.get("time_step_hours", 1)
        self.output_format = output_format or wcfg.get("output_format", "netcdf")

        # Date range: prefer weather-section keys, fall back to top-level keys
        self.start_year = start_year or wcfg.get("start_year") or cfg.get("start_year", 2019)
        self.end_year = end_year or wcfg.get("end_year") or cfg.get("end_year", 2019)

        self.edition = edition or cfg.get("edition", "08")

        if output_dir is not None:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(wcfg.get("output_dir", str(MOCK_CLIMATE_DIR)))

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._client: Optional[cdsapi.Client] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> cdsapi.Client:
        """Return (and lazily create) the CDS API client."""
        if self._client is None:
            self._client = cdsapi.Client()
        return self._client

    def _build_time_list(self) -> List[str]:
        """Build a list of hourly time strings ``HH:00`` for the request."""
        hours = range(0, 24, self.time_step)
        return [f"{h:02d}:00" for h in hours]

    def _output_path(self, year: int, month: int) -> Path:
        """Return the target NetCDF file path for a given year/month."""
        ext = "nc" if self.output_format == "netcdf" else "grib"
        fname = f"serl_climate_data_{year}_{month:02d}_edition{self.edition}.{ext}"
        return self.output_dir / fname

    def _csv_path(self, year: int, month: int) -> Path:
        """Return the target CSV file path for a given year/month."""
        fname = f"serl_climate_data_{year}_{month:02d}_edition{self.edition}.csv"
        return self.output_dir / fname

    def _nc_to_csv(self, nc_path: Path, csv_path: Path) -> None:
        """Convert a downloaded ERA5 NetCDF file to the SERL climate CSV format.

        The CDS API v2 may deliver the download as a ZIP archive containing
        two NetCDF files (one for instantaneous variables, one for accumulated
        variables).  This method handles both the plain NetCDF case and the
        ZIP case transparently.

        Output is hourly (one row per grid cell per UTC hour), matching the
        ERA5 native resolution.  No half-hourly interpolation is performed.

        The ``PUPRN`` column is intentionally omitted; it is added downstream
        when grid cells are linked to households.
        """
        import tempfile
        import zipfile
        import xarray as xr

        logger.info("Converting %s → CSV", nc_path.name)

        # --- Open dataset: handle plain NetCDF and CDS-v2 ZIP bundles ------
        if zipfile.is_zipfile(nc_path):
            # CDS API v2 bundles instant and accum variables in separate files
            # inside a ZIP.  Load everything into memory before closing handles
            # to allow temp-dir cleanup on Windows.
            logger.debug("Detected ZIP bundle; extracting inner NetCDF files.")
            tmp_dir = tempfile.mkdtemp()
            try:
                with zipfile.ZipFile(nc_path) as zf:
                    zf.extractall(tmp_dir)
                    inner_files = [Path(tmp_dir) / name for name in zf.namelist()]

                datasets = []
                for inner in inner_files:
                    _ds = xr.open_dataset(inner, engine="netcdf4")
                    _ds.load()   # pull all data into memory
                    _ds.close()  # release the file handle before temp cleanup
                    datasets.append(_ds)

                ds = xr.merge(datasets)
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            ds = xr.open_dataset(nc_path, engine="netcdf4")

        # ERA5 short name → SERL column name
        _VAR_RENAME: Dict[str, str] = {
            "t2m":  "2m_temperature_K",
            "ssrd": "surface_solar_radiation_downwards",
            "tp":   "total_precipitation",
            "u10":  "10m_u_component_of_wind",
            "v10":  "10m_v_component_of_wind",
        }

        # Detect time coordinate (CDS API v2 uses "valid_time", v1 uses "time")
        time_coord = "valid_time" if "valid_time" in ds.coords else "time"

        # Keep only the weather variables we need; squeeze/drop any extra dims
        keep_vars = [v for v in _VAR_RENAME if v in ds.data_vars]
        ds = ds[keep_vars]
        extra_dims = [d for d in ds.dims if d not in (time_coord, "latitude", "longitude")]
        for dim in extra_dims:
            logger.warning("Unexpected dimension %r in ERA5 file; selecting index 0.", dim)
            ds = ds.isel({dim: 0}, drop=True)

        # Flatten to a tidy DataFrame: rows = (time × lat × lon)
        df: pd.DataFrame = ds.to_dataframe().reset_index()
        df = df.rename(columns={time_coord: "_time_utc"})
        df["_time_utc"] = pd.to_datetime(df["_time_utc"], utc=True)

        # --- Grid-cell identifier (0-based col/row, zero-padded, NW origin) ----
        lats_uniq = sorted(df["latitude"].unique())
        lons_uniq = sorted(df["longitude"].unique())
        north = float(max(lats_uniq))
        west  = float(min(lons_uniq))
        lat_step = float(lats_uniq[1] - lats_uniq[0]) if len(lats_uniq) > 1 else self.grid[0]
        lon_step = float(lons_uniq[1] - lons_uniq[0]) if len(lons_uniq) > 1 else self.grid[1]

        row_idx = ((north - df["latitude"]) / lat_step).round().astype(int)
        col_idx = ((df["longitude"] - west)  / lon_step).round().astype(int)
        df["grid_cell"] = col_idx.map(lambda x: f"{x:02d}") + "_" + row_idx.map(lambda x: f"{x:02d}")
        df = df.drop(columns=["latitude", "longitude"])

        # --- Timestamp columns (hourly, no half-hourly expansion) -----------
        df = df.sort_values(["_time_utc", "grid_cell"]).reset_index(drop=True)
        df["date_time_utc"] = df["_time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        local_times = df["_time_utc"].dt.tz_convert("Europe/London")
        df["analysis_date"] = local_times.dt.strftime("%d/%m/%Y")
        df = df.drop(columns=["_time_utc"])

        # --- Rename ERA5 short names to SERL column names ------------------
        df = df.rename(columns=_VAR_RENAME)

        # --- Write CSV (PUPRN column omitted; added downstream) ------------
        out_cols = [
            "grid_cell",
            "analysis_date",
            "date_time_utc",
            "2m_temperature_K",
            "surface_solar_radiation_downwards",
            "total_precipitation",
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
        ]
        out_cols = [c for c in out_cols if c in df.columns]
        df[out_cols].to_csv(csv_path, index=False)
        logger.info("Saved CSV → %s", csv_path.name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download_month(self, year: int, month: int, *, overwrite: bool = False) -> Path:
        """Download ERA5 data for a single calendar month.

        Parameters
        ----------
        year, month:
            The year and month (1–12) to download.
        overwrite:
            If ``False`` (default), skip if the output file already exists.

        Returns
        -------
        Path
            Path to the downloaded file.
        """
        target = self._output_path(year, month)

        if target.exists() and not overwrite:
            logger.info("Skipping %s (already exists)", target.name)
            return target

        request: Dict[str, Any] = {
            "product_type": [self.product_type],
            "variable": self.variables,
            "year": [str(year)],
            "month": [f"{month:02d}"],
            "day": [f"{d:02d}" for d in range(1, 32)],
            "time": self._build_time_list(),
            "area": self.area,
            "grid": self.grid,
            "data_format": self.output_format,
            "download_format": "unarchived",
        }

        logger.info("Requesting CDS data: %s %04d-%02d", self.dataset, year, month)
        client = self._get_client()
        client.retrieve(self.dataset, request, str(target))
        logger.info("Saved to %s", target)
        return target

    def download_all(self, *, overwrite: bool = False) -> List[Path]:
        """Download ERA5 data for every month in the configured date range.

        Parameters
        ----------
        overwrite:
            Passed through to :meth:`download_month`.

        Returns
        -------
        list of Path
            Paths of all downloaded (or pre-existing) files.
        """
        paths: List[Path] = []
        for year in range(self.start_year, self.end_year + 1):
            for month in range(1, 13):
                path = self.download_month(year, month, overwrite=overwrite)
                paths.append(path)
        return paths

    def convert_month_to_csv(
        self,
        year: int,
        month: int,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Convert the downloaded ERA5 NetCDF file for *year*/*month* to CSV.

        If the CSV already exists and *overwrite* is ``False`` the conversion
        is skipped.  Raises ``FileNotFoundError`` if the source NetCDF is not
        present — call :meth:`download_month` first.

        Parameters
        ----------
        year, month:
            The year and calendar month (1–12) to convert.
        overwrite:
            Re-create the CSV even if it already exists.

        Returns
        -------
        Path
            Path to the resulting CSV file.
        """
        csv_path = self._csv_path(year, month)
        if csv_path.exists() and not overwrite:
            logger.info(
                "Skipping CSV conversion for %04d-%02d (already exists)",
                year, month,
            )
            return csv_path

        nc_path = self._output_path(year, month)
        if not nc_path.exists():
            raise FileNotFoundError(
                f"NetCDF source not found for {year}-{month:02d}: {nc_path}. "
                "Run download_month() first."
            )

        self._nc_to_csv(nc_path, csv_path)
        return csv_path

    def ensure_month(
        self,
        year: int,
        month: int,
        *,
        overwrite: bool = False,
    ) -> tuple[Path, Path]:
        """Ensure ERA5 data for *year*/*month* is both downloaded and converted.

        Each step is guarded by its own existence check, so neither the CDS
        download nor the CSV conversion is repeated if the output file already
        exists.

        Parameters
        ----------
        year, month:
            The year and calendar month (1–12) to process.
        overwrite:
            Passed through to both :meth:`download_month` and
            :meth:`convert_month_to_csv`.

        Returns
        -------
        tuple[Path, Path]
            ``(nc_path, csv_path)``
        """
        nc_path  = self.download_month(year, month, overwrite=overwrite)
        csv_path = self.convert_month_to_csv(year, month, overwrite=overwrite)
        return nc_path, csv_path

    def ensure_all(self, *, overwrite: bool = False) -> List[tuple[Path, Path]]:
        """Ensure every month in the configured date range is downloaded and converted.

        Parameters
        ----------
        overwrite:
            Passed through to :meth:`ensure_month`.

        Returns
        -------
        list of tuple[Path, Path]
            A ``(nc_path, csv_path)`` pair for every processed month.
        """
        results: List[tuple[Path, Path]] = []
        for year in range(self.start_year, self.end_year + 1):
            for month in range(1, 13):
                pair = self.ensure_month(year, month, overwrite=overwrite)
                results.append(pair)
        return results
