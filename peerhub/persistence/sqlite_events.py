import json
import sqlite3
from typing import Callable

from peerhub.core.protocol import EventEnvelope
from peerhub.events.contract import ConsumerOffset, EventLogRecord
from .sqlite_helpers import _json_text, _string_tuple, _optional_json_object  # pyright: ignore[reportPrivateUsage]


class SqliteEventRepository:
    """Provides methods for event_log and consumer_offsets."""

    def __init__(self, db_provider: Callable[[], sqlite3.Connection]) -> None:
        self._db = db_provider

    def append(self, envelope: EventEnvelope, appended_at: int) -> int:
        """Append one canonical event to the event log."""
        cursor = self._db().execute(
            """
            INSERT INTO event_log (
                event_id,
                protocol_major,
                protocol_minor,
                schema_version,
                correlation_id,
                occurred_at,
                event_kind,
                payload_json,
                request_id,
                round_id,
                evidence_refs_json,
                predecessor_digest,
                recovery_context_json,
                appended_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                envelope.event_id,
                envelope.protocol_major,
                envelope.protocol_minor,
                envelope.schema_version,
                envelope.correlation_id,
                envelope.occurred_at,
                envelope.kind,
                _json_text(envelope.payload),
                envelope.request_id,
                envelope.round_id,
                _json_text(envelope.evidence_refs),
                envelope.predecessor_digest,
                (
                    _json_text(envelope.recovery_context)
                    if envelope.recovery_context is not None
                    else None
                ),
                appended_at,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to obtain outbox_position after insert")
        return cursor.lastrowid

    def _record_from_row(self, row: sqlite3.Row) -> EventLogRecord:
        envelope = EventEnvelope(
            event_id=row["event_id"],
            protocol_major=row["protocol_major"],
            protocol_minor=row["protocol_minor"],
            schema_version=row["schema_version"],
            correlation_id=row["correlation_id"],
            occurred_at=row["occurred_at"],
            kind=row["event_kind"],
            payload=json.loads(row["payload_json"]),
            request_id=row["request_id"],
            round_id=row["round_id"],
            evidence_refs=_string_tuple(row["evidence_refs_json"]),
            predecessor_digest=row["predecessor_digest"],
            recovery_context=_optional_json_object(row["recovery_context_json"]),
        )
        return EventLogRecord(
            envelope=envelope,
            appended_at=row["appended_at"],
            outbox_position=row["outbox_position"],
        )

    def get(self, event_id: str) -> EventLogRecord | None:
        """Return one canonical event record."""
        row = self._db().execute(
            """
            SELECT *
            FROM event_log
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return None if row is None else self._record_from_row(row)

    def list(
        self,
        *,
        limit: int,
        after_position: int = 0,
    ) -> tuple[EventLogRecord, ...]:
        """Return matching events in canonical outbox order."""
        if type(after_position) is not int or after_position < 0:
            raise ValueError("after_position must be a nonnegative integer")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")

        rows = self._db().execute(
            """
            SELECT *
            FROM event_log
            WHERE outbox_position > ?
            ORDER BY outbox_position
            LIMIT ?
            """,
            (after_position, limit),
        ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def get_consumer_offset(self, consumer_id: str) -> ConsumerOffset | None:
        """Return the current offset for a consumer."""
        row = self._db().execute(
            """
            SELECT *
            FROM consumer_offsets
            WHERE consumer_id = ?
            """,
            (consumer_id,),
        ).fetchone()
        if row is None:
            return None
        return ConsumerOffset(
            consumer_id=row["consumer_id"],
            outbox_position=row["outbox_position"],
            event_id=row["event_id"],
            revision=row["revision"],
        )

    def add_consumer_offset(self, offset: ConsumerOffset) -> None:
        """Insert a new consumer offset."""
        self._db().execute(
            """
            INSERT INTO consumer_offsets (
                consumer_id,
                outbox_position,
                event_id,
                revision
            ) VALUES (?, ?, ?, ?)
            """,
            (
                offset.consumer_id,
                offset.outbox_position,
                offset.event_id,
                offset.revision,
            ),
        )

    def cas_update_consumer_offset(
        self,
        current: ConsumerOffset,
        updated: ConsumerOffset,
    ) -> bool:
        """Atomically update a consumer offset if the revision matches."""
        if current.consumer_id != updated.consumer_id:
            raise ValueError("consumer_id cannot change")
        if updated.revision <= current.revision:
            raise ValueError("revision must monotonically increase")

        cursor = self._db().execute(
            """
            UPDATE consumer_offsets
            SET outbox_position = ?, event_id = ?, revision = ?
            WHERE consumer_id = ? AND revision = ?
            """,
            (
                updated.outbox_position,
                updated.event_id,
                updated.revision,
                updated.consumer_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1
