from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.application.capability_config import (
    import_legacy_capability_configs,
)
from peerhub.application.commands import SubmissionMetadata
from peerhub.application.leadership import LeadershipMonopolyError
from peerhub.application.legacy import (
    DiscoverCandidatesCommand,
    ElectLeaderCommand,
    LegacyActionCall,
    LegacyTranslator,
    TranslatedCommand,
)
from peerhub.cli import main
from peerhub.client import Client
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.errors import RouteExhaustedError
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandSuccess
from peerhub.runtime import create_runtime
from tests.integration.conftest import FakeClock, FakeIdSource


def _write_sources(
    root: Path,
) -> tuple[Path, Path, dict[str, Path]]:
    ai = root / "ai"
    ai.mkdir(parents=True)
    protocol = ai / "protocol.json"
    orchestration = ai / "orchestration.json"
    protocol.write_text(
        json.dumps(
            {
                "workload": {
                    "capability_registry": {
                        "cc": ["architecture"],
                        "ag": ["Documentation", "image-generation"],
                        "cx": ["code-generation", "test-authoring"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    orchestration.write_text(
        json.dumps(
            {
                "hub_nodes": [
                    {
                        "node_id": "cc",
                        "aliases": ["claude"],
                        "enabled": True,
                    },
                    {
                        "node_id": "ag",
                        "aliases": ["agy"],
                        "enabled": True,
                    },
                    {
                        "node_id": "cx",
                        "aliases": ["codex"],
                        "enabled": True,
                    },
                ],
                "consensus": {
                    "default_voters": ["cc", "ag", "cx"],
                    "default_proposer": "rotating",
                },
                "roles_registry": {
                    "architect": ["cc", "ag", "cx"],
                    "reviewer": ["cc", "ag", "cx"],
                    "coder": ["cc", "ag", "cx"],
                },
            }
        ),
        encoding="utf-8",
    )
    health_paths: dict[str, Path] = {}
    for node_id, subdir, capabilities in (
        ("cc", "claude", ["planning"]),
        ("ag", "antigravity", ["documentation"]),
        ("cx", "codex", ["bug-fixing"]),
    ):
        health = root / subdir / "health.json"
        health.parent.mkdir(parents=True)
        health.write_text(
            json.dumps({"profile": {"capabilities": capabilities}}),
            encoding="utf-8",
        )
        health_paths[node_id] = health
    return protocol, orchestration, health_paths


def _context(workspace: Path) -> RuntimeContext:
    return RuntimeContext(
        "capability-test",
        PathLayout.for_workspace(workspace),
        FakeClock(),
        FakeIdSource(),
    )


def _import(runtime: object, source_root: Path):
    protocol, orchestration, health_paths = _write_sources(source_root)
    return import_legacy_capability_configs(
        getattr(runtime, "capability_config_service"),
        protocol_path=protocol,
        orchestration_path=orchestration,
        health_paths=health_paths,
    )


def test_importer_persists_real_targets_in_legacy_source_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    context = _context(workspace)
    with create_runtime(context) as runtime:
        result = _import(runtime, tmp_path / "legacy")
        assert tuple(config.target_id for config in result.configs) == (
            "peer-capability-config:cc",
            "peer-capability-config:ag",
            "peer-capability-config:cx",
        )
        ag = result.configs[1]
        assert tuple(capability.name for capability in ag.capabilities) == (
            "documentation",
            "image-generation",
            "architect",
            "reviewer",
            "coder",
        )
        assert ag.capabilities[0].sources == (
            "legacy-import:health.ag.profile.capabilities",
            "legacy-import:protocol.workload.capability_registry.ag",
        )
        assert ag.capabilities[-1].sources == (
            "legacy-import:orchestration.roles_registry.coder",
        )
        assert result.policy.target_id == (
            "routing-policy:capability-native-v1:1"
        )
        assert result.policy.default_proposer.rotation_order == (
            "cc",
            "ag",
            "cx",
        )

        # The importer is one-time: a second invocation observes the same
        # immutable initial revisions instead of silently resnapshotting.
        protocol = tmp_path / "legacy" / "ai" / "protocol.json"
        orchestration = tmp_path / "legacy" / "ai" / "orchestration.json"
        second = import_legacy_capability_configs(
            runtime.capability_config_service,
            protocol_path=protocol,
            orchestration_path=orchestration,
            health_paths={
                "cc": tmp_path / "legacy" / "claude" / "health.json",
                "ag": tmp_path / "legacy" / "antigravity" / "health.json",
                "cx": tmp_path / "legacy" / "codex" / "health.json",
            },
        )
        assert tuple(config.revision for config in second.configs) == (1, 1, 1)


def test_discover_round_trips_through_real_sqlite_without_a_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    context = _context(workspace)
    with create_runtime(context) as runtime:
        _import(runtime, tmp_path / "legacy")
        before = tuple(runtime.governance_broker.list_targets(
            "leader-election-decision", None
        ))

    # Reopen the database: configuration and policy must come from SQLite,
    # not from the importer files or in-memory objects.
    with create_runtime(context) as runtime:
        ranking = runtime.capability_matching_coordinator.discover(
            needs="code-generation", effort="medium"
        )
        assert ranking.requested_effort == "mid"
        assert ranking.ordered_matches[0].node_id == "cx"
        assert ranking.ordered_matches[0].ranking_score == 10
        assert ranking.ordered_matches[0].availability_status is None
        assert ranking.ordered_matches[0].provenance[3].evidence_state.value == (
            "ABSENT"
        )
        assert runtime.governance_broker.list_targets(
            "leader-election-decision", None
        ) == before


def test_elect_leader_commits_decision_before_claim_and_outcome_after(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "workspace")
    with create_runtime(context) as runtime:
        _import(runtime, tmp_path / "legacy")
        receipt = runtime.capability_matching_coordinator.elect_leader(
            needs="code-generation",
            effort="mid",
            reason="gap6-test",
            actor_id="operator",
        )
        assert receipt.selected_node_id == "cx"
        assert receipt.selection_basis == "RANKED_MATCH"
        assert receipt.outcome == "CLAIMED"
        decision = runtime.governance_broker.get_target(
            receipt.decision_target_id
        )
        outcome = runtime.governance_broker.get_target(receipt.outcome_target_id)
        assert decision is not None
        assert outcome is not None
        assert decision.state["status"] == "DECIDED"
        assert decision.state["selected_node_id"] == "cx"
        assert outcome.state["decision_target_id"] == decision.target_id
        assert outcome.state["decision_hash"] == decision.state["decision_hash"]
        assert outcome.state["leadership_revision"] == (
            receipt.leadership_revision
        )


def test_rejected_and_exhausted_elections_still_have_both_audits(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "workspace")
    with create_runtime(context) as runtime:
        imported = _import(runtime, tmp_path / "legacy")
        coordinator = runtime.capability_matching_coordinator
        for _ in range(3):
            coordinator.elect_leader(
                needs="code-generation", actor_id="operator"
            )
        with pytest.raises(LeadershipMonopolyError) as rejected:
            coordinator.elect_leader(
                needs="code-generation", actor_id="operator"
            )
        rejected_outcome = runtime.governance_broker.get_target(
            str(rejected.value.details["outcome_target_id"])
        )
        assert rejected_outcome is not None
        assert rejected_outcome.state["outcome"] == "REJECTED"
        assert rejected_outcome.state["error_class"] == (
            "LeadershipMonopolyError"
        )

        for config in imported.configs:
            runtime.capability_config_service.put_config(
                node_id=config.node_id,
                enabled=False,
                aliases=config.aliases,
                capabilities=config.capabilities,
                actor_id="operator",
            )
        with pytest.raises(RouteExhaustedError) as exhausted:
            coordinator.elect_leader(needs="anything", actor_id="operator")
        exhausted_outcome = runtime.governance_broker.get_target(
            str(exhausted.value.details["outcome_target_id"])
        )
        assert exhausted_outcome is not None
        assert exhausted_outcome.state["outcome"] == "ROUTE_EXHAUSTED"
        assert exhausted_outcome.state["selected_node_id"] is None
        assert len(runtime.governance_broker.list_targets(
            "leader-election-decision", None
        )) == 5
        assert len(runtime.governance_broker.list_targets(
            "leader-election-outcome", None
        )) == 5


def test_legacy_discover_and_elect_execute_through_command_api(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "workspace")
    with create_runtime(context) as runtime:
        _import(runtime, tmp_path / "legacy")
        client = Client(
            runtime.application_api,
            caller=RequestContext(principal="user", client_id="legacy"),
        )
        submission = SubmissionMetadata(
            "request-1",
            "correlation-1",
            "legacy",
            "operator",
            {},
            "idempotency-1",
            None,
            None,
            1000,
        )
        translated = LegacyTranslator().translate(
            LegacyActionCall(
                "discover", {"needs": "code-generation", "effort": "mid"}
            ),
            submission,
        )
        assert isinstance(translated, TranslatedCommand)
        assert isinstance(translated.command, DiscoverCandidatesCommand)
        result = client.submit(translated.command)
        assert isinstance(result, CommandSuccess)
        assert result.result["ordered_matches"][0]["node_id"] == "cx"

        translated = LegacyTranslator().translate(
            LegacyActionCall(
                "elect-leader",
                {
                    "needs": "code-generation",
                    "effort": "mid",
                    "reason": "legacy-test",
                },
            ),
            submission,
        )
        assert isinstance(translated, TranslatedCommand)
        assert isinstance(translated.command, ElectLeaderCommand)
        result = client.submit(translated.command)
        assert isinstance(result, CommandSuccess)
        assert result.result["selected_node_id"] == "cx"
        assert result.result["outcome"] == "CLAIMED"


def test_cli_import_discover_and_elect(tmp_path: Path, capsys) -> None:
    sys_root = tmp_path / "_sys"
    protocol, orchestration, _ = _write_sources(sys_root)
    workspace = tmp_path / "workspace" / "peerhub"
    assert main([
        "routing",
        "import-capabilities",
        "--workspace",
        str(workspace),
        "--protocol",
        str(protocol),
        "--orchestration",
        str(orchestration),
    ]) == 0
    capsys.readouterr()

    assert main([
        "routing",
        "discover",
        "--workspace",
        str(workspace),
        "--needs",
        "code-generation",
        "--json",
    ]) == 0
    discovered = json.loads(capsys.readouterr().out)
    assert discovered["ordered_matches"][0]["node_id"] == "cx"

    assert main([
        "routing",
        "elect-leader",
        "--workspace",
        str(workspace),
        "--needs",
        "code-generation",
        "--json",
    ]) == 0
    elected = json.loads(capsys.readouterr().out)
    assert elected["selected_node_id"] == "cx"
    assert elected["outcome"] == "CLAIMED"
