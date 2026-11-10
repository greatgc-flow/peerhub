"""Profile cards (0.24.5): AI-readable use_for/avoid_for/traits bound to the packaged model binding."""
import json
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
LESSONS = {item["id"] for item in tomllib.loads((REPO / "peerhub" / "config_data" / "lessons.toml").read_text(encoding="utf-8"))["lessons"]}
PACKAGED = {k: v for k, v in TOML["profiles"].items() if k != "*"}
ONE_PER_PEER = ("cc.effort", "cx.effort", "ag.effort")


def _overlay(tmp_path, monkeypatch, body: str) -> Path:
    home = tmp_path / "cfg"
    home.mkdir()
    (home / "models.toml").write_text("schema_version = 1\n" + body, encoding="utf-8")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))
    return tmp_path


def test_every_packaged_profile_has_a_complete_card():
    for pid, p in PACKAGED.items():
        assert isinstance(p.get("use_for"), str) and p["use_for"], pid
        assert isinstance(p.get("avoid_for"), str) and p["avoid_for"], pid
        assert isinstance(p.get("traits"), list) and p["traits"], pid
        assert isinstance(p.get("evidence"), list) and p["evidence"], pid
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.get("card_checked", "")), pid


def test_packaged_card_evidence_resolves():
    for pid, p in PACKAGED.items():
        for ref in p["evidence"]:
            if ref.startswith("#"):
                assert re.fullmatch(r"#\d+", ref), (pid, ref)
            elif "/" in ref:
                assert (REPO / ref).exists(), (pid, ref)
            else:
                assert ref in LESSONS, (pid, ref)


def test_packaged_cards_never_restate_pools():
    for pid, p in PACKAGED.items():
        text = " ".join([p["use_for"], p["avoid_for"], *p["traits"]])
        for pool in p["pools"]:
            assert pool not in text, (pid, pool)


@pytest.mark.parametrize("pid", sorted(PACKAGED))
def test_packaged_card_is_effective_and_sourced(tmp_path, pid):
    from peerhub.extensions.adapters.profiles import inspect_profile

    card = inspect_profile(pid, tmp_path)["card"]
    assert card["use_for"] == {"value": PACKAGED[pid]["use_for"], "source": "packaged"}
    assert card["evidence"]["value"] == PACKAGED[pid]["evidence"]


@pytest.mark.parametrize("pid", ONE_PER_PEER)
@pytest.mark.parametrize("key", ["model", "reasoning_effort", "selection_mode"])
def test_binding_change_drops_inherited_card(tmp_path, monkeypatch, pid, key):
    from peerhub.extensions.adapters.profiles import inspect_profile

    value = {"model": '"some-other-model"', "reasoning_effort": '"medium"', "selection_mode": '"cli_default"'}[key]
    if key == "reasoning_effort" and PACKAGED[pid].get("reasoning_effort") == "medium":
        value = '"low"'
    ws = _overlay(tmp_path, monkeypatch, f'[profiles."{pid}"]\n{key} = {value}\n')
    assert inspect_profile(pid, ws)["card"] is None


@pytest.mark.parametrize("pid", ONE_PER_PEER)
def test_repeating_the_packaged_binding_keeps_the_card(tmp_path, monkeypatch, pid):
    from peerhub.extensions.adapters.profiles import inspect_profile

    ws = _overlay(tmp_path, monkeypatch, f'[profiles."{pid}"]\nmodel = "{PACKAGED[pid]["model"]}"\n')
    assert inspect_profile(pid, ws)["card"]["use_for"]["source"] == "packaged"


@pytest.mark.parametrize("pid", ONE_PER_PEER)
def test_overlay_card_after_binding_change_needs_its_own_evidence(tmp_path, monkeypatch, pid):
    from peerhub.extensions.adapters.profiles import inspect_profile

    ws = _overlay(tmp_path, monkeypatch, f'[profiles."{pid}"]\nmodel = "x-new"\nuse_for = "my notes"\n')
    with pytest.raises(ValueError, match="evidence"):
        inspect_profile(pid, ws)
    (tmp_path / "cfg" / "models.toml").write_text(
        f'schema_version = 1\n[profiles."{pid}"]\nmodel = "x-new"\nuse_for = "my notes"\n'
        'evidence = ["#1"]\ncard_checked = "2026-11-10"\n', encoding="utf-8")
    card = inspect_profile(pid, ws)["card"]
    assert card["use_for"] == {"value": "my notes", "source": "global"}
    assert "avoid_for" not in card and "traits" not in card


@pytest.mark.parametrize("body,match", [
    ('use_for = "' + "x" * 81 + '"', "use_for"),
    ('traits = ["a", "b", "c", "d"]', "traits"),
    ('traits = ["' + "x" * 41 + '"]', "traits"),
    ('evidence = ["no spaces allowed here"]', "evidence"),
    ('evidence = []', "evidence"),
    ('card_checked = "10/11/2026"', "card_checked"),
])
def test_card_format_is_validated(tmp_path, monkeypatch, body, match):
    from peerhub.extensions.adapters.profiles import inspect_profile

    ws = _overlay(tmp_path, monkeypatch, f'[profiles."cx.effort"]\n{body}\n')
    with pytest.raises(ValueError, match=match):
        inspect_profile("cx.effort", ws)


def test_profile_cli_shows_cards(tmp_path, capsys):
    from peerhub.cli.app import main

    assert main(["profile", "show", "cc.deepthink", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "use_for" in out and PACKAGED["cc.deepthink"]["use_for"] in out
    assert main(["profile", "list", "--workspace", str(tmp_path)]) == 0
    assert "USE FOR" in capsys.readouterr().out
    assert main(["profile", "list", "--workspace", str(tmp_path), "--format", "json"]) == 0
    listed = json.loads(capsys.readouterr().out)["profiles"]
    assert all(p["card"]["evidence"]["value"] for p in listed if p["profile"] in PACKAGED)


def test_card_is_never_older_than_the_binding_check():
    """A packaged model or effort change must re-check the card (claims were measured on the old binding)."""
    for pid, p in PACKAGED.items():
        assert p["card_checked"] >= p["checked"], pid


@pytest.mark.parametrize("pid", ONE_PER_PEER)
def test_explain_profile_id_prints_the_effective_card(tmp_path, monkeypatch, capsys, pid):
    from peerhub.cli.recovery import run_explain_cli

    monkeypatch.chdir(tmp_path)
    assert run_explain_cli(pid) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Profile: {pid}") and PACKAGED[pid]["use_for"] in out and "[packaged]" in out
    assert run_explain_cli(pid, json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["profile"]["card"]["use_for"]["value"] == PACKAGED[pid]["use_for"]


def test_explain_distinguishes_unknown_profile_and_no_card(tmp_path, monkeypatch, capsys):
    from peerhub.cli.recovery import run_explain_cli

    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.extra"]\nselection_mode = "pinned"\nmodel = "m"\npools = ["X-7D"]\n'
                  'pools_source = "unverified"\n')
    monkeypatch.chdir(ws)
    assert run_explain_cli("cx.extra") == 0
    assert "No card" in capsys.readouterr().out
    assert run_explain_cli("cc.does-not-exist") == 1
    assert "unknown profile" in capsys.readouterr().err
    assert run_explain_cli("cx.My_Custom") == 1
    assert "unknown profile" in capsys.readouterr().err
    assert run_explain_cli("ag recipe") == 0  # lessons fallback unchanged
