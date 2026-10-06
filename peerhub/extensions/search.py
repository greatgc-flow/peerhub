"""M3.0 Search / Retrieval Engine (SRC-001..012).

Freeze Invariants:
1. Core & M2 unchanged; M3 removable.
2. Search rebuildable + provenance always.
Zero dev-dependency violation: Python standard library only.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from peerhub.core.models import Record
from peerhub.core.store import CoreStore
from peerhub.extensions.source_records import source_records


class SearchError(Exception):
    """Base exception for search module."""


class SearchIndexCorruptedError(SearchError):
    """Raised when search index database or FTS5 table is damaged or corrupted."""


class SearchProvenanceMissingError(SearchError):
    """Raised when search result lacks required source provenance tracking."""


class SearchQuerySyntaxError(SearchError):
    """Raised on invalid query syntax."""


class SearchIndexRebuildOrderError(SearchError):
    """Raised when index rebuild is attempted without authoritative CoreStore."""


@dataclass
class SearchResult:
    doc_id: str
    doc_type: str
    source_ref: str
    snippet: str
    score: float
    retrieval_method: str
    source_watermark: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


def _sanitize_fts_query(query: str) -> str:
    """Sanitize user input query string to avoid FTS5 syntax errors while preserving prefix search."""
    tokens = re.findall(r'[\w\-]+|\*|"[^"]*"', query)
    if not tokens:
        return '""'
    clean_tokens: list[str] = []
    for t in tokens:
        if t.startswith('"') and t.endswith('"') and len(t) > 2:
            clean_tokens.append(t)
        elif t.endswith("*") and len(t) > 1:
            clean_tokens.append(re.sub(r'[^\w\-]', '', t[:-1]) + "*")
        else:
            cleaned = re.sub(r'[^\w\-]', '', t)
            if cleaned:
                clean_tokens.append(f'"{cleaned}"')
    return " ".join(clean_tokens) if clean_tokens else '""'


class SearchIndex:
    """SQLite FTS5-based derived search projection with mandatory provenance."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._transaction: ContextVar[sqlite3.Connection | None] = ContextVar("search_transaction", default=None)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def clear_path(cls, db_path: Path) -> None:
        """Helper to cleanly wipe a database file on disk."""
        path = Path(db_path)
        for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    @contextmanager
    def _connection(self):
        transaction = self._transaction.get()
        if transaction is not None:
            yield transaction
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _commit(self, conn: sqlite3.Connection) -> None:
        if self._transaction.get() is None:
            conn.commit()

    def _init_db(self) -> None:
        try:
            with self._connection() as conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS doc_meta (
                        doc_id TEXT PRIMARY KEY,
                        doc_type TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        source_watermark TEXT NOT NULL,
                        title TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        indexed_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
                        doc_id UNINDEXED,
                        doc_type UNINDEXED,
                        source_ref UNINDEXED,
                        source_watermark UNINDEXED,
                        title,
                        body,
                        metadata_text,
                        tokenize = 'unicode61'
                    );
                    """
                )
                conn.commit()
        except sqlite3.DatabaseError as exc:
            raise SearchIndexCorruptedError(f"Corrupted search index database on init: {exc}") from exc

    def verify_integrity(self) -> bool:
        """Verify SQLite database and FTS5 table integrity."""
        try:
            with self._connection() as conn:
                row = conn.execute("PRAGMA integrity_check;").fetchone()
                if not row or row[0] != "ok":
                    raise SearchIndexCorruptedError(f"Integrity check failed: {row[0] if row else 'empty'}")
                conn.execute("SELECT count(*) FROM fts_documents").fetchone()
                return True
        except sqlite3.DatabaseError as exc:
            raise SearchIndexCorruptedError(f"Corrupted search index database: {exc}") from exc

    def index_record(self, record: Record) -> None:
        """Index a CoreStore record into search projection."""
        doc_id = record.record_id
        doc_type = "record"
        source_ref = f"{record.stream_id}:{record.position}"
        source_watermark = f"pos-{record.position}"
        title = f"Record {record.kind} on {record.stream_id}"
        body_text = json.dumps(record.body, ensure_ascii=False) if record.body is not None else ""
        meta_dict: dict[str, Any] = {
            "kind": record.kind,
            "author_peer_id": record.author_peer_id,
            "stream_id": record.stream_id,
            "position": record.position,
        }
        meta_json = json.dumps(meta_dict, ensure_ascii=False)
        indexed_at = datetime.now(timezone.utc).isoformat()

        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO doc_meta
                (doc_id, doc_type, source_ref, source_watermark, title, metadata_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, meta_json, indexed_at),
            )
            conn.execute("DELETE FROM fts_documents WHERE doc_id = ?;", (doc_id,))
            conn.execute(
                """
                INSERT INTO fts_documents (doc_id, doc_type, source_ref, source_watermark, title, body, metadata_text)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, body_text, meta_json),
            )
            self._commit(conn)

    def index_artifact(self, digest: str, metadata: dict[str, Any], snippet: str = "") -> None:
        """Index an artifact's metadata and content snippet into search projection."""
        doc_id = f"art-{digest}"
        doc_type = "artifact"
        source_ref = f"artifact:{digest}"
        source_watermark = f"sha256-{digest}"
        title = str(metadata.get("name") or f"Artifact {digest[:8]}")
        meta_json = json.dumps(metadata, ensure_ascii=False)
        indexed_at = datetime.now(timezone.utc).isoformat()

        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO doc_meta
                (doc_id, doc_type, source_ref, source_watermark, title, metadata_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, meta_json, indexed_at),
            )
            conn.execute("DELETE FROM fts_documents WHERE doc_id = ?;", (doc_id,))
            conn.execute(
                """
                INSERT INTO fts_documents (doc_id, doc_type, source_ref, source_watermark, title, body, metadata_text)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, snippet, meta_json),
            )
            self._commit(conn)

    def index_skill(self, skill_id: str, name: str, description: str, tags: list[str], *, source_watermark: str | None = None) -> None:
        """Index a skill's name, description, and tags into search projection."""
        doc_id = f"skl-{skill_id}"
        doc_type = "skill"
        source_ref = f"skill:{skill_id}"
        source_watermark = source_watermark or f"id-{skill_id}"
        title = name
        body_text = f"{description} " + " ".join(tags)
        meta_dict: dict[str, Any] = {"skill_id": skill_id, "name": name, "tags": tags}
        meta_json = json.dumps(meta_dict, ensure_ascii=False)
        indexed_at = datetime.now(timezone.utc).isoformat()

        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO doc_meta
                (doc_id, doc_type, source_ref, source_watermark, title, metadata_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, meta_json, indexed_at),
            )
            conn.execute("DELETE FROM fts_documents WHERE doc_id = ?;", (doc_id,))
            conn.execute(
                """
                INSERT INTO fts_documents (doc_id, doc_type, source_ref, source_watermark, title, body, metadata_text)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, doc_type, source_ref, source_watermark, title, body_text, meta_json),
            )
            self._commit(conn)

    def search(self, query: str, doc_type: str | None = None, limit: int = 10) -> list[SearchResult]:
        """Perform lexical FTS search with optional doc_type filtering."""
        if not isinstance(cast(object, limit), int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("search limit must be a positive integer")
        fts_query = _sanitize_fts_query(query)
        if fts_query == '""':
            return []

        type_clause = "AND fts.doc_type = ?" if doc_type is not None else ""
        sql = f"""
            SELECT
                fts.doc_id,
                fts.doc_type,
                fts.source_ref,
                fts.source_watermark,
                fts.title,
                snippet(fts_documents, 5, '<b>', '</b>', '...', 16) AS snip,
                bm25(fts_documents) AS rank_score,
                m.metadata_json
            FROM fts_documents fts
            JOIN doc_meta m ON fts.doc_id = m.doc_id
            WHERE fts_documents MATCH ? {type_clause}
            ORDER BY rank_score ASC, fts.doc_id ASC
            LIMIT ?;
        """
        params: list[Any] = [fts_query]
        if doc_type is not None:
            params.append(doc_type)
        params.append(limit)

        try:
            with self._connection() as conn:
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.DatabaseError as exc:
            raise SearchIndexCorruptedError(f"Search index query failed: {exc}") from exc

        results: list[SearchResult] = []
        for r in rows:
            meta = cast(dict[str, Any], json.loads(r["metadata_json"])) if r["metadata_json"] else {}
            # Mandatory provenance validation
            if not r["source_ref"] or not r["source_watermark"]:
                raise SearchProvenanceMissingError(f"Missing provenance for search hit {r['doc_id']}")

            # Convert bm25 (lower is better, typically negative in SQLite FTS5) to positive relevance score
            raw_rank = float(r["rank_score"])
            norm_score = max(0.1, round(abs(raw_rank), 3))

            results.append(
                SearchResult(
                    doc_id=r["doc_id"],
                    doc_type=r["doc_type"],
                    source_ref=r["source_ref"],
                    snippet=r["snip"] or r["title"],
                    score=norm_score,
                    retrieval_method="LEXICAL_FTS",
                    source_watermark=r["source_watermark"],
                    metadata=meta,
                )
            )
        return results

    def rebuild_from_sources(
        self,
        core_store: CoreStore | None,
        artifact_store: Any = None,
        skill_catalog: Any = None,
    ) -> int:
        """Full index rebuild from authoritative CoreStore and optional stores (Invariant 2)."""
        if core_store is None:
            raise SearchIndexRebuildOrderError("Rebuild requires valid authoritative CoreStore instance.")

        indexed_count = 0
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            token = self._transaction.set(conn)
            try:
                conn.execute("DELETE FROM fts_documents")
                conn.execute("DELETE FROM doc_meta")
                for rec in source_records(core_store):
                    self.index_record(rec)
                    indexed_count += 1
                if artifact_store is not None:
                    for digest in sorted(artifact_store.list_all_digests()):
                        data = artifact_store.read_bytes(digest)
                        self.index_artifact(digest, {"digest": digest, "size_bytes": len(data)},
                                            data[:65536].decode("utf-8", "replace"))
                        indexed_count += 1
                if skill_catalog is not None:
                    for skill in skill_catalog.list_skills():
                        skill_catalog.verify_skill_integrity(skill.skill_id)
                        self.index_skill(skill.skill_id, skill.name, skill.description, skill.tags,
                                         source_watermark=f"sha256-{skill.tree_digest}:rev-{skill.revision}")
                        indexed_count += 1
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                self._transaction.reset(token)
        return indexed_count

    def clear(self) -> None:
        """Wipes and recreates search projection cleanly."""
        self._init_db()
        with self._connection() as conn:
            conn.execute("DELETE FROM fts_documents")
            conn.execute("DELETE FROM doc_meta")
            conn.commit()

    def close(self) -> None:
        """Close search index."""
        # SQLite connection is per-operation; no persistent connection lock held.
        pass
