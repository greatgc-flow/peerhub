from __future__ import annotations

import sqlite3
from typing import Callable

_INVARIANT_KIND = "governance.ratified-invariant-write-request"  # == invariant_requests.RATIFIED_INVARIANT_EFFECT_KIND


class ActivationError(Exception):
    """Raised when consensus V2 activation is refused or invalid."""

def activate_consensus_v2(
    connection: sqlite3.Connection,
    *,
    now: int,
    fault_hook: Callable[[str], None] | None = None
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Re-check under the write lock: only a complete pre-activation state
        # may proceed (already activated or inconsistent are both refused).
        state = read_activation_state(connection)
        if state != "pre_activation":
            raise ActivationError(f"Activation refused: state is {state}")

        if fault_hook: fault_hook("before_triggers")
        
        connection.execute("""
            CREATE TRIGGER consensus_v2_guard_insert
            BEFORE INSERT ON governed_targets
            FOR EACH ROW
            WHEN NEW.target_kind = 'consensus-round' OR json_extract(NEW.state_json, '$.kind') = 'consensus-round'
            BEGIN
                SELECT RAISE(ABORT, 'ABORT');
            END;
        """)

        connection.execute("""
            CREATE TRIGGER consensus_v2_guard_update
            BEFORE UPDATE ON governed_targets
            FOR EACH ROW
            WHEN OLD.target_kind = 'consensus-round' OR json_extract(OLD.state_json, '$.kind') = 'consensus-round'
              OR NEW.target_kind = 'consensus-round' OR json_extract(NEW.state_json, '$.kind') = 'consensus-round'
            BEGIN
                SELECT RAISE(ABORT, 'ABORT');
            END;
        """)

        connection.execute("""
            CREATE TRIGGER consensus_v2_guard_delete
            BEFORE DELETE ON governed_targets
            FOR EACH ROW
            WHEN OLD.target_kind = 'consensus-round' OR json_extract(OLD.state_json, '$.kind') = 'consensus-round'
            BEGIN
                SELECT RAISE(ABORT, 'ABORT');
            END;
        """)

        # Effect-claim fence (cx criterion 3): after activation only the V2
        # worker (owner prefix 'consensus-v2:') may claim consensus.* effects,
        # and the legacy worker prefix may never claim the ratified-invariant
        # effect (reserved for its exclusive materializer). Unclaimed rows
        # only: work already claimed before activation may still complete.
        connection.execute(f"""
            CREATE TRIGGER consensus_v2_guard_effect_claim
            BEFORE UPDATE OF claimed_by ON effect_deliveries
            FOR EACH ROW
            WHEN OLD.claimed_by IS NULL AND NEW.claimed_by IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM event_log e
                WHERE e.event_id = NEW.event_id
                  AND (
                    (json_extract(e.payload_json, '$.effect_kind') LIKE 'consensus.%'
                     AND NEW.claimed_by NOT LIKE 'consensus-v2:%')
                    OR
                    (json_extract(e.payload_json, '$.effect_kind') = '{_INVARIANT_KIND}'
                     AND NEW.claimed_by LIKE 'consensus-worker:%')
                  )
              )
            BEGIN
                SELECT RAISE(ABORT, 'ABORT');
            END;
        """)

        if fault_hook: fault_hook("before_epoch")
        
        connection.execute("""
            UPDATE workspace_identity 
            SET activation_epoch = activation_epoch + 1
        """)
        
        epoch_row = connection.execute("SELECT activation_epoch FROM workspace_identity").fetchone()
        epoch = epoch_row[0] if type(epoch_row) is tuple else epoch_row["activation_epoch"]
        
        if fault_hook: fault_hook("before_metadata")
        
        connection.execute("""
            UPDATE consensus_activation
            SET activated = 1,
                activation_epoch = ?,
                activated_at = ?
            WHERE singleton = 1
        """, (epoch, now))
        
        if fault_hook: fault_hook("before_commit")
        
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def read_activation_state(connection: sqlite3.Connection) -> str:
    triggers_query = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN ('consensus_v2_guard_insert', 'consensus_v2_guard_update', 'consensus_v2_guard_delete', 'consensus_v2_guard_effect_claim')"
    ).fetchall()
    trigger_names = {t[0] if type(t) is tuple else t["name"] for t in triggers_query}
    has_all_triggers = len(trigger_names) == 4
    has_no_triggers = len(trigger_names) == 0

    meta = connection.execute("SELECT activated, activation_epoch FROM consensus_activation WHERE singleton = 1").fetchone()
    if not meta:
        return "inconsistent"
    
    activated = meta[0] if type(meta) is tuple else meta["activated"]
    meta_epoch = meta[1] if type(meta) is tuple else meta["activation_epoch"]

    identity = connection.execute("SELECT activation_epoch FROM workspace_identity").fetchone()
    if not identity:
        current_epoch = None
    else:
        current_epoch = identity[0] if type(identity) is tuple else identity["activation_epoch"]

    if activated == 1:
        if has_all_triggers and meta_epoch is not None and meta_epoch == current_epoch:
            return "activated"
        return "inconsistent"
    elif activated == 0:
        if has_no_triggers and meta_epoch is None:
            return "pre_activation"
        return "inconsistent"
    
    return "inconsistent"
