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
    SessionContextProjectionSnapshot,
    UsageMeasurement,
    SessionContextObserved,
)
from peerhub.dispatch.contract import SessionBindingKey
from .sqlite_helpers import (
    _json_text,  # pyright: ignore[reportPrivateUsage]
    _json_value,  # pyright: ignore[reportPrivateUsage]
    _string_tuple,  # pyright: ignore[reportPrivateUsage]
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
        value_encoder,  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
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
        value_decoder,  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
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
            value=(  # pyright: ignore[reportUnknownArgumentType]
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
        failure_category = self._evidence_value_from_dict(  # pyright: ignore[reportUnknownMemberType]
            _json_value(row["failure_category_json"]),
            lambda raw: OperationalFailureCategory(raw),  # pyright: ignore[reportUnknownLambdaType]
        )
        process_integrity = self._evidence_value_from_dict(  # pyright: ignore[reportUnknownMemberType]
            _json_value(row["process_integrity_json"]),
            lambda raw: bool(raw),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
        )
        latency = self._evidence_value_from_dict(  # pyright: ignore[reportUnknownMemberType]
            _json_value(row["latency_json"]),
            lambda raw: int(raw),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
        )
        usage = self._evidence_value_from_dict(  # pyright: ignore[reportUnknownMemberType]
            _json_value(row["usage_json"]),
            lambda raw: UsageMeasurement(  # pyright: ignore[reportUnknownLambdaType]
                quota_pool_scope=raw["quota_pool_scope"],  # pyright: ignore[reportUnknownArgumentType]
                used_fraction=float(raw["used_fraction"]),  # pyright: ignore[reportUnknownArgumentType]
                remaining_fraction=float(raw["remaining_fraction"]),  # pyright: ignore[reportUnknownArgumentType]
                window_started_at=int(raw["window_started_at"]),  # pyright: ignore[reportUnknownArgumentType]
                resets_at=int(raw["resets_at"]),  # pyright: ignore[reportUnknownArgumentType]
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
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        projection.failure_category,
                        lambda v: v.value,  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        projection.process_integrity,
                        lambda v: v,  # pyright: ignore[reportUnknownLambdaType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        projection.latency,
                        lambda v: v,  # pyright: ignore[reportUnknownLambdaType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        projection.usage,
                        lambda v: {  # pyright: ignore[reportUnknownLambdaType]
                            "quota_pool_scope": v.quota_pool_scope,  # pyright: ignore[reportUnknownMemberType]
                            "used_fraction": v.used_fraction,  # pyright: ignore[reportUnknownMemberType]
                            "remaining_fraction": v.remaining_fraction,  # pyright: ignore[reportUnknownMemberType]
                            "window_started_at": v.window_started_at,  # pyright: ignore[reportUnknownMemberType]
                            "resets_at": v.resets_at,  # pyright: ignore[reportUnknownMemberType]
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
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        updated.failure_category,
                        lambda v: v.value,  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        updated.process_integrity,
                        lambda v: v,  # pyright: ignore[reportUnknownLambdaType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        updated.latency,
                        lambda v: v,  # pyright: ignore[reportUnknownLambdaType]
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(  # pyright: ignore[reportUnknownMemberType]
                        updated.usage,
                        lambda v: {  # pyright: ignore[reportUnknownLambdaType]
                            "quota_pool_scope": v.quota_pool_scope,  # pyright: ignore[reportUnknownMemberType]
                            "used_fraction": v.used_fraction,  # pyright: ignore[reportUnknownMemberType]
                            "remaining_fraction": v.remaining_fraction,  # pyright: ignore[reportUnknownMemberType]
                            "window_started_at": v.window_started_at,  # pyright: ignore[reportUnknownMemberType]
                            "resets_at": v.resets_at,  # pyright: ignore[reportUnknownMemberType]
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

    # ── Slice 5: session context observations ──

    def _session_context_observation_from_row(  # pyright: ignore[reportUnknownParameterType]
        self,
        row: sqlite3.Row,
    ) -> SessionContextObserved:
        return SessionContextObserved(  # pyright: ignore[reportUnknownVariableType]
            observation_id=row["observation_id"],
            binding_key=SessionBindingKey(
                workspace_scope_id=row["workspace_scope_id"],
                instance_id=row["instance_id"],
                profile_id=row["profile_id"],
                conversation_scope=row["conversation_scope"] if "conversation_scope" in row.keys() else "global",
            ),
            generation_id=row["generation_id"],
            observed_tokens=row["observed_tokens"],
            window_tokens=row["window_tokens"],
            source=row["source"],
            observed_at=row["observed_at"],
        )

    def add_session_context_observation(
        self,
        observation: SessionContextObserved,  # pyright: ignore[reportUnknownParameterType]
    ) -> None:
        """Insert an immutable session context observation."""
        self._db().execute(
            """
            INSERT INTO session_context_observations (
                observation_id,
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                generation_id,
                observed_tokens,
                window_tokens,
                source,
                observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (  # pyright: ignore[reportUnknownArgumentType]
                observation.observation_id,  # pyright: ignore[reportUnknownMemberType]
                observation.binding_key.workspace_scope_id,  # pyright: ignore[reportUnknownMemberType]
                observation.binding_key.instance_id,  # pyright: ignore[reportUnknownMemberType]
                observation.binding_key.profile_id,  # pyright: ignore[reportUnknownMemberType]
                observation.binding_key.conversation_scope,  # pyright: ignore[reportUnknownMemberType]
                observation.generation_id,  # pyright: ignore[reportUnknownMemberType]
                observation.observed_tokens,  # pyright: ignore[reportUnknownMemberType]
                observation.window_tokens,  # pyright: ignore[reportUnknownMemberType]
                observation.source,  # pyright: ignore[reportUnknownMemberType]
                observation.observed_at,  # pyright: ignore[reportUnknownMemberType]
            ),
        )

    # ── Slice 5: session context projections ──

    def _session_context_projection_from_row(
        self,
        row: sqlite3.Row,
    ) -> SessionContextProjectionSnapshot:
        return SessionContextProjectionSnapshot(
            projection_id=row["projection_id"],
            binding_key=SessionBindingKey(
                workspace_scope_id=row["workspace_scope_id"],
                instance_id=row["instance_id"],
                profile_id=row["profile_id"],
                conversation_scope=row["conversation_scope"] if "conversation_scope" in row.keys() else "global",
            ),
            generation_id=row["generation_id"],
            observed_tokens=row["observed_tokens"],
            window_tokens=row["window_tokens"],
            source=row["source"],
            observed_at=row["observed_at"],
            revision=row["revision"],
            updated_at=row["updated_at"],
        )

    def add_session_context_projection(
        self,
        projection: SessionContextProjectionSnapshot,
    ) -> None:
        """Insert a revision-one session context projection."""
        self._db().execute(
            """
            INSERT INTO session_context_projections (
                projection_id,
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                generation_id,
                observed_tokens,
                window_tokens,
                source,
                observed_at,
                revision,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection.projection_id,
                projection.binding_key.workspace_scope_id,
                projection.binding_key.instance_id,
                projection.binding_key.profile_id,
                projection.binding_key.conversation_scope,
                projection.generation_id,
                projection.observed_tokens,
                projection.window_tokens,
                projection.source,
                projection.observed_at,
                projection.revision,
                projection.updated_at,
            ),
        )

    def get_session_context_projection(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        conversation_scope: str,
        generation_id: int,
    ) -> SessionContextProjectionSnapshot | None:
        """Return the current context occupancy by binding+generation."""
        row = self._db().execute(
            """
            SELECT *
            FROM session_context_projections
            WHERE workspace_scope_id = ? 
              AND instance_id = ? 
              AND profile_id = ? 
              AND conversation_scope = ?
              AND generation_id = ?
            """,
            (workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id),
        ).fetchone()
        return None if row is None else self._session_context_projection_from_row(row)

    def cas_update_session_context_projection(
        self,
        current: SessionContextProjectionSnapshot,
        updated: SessionContextProjectionSnapshot,
    ) -> bool:
        """CAS-update a session context projection snapshot."""
        if current.projection_id != updated.projection_id:
            raise ValueError("projection IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE session_context_projections
            SET
                observed_tokens = ?,
                window_tokens = ?,
                source = ?,
                observed_at = ?,
                revision = ?,
                updated_at = ?
            WHERE
                projection_id = ?
                AND revision = ?
            """,
            (
                updated.observed_tokens,
                updated.window_tokens,
                updated.source,
                updated.observed_at,
                updated.revision,
                updated.updated_at,
                current.projection_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1
