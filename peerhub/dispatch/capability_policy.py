"""Minimal concrete capability policy and enforcement-evidence providers.

Errata Section 7.1 makes ``CapabilityPolicy`` and
``PeerEnforcementEvidenceProvider`` injected dependencies of
``AdmissionCoordinator``.  This module supplies the smallest concrete
implementations that satisfy the fail-closed requirements of Sections 7.1 and
7.4; it is deliberately not a policy engine.

The defaults grant the requested tier but never invent enforcement evidence:
no real peer ships a measured enforcement ceiling today (DIR-004 requires
``cli_live``/``app_server``/``statusline``/``empirical_probe`` corroboration and
none exists), so every real peer resolves to ``enforcement_ceiling=None``.  A
mutating dispatch therefore fails closed at the mandatory floor regardless of
which peer was selected.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from peerhub.core.protocol import RevisionValue, require_text

from .capability import (
    CapabilityGrantDecision,
    CapabilityLeaseViolation,
    CapabilityTier,
    EnforcementLevel,
    PeerEnforcementEvidence,
    ValidatedCapabilityBinding,
)

# Peer kinds with a built-in adapter.  Each maps to itself with no measured
# enforcement ceiling: DIR-002 records ag's filesystem-confinement gap as an
# empirical_probe, and cc/cx enforcement remains TEST NEEDED (errata 7.4).
_BUILTIN_PEER_KINDS: frozenset[str] = frozenset({"ag", "cc", "cx"})


class StaticPeerEnforcementEvidenceProvider:
    """Resolve peer evidence from an explicit machine-owned table.

    Unmapped instances resolve to their own identifier as peer kind with an
    absent ceiling.  That is safe by construction: an absent ceiling can never
    satisfy an ``ENFORCED`` or ``CONFINED`` floor, so an unrecognized instance
    can only ever be granted ``READ_ONLY``.
    """

    def __init__(
        self,
        evidence: Mapping[str, PeerEnforcementEvidence] | None = None,
    ) -> None:
        self._evidence: Mapping[str, PeerEnforcementEvidence] = (
            MappingProxyType(dict(evidence)) if evidence else MappingProxyType({})
        )

    def resolve(
        self,
        *,
        peer_instance_id: str,
        profile_id: str,
    ) -> PeerEnforcementEvidence:
        """Return machine-owned evidence for one selected target."""

        instance = require_text(peer_instance_id, "peer_instance_id")
        require_text(profile_id, "profile_id")
        known = self._evidence.get(instance)
        if known is not None:
            return known
        return PeerEnforcementEvidence(
            peer_instance_id=instance,
            peer_kind=instance,
            enforcement_ceiling=None,
            source_tag="absent",
        )


def default_enforcement_evidence_provider() -> StaticPeerEnforcementEvidenceProvider:
    """Return the shipped provider: built-in peers, no measured ceiling."""

    return StaticPeerEnforcementEvidenceProvider(
        {
            kind: PeerEnforcementEvidence(
                peer_instance_id=kind,
                peer_kind=kind,
                enforcement_ceiling=None,
                source_tag="absent",
            )
            for kind in _BUILTIN_PEER_KINDS
        }
    )


class StaticCapabilityPolicy:
    """Grant exactly the requested tier; revalidate expiry and revision.

    Least privilege is structural rather than configured: ``authorized_tier``
    always equals ``required_tier`` (errata 7.1 point 6, and the
    ``authorized_tier = required_tier`` CHECK in migration 0018).  The
    mandatory enforcement floor is applied by the caller before ``decide()``
    and passed in as ``minimum_enforcement``; this policy never lowers it.
    """

    def __init__(
        self,
        *,
        issuer_id: str = "peerhub.dispatch.capability_policy.StaticCapabilityPolicy",
        lease_ttl_seconds: int | None = None,
        denied_tiers: frozenset[CapabilityTier] = frozenset(),
    ) -> None:
        self._issuer_id = require_text(issuer_id, "issuer_id")
        if lease_ttl_seconds is not None and (
            type(lease_ttl_seconds) is not int or lease_ttl_seconds <= 0
        ):
            raise ValueError("lease_ttl_seconds must be a positive integer")
        self._lease_ttl_seconds = lease_ttl_seconds
        self._denied_tiers = denied_tiers

    @property
    def issuer_id(self) -> str:
        """Return the issuer recorded on every lease this policy grants."""

        return self._issuer_id

    def expires_at(self, issued_at: int) -> int | None:
        """Return the expiry stamp for a lease issued at ``issued_at``."""

        if self._lease_ttl_seconds is None:
            return None
        return issued_at + self._lease_ttl_seconds

    def decide(
        self,
        *,
        subject_principal_id: str,
        selected_peer_kind: str,
        selected_peer_instance_id: str,
        selected_profile_id: str,
        policy_revision: RevisionValue,
        required_tier: CapabilityTier,
        minimum_enforcement: EnforcementLevel,
    ) -> CapabilityGrantDecision:
        """Return a grant decision bound to this subject and target."""

        if required_tier in self._denied_tiers:
            return CapabilityGrantDecision(
                granted=False,
                subject_principal_id=subject_principal_id,
                selected_peer_kind=selected_peer_kind,
                selected_peer_instance_id=selected_peer_instance_id,
                selected_profile_id=selected_profile_id,
                required_tier=required_tier,
                authorized_tier=None,
                minimum_enforcement=None,
                policy_revision=policy_revision,
                issuer_id=self._issuer_id,
                denial_reason="tier denied by policy",
            )
        return CapabilityGrantDecision(
            granted=True,
            subject_principal_id=subject_principal_id,
            selected_peer_kind=selected_peer_kind,
            selected_peer_instance_id=selected_peer_instance_id,
            selected_profile_id=selected_profile_id,
            required_tier=required_tier,
            authorized_tier=required_tier,
            minimum_enforcement=minimum_enforcement,
            policy_revision=policy_revision,
            issuer_id=self._issuer_id,
        )

    def revalidate(
        self,
        binding: ValidatedCapabilityBinding,
        *,
        current_policy_revision: RevisionValue,
        now: int,
    ) -> None:
        """Fail closed on expiry or any policy-revision change."""

        lease = binding.capability_lease
        if lease.policy_revision != current_policy_revision:
            raise CapabilityLeaseViolation(
                "capability lease policy revision differs from the current "
                "policy revision"
            )
        if lease.expires_at is not None and now > lease.expires_at:
            raise CapabilityLeaseViolation("capability lease has expired")


def default_capability_policy() -> StaticCapabilityPolicy:
    """Return the shipped policy used when no policy is injected."""

    return StaticCapabilityPolicy()


__all__ = [
    "StaticCapabilityPolicy",
    "StaticPeerEnforcementEvidenceProvider",
    "default_capability_policy",
    "default_enforcement_evidence_provider",
]
