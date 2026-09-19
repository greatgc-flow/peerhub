"""Restore-specific lifecycle checks and revocation over existing authority rows."""

from __future__ import annotations

import sqlite3

from .maintenance import WorkspaceMaintenanceError
from .tables import (
    TABLE_DISPATCH_ATTEMPTS,
    TABLE_DISPATCH_REQUESTS,
    TABLE_EFFECT_DELIVERIES,
    TABLE_EFFECT_RECEIPTS,
)


_TERMINAL = """'REJECTED_POLICY', 'FAILED_PRE_DISPATCH', 'SUCCEEDED_VERIFIED',
    'DELIVERED_UNVERIFIED', 'INCOMPLETE', 'FAILED', 'INTERRUPTED', 'CANCELLED'"""


def require_quiescent(connection: sqlite3.Connection) -> None:
    """Expiry alone is not evidence that a process or claimed effect stopped."""
    checks = {
        TABLE_DISPATCH_REQUESTS: f"state NOT IN ({_TERMINAL})",
        TABLE_DISPATCH_ATTEMPTS: f"state NOT IN ({_TERMINAL}) OR "
            "execution_certainty IN ('MAY_HAVE_STARTED', 'STARTED')",
        "leases": "state NOT IN ('RELEASED', 'FENCED', 'ABANDONED_PRE_SPAWN')",
        "session_bindings": "state IN ('CREATING', 'IN_USE', 'VERIFYING', 'SUSPECT', 'UNKNOWN')",
        "session_binding_generations": "state IN ('ROTATION_PENDING', 'DRAINING', 'SUSPECT')",
        "duty_leases": "state NOT IN ('RELEASED', 'EXPIRED')",
        "room_participation_sessions": "state = 'ACTIVE'",
        "recovery_probe_grants": "state IN ('GRANTED', 'CLAIMED')",
        TABLE_EFFECT_DELIVERIES: f"claimed_at IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {TABLE_EFFECT_RECEIPTS} WHERE outbox_event_id = {TABLE_EFFECT_DELIVERIES}.event_id)",
        "capability_leases": "revoked_at_epoch IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM leases WHERE lease_id = session_lease_id AND "
            "state IN ('RELEASED', 'FENCED', 'ABANDONED_PRE_SPAWN'))",
    }
    for table, predicate in checks.items():
        if connection.execute(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1").fetchone():
            raise WorkspaceMaintenanceError(f"workspace not quiescent: {table} active/uncertain")


def invalidate_restored_authority(connection: sqlite3.Connection) -> None:
    """Revoke authority without fabricating successful external-effect receipts."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Attempts first: the request quarantine subsequently forbids attempts.
        connection.execute(f"""UPDATE {TABLE_DISPATCH_ATTEMPTS}
            SET state = 'INTERRUPTED', revision = revision + 1,
                reconciliation_complete = 0,
                terminal_error_code = 'RESTORE_RECONCILIATION_REQUIRED'
            WHERE command_id IN (SELECT command_id FROM {TABLE_DISPATCH_REQUESTS}
                                 WHERE restore_quarantined = 0)
              AND state NOT IN ({_TERMINAL})""")
        connection.execute(f"""UPDATE {TABLE_DISPATCH_REQUESTS}
            SET state = CASE WHEN state NOT IN ({_TERMINAL}) THEN 'INTERRUPTED' ELSE state END,
                terminal_error_code = CASE WHEN state NOT IN ({_TERMINAL})
                    THEN 'RESTORE_RECONCILIATION_REQUIRED' ELSE terminal_error_code END,
                restore_quarantined = 1, revision = revision + 1""")
        connection.execute("""UPDATE capability_leases SET revoked_at_epoch =
            (SELECT activation_epoch FROM workspace_identity WHERE singleton = 1)
            WHERE revoked_at_epoch IS NULL""")
        connection.execute("""UPDATE leases SET
            state = CASE WHEN state IN ('RESERVED', 'ABANDONED_PRE_SPAWN')
                         THEN 'ABANDONED_PRE_SPAWN' ELSE 'FENCED' END,
            authority_epoch = authority_epoch + 1, revision = revision + 1""")
        connection.execute("""UPDATE session_bindings SET state = 'RETIRED',
            current_lease_id = NULL, session_generation = session_generation + 1,
            revision = revision + 1""")
        connection.execute("""UPDATE session_binding_generations SET state = 'RETIRED',
            claim_token = NULL, claim_expiry = NULL""")
        connection.execute("""UPDATE duty_leases SET state = 'EXPIRED',
            authority_epoch = authority_epoch + 1 WHERE state = 'ACTIVE'""")
        connection.execute("""UPDATE room_participation_sessions SET state = 'ABANDONED'
            WHERE state = 'ACTIVE'""")
        connection.execute("""UPDATE recovery_probe_grants SET state = 'EXPIRED',
            revision = revision + 1 WHERE state IN ('GRANTED', 'CLAIMED')""")
        connection.execute(f"""UPDATE {TABLE_EFFECT_DELIVERIES} SET reconciliation_required = 1
            WHERE NOT EXISTS (SELECT 1 FROM {TABLE_EFFECT_RECEIPTS}
                              WHERE outbox_event_id = {TABLE_EFFECT_DELIVERIES}.event_id)""")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
