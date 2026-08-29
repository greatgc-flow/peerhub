"""Durable operational-error series and quarantine-review requests.

Each ``(peer_key, pattern)`` pair owns one CAS-governed counter target. Every
report is retained in that target's history, and every count at or above the
configured threshold creates a distinct immutable quarantine-review request.
This records escalation intent only; health circuit state remains owned by the
health domain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, StaleRevisionError
from peerhub.core.protocol import CommandID, JsonValue, require_text

from .broker import GovernanceBroker
from .contract import EffectIntent, MutationRequest, MutationSubmission


class OperationalErrorService:
    """Append operational errors and request threshold-driven review."""

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
        request_id = self._ids.new_id("operational-error-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("operational-error-command")
                ),
                correlation_id=self._ids.new_id(
                    "operational-error-correlation"
                ),
                client_id="peerhub.operational-errors",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(
                    kind="operational-errors.noop", payload={}
                ),
            )
        )

    @staticmethod
    def _reports(value: JsonValue | None) -> tuple[JsonValue, ...]:
        if not isinstance(value, (list, tuple)):
            raise InvalidMutationError(
                "operational-error series reports must be a sequence"
            )
        reports: list[JsonValue] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise InvalidMutationError(
                    "operational-error series reports must contain objects"
                )
            reports.append(item)
        return tuple(reports)

    @staticmethod
    def _matches_review_identity(
        state: Mapping[str, JsonValue],
        *,
        series_target_id: str,
        trigger_count: int,
        peer_key: str,
        pattern_hash: str,
    ) -> bool:
        return (
            state.get("series_target_id") == series_target_id
            and state.get("trigger_count") == trigger_count
            and state.get("peer_key") == peer_key
            and state.get("pattern_hash") == pattern_hash
        )

    def report_error(
        self,
        *,
        peer_key: str,
        pattern: str,
        severity: str,
        detail: str,
        actor_id: str,
        threshold: int = 3,
    ) -> MutationSubmission:
        """Append one report and request review at every threshold count.

        The shared series counter is the exceptional governance domain that
        retries stale revisions: all callers append to the same target, and a
        lost CAS must not silently lose the report. The report's identity and
        timestamp are allocated once so retries cannot duplicate the logical
        report.
        """

        normalized_peer_key = require_text(peer_key, "peer_key")
        normalized_pattern = require_text(pattern, "pattern")
        normalized_severity = require_text(severity, "severity")
        normalized_actor_id = require_text(actor_id, "actor_id")
        if not isinstance(detail, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("detail must be a string")
        if type(threshold) is not int or threshold < 1:
            raise ValueError("threshold must be a positive integer")

        pattern_hash = hashlib.sha256(
            normalized_pattern.encode("utf-8")
        ).hexdigest()
        series_target_id = (
            f"operational-error-series:{normalized_peer_key}:{pattern_hash}"
        )
        report: dict[str, JsonValue] = {
            "report_id": self._ids.new_id("operational-error-report"),
            "severity": normalized_severity,
            "detail": detail,
            "reported_at": self._clock.now(),
            "actor_id": normalized_actor_id,
        }

        series_submission: MutationSubmission | None = None
        reports: tuple[JsonValue, ...] = ()
        next_count = 0
        review_id: str | None = None

        for _ in range(16):
            current = self._broker.get_target(series_target_id)
            if current is None:
                count = 0
                existing_reports: tuple[JsonValue, ...] = ()
                expected_revision = 0
                previous_review_id: JsonValue = None
            else:
                stored_threshold = current.state.get("threshold")
                if stored_threshold != threshold:
                    raise InvalidMutationError(
                        "operational-error series threshold cannot change "
                        f"from {stored_threshold!r} to {threshold!r}"
                    )
                stored_count = current.state.get("count")
                if type(stored_count) is not int or stored_count < 0:
                    raise InvalidMutationError(
                        "operational-error series count must be a "
                        "nonnegative integer"
                    )
                count = stored_count
                existing_reports = self._reports(
                    current.state.get("reports")
                )
                expected_revision = current.revision
                previous_review_id = current.state.get(
                    "quarantine_review_id"
                )

            next_count = count + 1
            reports = (*existing_reports, report)
            review_id = (
                f"{normalized_peer_key}:{pattern_hash}:"
                f"{threshold}:{next_count}"
                if next_count >= threshold
                else None
            )
            desired_state: dict[str, JsonValue] = {
                "kind": "operational-error-series",
                "scope": None,
                "schema_version": 1,
                "peer_key": normalized_peer_key,
                "pattern": normalized_pattern,
                "pattern_hash": pattern_hash,
                "threshold": threshold,
                "count": next_count,
                "reports": reports,
                "quarantine_review_id": (
                    review_id
                    if review_id is not None
                    else previous_review_id
                ),
                "updated_at": report["reported_at"],
            }
            try:
                series_submission = self._submit(
                    target_id=series_target_id,
                    expected_revision=expected_revision,
                    actor_id=normalized_actor_id,
                    operation="operational-error.report",
                    desired_state=desired_state,
                )
                break
            except StaleRevisionError:
                continue
        else:
            raise InvalidMutationError(
                "operational-error series changed repeatedly while "
                "recording a report"
            )

        assert series_submission is not None
        if review_id is None:
            return series_submission

        review_target_id = f"quarantine-review:{review_id}"
        review_state: dict[str, JsonValue] = {
            "kind": "quarantine-review",
            "scope": None,
            "schema_version": 1,
            "review_id": review_id,
            "peer_key": normalized_peer_key,
            "pattern": normalized_pattern,
            "pattern_hash": pattern_hash,
            "threshold": threshold,
            "trigger_count": next_count,
            "series_target_id": series_target_id,
            "series_revision": series_submission.receipt.next_revision,
            "status": "REQUESTED",
            "requested_at": self._clock.now(),
            "reports_snapshot": reports,
            "actor_id": normalized_actor_id,
        }
        try:
            self._submit(
                target_id=review_target_id,
                expected_revision=0,
                actor_id=normalized_actor_id,
                operation="operational-error.quarantine-review.request",
                desired_state=review_state,
            )
        except StaleRevisionError as exc:
            existing = self._broker.get_target(review_target_id)
            if existing is None or not self._matches_review_identity(
                existing.state,
                series_target_id=series_target_id,
                trigger_count=next_count,
                peer_key=normalized_peer_key,
                pattern_hash=pattern_hash,
            ):
                raise InvalidMutationError(
                    "quarantine-review target collision does not match "
                    "the committed operational-error trigger"
                ) from exc

        return series_submission
