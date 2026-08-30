"""Workspace-global leadership claims, challenge windows, and yields.

Deliberately NOT built on ``DutyLeaseCoordinator``: that models room-scoped,
immediately-``ACTIVE``, heartbeat-expiring, fence-strict duty leases, while
legacy leadership is workspace-global, ``PENDING``-with-a-challenge-window,
non-expiring, and vacates for anyone who asks. Placed in ``application``
rather than ``governance`` because it crosses the peer-registry and health
service boundaries, the same reasoning as ``role_assignment.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    StaleRevisionError,
)
from peerhub.core.protocol import CommandID, ErrorCode, JsonValue, require_text
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)
from peerhub.health.contract import AdmissionState, AvailabilityState
from peerhub.health.service import HealthService

LEADERSHIP_TARGET_ID = "leadership:workspace"

# Mirrors ArbiterBudgetManager's bounded retry. Legacy serializes these
# operations under one real global lock; CAS-retry-with-fresh-reevaluation
# is the analog of that sequential processing, not a shortcut.
_MAX_ATTEMPTS = 16


class LeadershipClaimDisposition(StrEnum):
    VACANT_CLAIM = "VACANT_CLAIM"
    SELF_RECLAIM = "SELF_RECLAIM"
    OPEN_WINDOW_CHALLENGE = "OPEN_WINDOW_CHALLENGE"
    FAILED_INCUMBENT_TAKEOVER = "FAILED_INCUMBENT_TAKEOVER"


class LeadershipStatus(StrEnum):
    VACANT = "VACANT"
    PENDING = "PENDING"
    # Reserved vocabulary only. Nothing in this round's scope writes ACTIVE:
    # no legacy operation transitions PENDING -> ACTIVE on elapsed time.
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class LeadershipPolicy:
    """Tuning constants for leadership claims.

    Hardcoded defaults injected at construction rather than loaded from a
    file: leadership has no on/off semantics, so ``arbiter.json``'s
    file-presence-as-enablement-switch pattern does not transfer.
    ``challenge_window_seconds`` derives from legacy's
    ``challenge_window_minutes`` (default 1) times 60.
    """

    challenge_window_seconds: int = 60
    monopoly_threshold: int = 3
    history_limit: int = 10

    def __post_init__(self) -> None:
        for name in (
            "challenge_window_seconds",
            "monopoly_threshold",
            "history_limit",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        # Legacy applies this same clamp to its configured threshold.
        object.__setattr__(
            self,
            "monopoly_threshold",
            max(1, self.monopoly_threshold),
        )


class LeadershipMonopolyError(InvalidMutationError):
    """AP-20: one peer has held the last N consecutive coordinator terms."""

    error_code = ErrorCode.ACTOR_UNAUTHORIZED

    def __init__(self, peer_node_id: str, threshold: int) -> None:
        self.peer_node_id = peer_node_id
        self.threshold = threshold
        super().__init__(
            (
                f"AP-20 violation: {peer_node_id} has been coordinator for "
                f"{threshold} consecutive terms"
            ),
            details={
                "peer_node_id": peer_node_id,
                "threshold": threshold,
            },
        )


class LeadershipIncumbentProtectedError(InvalidMutationError):
    """The sitting leader is healthy enough to keep leadership.

    Deliberately not ``PEER_UNAVAILABLE``: the incumbent IS available --
    that is precisely why the claim fails.
    """

    def __init__(
        self,
        peer_node_id: str,
        incumbent_peer_node_id: str,
        *,
        availability_state: AvailabilityState | None,
        admission_state: AdmissionState | None,
    ) -> None:
        self.peer_node_id = peer_node_id
        self.incumbent_peer_node_id = incumbent_peer_node_id
        self.availability_state = availability_state
        self.admission_state = admission_state
        status = (
            "UNKNOWN"
            if availability_state is None
            else availability_state.value
        )
        super().__init__(
            (
                "cannot claim leadership; "
                f"{incumbent_peer_node_id} is still active and healthy "
                f"({status})"
            ),
            details={
                "peer_node_id": peer_node_id,
                "incumbent_peer_node_id": incumbent_peer_node_id,
                "availability_state": (
                    None
                    if availability_state is None
                    else availability_state.value
                ),
                "admission_state": (
                    None if admission_state is None else admission_state.value
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class LeadershipClaimResult:
    disposition: LeadershipClaimDisposition
    submission: MutationSubmission
    target: TargetState


@dataclass(frozen=True, slots=True)
class LeadershipYieldResult:
    submission: MutationSubmission
    owner_mismatch: bool
    previous_leader_peer_node_id: str | None


class LeadershipService:
    """Claim and yield the single workspace-global leadership slot."""

    _DENIED_AVAILABILITY = {
        AvailabilityState.UNAVAILABLE,
        AvailabilityState.STALE,
    }
    _DENIED_ADMISSION = {
        AdmissionState.QUARANTINED,
        AdmissionState.COOLDOWN,
        AdmissionState.RECOVERY_REQUIRED,
    }

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        peer_registry: PeerRegistryService,
        health: HealthService,
        clock: Clock,
        ids: IdSource,
        policy: LeadershipPolicy = LeadershipPolicy(),
    ) -> None:
        self._broker = broker
        self._peer_registry = peer_registry
        self._health = health
        self._clock = clock
        self._ids = ids
        self._policy = policy

    @property
    def policy(self) -> LeadershipPolicy:
        return self._policy

    def _submit(
        self,
        *,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("leadership-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("leadership-command")),
                correlation_id=self._ids.new_id("leadership-correlation"),
                client_id="peerhub.leadership",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=LEADERSHIP_TARGET_ID,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(
                    kind="leadership.noop",
                    payload={},
                ),
            )
        )

    # --- reads ------------------------------------------------------------

    def get_leadership(self) -> TargetState | None:
        return self._broker.get_target(LEADERSHIP_TARGET_ID)

    def get_current_leader(self) -> TargetState | None:
        target = self.get_leadership()
        if target is None:
            return None
        status = target.state.get("status")
        if status not in (
            LeadershipStatus.PENDING.value,
            LeadershipStatus.ACTIVE.value,
        ):
            return None
        return target

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _required_state_text(target: TargetState, field: str) -> str:
        value = target.state.get(field)
        if not isinstance(value, str) or not value:
            raise InvalidMutationError(f"peer node has malformed {field}")
        return value

    @staticmethod
    def _history(target: TargetState | None) -> tuple[JsonValue, ...]:
        if target is None:
            return ()
        history = target.state.get("coordinator_history")
        if history is None:
            return ()
        if not isinstance(history, (list, tuple)):
            raise InvalidMutationError(
                "leadership coordinator_history is malformed"
            )
        return tuple(history)

    @staticmethod
    def _term(target: TargetState | None) -> int:
        if target is None:
            return 0
        term = target.state.get("term")
        if term is None:
            return 0
        if type(term) is not int:
            raise InvalidMutationError("leadership term is malformed")
        return term

    @staticmethod
    def _leader_peer_node_id(target: TargetState | None) -> str | None:
        """Return the stored leader's node id, or None when vacant.

        Defensive: a PENDING/ACTIVE record whose ``leader`` object is
        missing or malformed is treated as vacant rather than crashing a
        claim, since nothing can be evaluated against it.
        """

        if target is None:
            return None
        if target.state.get("status") == LeadershipStatus.VACANT.value:
            return None
        leader = target.state.get("leader")
        if not isinstance(leader, Mapping):
            return None
        peer_node_id = leader.get("peer_node_id")
        if not isinstance(peer_node_id, str) or not peer_node_id:
            return None
        return peer_node_id

    def _require_no_monopoly(
        self,
        target: TargetState | None,
        peer_node_id: str,
    ) -> None:
        """AP-20 coordinator monopoly guard.

        Runs first and unconditionally, including for a self-reclaim --
        legacy checks it before it ever looks at the current leader.
        """

        threshold = self._policy.monopoly_threshold
        tail = self._history(target)[-threshold:]
        if len(tail) != threshold:
            return
        for entry in tail:
            if not isinstance(entry, Mapping):
                return
            if entry.get("peer_node_id") != peer_node_id:
                return
        raise LeadershipMonopolyError(peer_node_id, threshold)

    def _null_claim_basis(
        self,
        disposition: LeadershipClaimDisposition,
        incumbent_peer_node_id: str | None,
    ) -> dict[str, JsonValue]:
        """Claim basis for a path that never read health evidence."""

        return {
            "disposition": disposition.value,
            "incumbent_peer_node_id": incumbent_peer_node_id,
            "projection_id": None,
            "projection_revision": None,
            "availability_state": None,
            "admission_state": None,
            "stale_at_read": None,
        }

    def _evaluate_claim(
        self,
        current: TargetState | None,
        peer_node_id: str,
        now: int,
    ) -> tuple[LeadershipClaimDisposition, dict[str, JsonValue]]:
        incumbent = self._leader_peer_node_id(current)

        if incumbent is None:
            return (
                LeadershipClaimDisposition.VACANT_CLAIM,
                self._null_claim_basis(
                    LeadershipClaimDisposition.VACANT_CLAIM,
                    None,
                ),
            )

        if incumbent == peer_node_id:
            return (
                LeadershipClaimDisposition.SELF_RECLAIM,
                self._null_claim_basis(
                    LeadershipClaimDisposition.SELF_RECLAIM,
                    incumbent,
                ),
            )

        # An unexpired challenge window PERMITS a competing claim to
        # overwrite the pending claimant. It does NOT protect the incumbent.
        # Gated purely on the timestamp -- legacy applies no status check.
        assert current is not None
        challenge_until = current.state.get("challenge_until")
        if isinstance(challenge_until, int) and now < challenge_until:
            return (
                LeadershipClaimDisposition.OPEN_WINDOW_CHALLENGE,
                self._null_claim_basis(
                    LeadershipClaimDisposition.OPEN_WINDOW_CHALLENGE,
                    incumbent,
                ),
            )

        return self._evaluate_incumbent_health(
            current,
            peer_node_id,
            incumbent,
            now,
        )

    def _evaluate_incumbent_health(
        self,
        current: TargetState,
        peer_node_id: str,
        incumbent: str,
        now: int,
    ) -> tuple[LeadershipClaimDisposition, dict[str, JsonValue]]:
        leader = current.state.get("leader")
        if not isinstance(leader, Mapping):
            raise InvalidMutationError("leadership leader binding is malformed")
        incumbent_peer_kind = leader.get("peer_kind")
        incumbent_profile_id = leader.get("profile_id")
        if not isinstance(incumbent_peer_kind, str) or not isinstance(
            incumbent_profile_id, str
        ):
            raise InvalidMutationError(
                "leadership leader binding has malformed health identity"
            )

        projection = self._health.read_health_projection(
            incumbent_peer_kind,
            incumbent_profile_id,
            evaluated_at=now,
        )
        if projection is None:
            # Absent evidence PROTECTS the incumbent. Same deny-list shape as
            # role-assignment, opposite user-visible outcome: legacy's
            # UNKNOWN is not in {RED, STALE}, so the incumbent survives.
            raise LeadershipIncumbentProtectedError(
                peer_node_id,
                incumbent,
                availability_state=None,
                admission_state=None,
            )

        stale_at_read = projection.stale_at_read
        replaceable = (
            stale_at_read
            or projection.effective_availability_state in self._DENIED_AVAILABILITY
            or projection.effective_admission_state in self._DENIED_ADMISSION
        )
        if not replaceable:
            raise LeadershipIncumbentProtectedError(
                peer_node_id,
                incumbent,
                availability_state=projection.effective_availability_state,
                admission_state=projection.effective_admission_state,
            )

        return (
            LeadershipClaimDisposition.FAILED_INCUMBENT_TAKEOVER,
            {
                "disposition": (
                    LeadershipClaimDisposition.FAILED_INCUMBENT_TAKEOVER.value
                ),
                "incumbent_peer_node_id": incumbent,
                "projection_id": projection.projection.projection_id,
                "projection_revision": projection.projection.revision,
                "availability_state": projection.effective_availability_state.value,
                "admission_state": projection.effective_admission_state.value,
                "stale_at_read": stale_at_read,
            },
        )

    # --- mutations --------------------------------------------------------

    def claim_leadership(
        self,
        *,
        peer_node_id: str,
        actor_id: str,
        reason: str = "",
        domain: str = "",
    ) -> LeadershipClaimResult:
        normalized_peer_node_id = require_text(peer_node_id, "peer_node_id")
        normalized_actor_id = require_text(actor_id, "actor_id")

        # Deliberately stricter than legacy, which accepts arbitrary --agent
        # text. An unresolvable name would have no health identity, making
        # it permanently unchallengeable once it became the incumbent.
        peer_node = self._peer_registry.get_node(normalized_peer_node_id)
        peer_kind = self._required_state_text(peer_node, "peer_kind")
        profile_id = self._required_state_text(peer_node, "profile_id")
        adapter_version = self._required_state_text(
            peer_node, "adapter_version"
        )

        stored_domain = domain or reason or "general"
        stored_reason = reason or "manual_claim"

        for _ in range(_MAX_ATTEMPTS):
            current = self._broker.get_target(LEADERSHIP_TARGET_ID)
            now = self._clock.now()

            # AP-20 and incumbent-protection raise straight out of the loop:
            # only StaleRevisionError may consume a retry attempt.
            self._require_no_monopoly(current, normalized_peer_node_id)
            disposition, claim_basis = self._evaluate_claim(
                current,
                normalized_peer_node_id,
                now,
            )

            claim_id = self._ids.new_id("leadership-claim")
            term = self._term(current) + 1
            history_entry: dict[str, JsonValue] = {
                "claim_id": claim_id,
                "term": term,
                "peer_node_id": normalized_peer_node_id,
                "peer_kind": peer_kind,
                "profile_id": profile_id,
                "claimed_at": now,
                "domain": stored_domain,
                "reason": stored_reason,
            }
            history = (
                *self._history(current),
                history_entry,
            )[-self._policy.history_limit:]

            desired_state: dict[str, JsonValue] = {
                "kind": "leadership",
                "scope": None,
                "schema_version": 1,
                "status": LeadershipStatus.PENDING.value,
                "term": term,
                "claim_id": claim_id,
                "leader": {
                    "peer_node_id": normalized_peer_node_id,
                    "peer_node_target_id": peer_node.target_id,
                    "peer_node_revision": peer_node.revision,
                    "peer_kind": peer_kind,
                    "profile_id": profile_id,
                    "adapter_version": adapter_version,
                },
                "claimed_at": now,
                "claimed_by": normalized_actor_id,
                "challenge_until": now + self._policy.challenge_window_seconds,
                "domain": stored_domain,
                "reason": stored_reason,
                "claim_basis": claim_basis,
                # Legacy replaces the whole leadership sub-object on a claim
                # too, so a prior yield's fields do not survive it.
                "yielded_by": None,
                "yielded_at": None,
                "yield_reason": None,
                "coordinator_history": history,
                "updated_at": now,
            }

            try:
                submission = self._submit(
                    expected_revision=(
                        0 if current is None else current.revision
                    ),
                    actor_id=normalized_actor_id,
                    operation="leadership.claim",
                    desired_state=desired_state,
                )
            except StaleRevisionError:
                continue

            target = self._broker.get_target(LEADERSHIP_TARGET_ID)
            if target is None:
                raise RuntimeError(
                    "committed leadership claim target could not be re-read"
                )
            return LeadershipClaimResult(
                disposition=disposition,
                submission=submission,
                target=target,
            )

        raise InvalidMutationError(
            "leadership changed repeatedly during claim"
        )

    def yield_leadership(
        self,
        *,
        yielding_peer_id: str,
        actor_id: str,
        reason: str = "",
    ) -> LeadershipYieldResult:
        """Vacate leadership unconditionally.

        Always mutates -- even when leadership is already VACANT, no target
        exists yet, or the yielding peer is not the current leader. An
        ownership mismatch is returned as warning metadata, never raised.
        """

        normalized_yielding_peer_id = require_text(
            yielding_peer_id, "yielding_peer_id"
        )
        normalized_actor_id = require_text(actor_id, "actor_id")
        yield_reason = reason or "none"

        for _ in range(_MAX_ATTEMPTS):
            current = self._broker.get_target(LEADERSHIP_TARGET_ID)
            now = self._clock.now()

            # Computed from THIS attempt's read, so a retry never reports a
            # leader that had already been replaced.
            previous_leader = self._leader_peer_node_id(current)
            owner_mismatch = (
                previous_leader is not None
                and previous_leader != normalized_yielding_peer_id
            )

            desired_state: dict[str, JsonValue] = {
                "kind": "leadership",
                "scope": None,
                "schema_version": 1,
                "status": LeadershipStatus.VACANT.value,
                # term and coordinator_history are retained: in legacy they
                # are siblings of the leadership sub-object in state.json and
                # a yield never touches them.
                "term": self._term(current),
                "claim_id": None,
                "leader": None,
                "claimed_at": None,
                "claimed_by": None,
                "challenge_until": None,
                "domain": None,
                # Claim-time context, same as domain/claimed_at/claimed_by
                # above -- a stale claim reason surviving a full vacancy
                # transition would be the same misleading-residue mistake
                # the ratified design corrected challenge_until for.
                "reason": None,
                "claim_basis": None,
                "yielded_by": normalized_yielding_peer_id,
                "yielded_at": now,
                "yield_reason": yield_reason,
                "coordinator_history": self._history(current),
                "updated_at": now,
            }

            try:
                submission = self._submit(
                    expected_revision=(
                        0 if current is None else current.revision
                    ),
                    actor_id=normalized_actor_id,
                    operation="leadership.yield",
                    desired_state=desired_state,
                )
            except StaleRevisionError:
                continue

            return LeadershipYieldResult(
                submission=submission,
                owner_mismatch=owner_mismatch,
                previous_leader_peer_node_id=previous_leader,
            )

        raise InvalidMutationError(
            "leadership changed repeatedly during yield"
        )
