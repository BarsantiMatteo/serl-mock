"""`epc_quirks.zero_floor_area_share` plants EPC floor areas of 0 (a real
EPC's "not recorded") without changing any other generated value."""
from __future__ import annotations

import pandas as pd
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock import paths as paths_module
from tests._shared import TINY_CONFIG

ONLY_EPC = {k: False for k in ("hh_smart_meter", "daily_smart_meter", "rt_summary", "weather", "survey",
                               "covid19_survey", "follow_up_survey", "harmonised_survey", "survey_2025",
                               "exporters_list")}


def _epc(tmp_path, monkeypatch, label: str, **extra) -> pd.DataFrame:
    root = tmp_path / "mock"
    monkeypatch.setattr(paths_module, "MOCK_DIR", root)
    config = {**TINY_CONFIG, "n_households": 400, "output_label": label, "generate": ONLY_EPC, **extra}
    cfg_path = tmp_path / f"{label}.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    run_all(skip_weather=True, config_path=cfg_path)
    return pd.read_csv(root / label / "serl_epc_data_edition08.csv")


def test_zero_floor_area_planted_only_where_configured(tmp_path, monkeypatch):
    plain = _epc(tmp_path, monkeypatch, "plain")
    quirky = _epc(tmp_path, monkeypatch, "quirky", epc_quirks={"zero_floor_area_share": 0.1})

    assert (plain["totalFloorArea"] == 0).sum() == 0
    zeros = quirky["totalFloorArea"] == 0
    assert 0 < zeros.sum() < 0.25 * quirky["totalFloorArea"].notna().sum()
    # Only the planted cells differ; a missing floor area stays missing.
    pd.testing.assert_frame_equal(plain[~zeros], quirky[~zeros])
    assert plain.loc[zeros, "totalFloorArea"].notna().all()
