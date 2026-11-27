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
