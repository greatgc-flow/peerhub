"""Workspace identity / generation (SQL-009/010, TD-04).

A workspace is a directory holding `core.db` and `workspace.generation`. The generation identifies one lineage of the
authoritative state; a supported restore/replacement assigns a NEW generation, so every owner/session token minted
under the previous generation is stale.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable


class SnapshotInvalidError(RuntimeError):
    """Restore snapshot failed validation; the live workspace was not touched."""


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "core.db"
        self._gen = self.root / "workspace.generation"
        if not self._gen.exists():
            self._write_generation(uuid.uuid4().hex)

    def _write_generation(self, value: str) -> None:
        tmp = self._gen.with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        os.replace(tmp, self._gen)  # atomic: readers see the old or the new identity, never a partial one

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

        Crash-safe order: validate -> temp copy + fsync -> NEW generation -> checkpoint/remove WAL -> atomic replace.
        Crash before the generation write leaves old state + old generation; after it, old state under a new generation
        (old tokens fenced); after replace, the restored state under the new generation. Claims stored in the snapshot
        carry the snapshot's old generation, so they are never valid under the new one.
        """
        def hit(point: str) -> None:
            if fault is not None:
                fault(point)

        self._validate_snapshot(snapshot)
        hit("restore.after_validate")
        tmp = self.root / "core.restore.tmp"
        try:
            with open(snapshot, "rb") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            hit("restore.after_temp_copy")
            if self.db_path.exists():  # fold the live WAL into the main file so deleting it loses nothing
                with closing(sqlite3.connect(self.db_path, timeout=2.0)) as c:
                    busy = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                if busy:
                    raise RuntimeError("live database is busy; restore aborted without changes")
            new = uuid.uuid4().hex
            self._write_generation(new)
            hit("restore.after_generation")
            for suffix in ("-wal", "-shm"):
                Path(str(self.db_path) + suffix).unlink(missing_ok=True)
            os.replace(tmp, self.db_path)
            hit("restore.after_replace")
            return new
        finally:
            tmp.unlink(missing_ok=True)
