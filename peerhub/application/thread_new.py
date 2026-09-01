"""Application-layer compatibility for legacy ``thread-new``."""

from __future__ import annotations

from dataclasses import dataclass

from peerhub.core.errors import StaleRevisionError
from peerhub.governance.contract import MutationSubmission
from peerhub.governance.rooms import RoomsService


@dataclass(frozen=True, slots=True)
class ThreadNewResult:
    """The created submission or legacy's duplicate-thread no-op envelope."""

    thread_id: str
    created: bool
    message: str | None
    submission: MutationSubmission | None


def create_thread_new(
    service: RoomsService,
    *,
    thread_id: str,
    room_id: str,
    subject: str,
    creator_id: str,
) -> ThreadNewResult:
    """Create a thread, treating an existing room-local thread as a no-op.

    ``RoomsService.create_thread`` intentionally retains its create-only CAS
    contract.  This compatibility adapter converts only its duplicate-thread
    conflict into legacy's successful guidance response.
    """

    try:
        submission = service.create_thread(
            thread_id=thread_id,
            room_id=room_id,
            subject=subject,
            creator_id=creator_id,
        )
    except StaleRevisionError as exc:
        existing = service.get_target(thread_id)
        if (
            exc.target_id != thread_id
            or existing is None
            or existing.state.get("kind") != "thread"
            or existing.state.get("room_id") != room_id
        ):
            raise
        return ThreadNewResult(
            thread_id=thread_id,
            created=False,
            message=(
                f"Thread '{thread_id}' already exists. "
                "Use thread-append to add messages."
            ),
            submission=None,
        )
    return ThreadNewResult(
        thread_id=thread_id,
        created=True,
        message=None,
        submission=submission,
    )
