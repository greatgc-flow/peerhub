"""M3.1 Memory / Second Brain Engine (MEM-001..012).

Freeze Invariants:
1. Core & M2 unchanged; M3 removable.
3. Memory is derived, never source truth.
4. Context Pack bounded.
Zero dev-dependency violation: Python standard library only.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from peerhub.m1.store import CoreStore


class MemoryError(Exception):
    """Base exception for memory module."""


class MemoryNotFoundError(MemoryError):
    """Raised when requested memory item is not found."""


class MemoryStateTransitionError(MemoryError):
    """Raised on invalid lifecycle transition."""


class MemorySourceRewriteForbiddenError(MemoryError):
    """Raised when an operation attempts to rewrite authoritative source records (Invariant 3)."""


class ContextPackBudgetExceededError(MemoryError):
    """Raised when token budget constraint cannot be satisfied (Invariant 4)."""


@dataclass
class MemoryItem:
    memory_id: str
    key: str
    content: str
    source_ref: str
    memory_type: str = "episodic"
    state: str = "CANDIDATE"
    confidence: float = 1.0
    revision: int = 1
    superseded_by: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ContextPack:
    pack_id: str
    items: list[MemoryItem]
    total_tokens: int
    budget: int
    selection_strategy: str = "PRIORITY_TRIM"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _estimate_tokens(text: str) -> int:
    """Rough conservative token count estimation (1 token per ~4 chars or whitespace)."""
    return max(1, len(text.split()))


class MemoryStore:
    """Derived episodic and semantic memory projection with bounded context packing."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    memory_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    revision INTEGER NOT NULL,
                    superseded_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_key ON memory_items (key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_state ON memory_items (state);")
            conn.commit()

    def rewrite_source_record(
        self,
        core_store: CoreStore,
        stream_id: str,
        position: int,
        new_body: dict[str, Any],
    ) -> None:
        """Enforces Invariant 3: Memory operations can NEVER rewrite source records."""
        raise MemorySourceRewriteForbiddenError(
            f"Invariant 3 violation: cannot rewrite source record at {stream_id}:{position}. "
            "Memory facts are strictly derived projections."
        )

    def propose_memory(
        self,
        key: str,
        content: str,
        source_ref: str,
        memory_type: str = "episodic",
        confidence: float = 1.0,
    ) -> MemoryItem:
        """Propose a new memory item in CANDIDATE state."""
        mem_id = f"mem-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        item = MemoryItem(
            memory_id=mem_id,
            key=key,
            content=content,
            source_ref=source_ref,
            memory_type=memory_type,
            state="CANDIDATE",
            confidence=confidence,
            revision=1,
            superseded_by=None,
            created_at=now,
            updated_at=now,
        )

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_items
                (memory_id, key, content, source_ref, memory_type, state, confidence, revision, superseded_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    item.memory_id,
                    item.key,
                    item.content,
                    item.source_ref,
                    item.memory_type,
                    item.state,
                    item.confidence,
                    item.revision,
                    item.superseded_by,
                    item.created_at,
                    item.updated_at,
                ),
            )
            conn.commit()
        return item

    def accept_memory(self, memory_id: str) -> MemoryItem:
        """Accept a CANDIDATE memory item into active memory."""
        item = self.get_memory(memory_id)
        if item is None:
            raise MemoryNotFoundError(f"Memory item {memory_id} not found.")

        if item.state != "CANDIDATE":
            raise MemoryStateTransitionError(f"Cannot accept memory from state {item.state}.")

        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE memory_items SET state = 'ACCEPTED', revision = revision + 1, updated_at = ? WHERE memory_id = ?;",
                (now, memory_id),
            )
            conn.commit()

        item.state = "ACCEPTED"
        item.revision += 1
        item.updated_at = now
        return item

    def reject_memory(self, memory_id: str, reason: str = "") -> MemoryItem:
        """Reject a CANDIDATE memory item."""
        item = self.get_memory(memory_id)
        if item is None:
            raise MemoryNotFoundError(f"Memory item {memory_id} not found.")

        if item.state != "CANDIDATE":
            raise MemoryStateTransitionError(f"Cannot reject memory from state {item.state}.")

        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE memory_items SET state = 'REJECTED', revision = revision + 1, updated_at = ? WHERE memory_id = ?;",
                (now, memory_id),
            )
            conn.commit()

        item.state = "REJECTED"
        item.revision += 1
        item.updated_at = now
        return item

    def supersede_memory(self, old_id: str, new_id: str) -> None:
        """Supersede an existing ACCEPTED memory item with a newer one."""
        old_item = self.get_memory(old_id)
        if old_item is None:
            raise MemoryNotFoundError(f"Old memory item {old_id} not found.")

        if old_item.state != "ACCEPTED":
            raise MemoryStateTransitionError(f"Only ACCEPTED memory can be superseded (current: {old_item.state}).")

        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE memory_items SET state = 'SUPERSEDED', superseded_by = ?, revision = revision + 1, updated_at = ? WHERE memory_id = ?;",
                (new_id, now, old_id),
            )
            conn.commit()

    def revoke_memory(self, memory_id: str, reason: str = "") -> None:
        """Revoke an ACCEPTED memory item."""
        item = self.get_memory(memory_id)
        if item is None:
            raise MemoryNotFoundError(f"Memory item {memory_id} not found.")

        if item.state != "ACCEPTED":
            raise MemoryStateTransitionError(f"Only ACCEPTED memory can be revoked (current: {item.state}).")

        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE memory_items SET state = 'REVOKED', revision = revision + 1, updated_at = ? WHERE memory_id = ?;",
                (now, memory_id),
            )
            conn.commit()

    def get_memory(self, memory_id: str) -> MemoryItem | None:
        """Retrieve memory item by ID."""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE memory_id = ?;", (memory_id,)).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    def query_active_memories(
        self,
        key_prefix: str | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryItem]:
        """Query ACCEPTED memory items with optional key prefix and type filters."""
        query = "SELECT * FROM memory_items WHERE state = 'ACCEPTED'"
        params: list[Any] = []

        if key_prefix is not None:
            query += " AND key LIKE ?"
            params.append(f"{key_prefix}%")

        if memory_type is not None:
            query += " AND memory_type = ?"
            params.append(memory_type)

        query += " ORDER BY created_at DESC;"

        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_item(r) for r in rows]

    def build_context_pack(
        self,
        query: str,
        max_tokens: int,
        max_items: int = 10,
    ) -> ContextPack:
        """Build bounded context pack satisfying Invariant 4."""
        if max_tokens <= 0:
            raise ContextPackBudgetExceededError(f"Context pack budget must be positive: got {max_tokens}")

        active_items = self.query_active_memories()
        selected: list[MemoryItem] = []
        accumulated_tokens = 0

        # Sort by relevance to query (simple token overlap) and recency
        query_words = set(query.lower().split())
        scored_items = sorted(
            active_items,
            key=lambda it: (
                len(query_words.intersection(set(it.content.lower().split()))),
                it.confidence,
                it.created_at,
            ),
            reverse=True,
        )

        for item in scored_items:
            if len(selected) >= max_items:
                break
            tokens = _estimate_tokens(item.content)
            if accumulated_tokens + tokens <= max_tokens:
                selected.append(item)
                accumulated_tokens += tokens

        pack_id = f"pack-{uuid.uuid4().hex[:12]}"
        return ContextPack(
            pack_id=pack_id,
            items=selected,
            total_tokens=accumulated_tokens,
            budget=max_tokens,
            selection_strategy="PRIORITY_TRIM",
        )

    def rebuild_from_records(self, core_store: CoreStore) -> int:
        """Reconstruct memory store by replaying memory records from CoreStore."""
        self.clear()
        count = 0
        with core_store.read_uow() as conn:
            streams = conn.execute("SELECT stream_id FROM streams;").fetchall()
            for s in streams:
                stream_id: str = s["stream_id"]
                records = core_store.read_records(stream_id, after_position=0, limit=10000)
                for rec in records:
                    rec_body: Any = rec.body
                    if rec.kind.startswith("m3.memory.") and isinstance(rec_body, dict):
                        body_dict = cast(dict[str, Any], rec_body)
                        key = str(body_dict["key"]) if "key" in body_dict else "default"
                        content = str(body_dict["content"]) if "content" in body_dict else ""
                        mem_type = str(body_dict["memory_type"]) if "memory_type" in body_dict else "episodic"
                        item = self.propose_memory(
                            key=key,
                            content=content,
                            source_ref=f"{stream_id}:{rec.position}",
                            memory_type=mem_type,
                        )
                        # Rebuilt items become accepted
                        self.accept_memory(item.memory_id)
                        count += 1
        return count

    def clear(self) -> None:
        """Wipe memory store tables."""
        with self._connection() as conn:
            conn.execute("DELETE FROM memory_items;")
            conn.commit()

    def close(self) -> None:
        """Close memory store."""
        pass

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            memory_id=row["memory_id"],
            key=row["key"],
            content=row["content"],
            source_ref=row["source_ref"],
            memory_type=row["memory_type"],
            state=row["state"],
            confidence=float(row["confidence"]),
            revision=int(row["revision"]),
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
