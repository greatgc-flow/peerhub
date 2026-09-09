"""Integration coverage for layered ``peerhub ask`` dispatch configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.application.ask_config import (
    AskConfigError,
    global_ask_config_path,
    load_ask_config,
)


def _write_global_config(config_home: Path, contents: str) -> None:
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "ask.toml").write_text(contents, encoding="utf-8")


def test_packaged_defaults_resolve_without_any_global_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "absent"))

    config = load_ask_config()

    assert config.continuity.enabled is True
    assert config.continuity.max_room_checkpoints >= 1
    assert config.continuity.max_task_checkpoints >= 1
    assert config.continuity.max_chars > 0


def test_global_layer_overrides_only_the_keys_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    _write_global_config(config_home, "[continuity]\nmax_task_checkpoints = 7\n")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    config = load_ask_config()

    assert config.continuity.max_task_checkpoints == 7
    # Untouched keys still come from the packaged layer.
    assert config.continuity.enabled is True


def test_config_home_override_replaces_the_home_directory_outright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "portable"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    assert global_ask_config_path() == config_home / "ask.toml"


def test_model_and_ask_global_helpers_share_one_central_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from peerhub.application import config_paths, model_config

    selected = tmp_path / "central config"
    calls: list[str] = []
    original = config_paths.resolve_global_config_home

    def recording_resolver():
        calls.append("resolved")
        return original(
            explicit=selected,
            environ={},
            user_home=tmp_path / "ignored",
        )

    monkeypatch.setattr(config_paths, "resolve_global_config_home", recording_resolver)

    assert model_config.global_config_path() == selected / "models.toml"
    assert global_ask_config_path() == selected / "ask.toml"
    assert calls == ["resolved", "resolved"]


def test_malformed_global_value_is_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    _write_global_config(config_home, '[continuity]\nmax_chars = "lots"\n')
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    with pytest.raises(AskConfigError, match="max_chars"):
        load_ask_config()
