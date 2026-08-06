"""SQLite WAL health/telemetry domain repository."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from peerhub.core.errors import RecoveryProbeGrantConflictError
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import AttemptTerminalObserved, OperationalFailureCategory
from peerhub.health.contract import (
    AdmissionDecision,
    AdmissionSnapshot,
    AdmissionSnapshotEntry,
    AdmissionState,
    AvailabilityState,
    CircuitState,
    HealthCircuitSnapshot,
    HealthPolicy,
    HealthProjectionSnapshot,
    PolicyReceipt,
    PolicyScope,
    ProbeResult,
    QuarantineAuthorityClass,
    ReadinessEvaluation,
    ReadinessGateState,
    ReadinessState,
    RecoveryProbeGrant,
    RecoveryProbeReceipt,
    RevalidationAction,
)
from peerhub.telemetry.contract import (
    OperationalObservation,
    OperationalProjectionSnapshot,
    ReadinessMeasurement,
    ReadinessObserved,
    UsageMeasurement,
)
from .sqlite_helpers import (
    _json_object,
    _json_text,
    _json_value,
    _string_tuple,
)


def _readiness_evaluation_data(
    evaluation: ReadinessEvaluation,
) -> dict[str, Any]:
    return {
        "readiness_state": evaluation.readiness_state.value,
        "availability_state": evaluation.availability_state.value,
        "gate_state": evaluation.gate_state.value,
        "admission_decision": (
            evaluation.admission_decision.value
        ),
        "provider_effect_permitted": (
            evaluation.provider_effect_permitted
        ),
        "reason_code": evaluation.reason_code,
        "revalidation_action": (
            evaluation.revalidation_action.value
            if evaluation.revalidation_action is not None
            else None
        ),
        "zero_dispatch_calls": evaluation.zero_dispatch_calls,
    }


def _readiness_evaluation_from_raw(
    raw: str | None,
) -> ReadinessEvaluation | None:
    if raw is None:
        return None
    data = _json_object(raw)
    raw_revalidation = data.get("revalidation_action")
    return ReadinessEvaluation(
        readiness_state=ReadinessState(
            data["readiness_state"]
        ),
        availability_state=AvailabilityState(
            data["availability_state"]
        ),
        gate_state=ReadinessGateState(
            data["gate_state"]
        ),
        admission_decision=AdmissionDecision(
            data["admission_decision"]
        ),
        provider_effect_permitted=data[
            "provider_effect_permitted"
        ],
        reason_code=data.get("reason_code"),
        revalidation_action=(
            RevalidationAction(raw_revalidation)
            if raw_revalidation is not None
            else None
        ),
        zero_dispatch_calls=data["zero_dispatch_calls"],
    )


class SqliteHealthRepository:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection]) -> None:
        self._db = db_factory

    # ── Slice 4: health policy revisions ──

    def _health_policy_from_row(self, row: sqlite3.Row) -> HealthPolicy:
        backoff = tuple(_json_value(row["recovery_backoff_seconds_json"]))
        return HealthPolicy(
            policy_id=row["policy_id"],
            revision=row["revision"],
            readiness_freshness_seconds=row["readiness_freshness_seconds"],
            recovery_backoff_seconds=backoff,
            recovery_jitter_fraction=row["recovery_jitter_fraction"],
            readiness_observation_threshold=row["readiness_observation_threshold"],
            administrative_recovery_probe_limit=row["administrative_recovery_probe_limit"],
        )

    def add_health_policy_revision(
        self,
        policy: HealthPolicy,
        created_at: int = 0,
    ) -> None:
        """Insert a frozen health policy revision."""
        self._db().execute(
            """
            INSERT INTO health_policy_revisions (
                policy_id,
                revision,
                readiness_freshness_seconds,
                recovery_backoff_seconds_json,
                recovery_jitter_fraction,
                readiness_observation_threshold,
                administrative_recovery_probe_limit,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.policy_id,
                policy.revision,
                policy.readiness_freshness_seconds,
                _json_text(list(policy.recovery_backoff_seconds)),
                policy.recovery_jitter_fraction,
                policy.readiness_observation_threshold,
                policy.administrative_recovery_probe_limit,
                created_at,
            ),
        )

    def get_health_policy_revision(
        self,
        policy_id: str,
        revision: int,
    ) -> HealthPolicy | None:
        """Return a health policy revision by ID and revision number."""
        row = self._db().execute(
            """
            SELECT *
            FROM health_policy_revisions
            WHERE policy_id = ? AND revision = ?
            """,
            (policy_id, revision),
        ).fetchone()
        return None if row is None else self._health_policy_from_row(row)

    # ── Slice 4: readiness observations ──

    def _readiness_observation_from_row(
        self,
        row: sqlite3.Row,
    ) -> ReadinessObserved:
        measurement = None
        if row["runtime_revision"] is not None:
            measurement = ReadinessMeasurement(
                runtime_revision=row["runtime_revision"],
                issued_at=row["issued_at"],
                valid_until=row["valid_until"],
                integrity_verified=bool(row["integrity_verified"]),
            )
        evidence = EvidenceValue(
            state=EvidenceState(row["evidence_state"]),
            source_tag=row["source_tag"],
            provider_id=row["provider_id"],
            provider_version=row["provider_version"],
            observed_at=row["observed_at"],
            captured_at=row["captured_at"],
            freshness_ttl=row["freshness_ttl"],
            evidence_ref=EvidenceRef(row["evidence_ref"]),
            value=measurement,
        )
        return ReadinessObserved(
            observation_id=row["observation_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            evidence=evidence,
        )

    def add_readiness_observation(
        self,
        observed: ReadinessObserved,
    ) -> None:
        """Insert an immutable readiness observation."""
        ev = observed.evidence
        val = ev.value
        self._db().execute(
            """
            INSERT INTO readiness_observations (
                observation_id,
                instance_id,
                profile_id,
                evidence_state,
                source_tag,
                provider_id,
                provider_version,
                observed_at,
                captured_at,
                freshness_ttl,
                evidence_ref,
                runtime_revision,
                issued_at,
                valid_until,
                integrity_verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed.observation_id,
                observed.instance_id,
                observed.profile_id,
                ev.state.value,
                ev.source_tag,
                ev.provider_id,
                ev.provider_version,
                ev.observed_at,
                ev.captured_at,
                ev.freshness_ttl,
                str(ev.evidence_ref),
                val.runtime_revision if val is not None else None,
                val.issued_at if val is not None else None,
                val.valid_until if val is not None else None,
                int(val.integrity_verified) if val is not None else None,
            ),
        )

    def get_readiness_observation(
        self,
        observation_id: str,
    ) -> ReadinessObserved | None:
        """Return a readiness observation by observation ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM readiness_observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        ).fetchone()
        return None if row is None else self._readiness_observation_from_row(row)

    # ── Slice 4: operational observations ──

    def _operational_observation_from_row(
        self,
        row: sqlite3.Row,
    ) -> OperationalObservation:
        cat = (
            OperationalFailureCategory(row["operational_failure_category"])
            if row["operational_failure_category"] is not None
            else None
        )
        terminal_event = AttemptTerminalObserved(
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            transport=row["transport"],
            operational_failure_category=cat,
            execution_certainty=ExecutionCertainty(row["execution_certainty"]),
            process_integrity=bool(row["process_integrity"]),
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            latency=row["latency"],
            evidence_refs=_string_tuple(row["evidence_refs_json"]),
        )
        return OperationalObservation(
            observation_id=row["observation_id"],
            source_event_id=row["source_event_id"],
            outbox_position=row["outbox_position"],
            terminal_event=terminal_event,
        )

    def add_operational_observation(
        self,
        observation: OperationalObservation,
    ) -> None:
        """Insert an immutable operational observation."""
        te = observation.terminal_event
        self._db().execute(
            """
            INSERT INTO operational_observations (
                observation_id,
                source_event_id,
                outbox_position,
                instance_id,
                profile_id,
                transport,
                operational_failure_category,
                execution_certainty,
                process_integrity,
                started_at,
                terminal_at,
                latency,
                evidence_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                observation.source_event_id,
                observation.outbox_position,
                te.instance_id,
                te.profile_id,
                te.transport,
                (
                    te.operational_failure_category.value
                    if te.operational_failure_category is not None
                    else None
                ),
                te.execution_certainty.value,
                int(te.process_integrity),
                te.started_at,
                te.terminal_at,
                te.latency,
                _json_text(list(te.evidence_refs)),
            ),
        )

    def get_operational_observation(
        self,
        observation_id: str,
    ) -> OperationalObservation | None:
        """Return an operational observation by observation ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM operational_observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        ).fetchone()
        return None if row is None else self._operational_observation_from_row(row)

    # ── Slice 4: operational projections ──
    #
    # Each EvidenceValue-typed field (failure_category, process_integrity,
    # latency) and the usage field are stored as one complete serialized
    # EvidenceValue JSON blob (state, source_tag, provider_id,
    # provider_version, observed_at, captured_at, freshness_ttl,
    # evidence_ref, value) -- not just state+value -- so the real per-field
    # evidence provenance round-trips instead of being fabricated on read.

    @staticmethod
    def _evidence_value_to_dict(
        ev: EvidenceValue[Any],
        value_encoder,
    ) -> dict[str, Any]:
        return {
            "state": ev.state.value,
            "source_tag": ev.source_tag,
            "provider_id": ev.provider_id,
            "provider_version": ev.provider_version,
            "observed_at": ev.observed_at,
            "captured_at": ev.captured_at,
            "freshness_ttl": ev.freshness_ttl,
            "evidence_ref": str(ev.evidence_ref),
            "value": (
                None if ev.value is None else value_encoder(ev.value)
            ),
        }

    @staticmethod
    def _evidence_value_from_dict(
        data: dict[str, Any],
        value_decoder,
    ) -> EvidenceValue[Any]:
        raw_value = data["value"]
        return EvidenceValue(
            state=EvidenceState(data["state"]),
            source_tag=data["source_tag"],
            provider_id=data["provider_id"],
            provider_version=data["provider_version"],
            observed_at=data["observed_at"],
            captured_at=data["captured_at"],
            freshness_ttl=data["freshness_ttl"],
            evidence_ref=EvidenceRef(data["evidence_ref"]),
            value=(
                None if raw_value is None else value_decoder(raw_value)
            ),
        )

    def _operational_projection_from_row(
        self,
        row: sqlite3.Row,
    ) -> OperationalProjectionSnapshot:
        refs = tuple(
            EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"])
        )
        failure_category = self._evidence_value_from_dict(
            _json_value(row["failure_category_json"]),
            lambda raw: OperationalFailureCategory(raw),
        )
        process_integrity = self._evidence_value_from_dict(
            _json_value(row["process_integrity_json"]),
            lambda raw: bool(raw),
        )
        latency = self._evidence_value_from_dict(
            _json_value(row["latency_json"]),
            lambda raw: int(raw),
        )
        usage = self._evidence_value_from_dict(
            _json_value(row["usage_json"]),
            lambda raw: UsageMeasurement(
                quota_pool_scope=raw["quota_pool_scope"],
                used_fraction=float(raw["used_fraction"]),
                remaining_fraction=float(raw["remaining_fraction"]),
                window_started_at=int(raw["window_started_at"]),
                resets_at=int(raw["resets_at"]),
            ),
        )
        return OperationalProjectionSnapshot(
            projection_id=row["projection_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            failure_category=failure_category,
            process_integrity=process_integrity,
            latency=latency,
            usage=usage,
            failure_streak=row["failure_streak"],
            last_terminal_at=row["last_terminal_at"],
            evidence_refs=refs,
            revision=row["revision"],
            updated_at=row["updated_at"],
        )

    def add_operational_projection(
        self,
        projection: OperationalProjectionSnapshot,
    ) -> None:
        """Insert a revision-one operational projection snapshot."""
        self._db().execute(
            """
            INSERT INTO operational_projections (
                projection_id,
                instance_id,
                profile_id,
                failure_category_json,
                process_integrity_json,
                latency_json,
                usage_json,
                failure_streak,
                last_terminal_at,
                evidence_refs_json,
                revision,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection.projection_id,
                projection.instance_id,
                projection.profile_id,
                _json_text(
                    self._evidence_value_to_dict(
                        projection.failure_category,
                        lambda v: v.value,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.process_integrity,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.latency,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.usage,
                        lambda v: {
                            "quota_pool_scope": v.quota_pool_scope,
                            "used_fraction": v.used_fraction,
                            "remaining_fraction": v.remaining_fraction,
                            "window_started_at": v.window_started_at,
                            "resets_at": v.resets_at,
                        },
                    )
                ),
                projection.failure_streak,
                projection.last_terminal_at,
                _json_text(list(str(r) for r in projection.evidence_refs)),
                projection.revision,
                projection.updated_at,
            ),
        )

    def get_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        """Return an operational projection by instance ID and profile ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM operational_projections
            WHERE instance_id = ? AND profile_id = ?
            """,
            (instance_id, profile_id),
        ).fetchone()
        return None if row is None else self._operational_projection_from_row(row)

    def cas_update_operational_projection(
        self,
        current: OperationalProjectionSnapshot,
        updated: OperationalProjectionSnapshot,
    ) -> bool:
        """CAS-update an operational projection snapshot."""
        if current.projection_id != updated.projection_id:
            raise ValueError("projection IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE operational_projections
            SET
                failure_category_json = ?,
                process_integrity_json = ?,
                latency_json = ?,
                usage_json = ?,
                failure_streak = ?,
                last_terminal_at = ?,
                evidence_refs_json = ?,
                revision = ?,
                updated_at = ?
            WHERE
                projection_id = ?
                AND revision = ?
            """,
            (
                _json_text(
                    self._evidence_value_to_dict(
                        updated.failure_category,
                        lambda v: v.value,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.process_integrity,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.latency,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.usage,
                        lambda v: {
                            "quota_pool_scope": v.quota_pool_scope,
                            "used_fraction": v.used_fraction,
                            "remaining_fraction": v.remaining_fraction,
                            "window_started_at": v.window_started_at,
                            "resets_at": v.resets_at,
                        },
                    )
                ),
                updated.failure_streak,
                updated.last_terminal_at,
                _json_text(list(str(r) for r in updated.evidence_refs)),
                updated.revision,
                updated.updated_at,
                current.projection_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: health projections ──

    def _health_projection_from_row(
        self,
        row: sqlite3.Row,
    ) -> HealthProjectionSnapshot:
        refs = tuple(EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"]))
        return HealthProjectionSnapshot(
            projection_id=row["projection_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            availability_state=AvailabilityState(row["availability_state"]),
            admission_state=AdmissionState(row["admission_state"]),
            readiness_observation_id=row["readiness_observation_id"],
            operational_projection_id=row["operational_projection_id"],
            operational_projection_revision=row["operational_projection_revision"],
            policy_id=row["policy_id"],
            policy_revision=row["policy_revision"],
            cooldown_until=row["cooldown_until"],
            evidence_refs=refs,
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            readiness_evaluation=(
                _readiness_evaluation_from_raw(
                    row["readiness_evaluation_json"]
                )
            ),
            sealed_runtime_revision=(
                row["sealed_runtime_revision"]
            ),
            adapter_declares_probe_safe=(
                None
                if row["adapter_declares_probe_safe"] is None
                else bool(row["adapter_declares_probe_safe"])
            ),
        )

    def add_health_projection(
        self,
        projection: HealthProjectionSnapshot,
    ) -> None:
        """Insert a revision-one health projection snapshot."""
        self._db().execute(
            """
            INSERT INTO health_projections (
                projection_id,
                instance_id,
                profile_id,
                availability_state,
                admission_state,
                readiness_observation_id,
                operational_projection_id,
                operational_projection_revision,
                policy_id,
                policy_revision,
                cooldown_until,
                evidence_refs_json,
                revision,
                created_at,
                updated_at,
                readiness_evaluation_json,
                sealed_runtime_revision,
                adapter_declares_probe_safe
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                projection.projection_id,
                projection.instance_id,
                projection.profile_id,
                projection.availability_state.value,
                projection.admission_state.value,
                projection.readiness_observation_id,
                projection.operational_projection_id,
                projection.operational_projection_revision,
                projection.policy_id,
                projection.policy_revision,
                projection.cooldown_until,
                _json_text(list(str(r) for r in projection.evidence_refs)),
                projection.revision,
                projection.created_at,
                projection.updated_at,
                (
                    _json_text(
                        _readiness_evaluation_data(
                            projection.readiness_evaluation
                        )
                    )
                    if projection.readiness_evaluation is not None
                    else None
                ),
                projection.sealed_runtime_revision,
                (
                    int(projection.adapter_declares_probe_safe)
                    if projection.adapter_declares_probe_safe
                    is not None
                    else None
                ),
            ),
        )

    def get_health_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> HealthProjectionSnapshot | None:
        """Return a health projection snapshot by instance ID and profile ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM health_projections
            WHERE instance_id = ? AND profile_id = ?
            """,
            (instance_id, profile_id),
        ).fetchone()
        return None if row is None else self._health_projection_from_row(row)

    def cas_update_health_projection(
        self,
        current: HealthProjectionSnapshot,
        updated: HealthProjectionSnapshot,
    ) -> bool:
        """CAS-update a health projection snapshot."""
        if current.projection_id != updated.projection_id:
            raise ValueError("projection IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE health_projections
            SET
                availability_state = ?,
                admission_state = ?,
                readiness_observation_id = ?,
                operational_projection_id = ?,
                operational_projection_revision = ?,
                policy_id = ?,
                policy_revision = ?,
                cooldown_until = ?,
                evidence_refs_json = ?,
                readiness_evaluation_json = ?,
                sealed_runtime_revision = ?,
                adapter_declares_probe_safe = ?,
                revision = ?,
                updated_at = ?
            WHERE
                projection_id = ?
                AND revision = ?
            """,
            (
                updated.availability_state.value,
                updated.admission_state.value,
                updated.readiness_observation_id,
                updated.operational_projection_id,
                updated.operational_projection_revision,
                updated.policy_id,
                updated.policy_revision,
                updated.cooldown_until,
                _json_text(list(str(r) for r in updated.evidence_refs)),
                (
                    _json_text(
                        _readiness_evaluation_data(
                            updated.readiness_evaluation
                        )
                    )
                    if updated.readiness_evaluation is not None
                    else None
                ),
                updated.sealed_runtime_revision,
                (
                    int(updated.adapter_declares_probe_safe)
                    if updated.adapter_declares_probe_safe
                    is not None
                    else None
                ),
                updated.revision,
                updated.updated_at,
                current.projection_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: health circuits ──

    def _health_circuit_from_row(
        self,
        row: sqlite3.Row,
    ) -> HealthCircuitSnapshot:
        receipt = None
        if row["receipt_incident"] is not None:
            receipt = PolicyReceipt(
                incident=row["receipt_incident"],
                gate_generation=row["receipt_gate_generation"],
                timestamp=row["receipt_timestamp"],
                fingerprint=row["receipt_fingerprint"],
            )
        return HealthCircuitSnapshot(
            circuit_id=row["circuit_id"],
            scope=PolicyScope(row["scope"]),
            subject=row["subject"],
            state=CircuitState(row["state"]),
            quarantine_authority_class=QuarantineAuthorityClass(
                row["quarantine_authority_class"]
            ),
            receipt=receipt,
            backoff_count=row["backoff_count"],
            cooldown_until=row["cooldown_until"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add_health_circuit(
        self,
        circuit: HealthCircuitSnapshot,
    ) -> None:
        """Insert a revision-one health circuit snapshot."""
        rcpt = circuit.receipt
        self._db().execute(
            """
            INSERT INTO health_circuits (
                circuit_id,
                scope,
                subject,
                state,
                quarantine_authority_class,
                receipt_incident,
                receipt_gate_generation,
                receipt_timestamp,
                receipt_fingerprint,
                backoff_count,
                cooldown_until,
                revision,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                circuit.circuit_id,
                circuit.scope.value,
                circuit.subject,
                circuit.state.value,
                circuit.quarantine_authority_class.value,
                rcpt.incident if rcpt is not None else None,
                rcpt.gate_generation if rcpt is not None else None,
                rcpt.timestamp if rcpt is not None else None,
                rcpt.fingerprint if rcpt is not None else None,
                circuit.backoff_count,
                circuit.cooldown_until,
                circuit.revision,
                circuit.created_at,
                circuit.updated_at,
            ),
        )

    def get_health_circuit(
        self,
        scope: PolicyScope | str,
        subject: str,
    ) -> HealthCircuitSnapshot | None:
        """Return a health circuit snapshot by scope and subject."""
        scope_str = scope.value if isinstance(scope, PolicyScope) else str(scope)
        row = self._db().execute(
            """
            SELECT *
            FROM health_circuits
            WHERE scope = ? AND subject = ?
            """,
            (scope_str, subject),
        ).fetchone()
        return None if row is None else self._health_circuit_from_row(row)

    def cas_update_health_circuit(
        self,
        current: HealthCircuitSnapshot,
        updated: HealthCircuitSnapshot,
    ) -> bool:
        """CAS-update a health circuit snapshot."""
        if current.circuit_id != updated.circuit_id:
            raise ValueError("circuit IDs do not match")
        rcpt = updated.receipt
        cursor = self._db().execute(
            """
            UPDATE health_circuits
            SET
                state = ?,
                quarantine_authority_class = ?,
                receipt_incident = ?,
                receipt_gate_generation = ?,
                receipt_timestamp = ?,
                receipt_fingerprint = ?,
                backoff_count = ?,
                cooldown_until = ?,
                revision = ?,
                updated_at = ?
            WHERE
                circuit_id = ?
                AND revision = ?
            """,
            (
                updated.state.value,
                updated.quarantine_authority_class.value,
                rcpt.incident if rcpt is not None else None,
                rcpt.gate_generation if rcpt is not None else None,
                rcpt.timestamp if rcpt is not None else None,
                rcpt.fingerprint if rcpt is not None else None,
                updated.backoff_count,
                updated.cooldown_until,
                updated.revision,
                updated.updated_at,
                current.circuit_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: recovery probe grants ──

    def _recovery_probe_grant_from_row(
        self,
        row: sqlite3.Row,
    ) -> RecoveryProbeGrant:
        receipt = PolicyReceipt(
            incident=row["receipt_incident"],
            gate_generation=row["receipt_gate_generation"],
            timestamp=row["receipt_timestamp"],
            fingerprint=row["receipt_fingerprint"],
        )
        return RecoveryProbeGrant(
            grant_id=row["grant_id"],
            circuit_id=row["circuit_id"],
            receipt=receipt,
            authorized_by=row["authorized_by"],
            authorized_at=row["authorized_at"],
            remaining_probes=row["remaining_probes"],
            consumed_at=row["consumed_at"],
            consumed_by_attempt_id=row["consumed_by_attempt_id"],
            revision=row["revision"],
        )

    def add_recovery_probe_grant(
        self,
        grant: RecoveryProbeGrant,
    ) -> None:
        """Insert an unconsumed recovery probe grant."""
        existing = self.get_live_recovery_probe_grant(
            grant.circuit_id
        )
        if existing is not None:
            raise RecoveryProbeGrantConflictError(
                grant.circuit_id,
                existing.grant_id,
            )

        rcpt = grant.receipt
        try:
            self._db().execute(
                """
                INSERT INTO recovery_probe_grants (
                    grant_id,
                    circuit_id,
                    receipt_incident,
                    receipt_gate_generation,
                    receipt_timestamp,
                    receipt_fingerprint,
                    authorized_by,
                    authorized_at,
                    remaining_probes,
                    consumed_at,
                    consumed_by_attempt_id,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.circuit_id,
                    rcpt.incident,
                    rcpt.gate_generation,
                    rcpt.timestamp,
                    rcpt.fingerprint,
                    grant.authorized_by,
                    grant.authorized_at,
                    grant.remaining_probes,
                    grant.consumed_at,
                    grant.consumed_by_attempt_id,
                    grant.revision,
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self.get_live_recovery_probe_grant(
                grant.circuit_id
            )
            if existing is not None:
                raise RecoveryProbeGrantConflictError(
                    grant.circuit_id,
                    existing.grant_id,
                ) from error
            raise

    def get_recovery_probe_grant(
        self,
        grant_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return a recovery probe grant by grant ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_grants
            WHERE grant_id = ?
            """,
            (grant_id,),
        ).fetchone()
        return None if row is None else self._recovery_probe_grant_from_row(row)

    def get_live_recovery_probe_grant(
        self,
        circuit_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return the sole unconsumed grant for a circuit."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_grants
            WHERE circuit_id = ?
              AND consumed_at IS NULL
            """,
            (circuit_id,),
        ).fetchone()
        return (
            None
            if row is None
            else self._recovery_probe_grant_from_row(row)
        )

    def cas_claim_recovery_probe_grant(
        self,
        current: RecoveryProbeGrant,
        updated: RecoveryProbeGrant,
    ) -> bool:
        """Attempt a contention-safe single-use claim on an unconsumed probe grant."""
        if current.grant_id != updated.grant_id:
            raise ValueError("grant IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE recovery_probe_grants
            SET
                remaining_probes = ?,
                consumed_at = ?,
                consumed_by_attempt_id = ?,
                revision = ?
            WHERE
                grant_id = ?
                AND revision = ?
                AND consumed_at IS NULL
            """,
            (
                updated.remaining_probes,
                updated.consumed_at,
                updated.consumed_by_attempt_id,
                updated.revision,
                current.grant_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: recovery probe receipts (immutable) ──

    def _recovery_probe_receipt_from_row(
        self,
        row: sqlite3.Row,
    ) -> RecoveryProbeReceipt:
        reported_receipt = PolicyReceipt(
            incident=row["reported_receipt_incident"],
            gate_generation=row["reported_receipt_gate_generation"],
            timestamp=row["reported_receipt_timestamp"],
            fingerprint=row["reported_receipt_fingerprint"],
        )
        refs = tuple(EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"]))
        return RecoveryProbeReceipt(
            probe_receipt_id=row["probe_receipt_id"],
            grant_id=row["grant_id"],
            attempt_id=row["attempt_id"],
            reported_revision=row["reported_revision"],
            reported_receipt=reported_receipt,
            result=ProbeResult(row["result"]),
            observed_at=row["observed_at"],
            evidence_refs=refs,
        )

    def add_recovery_probe_receipt(
        self,
        receipt: RecoveryProbeReceipt,
    ) -> None:
        """Insert an immutable recovery probe receipt."""
        rr = receipt.reported_receipt
        self._db().execute(
            """
            INSERT INTO recovery_probe_receipts (
                probe_receipt_id,
                grant_id,
                attempt_id,
                reported_revision,
                reported_receipt_incident,
                reported_receipt_gate_generation,
                reported_receipt_timestamp,
                reported_receipt_fingerprint,
                result,
                observed_at,
                evidence_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.probe_receipt_id,
                receipt.grant_id,
                receipt.attempt_id,
                receipt.reported_revision,
                rr.incident,
                rr.gate_generation,
                rr.timestamp,
                rr.fingerprint,
                receipt.result.value,
                receipt.observed_at,
                _json_text(list(str(r) for r in receipt.evidence_refs)),
            ),
        )

    def get_recovery_probe_receipt(
        self,
        probe_receipt_id: str,
    ) -> RecoveryProbeReceipt | None:
        """Return a recovery probe receipt by probe receipt ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_receipts
            WHERE probe_receipt_id = ?
            """,
            (probe_receipt_id,),
        ).fetchone()
        return None if row is None else self._recovery_probe_receipt_from_row(row)

    def list_recovery_probe_receipts(
        self,
        grant_id: str,
    ) -> tuple[RecoveryProbeReceipt, ...]:
        """Return all recovery probe receipts for a grant ID ordered by observation time."""
        rows = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_receipts
            WHERE grant_id = ?
            ORDER BY observed_at, probe_receipt_id
            """,
            (grant_id,),
        ).fetchall()
        return tuple(self._recovery_probe_receipt_from_row(row) for row in rows)

    # ── Slice 4: admission snapshots (immutable audit) ──

    def add_admission_snapshot(
        self,
        snapshot: AdmissionSnapshot,
    ) -> None:
        """Insert an immutable admission snapshot and all its entries."""
        db = self._db()
        db.execute(
            """
            INSERT INTO admission_snapshots (
                snapshot_id,
                revision,
                digest,
                configuration_revision,
                policy_id,
                policy_revision,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.revision,
                snapshot.digest,
                snapshot.configuration_revision,
                snapshot.policy_id,
                snapshot.policy_revision,
                snapshot.created_at,
            ),
        )
        for entry in snapshot.entries:
            db.execute(
                """
                INSERT INTO admission_snapshot_entries (
                    snapshot_id,
                    instance_id,
                    profile_id,
                    health_projection_id,
                    health_projection_revision,
                    availability_state,
                    admission_state,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    entry.instance_id,
                    entry.profile_id,
                    entry.health_projection_id,
                    entry.health_projection_revision,
                    entry.availability_state.value,
                    entry.admission_state.value,
                    _json_text(list(str(r) for r in entry.evidence_refs)),
                ),
            )

    def get_admission_snapshot(
        self,
        snapshot_id: str,
    ) -> AdmissionSnapshot | None:
        """Return a full admission snapshot including all its entries."""
        db = self._db()
        row = db.execute(
            """
            SELECT *
            FROM admission_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None

        entry_rows = db.execute(
            """
            SELECT *
            FROM admission_snapshot_entries
            WHERE snapshot_id = ?
            ORDER BY instance_id, profile_id
            """,
            (snapshot_id,),
        ).fetchall()

        entries = tuple(
            AdmissionSnapshotEntry(
                instance_id=erow["instance_id"],
                profile_id=erow["profile_id"],
                health_projection_id=erow["health_projection_id"],
                health_projection_revision=erow["health_projection_revision"],
                availability_state=AvailabilityState(erow["availability_state"]),
                admission_state=AdmissionState(erow["admission_state"]),
                evidence_refs=tuple(
                    EvidenceRef(r) for r in _string_tuple(erow["evidence_refs_json"])
                ),
            )
            for erow in entry_rows
        )

        return AdmissionSnapshot(
            snapshot_id=row["snapshot_id"],
            revision=row["revision"],
            digest=row["digest"],
            configuration_revision=row["configuration_revision"],
            policy_id=row["policy_id"],
            policy_revision=row["policy_revision"],
            entries=entries,
            created_at=row["created_at"],
        )
