"""0.28.0 review: a broken models.toml must not hide the bare diag report."""
import json
from pathlib import Path

import pytest

from peerhub.cli.app import main
from tests.communication.harness.bridge import BridgeHarness


@pytest.mark.parametrize("json_mode", [False, True])
def test_bare_diag_survives_invalid_profile_config(tmp_path, monkeypatch, capsys, json_mode):
    harness = BridgeHarness(tmp_path / "ws")
    work = tmp_path / "cwd"
    (work / ".peerhub" / "config").mkdir(parents=True)
    (work / ".peerhub" / "config" / "models.toml").write_bytes(b"\xff\xfe not utf-8")
    monkeypatch.chdir(work)
    argv = ["--db", str(harness.db_path), *(["--json"] if json_mode else []), "diag"]
    code = main(argv)
    out = capsys.readouterr()
    assert code in (0, 4), out.err
    assert out.out.strip()
    if json_mode:
        assert "sections" in json.loads(out.out)
    assert "profiles unavailable" in out.err


def test_observation_projection_counts_a_relative_reset():
    """0.28.0 review: the per-observation projection agrees with the summary after a relative reset."""
    from datetime import datetime, timezone

    from peerhub.extensions.diag_read_model import _serialize_obs_item

    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc).timestamp()
    observed = datetime.fromtimestamp(now - 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    item = {"kind": "quota", "state": "MEASURED", "subject_ref": "cc", "resource_pool_ref": "quota:cc:C-7D",
            "observed_at": observed, "captured_at": observed, "reset_in_seconds": 30,
            "payload": {"remaining_fraction": 0.0}}
    projection = _serialize_obs_item(item, now)["projection"]
    assert getattr(projection, "status", None) != "exhausted" and "exhausted" not in str(projection).lower()


def test_unreadable_profiles_leave_the_scoped_cap_to_pool_refs(monkeypatch, capsys):
    """#498: without a profile map, diag must not pass an empty one that switches the C-7D cap on F-7D off."""
    from peerhub.cli import diag_cmd
    from peerhub.extensions.adapters import profiles
    from peerhub.extensions.quota_windows import scoped_weekly_parents

    def broken(workspace):
        raise ValueError("bad models.toml")

    monkeypatch.setattr(profiles, "list_profiles", broken)
    pools_of_profile, profiles_by_pool = diag_cmd._diagnostic_profiles()
    assert (pools_of_profile, profiles_by_pool) == ({}, None)
    assert "profiles unavailable" in capsys.readouterr().err
    rows = [{"pool_ref": "quota:cc:C-7D", "profiles": (), "retired": False},
            {"pool_ref": "quota:cc:F-7D", "profiles": (), "retired": False}]
    parents = scoped_weekly_parents(rows, require_consumers=profiles_by_pool is not None)
    assert parents == {id(rows[1]): rows[0]}


def test_a_newer_unmeasured_report_never_displaces_a_measured_parent():
    """#498 review: a partial probe's UNKNOWN for C-7D one second later keeps the measured 5% reading."""
    from peerhub.extensions.diag_read_model import _select_quota_rows
    measured = {"kind": "quota", "state": "MEASURED", "subject_ref": "cc", "resource_pool_ref": "quota:cc:C-7D",
                "observed_at": "2026-11-27T12:00:00.000000Z", "payload": {"remaining_fraction": 0.05}}
    unknown = {**measured, "subject_ref": "cc2", "state": "UNKNOWN", "observed_at": "2026-11-27T12:00:01.000000Z",
               "payload": {}}
    rows = _select_quota_rows([measured, unknown], [])
    assert [row.item["state"] for row in rows] == ["MEASURED"]


def test_a_newer_complete_absent_report_still_displaces_a_measured_parent():
    from peerhub.extensions.diag_read_model import _select_quota_rows
    measured = {"kind": "quota", "state": "MEASURED", "subject_ref": "cc", "resource_pool_ref": "quota:cc:C-7D",
                "observed_at": "2026-11-27T12:00:00.000000Z", "payload": {"remaining_fraction": 0.05}}
    absent = {**measured, "state": "ABSENT", "observed_at": "2026-11-27T12:00:01.000000Z", "payload": {}}
    assert [row.item["state"] for row in _select_quota_rows([measured, absent], [])] == ["ABSENT"]
