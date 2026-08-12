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


class UnsupportedCapabilityError(PeerHubError):
    """A peer adapter does not support a requested capability."""

    error_code = ErrorCode.INVALID_PARAMS

    def __init__(self, adapter_id: str, capability: str) -> None:
        self.adapter_id = adapter_id
        self.capability = capability
        super().__init__(
            f"adapter {adapter_id!r} does not support capability {capability!r}",
            details={
                "adapter_id": adapter_id,
                "capability": capability,
            },
        )


class InvalidMutationError(PeerHubError):
    """A mutation violates a pure domain invariant."""

    error_code = ErrorCode.INVALID_PARAMS


class InvalidStateTransitionError(PeerHubError):
    """A request or attempt reducer was given an illegal edge."""

    error_code = ErrorCode.INVALID_PARAMS

    def __init__(
        self,
        record_kind: str,
        record_id: str,
        current_state: str,
        requested_state: str,
    ) -> None:
        self.record_kind = record_kind
        self.record_id = record_id
        self.current_state = current_state
        self.requested_state = requested_state
        super().__init__(
            (
                f"{record_kind} {record_id!r} cannot transition "
                f"from {current_state} to {requested_state}"
            ),
            details={
                "record_kind": record_kind,
                "record_id": record_id,
                "current_state": current_state,
                "requested_state": requested_state,
            },
        )


class ProtocolVersionMismatchError(PeerHubError):
    """The submitted protocol pair is not supported."""

    error_code = ErrorCode.PROTOCOL_VERSION_MISMATCH

    def __init__(
        self,
        actual_major: int,
        actual_minor: int,
        supported_major: int,
        supported_minor: int,
    ) -> None:
        super().__init__(
            (
                f"unsupported protocol {actual_major}.{actual_minor}; "
                f"supported protocol is "
                f"{supported_major}.{supported_minor}"
            ),
            details={
                "actual_protocol_major": actual_major,
                "actual_protocol_minor": actual_minor,
                "supported_protocol_major": supported_major,
                "supported_protocol_minor": supported_minor,
            },
        )


class SchemaVersionUnsupportedError(PeerHubError):
    """The submitted schema version is not supported."""

    error_code = ErrorCode.SCHEMA_VERSION_UNSUPPORTED

    def __init__(
        self,
        actual_schema: str,
        supported_schema: str,
    ) -> None:
        super().__init__(
            (
                f"unsupported schema {actual_schema!r}; "
                f"supported schema is {supported_schema!r}"
            ),
            details={
                "actual_schema_version": actual_schema,
                "supported_schema_version": supported_schema,
            },
        )


class MissingIdempotencyKeyError(PeerHubError):
    """A state-changing submission lacks an idempotency key."""

    error_code = ErrorCode.MISSING_IDEMPOTENCY_KEY

    def __init__(self, method: str) -> None:
        super().__init__(
            f"state-changing method {method!r} requires idempotency",
            details={"method": method},
        )


class ConfigurationStaleError(PeerHubError):
    """Admission observed a stale configuration revision."""

    error_code = ErrorCode.CONFIGURATION_STALE

    def __init__(
        self,
        expected_revision: object,
        current_revision: object,
    ) -> None:
        super().__init__(
            "expected configuration revision is not current",
            details={
                "expected_configuration_revision": expected_revision,
                "current_configuration_revision": current_revision,
            },
        )


class PolicyStaleError(PeerHubError):
    """Admission observed a stale policy revision."""

    error_code = ErrorCode.POLICY_STALE

    def __init__(
        self,
        expected_revision: object,
        current_revision: object,
    ) -> None:
        super().__init__(
            "expected policy revision is not current",
            details={
                "expected_policy_revision": expected_revision,
                "current_policy_revision": current_revision,
            },
        )


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


class DuplicateClientRequestError(PeerHubError):
    """A caller request ID was reused for different intent."""

    error_code = ErrorCode.DUPLICATE_ID_CONTENT_MISMATCH

    def __init__(
        self,
        client_id: str,
        client_request_id: str,
    ) -> None:
        self.client_id = client_id
        self.client_request_id = client_request_id
        super().__init__(
            "client request identity is bound to different content",
            details={
                "client_id": client_id,
                "client_request_id": client_request_id,
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


class ActorUnauthorizedError(PeerHubError):
    """The authenticated principal cannot submit the command."""

    error_code = ErrorCode.ACTOR_UNAUTHORIZED

    def __init__(self, authenticated_principal: str) -> None:
        self.authenticated_principal = authenticated_principal
        super().__init__(
            "authenticated principal is not authorized",
            details={
                "authenticated_principal": authenticated_principal,
            },
        )


class StaleAuthorityEpochError(PeerHubError):
    """A lease operation supplied a stale authority epoch."""

    error_code = ErrorCode.EPOCH_STALE

    def __init__(
        self,
        lease_id: str,
        expected_epoch: int,
        current_epoch: int,
    ) -> None:
        self.lease_id = lease_id
        self.expected_epoch = expected_epoch
        self.current_epoch = current_epoch
        super().__init__(
            (
                f"lease {lease_id!r} expected authority epoch "
                f"{expected_epoch}, current epoch is {current_epoch}"
            ),
            details={
                "lease_id": lease_id,
                "expected_epoch": expected_epoch,
                "current_epoch": current_epoch,
            },
        )


class LeaseOwnershipLostError(PeerHubError):
    """A supplied lease fence no longer owns publication authority."""

    error_code = ErrorCode.LEASE_OWNERSHIP_LOST

    def __init__(self, lease_id: str) -> None:
        self.lease_id = lease_id
        super().__init__(
            f"lease {lease_id!r} no longer owns authority",
            details={"lease_id": lease_id},
        )


class ActiveAttemptExistsError(PeerHubError):
    """A command already has a nonterminal attempt."""

    error_code = ErrorCode.UNIQUE_CONSTRAINT_VIOLATED

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(
            f"command {command_id!r} already has an active attempt",
            details={"command_id": command_id},
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


class RecoveryProbeGrantConflictError(PeerHubError):
    """A circuit already has a live recovery-probe grant."""

    error_code = ErrorCode.UNIQUE_CONSTRAINT_VIOLATED

    def __init__(
        self,
        circuit_id: str,
        current_grant_id: str,
    ) -> None:
        self.circuit_id = circuit_id
        self.current_grant_id = current_grant_id
        super().__init__(
            (
                f"health circuit {circuit_id!r} already has "
                f"live recovery-probe grant "
                f"{current_grant_id!r}"
            ),
            details={
                "circuit_id": circuit_id,
                "current_grant_id": current_grant_id,
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
