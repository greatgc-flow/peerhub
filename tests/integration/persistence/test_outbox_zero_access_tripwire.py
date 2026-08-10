"""Tripwire test: verifies that exactly 0 reads and 0 writes touch outbox_events or outbox_checkpoints."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.contract import (
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactState,
    CompletionContract,
    CompletionContractKind,
    OutboxCheckpoint,
    RequestState,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.service import DispatchService
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    MutationPlan,
    MutationRequest,
    OutboxEvent,
    OutboxState,
    TransitionReceipt,
    TransitionStatus,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, FakeClock, FakeIdSource, SequentialIdSource


class OutboxAccessTripwire:
    def __init__(self) -> None:
        self.forbidden_tables = {"outbox_events", "outbox_checkpoints"}
        self.accesses: list[tuple[int, str | None, str | None, str | None, str | None]] = []
        self.active = False

    def authorizer_callback(
        self,
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        source: str | None,
    ) -> int:
        if self.active:
            if arg1 in self.forbidden_tables or arg2 in self.forbidden_tables:
                self.accesses.append((action, arg1, arg2, dbname, source))
                # Return SQLITE_DENY to block forbidden access
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


@pytest.fixture
def tripwire_store(tmp_path: Path) -> Iterator[tuple[SqliteStateStore, OutboxAccessTripwire]]:
    tripwire = OutboxAccessTripwire()
    db_path = tmp_path / "tripwire.sqlite3"

    store = SqliteStateStore(
        db_path,
        workspace_home_id="workspace-tripwire-test",
    )
    # Initialize schema first (migrations create tables)
    store.initialize()

    # Now activate tripwire authorizer on the connection
    # pyright: ignore[reportPrivateUsage]
    conn = store._connect()
    conn.set_authorizer(tripwire.authorizer_callback)
    tripwire.active = True

    try:
        yield store, tripwire
    finally:
        tripwire.active = False
        conn.set_authorizer(None)
        store.close()


def test_zero_access_tripwire_across_full_governance_and_dispatch_pipeline(
    tripwire_store: tuple[SqliteStateStore, OutboxAccessTripwire],
) -> None:
    store, tripwire = tripwire_store

    # 1. Governance broker workflow
    broker = GovernanceBroker(
        store,
        clock=FakeClock([100, 200, 300]),
        ids=FakeIdSource(
            (
                "plan-tripwire",
                "receipt-tripwire",
                "event-tripwire",
                "effect-receipt-tripwire",
            )
        ),
    )
    request = MutationRequest(
        request_id="req-tripwire-1",
        command_id="cmd-tripwire-1",
        correlation_id="corr-tripwire-1",
        client_id="client-tripwire-1",
        command_type="governance.tripwire.test",
        idempotency_key="idemp-tripwire-1",
        actor_id="actor-tripwire-1",
        policy_revision="policy-1",
        target_id="target-tripwire-1",
        expected_revision=0,
        operation="set",
        desired_state={"enabled": True},
        effect_intent=EffectIntent(
            kind="tripwire.effect",
            payload={"enabled": True},
        ),
    )
    submission = broker.submit(request)
    event_id = submission.receipt.outbox_event_id

    pending = broker.recover_pending_effects()
    assert len(pending) == 1
    assert pending[0].event.event_id == event_id

    claimed = broker.claim_effect(
        event_id,
        owner_id="owner-tripwire",
        attempt_id="attempt-tripwire",
    )
    assert claimed.event_id == event_id

    receipt = broker.record_effect_result(
        claimed.event_id,
        owner_id="owner-tripwire",
        attempt_id="attempt-tripwire",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    assert receipt.outbox_event_id == event_id

    # 2. Dispatch service workflow
    dispatch = DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-req-tripwire",
        correlation_id="corr-tripwire-dispatch",
        client_id="client-01",
        actor_id="actor-01",
        scope={"workspace_id": "workspace-01", "home_id": "home-01"},
        method="peer.ask",
        params={"prompt": "tripwire test"},
        idempotency_key="idemp-tripwire-dispatch",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )
    contract = CompletionContract(
        contract_id="contract-tripwire",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    admitted, rec, reserved, _capability = dispatch.admit_request(
        envelope,
        authenticated_principal="principal-01",
        actor_authorized=True,
        completion_contract=contract,
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id="instance-01",
        selected_profile_id="profile-01",
        route_decision_digest="a" * 64,
        session_id="session-tripwire",
        owner_principal_id="principal-01",
        owner_instance_id="instance-01",
        authority_epoch=5,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )
    dispatch.prepare_request(admitted.command_id)
    attempt = dispatch.create_attempt(admitted.command_id)

    # 3. Artifact management and recovery digest
    manifest = ArtifactManifestRecord(
        attempt_id=attempt.attempt_id,
        workspace_scope_id="workspace-scope-1",
        staging_root_ref="staging://root-1",
        manifest_digest="sha256:digest-tripwire",
        item_count=1,
        created_at=100,
        revision=1,
        intent_event_id=event_id,
        consumed_at=None,
    )
    art = ArtifactMetadata(
        attempt_id=attempt.attempt_id,
        artifact_id="art-tripwire",
        placeholder="__ART_TRIPWIRE__",
        workspace_scope_id="workspace-scope-1",
        staging_ref="staging://file-1",
        access_mode="READ_WRITE",
        declared_lifecycle="DISPATCH_BOUND",
        state=ArtifactState.VERIFIED,
        declared_at=100,
        revision=1,
    )
    with store.unit_of_work() as unit:
        unit.add_artifact_manifest(manifest, (art,))
        digest = unit.get_artifact_recovery_digest(attempt.attempt_id)
        assert digest is not None
        assert digest.intent_event_id == event_id

    # 4. Outbox facade methods and checkpointing
    with store.unit_of_work() as unit:
        outbox_event = unit.get_outbox_event(event_id)
        assert outbox_event is not None
        events_by_cmd = unit.list_outbox_events_by_command(str(admitted.command_id))
        all_pending = unit.list_outbox_events((OutboxState.PENDING, OutboxState.CONSUMED), limit=100)
        assert len(all_pending) >= 1

        events_list = unit.events.list(limit=10)
        assert len(events_list) >= 2
        e1 = events_list[0]
        e2 = events_list[1]

        checkpoint = OutboxCheckpoint(
            consumer_id="consumer-tripwire",
            outbox_position=e1.outbox_position,
            event_id=e1.envelope.event_id,
            revision=1,
        )
        unit.add_outbox_checkpoint(checkpoint)
        fetched_checkpoint = unit.get_outbox_checkpoint("consumer-tripwire")
        assert fetched_checkpoint is not None
        assert fetched_checkpoint.event_id == e1.envelope.event_id

        updated_checkpoint = OutboxCheckpoint(
            consumer_id="consumer-tripwire",
            outbox_position=e2.outbox_position,
            event_id=e2.envelope.event_id,
            revision=2,
        )
        assert unit.cas_update_outbox_checkpoint(checkpoint, updated_checkpoint) is True
        unit.commit()

    # 5. Tripwire validation: Exactly 0 reads and 0 writes to outbox_events and outbox_checkpoints
    assert tripwire.accesses == [], f"Forbidden accesses detected: {tripwire.accesses}"
