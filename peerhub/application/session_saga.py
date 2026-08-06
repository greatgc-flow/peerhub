"""Session rotation decision logic and CAS coordination saga.

Decision Table:
| Policy | Safe Signal | Pressure Status      | Action Taken / Returned           |
|--------|-------------|----------------------|-----------------------------------|
| reuse  | (ignored)   | No Pressure / Absent | PROCEED_WITH_REUSE                |
| reuse  | (ignored)   | Pressure Reached     | CHECKPOINT_REQUIRED               |
| auto   | (ignored)   | No Pressure / Absent | PROCEED_WITH_REUSE                |
| auto   | False       | Pressure Reached     | ROTATION_PENDING_PROCEED          |
| auto   | True        | Pressure Reached     | CLAIM_ROTATION -> ROTATION_CLAIMED|
| fresh  | (ignored)   | (ignored)            | CLAIM_ROTATION -> ROTATION_CLAIMED|

Note: "Pressure Reached" requires evidence that is exact-attribution ("exact_attribution") and fresh.
If the evidence is absent, an estimate, or older than the max observation age, it is treated as "No Pressure / Absent".

If CLAIM_ROTATION fails (already claimed by concurrent process):
- Returns ROTATION_IN_PROGRESS_RETRY, allowing the caller to back off or retry.
"""

from __future__ import annotations
import enum
from dataclasses import dataclass
from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.telemetry.contract import SessionContextProjectionSnapshot


class RotationDecision(enum.Enum):
    PROCEED_WITH_REUSE = "PROCEED_WITH_REUSE"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    ROTATION_PENDING_PROCEED = "ROTATION_PENDING_PROCEED"
    ROTATION_CLAIMED = "ROTATION_CLAIMED"
    ROTATION_IN_PROGRESS_RETRY = "ROTATION_IN_PROGRESS_RETRY"


class DispatchRepository(Protocol):
    def claim_rotation(
        self,
        *,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        expected_generation_id: int,
        claim_token: str,
        claim_expiry: int,
        updated_at: int,
    ) -> bool:
        ...

    def commit_rotation(
        self,
        *,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        expected_generation_id: int,
        claim_token: str,
        new_conversation_id: str,
        updated_at: int,
    ) -> bool:
        ...


class TelemetryRepository(Protocol):
    def get_session_context_projection(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        generation_id: int,
    ) -> SessionContextProjectionSnapshot | None:
        ...


@dataclass(frozen=True)
class SessionSagaResult:
    decision: RotationDecision
    claim_token: str | None = None


class SessionRotationSaga:
    def __init__(
        self,
        dispatch_repo: DispatchRepository,
        telemetry_repo: TelemetryRepository,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._dispatch = dispatch_repo
        self._telemetry = telemetry_repo
        self._clock = clock
        self._ids = ids

    def evaluate_and_claim(
        self,
        policy: str,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        current_generation_id: int,
        rotation_safe: bool,
        max_observation_age_ms: int = 30000,
    ) -> SessionSagaResult:
        """Evaluate session context and apply rotation policy."""

        if policy == "fresh":
            return self._attempt_claim(
                workspace_scope_id,
                instance_id,
                profile_id,
                current_generation_id,
            )

        projection = self._telemetry.get_session_context_projection(
            workspace_scope_id=workspace_scope_id,
            instance_id=instance_id,
            profile_id=profile_id,
            generation_id=current_generation_id,
        )

        pressure_reached = False
        if projection is not None:
            is_exact = projection.source == "exact_attribution"
            is_fresh = (self._clock.now() - projection.observed_at) <= max_observation_age_ms
            if is_exact and is_fresh:
                pressure_reached = projection.observed_tokens >= projection.window_tokens

        if not pressure_reached:
            return SessionSagaResult(decision=RotationDecision.PROCEED_WITH_REUSE)

        if policy == "reuse":
            return SessionSagaResult(decision=RotationDecision.CHECKPOINT_REQUIRED)

        if policy == "auto":
            if not rotation_safe:
                return SessionSagaResult(decision=RotationDecision.ROTATION_PENDING_PROCEED)
            return self._attempt_claim(
                workspace_scope_id,
                instance_id,
                profile_id,
                current_generation_id,
            )

        raise ValueError(f"Unknown session policy: {policy}")

    def _attempt_claim(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        current_generation_id: int,
    ) -> SessionSagaResult:
        now = self._clock.now()
        claim_token = self._ids.new_id("claim")
        claim_expiry = now + 30000  # 30 seconds

        claimed = self._dispatch.claim_rotation(
            workspace_scope_id=workspace_scope_id,
            instance_id=instance_id,
            profile_id=profile_id,
            expected_generation_id=current_generation_id,
            claim_token=claim_token,
            claim_expiry=claim_expiry,
            updated_at=now,
        )

        if claimed:
            return SessionSagaResult(
                decision=RotationDecision.ROTATION_CLAIMED,
                claim_token=claim_token,
            )
        else:
            return SessionSagaResult(decision=RotationDecision.ROTATION_IN_PROGRESS_RETRY)
