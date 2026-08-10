"""Capability authority types and pure binding invariants.

This module is intentionally independent of persistence and dispatch
coordination.  Admission, replay, and dispatch gates can therefore reuse the
same validators without duplicating authority logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import CommandID, RevisionValue, require_text

if TYPE_CHECKING:
    from .contract import AdmissionReceipt, LeaseSnapshot, RequestSnapshot


class CapabilityTier(IntEnum):
    """Increasing downstream peer authority granted to one dispatch."""

    READ_ONLY = 0
    WORKTREE_WRITE = 1
    GIT_MUTATE = 2
    REMOTE_MUTATE = 3


class EnforcementLevel(IntEnum):
    """Increasing strength of enforcement for a capability tier."""

    ADVISORY = 0
    ENFORCED = 1
    CONFINED = 2


class CapabilityLeaseViolation(InvalidMutationError):
    """A capability lease fails an immutable authority invariant."""

    def __init__(self, invariant: str) -> None:
        self.invariant = require_text(invariant, "invariant")
        super().__init__(
            "capability lease binding invariant failed",
            details={"invariant": self.invariant},
        )


def _require_enum_member(value: object, enum_type: type[IntEnum], name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be {enum_type.__name__}")


def _require_nonnegative_int(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _normalize_revision(value: RevisionValue, name: str) -> RevisionValue:
    if type(value) is int:
        _require_nonnegative_int(value, name)
        return value
    if isinstance(value, str):
        return require_text(value, name)
    raise ValueError(f"{name} must be a string or integer")


def _require_sha256_hex(value: str, name: str) -> str:
    normalized = require_text(value, name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized


@dataclass(frozen=True)
class CapabilityGrantDecision:
    """Policy decision bound to one subject, target, and requested tier."""

    granted: bool
    subject_principal_id: str
    selected_peer_kind: str
    selected_peer_instance_id: str
    selected_profile_id: str
    required_tier: CapabilityTier
    authorized_tier: CapabilityTier | None
    minimum_enforcement: EnforcementLevel | None
    policy_revision: RevisionValue
    issuer_id: str
    denial_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.granted) is not bool:
            raise ValueError("granted must be a boolean")
        for name in (
            "subject_principal_id",
            "selected_peer_kind",
            "selected_peer_instance_id",
            "selected_profile_id",
            "issuer_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_enum_member(
            self.required_tier,
            CapabilityTier,
            "required_tier",
        )
        object.__setattr__(
            self,
            "policy_revision",
            _normalize_revision(self.policy_revision, "policy_revision"),
        )

        if self.granted:
            authorized_tier = self.authorized_tier
            if not isinstance(authorized_tier, CapabilityTier):
                raise ValueError(
                    "authorized_tier must be CapabilityTier"
                )
            _require_enum_member(
                self.minimum_enforcement,
                EnforcementLevel,
                "minimum_enforcement",
            )
            if authorized_tier < self.required_tier:
                raise ValueError(
                    "authorized_tier cannot be below required_tier"
                )
            if self.denial_reason is not None:
                raise ValueError(
                    "granted decision cannot have a denial_reason"
                )
            return

        if self.authorized_tier is not None:
            raise ValueError(
                "denied decision cannot have an authorized_tier"
            )
        if self.minimum_enforcement is not None:
            raise ValueError(
                "denied decision cannot have minimum_enforcement"
            )
        if self.denial_reason is None:
            raise ValueError("denied decision requires a denial_reason")
        object.__setattr__(
            self,
            "denial_reason",
            require_text(self.denial_reason, "denial_reason"),
        )


@dataclass(frozen=True)
class CapabilityLease:
    """Durable, issuer-owned capability authority for one command."""

    capability_lease_id: str
    command_id: CommandID
    admission_receipt_id: str
    session_lease_id: str
    subject_principal_id: str
    selected_peer_kind: str
    required_tier: CapabilityTier
    authorized_tier: CapabilityTier
    minimum_enforcement: EnforcementLevel
    selected_peer_instance_id: str
    selected_profile_id: str
    route_decision_digest: str
    policy_revision: RevisionValue
    issuer_id: str
    issued_at: int
    expires_at: int | None

    def __post_init__(self) -> None:
        for name in (
            "capability_lease_id",
            "admission_receipt_id",
            "session_lease_id",
            "subject_principal_id",
            "selected_peer_kind",
            "selected_peer_instance_id",
            "selected_profile_id",
            "issuer_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(require_text(str(self.command_id), "command_id")),
        )
        _require_enum_member(
            self.required_tier,
            CapabilityTier,
            "required_tier",
        )
        _require_enum_member(
            self.authorized_tier,
            CapabilityTier,
            "authorized_tier",
        )
        _require_enum_member(
            self.minimum_enforcement,
            EnforcementLevel,
            "minimum_enforcement",
        )
        object.__setattr__(
            self,
            "route_decision_digest",
            _require_sha256_hex(
                self.route_decision_digest,
                "route_decision_digest",
            ),
        )
        object.__setattr__(
            self,
            "policy_revision",
            _normalize_revision(self.policy_revision, "policy_revision"),
        )
        _require_nonnegative_int(self.issued_at, "issued_at")
        if self.expires_at is not None:
            _require_nonnegative_int(self.expires_at, "expires_at")
            if self.expires_at < self.issued_at:
                raise ValueError("expires_at cannot precede issued_at")


@dataclass(frozen=True)
class ValidatedCapabilityBinding:
    """Typed result proving the immutable capability binding was checked."""

    request: RequestSnapshot
    receipt: AdmissionReceipt
    session_lease: LeaseSnapshot
    capability_lease: CapabilityLease


def _require_binding(condition: bool, invariant: str) -> None:
    if not condition:
        raise CapabilityLeaseViolation(invariant)


def validate_capability_binding(
    request: RequestSnapshot,
    receipt: AdmissionReceipt,
    session_lease: LeaseSnapshot,
    capability_lease: CapabilityLease,
    *,
    expected_peer_kind: str,
) -> ValidatedCapabilityBinding:
    """Validate every immutable capability-to-admission equality.

    ``expected_peer_kind`` is required because the approved design makes it a
    machine-owned instance lookup, while the current request snapshot does not
    store peer kind.  Callers must never derive it from command payload text.
    """

    machine_peer_kind = require_text(
        expected_peer_kind,
        "expected_peer_kind",
    )
    _require_binding(
        capability_lease.command_id == request.command_id,
        "capability command_id must match request command_id",
    )
    _require_binding(
        capability_lease.command_id == receipt.command_id,
        "capability command_id must match receipt command_id",
    )
    _require_binding(
        capability_lease.command_id == session_lease.fence.command_id,
        "capability command_id must match session lease command_id",
    )
    _require_binding(
        capability_lease.admission_receipt_id
        == receipt.admission_receipt_id,
        "capability admission_receipt_id must match receipt",
    )
    _require_binding(
        capability_lease.session_lease_id == request.lease_id,
        "capability session_lease_id must match request lease_id",
    )
    _require_binding(
        capability_lease.session_lease_id == receipt.lease_id,
        "capability session_lease_id must match receipt lease_id",
    )
    _require_binding(
        capability_lease.session_lease_id == session_lease.lease_id,
        "capability session_lease_id must match session lease_id",
    )
    _require_binding(
        capability_lease.subject_principal_id
        == request.authenticated_principal,
        "capability subject must match authenticated principal",
    )
    _require_binding(
        capability_lease.selected_peer_instance_id
        == request.selected_peer_instance_id,
        "capability peer instance must match request",
    )
    _require_binding(
        capability_lease.selected_profile_id
        == request.selected_profile_id,
        "capability profile must match request",
    )
    _require_binding(
        capability_lease.route_decision_digest
        == request.route_decision_digest,
        "capability route digest must match request",
    )
    _require_binding(
        capability_lease.selected_peer_kind == machine_peer_kind,
        "capability peer kind must match machine-owned lookup",
    )
    _require_binding(
        capability_lease.policy_revision == request.policy_revision,
        "capability policy revision must match request",
    )
    _require_binding(
        capability_lease.policy_revision == receipt.policy_revision,
        "capability policy revision must match receipt",
    )
    _require_binding(
        capability_lease.required_tier
        == request.required_capability_tier,
        "capability required tier must match request",
    )
    _require_binding(
        capability_lease.authorized_tier
        == capability_lease.required_tier,
        "capability authorized tier must equal required tier",
    )
    return ValidatedCapabilityBinding(
        request=request,
        receipt=receipt,
        session_lease=session_lease,
        capability_lease=capability_lease,
    )


def mandatory_enforcement_floor(
    peer_kind: str,
    tier: CapabilityTier,
) -> EnforcementLevel:
    """Return the non-configurable minimum enforcement for a dispatch."""

    require_text(peer_kind, "peer_kind")
    _require_enum_member(tier, CapabilityTier, "tier")
    if peer_kind == "ag" and tier is not CapabilityTier.READ_ONLY:
        return EnforcementLevel.CONFINED
    if tier is not CapabilityTier.READ_ONLY:
        return EnforcementLevel.ENFORCED
    return EnforcementLevel.ADVISORY


@dataclass(frozen=True)
class PeerEnforcementEvidence:
    """Machine-owned peer identity and measured enforcement ceiling.

    ``enforcement_ceiling`` is ``None`` whenever no qualifying measurement
    exists.  Errata Section 7.4 requires unknown evidence to sort below every
    *enforceable* floor, so ``None`` is never widened into ``ADVISORY``.
    ``source_tag`` carries the DIR-004 provenance of the measurement, or
    ``"absent"`` when there is none.
    """

    peer_instance_id: str
    peer_kind: str
    enforcement_ceiling: EnforcementLevel | None
    source_tag: str

    def __post_init__(self) -> None:
        for name in ("peer_instance_id", "peer_kind", "source_tag"):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if self.enforcement_ceiling is not None:
            _require_enum_member(
                self.enforcement_ceiling,
                EnforcementLevel,
                "enforcement_ceiling",
            )
        elif self.source_tag != "absent":
            raise ValueError(
                "unmeasured evidence must carry the 'absent' source tag"
            )


def require_enforcement_floor(
    peer_kind: str,
    tier: CapabilityTier,
    evidence: PeerEnforcementEvidence | None,
) -> EnforcementLevel:
    """Return the satisfied mandatory floor, or fail closed.

    Per errata Section 7.4 the floor is code-owned.  Unknown or absent
    evidence is insufficient for an ``ENFORCED`` or ``CONFINED`` floor and is
    never guessed upward; a ``READ_ONLY`` dispatch whose floor is ``ADVISORY``
    needs no enforcement measurement to proceed.
    """

    floor = mandatory_enforcement_floor(peer_kind, tier)
    if floor is EnforcementLevel.ADVISORY:
        return floor
    if evidence is None or evidence.enforcement_ceiling is None:
        raise CapabilityLeaseViolation(
            "selected adapter has no measured enforcement evidence for the "
            "mandatory enforcement floor"
        )
    if evidence.enforcement_ceiling < floor:
        raise CapabilityLeaseViolation(
            "selected adapter cannot meet the mandatory enforcement floor"
        )
    return floor


class PeerEnforcementEvidenceProvider(Protocol):
    """Resolve a selected instance/profile to machine-owned peer evidence."""

    def resolve(
        self,
        *,
        peer_instance_id: str,
        profile_id: str,
    ) -> PeerEnforcementEvidence:
        """Return evidence for one selected target.

        Implementations must never read peer kind or enforcement claims from
        command payload text.
        """

        ...


class CapabilityPolicy(Protocol):
    """Authorize a capability grant and revalidate it over time."""

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

        ...

    def expires_at(self, issued_at: int) -> int | None:
        """Return the expiry stamp for a lease issued at ``issued_at``."""

        ...

    def revalidate(
        self,
        binding: ValidatedCapabilityBinding,
        *,
        current_policy_revision: RevisionValue,
        now: int,
    ) -> None:
        """Raise ``CapabilityLeaseViolation`` if the lease is no longer valid."""

        ...


@dataclass(frozen=True)
class ValidatedCapabilityLease:
    """Opaque dispatch-time proof; carries no independent grant authority."""

    capability_lease_id: str
    command_id: CommandID
    subject_principal_id: str
    selected_peer_kind: str
    selected_peer_instance_id: str
    selected_profile_id: str
    authorized_tier: CapabilityTier
    minimum_enforcement: EnforcementLevel
    satisfied_floor: EnforcementLevel
    revalidated_policy_revision: RevisionValue


@dataclass(frozen=True)
class InvocationEnforcementReceipt:
    """Evidence of the actual realized enforcement for one invocation plan.

    Errata Section 7.4: the receipt records the invocation-bound realized
    level, controls, evidence source tag, and plan digest.  It is accepted
    only when corroborated by the machine-owned launcher/evidence provider;
    an adapter's self-declaration is not proof.

    ``record_dispatch_intent*()`` requires this receipt alongside the
    ``ValidatedCapabilityLease`` to close the window between pre-plan
    validation and the dispatch-intent state transition.
    """

    capability_lease_id: str
    command_id: CommandID
    realized_enforcement: EnforcementLevel
    controls_description: str
    evidence_source_tag: str
    plan_digest: str

    def __post_init__(self) -> None:
        for name in (
            "capability_lease_id",
            "controls_description",
            "evidence_source_tag",
            "plan_digest",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(require_text(str(self.command_id), "command_id")),
        )
        _require_enum_member(
            self.realized_enforcement,
            EnforcementLevel,
            "realized_enforcement",
        )


__all__ = [
    "CapabilityGrantDecision",
    "CapabilityLease",
    "CapabilityLeaseViolation",
    "CapabilityPolicy",
    "CapabilityTier",
    "EnforcementLevel",
    "InvocationEnforcementReceipt",
    "PeerEnforcementEvidence",
    "PeerEnforcementEvidenceProvider",
    "ValidatedCapabilityBinding",
    "ValidatedCapabilityLease",
    "mandatory_enforcement_floor",
    "require_enforcement_floor",
    "validate_capability_binding",
]
