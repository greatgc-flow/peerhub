"""The live gate spends real provider quota: it must refuse to run without consent, in CI, or without the provider CLIs."""
from __future__ import annotations

from pathlib import Path

from tools import live_gate


def test_refuses_without_yes(capsys, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert live_gate.main([]) == 2
    assert "--yes" in capsys.readouterr().err


def test_refuses_in_ci_even_with_yes(capsys, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(live_gate.subprocess, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert live_gate.main(["--yes"]) == 2
    assert "local-only" in capsys.readouterr().err


def test_refuses_when_provider_clis_missing(capsys, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(live_gate.shutil, "which", lambda name: None)
    monkeypatch.setattr(live_gate.subprocess, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert live_gate.main(["--yes"]) == 2
    assert "agy" in capsys.readouterr().err


def test_runs_each_stage_with_live_env_and_junit(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(live_gate.shutil, "which", lambda name: name)
    monkeypatch.setattr(live_gate.subprocess, "check_output", lambda cmd, **k: "abc1234567890" if "rev-parse" in cmd else "")
    calls = []
    stamps = []
    import tools.release_evidence as re_mod
    monkeypatch.setattr(re_mod, "stamp_junit", lambda path, **k: stamps.append((path.name, k)))

    def fake_call(cmd, cwd, env):
        calls.append((cmd, env["PEERHUB_LIVE"]))
        junit = next(x for x in cmd if x.startswith("--junitxml=")).split("=", 1)[1]
        Path(junit).write_text("<testsuite/>")
        return 0

    monkeypatch.setattr(live_gate.subprocess, "call", fake_call)
    assert live_gate.main(["--yes", "--out", str(tmp_path)]) == 0
    assert [c[0][len(c[0]) - c[0][::-1].index("-m")] for c in calls] == ["live", "slow", "e2e"]
    assert all(env == "1" for _, env in calls) and any("g3-slow.xml" in a for a in calls[1][0])
    assert stamps == [(n, {"commit": "abc1234567890", "gate": "G3"}) for n in ("g3.xml", "g3-slow.xml", "g3-e2e.xml")]
    calls.clear()
    assert live_gate.main(["--yes", "--only", "slow", "--out", str(tmp_path)]) == 0 and len(calls) == 1


def test_refuses_to_stamp_a_dirty_tree(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(live_gate.shutil, "which", lambda name: name)
    monkeypatch.setattr(live_gate.subprocess, "check_output", lambda cmd, **k: "deadbeef" if "rev-parse" in cmd else " M peerhub/x.py")
    monkeypatch.setattr(live_gate.subprocess, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert live_gate.main(["--yes", "--out", str(tmp_path)]) == 2
    assert "uncommitted" in capsys.readouterr().err
