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
