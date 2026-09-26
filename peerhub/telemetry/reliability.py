"""24h dispatch reliability (R4 2.9 / B8).

"Definitive started attempts (deduplicated by attempt ID) completed within (as_of - 24h, as_of].
Rate = failed / (succeeded + failed). Show the counts, the excluded cancelled / unknown /
pre-admission categories, and partial coverage." A window with zero definitive attempts has NO
rate (never shown as 0%).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

WINDOW_SECONDS = 24 * 60 * 60

_SUCCEEDED = frozenset({"SUCCEEDED_VERIFIED"})
_CANCELLED = frozenset({"CANCELLING", "CANCELLED"})
_PRE_ADMISSION = frozenset({"FAILED_PRE_DISPATCH"})
_UNKNOWN = frozenset(
    {"DELIVERED_UNVERIFIED", "INCOMPLETE", "INTERRUPTED", "START_UNCERTAIN"}
)
_DEFINITIVE_START = frozenset({"STARTED", "TERMINAL"})


@dataclass(frozen=True)
class Reliability24h:
    succeeded: int
    failed: int
    excluded_cancelled: int
    excluded_unknown: int
    excluded_pre_admission: int
    as_of: int

    @property
    def definitive(self) -> int:
        return self.succeeded + self.failed

    @property
    def rate(self) -> float | None:
        """Failure rate, or None when there is no definitive attempt in the window."""
        return None if self.definitive == 0 else self.failed / self.definitive

    @property
    def partial_coverage(self) -> bool:
        """True when some attempts in the window could not be classified either way."""
        return self.excluded_unknown > 0


def compute_reliability(
    connection: sqlite3.Connection,
    *,
    instance_id: str,
    profile_id: str,
    as_of: int,
) -> Reliability24h:
    rows = connection.execute(
        """
        SELECT a.attempt_id, a.state, a.execution_certainty
        FROM dispatch_attempts a
        JOIN dispatch_requests r ON r.command_id = a.command_id
        WHERE r.selected_peer_instance_id = ?
          AND r.selected_profile_id = ?
          AND a.updated_at > ?
          AND a.updated_at <= ?
        """,
        (instance_id, profile_id, as_of - WINDOW_SECONDS, as_of),
    ).fetchall()
    succeeded = failed = cancelled = unknown = pre_admission = 0
    seen: set[str] = set()
    for attempt_id, state, certainty in rows:
        if attempt_id in seen:
            continue  # deduplicated by attempt id
        seen.add(attempt_id)
        if state in _SUCCEEDED:
            succeeded += 1
        elif state in _CANCELLED:
            cancelled += 1
        elif state in _PRE_ADMISSION:
            pre_admission += 1
        elif state == "FAILED":
            if certainty in _DEFINITIVE_START:
                failed += 1
            elif certainty == "NOT_STARTED":
                pre_admission += 1  # never started: not a reliability failure of the peer
            else:
                unknown += 1  # MAY_HAVE_STARTED: no definitive outcome
        elif state in _UNKNOWN:
            unknown += 1
        # non-terminal states (PREPARED, RUNNING, ...) are still in flight: not counted
    return Reliability24h(succeeded, failed, cancelled, unknown, pre_admission, as_of)
