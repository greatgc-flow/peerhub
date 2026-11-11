"""#341: `model_ping --peer P --model M` pings one exact model without reading or writing any profile overlay."""
import pytest

from tools import model_ping


@pytest.fixture
def no_profiles(monkeypatch):
    from peerhub.extensions.adapters import profiles

    monkeypatch.setattr(profiles, "list_profiles", lambda workspace: pytest.fail("must not read profiles/overlays"))


def test_model_ping_exact_model(no_profiles, monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(model_ping.PINGS, "cc", lambda model, effort, cwd: calls.append((model, effort)) or (True, "claude-x", "ok"))
    assert model_ping.main(["--peer", "cc", "--model", "claude-x", "--effort", "high"]) == 0
    assert calls == [("claude-x", "high")]
    out = capsys.readouterr().out
    assert "PASS" in out and "want=claude-x/high" in out and "answered=claude-x" in out


def test_model_ping_exact_model_mismatch_fails(no_profiles, monkeypatch, capsys):
    monkeypatch.setitem(model_ping.PINGS, "cx", lambda model, effort, cwd: (True, "other/-", "MODEL MISMATCH"))
    assert model_ping.main(["--peer", "cx", "--model", "m1"]) == 1
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--model", "m"], ["--model", "m", "--peer", "cc", "cc.standard"], ["--peer", "cc", "--effort", "high"]])
def test_model_ping_model_option_validation(argv, no_profiles, monkeypatch, capsys):
    monkeypatch.setattr(model_ping, "PINGS", {"cc": lambda *a: pytest.fail("must not call CLI")})
    with pytest.raises(SystemExit) as exc:
        model_ping.main(argv)
    assert exc.value.code == 2 and capsys.readouterr().err
