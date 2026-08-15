"""Test concurrent attempt claims under the SQLite unit of work."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from peerhub.core.errors import ConcurrentAttemptClaimError
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource
from tests.integration.dispatch.test_retry_authorization import (
    _TaggedIds,
    _authorize,
    _setup_retry_case,
)


from pathlib import Path

@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    state_store = SqliteStateStore(
        tmp_path / "concurrent-claim.sqlite3",
        workspace_home_id="workspace-concurrent-claim",
    )
    state_store.initialize()
    return state_store


def test_two_sqlite_callers_have_one_winner_and_one_race_loser(
    store: SqliteStateStore,
) -> None:
    case = _setup_retry_case(store)
    # Both callers will try to claim attempt number 2
    # The authorization step (5B) creates the authorization for attempt 2
    bundle = _authorize(case)

    barrier = threading.Barrier(2)

    def call(tag: str) -> bool | ConcurrentAttemptClaimError:
        service = DispatchService(
            store,
            clock=DeterministicClock(start=500),
            ids=_TaggedIds(tag),
        )
        barrier.wait()
        try:
            service.create_attempt(
                bundle.request.command_id,
                expected_authorized_attempt_number=2,
            )
            return True
        except ConcurrentAttemptClaimError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(call, "t1")
        f2 = executor.submit(call, "t2")
        result1 = f1.result()
        result2 = f2.result()

    results = [result1, result2]
    successes = [r for r in results if r is True]
    races = [r for r in results if isinstance(r, ConcurrentAttemptClaimError)]

    assert len(successes) == 1
    assert len(races) == 1

    race = races[0]
    assert race.expected_attempt_number == 2
    assert race.authorized_attempt_number == 2
    assert race.next_attempt_number == 3

    with store.unit_of_work() as unit:
        attempts = unit.list_attempts(bundle.request.command_id)

    assert [a.attempt_number for a in attempts] == [1, 2]
    # One caller won attempt 2


def test_corrupted_capability_binding_raises_violation(
    store: SqliteStateStore,
) -> None:
    from peerhub.dispatch.capability import CapabilityLeaseViolation

    case = _setup_retry_case(store)
    bundle = _authorize(case)

    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=_TaggedIds("t1"),
    )

    # Claim attempt 2 successfully
    service.create_attempt(
        bundle.request.command_id,
        expected_authorized_attempt_number=2,
    )

    # Now the next attempt is 3, but the lease only authorizes 2.
    # If the caller provides a mismatched expectation (e.g. they thought they were authorized for 99),
    # it is a corrupted binding, not a legitimate race loser for attempt 2.
    with pytest.raises(CapabilityLeaseViolation) as exc:
        service.create_attempt(
            bundle.request.command_id,
            expected_authorized_attempt_number=99,
        )

    assert "capability lease authorizes attempt 2" in exc.value.invariant


def test_divergence_raises_violation_not_race_error(
    store: SqliteStateStore,
) -> None:
    from peerhub.dispatch.capability import CapabilityLeaseViolation
    from peerhub.dispatch.contract import AttemptSnapshot, RequestState
    from peerhub.core.execution import ExecutionCertainty

    case = _setup_retry_case(store)
    bundle = _authorize(case)

    service = DispatchService(
        store,
        clock=DeterministicClock(start=500),
        ids=_TaggedIds("t1"),
    )

    # Claim attempt 2 successfully
    service.create_attempt(
        bundle.request.command_id,
        expected_authorized_attempt_number=2,
    )

    # Artificially insert attempt 3 to advance next_attempt_number to 4
    with store.unit_of_work() as unit:
        unit.add_attempt(
            AttemptSnapshot(
                attempt_id="attempt-3",
                command_id=bundle.request.command_id,
                attempt_number=3,
                lease_id=bundle.request.lease_id,
                state=RequestState.FAILED_PRE_DISPATCH,
                execution_certainty=ExecutionCertainty.NOT_STARTED,
                revision=1,
                created_at=500,
                updated_at=500,
            )
        )
        unit.commit()

    # Now the durable next-attempt is 4, but the capability authorizes 2.
    # The caller expects 2. authorized == expected (2 == 2) is True.
    # But attempt_number (4) is NOT authorized + 1 (3).
    # This must raise CapabilityLeaseViolation, NOT ConcurrentAttemptClaimError.
    with pytest.raises(CapabilityLeaseViolation) as exc:
        service.create_attempt(
            bundle.request.command_id,
            expected_authorized_attempt_number=2,
        )

    assert "capability lease authorizes attempt 2, not next attempt 4" in exc.value.invariant
