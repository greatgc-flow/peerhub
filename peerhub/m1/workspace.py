"""Workspace identity / generation (SQL-009/010, TD-04).

A workspace is a directory holding `core.db` and `workspace.generation`. The generation identifies one lineage of the
authoritative state; a supported restore/replacement assigns a NEW generation, so every owner/session token minted
under the previous generation is stale.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path


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

    def restore_snapshot(self, snapshot: Path) -> str:
        """Replace the Core database with a snapshot file and assign a fresh generation. Callers hold no open connections."""
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        shutil.copyfile(snapshot, self.db_path)
        return self.replace_generation()
