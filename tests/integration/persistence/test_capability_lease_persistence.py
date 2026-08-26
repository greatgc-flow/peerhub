"""Integration coverage for durable capability-lease storage."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.context import IdSource
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
    ErrorCode,
)
from peerhub.dispatch.capability import (
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityTier,
    EnforcementLevel,
    PeerEnforcementEvidence,
)
from peerhub.dispatch.capability_policy import (
    StaticPeerEnforcementEvidenceProvider,
)
from peerhub.dispatch.unit_of_work import FaultInjector, FaultPoint
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    CompletionContract,
    CompletionContractKind,
    RequestSnapshot,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.persistence.sqlite_dispatch import SqliteDispatchRepository
from tests.fakes import DeterministicClock, SequentialIdSource, deterministic_uuid4
from tests.integration.dispatch.test_retry_authorization import (
    _authorize as _authorize_retry_case,
    _setup_retry_case,
)


class _TaggedIdSource:
    """Sequential IDs carrying a distinguishing tag, and a mint log.

    ``SequentialIdSource`` restarts at 1 for every instance, so a second
    coordinator that wrongly re-minted a capability lease would hand back the
    *same* string as the original and an equality assertion would pass
    vacuously.  Tagging makes a re-mint visibly different, and ``namespaces``
    records every mint so a test can assert the "capability-lease" namespace
    was never drawn from at all on a replay.
    """

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._counters: dict[str, int] = {}
        self.namespaces: list[str] = []

    def new_id(self, namespace: str) -> str:
        """Return the next tagged identifier and record the namespace."""

        self.namespaces.append(namespace)
        count = self._counters.get(namespace, 0) + 1
        self._counters[namespace] = count
        value = f"{namespace}-{self._tag}-{count}"
        if namespace == "outbox-event":
            return deterministic_uuid4(value)
        return value


class _RaisingFaultInjector(FaultInjector):
    """Raise at one exact dispatch transaction boundary."""

    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")


def _store(database_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(
        database_path,
        workspace_home_id="workspace-capability-lease",
    )
    store.initialize()
    return store


# Increment 4 makes admission itself the authoritative issuer, and it fails
# closed on a mutating tier whose target has no measured enforcement ceiling.
# This fixture therefore states a measured ceiling for its own instance so the
# WORKTREE_WRITE admission under test is authorized; it is a controlled test
# mapping, not evidence that any real peer is enforced. source_tag is
# "controlled_fake" (not "empirical_probe") so this fixture can never be
# mistaken for a real DIR-004 measurement if grepped/copied later -- no real
# peer has empirical_probe-backed enforcement evidence today.
_MEASURED_CX_EVIDENCE = StaticPeerEnforcementEvidenceProvider(
    {
        "cx-instance-capability": PeerEnforcementEvidence(
            peer_instance_id="cx-instance-capability",
            peer_kind="cx",
            enforcement_ceiling=EnforcementLevel.ENFORCED,
            source_tag="controlled_fake",
        )
    }
)


def _envelope() -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="client-request-capability",
        correlation_id="correlation-capability",
        client_id="client-capability",
        actor_id="actor-capability",
        scope={"workspace_id": "workspace-capability"},
        method="peer.ask",
        params={"prompt": "persist capability lease"},
        idempotency_key="idempotency-capability",
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=10,
    )


def _completion_contract() -> CompletionContract:
    return CompletionContract(
        contract_id="completion-capability",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _service(
    store: SqliteStateStore,
    *,
    fault_injector: FaultInjector | None = None,
    ids: IdSource | None = None,
    start: int = 100,
) -> DispatchService:
    return DispatchService(
        store,
        clock=DeterministicClock(start=start),
        ids=ids if ids is not None else SequentialIdSource(),
        fault_injector=fault_injector,
        enforcement_evidence=_MEASURED_CX_EVIDENCE,
    )


def _admit(
    store: SqliteStateStore,
    *,
    fault_injector: FaultInjector | None = None,
    ids: IdSource | None = None,
    start: int = 100,
) -> tuple[RequestSnapshot, AdmissionReceipt, CapabilityLease]:
    service = _service(
        store,
        fault_injector=fault_injector,
        ids=ids,
        start=start,
    )
    envelope = _envelope()
    request, receipt, _, capability_lease = service.admit_request(
        envelope,
        authenticated_subject=AuthenticatedSubject(
            "principal-capability",
            "test",
        ),
        completion_contract=_completion_contract(),
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.WORKTREE_WRITE,
        selected_peer_instance_id="cx-instance-capability",
        selected_profile_id="cx.deepthink",
        route_decision_digest="a" * 64,
        session_id="session-capability",
        owner_principal_id="principal-capability",
        owner_instance_id="cx-instance-capability",
        authority_epoch=3,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="cx",
    )
    return request, receipt, capability_lease


def test_migrations_register_capability_tiers_without_implicit_grants(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "capability-schema.sqlite3"
    _store(database_path)

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            "PRAGMA user_version"
        ).fetchone() == (26,)
        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 18"
        ).fetchone() == ("0018_capability_leases",)

        request_columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(dispatch_requests)"
            ).fetchall()
        }
        tier_column = request_columns["required_capability_tier"]
        assert tier_column[3] == 0
        assert tier_column[4] is None

        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 19"
        ).fetchone() == (
            "0019_route_decision_capability_tier",
        )
        route_columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(route_decisions)"
            ).fetchall()
        }
        route_tier_column = route_columns[
            "required_capability_tier"
        ]
        assert route_tier_column[3] == 0
        assert route_tier_column[4] is None

        foreign_keys = {
            (row[3], row[2], row[4])
            for row in connection.execute(
                "PRAGMA foreign_key_list(capability_leases)"
            ).fetchall()
        }
        assert foreign_keys == {
            ("command_id", "dispatch_requests", "command_id"),
            (
                "admission_receipt_id",
                "admission_receipts",
                "admission_receipt_id",
            ),
            ("session_lease_id", "leases", "lease_id"),
            ("previous_attempt_id", "dispatch_attempts", "attempt_id"),
        }
        unique_indexes = {
            tuple(
                index_column[2]
                for index_column in connection.execute(
                    f"PRAGMA index_info({index_row[1]})"
                ).fetchall()
            )
            for index_row in connection.execute(
                "PRAGMA index_list(capability_leases)"
            ).fetchall()
            if index_row[2] == 1
        }
        assert {
            ("command_id", "authorized_attempt_number"),
            ("session_lease_id",),
        }.issubset(unique_indexes)
        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    finally:
        connection.close()


def test_capability_lease_write_rolls_back_on_fault_before_commit(
    tmp_path: Path,
) -> None:
    """A fault after the lease write leaves none of the four records durable.

    Increment 4 writes the capability lease inside the admission transaction,
    so rollback is now exercised through admission rather than a hand-inserted
    lease (errata 7.1 point 7).
    """

    store = _store(tmp_path / "capability-rollback.sqlite3")

    with pytest.raises(RuntimeError, match="AFTER_CAPABILITY_LEASE_WRITE"):
        _admit(
            store,
            fault_injector=_RaisingFaultInjector(
                FaultPoint.AFTER_CAPABILITY_LEASE_WRITE
            ),
        )

    with store.read_unit_of_work() as unit:
        assert unit.get_capability_lease(
            "capability-lease-1"
        ) is None
        assert unit.get_capability_lease_by_session_lease_id(
            "lease-1"
        ) is None
        assert unit.get_request("command-1") is None


def test_capability_lease_replay_returns_identical_durable_record(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "capability-replay.sqlite3"
    store = _store(database_path)
    request, receipt, lease = _admit(store)

    store.close()
    replay_store = _store(database_path)
    with replay_store.read_unit_of_work() as unit:
        by_id = unit.get_capability_lease(lease.capability_lease_id)
        by_attempt = unit.get_capability_lease_for_attempt(
            request.command_id,
            1,
        )
        by_receipt = unit.get_capability_lease_by_admission_receipt_id(
            receipt.admission_receipt_id
        )

    assert by_id == lease
    assert by_attempt == lease
    assert by_receipt == lease
    assert request.authenticated_principal == "principal-capability"
    assert lease.subject_principal_id == "principal-capability"
    assert by_id is not lease
    assert by_id is not by_attempt
    assert by_attempt is not by_receipt


def _count_capability_leases(database_path: Path) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM capability_leases"
        ).fetchone()
    finally:
        connection.close()
    return int(row[0])


def test_idempotent_admit_replay_returns_the_original_capability_lease(
    tmp_path: Path,
) -> None:
    """A replayed admission reuses the bound lease instead of minting one.

    Errata 7.1/7.2: exactly one capability lease exists per command, and the
    idempotency path must hand back *that* lease.  The replaying coordinator
    is given a tagged ID source, so a freshly minted lease would carry a
    visibly different identifier rather than colliding with the original --
    and ``namespaces`` proves the "capability-lease" namespace was never even
    drawn from.
    """

    database_path = tmp_path / "capability-idempotent-admit.sqlite3"
    store = _store(database_path)
    _, _, original = _admit(store)

    replay_ids = _TaggedIdSource("replay")
    _, _, replayed = _admit(store, ids=replay_ids, start=900)

    assert replayed.capability_lease_id == original.capability_lease_id
    assert replayed == original
    assert "capability-lease" not in replay_ids.namespaces
    assert _count_capability_leases(database_path) == 1


def test_idempotent_admit_replay_selects_capability_by_session_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay remains bound to the receipt's session lease with many rows.

    SQLite is free to return either attempt for an unordered command lookup.
    Simulate it selecting a retry row and prove replay instead uses the
    session-lease key, whose uniqueness survives migration 0022.
    """

    store = _store(tmp_path / "capability-session-replay.sqlite3")
    _, _, original = _admit(store)
    arbitrary_retry = replace(
        original,
        capability_lease_id="capability-lease-arbitrary-retry",
        session_lease_id="lease-arbitrary-retry",
        authorized_attempt_number=2,
        previous_attempt_id="attempt-arbitrary-retry",
    )
    original_lookup = SqliteDispatchRepository._get_capability_lease  # pyright: ignore[reportPrivateUsage]

    def _simulate_unordered_command_lookup(
        repository: SqliteDispatchRepository,
        column: str,
        value: str,
    ) -> CapabilityLease | None:
        if column == "command_id":
            return arbitrary_retry
        return original_lookup(repository, column, value)

    monkeypatch.setattr(
        SqliteDispatchRepository,
        "_get_capability_lease",
        _simulate_unordered_command_lookup,
    )

    _, _, replayed = _admit(store, ids=_TaggedIdSource("session-replay"))

    assert replayed == original


def test_create_attempt_rejects_missing_capability_for_current_session_lease(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-attempt-capability.sqlite3"
    store = _store(database_path)
    ids = SequentialIdSource()
    request, _, capability_lease = _admit(store, ids=ids)
    service = _service(store, ids=ids)
    service.prepare_request(request.command_id)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM capability_leases WHERE capability_lease_id = ?",
            (capability_lease.capability_lease_id,),
        )

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        service.create_attempt(request.command_id, expected_authorized_attempt_number=1)

    assert exc_info.value.invariant == (
        "attempt creation references a session lease with no capability"
    )
    with store.unit_of_work() as unit:
        assert unit.list_attempts(request.command_id) == ()


def test_create_attempt_rejects_capability_for_different_attempt_number(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mismatched-attempt-capability.sqlite3"
    store = _store(database_path)
    case = _setup_retry_case(store)
    bundle = _authorize_retry_case(case)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM capability_leases WHERE capability_lease_id = ?",
            (bundle.capability_lease.capability_lease_id,),
        )
    mismatched_capability = replace(
        case.original_capability,
        capability_lease_id="capability-lease-attempt-3",
        session_lease_id=bundle.session_lease.lease_id,
        authorized_attempt_number=3,
        previous_attempt_id=case.attempt.attempt_id,
    )
    with store.unit_of_work() as unit:
        unit.add_capability_lease(mismatched_capability)
        unit.commit()

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        case.dispatch.create_attempt(bundle.request.command_id, expected_authorized_attempt_number=1)

    assert exc_info.value.invariant == (
        "capability lease authorizes attempt 3, not next attempt 2"
    )
    with store.unit_of_work() as unit:
        attempts = unit.list_attempts(case.request.command_id)
    assert [attempt.attempt_id for attempt in attempts] == [
        case.attempt.attempt_id
    ]


def test_peek_idempotent_admission_returns_the_original_capability_lease(
    tmp_path: Path,
) -> None:
    """``peek_idempotent_admission`` returns the same lease identity too.

    The peek path is the one a caller uses *before* deciding to admit, so it
    is a separate way into ``_load_admission()`` and needs its own proof that
    it loads rather than mints.
    """

    database_path = tmp_path / "capability-peek.sqlite3"
    store = _store(database_path)
    _, _, original = _admit(store)

    peek_ids = _TaggedIdSource("peek")
    peeked = _service(store, ids=peek_ids, start=900).peek_idempotent_admission(
        _envelope(),
        authenticated_subject=AuthenticatedSubject(
            "principal-capability",
            "test",
        ),
        completion_contract=_completion_contract(),
    )

    assert peeked is not None
    assert peeked[3].capability_lease_id == original.capability_lease_id
    assert peeked[3] == original
    assert "capability-lease" not in peek_ids.namespaces
    assert _count_capability_leases(database_path) == 1


def test_peek_idempotent_admission_is_none_before_any_admission(
    tmp_path: Path,
) -> None:
    """No admission means no lease to hand back -- not a minted one."""

    database_path = tmp_path / "capability-peek-empty.sqlite3"
    store = _store(database_path)

    peek_ids = _TaggedIdSource("peek-empty")
    peeked = _service(store, ids=peek_ids).peek_idempotent_admission(
        _envelope(),
        authenticated_subject=AuthenticatedSubject(
            "principal-capability",
            "test",
        ),
        completion_contract=_completion_contract(),
    )

    assert peeked is None
    assert peek_ids.namespaces == []
    assert _count_capability_leases(database_path) == 0
