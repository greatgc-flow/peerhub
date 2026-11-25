"""Provider-neutral runtime port for the logical Execution Kernel.

This module intentionally imports neither the concrete Bridge nor a vendor adapter.
Importers of the historical bridge symbols continue to get these *same* class objects.
The port describes capabilities; it never owns execution certainty or persistence.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

from peerhub.core.peer_kinds import API_PEER_KINDS


_BILLING_FINGERPRINT = re.compile(r"(?:key|account):[0-9a-f]{64}")


def valid_billing_fingerprint(value: object) -> bool:
    return isinstance(value, str) and _BILLING_FINGERPRINT.fullmatch(value) is not None


@dataclass(frozen=True)
class BillingScope:
    provider: str
    fingerprint: str

    def __post_init__(self) -> None:
        if self.provider not in API_PEER_KINDS or not valid_billing_fingerprint(self.fingerprint):
            raise ValueError("invalid billing account scope")

    @classmethod
    def from_verified_identity(
        cls, provider: str, identity: str, *, identity_kind: Literal["key", "account"],
    ) -> BillingScope:
        """Fingerprint the identity bound to the source by the caller, in process memory."""
        if provider not in API_PEER_KINDS or identity_kind not in ("key", "account") or not identity:
            raise ValueError("invalid billing identity")
        digest = hashlib.sha256(f"peerhub.billing.v1\x00{provider}\x00{identity_kind}\x00".encode())
        digest.update(identity.encode("utf-8", errors="surrogatepass"))
        return cls(provider, f"{identity_kind}:{digest.hexdigest()}")


class RuntimeTargetError(RuntimeError):
    """Ambiguous runtime failure (process/session may have started)."""

    def __init__(self, message: str = "", *, termination_confirmed: bool | None = None) -> None:
        super().__init__(message)
        self.termination_confirmed = termination_confirmed


class PrespawnError(RuntimeTargetError):
    """Failure proven to be before any process/session start (resolves the invocation marker; certainty stays NOT_STARTED)."""


class ContextLostError(PrespawnError):
    """Adapter reports context/session loss (compaction, context limit) before the run began: safe NOT_STARTED retry on a fresh generation."""


RuntimeEvent = tuple[str, Any] | tuple[str, Any, Any]  # ("started", exec_id[, process_identity]) | ("timeout", seconds) | ...


class RuntimeTarget(Protocol):
    runtime_kind: str

    @property
    def resumable(self) -> bool: ...  # read capability; custom attributes also satisfy this boundary

    def fingerprint(self) -> str: ...
    def binding(self) -> str: ...  # model/profile binding; a mapping is only resumable under the same binding
    def create_session(self) -> str: ...
    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str: ...  # "ok" | "missing" | "unsupported" | "rejected"
    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]: ...


class SteerableTarget(Protocol):
    def steer(self, external_session_id: str, record: Any) -> None: ...
