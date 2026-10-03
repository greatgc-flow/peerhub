"""Pure schema-version contract (no I/O, no SQL): shared by the migration runner and read-only readers (Diag).

`SUPPORTED_SCHEMA_VERSION` is the newest Core schema this build understands; `migrations.CURRENT_VERSION` must equal it
(asserted at import). A database stamped with a newer version is refused, never guessed at or downgraded (TD-14, MIG-003).
"""
from __future__ import annotations

SUPPORTED_SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    """Stored schema is newer than this build supports (TD-14: rejected, never downgraded or repaired)."""


def future_schema_message(found: int, supported: int) -> str:
    return (f"database schema version {found} is newer than supported {supported}: nothing was changed. "
            f"Upgrade PeerHub to a build that supports schema {found}; automatic downgrade is not supported "
            f"(to keep using this older build, restore a backup taken before the upgrade)")
