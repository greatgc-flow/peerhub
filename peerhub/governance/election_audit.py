"""Immutable capability-election decision and outcome audit records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from peerhub.application.leadership import LeadershipClaimDisposition
from peerhub.core.context import Clock, IdSource
from peerhub.core.evidence import EvidenceRef
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandID, JsonValue, canonical_json_bytes
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    TargetState,
)
from peerhub.routing.capability_matching import (
    CapabilityEvidenceProvenance,
    CapabilityMatch,
    CapabilityRankingResult,
    FallbackResolution,
)


@dataclass(frozen=True, slots=True)
class LeadershipElectionReceipt:
    election_id: str
    decision_target_id: str
    decision_revision: int
    decision_hash: str
    outcome_target_id: str
    outcome_revision: int
    outcome: Literal["CLAIMED"]
    selected_node_id: str
    selection_basis: str
    leadership_target_id: str
    leadership_revision: int
    leadership_claim_id: str
    leadership_claim_disposition: LeadershipClaimDisposition


def _provenance_json(
    provenance: CapabilityEvidenceProvenance,
) -> dict[str, JsonValue]:
    return {
        "fact": provenance.fact,
        "evidence_state": provenance.evidence_state.value,
        "source_kind": provenance.source_kind,
        "source_id": provenance.source_id,
        "source_revision": provenance.source_revision,
        "source_digest": provenance.source_digest,
        "evidence_refs": tuple(str(ref) for ref in provenance.evidence_refs),
        "observed_at": provenance.observed_at,
    }


def _match_json(match: CapabilityMatch) -> dict[str, JsonValue]:
    return {
        "node_id": match.node_id,
        "peer_kind": match.peer_kind,
        "profile_id": match.profile_id,
        "candidate_status": match.candidate_status.value,
        "exclusion_reason": match.exclusion_reason,
        "availability_status": (
            None
            if match.availability_status is None
            else match.availability_status.value
        ),
        "admission_status": (
            None
            if match.admission_status is None
            else match.admission_status.value
        ),
        "cost_tier": match.cost_tier,
        "cost_tier_state": match.cost_tier_state.value,
        "model_tier": match.model_tier,
        "model_tier_state": match.model_tier_state.value,
        "ordered_capabilities": match.ordered_capabilities,
        "ranking_score": match.ranking_score,
        "components": tuple(
            {
                "name": component.name.value,
                "state": component.state.value,
                "raw_value": component.raw_value,
                "points": component.points,
                "reason": component.reason,
                "provenance_indexes": component.provenance_indexes,
            }
            for component in match.components
        ),
        "provenance": tuple(
            _provenance_json(provenance)
            for provenance in match.provenance
        ),
    }


def _fallback_json(
    fallback: FallbackResolution | None,
) -> JsonValue:
    if fallback is None:
        return None
    return {
        "node_id": fallback.node_id,
        "basis": fallback.basis,
        "considered_node_ids": fallback.considered_node_ids,
    }


def _sha256(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ElectionAuditService:
    """Create-only governed records around one non-idempotent election."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids

    def _create(
        self,
        *,
        target_id: str,
        actor_id: str,
        operation: str,
        state: dict[str, JsonValue],
    ) -> TargetState:
        request_id = self._ids.new_id("leader-election-audit-request")
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("leader-election-audit-command")
                ),
                correlation_id=self._ids.new_id(
                    "leader-election-audit-correlation"
                ),
                client_id="peerhub.leader-election-audit",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="capability-native-v1:1",
                target_id=target_id,
                expected_revision=0,
                operation=operation,
                desired_state=state,
                effect_intent=EffectIntent(
                    kind="leader-election-audit.noop", payload={}
                ),
            )
        )
        target = self._broker.get_target(target_id)
        if target is None:  # pragma: no cover - committed CAS guarantees it
            raise RuntimeError("committed election audit was not readable")
        return target

    def create_decision(
        self,
        *,
        ranking: CapabilityRankingResult,
        election_id: str,
        command_id: str,
        correlation_id: str,
        requested_by: str,
        reason: str,
        selected_node_id: str | None,
        selection_basis: str,
    ) -> TargetState:
        matches = (*ranking.ordered_matches, *ranking.excluded_candidates)
        candidate_snapshot = tuple(_match_json(match) for match in matches)
        configuration_facts = tuple(
            sorted(
                {
                    (
                        provenance.source_id,
                        provenance.source_revision,
                        provenance.source_digest,
                    )
                    for match in matches
                    for provenance in match.provenance
                    if provenance.fact
                    in {
                        "capability-configuration",
                        "capability-matching-policy",
                    }
                }
            )
        )
        configuration_digest = _sha256(
            configuration_facts
            or ((ranking.policy_id, ranking.policy_revision, None),)
        )
        evidence_refs: tuple[EvidenceRef, ...] = tuple(
            dict.fromkeys(
                ref
                for match in matches
                for provenance in match.provenance
                for ref in provenance.evidence_refs
            )
        )
        basis: dict[str, JsonValue] = {
            "needs": ranking.needs,
            "requested_effort": ranking.requested_effort,
            "reason": reason,
            "domain": ranking.needs or "general",
            "formula_id": ranking.formula_id,
            "policy_id": ranking.policy_id,
            "policy_revision": ranking.policy_revision,
            "configuration_digest": configuration_digest,
            "candidate_snapshot": candidate_snapshot,
            "ordered_match_node_ids": tuple(
                match.node_id for match in ranking.ordered_matches
            ),
            "selected_node_id": selected_node_id,
            "selection_basis": selection_basis,
            "fallback_resolution": _fallback_json(ranking.fallback),
            "tie_break": (
                "ranking_score_desc",
                "healthy_first",
                "node_id_asc",
            ),
            "evidence_refs": tuple(str(ref) for ref in evidence_refs),
        }
        decision_hash = _sha256(basis)
        state: dict[str, JsonValue] = {
            "kind": "leader-election-decision",
            "scope": None,
            "schema_version": 1,
            "schema": "peerhub.leader-election-decision.v1",
            "status": "DECIDED",
            "election_id": election_id,
            "command_id": command_id,
            "correlation_id": correlation_id,
            "requested_by": requested_by,
            "requested_at": ranking.evaluated_at,
            **basis,
            "decision_hash": decision_hash,
        }
        return self._create(
            target_id=f"leader-election-decision:{election_id}",
            actor_id=requested_by,
            operation="leader-election.decision.create",
            state=state,
        )

    def create_outcome(
        self,
        *,
        election_id: str,
        decision: TargetState,
        requested_by: str,
        outcome: Literal[
            "CLAIMED",
            "REJECTED",
            "ROUTE_EXHAUSTED",
            "CLAIM_OUTCOME_UNKNOWN",
        ],
        selected_node_id: str | None,
        leadership_target_id: str | None = None,
        leadership_revision: int | None = None,
        leadership_claim_id: str | None = None,
        leadership_claim_disposition: LeadershipClaimDisposition | None = None,
        error_code: str | None = None,
        error_class: str | None = None,
        execution_certainty: ExecutionCertainty | None = None,
    ) -> TargetState:
        decision_hash = decision.state.get("decision_hash")
        if not isinstance(decision_hash, str):
            raise RuntimeError("election decision has malformed decision_hash")
        state: dict[str, JsonValue] = {
            "kind": "leader-election-outcome",
            "scope": None,
            "schema_version": 1,
            "election_id": election_id,
            "decision_target_id": decision.target_id,
            "decision_revision": decision.revision,
            "decision_hash": decision_hash,
            "outcome": outcome,
            "selected_node_id": selected_node_id,
            "leadership_target_id": leadership_target_id,
            "leadership_revision": leadership_revision,
            "leadership_claim_id": leadership_claim_id,
            "leadership_claim_disposition": (
                None
                if leadership_claim_disposition is None
                else leadership_claim_disposition.value
            ),
            "error_code": error_code,
            "error_class": error_class,
            "execution_certainty": (
                None
                if execution_certainty is None
                else execution_certainty.value
            ),
            "completed_at": self._clock.now(),
        }
        return self._create(
            target_id=f"leader-election-outcome:{election_id}",
            actor_id=requested_by,
            operation="leader-election.outcome.create",
            state=state,
        )
