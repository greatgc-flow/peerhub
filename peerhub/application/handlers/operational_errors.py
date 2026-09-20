"""Operational-error command registration handlers."""

from __future__ import annotations

from typing import Any

from peerhub.application.commands.operational_errors import (
    ReportErrorCommand,
    ResolveQuarantineReviewCommand,
)
from peerhub.application.handlers._params import required_text
from peerhub.application.quarantine_review import QuarantineReviewCoordinator
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import CommandEnvelope
from peerhub.governance.operational_errors import OperationalErrorService


def register_operational_error_handlers(
    *,
    api: Any,
    service: OperationalErrorService,
    quarantine_reviews: QuarantineReviewCoordinator | None = None,
) -> None:
    """Register the existing operational-error wire handler unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    submission = api._submission
    receipt = api._receipt
    descriptor = CommandDescriptor
    mutating = Mutability.MUTATING
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    available = CommandAvailability.AVAILABLE

    def decode_report(envelope: CommandEnvelope) -> ReportErrorCommand:
        threshold = envelope.params.get("threshold", 3)
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            raise ValueError("threshold must be an integer")
        detail = envelope.params.get("detail", "")
        if not isinstance(detail, str):
            raise ValueError("detail must be a string")
        return ReportErrorCommand(
            submission=submission(envelope),
            peer_key=required_text(envelope, "peer_key"),
            pattern=required_text(envelope, "pattern"),
            severity=required_text(envelope, "severity"),
            detail=detail,
            actor_id=required_text(envelope, "actor_id"),
            threshold=threshold,
        )

    def decode_resolve(
        envelope: CommandEnvelope,
    ) -> ResolveQuarantineReviewCommand:
        return ResolveQuarantineReviewCommand(
            submission=submission(envelope),
            review_id=required_text(envelope, "review_id"),
            decision=required_text(envelope, "decision"),
            actor_principal_id=required_text(
                envelope, "actor_principal_id"
            ),
            evidence_source=required_text(envelope, "evidence_source"),
            reason=required_text(envelope, "reason"),
        )

    register(descriptor(
        "telemetry.error.record",
        mutating,
        any_scope,
        domain_atomic_required,
        decode_report,
        lambda command, _context: service.report_error(
            peer_key=command.peer_key,
            pattern=command.pattern,
            severity=command.severity,
            detail=command.detail,
            actor_id=command.actor_id,
            threshold=command.threshold,
        ),
        receipt,
        available,
    ))
    if quarantine_reviews is not None:
        register(descriptor(
            "governance.quarantine_review.resolve",
            mutating,
            any_scope,
            domain_atomic_required,
            decode_resolve,
            lambda command, _context: (
                quarantine_reviews.resolve_quarantine_review(
                    command.review_id,
                    decision=command.decision,
                    actor=AuthenticatedSubject(
                        principal_id=command.actor_principal_id,
                        evidence_source=command.evidence_source,
                    ),
                    reason=command.reason,
                )
            ),
            receipt,
            available,
        ))
