"""Gateway-level actor authorization for ApplicationAPI.submit() (R4/P4b).

Ratified 2026-09-19 (docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md):
elevates the already-shipped, already-tested D-CTX credential verifier
(peerhub.persistence.dispatch_context.verify_credential_for_actor, wired in
today only for ConsensusService.cast_vote) into a single gateway-level check
applied uniformly by ApplicationAPI.submit(), replacing the previous
near-tautological ``caller.client_id == cmd.submission.client_id`` stub.

Generalizes the asserted/verified duality already shipped for consensus
voting: a credential, when presented on the envelope, must verify against
the envelope's claimed actor_id, or the call is rejected outright -- no
falling back to the asserted check once a credential was presented. Absent
a credential, the call remains a valid asserted actor claim (today's
behavior, unchanged). Whether a given command REQUIRES a credential is a
per-command policy decision recorded in
docs/design/peerhub-production-call-map-R1.json's ``authorizer_behavior``
field, not something this class decides on its own.
"""

from __future__ import annotations

from typing import Protocol

from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandEnvelope


class CredentialVerifier(Protocol):
    """Same shape as peerhub.governance.consensus.CredentialVerifier -- a
    plain callable so this module never depends on peerhub.persistence
    directly. The real implementation is
    peerhub.persistence.dispatch_context.verify_credential_for_actor,
    wired in at the same production construction sites as before
    (peerhub/runtime.py, peerhub/cli/__init__.py)."""

    def __call__(self, *, credential_id: str, claimed_actor_id: str) -> bool: ...


class GovernanceAuthorizer:
    """Uniform actor-authorization check applied by every
    ApplicationAPI.submit() call, regardless of domain."""

    def __init__(self, verifier: CredentialVerifier | None) -> None:
        self._verifier = verifier

    def authorize(
        self,
        *,
        caller: RequestContext,
        envelope: CommandEnvelope,
        submission_client_id: str,
    ) -> bool:
        """Return whether this call is authorized to proceed.

        Verified path (``envelope.credential_id`` present): the credential
        must verify against ``envelope.actor_id`` via the injected
        verifier, or authorization fails -- a missing ``actor_id`` or a
        missing verifier both fail closed.

        Asserted path (no credential on the envelope): the pre-R4/P4b
        check, unchanged -- ``caller.client_id`` must equal the command's
        own ``submission.client_id``.
        """

        if envelope.credential_id is not None:
            if envelope.actor_id is None or self._verifier is None:
                return False
            return self._verifier(
                credential_id=envelope.credential_id,
                claimed_actor_id=envelope.actor_id,
            )
        return caller.client_id == submission_client_id
