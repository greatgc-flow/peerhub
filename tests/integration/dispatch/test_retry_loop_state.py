"""Real-SQLite proofs for the durable retry-loop read model."""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

import pytest

from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    RetryPolicyConflictError,
)
from peerhub.dispatch.contract import RequestState, RetryLoopState
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource
from tests.integration.application.test_workflows_kernel import (
    _admission_kwargs,
    _envelope,
    _route_request_factory,
    _seed_health,
    _workflows,
)
from tests.integration.dispatch.test_retry_authorization import (
    _authorize,
    _failover_intent,
    _setup_retry_case,
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    state_store = SqliteStateStore(
        tmp_path / "retry-loop-state.sqlite3",
        workspace_home_id="workspace-retry-loop-state",
    )
    state_store.initialize()
    return state_store


def _admit_without_attempt(
    store: SqliteStateStore,
) -> tuple[DispatchService, str]:
    _seed_health(store)
    workflows = _workflows(store)
    admission = workflows.admit_request(
        _envelope(),
        route_request_factory=_route_request_factory(
            client_request_id="client-request-01",
            configuration_revision=11,
        ),
        **_admission_kwargs(),
    )
    assert admission.dispatch_admission is not None
    request = admission.dispatch_admission[0]
    dispatch = workflows._dispatch  # pyright: ignore[reportPrivateUsage]
    return dispatch, str(request.command_id)


def test_retry_loop_state_missing_request_and_corrupt_history_fail_closed(
    store: SqliteStateStore,
) -> None:
    dispatch = DispatchService(
        store,
        clock=DeterministicClock(start=200),
        ids=SequentialIdSource(),
    )
    with pytest.raises(RecordNotFoundError):
        dispatch.load_retry_loop_state("missing-command")

    case = _setup_retry_case(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE dispatch_attempts SET attempt_number = 2 WHERE attempt_id = ?",
            (case.attempt.attempt_id,),
        )

    with pytest.raises(InvalidMutationError, match="attempt history"):
        case.dispatch.load_retry_loop_state(case.request.command_id)


def test_retry_loop_state_accepts_empty_history_only_before_first_attempt(
    store: SqliteStateStore,
) -> None:
    dispatch, command_id = _admit_without_attempt(store)

    state = dispatch.load_retry_loop_state(command_id)

    assert state.attempts == ()
    assert state.current_capability.authorized_attempt_number == 1

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE dispatch_requests SET state = 'FAILED' WHERE command_id = ?",
            (command_id,),
        )

    with pytest.raises(InvalidMutationError, match="empty attempt history"):
        dispatch.load_retry_loop_state(command_id)


def test_retry_loop_state_loads_one_through_n_and_rejects_gap_or_duplicate(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    _authorize(case)
    second = case.dispatch.create_attempt(case.request.command_id)

    state = case.dispatch.load_retry_loop_state(case.request.command_id)

    assert tuple(attempt.attempt_number for attempt in state.attempts) == (1, 2)

    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE dispatch_attempts SET attempt_number = 1 WHERE attempt_id = ?",
                (second.attempt_id,),
            )
        connection.rollback()
        connection.execute(
            "UPDATE dispatch_attempts SET attempt_number = 3 WHERE attempt_id = ?",
            (second.attempt_id,),
        )

    with pytest.raises(InvalidMutationError, match="attempt history"):
        case.dispatch.load_retry_loop_state(case.request.command_id)


def test_retry_policy_freeze_inserts_once_returns_equal_and_rejects_conflict(
    store: SqliteStateStore,
) -> None:
    dispatch, command_id = _admit_without_attempt(store)

    assert dispatch.freeze_retry_policy(command_id, 3) == 3
    assert dispatch.freeze_retry_policy(command_id, 3) == 3
    with pytest.raises(RetryPolicyConflictError):
        dispatch.freeze_retry_policy(command_id, 4)

    with sqlite3.connect(store.database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM retry_policies WHERE command_id = ?",
            (command_id,),
        ).fetchone()[0]
    assert count == 1


def test_retry_loop_state_reads_every_bound_record_from_one_snapshot(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)

    with patch.object(
        store,
        "read_unit_of_work",
        wraps=store.read_unit_of_work,
    ) as read_unit_of_work:
        state = case.dispatch.load_retry_loop_state(case.request.command_id)

    assert read_unit_of_work.call_count == 1
    assert state.request == case.request
    assert state.max_attempts == 3
    assert state.attempts == (case.attempt,)
    assert state.current_lease.lease_id == state.request.lease_id
    assert (
        state.current_capability.session_lease_id
        == state.current_lease.lease_id
    )
    assert state.route_decision.decision_id == case.original_route_decision.decision_id


def test_retry_loop_state_recovers_replacement_route_after_failover(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store, failover_ready=True)
    bundle = _authorize(case, route_intent=_failover_intent(case))

    state = case.dispatch.load_retry_loop_state(case.request.command_id)

    assert state.route_decision == bundle.route_decision
    assert (
        state.route_decision.decision_id
        != case.original_route_decision.decision_id
    )
    assert state.request.route_decision_digest == bundle.capability_lease.route_decision_digest


def test_retry_loop_state_exposes_only_durable_reconstructable_facts(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)

    state = case.dispatch.load_retry_loop_state(case.request.command_id)

    assert tuple(field.name for field in fields(RetryLoopState)) == (
        "request",
        "max_attempts",
        "attempts",
        "current_lease",
        "current_capability",
        "route_decision",
    )
    assert state.request.state is RequestState.FAILED_PRE_DISPATCH
    assert not hasattr(state, "execution")
    assert not hasattr(state, "decoded_output")
    assert not hasattr(state, "materialization")
