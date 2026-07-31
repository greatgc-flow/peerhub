"""Integration tests for server command admission and idempotency."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.errors import (
    ActorUnauthorizedError,
    DuplicateClientRequestError,
    IdempotencyPayloadMismatchError,
)
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
)
from peerhub.dispatch.service import DispatchService
from peerhub.governance.contract import OutboxState
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "command-idempotency.sqlite3",
        workspace_home_id="workspace-command-idempotency",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _contract() -> CompletionContract:
    return CompletionContract(
        contract_id="completion-contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _envelope(
    *,
    client_request_id: str = "client-request-01",
    correlation_id: str = "correlation-01",
    idempotency_key: str = "idempotency-01",
    params: dict[str, object] | None = None,
    client_timestamp: int = 10,
) -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=client_request_id,
        correlation_id=correlation_id,
        client_id="client-01",
        actor_id="actor-01",
        scope={
            "workspace_id": "workspace-01",
            "home_id": "home-01",
        },
        method="peer.ask",
        params=params or {"prompt": "hello"},
        idempotency_key=idempotency_key,
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=client_timestamp,
    )


def _service(
    store: SqliteStateStore,
    *,
    clock_start: int = 100,
) -> DispatchService:
    return DispatchService(
        store,
        clock=DeterministicClock(start=clock_start),
        ids=SequentialIdSource(),
    )


def _admit(
    service: DispatchService,
    envelope: CommandEnvelope,
    *,
    actor_authorized: bool = True,
):
    return service.admit_request(
        envelope,
        authenticated_principal="principal-01",
        actor_authorized=actor_authorized,
        completion_contract=_contract(),
        policy_revision=7,
        configuration_revision=11,
        selected_peer_instance_id="instance-01",
        selected_profile_id="profile-01",
        route_decision_digest="a" * 64,
        session_id="session-01",
        owner_principal_id="principal-01",
        owner_instance_id="instance-01",
        authority_epoch=3,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="peer-01",
    )


def test_same_digest_replay_returns_existing_admission_without_new_rows(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    first_envelope = _envelope()

    first_request, first_receipt, first_lease = _admit(
        service,
        first_envelope,
    )
    replay_envelope = replace(
        first_envelope,
        correlation_id="correlation-replay",
        client_timestamp=999,
    )
    replay_request, replay_receipt, replay_lease = _admit(
        service,
        replay_envelope,
    )

    assert replay_request == first_request
    assert replay_receipt == first_receipt
    assert replay_lease == first_lease
    assert str(first_request.command_id) == "command-1"
    assert first_lease.fence.fencing_token == 1

    with store.unit_of_work() as unit:
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )
        attempts = unit.list_attempts(first_request.command_id)

    assert len(events) == 1
    assert events[0].event_kind == "ADMITTED"
    assert attempts == ()

    second_request, _, second_lease = _admit(
        service,
        _envelope(
            client_request_id="client-request-02",
            correlation_id="correlation-02",
            idempotency_key="idempotency-02",
        ),
    )
    assert str(second_request.command_id) == "command-2"
    assert second_lease.fence.fencing_token == 2


def test_changed_payload_under_client_request_identity_is_rejected(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    first_request, _, _ = _admit(service, _envelope())

    with pytest.raises(DuplicateClientRequestError):
        _admit(
            service,
            _envelope(params={"prompt": "changed"}),
        )

    with store.unit_of_work() as unit:
        persisted = unit.get_request(first_request.command_id)
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert persisted == first_request
    assert len(events) == 1


def test_changed_payload_under_idempotency_key_is_rejected_separately(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    first_request, _, _ = _admit(service, _envelope())

    with pytest.raises(IdempotencyPayloadMismatchError):
        _admit(
            service,
            _envelope(
                client_request_id="client-request-02",
                correlation_id="correlation-02",
                params={"prompt": "changed"},
            ),
        )

    with store.unit_of_work() as unit:
        assert unit.get_request(first_request.command_id) == (
            first_request
        )
        assert (
            unit.get_client_request_binding(
                "client-01",
                "client-request-02",
            )
            is None
        )


def test_unauthorized_submission_has_no_ids_or_durable_writes(
    store: SqliteStateStore,
) -> None:
    service = _service(store, clock_start=500)
    envelope = _envelope()

    with pytest.raises(ActorUnauthorizedError):
        _admit(
            service,
            envelope,
            actor_authorized=False,
        )

    with store.unit_of_work() as unit:
        assert (
            unit.get_client_request_binding(
                envelope.client_id,
                envelope.client_request_id,
            )
            is None
        )
        assert (
            unit.get_command_idempotency_binding(
                envelope.client_id,
                envelope.method,
                envelope.idempotency_key or "",
            )
            is None
        )
        assert unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        ) == ()

    request, _, lease = _admit(service, envelope)
    assert str(request.command_id) == "command-1"
    assert request.created_at == 500
    assert lease.fence.fencing_token == 1


def test_concurrent_identical_submissions_converge_on_one_command(
    store: SqliteStateStore,
) -> None:
    envelope = _envelope()
    services = (
        _service(store, clock_start=700),
        _service(store, clock_start=800),
    )

    def submit(service: DispatchService):
        return _admit(service, envelope)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(submit, services))

    command_ids = {
        str(request.command_id)
        for request, _, _ in results
    }
    receipt_ids = {
        receipt.admission_receipt_id
        for _, receipt, _ in results
    }
    lease_ids = {
        lease.lease_id
        for _, _, lease in results
    }

    assert command_ids == {"command-1"}
    assert receipt_ids == {"admission-receipt-1"}
    assert lease_ids == {"lease-1"}

    with store.unit_of_work() as unit:
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )
        binding = unit.get_client_request_binding(
            envelope.client_id,
            envelope.client_request_id,
        )

    assert len(events) == 1
    assert binding is not None
    assert str(binding.command_id) == "command-1"


def test_partial_identity_replay_binds_each_missing_alias(
    store: SqliteStateStore,
) -> None:
    service = _service(store)
    first_request, first_receipt, first_lease = _admit(
        service,
        _envelope(),
    )

    by_client_request = _admit(
        service,
        _envelope(idempotency_key="idempotency-alias"),
    )
    assert by_client_request == (
        first_request,
        first_receipt,
        first_lease,
    )

    by_idempotency_key = _admit(
        service,
        _envelope(
            client_request_id="client-request-alias",
            idempotency_key="idempotency-01",
        ),
    )
    assert by_idempotency_key == (
        first_request,
        first_receipt,
        first_lease,
    )

    with store.unit_of_work() as unit:
        key_alias = unit.get_command_idempotency_binding(
            "client-01",
            "peer.ask",
            "idempotency-alias",
        )
        client_alias = unit.get_client_request_binding(
            "client-01",
            "client-request-alias",
        )
        events = unit.list_outbox_events(
            (OutboxState.PENDING,),
            limit=100,
        )

    assert key_alias is not None
    assert key_alias.command_id == first_request.command_id
    assert (
        key_alias.admission_receipt_id
        == first_receipt.admission_receipt_id
    )
    assert client_alias is not None
    assert client_alias.command_id == first_request.command_id
    assert (
        client_alias.admission_receipt_id
        == first_receipt.admission_receipt_id
    )
    assert len(events) == 1

    with pytest.raises(DuplicateClientRequestError):
        _admit(
            service,
            _envelope(
                client_request_id="client-request-alias",
                idempotency_key="idempotency-new",
                params={"prompt": "changed"},
            ),
        )

    with pytest.raises(IdempotencyPayloadMismatchError):
        _admit(
            service,
            _envelope(
                client_request_id="client-request-new",
                idempotency_key="idempotency-alias",
                params={"prompt": "changed"},
            ),
        )
