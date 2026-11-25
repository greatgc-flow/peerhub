"""Classify ask outcomes and dispatch exceptions for recovery guidance."""
from __future__ import annotations

import re


def _is_launch_failure_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    low = text.lower()
    return "executable not found" in low or "cannot spawn" in low


def _is_prompt_too_large_text(text: object) -> bool:
    return isinstance(text, str) and re.search(r"exceeding the \w+ inline limit", text) is not None


def select_guidance_code(
    *,
    status: str | None = None,
    processed_status: str | None = None,
    certainty: str | None = None,
    reason: str | None = None,
    detail_error: object = None,
    exception: BaseException | None = None,
) -> str:
    """Select a recovery code from an ask result or a dispatch exception."""
    if exception is not None:
        from peerhub.extensions.bridge_claims import ClaimHeldError
        from peerhub.extensions.runtime_contract import PrespawnError
        from peerhub.extensions.bridge_errors import (
            SessionBridgeError,
            ConflictingSessionFlagsError,
            InvalidSessionHandleSyntaxError,
        )
        from peerhub.extensions.ask import LegacySelectorUnsupported, PeerNotFoundError, StreamPeerMismatchError
        from peerhub.core.schema_version import SchemaVersionError
        from peerhub.core.store import CasMismatchError, IdempotencyConflictError, StorageReadOnlyError, StorageFullError, StorageCorruptError

        if isinstance(exception, SchemaVersionError):
            return "ask_schema_version_error"
        if isinstance(exception, CasMismatchError):
            return "ask_cas_conflict"
        if isinstance(exception, IdempotencyConflictError):
            return "ask_idempotency_conflict"
        if isinstance(exception, ClaimHeldError):
            return "ask_claim_held"
        if isinstance(exception, StreamPeerMismatchError):
            return "ask_stream_peer_mismatch"
        if isinstance(exception, PeerNotFoundError):
            return "peer_not_found"  # a user error (#401): the shared entry, as for `peer get`
        if isinstance(exception, (SessionBridgeError, LegacySelectorUnsupported, ConflictingSessionFlagsError, InvalidSessionHandleSyntaxError)):
            return "ask_selector_error"
        if isinstance(exception, PrespawnError):
            if _is_prompt_too_large_text(str(exception)):
                return "ask_prompt_too_large"
            return "ask_launch_failed" if _is_launch_failure_text(str(exception)) else "ask_delivery_failed"
        if isinstance(exception, (StorageReadOnlyError, StorageFullError, StorageCorruptError)):
            return "ask_delivery_failed"
        from peerhub.extensions.adapters.api import RequestRejected

        if isinstance(exception, RequestRejected):  # API peers (#378): refused before anything was stored or sent
            return "request_rejected"
        if isinstance(exception, ValueError) and "session" in type(exception).__name__.lower():
            return "ask_selector_error"
        return "ask_unexpected_failure"

    if status in ("delivered", "recovered_terminal"):
        return ""
    if status == "denied_empty" or (status == "pending_earlier_record" and processed_status == "denied_empty"):
        return "ask_denied_empty"
    if status == "pending_earlier_record":
        if processed_status in ("delivered", "recovered_terminal"):
            return ""
        if processed_status == "not_executed":
            return "ask_refusal"
        if processed_status == "failed_not_started":
            if _is_prompt_too_large_text(detail_error):
                return "ask_prompt_too_large"
            return "ask_launch_failed" if _is_launch_failure_text(detail_error) else "ask_delivery_failed"
        return "ask_blocked_uncertain"
    if status == "blocked_uncertain" or processed_status in ("uncertain", "blocked_uncertain"):
        return "ask_blocked_uncertain"
    if status in ("truncated", "incomplete"):  # API peers (#378): the reply ended; the error says why, nothing is uncertain
        return status
    if status == "uncertain" or (certainty in ("STARTED", "MAY_HAVE_STARTED") and status != "not_executed"):
        return "ask_uncertain"
    if status == "not_executed" or reason in ("quota_exhausted", "usage_limit", "invalid_model", "auth_failure", "refusal"):
        return "ask_refusal"
    if status == "session_unavailable":
        return "ask_selector_error"
    if status == "failed_not_started":
        if _is_prompt_too_large_text(detail_error):
            return "ask_prompt_too_large"
        if _is_launch_failure_text(detail_error):
            return "ask_launch_failed"
        return "ask_delivery_failed"
    return "ask_unexpected_failure"


def is_ag_writable_error(exception: BaseException | None) -> bool:
    """Identify the pre-spawn rejection that requires an explicit writable ask."""
    from peerhub.extensions.adapters.ag import AgRequiresWritableError

    return isinstance(exception, AgRequiresWritableError)
