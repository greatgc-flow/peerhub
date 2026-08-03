"""Dedicated lease-heartbeat background worker.

Ratified design: SLICE5-KICKOFF-R1.md "Process runner backend + lease
heartbeat RATIFIED" (Option B -- dedicated background task, never
piggybacked on chunk events as the sole mechanism).

The heartbeat worker runs in a dedicated background thread, renewing
the lease on a fixed schedule strictly inside ``heartbeat_timeout_ms``.
It starts once the lease is process-bound/ACTIVE and stops cleanly
before final lease close.

Ghost-renewal prevention: before each renewal tick, verifies the child
process is still alive (``process.poll() is None``) AND the recorded
``ProcessBirthIdentity`` still matches.  Never renews a lease for a
dead process.

Heartbeat-task failure (missed deadline, worker crash) is treated as
a first-class supervision failure -- the lease is no longer treated
as owned.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from peerhub.dispatch.contract import (
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseSnapshot,
    ProcessBirthIdentity,
)

logger = logging.getLogger(__name__)


class LeaseRenewer(Protocol):
    """Minimal renewal interface the heartbeat worker needs.

    This matches ``DispatchService.renew_lease``'s signature --
    the heartbeat worker never imports the full service, only this
    protocol, keeping the dependency boundary narrow.
    """

    def renew_lease(
        self,
        request: LeaseRenewRequest,
        *,
        heartbeat_timeout_ms: int,
    ) -> LeaseSnapshot: ...


@dataclass(frozen=True)
class HeartbeatFailure:
    """Describes why the heartbeat worker stopped treating the lease as owned.

    ``reason`` is a machine-readable tag; ``detail`` is a human-readable
    description for logging/diagnostics.
    """

    reason: str
    detail: str


class HeartbeatWorker:
    """Dedicated background heartbeat worker for a single lease.

    Lifecycle:
    1. Construct with a live ``subprocess.Popen`` handle, the initial
       ``LeaseSnapshot``, and a ``LeaseRenewer``.
    2. Call ``start()`` -- the background thread begins renewing.
    3. Call ``stop()`` -- the background thread joins cleanly.

    The worker tracks the latest fence from each successful renewal
    (CAS-based -- each renewal advances the fence revision/token).
    If the worker detects any failure condition (process death, missed
    deadline, renewal CAS failure), it records a ``HeartbeatFailure``
    and stops.

    Parameters
    ----------
    process:
        The ``subprocess.Popen`` handle of the supervised process.
    identity:
        The ``ProcessBirthIdentity`` recorded at spawn time.
    initial_lease:
        The lease snapshot at the time the heartbeat starts.
    renewer:
        A ``LeaseRenewer`` (typically ``DispatchService``) for CAS
        renewal transactions.
    heartbeat_timeout_ms:
        The lease's heartbeat timeout window in milliseconds.
    interval_ms:
        Renewal interval in milliseconds.  Defaults to
        ``heartbeat_timeout_ms // 3`` per the ratified design.
    clock:
        Optional injectable clock returning current time in seconds
        (``time.monotonic``-style).  Defaults to ``time.monotonic``.
    on_failure:
        Optional callback invoked when the heartbeat detects a failure.
        This is the hook point for the cancellation ladder -- currently
        a TODO pending DT-04's implementation (the actual cancellation
        trigger is a no-op/TODO; the detection/stop-treating-as-owned
        part is fully implemented).
    """

    def __init__(
        self,
        *,
        process: subprocess.Popen,  # type: ignore[type-arg]
        identity: ProcessBirthIdentity,
        initial_lease: LeaseSnapshot,
        renewer: LeaseRenewer,
        heartbeat_timeout_ms: int,
        interval_ms: int | None = None,
        clock: Callable[[], float] | None = None,
        on_failure: Callable[[HeartbeatFailure], None] | None = None,
    ) -> None:
        if heartbeat_timeout_ms < 1:
            raise ValueError(
                "heartbeat_timeout_ms must be positive"
            )

        self._process = process
        self._identity = identity
        self._heartbeat_timeout_ms = heartbeat_timeout_ms
        self._interval_ms = (
            interval_ms
            if interval_ms is not None
            else heartbeat_timeout_ms // 3
        )
        self._renewer = renewer
        self._clock = clock if clock is not None else time.monotonic
        self._on_failure = on_failure

        # Mutable state protected by _lock.
        self._lock = threading.Lock()
        self._latest_fence: LeaseFenceTuple = initial_lease.fence
        self._failure: HeartbeatFailure | None = None
        self._lease_owned: bool = True

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_alive(self) -> bool:
        """Whether the heartbeat background thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def failure(self) -> HeartbeatFailure | None:
        """The failure that caused the heartbeat to stop, if any."""
        with self._lock:
            return self._failure

    @property
    def lease_owned(self) -> bool:
        """Whether the worker still considers the lease as owned."""
        with self._lock:
            return self._lease_owned

    @property
    def latest_fence(self) -> LeaseFenceTuple:
        """The most recent fence (updated after each successful renewal)."""
        with self._lock:
            return self._latest_fence

    def start(self) -> None:
        """Start the background heartbeat thread.

        Raises ``RuntimeError`` if already started.
        """
        if self._thread is not None:
            raise RuntimeError(
                "heartbeat worker already started"
            )
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="lease-heartbeat",
        )
        self._thread.start()

    def stop(self, timeout: float | None = 10.0) -> None:
        """Signal the heartbeat thread to stop and join it.

        Blocks until the thread exits or ``timeout`` seconds elapse.
        Safe to call even if the thread has already stopped.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _record_failure(self, reason: str, detail: str) -> None:
        """Record a failure and stop treating the lease as owned."""
        failure = HeartbeatFailure(reason=reason, detail=detail)
        with self._lock:
            if self._failure is None:
                self._failure = failure
            self._lease_owned = False

        logger.warning(
            "heartbeat failure: %s -- %s", reason, detail
        )

        # Invoke the failure callback (cancellation ladder hook).
        # TODO(DT-04): The actual cancellation trigger is a no-op
        # pending DT-04's implementation.  The detection and
        # stop-treating-as-owned part is fully implemented above.
        if self._on_failure is not None:
            try:
                self._on_failure(failure)
            except Exception:
                logger.exception(
                    "on_failure callback raised"
                )

    def _verify_process_alive(self) -> bool:
        """Ghost-renewal prevention: verify process is still alive.

        Checks both ``process.poll() is None`` (process hasn't exited)
        AND that the PID still matches the recorded
        ``ProcessBirthIdentity``.  A bare PID is never sufficient
        fencing evidence per the ratified design.
        """
        # Check if the process has exited.
        if self._process.poll() is not None:
            return False

        # Verify PID still matches the recorded identity.
        if self._process.pid != self._identity.pid:
            return False

        return True

    def _do_renewal(self) -> bool:
        """Perform one CAS lease renewal.

        Returns ``True`` on success, ``False`` on failure (which
        records a ``HeartbeatFailure``).
        """
        with self._lock:
            fence = self._latest_fence

        request = LeaseRenewRequest(
            lease_id=fence.lease_id,
            fence=fence,
        )

        try:
            updated = self._renewer.renew_lease(
                request,
                heartbeat_timeout_ms=self._heartbeat_timeout_ms,
            )
        except Exception as exc:
            self._record_failure(
                "RENEWAL_FAILED",
                f"CAS renewal failed: {exc}",
            )
            return False

        # Update the latest fence from the successful renewal.
        with self._lock:
            self._latest_fence = updated.fence

        return True

    def _run(self) -> None:
        """Main heartbeat loop running in the background thread."""
        interval_s = self._interval_ms / 1000.0

        try:
            while not self._stop_event.is_set():
                # Wait for the next tick (interruptible by stop()).
                if self._stop_event.wait(timeout=interval_s):
                    break

                # Ghost-renewal prevention.
                if not self._verify_process_alive():
                    self._record_failure(
                        "PROCESS_DEAD",
                        "process exited before renewal tick; "
                        "not renewing for a dead process",
                    )
                    break

                # Perform the CAS renewal.
                if not self._do_renewal():
                    break  # Failure already recorded in _do_renewal.

        except Exception as exc:
            self._record_failure(
                "HEARTBEAT_TASK_CRASH",
                f"heartbeat thread raised: {exc}",
            )
