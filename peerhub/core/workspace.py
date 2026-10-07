"""Workspace identity / generation (SQL-009/010, TD-04).

A workspace is a directory holding `core.db` and `workspace.generation`. The generation identifies one lineage of the
authoritative state; a supported restore/replacement assigns a NEW generation, so every owner/session token minted
under the previous generation is stale.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable


class SnapshotInvalidError(RuntimeError):
    """Restore snapshot failed validation; the live workspace was not touched."""


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync (not supported on Windows; failure there must not fail the operation)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def restore_intent_path(root: Path) -> Path:
    """Marker written (durably) before restore_authoritative moves a workspace aside; names the fallback directory."""
    root = Path(root).resolve()
    return root.parent / f"{root.name}.restore-intent"


def recover_interrupted_restore(root: Path) -> bool:
    """Startup recovery for a hard crash between the two renames of an authoritative restore.

    If the intent marker exists and the workspace directory is missing, the pre-restore fallback named by the marker
    is renamed back (the old state returns intact). A marker with the workspace present (crash before the first rename
    or after the second) is just stale and removed. Returns True when a fallback was restored.
    """
    root = Path(root).resolve()
    marker = restore_intent_path(root)
    if not marker.is_file():
        return False
    restored = False
    if not root.exists():
        fallback = root.parent / marker.read_text(encoding="utf-8").strip()
        if fallback.parent == root.parent and fallback.name.startswith(f"{root.name}.pre-restore-") and fallback.is_dir():
            os.rename(fallback, root)
            restored = True
        else:
            return False  # nothing safe to restore; keep the marker for diagnosis
    marker.unlink(missing_ok=True)
    return restored


class Workspace:
    def __init__(self, root: Path) -> None:
        recover_interrupted_restore(Path(root))
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "core.db"
        self._gen = self.root / "workspace.generation"
        if not self._gen.exists():
            self._write_generation(uuid.uuid4().hex)

    def _write_generation(self, value: str) -> None:
        tmp = self._gen.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())  # data durable BEFORE the rename, so a power loss never leaves an empty identity file
        os.replace(tmp, self._gen)  # atomic: readers see the old or the new identity, never a partial one
        _fsync_dir(self.root)

    def generation(self) -> str:
        return self._gen.read_text(encoding="utf-8")

    def replace_generation(self) -> str:
        new = uuid.uuid4().hex
        self._write_generation(new)
        return new

    def _validate_snapshot(self, snapshot: Path) -> None:
        from .migrations import CURRENT_VERSION

        try:
            with closing(sqlite3.connect(Path(snapshot).resolve().as_uri() + "?mode=ro", uri=True)) as c:
                if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SnapshotInvalidError("snapshot failed integrity_check")
                version = c.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as e:
            raise SnapshotInvalidError(f"snapshot is not a readable SQLite database: {e}") from e
        if version > CURRENT_VERSION:
            raise SnapshotInvalidError(f"snapshot schema version {version} is newer than supported {CURRENT_VERSION}")
        missing = {"peers", "streams", "stream_members", "records", "offsets"} - tables
        if missing:
            raise SnapshotInvalidError(f"snapshot lacks Core tables: {sorted(missing)}")

    def restore_snapshot(self, snapshot: Path, fault: Callable[[str], None] | None = None) -> str:
        """Replace the Core database with a validated snapshot under a fresh generation. Callers hold no open connections.

        Crash-safe order: temp copy + fsync -> validate the COPY -> NEW generation -> checkpoint/remove WAL -> atomic replace.
        Crash before the generation write leaves old state + old generation; after it, old state under a new generation
        (old tokens fenced); after replace, the restored state under the new generation. Claims stored in the snapshot
        carry the snapshot's old generation, so they are never valid under the new one.
        """
        def hit(point: str) -> None:
            if fault is not None:
                fault(point)

        tmp = self.root / "core.restore.tmp"
        try:
            with open(snapshot, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            hit("restore.after_temp_copy")
            self._validate_snapshot(tmp)  # validate the exact bytes that will be installed (no check-then-copy window)
            hit("restore.after_validate")
            if self.db_path.exists():  # fold the live WAL into the main file so deleting it loses nothing
                with closing(sqlite3.connect(self.db_path, timeout=2.0)) as c:
                    busy = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                if busy:
                    raise RuntimeError("live database is busy; restore aborted without changes")
            old = self.generation()
            new = uuid.uuid4().hex
            self._write_generation(new)
            hit("restore.after_generation")
            try:
                for suffix in ("-wal", "-shm"):
                    Path(str(self.db_path) + suffix).unlink(missing_ok=True)
                os.replace(tmp, self.db_path)
            except OSError:
                self._write_generation(old)  # the DB was not replaced: do not fence tokens of the still-live state
                raise
            hit("restore.after_replace")
            return new
        finally:
            for suffix in ("", "-wal", "-shm"):  # a read-only validation of a WAL-mode copy can leave sidecars
                Path(str(tmp) + suffix).unlink(missing_ok=True)
