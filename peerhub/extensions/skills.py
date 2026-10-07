"""M2.3 Skill and General Capability Catalog Implementation.

Adheres strictly to M2_3_SKILL_CATALOG_CONTRACT.md and architecture invariants:
- Invariant 1: Core remains Peer/Stream/Record/Offset.
- Invariant 2: Core imports Extension = 0.
- Invariant 8: Skill source != generated index != catalog != policy.
- Invariant 9: Volatile measured facts stay in Observation; Catalog holds curated declared facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, ClassVar, Iterable, cast, Iterator

from peerhub.core.models import Record
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions.artifact import SecurityBoundaryError


class SkillCatalogError(Exception):
    """Base exception for Skill and Capability Catalog errors."""


class SkillNotFoundError(SkillCatalogError, LookupError):
    """Referenced skill_id does not exist in catalog."""


class CapabilityNotFoundError(SkillCatalogError, LookupError):
    """Referenced capability_id does not exist in catalog."""


class SkillRevisionConflictError(SkillCatalogError, RuntimeError):
    """Mutation expected_revision does not match current skill revision."""


class CapabilityConflictError(SkillCatalogError, RuntimeError):
    """Mutation expected_revision does not match current capability revision."""


class ForbiddenTransitionError(SkillCatalogError, RuntimeError):
    """Attempting an invalid state machine transition."""


class SkillManifestError(SkillCatalogError, ValueError):
    """SKILL.md frontmatter is missing, unparseable, or fails schema validation."""


class SkillTamperedError(SkillCatalogError, RuntimeError):
    """On-disk skill directory contents hash differs from indexed canonical digest."""


class UnauthorizedSkillError(SkillCatalogError, PermissionError):
    """Attempt to invoke or authorize an inactive, unvalidated, or suspended skill."""


class VolatileFactRejectedError(SkillCatalogError, ValueError):
    """Attempt to register volatile runtime measurement into curated capability catalog."""


@dataclass(frozen=True)
class SkillItem:
    """Represents a projected view of a skill derived from directory source and stream records."""

    skill_id: str
    stream_id: str
    name: str
    description: str
    version: str
    state: str
    revision: int
    tree_digest: str
    file_count: int
    path: str
    tags: list[str] = field(default_factory=lambda: cast(list[str], []))
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CapabilityItem:
    """Represents a curated declared capability view."""

    capability_id: str
    stream_id: str
    spec: dict[str, Any]
    revision: int
    created_at: str = ""
    updated_at: str = ""


class SkillCatalogEngine:
    """Engine managing Skill indexing, integrity verification, and capability declarations."""

    ALLOWED_SKILL_TRANSITIONS: ClassVar[set[tuple[str, str]]] = {
        ("DISCOVERED", "VALIDATED"),
        ("VALIDATED", "ACTIVE"),
        ("ACTIVE", "SUSPENDED"),
        ("SUSPENDED", "ACTIVE"),
        ("ACTIVE", "RETIRED"),
        ("SUSPENDED", "RETIRED"),
    }

    TERMINAL_STATES: ClassVar[set[str]] = {"RETIRED"}

    PROHIBITED_CAPABILITY_KEYS: ClassVar[set[str]] = {
        "quota",
        "rate_limit",
        "latency_ms",
        "tokens_remaining",
        "ping",
        "tps",
        "qps",
    }

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
        raise SkillCatalogError(f"No valid author peer could be resolved for stream {stream_id!r}")

    @staticmethod
    def compute_directory_digest(skill_dir: Path) -> tuple[str, int]:
        """Compute deterministic SHA-256 tree digest over sorted relative paths and file contents."""
        hasher = hashlib.sha256()
        file_count = 0
        resolved_dir = skill_dir.resolve()

        all_files: list[tuple[str, Path]] = []
        for p in resolved_dir.rglob("*"):
            if p.is_symlink():
                raise SecurityBoundaryError("Skill source symlinks are not allowed")
            if p.is_file():
                rel_path = p.relative_to(resolved_dir).as_posix()
                if ".git" in p.relative_to(resolved_dir).parts:
                    continue
                all_files.append((rel_path, p))

        all_files.sort(key=lambda x: x[0])
        # Length-prefixed framing (8-byte big-endian lengths): no byte sequence inside a path or a file can be mistaken for a
        # boundary, so two different directory trees can never share a digest. Files are streamed, memory stays bounded.
        for rel_path, file_path in all_files:
            name = rel_path.encode("utf-8")
            hasher.update(len(name).to_bytes(8, "big"))
            hasher.update(name)
            hasher.update(file_path.stat().st_size.to_bytes(8, "big"))
            with file_path.open("rb") as stream:
                while chunk := stream.read(65536):
                    hasher.update(chunk)
            file_count += 1

        return hasher.hexdigest(), file_count

    @staticmethod
    def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
        """Parse YAML frontmatter using pyyaml if available, or lightweight stdlib parser."""
        try:
            import importlib
            yaml_module = importlib.import_module("yaml")
            safe_load_fn = getattr(yaml_module, "safe_load", None)
            if callable(safe_load_fn):
                try:
                    parsed: object = safe_load_fn(text)
                    if isinstance(parsed, dict):
                        return cast(dict[str, Any], parsed)
                except Exception as e:
                    raise SkillManifestError(f"Failed to parse YAML frontmatter: {e}") from e
        except ImportError:
            pass

        # Standard-library fallback parser for basic YAML frontmatter
        result: dict[str, Any] = {}
        current_key: str | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- "):
                if current_key:
                    val = line[2:].strip().strip("\"'")
                    if current_key not in result:
                        result[current_key] = []
                    if isinstance(result[current_key], list):
                        cast(list[Any], result[current_key]).append(val)
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                current_key = k
                if v.startswith("[") and v.endswith("]"):  # inline list, as PyYAML reads `tags: [a, b]`
                    result[k] = [i.strip().strip("\"'") for i in v[1:-1].split(",") if i.strip()]
                    continue
                v = v.strip("\"'")
                if v:
                    result[k] = v
                else:
                    result[k] = []
        return result

    @classmethod
    def _parse_skill_manifest(cls, manifest_path: Path) -> dict[str, Any]:
        """Parse YAML frontmatter from SKILL.md."""
        if not manifest_path.is_file():
            raise SkillManifestError(f"SKILL.md not found in {manifest_path.parent}")

        content = manifest_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            raise SkillManifestError("SKILL.md must begin with YAML frontmatter delimiter '---'")

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise SkillManifestError("Unclosed YAML frontmatter in SKILL.md")

        frontmatter_text = parts[1]
        data = cls._parse_yaml_frontmatter(frontmatter_text)

        if "name" not in data or not isinstance(data["name"], str) or not data["name"]:
            raise SkillManifestError("SKILL.md frontmatter missing required non-empty 'name' field")

        return data

    def index_skill(
        self,
        stream_id: str,
        skill_dir: Path | str,
        author_peer_id: str | None = None,
    ) -> SkillItem:
        """Scan and index a skill directory source into read-model projection."""
        sdir = Path(skill_dir).resolve()
        manifest_path = sdir / "SKILL.md"
        meta = self._parse_skill_manifest(manifest_path)

        name = cast(str, meta["name"])
        skill_id = name
        description = str(meta.get("description", ""))
        version = str(meta.get("version", "1.0.0"))
        raw_tags = meta.get("tags", [])
        tags = [str(t) for t in cast(list[object], raw_tags)] if isinstance(raw_tags, list) else []

        tree_digest, file_count = self.compute_directory_digest(sdir)
        now = self._now_iso()

        payload = {
            "skill_id": skill_id,
            "name": name,
            "description": description,
            "version": version,
            "tags": tags,
            "tree_digest": tree_digest,
            "file_count": file_count,
            "path": sdir.as_posix(),
            "state": "DISCOVERED",
            "revision": 1,
            "indexed_at": now,
        }

        self.store.append_record(
            stream_id=stream_id,
            author_peer_id=self._resolve_author(stream_id, author_peer_id),
            kind="m2.skill.registered",
            body=payload,
            idempotency_key=f"skill-reg-{skill_id}",
            created_at=now,
        )

        skill = SkillItem(
            skill_id=skill_id,
            stream_id=stream_id,
            name=name,
            description=description,
            version=version,
            state="DISCOVERED",
            revision=1,
            tree_digest=tree_digest,
            file_count=file_count,
            path=sdir.as_posix(),
            tags=tags,
            created_at=now,
            updated_at=now,
        )

        self._save_skill_projection(skill)
        return skill

    def _stream_records(self, stream_id: str, page_size: int = 500) -> Iterator[Record]:
        position = 0
        while batch := self.store.read_records(stream_id, after_position=position, limit=page_size):
            yield from batch
            position = batch[-1].position

    def _authoritative_skill(self, skill_id: str, accepted: set[str] | None = None) -> SkillItem:
        """The skill as the ordered stream Records define it, repairing a stale projection (crash or another writer).
        CAS decisions must use this, never the cached projection row: the Record is the authority."""
        cached = self.get_skill(skill_id)
        skills, _caps, _n = self._fold(self._stream_records(cached.stream_id), accepted)
        state = skills.get(skill_id)
        if state is None:
            raise SkillNotFoundError(f"Skill {skill_id!r} has no authoritative Records in stream {cached.stream_id!r}")
        item = self._skill_from_state(state)
        if item != cached:
            self._save_skill_projection(item)
        return item

    def _authoritative_capability(self, capability_id: str, accepted: set[str] | None = None) -> CapabilityItem:
        cached = self.get_capability(capability_id)
        _skills, caps, _n = self._fold(self._stream_records(cached.stream_id), accepted)
        state = caps.get(capability_id)
        if state is None:
            raise CapabilityNotFoundError(f"Capability {capability_id!r} has no authoritative Records in stream {cached.stream_id!r}")
        item = self._capability_from_state(state)
        if item != cached:
            self._save_capability_projection(item)
        return item

    @staticmethod
    def _skill_from_state(sk: dict[str, Any]) -> SkillItem:
        return SkillItem(
            skill_id=cast(str, sk["skill_id"]), stream_id=cast(str, sk["stream_id"]), name=cast(str, sk["name"]),
            description=cast(str, sk["description"]), version=cast(str, sk["version"]), state=cast(str, sk["state"]),
            revision=cast(int, sk["revision"]), tree_digest=cast(str, sk["tree_digest"]), file_count=cast(int, sk["file_count"]),
            path=cast(str, sk["path"]), tags=list(cast(list[str], sk["tags"])), created_at=cast(str, sk["created_at"]),
            updated_at=cast(str, sk["updated_at"]))

    @staticmethod
    def _capability_from_state(cp: dict[str, Any]) -> CapabilityItem:
        return CapabilityItem(
            capability_id=cast(str, cp["capability_id"]), stream_id=cast(str, cp["stream_id"]),
            spec=cast(dict[str, Any], cp["spec"]), revision=cast(int, cp["revision"]),
            created_at=cast(str, cp["created_at"]), updated_at=cast(str, cp["updated_at"]))

    def _append_mutation(self, conflict: type[SkillCatalogError], identity: str, **record: Any) -> Record:
        """Append a CAS-guarded Record; the store's idempotency conflict on the same `id-revision` key means another writer
        already took that revision. The loser's cache is repaired before the conflict is raised."""
        try:
            return self.store.append_record(**record)
        except IdempotencyConflictError as exc:
            if conflict is SkillRevisionConflictError:
                self._authoritative_skill(identity)
            else:
                self._authoritative_capability(identity)
            raise conflict(f"Another writer already took this revision of {identity!r}: {exc}") from exc

    def _confirm_skill(self, skill_id: str, record_id: str) -> SkillItem:
        """Succeed only if the reducer accepted OUR record; a change that lost a revision race is ignored on replay,
        so acknowledging it would be a lost update."""
        accepted: set[str] = set()
        item = self._authoritative_skill(skill_id, accepted)
        if record_id not in accepted:
            raise SkillRevisionConflictError(
                f"Skill {skill_id!r}: the change was appended but lost to a concurrent writer (authoritative revision {item.revision})")
        return item

    def _confirm_capability(self, capability_id: str, record_id: str) -> CapabilityItem:
        accepted: set[str] = set()
        item = self._authoritative_capability(capability_id, accepted)
        if record_id not in accepted:
            raise CapabilityConflictError(
                f"Capability {capability_id!r}: the change was appended but lost to a concurrent writer (authoritative revision {item.revision})")
        return item

    def transition_skill(
        self,
        skill_id: str,
        expected_revision: int,
        target_state: str,
        reason: str | None = None,
        author_peer_id: str | None = None,
    ) -> SkillItem:
        """Advance skill lifecycle state with CAS optimistic revision check."""
        current = self._authoritative_skill(skill_id)

        if current.state in self.TERMINAL_STATES:
            raise ForbiddenTransitionError(
                f"Cannot transition skill {skill_id!r} from terminal state {current.state!r}"
            )

        if (current.state, target_state) not in self.ALLOWED_SKILL_TRANSITIONS:
            raise ForbiddenTransitionError(
                f"Forbidden transition from {current.state!r} to {target_state!r} for skill {skill_id!r}"
            )

        if current.revision != expected_revision:
            raise SkillRevisionConflictError(
                f"Skill {skill_id!r} revision conflict: expected {expected_revision}, got {current.revision}"
            )

        if target_state in {"VALIDATED", "ACTIVE"}:
            self.verify_skill_integrity(skill_id)

        now = self._now_iso()
        new_rev = current.revision + 1

        payload = {
            "skill_id": skill_id,
            "from_state": current.state,
            "to_state": target_state,
            "expected_revision": expected_revision,
            "new_revision": new_rev,
            "reason": reason,
            "transitioned_at": now,
        }

        appended = self._append_mutation(
            SkillRevisionConflictError, skill_id,
            stream_id=current.stream_id,
            author_peer_id=self._resolve_author(current.stream_id, author_peer_id),
            kind="m2.skill.transitioned",
            body=payload,
            idempotency_key=f"skill-trans-{skill_id}-r{new_rev}",
            created_at=now,
        )

        return self._confirm_skill(skill_id, appended.record_id)

    def verify_skill_integrity(self, skill_id: str) -> bool:
        """Verify that on-disk files match the canonical indexed tree digest."""
        skill = self.get_skill(skill_id)
        current_digest, _ = self.compute_directory_digest(Path(skill.path))
        if current_digest != skill.tree_digest:
            raise SkillTamperedError(
                f"Skill {skill_id!r} content tampered: expected digest {skill.tree_digest}, got {current_digest}"
            )
        return True

    def authorize_skill_execution(self, skill_id: str) -> SkillItem:
        """Authorize execution of a skill; rejects non-ACTIVE skills (Discovery != Authorization)."""
        skill = self.get_skill(skill_id)
        if skill.state != "ACTIVE":
            raise UnauthorizedSkillError(
                f"Skill {skill_id!r} is in state {skill.state!r}; execution requires ACTIVE state"
            )
        self.verify_skill_integrity(skill_id)
        return skill

    def declare_capability(
        self,
        stream_id: str,
        capability_id: str,
        spec: dict[str, Any],
        author_peer_id: str | None = None,
    ) -> CapabilityItem:
        """Declare curated capability metadata (strictly rejects volatile facts)."""
        self._validate_capability_spec(spec)
        now = self._now_iso()

        payload = {
            "capability_id": capability_id,
            "spec": spec,
            "revision": 1,
            "declared_at": now,
        }

        self.store.append_record(
            stream_id=stream_id,
            author_peer_id=self._resolve_author(stream_id, author_peer_id),
            kind="m2.catalog.declared",
            body=payload,
            idempotency_key=f"cap-dec-{capability_id}",
            created_at=now,
        )

        cap = CapabilityItem(
            capability_id=capability_id,
            stream_id=stream_id,
            spec=spec,
            revision=1,
            created_at=now,
            updated_at=now,
        )

        self._save_capability_projection(cap)
        return cap

    def update_capability(
        self,
        capability_id: str,
        expected_revision: int,
        spec: dict[str, Any],
        author_peer_id: str | None = None,
    ) -> CapabilityItem:
        """Update curated capability with CAS revision check."""
        current = self._authoritative_capability(capability_id)
        self._validate_capability_spec(spec)

        if current.revision != expected_revision:
            raise CapabilityConflictError(
                f"Capability {capability_id!r} revision conflict: expected {expected_revision}, got {current.revision}"
            )

        now = self._now_iso()
        new_rev = current.revision + 1

        payload = {
            "capability_id": capability_id,
            "spec": spec,
            "expected_revision": expected_revision,
            "new_revision": new_rev,
            "updated_at": now,
        }

        appended = self._append_mutation(
            CapabilityConflictError, capability_id,
            stream_id=current.stream_id,
            author_peer_id=self._resolve_author(current.stream_id, author_peer_id),
            kind="m2.catalog.updated",
            body=payload,
            idempotency_key=f"cap-upd-{capability_id}-r{new_rev}",
            created_at=now,
        )

        return self._confirm_capability(capability_id, appended.record_id)

    def _validate_capability_spec(self, spec: dict[str, Any]) -> None:
        """Ensure spec does not contain volatile runtime measurements (Invariant 9)."""
        for k in spec:
            if k.lower() in self.PROHIBITED_CAPABILITY_KEYS:
                raise VolatileFactRejectedError(
                    f"Prohibited volatile fact {k!r} in capability specification. "
                    "Measured runtime observations belong in M1 Observation/Diag."
                )
            value = spec[k]
            if isinstance(value, dict):
                self._validate_capability_spec(cast(dict[str, Any], value))
            elif isinstance(value, list):
                for item in cast(list[Any], value):
                    if isinstance(item, dict):
                        self._validate_capability_spec(cast(dict[str, Any], item))
                    elif isinstance(item, list):
                        self._validate_capability_spec({"nested": item})

    def list_skills(self) -> list[SkillItem]:
        """Enumerate projected canonical skills in deterministic identity order."""
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute("SELECT skill_id FROM ext_skills ORDER BY skill_id")]
        return [self.get_skill(skill_id) for skill_id in ids]

    def get_skill(self, skill_id: str) -> SkillItem:
        """Fetch skill projection by ID."""
        with sqlite3.connect(self.db_path) as conn:
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

    def search_skills(self, query: str, tags: list[str] | None = None) -> list[SkillItem]:
        """Search curated skill metadata, not provider runtime observations."""
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute("SELECT skill_id FROM ext_skills ORDER BY skill_id")]
        needle = query.casefold()
        return [item for skill_id in ids if
                needle in ((item := self.get_skill(skill_id)).name + " " + item.description).casefold()
                and set(tags or []).issubset(item.tags)]

    def get_capability(self, capability_id: str) -> CapabilityItem:
        """Fetch capability projection by ID."""
        with sqlite3.connect(self.db_path) as conn:
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

    def _save_skill_projection(self, skill: SkillItem, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with sqlite3.connect(self.db_path) as own:
                self._save_skill_projection(skill, own)
            return
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

    def _save_capability_projection(self, cap: CapabilityItem, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with sqlite3.connect(self.db_path) as own:
                self._save_capability_projection(cap, own)
            return
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

    def _fold(self, records: Iterable[Record], accepted: set[str] | None = None
              ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
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
                        or (existing["state"], p.get("to_state")) not in self.ALLOWED_SKILL_TRANSITIONS):
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
                    self._validate_capability_spec(cast(dict[str, Any], raw_spec))
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

    def rebuild_index(self, records: Iterable[Record]) -> int:
        """Rebuild the projection from the authoritative stream Records only.

        The skill directories are never rescanned here: a recorded digest must not be silently replaced by whatever is on disk.
        Drift between a recorded digest and the directory is detected by `verify_skill_integrity` (`SkillTamperedError`)."""
        self._init_db()
        skills_state, capabilities_state, applied_count = self._fold(records)
        # Replace both projections atomically: nothing is deleted until the full state has been computed, and a failure
        # while writing rolls everything back, so readers never see an empty or half-built projection.
        self._replace_projection(skills_state, capabilities_state)
        return applied_count

    def _replace_projection(self, skills_state: dict[str, dict[str, Any]], capabilities_state: dict[str, dict[str, Any]]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM ext_skills;")
            conn.execute("DELETE FROM ext_capabilities;")
            for sk in skills_state.values():
                skill_obj = SkillItem(
                    skill_id=cast(str, sk["skill_id"]),
                    stream_id=cast(str, sk["stream_id"]),
                    name=cast(str, sk["name"]),
                    description=cast(str, sk["description"]),
                    version=cast(str, sk["version"]),
                    state=cast(str, sk["state"]),
                    revision=cast(int, sk["revision"]),
                    tree_digest=cast(str, sk["tree_digest"]),
                    file_count=cast(int, sk["file_count"]),
                    path=cast(str, sk["path"]),
                    tags=cast(list[str], sk["tags"]),
                    created_at=cast(str, sk["created_at"]),
                    updated_at=cast(str, sk["updated_at"]),
                )
                self._save_skill_projection(skill_obj, conn)

            for cp in capabilities_state.values():
                cap_obj = CapabilityItem(
                    capability_id=cast(str, cp["capability_id"]),
                    stream_id=cast(str, cp["stream_id"]),
                    spec=cast(dict[str, Any], cp["spec"]),
                    revision=cast(int, cp["revision"]),
                    created_at=cast(str, cp["created_at"]),
                    updated_at=cast(str, cp["updated_at"]),
                )
                self._save_capability_projection(cap_obj, conn)
