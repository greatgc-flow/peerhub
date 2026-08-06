"""SQLite WAL telemetry domain repository."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import AttemptTerminalObserved, OperationalFailureCategory
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


class SqliteTelemetryRepository:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection]) -> None:
        self._db = db_factory

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
