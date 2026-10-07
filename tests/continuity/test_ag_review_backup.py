"""AG review triage regressions: restore TOCTOU, device names, generation rollback/durability, crash recovery."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.core import workspace as ws_mod
from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.core.workspace import (SnapshotInvalidError, Workspace, recover_interrupted_restore,
                                    restore_intent_path)
from peerhub.extensions import backup as backup_mod
from peerhub.extensions.backup import (BackupCorruptedError, create_backup, restore_authoritative, verify_backup)

REPO = Path(__file__).resolve().parents[2]


def make_core(root: Path, peer: str = "p") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    store = CoreStore(root / "core.db")
    store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s", members=[peer]))
    return root / "core.db"


def peers(db: Path) -> list[str]:
    with closing(sqlite3.connect(db)) as c:
        return sorted(r[0] for r in c.execute("SELECT peer_id FROM peers"))


def snapshot(tmp_path: Path, peer: str) -> Path:
    src = make_core(tmp_path / f"snapsrc_{peer}", peer)
    out = tmp_path / f"{peer}.snap"
    with closing(sqlite3.connect(src)) as a, closing(sqlite3.connect(out)) as b:
        a.backup(b)
    return out


# (1) restore validates the bytes it installs
def test_restore_snapshot_validates_the_copy_not_the_source(tmp_path):
    ws = Workspace(tmp_path / "w")
    make_core(ws.root, "live")
    snap = snapshot(tmp_path, "new")
    gen, before = ws.generation(), (ws.root / "core.db").read_bytes()

    def corrupt_copy(point):  # simulate a concurrent writer swapping bytes after the copy was taken
        if point == "restore.after_temp_copy":
            (ws.root / "core.restore.tmp").write_bytes(b"not a database" * 100)

    with pytest.raises(SnapshotInvalidError):
        ws.restore_snapshot(snap, fault=corrupt_copy)
    assert (ws.root / "core.db").read_bytes() == before and ws.generation() == gen
    assert not list(ws.root.glob("*.restore*"))

    def corrupt_source(point):  # control: corrupting the SOURCE after the copy is harmless
        if point == "restore.after_temp_copy":
            snap.write_bytes(b"garbage")
    new = ws.restore_snapshot(snap, fault=corrupt_source)
    assert new != gen and peers(ws.root / "core.db") == ["new"]


# (3) failed replace leaves the generation unchanged
def test_failed_db_replace_leaves_generation_and_state_unchanged(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    make_core(ws.root, "live")
    snap = snapshot(tmp_path, "new")
    gen, before = ws.generation(), (ws.root / "core.db").read_bytes()
    real = os.replace

    def flaky(src, dst, *a, **k):
        if Path(dst) == ws.db_path:
            raise PermissionError("locked")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(ws_mod.os, "replace", flaky)
    with pytest.raises(PermissionError, match="locked"):
        ws.restore_snapshot(snap)
    assert ws.generation() == gen and (ws.root / "core.db").read_bytes() == before
    assert not list(ws.root.glob("*.restore*"))
    monkeypatch.undo()
    assert ws.restore_snapshot(snap) != gen  # positive control


# (6) generation file durability
def test_generation_write_fsyncs_data_before_the_rename(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w")
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(ws_mod.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(ws_mod.os, "replace", lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    new = ws.replace_generation()
    assert events[:2] == ["fsync", "replace"]
    assert ws.generation() == new and not (ws.root / "workspace.tmp").exists()


# (2) Windows device names
@pytest.mark.parametrize("name", ["CON", "con", "NUL", "AUX", "PRN", "COM1", "LPT9", "CON.txt", "nul.tar.gz", "com3.json",
                                  "aux ", "COM¹"])
def test_reserved_device_name_helper_positive(name):
    assert backup_mod._has_reserved_device_name(f"artifacts/{name}")
    assert backup_mod._has_reserved_device_name(f"skills/{name}/x")


@pytest.mark.parametrize("name", ["core.db", "console", "COM10", "COM0", "LPT", "nullable.txt", "a/b/c.json", "auxiliary"])
def test_reserved_device_name_helper_negative_control(name):
    assert not backup_mod._has_reserved_device_name(f"artifacts/{name}")


def _good_bundle(tmp_path):
    db = make_core(tmp_path / "src")
    art = tmp_path / "art"
    art.mkdir()
    (art / "ok.txt").write_text("ok")
    create_backup(db, art, tmp_path / "bundle", skill_dir=tmp_path / "noskills")
    return tmp_path / "bundle"


@pytest.mark.parametrize("bad", ["artifacts/CON", "artifacts/nul.txt", "skills/COM1/x"])
def test_verify_and_restore_reject_device_names_before_any_open(tmp_path, monkeypatch, bad):
    bundle = _good_bundle(tmp_path)
    assert verify_backup(bundle) is True  # control
    mf = bundle / "backup_manifest.json"
    data = json.loads(mf.read_text())
    data["files"][bad] = {"sha256": "0" * 64, "bytes": 0}
    mf.write_text(json.dumps(data))
    touched = []
    real_sha = backup_mod._file_sha256
    monkeypatch.setattr(backup_mod, "_file_sha256",
                        lambda p: (touched.append(Path(p).name) if Path(p).name.upper().startswith(("CON", "NUL", "COM")) else None,
                                   real_sha(p))[1])
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: (touched.append(self.name) if self.name.upper().startswith(
        ("CON", "NUL", "COM")) else None, real_is_file(self))[1])
    with pytest.raises(BackupCorruptedError, match="Unsafe manifest file path"):
        verify_backup(bundle)
    target = tmp_path / "target"
    make_core(target, "live")
    before = (target / "core.db").read_bytes()
    with pytest.raises(BackupCorruptedError, match="Unsafe manifest file path"):
        restore_authoritative(bundle, target)
    assert touched == []  # rejected by name before the path was hashed or even stat'ed
    assert (target / "core.db").read_bytes() == before and not list(tmp_path.glob("target.pre-restore-*"))


def test_create_backup_rejects_device_named_source_file(tmp_path):
    db = make_core(tmp_path / "src")
    art = tmp_path / "art"
    art.mkdir()
    prefix = "\\\\?\\" if os.name == "nt" else ""
    raw = prefix + str(art / "aux.txt")
    try:
        with open(raw, "w") as f:
            f.write("x")
    except OSError:
        pytest.skip("cannot create a device-named file here")
    try:
        with pytest.raises(BackupCorruptedError, match="Reserved device name"):
            create_backup(db, art, tmp_path / "bundle", skill_dir=tmp_path / "noskills")
        assert not (tmp_path / "bundle").exists() and not list(tmp_path.glob("backup_stage_*"))
    finally:
        os.remove(raw)


# (7) hard crash between the two renames
CRASH_SCRIPT = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from peerhub.extensions.backup import restore_authoritative
    def hook(point):
        if point == "restore.before_publish":
            os._exit(7)  # hard kill: no except/finally runs
    restore_authoritative(Path(sys.argv[1]), Path(sys.argv[2]), fault_hook=hook)
""")


def _crash_restore(tmp_path):
    bundle = _good_bundle(tmp_path)
    target = tmp_path / "target"
    make_core(target, "live")
    r = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(bundle), str(target)], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 7, r.stderr
    return bundle, target


def test_hard_crash_between_renames_is_recovered_on_workspace_open(tmp_path):
    bundle, target = _crash_restore(tmp_path)
    assert not target.exists() and len(list(tmp_path.glob("target.pre-restore-*"))) == 1  # the exact hazardous state
    w = Workspace(target)  # startup recovery
    assert peers(target / "core.db") == ["live"] and w.generation()
    assert not list(tmp_path.glob("target.pre-restore-*")) and not restore_intent_path(target).exists()
    assert restore_authoritative(bundle, target) >= 1  # control: a retry restores normally
    assert peers(target / "core.db") == ["p"]


def test_hard_crash_is_recovered_by_restore_entry_point_itself(tmp_path):
    bundle, target = _crash_restore(tmp_path)
    assert not target.exists()
    restore_authoritative(bundle, target)
    assert peers(target / "core.db") == ["p"]


def test_restore_entry_point_recovers_fallback_even_when_the_restore_is_then_rejected(tmp_path):
    bundle, target = _crash_restore(tmp_path)
    assert not target.exists()
    (bundle / "core.db").write_bytes(b"corrupt")  # this restore request must be rejected by verification
    with pytest.raises(BackupCorruptedError):
        restore_authoritative(bundle, target)
    assert peers(target / "core.db") == ["live"]  # the interrupted restore's fallback is back in place
    assert not restore_intent_path(target).exists()


def test_recovery_ignores_unrelated_pre_restore_dirs_and_stale_marker(tmp_path):
    target = tmp_path / "target"
    (tmp_path / "target.pre-restore-old").mkdir()  # leftover from an earlier successful restore, no marker
    assert recover_interrupted_restore(target) is False and not target.exists()
    make_core(target, "live")
    restore_intent_path(target).write_text("target.pre-restore-old")
    assert recover_interrupted_restore(target) is False  # target present: marker is stale
    assert not restore_intent_path(target).exists() and (tmp_path / "target.pre-restore-old").is_dir()
    assert peers(target / "core.db") == ["live"]
    shutil.rmtree(target)
    restore_intent_path(target).write_text("../evil")  # marker naming a foreign dir is never followed
    assert recover_interrupted_restore(target) is False and not target.exists()
