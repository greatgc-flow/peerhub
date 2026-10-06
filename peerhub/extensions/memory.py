"""Derived memory lifecycle and deterministic bounded context packing.

Bind CoreStore/stream/author to make mutations append authoritative events before
updating the projection. Without that binding this is a projection-only API;
local edits are not claimed to survive a source rebuild.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, cast

from peerhub.core.models import Record
from peerhub.core.store import CoreStore
from peerhub.extensions.source_records import source_records


class MemoryError(Exception):
    """Base memory exception."""


class MemoryNotFoundError(MemoryError):
    pass


class MemoryStateTransitionError(MemoryError):
    pass


class MemorySourceRewriteForbiddenError(MemoryError):
    pass


class ContextPackBudgetExceededError(MemoryError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class ContextPack:
    pack_id: str
    items: list[MemoryItem]
    total_tokens: int
    budget: int
    selection_strategy: str = "PINNED_RELEVANCE_RECENCY"
    created_at: str = field(default_factory=_now)


def _estimate_tokens(text: str) -> int:
    """Conservative byte-token upper bound, not an invented vendor tokenizer count."""
    return max(1, len(text.encode("utf-8")))


class MemoryStore:
    def __init__(self, db_path: Path, *, core_store: CoreStore | None = None,
                 stream_id: str | None = None, author_peer_id: str | None = None) -> None:
        if (core_store is not None) != (stream_id is not None and author_peer_id is not None):
            raise ValueError("authoritative memory requires CoreStore, stream_id and author_peer_id together")
        if core_store is None and (stream_id is not None or author_peer_id is not None):
            raise ValueError("stream/author require an authoritative CoreStore")
        self.core_store, self.stream_id, self.author_peer_id = core_store, stream_id, author_peer_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_items (
                memory_id TEXT PRIMARY KEY, key TEXT NOT NULL, content TEXT NOT NULL,
                source_ref TEXT NOT NULL, memory_type TEXT NOT NULL, state TEXT NOT NULL,
                confidence REAL NOT NULL, revision INTEGER NOT NULL, superseded_by TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_key ON memory_items(key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_state ON memory_items(state)")
            conn.commit()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def rewrite_source_record(self, core_store: CoreStore, stream_id: str, position: int, new_body: dict[str, Any]) -> None:
        raise MemorySourceRewriteForbiddenError(f"cannot rewrite immutable source record {stream_id}:{position}")

    @staticmethod
    def _validate_item(item: MemoryItem) -> None:
        for value in (item.memory_id, item.key, item.source_ref, item.created_at, item.updated_at):
            if not isinstance(cast(object, value), str) or not value:
                raise MemoryStateTransitionError("memory identity, source and timestamps must be nonempty strings")
        if not isinstance(cast(object, item.content), str) or item.memory_type not in ("episodic", "semantic"):
            raise MemoryStateTransitionError("invalid memory content/type")
        if not isinstance(cast(object, item.confidence), (int, float)) or isinstance(item.confidence, bool) or not math.isfinite(item.confidence) or not 0 <= item.confidence <= 1:
            raise MemoryStateTransitionError("memory confidence must be finite in [0, 1]")
        if not isinstance(cast(object, item.revision), int) or isinstance(item.revision, bool) or item.revision <= 0:
            raise MemoryStateTransitionError("memory revision must be a positive integer")
        if item.state not in ("CANDIDATE", "ACCEPTED", "REJECTED", "SUPERSEDED", "REVOKED"):
            raise MemoryStateTransitionError("invalid memory state")
        if item.superseded_by is not None and (not isinstance(cast(object, item.superseded_by), str) or not item.superseded_by):
            raise MemoryStateTransitionError("invalid memory supersession target")
        for timestamp in (item.created_at, item.updated_at):
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise MemoryStateTransitionError("invalid memory timestamp") from exc
            if parsed.tzinfo is None:
                raise MemoryStateTransitionError("memory timestamps require a timezone")

    @staticmethod
    def _put(conn: sqlite3.Connection, item: MemoryItem) -> None:
        conn.execute("INSERT OR REPLACE INTO memory_items VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(asdict(item).values()))

    def _refresh(self) -> None:
        if self.core_store is not None:
            self.rebuild_from_records(self.core_store)

    def _commit(self, item: MemoryItem, action: str, expected_revision: int, reason: str = "") -> None:
        self._validate_item(item)
        if self.core_store is not None:
            def guard(conn: sqlite3.Connection, **scope: Any) -> None:
                row = conn.execute("""SELECT body_json FROM records WHERE stream_id=? AND kind LIKE 'm3.memory.%'
                    AND json_extract(body_json,'$.item.memory_id')=? ORDER BY position DESC LIMIT 1""",
                    (self.stream_id, item.memory_id)).fetchone()
                revision = json.loads(row[0])["item"]["revision"] if row else 0
                if revision != expected_revision:
                    raise MemoryStateTransitionError("authoritative memory revision changed; rebuild and retry")
                if item.superseded_by is not None:
                    replacement = conn.execute("""SELECT body_json FROM records WHERE stream_id=? AND kind LIKE 'm3.memory.%'
                        AND json_extract(body_json,'$.item.memory_id')=? ORDER BY position DESC LIMIT 1""",
                        (self.stream_id, item.superseded_by)).fetchone()
                    if replacement is None or json.loads(replacement[0])["item"]["state"] != "ACCEPTED":
                        raise MemoryStateTransitionError("authoritative supersession target is not ACCEPTED")
            self.core_store.append_record(stream_id=cast(str, self.stream_id), author_peer_id=cast(str, self.author_peer_id),
                kind=f"m3.memory.{action}", body={"schema_version": "1.0", "item": asdict(item), "reason": reason},
                idempotency_key=f"memory:{item.memory_id}:{item.revision}", created_at=item.updated_at, guard=guard)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision FROM memory_items WHERE memory_id=?", (item.memory_id,)).fetchone()
            current_revision = row[0] if row else 0
            if self.core_store is None and current_revision != expected_revision:
                raise MemoryStateTransitionError("projection revision changed")
            if current_revision >= item.revision:
                return  # a newer committed source event already reached this projection
            self._put(conn, item)
            conn.commit()

    def propose_memory(self, key: str, content: str, source_ref: str, memory_type: str = "episodic", confidence: float = 1.0) -> MemoryItem:
        self._refresh()
        now = _now()
        item = MemoryItem(f"mem-{uuid.uuid4().hex}", key, content, source_ref, memory_type, confidence=confidence,
                          created_at=now, updated_at=now)
        self._commit(item, "proposed", 0)
        return item

    def _transition(self, memory_id: str, state: str, action: str, reason: str = "", new_id: str | None = None) -> MemoryItem:
        self._refresh()
        old = self.get_memory(memory_id)
        if old is None:
            raise MemoryNotFoundError(memory_id)
        expected = "CANDIDATE" if state in ("ACCEPTED", "REJECTED") else "ACCEPTED"
        if old.state != expected:
            raise MemoryStateTransitionError(f"cannot transition {old.state} to {state}")
        if state == "SUPERSEDED":
            target = self.get_memory(cast(str, new_id))
            if target is None or target.state != "ACCEPTED" or target.memory_id == old.memory_id:
                raise MemoryStateTransitionError("supersession requires a distinct ACCEPTED replacement")
        item = replace(old, state=state, revision=old.revision + 1, superseded_by=new_id, updated_at=_now())
        self._commit(item, action, old.revision, reason)
        return item

    def accept_memory(self, memory_id: str) -> MemoryItem:
        return self._transition(memory_id, "ACCEPTED", "accepted")

    def reject_memory(self, memory_id: str, reason: str = "") -> MemoryItem:
        return self._transition(memory_id, "REJECTED", "rejected", reason)

    def supersede_memory(self, old_id: str, new_id: str) -> None:
        self._transition(old_id, "SUPERSEDED", "superseded", new_id=new_id)

    def revoke_memory(self, memory_id: str, reason: str = "") -> None:
        self._transition(memory_id, "REVOKED", "revoked", reason)

    def get_memory(self, memory_id: str) -> MemoryItem | None:
        self._refresh()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def query_active_memories(self, key_prefix: str | None = None, memory_type: str | None = None) -> list[MemoryItem]:
        self._refresh()
        query = "SELECT * FROM memory_items WHERE state='ACCEPTED'"
        params: list[Any] = []
        if key_prefix is not None:
            query += " AND substr(key,1,?)=?"
            params.extend((len(key_prefix), key_prefix))
        if memory_type is not None:
            query += " AND memory_type=?"
            params.append(memory_type)
        with self._connection() as conn:
            rows = conn.execute(query + " ORDER BY created_at DESC,memory_id ASC", params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def build_context_pack(self, query: str, max_tokens: int, max_items: int = 10, *,
                           pinned_ids: Iterable[str] = (), byte_limit: int | None = None) -> ContextPack:
        if not isinstance(cast(object, max_tokens), int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ContextPackBudgetExceededError("context budget must be a positive integer")
        if not isinstance(cast(object, max_items), int) or isinstance(max_items, bool) or max_items < 0:
            raise ContextPackBudgetExceededError("context item limit must be a nonnegative integer")
        if byte_limit is not None and (not isinstance(cast(object, byte_limit), int) or isinstance(byte_limit, bool) or byte_limit <= 0):
            raise ContextPackBudgetExceededError("context byte limit must be a positive integer")
        pinned = set(pinned_ids)
        words = set(query.lower().split())
        candidates = self.query_active_memories()
        # Stable identity is the final tie-break, independent of SQLite insertion order.
        candidates.sort(key=lambda it: it.memory_id)
        candidates.sort(key=lambda it: (it.memory_id in pinned,
            len(words.intersection(it.content.lower().split())), it.created_at), reverse=True)
        selected: list[MemoryItem] = []
        tokens = used_bytes = 0
        for item in candidates:
            if len(selected) >= max_items:
                break
            separator = 1 if selected else 0
            cost = _estimate_tokens(item.content) + separator
            size = len(item.content.encode("utf-8")) + separator
            if tokens + cost <= max_tokens and (byte_limit is None or used_bytes + size <= byte_limit):
                selected.append(item)
                tokens += cost
                used_bytes += size
        identity = json.dumps([query, max_tokens, max_items, byte_limit, sorted(pinned), [asdict(it) for it in selected]],
                              sort_keys=True, ensure_ascii=False).encode("utf-8")
        return ContextPack("pack-" + hashlib.sha256(identity).hexdigest(), selected, tokens, max_tokens)

    def rebuild_from_records(self, core_store: CoreStore) -> int:
        """Strict lifecycle replay; invalid source rolls back the old projection unchanged."""
        items: dict[str, MemoryItem] = {}
        origins: dict[str, str] = {}
        count = 0
        for rec in source_records(core_store):
            if not rec.kind.startswith("m3.memory."):
                continue
            self._replay(items, origins, rec)
            count += 1
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM memory_items")
            for item in items.values():
                self._put(conn, item)
            conn.commit()
        return count

    @classmethod
    def _replay(cls, items: dict[str, MemoryItem], origins: dict[str, str], rec: Record) -> None:
        body: Any = rec.body
        if not isinstance(body, dict):
            raise MemoryStateTransitionError(f"unsupported memory event schema at {rec.stream_id}:{rec.position}")
        body = cast("dict[str, Any]", body)
        if body.get("schema_version") != "1.0" or not isinstance(body.get("item"), dict):
            raise MemoryStateTransitionError(f"unsupported memory event schema at {rec.stream_id}:{rec.position}")
        fields = body["item"]
        if set(fields) != set(MemoryItem.__dataclass_fields__):
            raise MemoryStateTransitionError("memory event must contain a complete explicit item snapshot")
        item = MemoryItem(**fields)
        cls._validate_item(item)
        if item.updated_at != rec.created_at:
            raise MemoryStateTransitionError("memory event timestamp differs from its snapshot")
        old = items.get(item.memory_id)
        if rec.kind == "m3.memory.proposed":
            if old is not None or item.state != "CANDIDATE" or item.revision != 1 or item.superseded_by is not None or item.created_at != rec.created_at:
                raise MemoryStateTransitionError("invalid or duplicate memory proposal")
            origins[item.memory_id] = rec.stream_id
        else:
            transitions = {"accepted": ("CANDIDATE", "ACCEPTED"), "rejected": ("CANDIDATE", "REJECTED"),
                           "superseded": ("ACCEPTED", "SUPERSEDED"), "revoked": ("ACCEPTED", "REVOKED")}
            transition = transitions.get(rec.kind.removeprefix("m3.memory."))
            if old is None or transition != (old.state, item.state) or origins[item.memory_id] != rec.stream_id or item.revision != old.revision + 1:
                raise MemoryStateTransitionError("invalid memory lifecycle event")
            for name in ("key", "content", "source_ref", "memory_type", "confidence", "created_at"):
                if getattr(item, name) != getattr(old, name):
                    raise MemoryStateTransitionError("memory transition rewrites immutable proposal fields")
            if item.state == "SUPERSEDED":
                target = items.get(item.superseded_by or "")
                if target is None or target.state != "ACCEPTED" or target.memory_id == item.memory_id or origins.get(target.memory_id) != rec.stream_id:
                    raise MemoryStateTransitionError("invalid memory supersession target")
            elif item.superseded_by is not None:
                raise MemoryStateTransitionError("unexpected memory supersession link")
        items[item.memory_id] = item

    def clear(self) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM memory_items")
            conn.commit()

    def close(self) -> None:
        pass

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(**dict(row))
