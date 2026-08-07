"""Dedicated lease-heartbeat background worker.

Ratified design: SLICE5-KICKOFF-R1.md "Process runner backend + lease
heartbeat RATIFIED" (Option B -- dedicated background task, never
piggybacked on chunk events as the sole mechanism).

The heartbeat worker runs in a dedicated background thread, renewing
the lease on a fixed schedule strictly inside ``heartbeat_timeout_ms``.
It starts once the lease is process-bound/ACTIVE and stops cleanly
before final lease close.

Ghost-renewal prevention: before each renewal tick, verifies the child
process is still alive (``process.poll() is None``) and its PID matches
the recorded ``ProcessBirthIdentity``. Never renews a lease for an exited process.

Heartbeat-task failure (CAS renewal failure or worker crash) is treated
as a first-class supervision failure -- the lease is no longer treated
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
    If the worker detects any failure condition (lease-renewal CAS
    failure, a process-identity mismatch, or an unexpected crash of the
    heartbeat thread itself), it records a ``HeartbeatFailure`` and
    stops. A *normal* process exit is NOT a failure condition -- the
    worker simply stops renewing so the workflow can terminalize and
    release the lease.

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
        This is the hook point for the cancellation ladder: callers
        (e.g. ``ApplicationWorkflows.dispatch_and_execute``) wire it to
        ``ProcessSupervisor.begin_cancellation()`` to start terminating
        the process tree.
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

        self._process = process  # pyright: ignore[reportUnknownMemberType]
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
        notify_failure: HeartbeatFailure | None = None
        with self._lock:
            if self._failure is None:
                self._failure = failure
                notify_failure = failure
            self._lease_owned = False

        logger.warning(
            "heartbeat failure: %s -- %s", reason, detail
        )

        # Invoke the failure callback (cancellation ladder hook).
        # Calls the provided on_failure callback, which can drive
        # ProcessSupervisor.begin_cancellation() and TreeController.
        if notify_failure is not None and self._on_failure is not None:
            try:
                self._on_failure(notify_failure)
            except Exception:
                logger.exception(
                    "on_failure callback raised"
                )

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

                # A normally exited child no longer needs renewal; preserve
                # ownership so the workflow can terminalize and release it.
                if self._process.poll() is not None:  # pyright: ignore[reportUnknownMemberType]
                    break

                # A live process with a different PID is an identity breach,
                # not a normal exit, and must stop the cancellation-safe flow.
                if self._process.pid != self._identity.pid:  # pyright: ignore[reportUnknownMemberType]
                    self._record_failure(
                        "PROCESS_IDENTITY_MISMATCH",
                        "process PID no longer matches its recorded birth identity",
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
