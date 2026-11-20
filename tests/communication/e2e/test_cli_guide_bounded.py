"""Regression tests for quick-guide output bounds and safe terminal text (#277, reworked for 0.25.0 D5).

The guide no longer prints a profile table: nothing in a profile file can reach it, and it stays under 1600 bytes.
No provider, store, external tools, network, or real user profiles.
"""
from __future__ import annotations

import json

import pytest

from peerhub.cli.app import main


@pytest.mark.unit
@pytest.mark.parametrize("model", [
    "X" * 5000,
    "normal\x1b[2J\nINJECTED HEADING ‮",
    "한글 (모델) " * 500,
    "a" + "́" * 10000,
    "before middle after" * 100,
])
def test_custom_profile_cannot_extend_or_inject_guide(model, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "none"))
    assert main(["guide"]) == 0
    baseline = capsys.readouterr().out

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
    assert output == baseline
    assert len(output.encode("utf-8")) <= 1600
    assert "\x1b" not in output and "INJECTED HEADING" not in output
    assert not {"‮", " ", " "} & set(output)

    # The value used for actual model selection is the overlay's, untouched by the guide.
    from peerhub.extensions.adapters.profiles import resolve_profile
    actual, _ = resolve_profile("cx", "cx.standard", tmp_path)
    assert actual == model


@pytest.mark.unit
def test_guide_does_not_call_the_profile_loader(tmp_path, monkeypatch, capsys):
    from peerhub.extensions.adapters import profiles

    def fail_list(workspace):
        raise RuntimeError("disk unreadable \x1b‮" + "X" * 5000)

    monkeypatch.setattr(profiles, "list_profiles", fail_list)
    assert main(["guide"]) == 0
    output = capsys.readouterr().out
    assert "error loading profiles" not in output and "disk unreadable" not in output
    assert len(output.encode("utf-8")) <= 1600
