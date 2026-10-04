"""Wave 6 storage faults: FLT-001/002 (crash around append commit), FLT-009 (busy), FLT-011 (full), FLT-012 (read-only),
FLT-013 (corrupt). Real process death (os._exit), real lock holders, real max_page_count / chmod / corrupted bytes.
Every negative has a positive control and checks unchanged state against raw-SQL / hashlib oracles."""
import hashlib
import os
import shutil
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.m1 import store as core_store
from peerhub.m1.store import CoreStore, IdempotencyConflictError
from tests.m1.fakes import CrashInjected, CrashInjector
from tests.m1.harness.core import CoreHarness
from tests.m1.harness.crash_workers import CRASH_EXIT, core_append_crash_worker, spawn_exitcode
from tests.m1.helpers import req, seed

pytestmark = [pytest.mark.fault, pytest.mark.sqlite]


# --------------------------------------------------------------------------- independent oracles (raw SQL / hashlib only)
def raw(db, q, args=()):
    with closing(sqlite3.connect(db)) as c:
        return c.execute(q, args).fetchall()


def snapshot(db):
    """(per-table row counts, per-table sha256 over raw rows): computed here, not by the implementation."""
    out = {}
    with closing(sqlite3.connect(db)) as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1").fetchall():
            h = hashlib.sha256()
            rows = c.execute(f'SELECT * FROM "{t}" ORDER BY 1,2').fetchall()
            for r in rows:
                h.update(repr(r).encode())
            out[t] = (len(rows), h.hexdigest())
    return out


def file_sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def rec_rows(db, stream="s"):
    return raw(db, "SELECT record_id, position, idempotency_key FROM records WHERE stream_id=? ORDER BY position", (stream,))


def seeded(tmp_path, n=3, name="ws", harness_cls=CoreHarness):
    h = harness_cls(tmp_path / name)
    seed(h)
    for i in range(n):
        h.append_record(req(body=f"m{i}", key=f"k{i}"))
    return h


def release_files(h):
    """Drop the harness' store so no connection pins the WAL (sqlite deletes -wal/-shm when the last connection closes)."""
    h.store = None
    for suffix in ("-wal", "-shm"):
        assert not Path(str(h.db_path) + suffix).exists(), "WAL must be folded before the file-level fault is applied"


# =========================================================================== FLT-001
@pytest.mark.m1_id("FLT-001")
def test_flt_001_real_process_death_before_append_commit_leaves_no_record(tmp_path):
    h = seeded(tmp_path)
    before, prior = snapshot(h.db_path), rec_rows(h.db_path)
    request = req(body="doomed", key="doomed")
    assert spawn_exitcode(core_append_crash_worker, (str(h.db_path), request, "append.before_commit")) == CRASH_EXIT  # died AT the barrier
    assert rec_rows(h.db_path) == prior and snapshot(h.db_path) == before  # no uncommitted Record visible, nothing else changed
    assert raw(h.db_path, "SELECT COUNT(*) FROM records WHERE idempotency_key='doomed'") == [(0,)]
    assert raw(h.db_path, "PRAGMA integrity_check") == [("ok",)]
    h2 = CoreHarness(tmp_path / "ws")  # restart + clean retry commits exactly once
    rec = h2.append_record(request)
    rows = rec_rows(h2.db_path)
    assert len(rows) == len(prior) + 1 and rows[-1][2] == "doomed"
    assert rec.position > max(p[1] for p in prior) and rec.position == rows[-1][1]  # unique, greater than every committed position
    assert h2.append_record(request).record_id == rec.record_id and len(rec_rows(h2.db_path)) == len(prior) + 1


@pytest.mark.m1_id("FLT-001")
def test_flt_001_positive_control_crash_after_commit_keeps_the_record(tmp_path):
    h = seeded(tmp_path)
    n = len(rec_rows(h.db_path))
    assert spawn_exitcode(core_append_crash_worker, (str(h.db_path), req(body="kept", key="kept"), "append.after_commit")) == CRASH_EXIT
    assert len(rec_rows(h.db_path)) == n + 1  # the same worker DOES persist when death comes after COMMIT: the barrier test is not vacuous
    assert spawn_exitcode(core_append_crash_worker, (str(h.db_path), req(body="x", key="never"), "no.such.point")) == 0
    assert len(rec_rows(h.db_path)) == n + 2


@pytest.mark.parametrize("point", ["append.begin", "append.before_commit"])
@pytest.mark.m1_id("FLT-001")
def test_flt_001_injected_crash_never_swallowed_and_state_clean(tmp_path, point):
    h = seeded(tmp_path)
    before = snapshot(h.db_path)
    inj = CrashInjector(point)
    store = CoreStore(h.db_path, fault_hook=inj)
    with pytest.raises(CrashInjected):  # BaseException: no `except Exception` in the store may swallow it
        store.append_record(**req(body="x", key="kx"))
    assert inj.fired == [point] and snapshot(h.db_path) == before
    inj.disarm()
    assert store.append_record(**req(body="x", key="kx")).position == len(rec_rows(h.db_path))  # clean retry on the same store object


# =========================================================================== FLT-002
@pytest.mark.m1_id("FLT-002")
def test_flt_002_lost_response_after_commit_recovers_by_idempotency(tmp_path):
    h = seeded(tmp_path)
    request = req(body={"a": [1, 2]}, key="lost")
    inj = CrashInjector("append.after_commit")
    with pytest.raises(CrashInjected):
        CoreStore(h.db_path, fault_hook=inj).append_record(**request)  # committed, but the client never got the Record
    assert inj.fired == ["append.after_commit"]
    committed = [r for r in rec_rows(h.db_path) if r[2] == "lost"]
    assert len(committed) == 1
    got = CoreStore(h.db_path).append_record(**request)  # exact retry after reconnect
    assert (got.record_id, got.position) == committed[0][:2]
    assert [r for r in rec_rows(h.db_path) if r[2] == "lost"] == committed  # count remains 1, same row
    # negative control: only the EXACT request is recovered; a changed payload under the same key conflicts and writes nothing
    before = snapshot(h.db_path)
    with pytest.raises(IdempotencyConflictError):
        CoreStore(h.db_path).append_record(**req(body={"a": [1, 3]}, key="lost"))
    assert snapshot(h.db_path) == before


# =========================================================================== FLT-009
def _hold_write_lock(db):
    c = sqlite3.connect(db, timeout=0.1, isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    return c


@pytest.mark.m1_id("FLT-009")
def test_flt_009_busy_database_explicit_failure_when_wait_exceeds_timeout(tmp_path):
    h = seeded(tmp_path)
    before, off = snapshot(h.db_path), h.get_offset("b", "s")
    store = CoreStore(h.db_path, busy_timeout_ms=150)
    holder = _hold_write_lock(h.db_path)
    try:
        for op in (lambda: store.append_record(**req(body="x", key="busy")),
                   lambda: store.advance_offset_cas("b", "s", 1, off.revision),
                   lambda: store.cas_stream("s", 1, {"title": "t"})):
            with pytest.raises(sqlite3.OperationalError) as ei:
                op()
            assert ei.value.sqlite_errorcode == sqlite3.SQLITE_BUSY and "locked" in str(ei.value)  # explicit busy failure
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert snapshot(h.db_path) == before and h.get_offset("b", "s") == off  # no partial row / offset / stream change
    rec = store.append_record(**req(body="x", key="busy"))  # positive control: same call succeeds once the lock is gone
    assert rec.position == len(rec_rows(h.db_path)) and sum(1 for r in rec_rows(h.db_path) if r[2] == "busy") == 1
    assert raw(h.db_path, "PRAGMA integrity_check") == [("ok",)]


@pytest.mark.m1_id("FLT-009")
def test_flt_009_busy_database_append_waits_and_commits_fully_within_timeout(tmp_path):
    h = seeded(tmp_path)
    n = len(rec_rows(h.db_path))
    attempting, result = threading.Event(), {}

    def hook(point):
        if point == "append.begin":
            attempting.set()

    store = CoreStore(h.db_path, fault_hook=hook, busy_timeout_ms=20000)
    holder = _hold_write_lock(h.db_path)
    holder.execute("INSERT INTO peers (peer_id, metadata_json, created_at) VALUES ('ghost','{}','2026-10-01T00:00:00Z')")  # uncommitted

    def run():
        try:
            result["rec"] = store.append_record(**req(body="w", key="waits"))
        except BaseException as e:  # pragma: no cover - asserted below
            result["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert attempting.wait(20)
    t.join(0.3)
    assert t.is_alive() and not result  # blocked on the lock: it cannot commit while the holder owns the write lock
    assert len(rec_rows(h.db_path)) == n
    holder.execute("ROLLBACK")
    holder.close()
    t.join(30)
    assert not t.is_alive() and "err" not in result, result
    rows = rec_rows(h.db_path)
    assert len(rows) == n + 1 and rows[-1][:2] == (result["rec"].record_id, result["rec"].position)
    assert raw(h.db_path, "SELECT COUNT(*) FROM peers WHERE peer_id='ghost'") == [(0,)]  # the holder's rolled-back write never leaked


# =========================================================================== FLT-011
def _limit_to_current_size(db):
    with closing(sqlite3.connect(db)) as c:
        pages = c.execute("PRAGMA page_count").fetchone()[0]
    return pages, (lambda conn: conn.execute(f"PRAGMA max_page_count = {pages}"))


@pytest.mark.m1_id("FLT-011")
def test_flt_011_disk_full_during_append_is_atomic_and_restart_readable(tmp_path):
    h = seeded(tmp_path)
    release_files(h)
    pages, init = _limit_to_current_size(h.db_path)
    before, prior = snapshot(h.db_path), rec_rows(h.db_path)
    small = CoreStore(h.db_path, conn_init=init)
    big = req(body="X" * 400_000, key="big")
    with pytest.raises(core_store.StorageFullError) as ei:  # the real SQLite engine reports SQLITE_FULL
        small.append_record(**big)
    assert isinstance(ei.value.__cause__, sqlite3.OperationalError) and ei.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert snapshot(h.db_path) == before and rec_rows(h.db_path) == prior  # no partial row, no idempotency leftover
    assert raw(h.db_path, "PRAGMA integrity_check") == [("ok",)]
    assert raw(h.db_path, "SELECT COUNT(*) FROM records WHERE idempotency_key='big'") == [(0,)]
    # restart without the limit: complete prior state is readable, and the SAME request now commits exactly once
    fresh = CoreStore(h.db_path)
    assert [r.record_id for r in fresh.read_records("s", 0, 100)] == [r[0] for r in prior]
    rec = fresh.append_record(**big)
    rows = rec_rows(h.db_path)
    assert len(rows) == len(prior) + 1 and rec.position > max(p[1] for p in prior) and rows[-1][2] == "big"
    assert fresh.append_record(**big).record_id == rec.record_id and len(rec_rows(h.db_path)) == len(prior) + 1


@pytest.mark.m1_id("FLT-011")
def test_flt_011_positive_control_limit_alone_does_not_break_reads_or_replays(tmp_path):
    h = seeded(tmp_path)
    release_files(h)
    _, init = _limit_to_current_size(h.db_path)
    small = CoreStore(h.db_path, conn_init=init)
    assert len(small.read_records("s", 0, 100)) == 3  # reads work under the same limit
    again = small.append_record(**req(body="m1", key="k1"))  # replay of an existing request writes nothing and succeeds
    assert again.idempotency_key == "k1" and len(rec_rows(h.db_path)) == 3
    with pytest.raises(core_store.StorageFullError):  # while a write that needs new pages fails with the precise error type
        small.append_record(**req(body="Y" * 400_000, key="bigger"))
    assert len(rec_rows(h.db_path)) == 3


# =========================================================================== FLT-012
def make_writable(db: Path) -> None:
    """Undo the read-only fault for the WHOLE database family: SQLite creates the -wal/-shm sidecars with the main file's mode
    (0444 on POSIX), so restoring only the main file would leave the sidecars read-only."""
    for suffix in ("", "-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            os.chmod(side, stat.S_IWRITE | stat.S_IREAD)


@pytest.fixture
def readonly_db(tmp_path):
    h = seeded(tmp_path)
    release_files(h)
    db = Path(h.db_path)
    os.chmod(db, stat.S_IREAD)
    try:
        yield h, db
    finally:
        make_writable(db)


@pytest.mark.m1_id("FLT-012")
def test_flt_012_read_only_filesystem_rejects_mutation_without_replacing_the_database(tmp_path, readonly_db):
    h, db = readonly_db
    sha, st = file_sha(db), db.stat()
    ident = (st.st_ino, st.st_size)
    before = snapshot(db)
    store = CoreStore(db)  # open of a current-version read-only store succeeds (nothing to migrate)
    for op in (lambda: store.append_record(**req(body="x", key="ro")),
               lambda: store.cas_stream("s", 1, {"title": "t"}),
               lambda: store.advance_offset_cas("b", "s", 1, 1)):
        with pytest.raises(core_store.StorageReadOnlyError) as ei:
            op()
        assert ei.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_READONLY
    after = db.stat()
    assert file_sha(db) == sha and (after.st_ino, after.st_size) == ident  # original bytes untouched, same file (not replaced)
    assert snapshot(db) == before
    with store.read_uow() as conn:  # readable after reopening read-only
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3
    assert [r.body for r in CoreStore(db).read_records("s", 0, 10)] == ["m0", "m1", "m2"]
    # positive control: the same operations succeed once the file is writable again (failure was the read-only fault, nothing else)
    make_writable(db)
    assert CoreStore(db).append_record(**req(body="x", key="ro")).position == 4
    assert CoreStore(db).cas_stream("s", 1, {"title": "t"}).revision == 2


@pytest.mark.m1_id("FLT-012")
def test_flt_012_read_only_open_of_outdated_schema_fails_closed_without_migrating(tmp_path):
    db = tmp_path / "old.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE legacy (x)")
        c.commit()
    os.chmod(db, stat.S_IREAD)
    try:
        sha = file_sha(db)
        with pytest.raises(core_store.StorageReadOnlyError):
            CoreStore(db)  # would need to migrate: cannot, must say read-only
        assert file_sha(db) == sha and raw(db, "PRAGMA user_version") == [(0,)]
    finally:
        os.chmod(db, stat.S_IWRITE | stat.S_IREAD)


# =========================================================================== FLT-013
def _corrupt_header(p):
    b = bytearray(Path(p).read_bytes())
    b[:16] = b"NOT A SQLITE FILE"[:16]
    Path(p).write_bytes(bytes(b))


def _corrupt_records_page(p):
    with closing(sqlite3.connect(p)) as c:
        root = c.execute("SELECT rootpage FROM sqlite_master WHERE name='records'").fetchone()[0]
        size = c.execute("PRAGMA page_size").fetchone()[0]
    b = bytearray(Path(p).read_bytes())
    b[(root - 1) * size:(root) * size] = b"\xff" * size
    Path(p).write_bytes(bytes(b))


def _corrupt_copy(tmp_path, how):
    from tests.m1.harness.observation import ObservationHarness

    h = seeded(tmp_path, n=40, name="orig", harness_cls=ObservationHarness)  # extension tables present: pristine Diag is fully OK
    release_files(h)
    cp = tmp_path / f"disposable-{how}" / "core.db"
    cp.parent.mkdir()
    shutil.copy(h.db_path, cp)
    {"header": _corrupt_header, "page": _corrupt_records_page, "none": lambda p: None}[how](cp)
    return cp


def _dir(p):
    return sorted(x.name for x in Path(p).parent.iterdir())


@pytest.mark.parametrize("how", ["header", "page"])
@pytest.mark.m1_id("FLT-013")
def test_flt_013_corrupt_database_fails_explicit_without_recreate_repair_or_write(tmp_path, how):
    from peerhub.extensions.diag import ReadonlyDiag

    cp = _corrupt_copy(tmp_path, how)
    sha, size, names = file_sha(cp), cp.stat().st_size, _dir(cp)
    with pytest.raises(core_store.StorageCorruptError):
        CoreStore(cp)
    rep = ReadonlyDiag(cp).render()
    assert rep.status == "FAILED" and rep.sections == {}  # fail closed: no half-trusted report from a corrupt authoritative store
    assert ("not a database" if how == "header" else "integrity check failed") in rep.error or "malformed" in rep.error
    assert file_sha(cp) == sha and cp.stat().st_size == size  # no empty replacement DB, no repair, no write
    assert set(_dir(cp)) - set(names) <= {"core.db-wal", "core.db-shm"} and not (set(names) - set(_dir(cp)))  # nothing recreated/removed


@pytest.mark.m1_id("FLT-013")
def test_flt_013_positive_control_pristine_copy_opens_and_diag_is_ok(tmp_path):
    from peerhub.extensions.diag import ReadonlyDiag

    cp = _corrupt_copy(tmp_path, "none")
    store = CoreStore(cp)
    assert len(store.read_records("s", 0, 100)) == 40
    assert ReadonlyDiag(cp).render().status == "OK"


@pytest.mark.m1_id("FLT-013")
def test_flt_013_corruption_after_open_is_reported_precisely_on_read_and_write(tmp_path):
    cp = _corrupt_copy(tmp_path, "none")
    store = CoreStore(cp)
    assert len(store.read_records("s", 0, 100)) == 40  # positive control while pristine
    _corrupt_records_page(cp)
    sha = file_sha(cp)
    with pytest.raises(core_store.StorageCorruptError):
        store.read_records("s", 0, 100)
    with pytest.raises(core_store.StorageCorruptError):
        store.append_record(**req(body="x", key="after"))
    assert file_sha(cp) == sha  # main file never rewritten by the failed operations


@pytest.mark.parametrize("how,code", [("header", 4), ("page", 4), ("none", 0)])
@pytest.mark.m1_id("FLT-013")
def test_flt_013_cli_reports_storage_failure_with_its_own_exit_code(tmp_path, capsys, how, code):
    from peerhub.m1_cli import main

    cp = _corrupt_copy(tmp_path, how)
    sha = file_sha(cp)
    rc = main(["--db", str(cp), "peer", "get", "--id", "a"])
    err = capsys.readouterr().err
    assert rc == code  # "none" is the positive control: a pristine copy works through the same entrypoint
    if code:
        assert "STORAGE ERROR (StorageCorruptError)" in err and "Traceback" not in err
    assert file_sha(cp) == sha
