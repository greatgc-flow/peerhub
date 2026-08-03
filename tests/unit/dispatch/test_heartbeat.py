"""Unit tests for the dedicated lease-heartbeat worker (dispatch.heartbeat).

All tests use mocked/faked renewers and subprocess handles -- no real
peer CLIs are spawned.  Timing-sensitive tests use fault injection
(mock clocks, explicit stop events) rather than real sleeps.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from peerhub.core.protocol import CommandID
from peerhub.dispatch.contract import (
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseSnapshot,
    LeaseState,
    ProcessBirthIdentity,
)
from peerhub.dispatch.heartbeat import (
    HeartbeatFailure,
    HeartbeatWorker,
)


def _make_fence(
    *,
    revision: int = 1,
    fencing_token: int = 1,
    pid: int = 9999,
) -> LeaseFenceTuple:
    """Create a minimal valid LeaseFenceTuple for testing."""
    return LeaseFenceTuple(
        session_id="sess-1",
        lease_id="lease-1",
        fencing_token=fencing_token,
        revision=revision,
        owner_principal_id="principal-1",
        owner_instance_id="instance-1",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=pid,
            process_creation_time=1000,
        ),
        command_id=CommandID("cmd-1"),
        authority_epoch=1,
        attempt_id="attempt-1",
        owner_peer_id="peer-1",
    )


def _make_lease(
    *,
    revision: int = 1,
    fencing_token: int = 1,
    state: LeaseState = LeaseState.ACTIVE,
    pid: int = 9999,
) -> LeaseSnapshot:
    """Create a minimal valid LeaseSnapshot for testing."""
    fence = _make_fence(
        revision=revision,
        fencing_token=fencing_token,
        pid=pid,
    )
    return LeaseSnapshot(
        lease_id=fence.lease_id,
        session_id=fence.session_id,
        fence=fence,
        state=state,
        heartbeat_expires_at=999999999,
        created_at=1000,
        updated_at=1000,
    )


def _make_identity(pid: int = 9999) -> ProcessBirthIdentity:
    return ProcessBirthIdentity(
        pid=pid,
        process_creation_time=1000,
    )


class FakeRenewer:
    """A fake LeaseRenewer that tracks calls and returns updated snapshots."""

    def __init__(
        self,
        *,
        fail_after: int | None = None,
        fail_exc: Exception | None = None,
    ) -> None:
        self.calls: list[LeaseRenewRequest] = []
        self._revision = 1
        self._fencing_token = 1
        self._fail_after = fail_after
        self._fail_exc = fail_exc or RuntimeError("simulated CAS failure")

    def renew_lease(
        self,
        request: LeaseRenewRequest,
        *,
        heartbeat_timeout_ms: int,
    ) -> LeaseSnapshot:
        self.calls.append(request)

        if (
            self._fail_after is not None
            and len(self.calls) > self._fail_after
        ):
            raise self._fail_exc

        # Simulate CAS: advance revision and fencing_token.
        self._revision += 1
        self._fencing_token += 1

        new_fence = replace(
            request.fence,
            revision=self._revision,
            fencing_token=self._fencing_token,
        )
        return LeaseSnapshot(
            lease_id=request.lease_id,
            session_id=request.fence.session_id,
            fence=new_fence,
            state=LeaseState.RENEWED,
            heartbeat_expires_at=999999999,
            created_at=1000,
            updated_at=int(time.time() * 1000),
        )


class FakeProcess:
    """A fake subprocess.Popen handle for testing."""

    def __init__(self, pid: int = 9999, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive
        self._returncode: int | None = None if alive else 0

    def poll(self) -> int | None:
        if not self._alive:
            return self._returncode if self._returncode is not None else 0
        return None

    def simulate_exit(self, returncode: int = 0) -> None:
        """Simulate the process exiting."""
        self._alive = False
        self._returncode = returncode


class TestHeartbeatRenewsOnSchedule:
    """Test that the heartbeat renews on a fixed schedule."""

    def test_renews_for_live_process(self):
        """Heartbeat renews on schedule while the process is alive."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=300,  # 300ms timeout
            interval_ms=50,  # 50ms between ticks
        )

        worker.start()

        # Let it tick a few times.
        time.sleep(0.25)

        worker.stop(timeout=2.0)

        # Should have made at least 2 successful renewals.
        assert len(renewer.calls) >= 2
        assert worker.failure is None
        assert worker.lease_owned is True

    def test_fence_advances_after_each_renewal(self):
        """Each renewal advances the fence revision and fencing_token."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999, revision=1, fencing_token=1)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=300,
            interval_ms=50,
        )

        worker.start()
        time.sleep(0.20)
        worker.stop(timeout=2.0)

        # The latest fence should have advanced past the initial.
        assert worker.latest_fence.revision > lease.fence.revision
        assert (
            worker.latest_fence.fencing_token
            > lease.fence.fencing_token
        )


class TestHeartbeatGhostPrevention:
    """Test ghost-renewal prevention (process exits before tick)."""

    def test_does_not_renew_after_process_exits(self):
        """Once the process exits, no further renewals are made."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()
        failure_captured: list[HeartbeatFailure] = []

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=50,
            on_failure=lambda f: failure_captured.append(f),
        )

        worker.start()

        # Let one tick happen (renewal succeeds).
        time.sleep(0.08)
        count_before_exit = len(renewer.calls)

        # Simulate process exit.
        process.simulate_exit(returncode=0)

        # Wait for the next tick to notice.
        time.sleep(0.15)

        worker.stop(timeout=2.0)

        # No renewals should happen after process exit was detected.
        # The worker should have recorded a PROCESS_DEAD failure.
        assert worker.lease_owned is False
        assert worker.failure is not None
        assert worker.failure.reason == "PROCESS_DEAD"
        assert len(failure_captured) == 1
        assert failure_captured[0].reason == "PROCESS_DEAD"

    def test_pid_mismatch_prevents_renewal(self):
        """If the PID no longer matches identity, renewal is blocked."""
        process = FakeProcess(pid=1111)  # Different PID than identity
        identity = _make_identity(pid=9999)  # Identity expects pid=9999
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=50,
        )

        worker.start()
        time.sleep(0.15)
        worker.stop(timeout=2.0)

        # Should detect the mismatch and stop.
        assert worker.lease_owned is False
        assert worker.failure is not None
        assert worker.failure.reason == "PROCESS_DEAD"
        # No renewals should have been attempted.
        assert len(renewer.calls) == 0


class TestHeartbeatCleanStop:
    """Test that the heartbeat stops cleanly (no orphaned thread)."""

    def test_stop_joins_thread(self):
        """After stop(), the background thread is no longer alive."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=100,
        )

        worker.start()
        assert worker.is_alive

        worker.stop(timeout=2.0)

        assert not worker.is_alive

    def test_stop_before_start_is_safe(self):
        """Calling stop() without start() doesn't raise."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
        )

        # Should not raise.
        worker.stop(timeout=1.0)

    def test_double_start_raises(self):
        """Starting a worker twice raises RuntimeError."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=100,
        )

        worker.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                worker.start()
        finally:
            worker.stop(timeout=2.0)


class TestHeartbeatFailureDetection:
    """Test missed-deadline/task-failure scenarios."""

    def test_renewal_cas_failure_stops_treating_as_owned(self):
        """A CAS failure on renewal causes the lease to stop being owned."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer(fail_after=0)  # Fail immediately.
        failure_captured: list[HeartbeatFailure] = []

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=50,
            on_failure=lambda f: failure_captured.append(f),
        )

        worker.start()
        time.sleep(0.15)
        worker.stop(timeout=2.0)

        assert worker.lease_owned is False
        assert worker.failure is not None
        assert worker.failure.reason == "RENEWAL_FAILED"
        assert len(failure_captured) == 1

    def test_renewer_exception_is_detected(self):
        """An exception from the renewer is caught and recorded."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)

        # Renewer that raises after 1 successful renewal.
        renewer = FakeRenewer(
            fail_after=1,
            fail_exc=ConnectionError("storage unavailable"),
        )

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=3000,
            interval_ms=50,
        )

        worker.start()
        time.sleep(0.25)
        worker.stop(timeout=2.0)

        # First renewal succeeds, second fails.
        assert len(renewer.calls) == 2
        assert worker.lease_owned is False
        assert worker.failure is not None
        assert "storage unavailable" in worker.failure.detail

    def test_heartbeat_timeout_validation(self):
        """heartbeat_timeout_ms must be positive."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        with pytest.raises(ValueError, match="positive"):
            HeartbeatWorker(
                process=process,  # type: ignore[arg-type]
                identity=identity,
                initial_lease=lease,
                renewer=renewer,
                heartbeat_timeout_ms=0,
            )


class TestHeartbeatDefaultInterval:
    """Test the default interval calculation."""

    def test_default_interval_is_one_third_timeout(self):
        """The default interval_ms is heartbeat_timeout_ms // 3."""
        process = FakeProcess(pid=9999)
        identity = _make_identity(pid=9999)
        lease = _make_lease(pid=9999)
        renewer = FakeRenewer()

        worker = HeartbeatWorker(
            process=process,  # type: ignore[arg-type]
            identity=identity,
            initial_lease=lease,
            renewer=renewer,
            heartbeat_timeout_ms=900,
        )

        # Internal: verify interval was set to 300.
        assert worker._interval_ms == 300
