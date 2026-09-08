"""Integration coverage for layered dispatch model configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.adapters.contract import ModelSelectionMode
from peerhub.application import model_config
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
