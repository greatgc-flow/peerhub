"""SQLite storage for the rebuildable skill and capability catalog views."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from peerhub.core.models import Record
from peerhub.extensions.skill_models import (
    CapabilityItem, CapabilityNotFoundError, SkillItem, SkillNotFoundError, VolatileFactRejectedError,
)
from peerhub.extensions.sqlite_tx import sqlite_tx


def initialize(db_path: Path) -> None:
    with sqlite_tx(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS ext_skills (
            skill_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '1.0.0',
            state TEXT NOT NULL,
            revision INTEGER NOT NULL,
            tree_digest TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            path TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS ext_capabilities (
            capability_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            spec_json TEXT NOT NULL DEFAULT '{}',
            revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

def get_skill(db_path: Path, skill_id: str) -> SkillItem:
    """Fetch skill projection by ID."""
    with sqlite_tx(db_path) as conn:
        row = conn.execute(
            """
            SELECT skill_id, stream_id, name, description, version, state,
                   revision, tree_digest, file_count, path, tags_json, created_at, updated_at
            FROM ext_skills WHERE skill_id = ?
            """,
            (skill_id,),
        ).fetchone()

    if row is None:
        raise SkillNotFoundError(f"Skill {skill_id!r} not found in catalog")

    return SkillItem(
        skill_id=row[0],
        stream_id=row[1],
        name=row[2],
        description=row[3],
        version=row[4],
        state=row[5],
        revision=row[6],
        tree_digest=row[7],
        file_count=row[8],
        path=row[9],
        tags=json.loads(row[10]),
        created_at=row[11],
        updated_at=row[12],
    )

def get_capability(db_path: Path, capability_id: str) -> CapabilityItem:
    """Fetch capability projection by ID."""
    with sqlite_tx(db_path) as conn:
        row = conn.execute(
            """
            SELECT capability_id, stream_id, spec_json, revision, created_at, updated_at
            FROM ext_capabilities WHERE capability_id = ?
            """,
            (capability_id,),
        ).fetchone()

    if row is None:
        raise CapabilityNotFoundError(f"Capability {capability_id!r} not found in catalog")

    return CapabilityItem(
        capability_id=row[0],
        stream_id=row[1],
        spec=json.loads(row[2]),
        revision=row[3],
        created_at=row[4],
        updated_at=row[5],
    )

def save_skill(conn: sqlite3.Connection, skill: SkillItem) -> None:
    conn.execute(
        """
        INSERT INTO ext_skills (
            skill_id, stream_id, name, description, version, state,
            revision, tree_digest, file_count, path, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(skill_id) DO UPDATE SET
            stream_id = excluded.stream_id,
            name = excluded.name,
            description = excluded.description,
            version = excluded.version,
            state = excluded.state,
            revision = excluded.revision,
            tree_digest = excluded.tree_digest,
            file_count = excluded.file_count,
            path = excluded.path,
            tags_json = excluded.tags_json,
            updated_at = excluded.updated_at
        """,
        (
            skill.skill_id,
            skill.stream_id,
            skill.name,
            skill.description,
            skill.version,
            skill.state,
            skill.revision,
            skill.tree_digest,
            skill.file_count,
            skill.path,
            json.dumps(skill.tags),
            skill.created_at,
            skill.updated_at,
        ),
    )

def save_capability(conn: sqlite3.Connection, cap: CapabilityItem) -> None:
    conn.execute(
        """
        INSERT INTO ext_capabilities (
            capability_id, stream_id, spec_json, revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(capability_id) DO UPDATE SET
            stream_id = excluded.stream_id,
            spec_json = excluded.spec_json,
            revision = excluded.revision,
            updated_at = excluded.updated_at
        """,
        (
            cap.capability_id,
            cap.stream_id,
            json.dumps(cap.spec),
            cap.revision,
            cap.created_at,
            cap.updated_at,
        ),
    )


def fold_records(records: Iterable[Record], accepted: set[str] | None, *,
                 allowed_transitions: set[tuple[str, str]], validate_spec: Callable[[dict[str, Any]], None]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    """The single ordered-Record reducer for skills and capabilities: pure, no database access.
    `accepted` collects the record ids the reducer applied."""
    applied_count = 0
    skills_state: dict[str, dict[str, Any]] = {}
    capabilities_state: dict[str, dict[str, Any]] = {}

    for record in records:
        raw_body: object = record.body
        if not isinstance(raw_body, dict):
            continue

        p = cast(dict[str, object], raw_body)
        kind = record.kind

        if kind == "m2.skill.transitioned":
            sid = p.get("skill_id")
            if not isinstance(sid, str) or sid not in skills_state:
                continue
            existing = skills_state[sid]
            expected, revision = p.get("expected_revision"), p.get("new_revision")
            if (record.stream_id != existing["stream_id"] or type(expected) is not int
                    or type(revision) is not int or expected != existing["revision"]
                    or revision != expected + 1 or not isinstance(p.get("to_state"), str)
                    or p.get("from_state") != existing["state"]
                    or (existing["state"], p.get("to_state")) not in allowed_transitions):
                continue
        if kind == "m2.catalog.updated":
            cid = p.get("capability_id")
            if not isinstance(cid, str) or cid not in capabilities_state:
                continue
            existing = capabilities_state[cid]
            expected, revision = p.get("expected_revision"), p.get("new_revision")
            if (record.stream_id != existing["stream_id"] or type(expected) is not int
                    or type(revision) is not int or expected != existing["revision"] or revision != expected + 1):
                continue
        if kind in {"m2.catalog.declared", "m2.catalog.updated"}:
            raw_spec = p.get("spec")
            if not isinstance(raw_spec, dict):
                continue
            try:
                validate_spec(cast(dict[str, Any], raw_spec))
            except VolatileFactRejectedError:
                continue

        if kind == "m2.skill.registered":
            raw_sid = p.get("skill_id")
            if not isinstance(raw_sid, str) or not raw_sid or raw_sid in skills_state:
                continue
            skill_id: str = raw_sid
            raw_name = p.get("name")
            name_str: str = raw_name if isinstance(raw_name, str) else skill_id
            raw_desc = p.get("description")
            desc_str: str = raw_desc if isinstance(raw_desc, str) else ""
            raw_ver = p.get("version")
            ver_str: str = raw_ver if isinstance(raw_ver, str) else "1.0.0"
            raw_tags = p.get("tags")
            tags_list: list[str] = [str(x) for x in cast(list[object], raw_tags)] if isinstance(raw_tags, list) else []
            raw_digest = p.get("tree_digest")
            digest_str: str = raw_digest if isinstance(raw_digest, str) else ""
            raw_fc = p.get("file_count")
            fc_num: int = raw_fc if isinstance(raw_fc, int) else 0
            raw_path = p.get("path")
            path_str: str = raw_path if isinstance(raw_path, str) else ""
            raw_state = p.get("state")
            state_str: str = raw_state if isinstance(raw_state, str) else "DISCOVERED"
            raw_rev = p.get("revision")
            rev_num: int = raw_rev if isinstance(raw_rev, int) else 1
            raw_created = p.get("indexed_at")
            created_str: str = raw_created if isinstance(raw_created, str) else record.created_at

            skills_state[skill_id] = {
                "skill_id": skill_id,
                "stream_id": record.stream_id,
                "name": name_str,
                "description": desc_str,
                "version": ver_str,
                "tags": tags_list,
                "tree_digest": digest_str,
                "file_count": fc_num,
                "path": path_str,
                "state": state_str,
                "revision": rev_num,
                "created_at": created_str,
                "updated_at": created_str,
            }
            applied_count += 1
            if accepted is not None:
                accepted.add(record.record_id)

        elif kind == "m2.skill.transitioned":
            raw_sid = p.get("skill_id")
            if not isinstance(raw_sid, str) or raw_sid not in skills_state:
                continue
            sk = skills_state[raw_sid]
            raw_to = p.get("to_state")
            if isinstance(raw_to, str):
                sk["state"] = raw_to
                raw_new_rev = p.get("new_revision")
                sk["revision"] = raw_new_rev if isinstance(raw_new_rev, int) else cast(int, sk["revision"]) + 1
                raw_trans_at = p.get("transitioned_at")
                sk["updated_at"] = raw_trans_at if isinstance(raw_trans_at, str) else record.created_at
                applied_count += 1
                if accepted is not None:
                    accepted.add(record.record_id)

        elif kind == "m2.catalog.declared":
            raw_cid = p.get("capability_id")
            if not isinstance(raw_cid, str) or not raw_cid or raw_cid in capabilities_state:
                continue
            cap_id: str = raw_cid
            raw_spec = p.get("spec")
            spec_dict: dict[str, Any] = cast(dict[str, Any], raw_spec) if isinstance(raw_spec, dict) else {}
            raw_rev = p.get("revision")
            rev_num: int = raw_rev if isinstance(raw_rev, int) else 1
            raw_created = p.get("declared_at")
            created_str: str = raw_created if isinstance(raw_created, str) else record.created_at

            capabilities_state[cap_id] = {
                "capability_id": cap_id,
                "stream_id": record.stream_id,
                "spec": spec_dict,
                "revision": rev_num,
                "created_at": created_str,
                "updated_at": created_str,
            }
            applied_count += 1
            if accepted is not None:
                accepted.add(record.record_id)

        elif kind == "m2.catalog.updated":
            raw_cid = p.get("capability_id")
            if not isinstance(raw_cid, str) or raw_cid not in capabilities_state:
                continue
            cp = capabilities_state[raw_cid]
            raw_spec = p.get("spec")
            if isinstance(raw_spec, dict):
                cp["spec"] = cast(dict[str, Any], raw_spec)
                raw_new_rev = p.get("new_revision")
                cp["revision"] = raw_new_rev if isinstance(raw_new_rev, int) else cast(int, cp["revision"]) + 1
                raw_upd_at = p.get("updated_at")
                cp["updated_at"] = raw_upd_at if isinstance(raw_upd_at, str) else record.created_at
                applied_count += 1
                if accepted is not None:
                    accepted.add(record.record_id)

    return skills_state, capabilities_state, applied_count
