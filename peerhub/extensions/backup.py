"""Backup & Disaster Recovery Engine.

Implements authoritative backup creation, cryptographic manifest verification,
generation fencing, atomic restoration, and derived projection rebuild.

Core Invariant 11: Restore starts from authoritative and rebuilds derived.
Core imports Extension = 0.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import stat
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, cast

from peerhub.core.workspace import recover_interrupted_restore, restore_intent_path


# -----------------------------------------------------------------------------
# Exceptions (EXC-046 .. EXC-050)
# -----------------------------------------------------------------------------
class BackupCorruptedError(Exception):
    """File checksum mismatch or corruption detected in backup bundle (EXC-046)."""


class BackupManifestMissingError(Exception):
    """Missing or unreadable backup_manifest.json (EXC-047)."""


class GenerationFencingConflictError(Exception):
    """Restoration attempted with older generation than active fence epoch (EXC-048)."""


class AuthoritativeRestoreOrderError(Exception):
    """Invariant 11 violation: attempted projection rebuild without authoritative state (EXC-049)."""


class SecretLeakageDetectedError(Exception):
    """Unmasked secret or credential detected in backup bundle (EXC-050)."""


# -----------------------------------------------------------------------------
# Canonical JSON & Hash Helpers
# -----------------------------------------------------------------------------
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{base}{n}" for base in ("COM", "LPT") for n in "123456789¹²³"}
)


def _has_reserved_device_name(rel_path: str) -> bool:
    """True if any path component is a Windows device name (with or without extension, trailing dots/spaces ignored).

    Opening such a name on Windows addresses a device (CON blocks on console input), so it must be rejected by NAME
    before any open/stat of the path, on every platform (bundles must be portable).
    """
    for part in re.split(r"[\/]", rel_path):
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in _RESERVED_DEVICE_NAMES:
            return True
    return False


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# -----------------------------------------------------------------------------
# Secret Masking Guard
# -----------------------------------------------------------------------------
_SECRET_VALUE = re.compile(r"sk-(?:secret|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")
_SECRET_ASSIGNMENT = re.compile(r'''(?im)(?:api[_-]?key|private[_-]?key|password|access[_-]?token|auth[_-]?token|credential|secret[_-]?token)\s*["']?\s*[:=]\s*["']?([^\s"'#,{}\[\]]+)''')


def assert_no_plaintext_secrets(data: Any) -> None:
    """Recursively checks for plaintext secrets, raising SecretLeakageDetectedError if found."""
    if isinstance(data, dict):
        dict_data = cast(dict[Any, Any], data)
        for k, v in dict_data.items():
            key_str = str(k).lower()
            if key_str == "token" or any(p in key_str for p in ("api_key", "private_key", "secret", "password", "access_token", "auth_token", "credential")):
                if isinstance(v, str) and v and not v.startswith("***"):
                    raise SecretLeakageDetectedError(f"Plaintext secret detected in key '{k}'")
            assert_no_plaintext_secrets(v)
    elif isinstance(data, (list, tuple)):
        seq_data = cast(Sequence[Any], data)
        for item in seq_data:
            assert_no_plaintext_secrets(item)
    elif isinstance(data, str):
        if "sk-secret" in data or _SECRET_VALUE.search(data):
            raise SecretLeakageDetectedError("Plaintext secret detected in content value")
        if data.lstrip().startswith(("{", "[")):
            try:
                structured = json.loads(data)
            except ValueError:
                pass
            else:
                assert_no_plaintext_secrets(structured)
                return
        if any(not match.group(1).startswith("***") for match in _SECRET_ASSIGNMENT.finditer(data)):
            raise SecretLeakageDetectedError("Plaintext credential assignment detected")


def _scan_secrets(path: Path) -> None:
    """Fail closed, rather than mask authoritative bytes and invalidate their identity."""
    def check(value: str) -> None:
        assert_no_plaintext_secrets(value)
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return
        assert_no_plaintext_secrets(decoded)
    if path.name == "core.db":
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (table,) in tables:
                quoted = table.replace('"', '""')
                with closing(conn.execute(f'SELECT * FROM "{quoted}"')) as cursor:
                    names = [column[0] for column in cursor.description]
                    for row in cursor:
                        assert_no_plaintext_secrets(dict(zip(names, (
                            value.decode("utf-8", "replace") if isinstance(value, bytes) else value for value in row
                        ))))
                        for value in row:
                            if isinstance(value, str):
                                check(value)
                            elif isinstance(value, bytes):
                                check(value.decode("utf-8", "replace"))
    else:
        # Overlap catches credentials split across bounded read chunks.
        tail = b""
        with path.open("rb") as source:
            while chunk := source.read(65536):
                check((tail + chunk).decode("utf-8", "replace"))
                tail = chunk[-1024:]
        if path.suffix.lower() in (".json", ".toml"):
            if path.stat().st_size > 1_000_000:
                raise SecretLeakageDetectedError("Structured source exceeds secret inspection limit")
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".toml":
                import tomllib
                assert_no_plaintext_secrets(tomllib.loads(text))
            else:
                assert_no_plaintext_secrets(json.loads(text))


def _copy_sources(source: Path, target: Path) -> None:
    target.mkdir()
    if not source.exists():
        return
    for path in source.rglob("*"):
        if _is_link(path):
            raise BackupCorruptedError("Authoritative source must not contain symlinks")
        relative = path.relative_to(source)
        if _has_reserved_device_name(relative.as_posix()):
            raise BackupCorruptedError(f"Reserved device name in authoritative source: {relative.as_posix()}")
        if any(part == ".tmp" or part.endswith(".tmp") for part in relative.parts):
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def _discard_stage(stage: Path) -> None:
    # CAS copies may inherit Windows read-only attributes. Only our own staging
    # directory is removed; authoritative and pre-restore paths are never deleted.
    for path in stage.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(stage)


# -----------------------------------------------------------------------------
# Backup Manifest
# -----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class BackupManifest:
    backup_id: str
    created_at: str
    generation: int
    records_count: int
    secret_exclusion_policy: str
    files: dict[str, dict[str, Any]]
    reopen_proof: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "generation": self.generation,
            "records_count": self.records_count,
            "secret_exclusion_policy": self.secret_exclusion_policy,
            "files": self.files,
            "reopen_proof": self.reopen_proof,
        }


# -----------------------------------------------------------------------------
# Backup Creation
# -----------------------------------------------------------------------------
def create_backup(
    authoritative_db: Path,
    artifact_dir: Path,
    target_dir: Path,
    generation: int = 1,
    secrets_masking: bool = True,
    *, skill_dir: Path | None = None,
) -> BackupManifest:
    """Create an authoritative backup bundle with cryptographic manifest and reopen proof."""
    auth_db = Path(authoritative_db).resolve()
    art_dir = Path(artifact_dir).resolve()
    out_dir = Path(target_dir).resolve()
    if not auth_db.is_file():
        raise BackupCorruptedError("Authoritative database does not exist")
    skills = Path(skill_dir).resolve() if skill_dir else auth_db.parent / "skills"
    for raw in (Path(authoritative_db), Path(artifact_dir), Path(skill_dir) if skill_dir else skills):
        if raw.exists() and _is_link(raw):
            raise BackupCorruptedError("Authoritative roots must not be links or Windows reparse points")
    for source in (auth_db, art_dir, skills):
        if out_dir == source or source in out_dir.parents or out_dir in source.parents:
            raise BackupCorruptedError("Backup target overlaps authoritative storage")
    if out_dir.exists():
        raise BackupCorruptedError("Backup target already exists; choose a new bundle directory")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="backup_stage_", dir=out_dir.parent))
    try:
        manifest = _create_backup_staged(auth_db, art_dir, skills, stage, generation, secrets_masking)
        verify_backup(stage)
        os.rename(stage, out_dir)
        return manifest
    finally:
        if stage.exists():
            _discard_stage(stage)


def _create_backup_staged(auth_db: Path, art_dir: Path, skills: Path, out_dir: Path,
                          generation: int, secrets_masking: bool) -> BackupManifest:
    if type(generation) is not int or generation < 1:
        raise BackupCorruptedError("Generation must be a positive integer")

    # 1. Online backup of SQLite database (avoids locks / dirty writes)
    backup_db_path = out_dir / "core.db"
    with closing(sqlite3.connect(auth_db.as_uri() + "?mode=ro", uri=True, timeout=30.0)) as src_conn:
        with closing(sqlite3.connect(backup_db_path)) as dst_conn:
            src_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA journal_mode=DELETE")

    # 2. Copy CAS Artifact directory recursively
    backup_art_dir = out_dir / "artifacts"
    _copy_sources(art_dir, backup_art_dir)
    _copy_sources(skills, out_dir / "skills")

    # 3. Compute per-file SHA-256 and byte sizes
    files_manifest: dict[str, dict[str, Any]] = {}
    db_sha = _file_sha256(backup_db_path)
    files_manifest["core.db"] = {
        "sha256": db_sha,
        "bytes": backup_db_path.stat().st_size,
    }

    for root, _, files in os.walk(out_dir):
        for f in files:
            p = Path(root) / f
            rel = str(p.relative_to(out_dir)).replace("\\", "/")
            files_manifest[rel] = {
                "sha256": _file_sha256(p),
                "bytes": p.stat().st_size,
            }
            if secrets_masking:
                _scan_secrets(p)

    # 4. Verify reopen proof
    records_count = 0
    with closing(sqlite3.connect(backup_db_path)) as c:
        row = c.execute("PRAGMA integrity_check").fetchone()
        integrity_ok = bool(row and row[0] == "ok")
        try:
            records_count = int(c.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise BackupCorruptedError("Snapshot lacks authoritative Record table") from exc
    if not integrity_ok:
        raise BackupCorruptedError("Snapshot failed integrity_check")

    reopen_proof = {
        "verified": integrity_ok,
        "integrity_check": "ok" if integrity_ok else "failed",
    }

    now = _now_iso()
    backup_id = f"bck-{hashlib.sha256(f'{db_sha}:{now}'.encode()).hexdigest()[:16]}"
    manifest = BackupManifest(
        backup_id=backup_id,
        created_at=now,
        generation=generation,
        records_count=records_count,
        secret_exclusion_policy="EXCLUDED_BY_DEFAULT" if secrets_masking else "NONE",
        files=files_manifest,
        reopen_proof=reopen_proof,
    )

    # Write manifest file
    manifest_file = out_dir / "backup_manifest.json"
    manifest_file.write_text(_canonical_json(manifest.to_dict()), encoding="utf-8")

    return manifest


# -----------------------------------------------------------------------------
# Backup Verification
# -----------------------------------------------------------------------------
def verify_backup(backup_dir: Path, *, expected_manifest_sha256: str | None = None) -> bool:
    """Verify cryptographic integrity of a backup bundle against its manifest."""
    if _is_link(Path(backup_dir)):
        raise BackupCorruptedError("Backup root must not be a link or Windows reparse point")
    b_dir = Path(backup_dir).resolve()
    manifest_file = b_dir / "backup_manifest.json"
    if not manifest_file.exists():
        raise BackupManifestMissingError(f"Missing backup manifest: {manifest_file}")
    if expected_manifest_sha256 is not None and _file_sha256(manifest_file) != expected_manifest_sha256:
        raise BackupCorruptedError("Backup manifest changed during restoration")

    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise BackupCorruptedError(f"Unreadable backup manifest: {e}") from e

    if not isinstance(data, dict):
        raise BackupCorruptedError("Manifest must be an object")
    data = cast(dict[str, Any], data)
    files = data.get("files", {})
    if not isinstance(files, dict) or "core.db" not in files:
        raise BackupCorruptedError("Manifest contains no file records")
    files = cast(dict[Any, Any], files)
    if data.get("reopen_proof", {}).get("verified") is not True:
        raise BackupCorruptedError("Manifest has no successful reopen proof")

    for rel_path, meta in files.items():
        if not isinstance(rel_path, str) or not isinstance(meta, dict):
            raise BackupCorruptedError("Invalid manifest file entry")
        meta = cast(dict[str, Any], meta)
        relative = Path(rel_path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in rel_path or ":" in rel_path
                or _has_reserved_device_name(rel_path) or relative.as_posix() != rel_path or not relative.parts
                or (rel_path != "core.db" and relative.parts[0] not in ("artifacts", "skills"))):
            raise BackupCorruptedError("Unsafe manifest file path")
        p = b_dir / relative
        if _is_link(p) or any(_is_link(parent) for parent in p.parents if parent != b_dir and b_dir in parent.parents):
            raise BackupCorruptedError("Backup contains symlinks")
        if not p.is_file():
            raise BackupCorruptedError(f"File missing from backup: {rel_path}")
        actual_sha = _file_sha256(p)
        if actual_sha != meta.get("sha256"):
            raise BackupCorruptedError(
                f"Checksum mismatch for {rel_path}: expected {meta.get('sha256')}, got {actual_sha}"
            )
        if p.stat().st_size != meta.get("bytes"):
            raise BackupCorruptedError(f"File size mismatch: {rel_path}")
        if data.get("secret_exclusion_policy") == "EXCLUDED_BY_DEFAULT":
            _scan_secrets(p)
    actual_files = {p.relative_to(b_dir).as_posix() for p in b_dir.rglob("*") if p.is_file() and p != manifest_file}
    if actual_files != set(files):
        raise BackupCorruptedError("Backup contains unlisted files")

    # Verify SQLite integrity
    core_db = b_dir / "core.db"
    if core_db.exists():
        with closing(sqlite3.connect(core_db.as_uri() + "?mode=ro", uri=True)) as c:
            row = c.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupCorruptedError("core.db failed PRAGMA integrity_check")
            if c.execute("SELECT COUNT(*) FROM records").fetchone()[0] != data.get("records_count"):
                raise BackupCorruptedError("Manifest Record count mismatch")

    return True


# -----------------------------------------------------------------------------
# Authoritative Restoration (Invariant 11)
# -----------------------------------------------------------------------------
_MANAGED_DIRS = {"artifacts", "skills"}
_MANAGED_FILES = {"workspace.generation", "restore.epoch"}
_MANAGED_DB_SUFFIXES = (".db", ".db-wal", ".db-shm")


def _managed_workspace_entry(path: Path) -> bool:
    """A restore target may hold only PeerHub workspace content: authoritative dirs/markers or SQLite databases (core + derived)."""
    if path.is_dir():
        return path.name in _MANAGED_DIRS
    return path.name in _MANAGED_FILES or path.name.endswith(_MANAGED_DB_SUFFIXES)


def restore_authoritative(
    backup_dir: Path,
    target_dir: Path,
    current_generation: int = 0,
    allow_rollback: bool = True,
    *, fault_hook: Callable[[str], None] | None = None,
) -> int:
    """Offline workspace restore with staged replacement and recoverable prior state.

    Stop all writers before calling. Directory replacement cannot be transactional
    with arbitrary processes holding open SQLite handles. The previous directory
    is retained next to the target for rollback; it is never merged with the snapshot.
    """
    b_dir = Path(backup_dir).resolve()
    t_dir = Path(target_dir).resolve()
    recover_interrupted_restore(t_dir)  # a previous hard crash may have left the target missing
    intent = restore_intent_path(t_dir)
    manifest_path = b_dir / "backup_manifest.json"
    if not manifest_path.is_file():
        raise BackupManifestMissingError("Missing backup manifest")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    # Pin one manifest identity across source verification and staged validation.
    verify_backup(backup_dir, expected_manifest_sha256=manifest_sha)
    if t_dir == b_dir or t_dir in b_dir.parents or b_dir in t_dir.parents:
        raise BackupCorruptedError("Restore source and target overlap")
    if t_dir.exists():
        if not (t_dir / "core.db").is_file() or any(not _managed_workspace_entry(p) for p in t_dir.iterdir()):
            raise BackupCorruptedError("Restore target is not a dedicated managed workspace")

    manifest_data = json.loads(manifest_bytes)
    backup_gen = manifest_data.get("generation")
    if isinstance(backup_gen, bool) or not isinstance(backup_gen, int) or backup_gen < 1:
        raise BackupCorruptedError("Invalid backup generation")
    if (t_dir / "restore.epoch").exists():
        current_generation = max(current_generation, int((t_dir / "restore.epoch").read_text()))

    # 2. Generation Fencing check
    if backup_gen < current_generation and not allow_rollback:
        raise GenerationFencingConflictError(
            f"Stale generation conflict: backup generation {backup_gen} is older than active generation {current_generation}"
        )

    # If restoring over current generation, increment generation to fence older writers
    new_generation = backup_gen if current_generation == 0 else max(backup_gen, current_generation) + 1

    # 3. Atomic extraction via staging
    t_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix="restore_stage_", dir=t_dir.parent))

    previous_dir = t_dir.with_name(f"{t_dir.name}.pre-restore-{uuid.uuid4().hex}")
    moved = False
    def hit(point: str) -> None:
        if fault_hook:
            fault_hook(point)
    try:
        shutil.copy2(b_dir / "core.db", stage_dir / "core.db")
        for name in ("artifacts", "skills"):
            _copy_sources(b_dir / name, stage_dir / name)
        (stage_dir / "backup_manifest.json").write_bytes(manifest_bytes)
        verify_backup(stage_dir, expected_manifest_sha256=manifest_sha)
        (stage_dir / "backup_manifest.json").unlink()
        (stage_dir / "workspace.generation").write_text(uuid.uuid4().hex, encoding="utf-8")
        (stage_dir / "restore.epoch").write_text(str(new_generation), encoding="utf-8")
        hit("restore.staged")
        if t_dir.exists():
            # Exclusive boundary = the whole workspace directory: EVERY database in it (core and every derived/extension writer)
            # must be idle before the swap; never unlink a live WAL by itself. Derived databases are discarded by the swap and
            # rebuilt from the restored authoritative state; the previous directory stays recoverable next to the target.
            for db_file in sorted(p for p in t_dir.iterdir() if p.is_file() and p.suffix == ".db"):
                try:
                    with closing(sqlite3.connect(db_file, timeout=2)) as conn:
                        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                except sqlite3.OperationalError as exc:
                    raise GenerationFencingConflictError(f"Workspace is busy ({db_file.name}: {exc}); stop writers before restore") from exc
                if busy:
                    raise GenerationFencingConflictError(f"Workspace is busy ({db_file.name}); stop writers before restore")
            with open(intent, "w", encoding="utf-8") as f:  # durable BEFORE the first rename (see recover_interrupted_restore)
                f.write(previous_dir.name)
                f.flush()
                os.fsync(f.fileno())
            os.rename(t_dir, previous_dir)
            moved = True
        hit("restore.before_publish")
        os.rename(stage_dir, t_dir)
    except BaseException:
        if moved and not t_dir.exists():
            os.rename(previous_dir, t_dir)
        raise
    finally:
        if stage_dir.exists():
            _discard_stage(stage_dir)
        if t_dir.exists():
            intent.unlink(missing_ok=True)  # target present again (restored or rolled back): marker is no longer needed

    return new_generation


# -----------------------------------------------------------------------------
# Derived Projection Rebuild (Invariant 11)
# -----------------------------------------------------------------------------
def rebuild_derived_projections(core_store: Any, work_db_path: Path, *, page_size: int = 500) -> int:
    """Rebuild derived work projections by replaying authoritative CoreStore records.

    Invariant 11: Authoritative records are the sole truth; projection tables
    are cleanly replayed from stream records.
    """
    from peerhub.core.store import CoreStore
    from peerhub.extensions.work import WorkProjection

    if isinstance(core_store, (str, Path)):
        core_path = Path(core_store).resolve()
        if not core_path.exists():
            raise AuthoritativeRestoreOrderError(
                "Invariant 11 Violation: Authoritative core store does not exist. Restore authoritative state first."
            )
        core_store = CoreStore(core_path)
    else:
        db_path = getattr(core_store, "db_path", None)
        if not db_path or not Path(db_path).exists():
            raise AuthoritativeRestoreOrderError(
                "Invariant 11 Violation: Authoritative core store does not exist. Restore authoritative state first."
            )

    work_db = Path(work_db_path).resolve()
    work_db.parent.mkdir(parents=True, exist_ok=True)

    projection = WorkProjection(db_path=work_db, store=core_store)

    # Read all stream records from authoritative core store
    try:
        with closing(sqlite3.connect(core_store.db_path)) as c:
            streams = [r[0] for r in c.execute("SELECT stream_id FROM streams").fetchall()]

        from peerhub.core.models import Record

        def replay() -> Iterator[Record]:
            # Positions are per stream (no cross-stream global order exists in the schema), and the projection tracks
            # its position per stream, so stream-by-stream replay is exact. Pages are streamed, never accumulated.
            for stream_id in streams:
                position = 0
                while batch := core_store.read_records(stream_id, after_position=position, limit=page_size):
                    yield from batch
                    position = batch[-1].position

        return projection.rebuild_projection(replay())
    except Exception as e:
        raise AuthoritativeRestoreOrderError(f"Failed to replay authoritative stream records: {e}") from e
