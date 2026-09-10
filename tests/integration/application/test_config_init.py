"""Item 13 (dotdir consolidation, ratified 2026-09-09): `peerhub config
init --scope global|workspace` scaffolds one scope's config directory and
seeds commented starter files, never overwriting an existing one."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.application.config_init import init_config_scope


def test_init_global_scope_creates_directory_and_both_starters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_home = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))

    config_home, created = init_config_scope(scope="global", workspace_root=None)

    assert config_home == global_home
    assert set(created) == {"ask.toml", "models.toml"}
    assert (global_home / "ask.toml").is_file()
    assert (global_home / "models.toml").is_file()
    # Copied verbatim from the packaged default -- a real, commented file,
    # not an empty placeholder.
    assert "schema_version" in (global_home / "ask.toml").read_text(encoding="utf-8")


def test_init_workspace_scope_creates_only_ask_toml(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    config_home, created = init_config_scope(scope="workspace", workspace_root=workspace_root)

    assert config_home == workspace_root / ".peerhub" / "config"
    assert created == ("ask.toml",)
    assert (config_home / "ask.toml").is_file()
    # No workspace models.toml -- workspace model bindings remain the sole
    # workspace model authority (ratified release gate).
    assert not (config_home / "models.toml").exists()


def test_init_never_overwrites_an_existing_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_home = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))
    global_home.mkdir()
    (global_home / "ask.toml").write_text("# my custom edits\n", encoding="utf-8")

    _, created = init_config_scope(scope="global", workspace_root=None)

    assert created == ("models.toml",)
    assert (global_home / "ask.toml").read_text(encoding="utf-8") == "# my custom edits\n"


def test_init_is_idempotent_on_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_home = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))

    init_config_scope(scope="global", workspace_root=None)
    _, created = init_config_scope(scope="global", workspace_root=None)

    assert created == ()


def test_init_rejects_unknown_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        init_config_scope(scope="bogus", workspace_root=None)


def test_init_workspace_scope_requires_workspace_root() -> None:
    with pytest.raises(ValueError, match="workspace_root"):
        init_config_scope(scope="workspace", workspace_root=None)
