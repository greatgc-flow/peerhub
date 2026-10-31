"""Regression tests for quick-guide output bounds and safe terminal text (#277).

No provider, store, external tools, network, or real user profiles.
"""
from __future__ import annotations

import json

import pytest

from peerhub.cli.app import main
from peerhub.cli.guide import _cell


@pytest.mark.unit
@pytest.mark.parametrize("model", [
    "X" * 5000,
    "normal\x1b[2J\nINJECTED HEADING \u202e",
    "한글 (모델) " * 500,
    "a" + "\u0301" * 10000,
    "before\u2028middle\u2029after" * 100,
])
def test_custom_profile_cannot_extend_or_inject_guide(
    model, tmp_path, monkeypatch, capsys
):
    home = tmp_path / "isolated-profile"
    home.mkdir()
    config = (
        'schema_version = 1\n'
        '[profiles."cx.standard"]\n'
        'selection_mode = "pinned"\n'
        f'model = {json.dumps(model, ensure_ascii=True)}\n'
        'pools = ["LONGPOOL" , "EXTRA", "MORE", "EVENMORE"]\n'
    )
    (home / "models.toml").write_text(config, encoding="utf-8")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))

    assert main(["guide"]) == 0
    output = capsys.readouterr().out
    table = output.split("Profiles (defaulting to .standard first):\n", 1)[1].split(
        "Two-Asks Session Reuse Example:", 1
    )[0]
    assert len(output.splitlines()) <= 60
    assert all(len(line) <= 100 for line in table.splitlines())
    assert len(output.encode("utf-8")) < 8000
    assert "\x1b" not in output
    assert "\u202e" not in output
    assert "\u2028" not in output
    assert "\u2029" not in output
    assert "INJECTED HEADING" not in output
    assert "~" in table or len(model) <= 24

    # The underlying value used for actual model selection is *not* truncated.
    from peerhub.extensions.adapters.profiles import resolve_profile
    actual, _ = resolve_profile("cx", "cx.standard", tmp_path)
    assert actual == model


@pytest.mark.unit
def test_guide_cell_bounds_characters_and_sanitizes_controls():
    assert _cell("한글", 4) == "한글  "
    assert _cell("한글모델명", 4) == "한글모~"
    assert _cell("a" + "\u0301" * 10000, 24) == "a" + "\u0301" * 22 + "~"
    assert _cell("a\u2028b\u2029c\x1b\u202e", 7) == "a?b?c??"
    assert len(_cell("abc", 5)) == 5
    assert _cell("prefix\x1b[2J", 14).find("\x1b") == -1
    assert _cell("x" * 100, 8) == "xxxxxxx~"


@pytest.mark.unit
def test_guide_load_failure_is_sanitized_and_bounded(tmp_path, monkeypatch, capsys):
    from peerhub.extensions.adapters import profiles

    def fail_list(workspace):
        raise RuntimeError("disk unreadable\u2028\x1b\u202e" + "X" * 5000)

    monkeypatch.setattr(profiles, "list_profiles", fail_list)

    assert main(["guide"]) == 0
    output = capsys.readouterr().out
    assert "error loading profiles:" in output
    message = output.split("error loading profiles: ", 1)[1].split(")", 1)[0]
    assert message.startswith("RuntimeError: disk unreadable???")
    assert len(message) == 120
    assert message.endswith("~")
    assert len(output.splitlines()) <= 60
