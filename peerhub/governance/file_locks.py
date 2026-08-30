from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from peerhub.core.context import Clock, IdSource
from peerhub.core.protocol import CommandID, JsonValue, require_text
from peerhub.core.errors import (
    FileLockConflictError,
    FileLockOwnershipMismatchError,
)

from .broker import GovernanceBroker
from .contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)

class FileUnlockDisposition(StrEnum):
    RELEASED = "RELEASED"
    NOT_LOCKED = "NOT_LOCKED"


@dataclass(frozen=True, slots=True)
class FileUnlockResult:
    disposition: FileUnlockDisposition
    submission: MutationSubmission | None
    target: TargetState | None


class FileLockService:
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

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("file-lock-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("file-lock-command")),
                correlation_id=self._ids.new_id("file-lock-correlation"),
                client_id="peerhub.file_locks",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="file-lock.noop", payload={}),
            )
        )

    @staticmethod
    def _target_id(name: str) -> str:
        return f"file-lock:{name}"

    def lock_file(self, name: str, owner: str, lock_scope: str = "file") -> MutationSubmission:
        normalized_name = require_text(name, "name")
        normalized_owner = require_text(owner, "owner")
        normalized_lock_scope = require_text(lock_scope, "lock_scope")

        target_id = self._target_id(name)
        current = self._broker.get_target(target_id)
        now = self._clock.now()

        # Unlock hard-deletes the target (matching legacy's data.pop()), so an
        # absent target is the only "no active lock" case in practice. The
        # status=="RELEASED" check is defensive in case a target is ever
        # created directly in that state rather than deleted.
        if current is not None:
            # Check owner
            current_owner = current.state.get("owner")
            if not isinstance(current_owner, str):
                current_owner = ""
                
            if current_owner != normalized_owner:
                raise FileLockConflictError(normalized_name, current_owner)
            
            # Same owner re-lock
            locked_at = current.state.get("locked_at")
            if not isinstance(locked_at, int):
                locked_at = now
            
            desired_state: dict[str, JsonValue] = dict(current.state)
            desired_state["lock_scope"] = normalized_lock_scope
            desired_state["locked_at"] = locked_at
            desired_state["updated_at"] = now
            expected_revision = current.revision
        else:
            desired_state: dict[str, JsonValue] = {
                "kind": "file-lock",
                "scope": None,
                "schema_version": 1,
                "name": name,
                "owner": normalized_owner,
                "lock_scope": normalized_lock_scope,
                "locked_at": now,
                "updated_at": now,
            }
            expected_revision = 0
            
        return self._submit(
            target_id=target_id,
            expected_revision=expected_revision,
            actor_id=normalized_owner,
            operation="file-lock.acquire",
            desired_state=desired_state,
        )

    def unlock_file(self, name: str, owner: str | None = None) -> FileUnlockResult:
        normalized_name = require_text(name, "name")
        normalized_owner = None if owner is None or owner == "" else require_text(owner, "owner")
        
        target_id = self._target_id(name)
        current = self._broker.get_target(target_id)
        
        if current is None:
            return FileUnlockResult(
                disposition=FileUnlockDisposition.NOT_LOCKED,
                submission=None,
                target=current,
            )
            
        current_owner = current.state.get("owner")
        if not isinstance(current_owner, str):
            current_owner = ""
            
        if normalized_owner is not None and current_owner != normalized_owner:
            raise FileLockOwnershipMismatchError(normalized_name, current_owner, normalized_owner)
            
        # Perform hard delete by setting state={}
        submission = self._submit(
            target_id=target_id,
            expected_revision=current.revision,
            actor_id="system",
            operation="file-lock.release",
            desired_state={},
        )
        
        return FileUnlockResult(
            disposition=FileUnlockDisposition.RELEASED,
            submission=submission,
            target=current,
        )

    def list_active_locks(self) -> Sequence[TargetState]:
        return self._broker.list_targets("file-lock", None)
