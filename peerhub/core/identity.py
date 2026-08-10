"""Authenticated caller identity for trusted application boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

import psutil

from peerhub.core.errors import ActorUnauthorizedError
from peerhub.core.protocol import require_text


@dataclass(frozen=True, slots=True)
class AuthenticatedSubject:
    """Opaque proof that a trusted boundary authenticated one principal."""

    principal_id: str
    evidence_source: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "principal_id",
            require_text(self.principal_id, "principal_id"),
        )
        object.__setattr__(
            self,
            "evidence_source",
            require_text(self.evidence_source, "evidence_source"),
        )


class CallerIdentityProvider(Protocol):
    """Resolve a machine-owned identity for the current local caller."""

    def resolve(self) -> AuthenticatedSubject | None:
        """Return verified identity evidence, or ``None`` when unavailable."""

        ...


class CallerIdentityUnavailableError(RuntimeError):
    """The local caller could not be authenticated without caller input."""


class LocalProcessCallerIdentityProvider:
    """Authenticate the owner of the running CLI process via the OS."""

    def resolve(self) -> AuthenticatedSubject | None:
        try:
            raw_account_name = cast(
                object,
                psutil.Process().username(),
            )
        except (OSError, psutil.Error):
            return None
        if not isinstance(raw_account_name, str):
            return None
        account_name = raw_account_name.strip()
        if not account_name:
            return None
        return AuthenticatedSubject(
            principal_id=f"local-cli:{account_name}",
            evidence_source="os-process-owner",
        )


def require_caller_identity(
    provider: CallerIdentityProvider,
) -> AuthenticatedSubject:
    """Resolve one subject and fail closed when no evidence is available."""

    subject = provider.resolve()
    if subject is None:
        raise CallerIdentityUnavailableError(
            "local caller identity could not be verified"
        )
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        subject,
        AuthenticatedSubject,
    ):
        raise CallerIdentityUnavailableError(
            "local caller identity provider returned invalid evidence"
        )
    return subject


def require_authenticated_subject(
    subject: object,
) -> AuthenticatedSubject:
    """Reject missing or forged admission inputs before any durable write."""

    if not isinstance(subject, AuthenticatedSubject):
        raise ActorUnauthorizedError("unverified-subject")
    return subject


__all__ = [
    "AuthenticatedSubject",
    "CallerIdentityProvider",
    "CallerIdentityUnavailableError",
    "LocalProcessCallerIdentityProvider",
    "require_authenticated_subject",
    "require_caller_identity",
]
