"""M2.3 Skill and General Capability Catalog Implementation.

Adheres strictly to M2_3_SKILL_CATALOG_CONTRACT.md and architecture invariants:
- Invariant 1: Core remains Peer/Stream/Record/Offset.
- Invariant 2: Core imports Extension = 0.
- Invariant 8: Skill source != generated index != catalog != policy.
- Invariant 9: Volatile measured facts stay in Observation; Catalog holds curated declared facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, ClassVar, Iterable, cast

from peerhub.extensions.authoritative import authoritative_item, stream_records
from peerhub.extensions.sqlite_tx import sqlite_tx
from peerhub.core.models import Record
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions import skill_projection, skill_source
from peerhub.extensions.skill_models import (
    CapabilityConflictError,
    CapabilityItem,
    CapabilityNotFoundError,
    ForbiddenTransitionError,
    SkillCatalogError,
    SkillItem,
    SkillManifestError,
    SkillNotFoundError,
    SkillRevisionConflictError,
    SkillTamperedError,
    UnauthorizedSkillError,
    VolatileFactRejectedError,
)

__all__ = [
    "CapabilityConflictError", "CapabilityItem", "CapabilityNotFoundError", "ForbiddenTransitionError",
    "SkillCatalogEngine", "SkillCatalogError", "SkillItem", "SkillManifestError", "SkillNotFoundError",
    "SkillRevisionConflictError", "SkillTamperedError", "UnauthorizedSkillError", "VolatileFactRejectedError",
]


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
        skill_projection.initialize(self.db_path)

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

    compute_directory_digest = staticmethod(skill_source.compute_directory_digest)

    def index_skill(
        self,
        stream_id: str,
        skill_dir: Path | str,
        author_peer_id: str | None = None,
    ) -> SkillItem:
        """Scan and index a skill directory source into read-model projection."""
        sdir = Path(skill_dir).resolve()
        manifest_path = sdir / "SKILL.md"
        meta = skill_source.parse_skill_manifest(manifest_path)

        name = cast(str, meta["name"])
        skill_id = name
        description = str(meta.get("description", ""))
        version = str(meta.get("version", "1.0.0"))
        raw_tags = meta.get("tags", [])
        tags = [str(t) for t in cast(list[object], raw_tags)] if isinstance(raw_tags, list) else []

        tree_digest, file_count = self.compute_directory_digest(sdir)
        located = self._find_stream(skill_id=skill_id)
        if located is not None:  # registered already (possibly: Record durable, projection row lost to a crash)
            adopted = self._authoritative_skill(skill_id)
            if located == stream_id and adopted.created_at and adopted.name == name and adopted.version == version \
                    and adopted.description == description:
                return adopted  # idempotent: the projection is repaired, nothing is appended
            raise SkillRevisionConflictError(f"Skill {skill_id!r} is already registered with different content or in another stream")
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

    def _find_stream(self, *, skill_id: str | None = None, capability_id: str | None = None) -> str | None:
        """The stream whose Records create the skill/capability, for the rare case that its projection row is missing."""
        for stream in self.store.list_streams():
            skills, caps, _n = self._fold(stream_records(self.store, stream.stream_id))
            if (skill_id is not None and skill_id in skills) or (capability_id is not None and capability_id in caps):
                return stream.stream_id
        return None

    def _authoritative_skill(self, skill_id: str, accepted: set[str] | None = None) -> SkillItem:
        """The skill as the ordered stream Records define it, repairing a stale projection (crash or another writer).
        CAS decisions must use this, never the cached projection row: the Record is the authority."""
        return cast(SkillItem, authoritative_item(
            get_cached=lambda: self.get_skill(skill_id), locate=lambda: self._find_stream(skill_id=skill_id),
            replay=lambda stream_id: self._fold(stream_records(self.store, stream_id), accepted)[0].get(skill_id),
            build=self._skill_from_state, save=self._save_skill_projection, not_found=SkillNotFoundError, what="Skill", identity=skill_id))

    def _authoritative_capability(self, capability_id: str, accepted: set[str] | None = None) -> CapabilityItem:
        return cast(CapabilityItem, authoritative_item(
            get_cached=lambda: self.get_capability(capability_id), locate=lambda: self._find_stream(capability_id=capability_id),
            replay=lambda stream_id: self._fold(stream_records(self.store, stream_id), accepted)[1].get(capability_id),
            build=self._capability_from_state, save=self._save_capability_projection, not_found=CapabilityNotFoundError,
            what="Capability", identity=capability_id))

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
        located = self._find_stream(capability_id=capability_id)
        if located is not None:
            adopted_cap = self._authoritative_capability(capability_id)
            if located == stream_id and adopted_cap.revision == 1 and adopted_cap.spec == spec:
                return adopted_cap  # idempotent re-declaration after a crash: repaired, nothing appended
            raise CapabilityConflictError(f"Capability {capability_id!r} is already declared with different content or in another stream")
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
        with sqlite_tx(self.db_path) as conn:
            ids = [row[0] for row in conn.execute("SELECT skill_id FROM ext_skills ORDER BY skill_id")]
        return [self.get_skill(skill_id) for skill_id in ids]

    def get_skill(self, skill_id: str) -> SkillItem:
        """Fetch skill projection by ID."""
        return skill_projection.get_skill(self.db_path, skill_id)

    def search_skills(self, query: str, tags: list[str] | None = None) -> list[SkillItem]:
        """Search curated skill metadata, not provider runtime observations."""
        with sqlite_tx(self.db_path) as conn:
            ids = [row[0] for row in conn.execute("SELECT skill_id FROM ext_skills ORDER BY skill_id")]
        needle = query.casefold()
        return [item for skill_id in ids if
                needle in ((item := self.get_skill(skill_id)).name + " " + item.description).casefold()
                and set(tags or []).issubset(item.tags)]

    def get_capability(self, capability_id: str) -> CapabilityItem:
        """Fetch capability projection by ID."""
        return skill_projection.get_capability(self.db_path, capability_id)

    def _save_skill_projection(self, skill: SkillItem, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with sqlite_tx(self.db_path) as own:
                self._save_skill_projection(skill, own)
            return
        skill_projection.save_skill(conn, skill)

    def _save_capability_projection(self, cap: CapabilityItem, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with sqlite_tx(self.db_path) as own:
                self._save_capability_projection(cap, own)
            return
        skill_projection.save_capability(conn, cap)

    def _fold(self, records: Iterable[Record], accepted: set[str] | None = None
              ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
        return skill_projection.fold_records(records, accepted, allowed_transitions=self.ALLOWED_SKILL_TRANSITIONS,
                                             validate_spec=self._validate_capability_spec)

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
        with sqlite_tx(self.db_path) as conn:
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
