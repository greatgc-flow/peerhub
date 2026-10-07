"""M2.2 Work Projection Implementation.

Adheres strictly to M2_2_WORK_CONTRACT.md and architecture invariants:
- Invariant 1: Core remains Peer/Stream/Record/Offset.
- Invariant 2: Core imports Extension = 0.
- Invariant 6: Work truth only ordered Records.
- Invariant 7: Work projection fully rebuildable from Core Stream records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sqlite3
from typing import Any, ClassVar, Iterable, cast

from peerhub.core.models import Record
from peerhub.core.store import CoreStore


class WorkError(Exception):
    """Base exception for Work Projection errors."""


class WorkNotFoundError(WorkError, LookupError):
    """Referenced work_id does not exist in projection or stream."""


class WorkRevisionConflictError(WorkError, RuntimeError):
    """Mutation expected_revision does not match current projection revision."""


class WorkAlreadyExistsError(WorkError, ValueError):
    """Attempt to create a work item with an already registered work_id."""


class ForbiddenTransitionError(WorkError, RuntimeError):
    """Attempting an invalid state machine transition."""


class InvalidWorkPayloadError(WorkError, ValueError):
    """Work payload fails schema constraints."""


@dataclass(frozen=True)
class WorkItem:
    """Represents a projected view of a work item derived from Stream records."""

    work_id: str
    stream_id: str
    state: str
    revision: int
    title: str
    spec: dict[str, Any]
    artifacts: list[str] = field(default_factory=lambda: cast(list[str], []))
    checkpoint: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""


class WorkProjection:
    """Read-model projection engine reducing Core Stream records into work state."""

    ALLOWED_TRANSITIONS: ClassVar[set[tuple[str, str]]] = {
        ("OPEN", "ACTIVE"),
        ("OPEN", "CANCELLED"),
        ("ACTIVE", "PAUSED"),
        ("PAUSED", "ACTIVE"),
        ("ACTIVE", "BLOCKED"),
        ("BLOCKED", "ACTIVE"),
        ("ACTIVE", "DONE"),
        ("ACTIVE", "FAILED"),
        ("ACTIVE", "CANCELLED"),
        ("PAUSED", "CANCELLED"),
        ("BLOCKED", "CANCELLED"),
    }

    TERMINAL_STATES: ClassVar[set[str]] = {"DONE", "FAILED", "CANCELLED"}

    def __init__(self, db_path: Path | str, store: CoreStore) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = store
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS ext_work_items (
                work_id TEXT PRIMARY KEY,
                stream_id TEXT NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                title TEXT NOT NULL,
                spec_json TEXT NOT NULL DEFAULT '{}',
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                checkpoint_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _resolve_author(self, stream_id: str, author_peer_id: str | None = None) -> str:
        if author_peer_id:
            return author_peer_id
        stream = self.store.get_stream(stream_id)
        if stream is not None and stream.members:
            return stream.members[0]
        peer = self.store.get_peer("system")
        if peer is not None:
            return peer.peer_id
        raise WorkError(f"No valid author peer could be resolved for stream {stream_id!r}")

    def create_work(
        self,
        stream_id: str,
        work_id: str,
        title: str,
        spec: dict[str, Any] | None = None,
        initial_state: str = "OPEN",
        author_peer_id: str | None = None,
    ) -> WorkItem:
        """Create a new work item by appending an authoritative record and updating projection."""
        if (not isinstance(cast(object, work_id), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", work_id)
                or not isinstance(cast(object, title), str) or not title
                or initial_state != "OPEN" or (spec is not None and not isinstance(cast(object, spec), dict))):
            raise InvalidWorkPayloadError("Work requires an ASCII identity, title, object spec, and OPEN initial state")
        try:
            self.get_work(work_id)
            raise WorkAlreadyExistsError(f"Work item {work_id!r} already exists")
        except WorkNotFoundError:
            pass

        spec_data = spec or {}
        now = self._now_iso()

        payload = {
            "work_id": work_id,
            "title": title,
            "spec": spec_data,
            "state": initial_state,
            "revision": 1,
            "created_at": now,
        }

        # Authoritative Core record append
        self.store.append_record(
            stream_id=stream_id,
            author_peer_id=self._resolve_author(stream_id, author_peer_id),
            kind="m2.work.created",
            body=payload,
            idempotency_key=f"work-created-{work_id}",
            created_at=now,
        )

        work = WorkItem(
            work_id=work_id,
            stream_id=stream_id,
            state=initial_state,
            revision=1,
            title=title,
            spec=spec_data,
            artifacts=[],
            checkpoint=None,
            created_at=now,
            updated_at=now,
        )

        self._save_work_projection(work)
        return work

    def transition_work(
        self,
        work_id: str,
        expected_revision: int,
        target_state: str,
        reason: str | None = None,
        author_peer_id: str | None = None,
    ) -> WorkItem:
        """Transition work item state with CAS optimistic revision gating."""
        current = self.get_work(work_id)

        if current.state in self.TERMINAL_STATES:
            raise ForbiddenTransitionError(
                f"Cannot transition work {work_id!r} from terminal state {current.state!r}"
            )

        if (current.state, target_state) not in self.ALLOWED_TRANSITIONS:
            raise ForbiddenTransitionError(
                f"Forbidden transition from {current.state!r} to {target_state!r} for work {work_id!r}"
            )

        if current.revision != expected_revision:
            raise WorkRevisionConflictError(
                f"Work {work_id!r} revision conflict: expected {expected_revision}, got {current.revision}"
            )

        now = self._now_iso()
        new_rev = current.revision + 1

        payload = {
            "work_id": work_id,
            "from_state": current.state,
            "to_state": target_state,
            "expected_revision": expected_revision,
            "new_revision": new_rev,
            "reason": reason,
            "transitioned_at": now,
        }

        # Authoritative Core record append
        self.store.append_record(
            stream_id=current.stream_id,
            author_peer_id=self._resolve_author(current.stream_id, author_peer_id),
            kind="m2.work.transitioned",
            body=payload,
            idempotency_key=f"work-trans-{work_id}-r{new_rev}",
            created_at=now,
        )

        updated = WorkItem(
            work_id=current.work_id,
            stream_id=current.stream_id,
            state=target_state,
            revision=new_rev,
            title=current.title,
            spec=current.spec,
            artifacts=list(current.artifacts),
            checkpoint=current.checkpoint,
            created_at=current.created_at,
            updated_at=now,
        )

        self._save_work_projection(updated)
        return updated

    def checkpoint_work(
        self,
        work_id: str,
        expected_revision: int,
        checkpoint_data: dict[str, Any],
        author_peer_id: str | None = None,
    ) -> WorkItem:
        """Save checkpoint data on active work item and advance revision."""
        if not isinstance(cast(object, checkpoint_data), dict):
            raise InvalidWorkPayloadError("Checkpoint must be a JSON object")
        current = self.get_work(work_id)

        if current.state in self.TERMINAL_STATES:
            raise ForbiddenTransitionError(
                f"Cannot checkpoint work {work_id!r} in terminal state {current.state!r}"
            )

        if current.revision != expected_revision:
            raise WorkRevisionConflictError(
                f"Work {work_id!r} revision conflict: expected {expected_revision}, got {current.revision}"
            )

        now = self._now_iso()
        new_rev = current.revision + 1

        payload = {
            "work_id": work_id,
            "checkpoint": checkpoint_data,
            "expected_revision": expected_revision,
            "new_revision": new_rev,
            "checkpointed_at": now,
        }

        self.store.append_record(
            stream_id=current.stream_id,
            author_peer_id=self._resolve_author(current.stream_id, author_peer_id),
            kind="m2.work.checkpointed",
            body=payload,
            idempotency_key=f"work-chk-{work_id}-r{new_rev}",
            created_at=now,
        )

        updated = WorkItem(
            work_id=current.work_id,
            stream_id=current.stream_id,
            state=current.state,
            revision=new_rev,
            title=current.title,
            spec=current.spec,
            artifacts=list(current.artifacts),
            checkpoint=checkpoint_data,
            created_at=current.created_at,
            updated_at=now,
        )

        self._save_work_projection(updated)
        return updated

    def link_artifact(
        self,
        work_id: str,
        expected_revision: int,
        digest: str,
        author_peer_id: str | None = None,
    ) -> WorkItem:
        """Link an immutable CAS artifact digest to a work item."""
        if not isinstance(cast(object, digest), str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InvalidWorkPayloadError("Artifact reference must be a lowercase SHA-256 digest")
        current = self.get_work(work_id)

        if current.revision != expected_revision:
            raise WorkRevisionConflictError(
                f"Work {work_id!r} revision conflict: expected {expected_revision}, got {current.revision}"
            )

        now = self._now_iso()
        new_rev = current.revision + 1
        new_artifacts = list(current.artifacts)
        if digest not in new_artifacts:
            new_artifacts.append(digest)

        payload = {
            "work_id": work_id,
            "digest": digest,
            "expected_revision": expected_revision,
            "new_revision": new_rev,
            "linked_at": now,
        }

        self.store.append_record(
            stream_id=current.stream_id,
            author_peer_id=self._resolve_author(current.stream_id, author_peer_id),
            kind="m2.work.artifact_linked",
            body=payload,
            idempotency_key=f"work-art-{work_id}-r{new_rev}",
            created_at=now,
        )

        updated = WorkItem(
            work_id=current.work_id,
            stream_id=current.stream_id,
            state=current.state,
            revision=new_rev,
            title=current.title,
            spec=current.spec,
            artifacts=new_artifacts,
            checkpoint=current.checkpoint,
            created_at=current.created_at,
            updated_at=now,
        )

        self._save_work_projection(updated)
        return updated

    def get_work(self, work_id: str) -> WorkItem:
        """Fetch a work item projection from SQLite by its work_id."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT work_id, stream_id, state, revision, title, spec_json,
                       artifacts_json, checkpoint_json, created_at, updated_at
                FROM ext_work_items WHERE work_id = ?
                """,
                (work_id,),
            ).fetchone()

        if row is None:
            raise WorkNotFoundError(f"Work item {work_id!r} not found in projection")

        return WorkItem(
            work_id=row[0],
            stream_id=row[1],
            state=row[2],
            revision=row[3],
            title=row[4],
            spec=json.loads(row[5]),
            artifacts=json.loads(row[6]),
            checkpoint=json.loads(row[7]) if row[7] else None,
            created_at=row[8],
            updated_at=row[9],
        )

    def list_work(self) -> list[WorkItem]:
        """Return the current flat work projection; no synthetic parent hierarchy."""
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute("SELECT work_id FROM ext_work_items ORDER BY work_id")]
        return [self.get_work(work_id) for work_id in ids]

    def _save_work_projection(self, work: WorkItem, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with sqlite3.connect(self.db_path) as own:
                self._save_work_projection(work, own)
            return
        conn.execute(
            """
            INSERT INTO ext_work_items (
                work_id, stream_id, state, revision, title, spec_json,
                artifacts_json, checkpoint_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET
                stream_id = excluded.stream_id,
                state = excluded.state,
                revision = excluded.revision,
                title = excluded.title,
                spec_json = excluded.spec_json,
                artifacts_json = excluded.artifacts_json,
                checkpoint_json = excluded.checkpoint_json,
                updated_at = excluded.updated_at
            """,
            (
                work.work_id,
                work.stream_id,
                work.state,
                work.revision,
                work.title,
                json.dumps(work.spec),
                json.dumps(work.artifacts),
                json.dumps(work.checkpoint) if work.checkpoint is not None else None,
                work.created_at,
                work.updated_at,
            ),
        )

    def rebuild_projection(self, records: Iterable[Record]) -> int:
        """Wipe projection tables and rebuild state entirely from ordered Core records."""
        self._init_db()

        items: dict[str, dict[str, Any]] = {}
        applied_count = 0
        positions: dict[str, int] = {}

        for record in records:
            if record.position <= positions.get(record.stream_id, 0):
                continue
            positions[record.stream_id] = record.position
            raw_body: object = record.body
            if not isinstance(raw_body, dict):
                continue

            p = cast(dict[str, object], raw_body)
            kind = record.kind

            if kind in {"m2.work.transitioned", "m2.work.checkpointed", "m2.work.artifact_linked"}:
                wid = p.get("work_id")
                if not isinstance(wid, str) or wid not in items:
                    continue
                existing = items[wid]
                expected, new_revision = p.get("expected_revision"), p.get("new_revision")
                if (record.stream_id != existing["stream_id"] or type(expected) is not int
                        or type(new_revision) is not int or expected != existing["revision"]
                        or new_revision != expected + 1):
                    continue
                if kind == "m2.work.transitioned" and (
                        not isinstance(p.get("to_state"), str) or p.get("from_state") != existing["state"]
                        or (existing["state"], p.get("to_state")) not in self.ALLOWED_TRANSITIONS):
                    continue
                if kind == "m2.work.checkpointed" and (
                        existing["state"] in self.TERMINAL_STATES or not isinstance(p.get("checkpoint"), dict)):
                    continue
                if kind == "m2.work.artifact_linked" and (
                        not isinstance(p.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", cast(str, p["digest"]))):
                    continue

            if kind == "m2.work.created":
                raw_wid = p.get("work_id")
                if (not isinstance(raw_wid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", raw_wid)
                        or raw_wid in items or p.get("state") != "OPEN" or type(p.get("revision")) is not int
                        or p.get("revision") != 1 or not isinstance(p.get("title"), str) or not p.get("title")
                        or not isinstance(p.get("spec"), dict)):
                    continue
                work_id: str = raw_wid
                raw_state = p.get("state")
                state_str: str = raw_state if isinstance(raw_state, str) else "OPEN"
                raw_rev = p.get("revision")
                rev_num: int = raw_rev if isinstance(raw_rev, int) else 1
                raw_title = p.get("title")
                title_str: str = raw_title if isinstance(raw_title, str) else ""
                raw_spec = p.get("spec")
                spec_dict: dict[str, Any] = cast(dict[str, Any], raw_spec) if isinstance(raw_spec, dict) else {}
                raw_created = p.get("created_at")
                created_str: str = raw_created if isinstance(raw_created, str) else record.created_at

                items[work_id] = {
                    "work_id": work_id,
                    "stream_id": record.stream_id,
                    "state": state_str,
                    "revision": rev_num,
                    "title": title_str,
                    "spec": spec_dict,
                    "artifacts": cast(list[str], []),
                    "checkpoint": None,
                    "created_at": created_str,
                    "updated_at": created_str,
                }
                applied_count += 1

            elif kind == "m2.work.transitioned":
                raw_wid = p.get("work_id")
                if not isinstance(raw_wid, str) or raw_wid not in items:
                    continue
                it = items[raw_wid]
                to_state = p.get("to_state")
                if isinstance(to_state, str):
                    it["state"] = to_state
                    raw_new_rev = p.get("new_revision")
                    it["revision"] = raw_new_rev if isinstance(raw_new_rev, int) else cast(int, it["revision"]) + 1
                    raw_trans_at = p.get("transitioned_at")
                    it["updated_at"] = raw_trans_at if isinstance(raw_trans_at, str) else record.created_at
                    applied_count += 1

            elif kind == "m2.work.checkpointed":
                raw_wid = p.get("work_id")
                if not isinstance(raw_wid, str) or raw_wid not in items:
                    continue
                it = items[raw_wid]
                raw_chk = p.get("checkpoint")
                it["checkpoint"] = cast(dict[str, Any], raw_chk) if isinstance(raw_chk, dict) else None
                raw_new_rev = p.get("new_revision")
                it["revision"] = raw_new_rev if isinstance(raw_new_rev, int) else cast(int, it["revision"]) + 1
                raw_chk_at = p.get("checkpointed_at")
                it["updated_at"] = raw_chk_at if isinstance(raw_chk_at, str) else record.created_at
                applied_count += 1

            elif kind == "m2.work.artifact_linked":
                raw_wid = p.get("work_id")
                raw_digest = p.get("digest")
                if not isinstance(raw_wid, str) or not isinstance(raw_digest, str) or raw_wid not in items:
                    continue
                it = items[raw_wid]
                artifacts_list = cast(list[str], it["artifacts"])
                if raw_digest not in artifacts_list:
                    artifacts_list.append(raw_digest)
                raw_new_rev = p.get("new_revision")
                it["revision"] = raw_new_rev if isinstance(raw_new_rev, int) else cast(int, it["revision"]) + 1
                raw_linked_at = p.get("linked_at")
                it["updated_at"] = raw_linked_at if isinstance(raw_linked_at, str) else record.created_at
                applied_count += 1

        # Replace the projection atomically: a failure while reading `records` above never reaches the DELETE, and a
        # failure here rolls the whole replacement back, so readers never see an empty or half-built projection.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM ext_work_items;")
            for it in items.values():
                work = WorkItem(
                    work_id=cast(str, it["work_id"]),
                    stream_id=cast(str, it["stream_id"]),
                    state=cast(str, it["state"]),
                    revision=cast(int, it["revision"]),
                    title=cast(str, it["title"]),
                    spec=cast(dict[str, Any], it["spec"]),
                    artifacts=cast(list[str], it["artifacts"]),
                    checkpoint=cast(dict[str, Any] | None, it["checkpoint"]),
                    created_at=cast(str, it["created_at"]),
                    updated_at=cast(str, it["updated_at"]),
                )
                self._save_work_projection(work, conn)

        return applied_count
