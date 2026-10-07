"""`peerhub store rename-legacy`: rename `.peerhub/m1.db` to `.peerhub/core.db` safely and explicitly (never automatic).

Safety contract: the move happens only when this process is the sole user of the database. Switching the journal mode from WAL to
DELETE needs exclusive access (it fails while any other connection, reader or writer, is open) and folds the write-ahead log into
the main file, leaving ONE file to rename. Existing destination files are never overwritten. If a sidecar reappears after the move
(a writer reopened the old name) the command reports it instead of claiming success.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from peerhub.cli.store_select import DEFAULT_DB_PATH, LEGACY_DB_PATH

SIDECARS = ("-wal", "-shm", "-journal")


def register_store_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("store", help="Store file maintenance")
    sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("rename-legacy", help="Rename .peerhub/m1.db to .peerhub/core.db (stop every PeerHub process first)")
    r.add_argument("--workspace", type=Path, default=None, help="Workspace root holding .peerhub (default: current directory)")


def _sidecars(path: Path) -> list[Path]:
    return [path.with_name(path.name + s) for s in SIDECARS if path.with_name(path.name + s).exists()]


def rename_legacy(workspace: Path) -> dict[str, Any]:
    """Returns {status, ...}; status is one of RENAMED, NOTHING_TO_DO, REFUSED, FAILED."""
    root = Path(workspace).resolve()
    src, dst = root / LEGACY_DB_PATH, root / DEFAULT_DB_PATH
    if not src.is_file():
        return {"status": "NOTHING_TO_DO", "reason": f"{src} does not exist"}
    blockers = [str(p) for p in ([dst] if dst.exists() else []) + _sidecars(dst)]
    if blockers:
        return {"status": "REFUSED", "reason": "destination already exists; nothing was changed", "existing": blockers}
    try:
        with closing(sqlite3.connect(src, timeout=0, isolation_level=None)) as conn:
            mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
    except sqlite3.OperationalError as exc:
        return {"status": "REFUSED", "reason": f"the database is in use by another process or cannot be switched to a single file: {exc}"}
    if mode != "delete" or _sidecars(src):
        return {"status": "REFUSED", "reason": "the database could not be reduced to a single file (another process still uses it)",
                "leftover": [str(p) for p in _sidecars(src)]}
    try:
        os.replace(src, dst)
    except OSError as exc:  # Windows: another process still holds the file
        return {"status": "REFUSED", "reason": f"the file is in use and was not renamed: {exc}"}
    stray = _sidecars(src)
    if stray:
        return {"status": "FAILED", "reason": "a process reopened the old name during the rename; stop every PeerHub process and inspect the files",
                "stray": [str(p) for p in stray], "renamed_to": str(dst)}
    return {"status": "RENAMED", "from": str(src), "to": str(dst)}


def run_store(args: argparse.Namespace) -> int:
    result = rename_legacy(args.workspace or Path.cwd())
    print(json.dumps(result, indent=2, ensure_ascii=True))
    if result["status"] in ("REFUSED", "FAILED"):
        print(f"store rename-legacy {result['status']}: {result['reason']}", file=sys.stderr)
    return {"RENAMED": 0, "NOTHING_TO_DO": 0, "REFUSED": 1, "FAILED": 1}[result["status"]]
