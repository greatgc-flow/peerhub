"""Tests for machine-owned local caller identity resolution."""

from types import SimpleNamespace

import pytest

from peerhub.core.identity import (
    AuthenticatedSubject,
    CallerIdentityUnavailableError,
    LocalProcessCallerIdentityProvider,
    require_caller_identity,
)


def test_local_process_identity_uses_os_process_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "peerhub.core.identity.psutil.Process",
        lambda: SimpleNamespace(username=lambda: r"DOMAIN\alice"),
    )

    subject = require_caller_identity(
        LocalProcessCallerIdentityProvider()
    )

    assert subject == AuthenticatedSubject(
        principal_id=r"local-cli:DOMAIN\alice",
        evidence_source="os-process-owner",
    )
    assert subject.principal_id != "cli-user"


def test_local_process_identity_fails_closed_when_owner_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied() -> object:
        raise OSError("process-token lookup denied")

    monkeypatch.setattr(
        "peerhub.core.identity.psutil.Process",
        denied,
    )

    with pytest.raises(
        CallerIdentityUnavailableError,
        match="could not be verified",
    ):
        require_caller_identity(LocalProcessCallerIdentityProvider())
