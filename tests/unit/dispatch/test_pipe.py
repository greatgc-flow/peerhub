"""Unit tests for the pipe-based process runner (dispatch.pipe).

Tests use trivial ``python -c "..."`` subprocesses so they are
hermetic and never depend on external peer CLIs or consume quota.
"""

from __future__ import annotations

import sys
import threading

import pytest
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.pipe import (
    PipeOutputChannel,
    PipeProcessChunk,
    PipeRunnerConfig,
    run_process,
)
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

    def test_identity_has_real_creation_time_with_psutil(self):
        """The recorded creation time is a real timestamp (> 0) since psutil is available."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "pass"],
        )

        run_process(config, supervisor)

        assert supervisor.identity is not None
        assert supervisor.identity.process_creation_time > 0


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

    def test_chunk_callback_is_live_ordered_and_single_consumer(self):
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                (
                    "import sys, time; "
                    "sys.stdout.write('OUT\\n'); sys.stdout.flush(); "
                    "time.sleep(0.5); "
                    "sys.stderr.write('ERR\\n'); sys.stderr.flush()"
                ),
            ],
        )
        process = None
        chunks: list[PipeProcessChunk] = []
        callback_thread_ids: list[int] = []
        process_was_live: list[bool] = []

        def on_spawned(proc, _identity):
            nonlocal process
            process = proc

        def on_chunk(chunk: PipeProcessChunk) -> None:
            chunks.append(chunk)
            callback_thread_ids.append(threading.get_ident())
            if b"OUT" in chunk.data:
                assert process is not None
                process_was_live.append(process.poll() is None)

        outcome = run_process(
            config,
            supervisor,
            on_spawned=on_spawned,
            on_chunk=on_chunk,
        )

        assert outcome.exit_code == 0
        assert process_was_live == [True]
        assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
        assert [chunk.timestamp_ms for chunk in chunks] == sorted(
            chunk.timestamp_ms for chunk in chunks
        )
        assert len(set(callback_thread_ids)) == 1
        assert b"OUT" in b"".join(
            chunk.data
            for chunk in chunks
            if chunk.channel is PipeOutputChannel.STDOUT
        )
        assert b"ERR" in b"".join(
            chunk.data
            for chunk in chunks
            if chunk.channel is PipeOutputChannel.STDERR
        )


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
            == ExecutionCertainty.TERMINAL
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
            == ExecutionCertainty.TERMINAL
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


class TestPipeRunnerOnSpawnedCallback:
    """Bug 1 regression: on_spawned callback fires with live process handle."""

    def test_on_spawned_callback_receives_live_process(self):
        """The on_spawned callback must fire while the process is still alive,
        providing both the live Popen handle and ProcessBirthIdentity.

        This test would have caught the original bug: without the callback,
        there was no way to get the process handle before run_process blocked
        until exit.
        """
        import subprocess

        captured: list[tuple[subprocess.Popen, ProcessBirthIdentity]] = []
        poll_results: list[int | None] = []

        def on_spawn(proc: subprocess.Popen, identity: ProcessBirthIdentity) -> None:
            # Capture the live process handle and identity.
            captured.append((proc, identity))
            # Record whether the process is still running at callback time.
            # poll() returns None if the process hasn't exited yet.
            poll_results.append(proc.poll())

        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                # A short sleep to ensure the process is still alive
                # when the on_spawned callback fires.
                "import time; time.sleep(0.5); print('done')",
            ],
        )

        outcome = run_process(config, supervisor, on_spawned=on_spawn)

        # Callback was invoked exactly once.
        assert len(captured) == 1
        proc, identity = captured[0]

        # The identity is valid.
        assert isinstance(identity, ProcessBirthIdentity)
        assert identity.pid > 0

        # The process handle is a real Popen object.
        assert isinstance(proc, subprocess.Popen)

        # At callback time, the process was still running (poll returned None).
        assert poll_results[0] is None, (
            "on_spawned callback fired after the process already exited — "
            "this defeats the purpose of exposing the live handle"
        )

        # After run_process returns, the process has exited cleanly.
        assert outcome.exit_code == 0
        assert b"done" in outcome.canonical_stream

    def test_on_spawned_callback_not_required(self):
        """run_process still works when on_spawned is not provided."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "pass"],
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 0
        assert supervisor.identity is not None


class TestPipeRunnerCancellationLadder:
    """Decision A regression: run_process drives the cancellation ladder
    from the main thread during its polling loop when cancellation is active."""

    def test_cancellation_triggered_externally_drives_ladder(self):
        """When begin_cancellation is triggered externally (simulating a
        heartbeat failure callback), run_process's main thread drives the
        cancellation ladder via the tree controller.

        Uses a long-running subprocess and triggers cancellation from
        a background thread to simulate the heartbeat worker.
        """
        import threading

        from peerhub.dispatch.process import CancellationStage

        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                # Long-running process that will be killed by the ladder.
                "import time; time.sleep(30)",
            ],
        )

        cancel_called = threading.Event()

        def on_spawn(proc, identity):
            # Trigger cancellation from a background thread after a brief
            # delay (simulating heartbeat failure detection).
            def trigger():
                cancel_called.wait(timeout=0.2)
                supervisor.begin_cancellation(now_ms=0)
                cancel_called.set()

            t = threading.Thread(target=trigger, daemon=True)
            t.start()
            cancel_called.set()

        outcome = run_process(config, supervisor, on_spawned=on_spawn)

        # The process should have been cancelled.
        assert supervisor.cancellation_active is True
        assert outcome.execution_outcome.cancelled is True

    def test_normal_process_no_cancellation(self):
        """A quick-exiting process without cancellation still works normally
        with the new polling loop."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "print('no cancel')"],
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 0
        assert supervisor.cancellation_active is False
        assert b"no cancel" in outcome.canonical_stream


class TestDispatchCancellationAction:
    """Tests for _dispatch_cancellation_action helper."""

    def test_soft_cancel_dispatches(self):
        from peerhub.dispatch.pipe import (
            TreeDispatchReceipt,
            _dispatch_cancellation_action,
        )
        from peerhub.dispatch.process import CancellationAction

        calls: list[str] = []

        class FakeController:
            def soft_cancel(self, tree):
                calls.append("soft_cancel")
                return TreeDispatchReceipt(dispatched=True, signal_name="SOFT")

            def terminate_tree(self, tree):
                calls.append("terminate_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="TERM")

            def kill_tree(self, tree):
                calls.append("kill_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="KILL")

        controller = FakeController()
        handle = object()

        result = _dispatch_cancellation_action(
            CancellationAction.SOFT_CANCEL, controller, handle
        )
        assert calls == ["soft_cancel"]
        assert result is not None and result.dispatched

    def test_terminate_tree_dispatches(self):
        from peerhub.dispatch.pipe import (
            TreeDispatchReceipt,
            _dispatch_cancellation_action,
        )
        from peerhub.dispatch.process import CancellationAction

        calls: list[str] = []

        class FakeController:
            def soft_cancel(self, tree):
                calls.append("soft_cancel")
                return TreeDispatchReceipt(dispatched=True, signal_name="SOFT")

            def terminate_tree(self, tree):
                calls.append("terminate_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="TERM")

            def kill_tree(self, tree):
                calls.append("kill_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="KILL")

        controller = FakeController()
        result = _dispatch_cancellation_action(
            CancellationAction.TERMINATE_TREE, controller, object()
        )
        assert calls == ["terminate_tree"]
        assert result is not None and result.dispatched

    def test_kill_tree_dispatches(self):
        from peerhub.dispatch.pipe import (
            TreeDispatchReceipt,
            _dispatch_cancellation_action,
        )
        from peerhub.dispatch.process import CancellationAction

        calls: list[str] = []

        class FakeController:
            def soft_cancel(self, tree):
                return TreeDispatchReceipt(dispatched=True, signal_name="SOFT")

            def terminate_tree(self, tree):
                return TreeDispatchReceipt(dispatched=True, signal_name="TERM")

            def kill_tree(self, tree):
                calls.append("kill_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="KILL")

        controller = FakeController()
        result = _dispatch_cancellation_action(
            CancellationAction.KILL_TREE, controller, object()
        )
        assert calls == ["kill_tree"]

    def test_none_action_returns_none(self):
        from peerhub.dispatch.pipe import _dispatch_cancellation_action
        from peerhub.dispatch.process import CancellationAction

        result = _dispatch_cancellation_action(
            CancellationAction.NONE, object(), object()
        )
        assert result is None


class TestDriveCancellationLadder:
    """Tests for _drive_cancellation_ladder helper."""

    def test_drives_ladder_step(self):
        """_drive_cancellation_ladder dispatches the action and feeds back
        tree observations to the supervisor."""
        from peerhub.dispatch.pipe import (
            TreeDispatchReceipt,
            _drive_cancellation_ladder,
        )
        from peerhub.dispatch.process import (
            CancellationStage,
            ObservationState,
            TreeProcessObservation,
        )

        supervisor = ProcessSupervisor()
        supervisor.on_spawned(pid=7000)
        supervisor.begin_cancellation(now_ms=0)

        dispatched_actions: list[str] = []
        observed: list[bool] = []

        root_identity = ProcessBirthIdentity(pid=7000, process_creation_time=0)

        class FakeController:
            def soft_cancel(self, tree):
                dispatched_actions.append("soft_cancel")
                return TreeDispatchReceipt(dispatched=True, signal_name="SOFT")

            def terminate_tree(self, tree):
                dispatched_actions.append("terminate_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="TERM")

            def kill_tree(self, tree):
                dispatched_actions.append("kill_tree")
                return TreeDispatchReceipt(dispatched=True, signal_name="KILL")

            def observe_tree(self, tree):
                observed.append(True)
                return (
                    TreeProcessObservation(
                        identity=root_identity,
                        state=ObservationState.TERMINATED,
                    ),
                )

        class FakeHandle:
            root_identity = ProcessBirthIdentity(pid=7000, process_creation_time=0)

        _drive_cancellation_ladder(
            supervisor, FakeController(), FakeHandle(), lambda: 1000
        )

        # Should have dispatched soft_cancel (the action from begin_cancellation).
        assert "soft_cancel" in dispatched_actions
        # Should have observed the tree.
        assert len(observed) == 1
        # Since all processes are TERMINATED, the ladder should complete.
        decision = supervisor.cancellation_decision
        assert decision is not None
        assert decision.stage is CancellationStage.COMPLETED


class TestPipeRunnerLimits:
    """Test process/silence timeouts and max output bytes."""

    def test_process_timeout_fires(self):
        """process_timeout_ms terminates a genuinely slow process."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "import time; time.sleep(10)"],
            process_timeout_ms=500,
        )

        outcome = run_process(config, supervisor)

        assert outcome.execution_outcome.timed_out is True
        assert outcome.terminal_classification == TerminalClassification.PROCESS_TIMEOUT

    def test_silence_timeout_fires(self):
        """silence_timeout_ms terminates a process that goes silent."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                "import time, sys; print('hi'); sys.stdout.flush(); time.sleep(10)",
            ],
            silence_timeout_ms=500,
        )

        outcome = run_process(config, supervisor)

        assert outcome.execution_outcome.timed_out is True
        assert outcome.terminal_classification == TerminalClassification.SILENCE_TIMEOUT
        assert b"hi" in outcome.canonical_stream

    def test_max_output_bytes_caps(self):
        """max_output_bytes caps correctly and terminates the process."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[
                sys.executable,
                "-c",
                "import sys\nwhile True: sys.stdout.write('x' * 1000); sys.stdout.flush()",
            ],
            max_output_bytes=2000,
        )

        outcome = run_process(config, supervisor)

        assert outcome.execution_outcome.cancelled is True
        assert outcome.terminal_classification == TerminalClassification.OUTPUT_LIMIT_EXCEEDED
        assert len(outcome.canonical_stream) >= 2000

    def test_limits_no_false_positive(self):
        """None of the limits false-positive on a normal fast-completing process."""
        supervisor = ProcessSupervisor()
        config = PipeRunnerConfig(
            argv=[sys.executable, "-c", "print('fast')"],
            process_timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=10000,
        )

        outcome = run_process(config, supervisor)

        assert outcome.exit_code == 0
        assert outcome.execution_outcome.timed_out is False
        assert outcome.execution_outcome.cancelled is False
        assert outcome.terminal_classification is None
        assert b"fast" in outcome.canonical_stream
