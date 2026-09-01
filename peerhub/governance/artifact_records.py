"""Durable, workspace-scoped ownership records for named artifacts.

This journal is intentionally separate from dispatch artifact materialization.
Materialization rows belong to one dispatch attempt; these records coordinate
peer ownership of a named document across attempts and sessions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    ArtifactClaimConflictError,
    ArtifactFileNotFoundError,
    ArtifactNotClaimedError,
)
from peerhub.core.protocol import CommandID, JsonValue, require_text

from .broker import GovernanceBroker
from .contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)


@dataclass(frozen=True, slots=True)
class ArtifactMutationResult:
    """A committed artifact-record mutation and its resulting record."""

    submission: MutationSubmission
    record: TargetState


@dataclass(frozen=True, slots=True)
class ArtifactStatusResult:
    """One named record or the stable full artifact-record listing."""

    items: tuple[TargetState, ...]
    single: bool


class ArtifactRecordService:
    """Claim, inspect, draft, and finalize durable named artifacts."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        workspace_root: Path,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._workspace_root = workspace_root.resolve()
        self._clock = clock
        self._ids = ids

    @staticmethod
    def _target_id(name: str) -> str:
        return f"artifact-record:{name}"

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> ArtifactMutationResult:
        request_id = self._ids.new_id("artifact-record-request")
        submission = self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("artifact-record-command")
                ),
                correlation_id=self._ids.new_id(
                    "artifact-record-correlation"
                ),
                client_id="peerhub.artifact_records",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(
                    kind="artifact-record.noop",
                    payload={},
                ),
            )
        )
        record = self._broker.get_target(target_id)
        if record is None:  # pragma: no cover - committed CAS guarantees it
            raise RuntimeError("committed artifact record was not readable")
        return ArtifactMutationResult(submission=submission, record=record)

    def is_workspace_local(self, path: str | Path) -> bool:
        """Return whether a path resolves inside this service's workspace."""

        try:
            resolved = Path(path).resolve()
        except OSError:
            return False
        return (
            resolved == self._workspace_root
            or self._workspace_root in resolved.parents
        )

    def get_record(self, name: str) -> TargetState | None:
        normalized_name = require_text(name, "name")
        return self._broker.get_target(self._target_id(normalized_name))

    def list_records(self) -> Sequence[TargetState]:
        return self._broker.list_targets("artifact-record", None)

    def status(self, name: str | None = None) -> ArtifactStatusResult:
        if name is None or name == "":
            return ArtifactStatusResult(
                items=tuple(self.list_records()),
                single=False,
            )
        record = self.get_record(name)
        return ArtifactStatusResult(
            items=() if record is None else (record,),
            single=True,
        )

    def claim(self, name: str, owner: str) -> ArtifactMutationResult:
        normalized_name = require_text(name, "name")
        normalized_owner = require_text(owner, "owner")
        target_id = self._target_id(normalized_name)
        current = self._broker.get_target(target_id)
        now = self._clock.now()

        current_state: Mapping[str, JsonValue] = (
            {} if current is None else current.state
        )
        current_owner = current_state.get("owner")
        current_status = current_state.get("status")
        if (
            isinstance(current_owner, str)
            and current_owner
            and current_owner != normalized_owner
            and current_status != "finalized"
        ):
            raise ArtifactClaimConflictError(
                normalized_name,
                current_owner,
            )

        claimed_at = current_state.get("claimed_at")
        if not isinstance(claimed_at, int):
            claimed_at = now
        artifact_hash = current_state.get("hash")
        if not isinstance(artifact_hash, str):
            artifact_hash = ""
        existing_drafts = current_state.get("drafts")
        drafts: dict[str, JsonValue] = {}
        if isinstance(existing_drafts, Mapping):
            drafts = {
                str(peer): path
                for peer, path in existing_drafts.items()
                if isinstance(path, str)
            }
        existing_updated_at = current_state.get("updated_at")
        updated_at = now
        if (
            current_owner == normalized_owner
            and current_status == "claimed"
            and isinstance(existing_updated_at, int)
        ):
            updated_at = existing_updated_at

        # Build a fresh record, matching legacy's replacement semantics.  In
        # particular, reclaiming a finalized artifact deliberately drops its
        # prior finalized_at/actual_path fields while preserving its claim
        # timestamp, draft map, and last content hash.
        desired_state: dict[str, JsonValue] = {
            "kind": "artifact-record",
            "scope": None,
            "schema_version": 1,
            "artifact": normalized_name,
            "owner": normalized_owner,
            "mode": "single_owner_merge",
            "drafts": drafts,
            "status": "claimed",
            "claimed_at": claimed_at,
            "hash": artifact_hash,
            "updated_at": updated_at,
        }
        return self._submit(
            target_id=target_id,
            expected_revision=0 if current is None else current.revision,
            actor_id=normalized_owner,
            operation="artifact.claim",
            desired_state=desired_state,
        )

    def register_draft(
        self,
        name: str,
        *,
        peer: str,
        draft_path: str,
    ) -> ArtifactMutationResult:
        normalized_name = require_text(name, "name")
        normalized_peer = require_text(peer, "peer")
        normalized_path = require_text(draft_path, "draft_path")
        current = self._broker.get_target(self._target_id(normalized_name))
        if current is None:
            raise ArtifactNotClaimedError(normalized_name)

        existing_drafts = current.state.get("drafts")
        drafts: dict[str, JsonValue] = {}
        if isinstance(existing_drafts, Mapping):
            drafts = {
                str(owner): path
                for owner, path in existing_drafts.items()
                if isinstance(path, str)
            }
        drafts[normalized_peer] = normalized_path

        desired_state: dict[str, JsonValue] = dict(current.state)
        desired_state["drafts"] = drafts
        desired_state["status"] = "draft"
        external_draft_warned = not self.is_workspace_local(normalized_path)
        desired_state["external_draft_warned"] = external_draft_warned
        previous_drafts = current.state.get("drafts")
        previous_path = (
            previous_drafts.get(normalized_peer)
            if isinstance(previous_drafts, Mapping)
            else None
        )
        if (
            current.state.get("status") != "draft"
            or previous_path != normalized_path
            or current.state.get("external_draft_warned")
            is not external_draft_warned
        ):
            desired_state["updated_at"] = self._clock.now()
        return self._submit(
            target_id=current.target_id,
            expected_revision=current.revision,
            actor_id=normalized_peer,
            operation="artifact.draft.register",
            desired_state=desired_state,
        )

    def finalize(
        self,
        name: str,
        file_path: str | Path,
    ) -> ArtifactMutationResult:
        normalized_name = require_text(name, "name")
        actual_file = Path(file_path)
        if not actual_file.exists():
            raise ArtifactFileNotFoundError(str(file_path))

        digest = hashlib.sha256()
        with actual_file.open("rb") as stream:
            while chunk := stream.read(65536):
                digest.update(chunk)
        sha_str = f"sha256:{digest.hexdigest()}"

        current = self._broker.get_target(self._target_id(normalized_name))
        if current is None:
            raise ArtifactNotClaimedError(normalized_name)

        now = self._clock.now()
        desired_state: dict[str, JsonValue] = dict(current.state)
        desired_state["status"] = "finalized"
        desired_state["hash"] = sha_str
        desired_state["finalized_at"] = now
        desired_state["actual_path"] = str(actual_file.resolve())
        desired_state["updated_at"] = now
        owner = current.state.get("owner")
        actor_id = owner if isinstance(owner, str) and owner else "unknown"
        return self._submit(
            target_id=current.target_id,
            expected_revision=current.revision,
            actor_id=actor_id,
            operation="artifact.finalize",
            desired_state=desired_state,
        )
