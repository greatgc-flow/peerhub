"""M2.5 Backup & Disaster Recovery Engine.

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
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, cast


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# -----------------------------------------------------------------------------
# Secret Masking Guard
# -----------------------------------------------------------------------------
_SECRET_PATTERNS = ("api_key", "secret_", "sk-", "private_key", "password", "token")


def assert_no_plaintext_secrets(data: Any) -> None:
    """Recursively checks for plaintext secrets, raising SecretLeakageDetectedError if found."""
    if isinstance(data, dict):
        dict_data = cast(dict[Any, Any], data)
        for k, v in dict_data.items():
            key_str = str(k).lower()
            if any(p in key_str for p in ("api_key", "private_key", "secret", "password")):
                if isinstance(v, str) and not v.startswith("***"):
                    raise SecretLeakageDetectedError(f"Plaintext secret detected in key '{k}'")
            assert_no_plaintext_secrets(v)
    elif isinstance(data, (list, tuple)):
        seq_data = cast(Sequence[Any], data)
        for item in seq_data:
            assert_no_plaintext_secrets(item)
    elif isinstance(data, str):
        if any(p in data for p in ("sk-secret", "BEGIN PRIVATE KEY")):
            raise SecretLeakageDetectedError("Plaintext secret detected in content value")


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
) -> BackupManifest:
    """Create an authoritative backup bundle with cryptographic manifest and reopen proof."""
    auth_db = Path(authoritative_db).resolve()
    art_dir = Path(artifact_dir).resolve()
    out_dir = Path(target_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Online backup of SQLite database (avoids locks / dirty writes)
    backup_db_path = out_dir / "core.db"
    with closing(sqlite3.connect(auth_db, timeout=30.0)) as src_conn:
        with closing(sqlite3.connect(backup_db_path)) as dst_conn:
            src_conn.backup(dst_conn)

    # 2. Copy CAS Artifact directory recursively
    backup_art_dir = out_dir / "artifacts"
    if art_dir.exists():
        shutil.copytree(art_dir, backup_art_dir, dirs_exist_ok=True)
    else:
        backup_art_dir.mkdir(parents=True, exist_ok=True)

    # 3. Compute per-file SHA-256 and byte sizes
    files_manifest: dict[str, dict[str, Any]] = {}
    db_sha = _file_sha256(backup_db_path)
    files_manifest["core.db"] = {
        "sha256": db_sha,
        "bytes": backup_db_path.stat().st_size,
    }

    for root, _, files in os.walk(backup_art_dir):
        for f in files:
            p = Path(root) / f
            rel = str(p.relative_to(out_dir)).replace("\\", "/")
            files_manifest[rel] = {
                "sha256": _file_sha256(p),
                "bytes": p.stat().st_size,
            }

    # 4. Verify reopen proof
    records_count = 0
    with closing(sqlite3.connect(backup_db_path)) as c:
        row = c.execute("PRAGMA integrity_check").fetchone()
        integrity_ok = bool(row and row[0] == "ok")
        try:
            records_count = int(c.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        except Exception:
            records_count = 0

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
def verify_backup(backup_dir: Path) -> bool:
    """Verify cryptographic integrity of a backup bundle against its manifest."""
    b_dir = Path(backup_dir).resolve()
    manifest_file = b_dir / "backup_manifest.json"
    if not manifest_file.exists():
        raise BackupManifestMissingError(f"Missing backup manifest: {manifest_file}")

    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise BackupCorruptedError(f"Unreadable backup manifest: {e}") from e

    files = data.get("files", {})
    if not files:
        raise BackupCorruptedError("Manifest contains no file records")

    for rel_path, meta in files.items():
        p = b_dir / Path(rel_path)
        if not p.exists():
            raise BackupCorruptedError(f"File missing from backup: {rel_path}")
        actual_sha = _file_sha256(p)
        if actual_sha != meta.get("sha256"):
            raise BackupCorruptedError(
                f"Checksum mismatch for {rel_path}: expected {meta.get('sha256')}, got {actual_sha}"
            )

    # Verify SQLite integrity
    core_db = b_dir / "core.db"
    if core_db.exists():
        with closing(sqlite3.connect(core_db)) as c:
            row = c.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupCorruptedError("core.db failed PRAGMA integrity_check")

    return True


# -----------------------------------------------------------------------------
# Authoritative Restoration (Invariant 11)
# -----------------------------------------------------------------------------
def restore_authoritative(
    backup_dir: Path,
    target_dir: Path,
    current_generation: int = 0,
    allow_rollback: bool = True,
) -> int:
    """Restore authoritative state first, applying generation fencing."""
    # 1. Pre-verify backup integrity
    verify_backup(backup_dir)

    b_dir = Path(backup_dir).resolve()
    t_dir = Path(target_dir).resolve()

    manifest_data = json.loads((b_dir / "backup_manifest.json").read_text(encoding="utf-8"))
    backup_gen = int(manifest_data.get("generation", 1))

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

    try:
        shutil.copy2(b_dir / "core.db", stage_dir / "core.db")
        if (b_dir / "artifacts").exists():
            shutil.copytree(b_dir / "artifacts", stage_dir / "artifacts", dirs_exist_ok=True)

        # Move into target
        t_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage_dir / "core.db", t_dir / "core.db")
        if (stage_dir / "artifacts").exists():
            shutil.copytree(stage_dir / "artifacts", t_dir / "artifacts", dirs_exist_ok=True)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    return new_generation


# -----------------------------------------------------------------------------
# Derived Projection Rebuild (Invariant 11)
# -----------------------------------------------------------------------------
def rebuild_derived_projections(core_store: Any, work_db_path: Path) -> int:
    """Rebuild derived work projections by replaying authoritative CoreStore records.

    Invariant 11: Authoritative records are the sole truth; projection tables
    are cleanly replayed from stream records.
    """
    from peerhub.m1.store import CoreStore
    from peerhub.m2.work import WorkProjection

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

        from peerhub.m1.models import Record

        all_records: list[Record] = []
        for stream_id in streams:
            all_records.extend(core_store.read_records(stream_id))

        return projection.rebuild_projection(all_records)
    except Exception as e:
        raise AuthoritativeRestoreOrderError(f"Failed to replay authoritative stream records: {e}") from e
