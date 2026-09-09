"""Integration coverage for layered dispatch model configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.adapters.contract import ModelSelectionMode
from peerhub.application import model_config
from peerhub.application.config_paths import (
    ConfigPathSource,
    resolve_config_paths,
    resolve_global_config_home,
)
from peerhub.application.model_config import ModelConfigError, ModelConfigService
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock
from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import CommandID
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import SequentialIdSource


class FixedClock(Clock):
    def now(self) -> int:
        return 1_000


def _service(tmp_path: Path) -> tuple[PeerRegistryService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "model-config.sqlite3",
        workspace_home_id="model-config-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    return PeerRegistryService(broker, clock=clock, ids=ids), broker


def _write_global_config(config_home: Path, contents: str) -> None:
    config_home.mkdir()
    (config_home / "models.toml").write_text(contents, encoding="utf-8")


@pytest.mark.parametrize(
    "config_home",
    (
        Path("C:/Portable Dev (v2.1)/공유 설정"),
        Path("/opt/Peer Hub/共有 config"),
    ),
)
def test_central_config_paths_preserve_injected_cross_platform_paths(
    config_home: Path,
    tmp_path: Path,
) -> None:
    resolved = resolve_config_paths(
        workspace_root=tmp_path / "work space" / "프로젝트",
        explicit_global_config_home=config_home,
        environ={"PEERHUB_CONFIG_HOME": "ignored"},
        user_home=tmp_path / "ignored-home",
        temp_root=tmp_path / "OS temp" / "임시",
    )

    assert resolved.global_config_home.path == config_home
    assert resolved.global_config_home.source is ConfigPathSource.EXPLICIT
    assert resolved.models_toml.path == config_home / "models.toml"
    assert resolved.ask_toml.path == config_home / "ask.toml"
    assert resolved.workspace_config_home.path == (
        tmp_path / "work space" / "프로젝트" / ".peerhub" / "config"
    )
    assert resolved.workspace_temp.path.is_relative_to(
        tmp_path / "OS temp" / "임시" / "peerhub" / "workspaces"
    )


def test_global_config_home_precedence_and_absent_selection_do_not_fallback(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    home = tmp_path / "home"

    assert resolve_global_config_home(
        explicit=explicit,
        environ={"PEERHUB_CONFIG_HOME": str(environment)},
        user_home=home,
    ).path == explicit
    selected = resolve_global_config_home(
        environ={"PEERHUB_CONFIG_HOME": str(environment)},
        user_home=home,
    )
    assert selected.path == environment
    assert selected.source is ConfigPathSource.ENV
    assert not environment.exists()
    defaulted = resolve_global_config_home(environ={}, user_home=home)
    assert defaulted.path == home / ".peerhub" / "config"
    assert defaulted.source is ConfigPathSource.DEFAULT


def test_workspace_binding_takes_precedence_over_global_and_packaged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = _service(tmp_path)
    registry.bind_profile(
        node_id="cx",
        profile_id="cx.standard",
        model_id="workspace-model",
        reasoning_effort="high",
        actor_id="tester",
    )
    config_home = tmp_path / "global"
    _write_global_config(
        config_home,
        '[profiles."cx.standard"]\nselection_mode = "pinned"\nmodel = "global-model"\n',
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    binding = ModelConfigService(registry).resolve(
        node_id="cx", profile_id="cx.standard"
    )

    assert binding.selection_mode is ModelSelectionMode.PINNED
    assert binding.model_id == "workspace-model"
    assert binding.reasoning_effort == "high"
    assert binding.source_layer == "workspace"


def test_global_config_takes_precedence_over_packaged_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "global"
    _write_global_config(
        config_home,
        '[profiles."cx.standard"]\nselection_mode = "pinned"\nmodel = "global-model"\n',
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    binding = ModelConfigService(None).resolve(
        node_id="cx", profile_id="cx.standard"
    )

    assert binding.selection_mode is ModelSelectionMode.PINNED
    assert binding.model_id == "global-model"
    assert binding.source_layer == "global"


@pytest.mark.parametrize(
    ("profile_id", "selection_mode", "model_id", "reasoning_effort"),
    (
        ("cx.standard", ModelSelectionMode.PINNED, "gpt-5.6-luna", "low"),
        ("cx.effort", ModelSelectionMode.PINNED, "gpt-5.6-terra", "high"),
        ("cx.deepthink", ModelSelectionMode.PINNED, "gpt-6-astra", "xhigh"),
        ("cc.standard", ModelSelectionMode.CLI_DEFAULT, None, None),
        ("ag.standard", ModelSelectionMode.CLI_DEFAULT, None, None),
    ),
)
def test_packaged_defaults_resolve_each_known_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    selection_mode: ModelSelectionMode,
    model_id: str | None,
    reasoning_effort: str | None,
) -> None:
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "missing-global"))

    binding = ModelConfigService(None).resolve(
        node_id=profile_id.partition(".")[0], profile_id=profile_id
    )

    assert binding.selection_mode is selection_mode
    assert binding.model_id == model_id
    assert binding.reasoning_effort == reasoning_effort
    assert binding.source_layer == "packaged_default"


def test_packaged_wildcard_resolves_unknown_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "missing-global"))

    binding = ModelConfigService(None).resolve(
        node_id="future", profile_id="future.standard"
    )

    assert binding.selection_mode is ModelSelectionMode.CLI_DEFAULT
    assert binding.model_id is None
    assert binding.source_layer == "packaged_default"


def test_missing_every_layer_raises_an_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "missing-global"))
    defaults_dir = tmp_path / "test-defaults"
    defaults_dir.mkdir()
    (defaults_dir / "model-defaults.toml").write_text(
        "[profiles.known]\nselection_mode = \"cli_default\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config.resources, "files", lambda _: defaults_dir)

    with pytest.raises(ModelConfigError, match="peerhub node bind-profile"):
        ModelConfigService(None).resolve(
            node_id="missing-node", profile_id="missing.profile"
        )


def test_bind_profile_validates_selection_mode_and_reads_legacy_v1_as_pinned(
    tmp_path: Path,
) -> None:
    registry, broker = _service(tmp_path)

    with pytest.raises(InvalidMutationError, match="must not be set"):
        registry.bind_profile(
            node_id="cc",
            profile_id="cc.standard",
            model_id="forbidden",
            selection_mode="cli_default",
            actor_id="tester",
        )
    with pytest.raises(InvalidMutationError, match="is required"):
        registry.bind_profile(
            node_id="cc",
            profile_id="cc.standard",
            selection_mode="pinned",
            actor_id="tester",
        )
    cli_default = registry.bind_profile(
        node_id="cc",
        profile_id="cc.standard",
        selection_mode="cli_default",
        actor_id="tester",
    )
    cli_default_target = broker.get_target(cli_default.receipt.target_id)
    assert cli_default_target is not None
    assert cli_default_target.state["selection_mode"] == "cli_default"
    assert cli_default_target.state["model_id"] is None

    broker.submit(
        MutationRequest(
            request_id="legacy-binding-request",
            command_id=CommandID("legacy-binding-command"),
            correlation_id="legacy-binding-correlation",
            client_id="test.model-config",
            command_type="peer-registry.profile.bind",
            idempotency_key="legacy-binding-request",
            actor_id="tester",
            policy_revision="protocol-v2",
            target_id="peer-profile-binding:legacy:cx.standard",
            expected_revision=0,
            operation="peer-registry.profile.bind",
            desired_state={
                "kind": "peer-profile-binding",
                "scope": "legacy",
                "schema_version": 1,
                "binding_id": "peer-profile-binding:legacy:cx.standard",
                "node_id": "legacy",
                "profile_id": "cx.standard",
                "model_id": "legacy-model",
                "reasoning_effort": "medium",
                "updated_at": 1_000,
                "updated_by": "tester",
            },
            effect_intent=EffectIntent(
                kind="peer-registry.noop",
                payload={},
            ),
        )
    )

    binding = ModelConfigService(registry).resolve(
        node_id="legacy", profile_id="cx.standard"
    )
    assert binding.selection_mode is ModelSelectionMode.PINNED
    assert binding.model_id == "legacy-model"
    assert binding.reasoning_effort == "medium"
    assert binding.source_layer == "workspace"
