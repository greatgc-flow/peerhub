"""Wave 7: MIG-003 future major schema / unsupported downgrade fail explicitly + side-by-side cutover and rollback rules
(MIGRATION_CUTOVER.md; RELEASE_PROMOTION_ROLLBACK.md "Rollback" principles 1-4)."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.m1.migrations import CURRENT_VERSION, SchemaVersionError, run_migrations
from peerhub.m1.models import Peer
from peerhub.m1.store import CoreStore
from peerhub.m1.workspace import SnapshotInvalidError, Workspace
from tests.m1.harness.legacy_fixture import make_legacy, raw_dump, tree_fingerprint
from tests.m1.harness.migration_fixtures import FIXTURE_MIGRATIONS

pytestmark = [pytest.mark.migration, pytest.mark.compatibility]

FUTURE_VERSIONS = [CURRENT_VERSION + 1, CURRENT_VERSION + 7, 100, 2**31 - 1]  # SQLite user_version is a signed 32-bit int


def _make_db(path, *, user_version=None, journal="wal"):
    st = CoreStore(path)
    st.register_peer(Peer(peer_id="p1", display_name="kept"))
    with closing(sqlite3.connect(path)) as c:
        c.execute(f"PRAGMA journal_mode = {journal}")
        if user_version is not None:
            c.execute(f"PRAGMA user_version = {int(user_version)}")
        c.commit()
    return path


def _in(base, d, name):
    (base / d).mkdir(exist_ok=True)
    return base / d / name


def _uv(path):
    with closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _journal(path):
    with closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA journal_mode").fetchone()[0]


@pytest.mark.parametrize("journal", ["delete", "wal"])
@pytest.mark.parametrize("version", FUTURE_VERSIONS)
def test_mig_003_future_major_schema_is_refused_with_actionable_error_and_no_writes(tmp_path, version, journal):
    db = _make_db(_in(tmp_path, "ws", "core.db"), user_version=version, journal=journal)
    before = tree_fingerprint(db.parent)
    with pytest.raises(SchemaVersionError) as ei:
        CoreStore(db)
    msg = str(ei.value)
    assert str(version) in msg and str(CURRENT_VERSION) in msg  # names found and supported versions
    assert "upgrade peerhub" in msg.lower() and "downgrade" in msg.lower() and "backup" in msg.lower()  # actionable
    assert tree_fingerprint(db.parent) == before  # not one byte or file changed (no WAL files, no repair)
    assert _uv(db) == version and _journal(db) == journal  # no journal-mode conversion, no version rewrite
    with pytest.raises(SchemaVersionError):
        run_migrations(db)  # the migration entrypoint itself refuses too
    assert tree_fingerprint(db.parent) == before


def test_mig_003_supported_versions_are_not_refused_positive_control(tmp_path):
    db = _make_db(tmp_path / "core.db", user_version=CURRENT_VERSION)
    assert CoreStore(db).get_peer("p1").display_name == "kept"
    CoreStore(db).register_peer(Peer(peer_id="p2"))  # and it is writable
    assert _uv(db) == CURRENT_VERSION


def test_mig_003_newer_database_opened_by_older_runtime_is_not_downgraded_or_modified(tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db).register_peer(Peer(peer_id="p1", display_name="kept"))
    run_migrations(db, FIXTURE_MIGRATIONS)  # "newer code" upgrades the store to N+1 (adds peers.note + an index)
    newer = CURRENT_VERSION + 1
    assert _uv(db) == newer
    before = tree_fingerprint(db.parent)
    with pytest.raises(SchemaVersionError):
        CoreStore(db)  # the current (older) runtime must not guess
    assert tree_fingerprint(db.parent) == before
    with closing(sqlite3.connect(db)) as c:  # data and the newer schema survive untouched: no destructive downgrade
        assert "note" in [r[1] for r in c.execute("PRAGMA table_info(peers)")]
        assert c.execute("SELECT display_name FROM peers").fetchall() == [("kept",)]
        assert c.execute("SELECT name FROM sqlite_master WHERE name='idx_records_author'").fetchone() is not None
    assert run_migrations(db, FIXTURE_MIGRATIONS) == []  # the newer runtime still opens it (control)


def test_mig_003_read_only_diagnostics_do_not_interpret_a_future_schema(tmp_path):
    from peerhub.extensions.diag import ReadonlyDiag

    ok = _make_db(_in(tmp_path, "ok", "core.db"), user_version=CURRENT_VERSION)
    assert ReadonlyDiag(ok).render(["streams"]).status != "FAILED"  # positive control
    fut = _make_db(_in(tmp_path, "fut", "core.db"), user_version=CURRENT_VERSION + 1)
    before = tree_fingerprint(fut.parent)
    rep = ReadonlyDiag(fut).render(["streams"])
    assert rep.status == "FAILED" and "schema version" in rep.error.lower() and str(CURRENT_VERSION + 1) in rep.error
    after = tree_fingerprint(fut.parent)  # a read-only WAL reader may leave an empty -wal and a -shm index; the database file is untouched
    empty = hashlib.sha256(b"").hexdigest()  # -shm is only the wal-index (no data); an empty -wal holds nothing
    assert {k: v for k, v in after.items() if not (k.endswith("-shm") or (k.endswith("-wal") and v == empty))} == before


def test_mig_003_restore_of_a_future_snapshot_is_refused_and_live_state_is_unchanged(tmp_path):
    ws = Workspace(tmp_path / "ws")
    CoreStore(ws.db_path).register_peer(Peer(peer_id="live"))
    snap = _make_db(tmp_path / "snap.db", user_version=CURRENT_VERSION + 1)
    gen, before = ws.generation(), tree_fingerprint(ws.root)
    with pytest.raises(SnapshotInvalidError, match="newer"):
        ws.restore_snapshot(snap)
    assert ws.generation() == gen and tree_fingerprint(ws.root) == before
    good = _make_db(tmp_path / "good.db", user_version=CURRENT_VERSION)
    assert ws.restore_snapshot(good) != gen  # positive control: a supported snapshot restores under a new generation


def test_mig_003_cli_reports_future_schema_with_a_dedicated_exit_code_and_no_writes(tmp_path, capsys):
    from peerhub.m1_cli import main

    db = _make_db(tmp_path / "core.db", user_version=CURRENT_VERSION + 1)
    before = tree_fingerprint(tmp_path)
    assert main(["--db", str(db), "peer", "get", "--id", "p1"]) == 6
    err = capsys.readouterr().err
    assert "SCHEMA VERSION" in err and "upgrade" in err.lower()
    assert tree_fingerprint(tmp_path) == before
    ok = _make_db(tmp_path / "ok.db", user_version=CURRENT_VERSION)  # control
    assert main(["--db", str(ok), "peer", "get", "--id", "p1"]) == 0


# ------------------------------------------------------------------ cutover (side-by-side, MIGRATION_CUTOVER.md)
LEGACY_PACKAGES = ["adapters", "application", "cli", "config_data", "core", "dispatch", "events", "governance", "health",
                   "persistence", "routing", "state", "telemetry"]


def test_mig_003_cutover_is_side_by_side_legacy_code_stays_importable():
    import importlib

    import peerhub

    root = __import__("pathlib").Path(peerhub.__file__).parent
    for pkg in LEGACY_PACKAGES:
        assert (root / pkg).is_dir(), pkg
        importlib.import_module(f"peerhub.{pkg}")
    from peerhub.cli import main as legacy_main  # legacy entrypoint and the M1 entrypoint coexist
    from peerhub.m1_cli import main as m1_main

    assert legacy_main is not m1_main


def test_mig_003_rollback_after_cutover_discards_m1_target_and_legacy_source_is_still_usable(tmp_path):
    """Rollback principle: binary and data rollback are separate; the importer never touches the legacy store, so rolling
    back to v0 is 'stop using the M1 target' -- the legacy database is still a valid, complete, openable v0 store."""
    from peerhub.m1.legacy_import import LegacyImporter
    from peerhub.persistence.sqlite import SqliteStateStore

    src = make_legacy(_in(tmp_path, "legacy", "l.db"))
    before = tree_fingerprint(src.parent)
    target = tmp_path / "m1" / "core.db"
    LegacyImporter(src, target).apply()
    target.unlink()  # roll back the cutover: drop the M1 data only
    assert tree_fingerprint(src.parent) == before
    home_id = raw_dump(src, "workspace_identity", "singleton", "workspace_home_id")[0][0]  # identity the legacy runtime bound
    SqliteStateStore(src, workspace_home_id=home_id).initialize()  # the legacy runtime still opens it (idempotent, nothing to migrate)
    assert raw_dump(src, "event_log", "outbox_position", "event_id") == [("e1",), ("e2",), ("e3",)]
    # and a re-cutover after the rollback is a clean, complete import again
    rep = LegacyImporter(src, target).apply()
    assert rep["totals"]["imported_units"] == 2


def test_mig_003_importer_refuses_a_future_schema_m1_target_without_writes(tmp_path):
    from peerhub.m1.legacy_import import LegacyImporter

    src = make_legacy(tmp_path / "l.db")
    target = _make_db(_in(tmp_path, "m1", "core.db"), user_version=CURRENT_VERSION + 1)
    before = tree_fingerprint(tmp_path)
    for call in ("dry_run", "apply"):
        with pytest.raises(SchemaVersionError):
            getattr(LegacyImporter(src, target), call)()
    assert tree_fingerprint(tmp_path) == before
    assert json.dumps(raw_dump(target, "peers", "peer_id", "peer_id")) == '[["p1"]]'
