"""Read-only presentation model for legacy ``lease-status``."""

from __future__ import annotations

from collections.abc import Mapping

from peerhub.core.protocol import JsonValue
from peerhub.dispatch.service import DispatchService
from peerhub.dispatch.tree_controller import verify_process_identity


def collect_lease_status(
    dispatch: DispatchService,
    *,
    now: int,
) -> tuple[Mapping[str, JsonValue], ...]:
    """Report active leases and their PID identity without changing lease state."""

    rows: list[Mapping[str, JsonValue]] = []
    for lease in dispatch.list_active_leases():
        identity = lease.fence.owner_process_birth_identity
        alive = "N/A"
        actual_creation_time: int | None = None
        if identity is not None:
            try:
                verified, actual_creation_time = verify_process_identity(
                    identity.pid, identity
                )
                alive = "YES" if verified and actual_creation_time > 0 else "NO"
            except Exception:
                alive = "ERR"

        expired = lease.heartbeat_expires_at < now
        # Legacy rendered every current lease as "open". Preserve the
        # canonical native lifecycle separately for JSON consumers.
        status = "open"
        if expired:
            status += " !"
        rows.append({
            "lease_id": lease.lease_id,
            "peer": lease.fence.owner_peer_id or lease.fence.owner_instance_id,
            "status": status,
            "lease_state": lease.state.value,
            "pid": identity.pid if identity is not None else None,
            "alive": alive,
            "expires_at": lease.heartbeat_expires_at,
            "heartbeat_at": lease.updated_at,
            "expired": expired,
            "process_creation_time": (
                identity.process_creation_time if identity is not None else None
            ),
            "actual_process_creation_time": actual_creation_time,
        })
    return tuple(rows)
