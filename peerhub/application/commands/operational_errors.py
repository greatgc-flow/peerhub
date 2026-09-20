"""Operational-error reporting commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class ReportErrorCommand(Command[Any]):
    method: ClassVar[str] = "telemetry.error.record"
    submission: SubmissionMetadata
    peer_key: str
    pattern: str
    severity: str
    detail: str
    actor_id: str
    threshold: int = 3

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_key": self.peer_key,
            "pattern": self.pattern,
            "severity": self.severity,
            "detail": self.detail,
            "actor_id": self.actor_id,
            "threshold": self.threshold,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ResolveQuarantineReviewCommand(Command[Any]):
    method: ClassVar[str] = "governance.quarantine_review.resolve"
    submission: SubmissionMetadata
    review_id: str
    decision: str
    actor_principal_id: str
    evidence_source: str
    reason: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "review_id": self.review_id,
            "decision": self.decision,
            "actor_principal_id": self.actor_principal_id,
            "evidence_source": self.evidence_source,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value
