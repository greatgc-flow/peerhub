"""Errors raised by bridge delivery, control and session selection."""


class IllegalCertaintyTransition(ValueError):
    """Forbidden execution-certainty transition (TD-11); nothing was written."""


class TerminalConflictError(RuntimeError):
    """A different terminal result arrived for a delivery whose terminal truth is already committed (BRG-019)."""


class InvalidTerminalResultError(ValueError):
    """Terminal result is not a strict-JSON dict with a `response` key; rejected before anything is committed."""


class OffsetAckConflictError(RuntimeError):
    """Offset CAS conflicts exhausted the retry budget: the delivery is NOT acked (terminal truth stays, recovery re-acks)."""


class InvocationMarkerError(ValueError):
    """Invocation marker/resolution violates the marker protocol (no unresolved marker, or certainty is not NOT_STARTED); nothing was written."""


class RecordRejectedError(ValueError):
    """The Record handed to begin_attempt is not the persisted Record of this claim's Stream/peer; nothing was written."""


class NoDeliveryError(LookupError):
    """No delivery exists for the claim scope."""


class ReconcileRejectedError(ValueError):
    """The reconciliation Record cannot authorize a retry (wrong kind/decision/target/certainty)."""


class ControlRejectedError(ValueError):
    """A control request is not authentic/addressed (not durable, not a control Record, wrong peer/author); nothing was written."""


class BoundaryConflictError(RuntimeError):
    """The deterministic context.boundary key is occupied by a Record that is not this generation's boundary."""


class SessionError(RuntimeError):
    """Session create/resume failure."""


class SessionBridgeError(ValueError):
    """Definitive session selection failure before invocation."""
    code = "session_selection_error"


class SessionNotFoundError(SessionBridgeError):
    code = "session_not_found"


class SessionTombstonedError(SessionBridgeError):
    code = "session_tombstoned"


class SessionDeltaOverflowError(SessionBridgeError):
    code = "session_delta_overflow"


class SessionStreamMismatchError(SessionBridgeError):
    code = "session_stream_mismatch"


class SessionPeerMismatchError(SessionBridgeError):
    code = "session_peer_mismatch"


class SessionWorkspaceMismatchError(SessionBridgeError):
    code = "session_workspace_mismatch"


class SessionBindingMismatchError(SessionBridgeError):
    code = "session_binding_mismatch"


class SessionFingerprintMismatchError(SessionBridgeError):
    code = "session_fingerprint_mismatch"


class SessionUnavailableError(SessionBridgeError):
    code = "session_unavailable"


class SessionLostError(SessionBridgeError):
    code = "session_lost"


class ConflictingSessionFlagsError(ValueError):
    code = "conflicting_session_flags"


class InvalidSessionHandleSyntaxError(ValueError):
    code = "invalid_session_handle_syntax"
