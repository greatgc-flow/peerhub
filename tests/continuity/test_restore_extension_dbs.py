"""Exclusive restore boundary = the whole workspace directory: every database in it must be idle, derived databases are
discarded by the swap (and rebuilt), a hard crash between the renames recovers the previous directory with ALL its databases."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.core.workspace import recover_interrupted_restore
from peerhub.extensions.backup import (BackupCorruptedError, GenerationFencingConflictError, create_backup,
                                       restore_authoritative)

REPO = Path(__file__).resolve().parents[2]
DERIVED = ("work.db", "memory.db", "search.db", "skills.db", "host.db")


def make_core(root: Path, peer: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    store = CoreStore(root / "core.db")
    store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s", members=[peer]))
    return root / "core.db"


def add_derived(root: Path) -> None:
    for name in DERIVED:
        with closing(sqlite3.connect(root / name)) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS t(v)")
            c.execute("INSERT INTO t VALUES ('live')")
            c.commit()


def bundle(tmp_path: Path) -> Path:
    db = make_core(tmp_path / "src", "restored")
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.txt").write_text("a")
    create_backup(db, art, tmp_path / "bundle", skill_dir=tmp_path / "noskills")
    return tmp_path / "bundle"


def peers(db: Path) -> list[str]:
    with closing(sqlite3.connect(db)) as c:
        return sorted(r[0] for r in c.execute("SELECT peer_id FROM peers"))


def test_restore_succeeds_over_a_workspace_with_derived_databases_and_discards_them(tmp_path):
    target = tmp_path / "target"
    make_core(target, "live")
    add_derived(target)
    restore_authoritative(bundle(tmp_path), target)
    assert peers(target / "core.db") == ["restored"]
    assert not [p.name for p in target.iterdir() if p.name in DERIVED]  # projections are rebuilt, never preserved
    previous = list(tmp_path.glob("target.pre-restore-*"))
    assert len(previous) == 1 and all((previous[0] / n).is_file() for n in DERIVED)  # recoverable prior state keeps them


def test_restore_refuses_while_any_extension_database_has_an_active_writer(tmp_path):
    target = tmp_path / "target"
    make_core(target, "live")
    add_derived(target)
    holder = sqlite3.connect(target / "memory.db", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES ('uncommitted')")
        with pytest.raises(GenerationFencingConflictError):
            restore_authoritative(bundle(tmp_path), target)
    finally:
        holder.close()
    assert peers(target / "core.db") == ["live"] and (target / "memory.db").is_file()  # untouched
    assert not list(tmp_path.glob("target.pre-restore-*"))


def test_foreign_files_still_make_the_target_unmanaged(tmp_path):
    target = tmp_path / "target"
    make_core(target, "live")
    (target / "notes.txt").write_text("not a workspace file")
    with pytest.raises(BackupCorruptedError):
        restore_authoritative(bundle(tmp_path), target)
    assert (target / "notes.txt").is_file()


CRASH = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from peerhub.extensions.backup import restore_authoritative
    def hook(point):
        if point == "restore.before_publish":
            os._exit(7)
    restore_authoritative(Path(sys.argv[1]), Path(sys.argv[2]), fault_hook=hook)
""")


def test_hard_crash_between_renames_recovers_every_database_of_the_previous_workspace(tmp_path):
    target = tmp_path / "target"
    make_core(target, "live")
    add_derived(target)
    b = bundle(tmp_path)
    r = subprocess.run([sys.executable, "-c", CRASH, str(b), str(target)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 7, r.stderr
    assert not target.exists()  # the crash left no target
    recover_interrupted_restore(target)
    assert peers(target / "core.db") == ["live"]
    for name in DERIVED:
        with closing(sqlite3.connect(target / name)) as c:
            assert c.execute("SELECT v FROM t").fetchall() == [("live",)]


def test_a_stale_intent_marker_after_a_completed_swap_never_reverts_the_restored_workspace(tmp_path):
    """Crash after BOTH renames but before the marker was deleted: recovery must keep the restored state."""
    target = tmp_path / "target"
    make_core(target, "live")
    restore_authoritative(bundle(tmp_path), target)  # a completed restore; the previous directory is kept next to it
    previous = next(tmp_path.glob("target.pre-restore-*"))
    from peerhub.core.workspace import restore_intent_path

    restore_intent_path(target).write_text(previous.name, encoding="utf-8")  # the marker the crash left behind
    assert recover_interrupted_restore(target) is False  # nothing is reverted because the workspace exists
    assert peers(target / "core.db") == ["restored"] and previous.is_dir()
    assert not restore_intent_path(target).exists()  # the stale marker is just removed
