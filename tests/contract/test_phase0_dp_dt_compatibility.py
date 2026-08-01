"""
Slice 5 compatibility tests for Phase 0 vectors DP-06 and DT-01..06.

Note on Step 2 (Contracts):
This file relies on a genuinely new result type `ProcessSupervisionOutcome`
and new enumerations like `TerminalClassification`. These must be formally
defined in Step 2, separate from the already-existing `ExecutionOutcome`
in `peerhub.dispatch.contract`.
"""

from __future__ import annotations

import pytest

# Existing real types from the current codebase
from peerhub.core.execution import ExecutionCertainty


def test_dp06_post_intent_crash_recovery_is_uncertain():
    """
    DP-06: Crash after durable isolated-journal append of INTENT_PERSISTED,
    but before reduction. Recovery MUST be MAY_HAVE_STARTED / UNKNOWN, MUST NOT
    automatically replay, and MUST retain the journal digest.
    (CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md / R2.md)
    """
    try:
        from peerhub.dispatch.process import TerminalClassification
        from peerhub.dispatch.service import recover_interrupted_attempt
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    # Setup: simulate a durable journal append of INTENT_PERSISTED without
    # any subsequent SPAWNED, EXIT, or terminal evidence.
    journal_entries = ["INTENT_PERSISTED"]
    journal_digest = "sha256:fake_digest_123"

    recovered_state = recover_interrupted_attempt(
        journal_entries=journal_entries,
        journal_digest=journal_digest
    )

    assert recovered_state.terminal_classification == TerminalClassification.START_UNCERTAIN
    assert recovered_state.effect_certainty == ExecutionCertainty.MAY_HAVE_STARTED
    # `ExecutionOutcome` is the existing dispatch contract type, which we expect to
    # be populated with safe uncertainty defaults here.
    assert recovered_state.execution_outcome.started is False
    assert recovered_state.execution_outcome.execution_certainty == ExecutionCertainty.MAY_HAVE_STARTED

    # Must not automatically replay
    assert not recovered_state.automatic_replay_authorized
    # Must retain journal digest
    assert recovered_state.journal_digest == journal_digest


def test_dt01_ordered_stream_and_clean_exit():
    """
    DT-01: PTY emits ordered timestamped chunks, clean exit (0), terminal receipt.
    (V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md)
    """
    try:
        from peerhub.dispatch.process import ProcessSupervisor
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    supervisor = ProcessSupervisor()

    # Simulate chronological process events
    supervisor.on_spawned(pid=1001)
    supervisor.on_chunk(b"hello ", timestamp_ms=100)
    supervisor.on_chunk(b"world\n", timestamp_ms=110)
    supervisor.on_exit(exit_code=0)

    # ProcessSupervisionOutcome is a new type to be defined in Step 2
    outcome = supervisor.finalize_execution_outcome()

    assert outcome.exit_code == 0
    assert outcome.stream_events_ordered
    assert b"hello world\n" in outcome.canonical_stream


def test_dt02_incremental_framing_split_boundaries():
    """
    DT-02: Split UTF-8 and CR/LF across chunks. Canonical text/line event order
    must be independent of read chunking.
    (DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md)
    """
    try:
        from peerhub.builtins.fake_adapter import FakePeerAdapter
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    adapter = FakePeerAdapter()

    # Simulate a chunk boundary splitting a UTF-8 character and a \r\n
    chunk1 = b"line 1\r"
    chunk2 = b"\nline 2: \xe2"
    chunk3 = b"\x9c\x93\n"  # Checkmark emoji

    adapter.interpret_chunk(chunk1)
    adapter.interpret_chunk(chunk2)
    adapter.interpret_chunk(chunk3)

    assessment = adapter.finalize_protocol_assessment()

    # Framing must correctly reassemble the lines
    assert assessment.canonical_lines == ["line 1", "line 2: ✓"]


def test_dt03_timeout_selection_independence():
    """
    DT-03: Silence expiry classifies as SILENCE_TIMEOUT, process-deadline expiry
    classifies as PROCESS_TIMEOUT. Neither case inherits the other's terminal result.
    (DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md)
    """
    try:
        from peerhub.dispatch.process import ProcessSupervisor, TerminalClassification
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    supervisor1 = ProcessSupervisor()
    supervisor1.on_spawned(pid=1002)
    supervisor1.trigger_silence_timeout()
    outcome1 = supervisor1.finalize_execution_outcome()

    assert outcome1.terminal_classification == TerminalClassification.SILENCE_TIMEOUT

    supervisor2 = ProcessSupervisor()
    supervisor2.on_spawned(pid=1003)
    supervisor2.trigger_process_deadline()
    outcome2 = supervisor2.finalize_execution_outcome()

    assert outcome2.terminal_classification == TerminalClassification.PROCESS_TIMEOUT


def test_dt04_cancellation_ladder_and_uncertainty():
    """
    DT-04: Cancellation ladder. Ignore first cancel, obey bounded termination.
    Retain PROCESS_TIMEOUT with MAY_HAVE_STARTED uncertainty if termination
    is not conclusively reconciled.
    (DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md)
    """
    try:
        from peerhub.dispatch.process import (
            ProcessSupervisor,
            TerminalClassification,
            CancellationLadder,
        )
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    ladder = CancellationLadder()
    supervisor = ProcessSupervisor(cancellation_ladder=ladder)
    supervisor.on_spawned(pid=1004)

    supervisor.begin_cancellation()
    # Fake process ignores the first SIGTERM equivalent
    supervisor.trigger_process_deadline()  # Bounded termination triggered

    outcome = supervisor.finalize_execution_outcome()

    assert outcome.terminal_classification == TerminalClassification.PROCESS_TIMEOUT
    assert outcome.effect_certainty == ExecutionCertainty.MAY_HAVE_STARTED


def test_dt05_process_tree_closure():
    """
    DT-05: Tree closure. Every initial identity must be proven terminated or
    appear in an unresolved set yielding an explicit CANCELLATION_CLEANUP_FAILED.
    (DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md)
    """
    try:
        from peerhub.dispatch.process import ProcessSupervisor
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=1005)

    # Simulate a TREE_STATE event identifying a child process
    supervisor.on_tree_state(initial_identities=[1005, 1006])

    supervisor.begin_cancellation()
    # Simulate child 1006 failing to terminate
    supervisor.on_cleanup_error(unresolved_identities=[1006])

    outcome = supervisor.finalize_execution_outcome()

    assert outcome.cleanup_failed
    assert outcome.cleanup_evidence.unresolved_identities == [1006]
    assert outcome.cleanup_evidence.reason == "CANCELLATION_CLEANUP_FAILED"


def test_dt06_cleanup_failure_preserves_primary_result():
    """
    DT-06: Partial output, primary failure, CLEANUP_ERROR. Primary terminal state
    remains authoritative/unchanged; cleanup error is attached.
    (V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md)
    """
    try:
        from peerhub.dispatch.process import ProcessSupervisor, TerminalClassification
    except ImportError as e:
        pytest.fail(f"TDD failure: missing Slice 5 module: {e}")

    supervisor = ProcessSupervisor()
    supervisor.on_spawned(pid=1007)

    # Primary failure
    supervisor.on_exit(exit_code=1)

    # Subsequent cleanup failure
    supervisor.on_cleanup_error(unresolved_identities=[1007])

    outcome = supervisor.finalize_execution_outcome()

    # Primary result remains authoritative
    assert outcome.exit_code == 1
    assert outcome.terminal_classification == TerminalClassification.EXIT_NON_ZERO
    # Cleanup evidence is attached
    assert outcome.cleanup_failed
    assert outcome.cleanup_evidence is not None
