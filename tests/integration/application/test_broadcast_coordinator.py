from __future__ import annotations

import hashlib
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    InvocationPlan,
    ProfileDescriptor,
    SessionHint,
)
from peerhub.adapters.registry import ResolvedPeerTarget
from peerhub.application.bootstrap import build_direct_ask_admission_config
from peerhub.application.broadcast import BroadcastCoordinator, FanOutRequest
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.context import Clock, IdSource, PathLayout, RuntimeContext
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import RequestState
from peerhub.health.contract import HealthScopeMembershipSnapshot
from peerhub.runtime import Runtime, create_runtime
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from tests.integration.application.test_direct_ask import DummyClock, DummyIds


class RecordingFakePeerAdapter(FakePeerAdapter):
    def __init__(self) -> None:
        super().__init__(stdout="success")
        self.planned_requests: list[AdapterRequest] = []

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan:
        self.planned_requests.append(request)
        return super().plan_invocation(request, profile, session, limits)


@pytest.fixture
def clock() -> Clock:
    return DummyClock()


@pytest.fixture
def ids() -> IdSource:
    return DummyIds()


def _target(peer_kind: str) -> ResolvedPeerTarget:
    adapter = RecordingFakePeerAdapter()
    adapter.descriptor = replace(adapter.descriptor, peer_kind=peer_kind)
    return ResolvedPeerTarget(
        cli_name=peer_kind,
        peer_kind=peer_kind,
        adapter=adapter,
        profile=adapter.descriptor.profiles[0],
        executable_path=Path(sys.executable),
    )


def _persist_readiness(
    runtime: Runtime,
    *,
    clock: Clock,
    target: ResolvedPeerTarget,
) -> None:
    identity = f"{target.peer_kind}:{target.profile.profile_id}:{clock.now()}"
    observation_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    observation = ReadinessObserved(
        observation_id=observation_digest,
        instance_id=target.peer_kind,
        profile_id=target.profile.profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="controlled-fake",
            provider_version="1",
            observed_at=clock.now(),
            captured_at=clock.now(),
            freshness_ttl=86400,
            evidence_ref=EvidenceRef(f"sha256:{observation_digest}"),
            value=ReadinessMeasurement(
                runtime_revision="controlled-fake-v1",
                issued_at=clock.now(),
                valid_until=clock.now() + 86400000,
                integrity_verified=True,
            ),
        ),
    )
    runtime.application_workflows._health.evaluate_and_persist_readiness(
        observation,
        sealed_runtime_revision="controlled-fake-v1",
        adapter_declares_probe_safe=True,
    )


def test_fan_out_completes_two_waves_through_real_admission_and_dispatch(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = {peer: _target(peer) for peer in ("ag", "cx", "cc")}

    def resolve_target(
        name: str,
        *,
        profile_id: str | None = None,
    ) -> ResolvedPeerTarget:
        target = targets[name]
        if profile_id is not None and profile_id != target.profile.profile_id:
            raise ValueError(f"unsupported profile {profile_id}")
        return target

    monkeypatch.setattr(
        "peerhub.application.broadcast.resolve_peer_target",
        resolve_target,
    )

    admission_config = build_direct_ask_admission_config(
        targets["ag"],
        clock=clock,
        ids=ids,
    )
    configured_members = tuple(
        (target.peer_kind, target.profile.profile_id)
        for target in targets.values()
    )
    configuration_digest = hashlib.sha256(
        repr(configured_members).encode("utf-8")
    ).hexdigest()
    admission_config = replace(
        admission_config,
        configuration=replace(
            admission_config.configuration,
            digest=configuration_digest,
        ),
        membership=HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest=configuration_digest,
            configured_members=configured_members,
            bindings=(),
        ),
    )

    paths = PathLayout.for_workspace(tmp_path)
    runtime = create_runtime(
        RuntimeContext(
            workspace_home_id="cli",
            paths=paths,
            clock=clock,
            ids=ids,
        ),
        admission_config=admission_config,
    )
    try:
        _persist_readiness(runtime, clock=clock, target=targets["cx"])
        _persist_readiness(runtime, clock=clock, target=targets["cc"])

        coordinator = BroadcastCoordinator(runtime=runtime, clock=clock, ids=ids)
        common = {
            "workspace_root": tmp_path,
            "required_capability_tier": CapabilityTier.READ_ONLY,
            "limits": TransportLimits(
                process_timeout_ms=10000,
                silence_timeout_ms=10000,
                max_output_bytes=10000,
            ),
            "authenticated_subject": AuthenticatedSubject(
                "local-cli:test-user",
                "test",
            ),
        }
        wave_one = coordinator.fan_out(
            FanOutRequest(
                prompt="wave 1 prompt",
                targets=[("ag", None), ("cx", None)],
                **common,
            )
        )
        wave_two = coordinator.fan_out(
            FanOutRequest(
                prompt="wave 2 synthesis",
                targets=[("cc", None)],
                wave_of=wave_one.round_id,
                **common,
            )
        )

        assert wave_one.disposition == "all_completed"
        assert wave_two.disposition == "all_completed"
        assert [(leg.target, leg.leg_state, leg.response_text) for leg in wave_one.legs] == [
            ("ag/fake-standard", "completed", "success"),
            ("cx/fake-standard", "completed", "success"),
        ]
        assert [(leg.target, leg.leg_state, leg.response_text) for leg in wave_two.legs] == [
            ("cc/fake-standard", "completed", "success")
        ]
        command_ids = {leg.command_id for leg in (*wave_one.legs, *wave_two.legs)}
        assert len(command_ids) == 3
        planned_requests = {
            peer: cast(RecordingFakePeerAdapter, target.adapter).planned_requests
            for peer, target in targets.items()
        }
        assert [request.prompt_content for request in planned_requests["ag"]] == [
            "wave 1 prompt"
        ]
        assert [request.prompt_content for request in planned_requests["cx"]] == [
            "wave 1 prompt"
        ]
        assert [request.prompt_content for request in planned_requests["cc"]] == [
            "wave 2 synthesis"
        ]
        assert all(
            request.prompt_reference is None
            for requests in planned_requests.values()
            for request in requests
        )

        connection = sqlite3.connect(paths.database_path)
        connection.row_factory = sqlite3.Row
        try:
            rounds = {
                row["broadcast_round_id"]: row
                for row in connection.execute(
                    """
                    SELECT broadcast_round_id, wave_of, prompt_digest,
                           requested_targets, status, disposition, closed_at
                    FROM broadcast_rounds
                    """
                )
            }
            assert set(rounds) == {wave_one.round_id, wave_two.round_id}
            assert rounds[wave_one.round_id]["wave_of"] is None
            assert rounds[wave_two.round_id]["wave_of"] == wave_one.round_id
            assert rounds[wave_one.round_id]["prompt_digest"] == hashlib.sha256(
                b"wave 1 prompt"
            ).hexdigest()
            assert rounds[wave_two.round_id]["prompt_digest"] == hashlib.sha256(
                b"wave 2 synthesis"
            ).hexdigest()
            assert rounds[wave_one.round_id]["requested_targets"] == 2
            assert rounds[wave_two.round_id]["requested_targets"] == 1
            assert all(row["status"] == "closed" for row in rounds.values())
            assert all(
                row["disposition"] == "all_completed" for row in rounds.values()
            )
            assert all(row["closed_at"] is not None for row in rounds.values())

            legs = connection.execute(
                """
                SELECT bl.broadcast_round_id, bl.leg_target,
                       bl.client_leg_request_id, bl.command_id,
                       bl.leg_state, bl.terminal_at, dr.state AS request_state
                FROM broadcast_legs AS bl
                JOIN dispatch_requests AS dr ON dr.command_id = bl.command_id
                """
            ).fetchall()
            assert len(legs) == 3
            assert {row["command_id"] for row in legs} == command_ids
            assert len({row["client_leg_request_id"] for row in legs}) == 3
            assert all(row["leg_state"] == "completed" for row in legs)
            assert all(row["terminal_at"] is not None for row in legs)
            assert all(
                row["request_state"] == RequestState.SUCCEEDED_VERIFIED.value
                for row in legs
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()
    finally:
        runtime.close()
