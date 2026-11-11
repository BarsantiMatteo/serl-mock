"""scripts/publish_release.py: copies a dataset into <sources root>/<kind>/<name>/
with its holiday CSVs and a release record, and never overwrites a release."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts import publish_release as pr


@pytest.fixture
def mock_tree(tmp_path, monkeypatch):
    mock = tmp_path / "mock"
    ds = mock / "edition08_tiny"
    (ds / "mock_internal").mkdir(parents=True)
    (ds / "serl_participant_summary_edition08.csv").write_text("PUPRN\nA\n", encoding="utf-8")
    (ds / "mock_internal" / "serl_mock_config.yaml").write_text("seed: 1\n", encoding="utf-8")
    raw = mock / "raw_from_edition08_tiny"
    raw.mkdir()
    (raw / "SERL_half_hourly_2024_01.csv").write_text("x\n", encoding="utf-8")
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / pr.HOLIDAY_FILES[0]).write_text("date\n2024-01-01\n", encoding="utf-8")
    monkeypatch.setattr(pr, "MOCK_DIR", mock)
    monkeypatch.setattr(pr, "REFERENCE_DIR", ref)
    return tmp_path


def test_edition_published_with_holidays_and_record(mock_tree):
    root = mock_tree / "sources"
    target = pr.publish("edition08_tiny", "mock_edition08_tiny", "edition", root)
    assert target == root / "editions" / "mock_edition08_tiny"
    assert (target / pr.HOLIDAY_FILES[0]).is_file()          # added next to the edition's files
    meta = json.loads((target / "release.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "edition" and meta["source"]["dataset"] == "edition08_tiny"
    assert meta["source"]["config"] == "mock_internal/serl_mock_config.yaml"
    assert meta["added_files"] == [pr.HOLIDAY_FILES[0]]
    files = {f["path"]: f for f in meta["files"]}
    summary = target / "serl_participant_summary_edition08.csv"
    assert files[summary.name]["sha256"] == hashlib.sha256(summary.read_bytes()).hexdigest()
    assert "release.json" not in files and "RELEASE.md" not in files
    assert (target / "RELEASE.md").read_text(encoding="utf-8").startswith("# mock_edition08_tiny")


def test_raw_goes_to_raw_without_holidays(mock_tree):
    target = pr.publish("raw_from_edition08_tiny", "mock_raw", "raw", mock_tree / "sources")
    assert target.parent.name == "raw"
    assert not (target / pr.HOLIDAY_FILES[0]).exists()


def test_release_never_overwritten(mock_tree):
    root = mock_tree / "sources"
    pr.publish("edition08_tiny", "x", "edition", root)
    with pytest.raises(FileExistsError):
        pr.publish("edition08_tiny", "x", "edition", root)


def test_missing_dataset(mock_tree):
    with pytest.raises(FileNotFoundError):
        pr.publish("nope", "x", "edition", mock_tree / "sources")


def test_cli_kind_and_name_defaults(mock_tree, monkeypatch):
    monkeypatch.setenv("SERL_SOURCES_ROOT", str(mock_tree / "env_root"))
    assert pr.main(["--dataset", "raw_from_edition08_tiny"]) == 0
    assert (mock_tree / "env_root" / "raw" / "mock_raw_from_edition08_tiny" / "release.json").is_file()
    assert pr.main(["--dataset", "raw_from_edition08_tiny"]) == 1    # already published
