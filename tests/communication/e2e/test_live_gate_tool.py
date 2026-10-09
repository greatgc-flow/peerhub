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
    assert [c[0][len(c[0]) - c[0][::-1].index("-m")] for c in calls] == [live_gate.STAGE_SELECTIONS[stage] for stage in ("live", "slow", "e2e")]
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


def test_stage_selections_are_disjoint_and_preserve_union():
    from itertools import product
    for live, slow, e2e in product((False, True), repeat=3):
        names = {"live": live, "slow": slow, "e2e": e2e}
        selected = [eval(expr, {"__builtins__": {}}, names)
                    for expr in live_gate.STAGE_SELECTIONS.values()]
        assert sum(selected) == int(live or slow or e2e)


def test_single_peer_live_stage_allows_missing_binaries_and_filters(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(live_gate.shutil, "which", lambda name: None)
    monkeypatch.setattr(live_gate.subprocess, "check_output", lambda cmd, **k: "abc123" if "rev-parse" in cmd else "")
    calls = []
    monkeypatch.setattr(live_gate.subprocess, "call", lambda cmd, **k: calls.append(cmd) or 0)
    assert live_gate.main(["--yes", "--only", "live", "--peer", "cx", "--out", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0][len(calls[0]) - 1 - calls[0][::-1].index("-m") + 1] == "(live and not slow and not e2e) and cx"


def test_single_peer_canary_has_separate_output_without_release_stamp(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(live_gate.shutil, "which", lambda name: None)
    monkeypatch.setattr(live_gate.subprocess, "check_output", lambda cmd, **k: "abc123" if "rev-parse" in cmd else "")
    import tools.release_evidence as re_mod
    stamps = []
    monkeypatch.setattr(re_mod, "stamp_junit", lambda *a, **k: stamps.append((a, k)))
    calls = []

    def fake_call(cmd, **kwargs):
        calls.append(cmd)
        junit = next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--junitxml="))
        Path(junit).write_text("<testsuite/>\n", encoding="utf-8")
        assert kwargs["env"]["PEERHUB_LIVE"] == "1"
        return 0

    monkeypatch.setattr(live_gate.subprocess, "call", fake_call)
    assert live_gate.main(["--yes", "--only", "canary", "--peer", "cc", "--out", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0][len(calls[0]) - 1 - calls[0][::-1].index("-m") + 1] == "(canary) and cc"
    assert (tmp_path / "canary.xml").is_file()
    assert not (tmp_path / "g3.xml").exists()
    assert stamps == []


def test_peer_filter_requires_live_or_canary_stage():
    import pytest
    with pytest.raises(SystemExit) as exc:
        live_gate.main(["--yes", "--peer", "cc"])
    assert exc.value.code == 2
