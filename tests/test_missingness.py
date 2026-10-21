"""Synthetic missingness (src/serl_mock/missingness.py).

Runs the smart-meter steps on the tiny fixture with a `missingness:` block
and checks the HH files, daily files and rt_summary against the ground
truth the run writes to mock_internal/. Also checks that switching
missingness on leaves every untouched reading exactly as generated without
it (separate rngs), comparing against the session's plain fixture run.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts.generate_mock_data import run_all
from src.serl_mock.missingness import MissingnessPlanner, parse_patterns
from tests._shared import TINY_CONFIG, TINY_CONFIG_EDITION09, _read_table

NON_SMART_METER = {
    "weather": False, "epc": False, "survey": False, "covid19_survey": False,
    "follow_up_survey": False, "harmonised_survey": False, "survey_2025": False,
    "participant_summary": False, "exporters_list": False,
}

MISSINGNESS = {
    "enabled": True,
    "patterns": [
        {"name": "outage", "type": "outage", "households": {"share": 1.0},
         "per_household": [1, 1], "length_days": [[2, 3, 1]], "effect": "drop_row"},
        {"name": "event", "type": "event", "households": {"share": 1.0},
         "start": "2021-02-10 06:00", "end": "2021-02-10 09:00", "effect": "drop_row"},
        {"name": "late", "type": "late_start", "households": {"share": 1.0},
         "length_days": [[3, 3, 1]], "effect": "drop_row"},
        {"name": "export_fail", "type": "scattered", "households": {"traits": {"has_export_meter": True}},
         "time_window": {"start": "10:00", "end": "14:00"}, "prob_per_slot": {"low": 0.5, "high": 0.5},
         "streams": ["elec_exp", "elec_react_exp"], "effect": "missing"},
        {"name": "gas_missing", "type": "outage", "households": {"traits": {"has_gas_meter": True}},
         "per_household": [1, 1], "length_days": [[1, 1, 1]], "streams": ["gas"], "effect": "missing"},
        {"name": "gas_max", "type": "scattered", "households": {"traits": {"has_gas_meter": True}},
         "prob_per_slot": {"low": 0.01, "high": 0.01}, "streams": ["gas"], "effect": "max_read"},
        {"name": "react_flag", "type": "scattered", "households": {"share": 1.0},
         "prob_per_slot": {"low": 0.01, "high": 0.01}, "streams": ["elec_react_imp"],
         "effect": {"type": "flag", "code": -2}},
        {"name": "dups", "type": "scattered", "households": {"share": 1.0},
         "prob_per_slot": {"low": 0.001, "high": 0.001}, "effect": "duplicate"},
        {"name": "offgrid", "type": "scattered", "households": {"share": 1.0},
         "prob_per_slot": {"low": 0.001, "high": 0.001}, "effect": {"type": "off_grid", "minutes": 7}},
        {"name": "switched_off", "type": "outage", "enabled": False, "per_household": [1, 1],
         "length_days": [[1, 1, 1]], "effect": "drop_row"},
    ],
}


def _run(tmp_path_factory, base_cfg: dict, name: str) -> Path:
    cfg = {**base_cfg, "generate": NON_SMART_METER, "missingness": MISSINGNESS}
    cfg_path = tmp_path_factory.mktemp(f"config_{name}") / "serl_mock.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    out = tmp_path_factory.mktemp(f"mock_{name}")
    run_all(skip_weather=True, config_path=cfg_path, output_dir=out)
    return out


@pytest.fixture(scope="module")
def gappy(tmp_path_factory) -> Path:
    return _run(tmp_path_factory, TINY_CONFIG, "gappy")


def _hh(out: Path, edition: str = "08") -> pd.DataFrame:
    files = sorted((out / f"serl_smart_meter_hh_edition{edition}").glob("serl_half_hourly_*"))
    assert files
    return pd.concat([_read_table(f) for f in files], ignore_index=True)


def _daily(out: Path) -> pd.DataFrame:
    files = sorted((out / "serl_smart_meter_daily_edition08").glob("*.csv"))
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def _truth(out: Path) -> pd.DataFrame:
    gt = pd.read_csv(out / "mock_internal" / "missingness_ground_truth.csv")
    gt["start_utc"] = pd.to_datetime(gt["start_utc"], utc=True)
    gt["end_utc"] = pd.to_datetime(gt["end_utc"], utc=True)
    return gt


def _end_stamps(hh: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(hh["Read_date_time_UTC"], utc=True)


def _planted_stamps(gt: pd.DataFrame, pattern: str) -> pd.DataFrame:
    """(PUPRN, end stamp) of every planted half-hour of `pattern`."""
    rows = []
    for r in gt[gt["pattern"] == pattern].itertuples():
        ends = pd.date_range(r.start_utc + pd.Timedelta(minutes=30), periods=r.n_slots, freq="30min")
        rows.append(pd.DataFrame({"PUPRN": r.PUPRN, "end": ends}))
    return pd.concat(rows, ignore_index=True)


def test_ground_truth_written(gappy: Path):
    gt = _truth(gappy)
    summary = pd.read_csv(gappy / "mock_internal" / "missingness_patterns.csv")
    assert list(summary["pattern"]) == ["outage", "event", "late", "export_fail", "gas_missing",
                                        "gas_max", "react_flag", "dups", "offgrid"]
    assert set(gt["pattern"]) <= set(summary["pattern"])
    assert (gt["end_utc"] - gt["start_utc"] == gt["n_slots"] * pd.Timedelta(minutes=30)).all()


def test_dropped_rows_are_absent(gappy: Path):
    hh = _hh(gappy)
    present = set(zip(hh["PUPRN"], _end_stamps(hh)))
    gt = _truth(gappy)
    for pattern in ("outage", "event", "late"):
        planted = _planted_stamps(gt, pattern)
        assert len(planted) > 0, pattern
        assert not any((p, e) in present for p, e in zip(planted["PUPRN"], planted["end"])), pattern


def test_event_and_late_start_shape(gappy: Path):
    gt = _truth(gappy)
    traits = pd.read_csv(gappy / "mock_internal" / "household_traits.csv")
    event = gt[gt["pattern"] == "event"]
    assert set(event["PUPRN"]) == set(traits["PUPRN"])
    assert (event["start_utc"] == pd.Timestamp("2021-02-10 06:00", tz="UTC")).all()   # GMT: local = UTC
    late = gt[gt["pattern"] == "late"]
    assert (late["start_utc"] == pd.Timestamp("2021-01-01", tz="UTC")).all()
    assert (late["n_slots"] == 3 * 48).all()


def test_export_failures_respect_traits_and_window(gappy: Path):
    hh = _hh(gappy)
    traits = pd.read_csv(gappy / "mock_internal" / "household_traits.csv").set_index("PUPRN")
    exporters = set(traits.index[traits["has_export_meter"] == 1])
    bad = hh[hh["Elec_act_exp_flag"] == 0]
    assert len(bad) > 0 and set(bad["PUPRN"]) <= exporters
    start_local = (_end_stamps(bad) - pd.Timedelta(minutes=30)).dt.tz_convert("Europe/London").dt.strftime("%H:%M")
    assert ((start_local >= "10:00") & (start_local < "14:00")).all()
    # both export streams fail together; import untouched on those rows
    assert (bad["Elect_react_exp_flag"] == 0).all()
    assert bad["Elec_act_exp_hh_Wh"].isna().all() and bad["Elec_react_exp_hh_varh"].isna().all()
    assert (bad["Elec_act_imp_flag"] == 1).all()


def test_max_read_and_flag_effects(gappy: Path):
    hh = _hh(gappy)
    maxed = hh[hh["Gas_flag"] == -1]
    assert len(maxed) > 0
    assert (maxed["Gas_hh_m3"] == 16777.215).all()
    assert (maxed["Gas_hh_Wh"] == round(16777.215 * 39.5 * 1000 / 3.6)).all()
    flagged = hh[hh["Elect_react_imp_flag"] == -2]
    assert len(flagged) > 0 and flagged["Elec_react_imp_hh_varh"].notna().all()
    # a max read is not valid, so its day has no gas HH sum
    daily = _daily(gappy)
    eff = (_end_stamps(maxed) - pd.Timedelta(minutes=1)).dt.tz_convert("Europe/London").dt.strftime("%Y-%m-%d")
    days = set(zip(maxed["PUPRN"], eff))
    rows = daily[[(p, d) in days for p, d in zip(daily["PUPRN"], daily["Read_date_effective_local"])]]
    assert len(rows) > 0
    assert not rows["Valid_hh_sum_or_daily_gas"].astype(bool).any()
    assert rows["Gas_hh_sum_m3"].isna().all()
    rt = pd.read_csv(gappy / "serl_smart_meter_rt_summary_edition08.csv")
    gpf = rt[(rt["deviceType"] == "GPF") & (rt["readType"] == "AI")]
    assert gpf["maxRead"].sum() == len(maxed)


def test_gas_missing_reads(gappy: Path):
    hh = _hh(gappy)
    missing = hh[hh["Gas_flag"] == 0]
    assert len(missing) > 0
    assert missing["Gas_hh_Wh"].isna().all() and missing["Gas_hh_m3"].isna().all()
    assert (missing["Elec_act_imp_flag"] == 1).all()


def test_duplicates_and_off_grid(gappy: Path, generated_output_dir: Path):
    hh = _hh(gappy)
    gt = _truth(gappy)
    n_dup = int(hh.duplicated(["PUPRN", "Read_date_time_UTC"]).sum())
    assert n_dup == int(gt.loc[gt["pattern"] == "dups", "n_slots"].sum()) > 0
    base = _hh(generated_output_dir)
    n_offgrid_planted = int(gt.loc[gt["pattern"] == "offgrid", "n_slots"].sum())
    assert n_offgrid_planted > 0
    assert hh["HH"].isna().sum() == base["HH"].isna().sum() + n_offgrid_planted
    moved = hh[hh["HH"].isna() & (hh["Elec_act_imp_flag"] == -5)]
    assert (_end_stamps(moved).dt.minute % 30 != 0).all()


def test_untouched_readings_unchanged(gappy: Path, generated_output_dir: Path):
    """Missingness has its own rngs: every reading it didn't touch matches
    the run without it, value for value."""
    key = ["PUPRN", "Read_date_time_UTC"]
    cols = ["Elec_act_imp_hh_Wh", "Elec_act_exp_hh_Wh", "Gas_hh_Wh"]
    gappy_hh = _hh(gappy).drop_duplicates(key)
    gappy_hh = gappy_hh[(gappy_hh["Elec_act_exp_flag"] == 1) & (gappy_hh["Gas_flag"] == 1)]
    merged = gappy_hh[key + cols].merge(_hh(generated_output_dir)[key + cols], on=key, suffixes=("", "_base"))
    assert len(merged) > 0.9 * len(gappy_hh)
    for c in cols:
        assert (merged[c].astype(float) == merged[f"{c}_base"].astype(float)).all(), c


def test_daily_follows_planted_gaps(gappy: Path):
    daily = _daily(gappy)
    gt = _truth(gappy)
    late_days = daily[daily["Read_date_effective_local"].isin(["2021-01-01", "2021-01-02", "2021-01-03"])]
    assert not late_days["Valid_hh_sum_or_daily_elec"].astype(bool).any()
    assert late_days["Elec_act_imp_hh_sum_Wh"].isna().all()
    gas = gt[gt["pattern"] == "gas_missing"].iloc[0]
    day = (gas["start_utc"] + pd.Timedelta(hours=12)).tz_convert("Europe/London").strftime("%Y-%m-%d")
    row = daily[(daily["PUPRN"] == gas["PUPRN"]) & (daily["Read_date_effective_local"] == day)]
    assert not bool(row["Valid_hh_sum_or_daily_gas"].iloc[0])


def test_rt_summary_counts_absent_and_flag0_as_missing(gappy: Path, generated_output_dir: Path):
    rt = pd.read_csv(gappy / "serl_smart_meter_rt_summary_edition08.csv")
    base = pd.read_csv(generated_output_dir / "serl_smart_meter_rt_summary_edition08.csv")
    assert (base["missing"] == 0).all()
    ai = rt[(rt["deviceType"] == "ESME") & (rt["readType"] == "AI")].set_index("PUPRN")
    gt = _truth(gappy)
    dropped = gt[gt["pattern"].isin(["outage", "event", "late"])].groupby("PUPRN")["n_slots"].sum()
    # absent stamps; off-grid moves add no absent row (the moved row is still there)
    assert (ai.loc[dropped.index, "missing"] >= dropped).all()
    assert (ai["percMissing"] > 0).all()
    gpf = rt[(rt["deviceType"] == "GPF") & (rt["readType"] == "AI")].set_index("PUPRN")
    gas_planted = gt[gt["pattern"] == "gas_missing"].groupby("PUPRN")["n_slots"].sum()
    assert (gpf.loc[gas_planted.index, "missing"] >= gas_planted).all()


def test_edition09_harmonised_output(tmp_path_factory):
    out = _run(tmp_path_factory, TINY_CONFIG_EDITION09, "gappy09")
    hh = _hh(out, "09")
    assert "Gas_hh_m3" not in hh.columns
    missing = hh[hh["Gas_flag"] == 0]
    assert len(missing) > 0 and missing["Gas_hh_Wh"].isna().all()
    assert (out / "mock_internal" / "missingness_ground_truth.csv").exists()


# ---------- planner and config, no pipeline ----------

def _planner(seed=1):
    patterns = parse_patterns(MISSINGNESS, {"has_export_meter", "has_gas_meter"})
    traits = pd.DataFrame({"has_export_meter": [1, 0, 1], "has_gas_meter": [1, 1, 0]},
                          index=["A", "B", "C"])
    return MissingnessPlanner(patterns, ["A", "B", "C"], traits, 2021, 2021, seed)


def test_planner_is_deterministic():
    a, b, c = _planner(), _planner(), _planner(seed=2)
    assert a.runs == b.runs
    assert all(np.array_equal(x, y) for x, y in zip(a.selected, b.selected))
    assert a.runs != c.runs


def test_disabled_block_plants_nothing():
    assert parse_patterns(None) == []
    assert parse_patterns({**MISSINGNESS, "enabled": False}) == []
    assert "switched_off" not in [p.name for p in parse_patterns(MISSINGNESS)]


@pytest.mark.parametrize("bad, msg", [
    ({"name": "x", "type": "outage", "per_household": [1, 1], "length_days": [[1, 2, 1]],
      "effect": "drop_row", "streams": ["gas"]}, "whole row"),
    ({"name": "x", "type": "scattered", "prob_per_slot": {"low": 0.1}, "effect": "missing"}, "needs `streams`"),
    ({"name": "x", "type": "scattered", "prob_per_slot": {"low": 0.1}, "streams": ["water"],
      "effect": "missing"}, "unknown streams"),
    ({"name": "x", "type": "event", "start": "2021-01-02", "end": "2021-01-01", "effect": "drop_row"}, "before"),
    ({"name": "x", "type": "scattered", "prob_per_slot": {"low": 0.1},
      "effect": {"type": "off_grid", "minutes": 30}}, "multiple of 30"),
    ({"name": "x", "type": "sometimes", "effect": "drop_row"}, "type must be"),
    ({"name": "x", "type": "scattered", "prob_per_slot": {"low": 0.1}, "effect": "drop_row",
      "households": {"traits": {"has_dog": True}}}, "unknown household trait"),
])
def test_config_validation(bad, msg):
    with pytest.raises(ValueError, match=msg):
        parse_patterns({"enabled": True, "patterns": [bad]}, {"has_pv", "has_gas_meter"})
