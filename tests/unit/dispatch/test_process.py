"""Unit tests for ProcessSupervisor pure reducer logic in Slice 5 Step 5."""

import pytest
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.process import (
    ProcessSupervisor,
    TerminalClassification,
    ProcessCleanupEvidence,
)


def test_process_supervisor_on_spawned_with_birth_identity():
    supervisor = ProcessSupervisor()
    identity = ProcessBirthIdentity(pid=1234, process_creation_time=5678)
    supervisor.on_spawned(identity=identity)

    assert supervisor.identity == identity
    outcome = supervisor.finalize_execution_outcome()
    assert outcome.execution_outcome.started is True
    # Without an exit code recorded, the process is still in-flight
    # (STARTED, not TERMINAL).
    assert outcome.effect_certainty == ExecutionCertainty.STARTED


def test_process_supervisor_on_spawned_with_pid_only():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=999)

    assert supervisor.identity is not None
    assert supervisor.identity.pid == 999
    assert supervisor.identity.process_creation_time == 0


def test_process_supervisor_on_spawned_invalid_args():
    supervisor = ProcessSupervisor()
    with pytest.raises(ValueError, match="on_spawned requires either pid or identity"):
        supervisor.on_spawned()


def test_process_supervisor_dt01_reduction():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=1001)
    supervisor.on_chunk(b"hello ", timestamp_ms=100)
    supervisor.on_chunk(b"world\n", timestamp_ms=105)
    supervisor.on_exit(exit_code=0)

    outcome = supervisor.finalize_execution_outcome()
    assert outcome.exit_code == 0
    assert outcome.stream_events_ordered is True
    assert outcome.canonical_stream == b"hello world\n"
    assert outcome.terminal_classification is None
    assert outcome.cleanup_failed is False
    assert outcome.cleanup_evidence is None
    # Spawned + known exit code + not timed out + not cancelled => TERMINAL
    assert outcome.effect_certainty == ExecutionCertainty.TERMINAL


def test_process_supervisor_out_of_order_stream():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=1001)
    supervisor.on_chunk(b"chunk1", timestamp_ms=200)
    supervisor.on_chunk(b"chunk2", timestamp_ms=100)  # out of order

    outcome = supervisor.finalize_execution_outcome()
    assert outcome.stream_events_ordered is False
    assert outcome.canonical_stream == b"chunk1chunk2"


def test_process_supervisor_dt06_reduction():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(
        identity=ProcessBirthIdentity(pid=2002, process_creation_time=10)
    )
    supervisor.on_chunk(b"error stream", timestamp_ms=10)
    supervisor.on_exit(exit_code=1)
    supervisor.on_cleanup_error(unresolved_identities=[2002])

    outcome = supervisor.finalize_execution_outcome()
    assert outcome.exit_code == 1
    assert outcome.terminal_classification == TerminalClassification.EXIT_NON_ZERO
    assert outcome.cleanup_failed is True
    assert outcome.cleanup_evidence is not None
    assert outcome.cleanup_evidence.unresolved_identities == [2002]


def test_process_supervisor_dt03_first_wins_silence_timeout():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=100)
    supervisor.trigger_silence_timeout()

    outcome = supervisor.finalize_execution_outcome()
    assert outcome.terminal_classification == TerminalClassification.SILENCE_TIMEOUT
    assert outcome.execution_outcome.timed_out is True
    assert outcome.execution_outcome.cancelled is False

    # Second trigger doesn't overwrite first (first-wins)
    supervisor.trigger_process_deadline()
    outcome2 = supervisor.finalize_execution_outcome()
    assert outcome2.terminal_classification == TerminalClassification.SILENCE_TIMEOUT


def test_process_supervisor_dt03_first_wins_process_deadline():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=200)
    supervisor.trigger_process_deadline()

    outcome = supervisor.finalize_execution_outcome()
    assert outcome.terminal_classification == TerminalClassification.PROCESS_TIMEOUT
    assert outcome.execution_outcome.timed_out is True
    assert outcome.execution_outcome.cancelled is False

    # Second trigger doesn't overwrite first
    supervisor.trigger_silence_timeout()
    outcome2 = supervisor.finalize_execution_outcome()
    assert outcome2.terminal_classification == TerminalClassification.PROCESS_TIMEOUT


def test_process_supervisor_dt04_begin_cancellation_idempotent():
    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=300)
    decision1 = supervisor.begin_cancellation(now_ms=1000)

    assert decision1.stage.value == "SOFT_CANCEL"
    assert decision1.action.value == "SOFT_CANCEL"
    assert decision1.next_deadline_ms == 6000  # 1000 + 5000 soft grace

    # Second call is idempotent
    decision2 = supervisor.begin_cancellation(now_ms=2000)
    assert decision2.stage.value == "SOFT_CANCEL"
    assert decision2.next_deadline_ms == 6000


def test_process_supervisor_dt05_on_tree_state_observations():
    from peerhub.dispatch.process import (
        ObservationState,
        TreeProcessObservation,
    )

    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=400)

    obs = [
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=400, process_creation_time=10),
            state=ObservationState.RUNNING,
        ),
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=401, process_creation_time=20),
            state=ObservationState.IDENTITY_UNCERTAIN,
        ),
    ]

    decision = supervisor.on_tree_state(observations=obs, now_ms=1000)
    assert decision.unresolved_identities == (400, 401)
    assert decision.all_terminated is False

    # Advance time past soft grace to escalate to TERMINATE_TREE
    decision2 = supervisor.on_tree_state(observations=obs, now_ms=7000)
    assert decision2.stage.value == "TERMINATE_TREE"
    assert decision2.action.value == "TERMINATE_TREE"

    # All terminated observations allow COMPLETED
    all_term_obs = [
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=400, process_creation_time=10),
            state=ObservationState.TERMINATED,
        ),
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=401, process_creation_time=20),
            state=ObservationState.TERMINATED,
        ),
    ]
    decision3 = supervisor.on_tree_state(observations=all_term_obs, now_ms=8000)
    assert decision3.stage.value == "COMPLETED"
    assert decision3.all_terminated is True
    assert decision3.unresolved_identities == ()



class TestProcessSupervisorTerminalCertainty:
    """Bug 3 regression: finalize_execution_outcome must produce TERMINAL
    for a spawned process that exits with a known exit code, not timed out,
    not cancelled.  Without this, completion.py's entire VERIFIED/INCOMPLETE/
    UNVERIFIED decision table is unreachable."""

    def test_clean_exit_produces_terminal(self):
        """DT-01: ordered stream + clean exit => TERMINAL."""
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=100)
        supervisor.on_chunk(b"output", timestamp_ms=10)
        supervisor.on_exit(exit_code=0)

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.TERMINAL
        assert outcome.execution_outcome.started is True
        assert outcome.execution_outcome.exit_code == 0
        assert outcome.execution_outcome.timed_out is False
        assert outcome.execution_outcome.cancelled is False

    def test_nonzero_exit_produces_terminal(self):
        """A process that exits with nonzero code is still TERMINAL
        (deterministic, observed completion)."""
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=200)
        supervisor.on_exit(exit_code=1)

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.TERMINAL

    def test_cleanup_failure_preserves_terminal(self):
        """DT-06: cleanup failure preserves primary TERMINAL result."""
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(
            identity=ProcessBirthIdentity(pid=300, process_creation_time=10)
        )
        supervisor.on_chunk(b"data", timestamp_ms=10)
        supervisor.on_exit(exit_code=0)
        supervisor.on_cleanup_error(unresolved_identities=[300])

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.TERMINAL
        assert outcome.cleanup_failed is True

    def test_spawned_no_exit_stays_started(self):
        """Spawned but no exit code yet => STARTED (in-flight)."""
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=400)
        # No on_exit call => process still running

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.STARTED

    def test_not_spawned_stays_not_started(self):
        """Never spawned => NOT_STARTED."""
        supervisor = ProcessSupervisor()
        # No on_spawned call

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.NOT_STARTED

    def test_terminal_unlocks_completion_assessment(self):
        """Integration-level check: a TERMINAL execution outcome allows
        completion.py's assess_completion to return a real assessment
        (not NOT_APPLICABLE).

        This is the definitive Bug 3 regression test."""
        from peerhub.adapters.contract import ProtocolAssessment
        from peerhub.dispatch.completion import assess_completion
        from peerhub.dispatch.contract import (
            CompletionAssessmentState,
            CompletionContract,
            CompletionContractKind,
        )

        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=500)
        supervisor.on_chunk(b"response data", timestamp_ms=10)
        supervisor.on_exit(exit_code=0)

        outcome = supervisor.finalize_execution_outcome()
        assert outcome.effect_certainty == ExecutionCertainty.TERMINAL

        contract = CompletionContract(
            contract_id="test-contract",
            kind=CompletionContractKind.DELIVERY_ONLY,
            requirements=(),
            replay_safe=False,
        )
        protocol = ProtocolAssessment(
            parsed=True,
            response_present=True,
            vendor_completion_marker=None,
            suspected_truncation=False,
            protocol_failure=None,
        )

        assessment = assess_completion(
            contract,
            outcome.execution_outcome,
            protocol,
        )

        # With the bug, this was ALWAYS NOT_APPLICABLE.
        assert assessment.state != CompletionAssessmentState.NOT_APPLICABLE, (
            "assess_completion returned NOT_APPLICABLE for a clean TERMINAL "
            "execution — Bug 3 is still present"
        )
        assert assessment.state == CompletionAssessmentState.VERIFIED


class TestProcessSupervisorThreadSafety:
    """Decision B regression: ProcessSupervisor must be safe across threads.

    These tests verify the threading.Lock protects shared state when
    on_chunk (stream-reader thread), begin_cancellation (heartbeat thread),
    and finalize_execution_outcome (main thread) are called concurrently.
    """

    def test_concurrent_on_chunk_from_multiple_threads(self):
        """Concurrent on_chunk calls from multiple stream-reader threads
        must not lose chunks or corrupt state."""
        import threading

        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=9000)

        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def writer(data: bytes, base_ts: int, count: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(count):
                    supervisor.on_chunk(data, timestamp_ms=base_ts + i)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(b"A", 0, 50)),
            threading.Thread(target=writer, args=(b"B", 100, 50)),
            threading.Thread(target=writer, args=(b"C", 200, 50)),
            threading.Thread(target=writer, args=(b"D", 300, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        supervisor.on_exit(exit_code=0)
        outcome = supervisor.finalize_execution_outcome()
        # All 200 chunks must be present (4 threads × 50 chunks).
        assert len(outcome.canonical_stream) == 200

    def test_begin_cancellation_from_background_thread(self):
        """begin_cancellation called from a background thread (simulating
        the heartbeat thread) must not race with on_chunk."""
        import threading

        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=9001)

        cancel_result: list = []

        def cancel_worker() -> None:
            decision = supervisor.begin_cancellation(now_ms=1000)
            cancel_result.append(decision)

        t = threading.Thread(target=cancel_worker)
        t.start()
        # Concurrently feed chunks while cancellation starts.
        for i in range(20):
            supervisor.on_chunk(b"x", timestamp_ms=500 + i)
        t.join(timeout=5)

        assert len(cancel_result) == 1
        assert cancel_result[0].stage.value == "SOFT_CANCEL"
        # Supervisor should report cancellation active.
        assert supervisor.cancellation_active is True

    def test_has_lock_attribute(self):
        """ProcessSupervisor must have a threading.Lock as per Decision B."""
        import threading

        supervisor = ProcessSupervisor()
        assert hasattr(supervisor, "_lock")
        assert isinstance(supervisor._lock, type(threading.Lock()))


class TestProcessSupervisorCancellationActive:
    """Tests for the cancellation_active property."""

    def test_cancellation_active_false_initially(self):
        supervisor = ProcessSupervisor()
        assert supervisor.cancellation_active is False

    def test_cancellation_active_true_after_begin(self):
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=5000)
        supervisor.begin_cancellation(now_ms=0)
        assert supervisor.cancellation_active is True

    def test_cancellation_active_true_after_silence_timeout(self):
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=5001)
        supervisor.trigger_silence_timeout(now_ms=0)
        assert supervisor.cancellation_active is True

    def test_cancellation_active_true_after_process_deadline(self):
        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=5002)
        supervisor.trigger_process_deadline(now_ms=0)
        assert supervisor.cancellation_active is True

