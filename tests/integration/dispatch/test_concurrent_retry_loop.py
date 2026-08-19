"""Real-SQLite proofs for T1 increment 5C-3b: the outer retry loop's
concurrency wiring, exercised through the actual ``dispatch_with_retries()``
loop end-to-end (not the pure ``classify_concurrent_claim()`` classifier,
which is already covered by ``tests/unit/application/
test_retry_classification.py``).
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Iterator

import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    InvocationPlan,
    OutputDecoder,
    ProfileDescriptor,
    SessionHint,
)
from peerhub.application.retry import (
    MultiAttemptExecutionResult,
    RetryLoopStopReason,
)
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.execution import TransportLimits
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, deterministic_uuid4
from tests.integration.dispatch.test_retry_authorization import _TaggedIds
from tests.unit.application.test_workflows_dispatch_and_execute import (
    _CONTROLLED_FAKE_EVIDENCE,
    _seed_health,
    _workflows,
)
from tests.unit.application.test_workflows_dispatch_with_retries import (
    _admit_and_prepare_with_contract,
    _contract,
    _count_attempts,
    _plan,
    _route_decision_id,
    _run,
    _setup,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "concurrent-retry-loop.sqlite3",
        workspace_home_id="workspace-concurrent-retry-loop",
    )
    state_store.initialize()
    _seed_health(state_store)
    yield state_store


def _lease_and_capability_counts(store: SqliteStateStore) -> tuple[int, int]:
    with sqlite3.connect(store.database_path) as connection:
        leases = connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        capabilities = connection.execute(
            "SELECT COUNT(*) FROM capability_leases"
        ).fetchone()[0]
    return int(leases), int(capabilities)


# ---------------------------------------------------------------------------
# (a) two real concurrent callers race at the attempt-claim boundary
#     (create_attempt() / ConcurrentAttemptClaimError), driven through the
#     real dispatch_with_retries() loop.
#
# Covers verification target items:
#   3 (concurrent authorization with no N+1 row returns
#      CONCURRENT_ATTEMPT_IN_PROGRESS -- here at the attempt-creation seam
#      rather than the authorization seam, per item 10's requirement that
#      both seams share loser semantics),
#   6 (exactly one N+1 attempt row and at most one process spawn occur),
#   7 (the loser creates no lease/capability/attempt/process side effect).
# ---------------------------------------------------------------------------


def test_two_real_callers_race_at_attempt_creation_one_loses_cleanly(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    class _BlockingAdapter(FakePeerAdapter):
        def __init__(self) -> None:
            super().__init__(stdout="ok\n")
            self.loser_done = threading.Event()

        def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
            # The winner pauses here (after create_attempt and record_dispatch_intent,
            # while attempt 1 is durably recorded and in-progress in SQLite) until
            # the loser has cleanly observed CONCURRENT_ATTEMPT_IN_PROGRESS.
            self.loser_done.wait(timeout=10.0)
            return super().new_decoder(plan)

    adapter = _BlockingAdapter()
    workflows, command_id, plan, contract = _setup(
        store,
        tmp_path,
        adapter=adapter,
    )

    before_leases, before_capabilities = _lease_and_capability_counts(store)
    barrier = threading.Barrier(2)

    def call(tag: str) -> MultiAttemptExecutionResult:
        service = DispatchService(
            store,
            clock=DeterministicClock(start=500),
            ids=_IntruderIds(tag),
            enforcement_evidence=_CONTROLLED_FAKE_EVIDENCE,
        )
        barrier.wait()
        return _run(
            workflows,
            command_id,
            plan,
            tmp_path=tmp_path,
            store=store,
            contract=contract,
            service=service,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_one = executor.submit(call, "one")
        future_two = executor.submit(call, "two")

        # The winner claims attempt 1 and pauses in new_decoder.
        # The loser fails at create_attempt and completes immediately.
        done, not_done = wait(
            [future_one, future_two], return_when=FIRST_COMPLETED, timeout=10.0
        )
        adapter.loser_done.set()

        results = [f.result() for f in list(done) + list(not_done)]

    winners = [
        r for r in results if r.stop_reason is RetryLoopStopReason.VERIFIED_SUCCESS
    ]
    losers = [
        r
        for r in results
        if r.stop_reason is RetryLoopStopReason.CONCURRENT_ATTEMPT_IN_PROGRESS
    ]

    # Exactly one winner proceeds normally; exactly one loser backs off
    # cleanly via the new ConcurrentAttemptClaimError -> classify ->
    # CONCURRENT_ATTEMPT_IN_PROGRESS wiring, never a retry of its own.
    assert len(winners) == 1
    assert len(losers) == 1

    loser = losers[0]
    assert len(loser.attempts) == 1
    assert loser.attempts[0].retry_authorization is None

    # Item 6: exactly one durable N+1 (attempt 1) row exists, meaning at
    # most one process was ever spawned for this command.
    assert _count_attempts(store) == 1

    # Item 7: the loser created no additional lease/capability row. It never
    # reached materialization or process supervision either, since
    # create_attempt() raises before either of those steps runs.
    after_leases, after_capabilities = _lease_and_capability_counts(store)
    assert after_leases == before_leases
    assert after_capabilities == before_capabilities


# ---------------------------------------------------------------------------
# (b) a race where the winner's attempt is already terminal by the time the
#     loser reloads state hits ATTEMPT_TERMINAL_REBUILD and adjudicates from
#     durable facts rather than re-executing.
#
# Real thread timing cannot reliably force "the winner is already terminal
# by the time the loser reloads" -- the loser's create_attempt() unblocks
# essentially immediately after the winner's short create_attempt()
# transaction commits, well before the winner's own subsequent
# materialize/spawn/adjudicate steps finish. So this uses a controlled/fake
# execution path (as the task anticipates): a real, non-mocked adapter
# subclass whose plan_invocation() -- called by the loop's own attempt-2
# dispatch, after its own authorize_retry() already committed attempt 2's
# authorization -- lets an independent DispatchService durably create *and*
# terminalize attempt 2 first. The loop's own subsequent create_attempt()
# call for attempt 2 then deterministically observes an already-terminal
# durable winner, forcing exactly the timing item (b) requires.
#
# Covers verification target items:
#   5 (a terminal N+1 is rebuilt and adjudicated rather than executed
#      again), 8 (resume refetches durable facts on the next iteration),
#   9 (bounded; this is a one-shot REBUILD, not a spin).
# ---------------------------------------------------------------------------


class _IntruderIds(_TaggedIds):
    """``_TaggedIds`` extended to emit UUIDv4 outbox-event ids.

    ``fail_pre_dispatch()`` dispatches an outbox event, which requires an
    RFC 4122 UUIDv4 id; plain ``_TaggedIds`` (built for the raced
    ``create_attempt()``/``authorize_retry()`` calls elsewhere, which never
    reach the outbox) does not special-case that namespace the way
    ``SequentialIdSource`` does.
    """

    def new_id(self, namespace: str) -> str:
        raw = super().new_id(namespace)
        if namespace == "outbox-event":
            return deterministic_uuid4(raw)
        return raw


class _AttemptTwoIntruderAdapter(FakePeerAdapter):
    """A real ``FakePeerAdapter`` whose second invocation is preceded by an
    independent caller durably winning and terminalizing attempt 2.

    Both the wrapped behavior and the injected side effect use real
    dataclass/service instances (``DispatchService``, ``AttemptSnapshot``
    returned by ``create_attempt()``) -- nothing here is ``Mock(spec=...)``.
    """

    def __init__(self, *, store: SqliteStateStore, command_id: str) -> None:
        super().__init__(stdout="failing\n", exit_code=3)
        self._store = store
        self._command_id = command_id
        self.plan_invocation_calls = 0
        self.injected_attempt_id: str | None = None

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ):
        self.plan_invocation_calls += 1
        if self.plan_invocation_calls == 2 and self.injected_attempt_id is None:
            intruder = DispatchService(
                self._store,
                clock=DeterministicClock(start=999),
                ids=_IntruderIds("intruder"),
            )
            attempt_two = intruder.create_attempt(
                self._command_id,
                expected_authorized_attempt_number=2,
            )
            intruder.fail_pre_dispatch(
                self._command_id,
                attempt_two.attempt_id,
                error_code=ErrorCode.SPAWN_FAILED,
                transport="pipe",
            )
            self.injected_attempt_id = attempt_two.attempt_id
        return super().plan_invocation(request, profile, session, limits)


def test_terminal_winner_by_reload_time_rebuilds_instead_of_re_executing(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    workflows, _dispatch = _workflows(store)
    contract = _contract(replay_safe=True)
    command_id, capability_lease_id, peer_instance = (
        _admit_and_prepare_with_contract(workflows, contract)
    )
    adapter = _AttemptTwoIntruderAdapter(store=store, command_id=command_id)
    plan = _plan(
        capability_lease_id=capability_lease_id,
        peer_instance_id=peer_instance,
        route_decision_id=_route_decision_id(workflows, command_id),
        adapter=adapter,
        contract=contract,
    )

    result = _run(
        workflows,
        command_id,
        plan,
        tmp_path=tmp_path,
        store=store,
        contract=contract,
        max_attempts=3,
    )

    # The intruder durably won and terminalized attempt 2 before the loop's
    # own create_attempt() call for attempt 2 ran, so that call raised
    # ConcurrentAttemptClaimError. The loop rebuilt from the durable terminal
    # attempt 2 and adjudicated it (RETRY_SAME_TARGET, replay-safe), then
    # went on to authorize and really execute attempt 3, which exhausts
    # max_attempts=3.
    assert adapter.injected_attempt_id is not None
    assert result.stop_reason is RetryLoopStopReason.ATTEMPT_LIMIT_REACHED

    # Exactly 3 durable attempts exist: 1 (real), 2 (intruder-created,
    # rebuilt -- never re-created by the loop), 3 (real). No duplicate
    # attempt-2 row was ever created, proving the loop did not re-execute
    # the contested attempt.
    assert _count_attempts(store) == 3

    # plan_invocation() was called exactly 3 times: attempt 1 (real,
    # fails), attempt 2 (the injection call -- its returned plan is
    # discarded because create_attempt() raises immediately afterward, so
    # no second process ever spawns for attempt 2), attempt 3 (real,
    # fails and exhausts the limit). A buggy rebuild that re-executed
    # attempt 2 would show a 4th call instead.
    assert adapter.plan_invocation_calls == 3
