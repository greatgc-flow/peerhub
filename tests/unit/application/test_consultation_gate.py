"""Consultation gate (R4 2.1 / section 8.1): NONE and NOTIFY proceed; stricter depths fail closed."""
from pathlib import Path

import pytest

from peerhub.application.consultation_gate import ConsultationBlockedError, evaluate_consultation
from peerhub.dispatch.policy import ConsultationDepth


@pytest.fixture(autouse=True)
def isolate_global_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))


def _ws(tmp_path: Path, toml: str | None) -> Path:
    if toml is not None:
        cfg = tmp_path / ".peerhub" / "config"
        cfg.mkdir(parents=True)
        (cfg / "dispatch-policy.toml").write_text(toml, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("action", ["ask", "broadcast"])
def test_shipped_defaults_do_not_block_ordinary_ask_and_broadcast(tmp_path, action):
    d = evaluate_consultation(_ws(tmp_path, None), action)
    assert d.depth is ConsultationDepth.NONE and d.proceed and d.note is None


def test_notify_is_non_blocking_and_says_what_is_not_implemented(tmp_path):
    ws = _ws(tmp_path, '[consultation.overrides]\n"ask" = "notify"\n')
    d = evaluate_consultation(ws, "ask")
    assert d.depth is ConsultationDepth.NOTIFY and d.proceed and "not implemented" in (d.note or "")


@pytest.mark.parametrize("depth", ["review", "quorum", "unanimous"])
def test_stricter_configured_depths_fail_closed_instead_of_being_bypassed(tmp_path, depth):
    ws = _ws(tmp_path, f'[consultation.overrides]\n"ask" = "{depth}"\n')
    with pytest.raises(ConsultationBlockedError, match="refusing to bypass"):
        evaluate_consultation(ws, "ask")


def test_other_actions_keep_their_own_defaults(tmp_path):
    ws = _ws(tmp_path, None)
    assert evaluate_consultation.__module__  # sanity
    with pytest.raises(ConsultationBlockedError):
        evaluate_consultation(ws, "consensus.propose")  # shipped default: quorum


def test_invalid_config_is_an_error_not_a_silent_default(tmp_path):
    ws = _ws(tmp_path, '[consultation.overrides]\n"ask" = "sometimes"\n')
    with pytest.raises(ConsultationBlockedError, match="invalid dispatch policy"):
        evaluate_consultation(ws, "ask")
