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
