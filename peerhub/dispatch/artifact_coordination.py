from __future__ import annotations

from collections.abc import Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.state.contract import StateStore

from .contract import (
    ArtifactManifestRecord,
    ArtifactMetadata,
)
from .unit_of_work import DispatchUnitOfWork, FaultInjector, _NoFaultInjector  # pyright: ignore[reportPrivateUsage]


class ArtifactCoordinator:
    """Orchestrate Phase 1 artifact coordination and manifests."""

    def __init__(
        self,
        store: StateStore[DispatchUnitOfWork],
        *,
        clock: Clock,
        ids: IdSource,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._faults = fault_injector or _NoFaultInjector()

    def record_artifact_manifest(
        self,
        manifest_record: ArtifactManifestRecord,
        item_records: Sequence[ArtifactMetadata],
    ) -> None:
        """Persist an artifact manifest and item metadata records."""
        with self._store.unit_of_work() as unit:
            unit.add_artifact_manifest(  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                manifest_record,
                tuple(item_records),
            )
            unit.commit()

    def mark_artifacts_orphaned_if_manifest_exists(
        self,
        attempt_id: str,
        *,
        failure_code: str,
    ) -> bool:
        """Mark artifacts orphaned for an attempt if an artifact manifest exists."""
        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            manifest_row = unit.get_artifact_manifest(attempt_id)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
            if manifest_row is None:
                return False
            unit.mark_artifacts_orphaned(  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                attempt_id=attempt_id,
                expected_manifest_revision=manifest_row.revision,  # pyright: ignore[reportUnknownMemberType]
                orphaned_at=timestamp,
                failure_code=failure_code,
            )
            unit.commit()
            return True
