"""Basic Approval Engine (M3.5).

Implements Core Invariant 11:
- Approval exact-effect binding: cryptographic SHA-256 digest of target effect.
- Single-use approval: tokens transition to CONSUMED and cannot be re-consumed.
- Strict lifecycle states: REQUESTED -> APPROVED -> CONSUMED | REJECTED | EXPIRED.
- Pure Python standard library implementation (REL-009).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Generator, cast


class ApprovalError(Exception):
    """Base exception for approval operations."""


class ApprovalNotFoundError(ApprovalError):
    """Raised when an approval_id is not found."""


class ApprovalStateTransitionError(ApprovalError):
    """Raised when an illegal lifecycle transition is attempted."""


class ApprovalExpiredError(ApprovalError):
    """Raised when an expired approval token is operated upon."""


class ApprovalAlreadyConsumedError(ApprovalError):
    """Raised when an approval token has already been consumed (Invariant 11)."""


class ApprovalEffectMismatchError(ApprovalError):
    """Raised when the target effect payload does not match approved digest (Invariant 11)."""


def compute_effect_digest(effect_payload: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 digest of effect payload with sorted keys."""
    serialized = json.dumps(effect_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    action_type: str
    effect_payload: dict[str, Any]
    target_effect_digest: str
    requested_by: str
    state: str  # REQUESTED | APPROVED | REJECTED | EXPIRED | CONSUMED
    approver: str | None
    rejection_reason: str | None
    valid_until: str
    consumed_at: str | None
    created_at: str


class ApprovalEngine:
    """Engine managing single-use, exact-effect approval requests."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    effect_payload_json TEXT NOT NULL,
                    target_effect_digest TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    state TEXT NOT NULL,
                    approver TEXT,
                    rejection_reason TEXT,
                    valid_until TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def close(self) -> None:
        """No long-lived connection held; provides clean lifecycle shutdown."""

    def clear(self) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM approvals")
            conn.commit()

    def request_approval(
        self,
        action_type: str,
        effect_payload: dict[str, Any],
        requested_by: str,
        ttl_seconds: int = 3600,
    ) -> ApprovalRequest:
        now = datetime.datetime.now(datetime.timezone.utc)
        valid_until_dt = now + datetime.timedelta(seconds=ttl_seconds)

        created_at_iso = now.isoformat()
        valid_until_iso = valid_until_dt.isoformat()

        target_digest = compute_effect_digest(effect_payload)
        approval_id = f"app-{hashlib.sha256(f'{created_at_iso}:{target_digest}:{requested_by}'.encode('utf-8')).hexdigest()[:16]}"
        payload_json = json.dumps(effect_payload, sort_keys=True)

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, action_type, effect_payload_json, target_effect_digest,
                    requested_by, state, approver, rejection_reason, valid_until,
                    consumed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    action_type,
                    payload_json,
                    target_digest,
                    requested_by,
                    "REQUESTED",
                    None,
                    None,
                    valid_until_iso,
                    None,
                    created_at_iso,
                ),
            )
            conn.commit()

        req = self.get_approval(approval_id)
        assert req is not None
        return req

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_request(row)

    def approve(self, approval_id: str, approver: str) -> ApprovalRequest:
        req = self.get_approval(approval_id)
        if req is None:
            raise ApprovalNotFoundError(f"Approval request '{approval_id}' not found.")

        self._check_and_update_expiry(req)
        # Reload after expiry check
        reloaded = self.get_approval(approval_id)
        assert reloaded is not None
        req = reloaded

        if req.state == "EXPIRED":
            raise ApprovalExpiredError(f"Approval '{approval_id}' has expired.")
        if req.state != "REQUESTED":
            raise ApprovalStateTransitionError(
                f"Cannot approve request in state '{req.state}' (expected REQUESTED)."
            )

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET state = 'APPROVED', approver = ?
                WHERE approval_id = ?
                """,
                (approver, approval_id),
            )
            conn.commit()

        updated = self.get_approval(approval_id)
        assert updated is not None
        return updated

    def reject(self, approval_id: str, reason: str, approver: str) -> ApprovalRequest:
        req = self.get_approval(approval_id)
        if req is None:
            raise ApprovalNotFoundError(f"Approval request '{approval_id}' not found.")

        self._check_and_update_expiry(req)
        reloaded = self.get_approval(approval_id)
        assert reloaded is not None
        req = reloaded

        if req.state == "EXPIRED":
            raise ApprovalExpiredError(f"Approval '{approval_id}' has expired.")
        if req.state != "REQUESTED":
            raise ApprovalStateTransitionError(
                f"Cannot reject request in state '{req.state}' (expected REQUESTED)."
            )

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET state = 'REJECTED', rejection_reason = ?, approver = ?
                WHERE approval_id = ?
                """,
                (reason, approver, approval_id),
            )
            conn.commit()

        updated = self.get_approval(approval_id)
        assert updated is not None
        return updated

    def consume_approval(
        self, approval_id: str, effect_payload: dict[str, Any]
    ) -> ApprovalRequest:
        req = self.get_approval(approval_id)
        if req is None:
            raise ApprovalNotFoundError(f"Approval request '{approval_id}' not found.")

        if req.state == "CONSUMED":
            raise ApprovalAlreadyConsumedError(
                f"Approval '{approval_id}' has already been consumed (Invariant 11)."
            )

        self._check_and_update_expiry(req)
        reloaded = self.get_approval(approval_id)
        assert reloaded is not None
        req = reloaded

        if req.state == "EXPIRED":
            raise ApprovalExpiredError(f"Approval '{approval_id}' has expired.")

        if req.state != "APPROVED":
            raise ApprovalStateTransitionError(
                f"Cannot consume request in state '{req.state}' (expected APPROVED)."
            )

        # Invariant 11: Exact-effect check
        actual_digest = compute_effect_digest(effect_payload)
        if actual_digest != req.target_effect_digest:
            raise ApprovalEffectMismatchError(
                f"Effect payload digest mismatch: expected '{req.target_effect_digest}', got '{actual_digest}'."
            )

        consumed_at_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET state = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ?
                """,
                (consumed_at_iso, approval_id),
            )
            conn.commit()

        updated = self.get_approval(approval_id)
        assert updated is not None
        return updated

    def list_approvals(
        self,
        state: str | None = None,
        requested_by: str | None = None,
    ) -> list[ApprovalRequest]:
        query = "SELECT * FROM approvals WHERE 1=1"
        params: list[Any] = []
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        if requested_by is not None:
            query += " AND requested_by = ?"
            params.append(requested_by)
        query += " ORDER BY created_at ASC"

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_request(row) for row in cursor.fetchall()]

    def _check_and_update_expiry(self, req: ApprovalRequest) -> None:
        if req.state in ("CONSUMED", "REJECTED", "EXPIRED"):
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        valid_until_dt = datetime.datetime.fromisoformat(req.valid_until)
        if now > valid_until_dt:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE approvals SET state = 'EXPIRED' WHERE approval_id = ?",
                    (req.approval_id,),
                )
                conn.commit()

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
        payload = cast(dict[str, Any], json.loads(cast(str, row["effect_payload_json"])))
        return ApprovalRequest(
            approval_id=cast(str, row["approval_id"]),
            action_type=cast(str, row["action_type"]),
            effect_payload=payload,
            target_effect_digest=cast(str, row["target_effect_digest"]),
            requested_by=cast(str, row["requested_by"]),
            state=cast(str, row["state"]),
            approver=cast(str | None, row["approver"]),
            rejection_reason=cast(str | None, row["rejection_reason"]),
            valid_until=cast(str, row["valid_until"]),
            consumed_at=cast(str | None, row["consumed_at"]),
            created_at=cast(str, row["created_at"]),
        )
