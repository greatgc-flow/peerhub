"""Internal PeerHub exceptions and protocol-code mapping."""

from __future__ import annotations

from collections.abc import Mapping

from .protocol import ErrorCode


class PeerHubError(Exception):
    """Base class for expected, protocol-classifiable failures."""

    error_code: ErrorCode

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class InvalidMutationError(PeerHubError):
    """A mutation violates a pure domain invariant."""

    error_code = ErrorCode.INVALID_PARAMS


class StaleRevisionError(PeerHubError):
    """A mutation's expected revision is not current."""

    error_code = ErrorCode.REVISION_CONFLICT

    def __init__(
        self,
        target_id: str,
        expected_revision: int,
        current_revision: int,
    ) -> None:
        self.target_id = target_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            (
                f"target {target_id!r} expected revision "
                f"{expected_revision}, current revision is "
                f"{current_revision}"
            ),
            details={
                "target_id": target_id,
                "expected_revision": expected_revision,
                "current_revision": current_revision,
            },
        )


class IdempotencyPayloadMismatchError(PeerHubError):
    """An idempotency identity was reused with different content."""

    error_code = ErrorCode.IDEMPOTENCY_PAYLOAD_MISMATCH

    def __init__(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> None:
        self.client_id = client_id
        self.command_type = command_type
        self.idempotency_key = idempotency_key
        super().__init__(
            "idempotency identity is bound to a different payload",
            details={
                "client_id": client_id,
                "command_type": command_type,
                "idempotency_key": idempotency_key,
            },
        )


class RecordNotFoundError(PeerHubError):
    """A required authoritative record does not exist."""

    error_code = ErrorCode.RECORD_NOT_FOUND

    def __init__(self, record_kind: str, record_id: str) -> None:
        self.record_kind = record_kind
        self.record_id = record_id
        super().__init__(
            f"{record_kind} {record_id!r} was not found",
            details={
                "record_kind": record_kind,
                "record_id": record_id,
            },
        )


class ExclusiveClaimConflictError(PeerHubError):
    """An outbox effect is already owned or terminal."""

    error_code = ErrorCode.UNIQUE_CONSTRAINT_VIOLATED

    def __init__(
        self,
        event_id: str,
        current_owner_id: str | None,
    ) -> None:
        self.event_id = event_id
        self.current_owner_id = current_owner_id
        super().__init__(
            f"outbox event {event_id!r} is not claimable",
            details={
                "event_id": event_id,
                "current_owner_id": current_owner_id,
            },
        )


class WorkspaceIdentityMismatchError(PeerHubError):
    """A database belongs to a different workspace identity."""

    error_code = ErrorCode.WORKSPACE_IDENTITY_MISMATCH

    def __init__(
        self,
        expected_workspace_home_id: str,
        stored_workspace_home_id: str,
    ) -> None:
        self.expected_workspace_home_id = (
            expected_workspace_home_id
        )
        self.stored_workspace_home_id = stored_workspace_home_id
        super().__init__(
            "SQLite database workspace identity does not match",
            details={
                "expected_workspace_home_id": (
                    expected_workspace_home_id
                ),
                "stored_workspace_home_id": (
                    stored_workspace_home_id
                ),
            },
        )


def error_code_for(error: PeerHubError) -> ErrorCode:
    """Return the stable protocol code for an expected error."""

    return error.error_code
