"""Application composition for capability discovery and leader election."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace

from peerhub.application.capability_config import CapabilityConfigService
from peerhub.application.leadership import LeadershipService
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock, IdSource
from peerhub.core.evidence import EvidenceState
from peerhub.core.errors import PeerHubError, RouteExhaustedError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    ErrorCode,
    JsonValue,
    canonical_json_bytes,
    require_text,
)
from peerhub.governance.contract import TargetState
from peerhub.governance.election_audit import (
    ElectionAuditService,
    LeadershipElectionReceipt,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.capability_matching import (
    CapabilityCandidateFacts,
    CapabilityEvidenceProvenance,
    CapabilityMatch,
    CapabilityRankingResult,
    CapabilityScoreComponent,
    QuotaRankingFact,
    ScoreComponentName,
    rank_capability_candidates,
    resolve_default_proposer,
)


class LeadershipElectionUnknownError(PeerHubError):
    """The claim may have changed leadership, so it must not be replayed."""

    error_code = ErrorCode.INTERNAL_ERROR


def _state_digest(target: TargetState) -> str:
    return hashlib.sha256(canonical_json_bytes(target.state)).hexdigest()


def _required_text(target: TargetState, field: str) -> str:
    value = target.state.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{target.target_id} has malformed {field}")
    return value


def _history(target: TargetState | None) -> tuple[str, ...]:
    if target is None:
        return ()
    value = target.state.get("coordinator_history")
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("leadership coordinator_history is malformed")
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        node_id = entry.get("peer_node_id")
        if isinstance(node_id, str) and node_id:
            result.append(node_id)
    return tuple(result)


def _current_leader(target: TargetState | None) -> str | None:
    if target is None or target.state.get("status") == "VACANT":
        return None
    leader = target.state.get("leader")
    if not isinstance(leader, Mapping):
        return None
    node_id = leader.get("peer_node_id")
    return node_id if isinstance(node_id, str) and node_id else None


def _leadership_provenance(
    target: TargetState | None,
) -> CapabilityEvidenceProvenance:
    return CapabilityEvidenceProvenance(
        fact="leadership-snapshot",
        evidence_state=(
            EvidenceState.ABSENT if target is None else EvidenceState.MEASURED
        ),
        source_kind="governance-target",
        source_id="workspace-leadership",
        source_revision=None if target is None else target.revision,
        source_digest=None if target is None else _state_digest(target),
        evidence_refs=(),
        observed_at=None if target is None else target.updated_at,
    )


def _enrich_leadership(
    match: CapabilityMatch,
    provenance: CapabilityEvidenceProvenance,
) -> CapabilityMatch:
    index = len(match.provenance)
    components = tuple(
        replace(component, provenance_indexes=(index,))
        if component.name
        in {ScoreComponentName.CONTINUITY, ScoreComponentName.RECENT_USE}
        else component
        for component in match.components
    )
    return replace(
        match,
        components=components,
        provenance=(*match.provenance, provenance),
    )


class CapabilityMatchingCoordinator:
    """Gather authoritative facts, invoke the reducer, and audit elections."""

    def __init__(
        self,
        *,
        peer_registry: PeerRegistryService,
        capability_config: CapabilityConfigService,
        health: HealthService,
        leadership: LeadershipService,
        usage_store: SqliteStateStore,
        election_audit: ElectionAuditService,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._peer_registry = peer_registry
        self._capability_config = capability_config
        self._health = health
        self._leadership = leadership
        self._usage_store = usage_store
        self._election_audit = election_audit
        self._clock = clock
        self._ids = ids

    def _quota_facts(
        self,
        projections: Sequence[object],
        *,
        evaluated_at: int,
    ) -> tuple[QuotaRankingFact, ...]:
        facts: list[QuotaRankingFact] = []
        for projection in projections:
            projection_id = getattr(projection, "projection_id")
            instance_id = getattr(projection, "instance_id")
            profile_id = getattr(projection, "profile_id")
            quota_pool_scope = getattr(projection, "quota_pool_scope")
            remaining_fraction = getattr(projection, "remaining_fraction")
            resets_at = getattr(projection, "resets_at")
            revision = getattr(projection, "revision")
            updated_at = getattr(projection, "updated_at")
            state = (
                EvidenceState.MEASURED
                if evaluated_at < resets_at
                else EvidenceState.STALE
            )
            provenance = CapabilityEvidenceProvenance(
                fact="quota-margin",
                evidence_state=state,
                source_kind="usage-projection",
                source_id=projection_id,
                source_revision=revision,
                source_digest=None,
                evidence_refs=(),
                observed_at=updated_at,
            )
            facts.append(
                QuotaRankingFact(
                    projection_id=projection_id,
                    instance_id=instance_id,
                    profile_id=profile_id,
                    quota_pool_scope=quota_pool_scope,
                    remaining_fraction=remaining_fraction,
                    resets_at=resets_at,
                    revision=revision,
                    updated_at=updated_at,
                    evidence_state=state,
                    provenance=provenance,
                )
            )
        return tuple(facts)

    def discover(
        self,
        *,
        needs: str,
        effort: str = "mid",
        evaluated_at: int | None = None,
    ) -> CapabilityRankingResult:
        """Read and rank configured candidates without persisting anything."""

        if not isinstance(needs, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("needs must be a string")
        if not isinstance(effort, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("effort must be a string")
        timestamp = self._clock.now() if evaluated_at is None else evaluated_at
        if type(timestamp) is not int or timestamp < 0:
            raise ValueError("evaluated_at must be a nonnegative integer")
        policy = replace(
            self._capability_config.get_policy(), evaluated_at=timestamp
        )
        leadership = self._leadership.get_leadership()
        current_leader = _current_leader(leadership)
        history = _history(leadership)
        leadership_provenance = _leadership_provenance(leadership)
        with self._usage_store.read_unit_of_work() as unit:
            all_quota = tuple(unit.list_usage_projections(None))

        candidates: list[CapabilityCandidateFacts] = []
        for peer_node in self._peer_registry.list_nodes():
            node_id = _required_text(peer_node, "node_id")
            config = self._capability_config.get_config(node_id)
            if config is None:
                continue
            peer_kind = _required_text(peer_node, "peer_kind")
            profile_id = _required_text(peer_node, "profile_id")
            health = self._health.read_health_projection(
                peer_kind,
                profile_id,
                evaluated_at=timestamp,
            )
            if health is None:
                availability = None
                admission = None
                backed_off = None
                health_provenance = CapabilityEvidenceProvenance(
                    fact="health-grade",
                    evidence_state=EvidenceState.ABSENT,
                    source_kind="health-projection",
                    source_id=f"health-projection:{peer_kind}:{profile_id}",
                    source_revision=None,
                    source_digest=None,
                    evidence_refs=(),
                    observed_at=None,
                )
            else:
                availability = health.effective_availability_state
                admission = health.effective_admission_state
                backed_off = health.profile_gate_backed_off
                projection = health.projection
                health_provenance = CapabilityEvidenceProvenance(
                    fact="health-grade",
                    evidence_state=(
                        EvidenceState.STALE
                        if availability.value == "STALE"
                        else EvidenceState.MEASURED
                    ),
                    source_kind="health-projection",
                    source_id=projection.projection_id,
                    source_revision=projection.revision,
                    source_digest=None,
                    evidence_refs=projection.evidence_refs,
                    observed_at=projection.updated_at,
                )
            quota_rows = tuple(
                projection
                for projection in all_quota
                if projection.instance_id in {node_id, peer_kind}
                and projection.profile_id == profile_id
            )
            candidates.append(
                CapabilityCandidateFacts(
                    node_id=node_id,
                    peer_kind=peer_kind,
                    profile_id=profile_id,
                    peer_node_target_id=peer_node.target_id,
                    peer_node_revision=peer_node.revision,
                    enabled=config.enabled,
                    aliases=config.aliases,
                    capabilities=config.capabilities,
                    capability_config_target_id=config.target_id,
                    capability_config_revision=config.revision,
                    availability_status=availability,
                    admission_status=admission,
                    profile_gate_backed_off=backed_off,
                    health_provenance=health_provenance,
                    quota_projections=self._quota_facts(
                        quota_rows, evaluated_at=timestamp
                    ),
                    is_current_leader=node_id == current_leader,
                    recent_leader_node_ids=history,
                )
            )
        ranking = rank_capability_candidates(
            tuple(candidates),
            needs=needs,
            requested_effort=effort,
            policy=policy,
        )
        ranking = replace(
            ranking,
            ordered_matches=tuple(
                _enrich_leadership(match, leadership_provenance)
                for match in ranking.ordered_matches
            ),
            excluded_candidates=tuple(
                _enrich_leadership(match, leadership_provenance)
                for match in ranking.excluded_candidates
            ),
        )
        if not ranking.ordered_matches:
            ranking = replace(
                ranking,
                fallback=resolve_default_proposer(
                    ranking,
                    policy=policy.default_proposer,
                    coordinator_history=history,
                ),
            )
        return ranking

    def elect_leader(
        self,
        *,
        needs: str = "general",
        effort: str = "mid",
        reason: str = "",
        actor_id: str,
    ) -> LeadershipElectionReceipt:
        """Commit a decision, claim exactly once, then commit its outcome."""

        normalized_actor = require_text(actor_id, "actor_id")
        if not isinstance(reason, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("reason must be a string")
        ranking = self.discover(
            needs=needs,
            effort=effort,
            evaluated_at=self._clock.now(),
        )
        if ranking.ordered_matches:
            selected = ranking.ordered_matches[0].node_id
            selection_basis = "RANKED_MATCH"
        else:
            selected = None if ranking.fallback is None else ranking.fallback.node_id
            selection_basis = (
                "NO_ELIGIBLE_DEFAULT"
                if ranking.fallback is None
                else ranking.fallback.basis
            )
        election_id = self._ids.new_id("leader-election")
        command_id = self._ids.new_id("leader-election-command")
        correlation_id = self._ids.new_id("leader-election-correlation")
        decision = self._election_audit.create_decision(
            ranking=ranking,
            election_id=election_id,
            command_id=command_id,
            correlation_id=correlation_id,
            requested_by=normalized_actor,
            reason=reason,
            selected_node_id=selected,
            selection_basis=selection_basis,
        )
        if selected is None:
            outcome = self._election_audit.create_outcome(
                election_id=election_id,
                decision=decision,
                requested_by=normalized_actor,
                outcome="ROUTE_EXHAUSTED",
                selected_node_id=None,
                error_code=ErrorCode.ROUTE_EXHAUSTED.value,
                error_class=RouteExhaustedError.__name__,
                execution_certainty=ExecutionCertainty.NOT_STARTED,
            )
            error = RouteExhaustedError(command_id)
            error.details.update(
                {
                    "election_id": election_id,
                    "decision_target_id": decision.target_id,
                    "decision_revision": decision.revision,
                    "outcome_target_id": outcome.target_id,
                    "outcome_revision": outcome.revision,
                }
            )
            raise error

        try:
            claim = self._leadership.claim_leadership(
                peer_node_id=selected,
                actor_id=normalized_actor,
                reason=reason or f"elected_for:{ranking.needs or 'general'}",
                domain=ranking.needs or "general",
            )
        except PeerHubError as exc:
            outcome = self._election_audit.create_outcome(
                election_id=election_id,
                decision=decision,
                requested_by=normalized_actor,
                outcome="REJECTED",
                selected_node_id=selected,
                error_code=exc.error_code.value,
                error_class=type(exc).__name__,
                execution_certainty=ExecutionCertainty.TERMINAL,
            )
            exc.details.update(
                {
                    "election_id": election_id,
                    "decision_target_id": decision.target_id,
                    "decision_revision": decision.revision,
                    "outcome_target_id": outcome.target_id,
                    "outcome_revision": outcome.revision,
                }
            )
            raise
        except Exception as exc:
            outcome = self._election_audit.create_outcome(
                election_id=election_id,
                decision=decision,
                requested_by=normalized_actor,
                outcome="CLAIM_OUTCOME_UNKNOWN",
                selected_node_id=selected,
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_class=type(exc).__name__,
                execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED,
            )
            raise LeadershipElectionUnknownError(
                "leadership claim outcome is unknown; observe state before retry",
                details={
                    "election_id": election_id,
                    "decision_target_id": decision.target_id,
                    "decision_revision": decision.revision,
                    "outcome_target_id": outcome.target_id,
                    "outcome_revision": outcome.revision,
                    "cause_class": type(exc).__name__,
                },
            ) from exc

        claim_id = claim.target.state.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise RuntimeError("successful leadership claim lacks claim_id")
        outcome = self._election_audit.create_outcome(
            election_id=election_id,
            decision=decision,
            requested_by=normalized_actor,
            outcome="CLAIMED",
            selected_node_id=selected,
            leadership_target_id=claim.target.target_id,
            leadership_revision=claim.target.revision,
            leadership_claim_id=claim_id,
            leadership_claim_disposition=claim.disposition,
            execution_certainty=ExecutionCertainty.TERMINAL,
        )
        decision_hash = decision.state.get("decision_hash")
        if not isinstance(decision_hash, str):
            raise RuntimeError("leader election decision lacks decision_hash")
        return LeadershipElectionReceipt(
            election_id=election_id,
            decision_target_id=decision.target_id,
            decision_revision=decision.revision,
            decision_hash=decision_hash,
            outcome_target_id=outcome.target_id,
            outcome_revision=outcome.revision,
            outcome="CLAIMED",
            selected_node_id=selected,
            selection_basis=selection_basis,
            leadership_target_id=claim.target.target_id,
            leadership_revision=claim.target.revision,
            leadership_claim_id=claim_id,
            leadership_claim_disposition=claim.disposition,
        )


def encode_capability_ranking(
    ranking: CapabilityRankingResult,
) -> Mapping[str, JsonValue]:
    """Encode a discovery result without introducing mutable JSON arrays."""

    def encode_component(component: CapabilityScoreComponent) -> JsonValue:
        return {
            "name": component.name.value,
            "state": component.state.value,
            "raw_value": component.raw_value,
            "points": component.points,
            "reason": component.reason,
            "provenance_indexes": component.provenance_indexes,
        }

    def encode_match(match: CapabilityMatch) -> JsonValue:
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
                encode_component(component) for component in match.components
            ),
            "provenance": tuple(
                {
                    "fact": provenance.fact,
                    "evidence_state": provenance.evidence_state.value,
                    "source_kind": provenance.source_kind,
                    "source_id": provenance.source_id,
                    "source_revision": provenance.source_revision,
                    "source_digest": provenance.source_digest,
                    "evidence_refs": tuple(
                        str(ref) for ref in provenance.evidence_refs
                    ),
                    "observed_at": provenance.observed_at,
                }
                for provenance in match.provenance
            ),
        }

    fallback: JsonValue = None
    if ranking.fallback is not None:
        fallback = {
            "node_id": ranking.fallback.node_id,
            "basis": ranking.fallback.basis,
            "considered_node_ids": ranking.fallback.considered_node_ids,
        }
    return {
        "formula_id": ranking.formula_id,
        "policy_id": ranking.policy_id,
        "policy_revision": ranking.policy_revision,
        "needs": ranking.needs,
        "requested_effort": ranking.requested_effort,
        "ordered_matches": tuple(
            encode_match(match) for match in ranking.ordered_matches
        ),
        "excluded_candidates": tuple(
            encode_match(match) for match in ranking.excluded_candidates
        ),
        "fallback": fallback,
        "evaluated_at": ranking.evaluated_at,
    }


def encode_leadership_election_receipt(
    receipt: LeadershipElectionReceipt,
) -> Mapping[str, JsonValue]:
    return {
        "election_id": receipt.election_id,
        "decision_target_id": receipt.decision_target_id,
        "decision_revision": receipt.decision_revision,
        "decision_hash": receipt.decision_hash,
        "outcome_target_id": receipt.outcome_target_id,
        "outcome_revision": receipt.outcome_revision,
        "outcome": receipt.outcome,
        "selected_node_id": receipt.selected_node_id,
        "selection_basis": receipt.selection_basis,
        "leadership_target_id": receipt.leadership_target_id,
        "leadership_revision": receipt.leadership_revision,
        "leadership_claim_id": receipt.leadership_claim_id,
        "leadership_claim_disposition": (
            receipt.leadership_claim_disposition.value
        ),
    }
