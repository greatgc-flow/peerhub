"""Unit tests for the pipe-based process runner (dispatch.pipe).

Tests use trivial ``python -c "..."`` subprocesses so they are
hermetic and never depend on external peer CLIs or consume quota.
"""

from __future__ import annotations

import sys

import pytest
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.pipe import PipeRunnerConfig, run_process
from peerhub.dispatch.process import (
    ProcessSupervisor,
    TerminalClassification,
)


class TestPipeRunnerSpawnAndIdentity:
    """Test that pipe.py correctly spawns and calls on_spawned."""

    def test_spawns_and_calls_on_spawned_with_identity(self):
        """A trivial process is spawned and ProcessBirthIdentity is set."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "print('hello')"],
        )

        outcome = run_process(config, supervisor)

        # on_spawned must have been called -- identity is set.
        assert supervisor.identity is not None
        assert isinstance(supervisor.identity, ProcessBirthIdentity)
        assert supervisor.identity.pid > 0

    def test_identity_has_valid_pid(self):
        """The recorded PID is a real positive integer."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "pass"],
        )

        run_process(config, supervisor)

        assert supervisor.identity is not None
        assert supervisor.identity.pid > 0


class TestPipeRunnerChunks:
    """Test that pipe.py streams chunks via on_chunk correctly."""

    def test_stdout_chunks_captured(self):
        """stdout output is captured and forwarded via on_chunk."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "print('hello world')"],
        )

        outcome = run_process(config, supervisor)

        # canonical_stream contains all stdout data.
        assert b"hello world" in outcome.canonical_stream

    def test_stderr_captured_as_chunks(self):
        """stderr output is captured and forwarded via on_chunk.

        Per ProcessSupervisor's existing contract, both stdout and
        stderr are fed as chunks.  This test verifies stderr is not
        silently dropped.
        """
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('err_output\\n')",
            ],
        )

        outcome = run_process(config, supervisor)

        # stderr content appears in the canonical stream.
        assert b"err_output" in outcome.canonical_stream

    def test_stdout_and_stderr_both_captured(self):
        """Both stdout and stderr are captured distinctly.

        The current ProcessSupervisor interleaves them chronologically
        into canonical_stream.  Both must be present.
        """
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.write('OUT\\n'); sys.stdout.flush(); "
                    "sys.stderr.write('ERR\\n'); sys.stderr.flush()"
                ),
            ],
        )

        outcome = run_process(config, supervisor)

        assert b"OUT" in outcome.canonical_stream
        assert b"ERR" in outcome.canonical_stream


class TestPipeRunnerExitCode:
    """Test that on_exit is called with the correct exit code."""

    def test_clean_exit_code_zero(self):
        """A process exiting with code 0 is recorded correctly."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "pass"],
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 0
        assert outcome.execution_outcome.started is True
        assert (
            outcome.execution_outcome.execution_certainty
            == ExecutionCertainty.STARTED
        )
        # Clean exit => no terminal classification per existing reducer.
        assert outcome.terminal_classification is None

    def test_nonzero_exit_code(self):
        """A process exiting with nonzero code is EXIT_NON_ZERO."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "raise SystemExit(42)"],
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 42
        assert (
            outcome.terminal_classification
            == TerminalClassification.EXIT_NON_ZERO
        )


class TestPipeRunnerOutcome:
    """Test finalize_execution_outcome produces correct results."""

    def test_full_lifecycle_clean_exit(self):
        """Full lifecycle: spawn, output, clean exit."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                "print('lifecycle test')",
            ],
        )

        outcome = run_process(config, supervisor)

        assert outcome.execution_outcome.started is True
        assert outcome.exit_code == 0
        assert outcome.execution_outcome.timed_out is False
        assert outcome.execution_outcome.cancelled is False
        assert (
            outcome.execution_outcome.execution_certainty
            == ExecutionCertainty.STARTED
        )
        assert outcome.stream_events_ordered is True
        assert b"lifecycle test" in outcome.canonical_stream
        assert outcome.cleanup_failed is False
        assert outcome.cleanup_evidence is None

    def test_empty_output_process(self):
        """A process that produces no output still finalizes correctly."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "pass"],
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 0
        assert outcome.canonical_stream == b""
        assert outcome.stream_events_ordered is True


class TestPipeRunnerStdin:
    """Test stdin data delivery."""

    def test_stdin_data_delivered(self):
        """stdin_data is written to the subprocess's stdin."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                "import sys; data = sys.stdin.read(); print(f'got:{data}')",
            ],
            stdin_data=b"test_input",
        )

        outcome = run_process(config, supervisor)

        assert b"got:test_input" in outcome.canonical_stream
