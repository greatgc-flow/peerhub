"""Item 13 (dotdir consolidation, ratified 2026-09-09): `peerhub config
validate` exercises every config family's real loader and reports
OK/ERROR, surfacing item 9's winning-layer diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.application.config_validate import validate_workspace_config


def _report(reports, name):
    for report in reports:
        if report.name == name:
            return report
    raise AssertionError(f"no report named {name!r} in {[r.name for r in reports]}")


def test_validate_fresh_workspace_reports_every_family_ok(tmp_path: Path) -> None:
    reports = validate_workspace_config(tmp_path)

    names = [report.name for report in reports]
    assert names == ["models.toml", "ask.toml", "arbiter.json", "proposals.json"]
    assert all(report.ok for report in reports)
    assert "absent" in _report(reports, "models.toml").detail


def test_validate_reports_valid_models_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_home = tmp_path / "global"
    global_home.mkdir()
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))
    (global_home / "models.toml").write_text("[peers.cc]\n", encoding="utf-8")

    report = _report(validate_workspace_config(tmp_path), "models.toml")

    assert report.ok is True
    assert "parses OK" in report.detail


def test_validate_reports_malformed_models_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_home = tmp_path / "global"
    global_home.mkdir()
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))
    (global_home / "models.toml").write_text("this is not [valid toml", encoding="utf-8")

    report = _report(validate_workspace_config(tmp_path), "models.toml")

    assert report.ok is False


def test_validate_reports_arbiter_schema_version_error(tmp_path: Path) -> None:
    config_dir = tmp_path / ".peerhub"
    config_dir.mkdir()
    (config_dir / "arbiter.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8"
    )

    report = _report(validate_workspace_config(tmp_path), "arbiter.json")

    assert report.ok is False
    assert "schema_version" in report.detail


def test_validate_reports_proposals_conflict(tmp_path: Path) -> None:
    legacy_dir = tmp_path / ".peerhub"
    current_dir = legacy_dir / "config"
    current_dir.mkdir(parents=True)
    (legacy_dir / "proposals.json").write_text("{}", encoding="utf-8")
    (current_dir / "proposals.json").write_text("{}", encoding="utf-8")

    report = _report(validate_workspace_config(tmp_path), "proposals.json")

    assert report.ok is False
    assert "config migrate" in report.detail


def test_validate_surfaces_winning_layer_diagnostics(tmp_path: Path) -> None:
    config_dir = tmp_path / ".peerhub" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "arbiter.json").write_text(
        json.dumps({"schema_version": 1, "enabled": True}), encoding="utf-8"
    )

    report = _report(validate_workspace_config(tmp_path), "arbiter.json")

    assert report.ok is True
    assert report.winning_layer == {"enabled": "workspace"}


def test_config_family_report_as_dict_round_trips() -> None:
    from peerhub.application.config_validate import ConfigFamilyReport

    report = ConfigFamilyReport("ask.toml", True, "resolves OK", {"a": "workspace"})

    assert report.as_dict() == {
        "name": "ask.toml",
        "ok": True,
        "detail": "resolves OK",
        "winning_layer": {"a": "workspace"},
    }
