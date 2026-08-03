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


def test_process_supervisor_unimplemented_slice5_methods():
    supervisor = ProcessSupervisor()
    with pytest.raises(NotImplementedError):
        supervisor.begin_cancellation()

    with pytest.raises(NotImplementedError):
        supervisor.trigger_silence_timeout()

    with pytest.raises(NotImplementedError):
        supervisor.trigger_process_deadline()

    with pytest.raises(NotImplementedError):
        supervisor.on_tree_state(initial_identities=[1, 2])


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
