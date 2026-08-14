import sqlite3
from typing import Callable

from peerhub.core.evidence import EvidenceRef
from peerhub.dispatch.capability import CapabilityTier
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateDecision,
    RouteDecision,
    RouteEligibility,
    canonical_route_decision_digest,
)

from .sqlite_helpers import _json_text, _string_tuple  # pyright: ignore[reportPrivateUsage]

class SqliteRoutingRepository:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection]) -> None:
        self._db = db_factory

    def add_route_decision(
        self,
        decision: RouteDecision,
    ) -> None:
        """Insert an immutable route decision audit and all candidate decisions."""
        db = self._db()
        db.execute(
            """
            INSERT INTO route_decisions (
                decision_id,
                client_request_id,
                configuration_revision,
                configuration_digest,
                admission_snapshot_id,
                admission_snapshot_revision,
                admission_snapshot_digest,
                routing_policy_id,
                routing_policy_revision,
                required_capability_tier,
                audit_seed,
                selection_index,
                selected_candidate_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.client_request_id,
                decision.configuration.revision,
                decision.configuration.digest,
                decision.admission_snapshot_id,
                decision.admission_snapshot_revision,
                decision.admission_snapshot_digest,
                decision.routing_policy_id,
                decision.routing_policy_revision,
                decision.required_capability_tier.name,
                decision.audit_seed,
                decision.selection_index,
                decision.selected_candidate_id,
                decision.created_at,
            ),
        )
        for candidate in decision.candidates:
            db.execute(
                """
                INSERT INTO route_candidate_decisions (
                    decision_id,
                    candidate_id,
                    instance_id,
                    representative_profile_id,
                    eligibility,
                    effective_weight,
                    exclusion_reason,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    candidate.candidate_id,
                    candidate.instance_id,
                    candidate.representative_profile_id,
                    candidate.eligibility.value,
                    candidate.effective_weight,
                    candidate.exclusion_reason,
                    _json_text(list(str(r) for r in candidate.evidence_refs)),
                ),
            )

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return a full route decision audit including all candidate decisions."""
        db = self._db()
        row = db.execute(
            """
            SELECT *
            FROM route_decisions
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if row is None:
            return None

        candidate_rows = db.execute(
            """
            SELECT *
            FROM route_candidate_decisions
            WHERE decision_id = ?
            ORDER BY candidate_id
            """,
            (decision_id,),
        ).fetchall()

        candidates = tuple(
            RouteCandidateDecision(
                candidate_id=crow["candidate_id"],
                instance_id=crow["instance_id"],
                representative_profile_id=crow["representative_profile_id"],
                eligibility=RouteEligibility(crow["eligibility"]),
                effective_weight=crow["effective_weight"],
                exclusion_reason=crow["exclusion_reason"],
                evidence_refs=tuple(
                    EvidenceRef(r) for r in _string_tuple(crow["evidence_refs_json"])
                ),
            )
            for crow in candidate_rows
        )

        config = ConfigurationSnapshot(
            revision=row["configuration_revision"],
            digest=row["configuration_digest"],
        )

        return RouteDecision(
            decision_id=row["decision_id"],
            client_request_id=row["client_request_id"],
            configuration=config,
            admission_snapshot_id=row["admission_snapshot_id"],
            admission_snapshot_revision=row["admission_snapshot_revision"],
            admission_snapshot_digest=row["admission_snapshot_digest"],
            routing_policy_id=row["routing_policy_id"],
            routing_policy_revision=row["routing_policy_revision"],
            required_capability_tier=(
                _required_capability_tier_from_stored(
                    row["required_capability_tier"]
                )
            ),
            candidates=candidates,
            audit_seed=row["audit_seed"],
            selection_index=row["selection_index"],
            selected_candidate_id=row["selected_candidate_id"],
            created_at=row["created_at"],
        )

    def get_route_decision_by_binding(
        self,
        client_request_id: str,
        route_decision_digest: str,
    ) -> RouteDecision | None:
        """Return exactly one digest-matching immutable decision by binding."""
        rows = self._db().execute(
            """
            SELECT decision_id
            FROM route_decisions
            WHERE client_request_id = ?
            """,
            (client_request_id,),
        ).fetchall()

        matches: list[RouteDecision] = []
        for row in rows:
            decision = self.get_route_decision(row["decision_id"])
            if (
                decision is not None
                and canonical_route_decision_digest(decision)
                == route_decision_digest
            ):
                matches.append(decision)

        return matches[0] if len(matches) == 1 else None


def _required_capability_tier_from_stored(
    raw: object,
) -> CapabilityTier:
    if not isinstance(raw, str):
        raise RuntimeError(
            "stored route decision is missing required_capability_tier"
        )
    try:
        return CapabilityTier[raw]
    except KeyError as exc:
        raise RuntimeError(
            "stored route decision required_capability_tier is invalid"
        ) from exc
