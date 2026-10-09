"""Wave 6 gate fixes: real disk-full in migration/claims/observation (no masked errors), old-schema upgrade, preflight size modes."""
import hashlib
import shutil
import sqlite3
import types
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.extensions import bridge_claims, observation
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.core import store as core_store
from peerhub.core.migrations import Migration, run_migrations
from peerhub.core.store import CoreStore
from tests.communication.bridge_helpers import bseed, delivery_rows, responses, sql
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.fault.test_flt_storage import (_corrupt_header, _corrupt_records_page, file_sha, raw, release_files, seeded, snapshot)
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.clock import ManualClock
from tests.communication.helpers import req

pytestmark = [pytest.mark.fault, pytest.mark.sqlite]


def _patch_connect(monkeypatch, module, pages):
    real = sqlite3.connect

    def connect(*a, **k):
        c = real(*a, **k)
        c.execute(f"PRAGMA max_page_count = {pages}")
        return c

    class Shim:
        def __getattr__(self, n):
            return getattr(sqlite3, n)
    shim = Shim()
    shim.connect = connect
    monkeypatch.setattr(module, "sqlite3", shim)


def _pages(db):
    return raw(db, "PRAGMA page_count")[0][0]


# ----------------------------------------------------------------------------- 2. rollback must never mask the real error
@pytest.mark.catalog_id("FLT-011")
def test_flt_011_disk_full_during_migration_reports_full_and_leaves_old_version(tmp_path):
    h = seeded(tmp_path)
    release_files(h)
    db = h.db_path
    pages, before, ver = _pages(db), snapshot(db), raw(db, "PRAGMA user_version")[0][0]

    def grow(conn):
        conn.execute(f"PRAGMA max_page_count = {pages}")  # the disk is full from now on (real engine limit)
        conn.execute("CREATE TABLE fixture_big AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 200000) SELECT i, randomblob(64) b FROM n")

    mig = [Migration(ver, "current", lambda c: None), Migration(ver + 1, "grow", grow)]
    with pytest.raises(sqlite3.OperationalError) as ei:
        run_migrations(db, migrations=mig)
    assert ei.value.sqlite_errorcode == sqlite3.SQLITE_FULL and "rollback" not in str(ei.value)  # the REAL error, not a masking one
    assert raw(db, "PRAGMA user_version") == [(ver,)] and snapshot(db) == before  # complete old state
    assert raw(db, "PRAGMA integrity_check") == [("ok",)]
    ok = run_migrations(db, migrations=[Migration(ver, "current", lambda c: None), Migration(ver + 1, "noop", lambda c: None)])
    assert ok == [ver + 1]  # clean retry once space is available


@pytest.mark.catalog_id("FLT-011")
def test_flt_011_migration_failure_after_engine_auto_rollback_is_not_masked(tmp_path):
    db = tmp_path / "m.db"
    CoreStore(db)
    ver = raw(db, "PRAGMA user_version")[0][0]

    def auto_rollback_then_full(conn):  # what SQLite does on a severe SQLITE_FULL: the transaction is already gone
        conn.execute("ROLLBACK")
        raise sqlite3.OperationalError("database or disk is full")

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        run_migrations(db, migrations=[Migration(ver, "cur", lambda c: None), Migration(ver + 1, "x", auto_rollback_then_full)])
    assert raw(db, "PRAGMA user_version") == [(ver,)]


@pytest.mark.catalog_id("FLT-011")
def test_flt_011_disk_full_in_claim_store_is_not_masked_by_rollback_error(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    release_files(h)
    cs = ClaimStore(h.db_path, generation=h.ws.generation, clock=lambda: 1000.0)
    before = snapshot(h.db_path)
    _patch_connect(monkeypatch, bridge_claims, _pages(h.db_path))
    with pytest.raises(sqlite3.OperationalError) as ei:
        cs.acquire("b", "s", "O" * 400_000, 30.0)  # needs overflow pages the full disk cannot give
    assert ei.value.sqlite_errorcode == sqlite3.SQLITE_FULL and "rollback" not in str(ei.value)
    assert snapshot(h.db_path) == before
    monkeypatch.undo()
    assert cs.acquire("b", "s", "owner", 30.0).generation == 1  # positive control: same call works with space


@pytest.mark.catalog_id("FLT-011")
def test_flt_011_disk_full_in_observation_store_is_not_masked_by_rollback_error(tmp_path, monkeypatch):
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from tests.communication.harness.observation import ObservationHarness

    h = ObservationHarness(tmp_path / "ws")
    h.create_peer({"peer_id": "p"})
    release_files(h)
    h.store = None
    before = snapshot(h.db_path)
    _patch_connect(monkeypatch, observation, _pages(h.db_path))
    with pytest.raises(sqlite3.OperationalError) as ei:
        h.obs.capture("peer:p", "quota", FakeObservationSource(measured({"note": "X" * 400_000})))
    assert ei.value.sqlite_errorcode == sqlite3.SQLITE_FULL and "rollback" not in str(ei.value)
    assert snapshot(h.db_path) == before
    monkeypatch.undo()
    assert h.obs.capture("peer:p", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}))).payload == {"remaining_fraction": 0.5}


# ----------------------------------------------------------------------------- 1. old-schema (Wave-5) upgrade is idempotent
OLD_BRIDGE_SESSIONS = """CREATE TABLE bridge_sessions (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, runtime_kind TEXT NOT NULL, external_session_id TEXT NOT NULL,
        session_generation INTEGER NOT NULL, adapter_fingerprint TEXT NOT NULL, binding TEXT NOT NULL,
        resumable INTEGER NOT NULL, state TEXT NOT NULL, last_seen REAL NOT NULL, PRIMARY KEY (stream_id, peer_id))"""


def _schema(db):
    return raw(db, "SELECT type, name, sql FROM sqlite_master ORDER BY 1,2")


@pytest.mark.catalog_id("FLT-008")
def test_flt_008_wave5_era_store_upgrades_and_runs_a_full_delivery_cycle(tmp_path):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    bseed(h)
    release_files(h)
    with closing(sqlite3.connect(h.db_path)) as c:  # downgrade ONLY the table this wave altered to its Wave-5 definition + a live row
        c.execute("DROP TABLE bridge_sessions")
        c.execute(OLD_BRIDGE_SESSIONS)
        c.execute("INSERT INTO bridge_sessions VALUES ('s','b','fake','ext-1',1,'F1','model-a/profile-1',1,'ACTIVE',5.0)")
        c.commit()
    assert [r[1] for r in raw(h.db_path, "PRAGMA table_info(bridge_sessions)")][-1] == "last_seen"
    h2 = BridgeHarness(ws)  # opening upgrades in place
    cols = [r[1] for r in raw(h2.db_path, "PRAGMA table_info(bridge_sessions)")]
    assert cols[10:] == ["workspace_generation", "vendor_session_id", "context_watermark", "bootstrap_truncated", "usage_json"]
    assert raw(h2.db_path, "SELECT external_session_id, workspace_generation FROM bridge_sessions") == [("ext-1", "")]
    rt = FakeRuntimeTarget()
    rt.sessions.add("ext-1")  # the provider still has the old session, but its lineage is unknown
    res = h2.delivery_cycle("b", "s", rt)  # full cycle on the upgraded store: no 'no such column'
    assert res.status == "delivered" and len(responses(h2)) == 1 and ("resume", "ext-1") not in rt.calls
    assert res.session_generation == 2
    assert raw(h2.db_path, "SELECT workspace_generation FROM bridge_sessions") == [(h2.workspace_generation(),)]
    # idempotent: every extension store reopens any number of times without schema change or error
    ClaimStore(h2.db_path, generation=h2.ws.generation, clock=lambda: 1.0)
    observation.ObservationStore(h2.db_path)  # first creation of the observation tables (legit schema growth)
    schema = _schema(h2.db_path)
    for _ in range(3):
        BridgeHarness(ws)
        ClaimStore(h2.db_path, generation=h2.ws.generation, clock=lambda: 1.0)
        observation.ObservationStore(h2.db_path)
    assert _schema(h2.db_path) == schema
    # fresh stores also work end to end (positive control for the fixture path)
    h3 = BridgeHarness(tmp_path / "ws3")
    bseed(h3)
    assert h3.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"


# ----------------------------------------------------------------------------- 3. preflight size modes
def _big(tmp_path):
    h = seeded(tmp_path, n=300, name="big")
    release_files(h)
    return h.db_path


@pytest.mark.catalog_id("FLT-013")
def test_flt_013_preflight_full_below_threshold_and_light_above_it(tmp_path):
    db = Path(_big(tmp_path))
    size = db.stat().st_size
    assert CoreStore(db).preflight_mode == "full"  # default threshold 256 MiB >> this file
    assert core_store.DEFAULT_QUICK_CHECK_MAX_BYTES == 256 * 1024 * 1024
    assert CoreStore(db, quick_check_max_bytes=size).preflight_mode == "full"  # boundary: size <= threshold
    assert CoreStore(db, quick_check_max_bytes=size - 1).preflight_mode == "light"
    # header damage is caught by BOTH modes
    for thr in (10 ** 9, 0):
        cp = tmp_path / f"h{thr}" / "core.db"
        cp.parent.mkdir()
        shutil.copy(db, cp)
        _corrupt_header(cp)
        sha = file_sha(cp)
        with pytest.raises(core_store.StorageCorruptError):
            CoreStore(cp, quick_check_max_bytes=thr)
        assert file_sha(cp) == sha
    # root-page damage is also caught by the light sample
    cp = tmp_path / "root" / "core.db"
    cp.parent.mkdir()
    shutil.copy(db, cp)
    _corrupt_records_page(cp)
    with pytest.raises(core_store.StorageCorruptError):
        CoreStore(cp, quick_check_max_bytes=0)


@pytest.mark.catalog_id("FLT-013")
def test_flt_013_light_mode_misses_deep_damage_at_open_but_access_maps_it_lazily(tmp_path):
    db = Path(_big(tmp_path))
    pages = raw(db, "PRAGMA page_count")[0][0]
    victim = None
    for pg in range(pages, 1, -1):  # find a middle/leaf page whose damage full mode rejects but light mode does not see at open
        cp = tmp_path / f"v{pg}" / "core.db"
        cp.parent.mkdir()
        shutil.copy(db, cp)
        b = bytearray(cp.read_bytes())
        size = raw(cp, "PRAGMA page_size")[0][0]
        b[(pg - 1) * size:pg * size] = b"\xff" * size
        cp.write_bytes(bytes(b))
        try:
            CoreStore(cp, quick_check_max_bytes=10 ** 9)
            continue  # full mode did not object: not corruption we care about
        except core_store.StorageCorruptError:
            pass
        try:
            light = CoreStore(cp, quick_check_max_bytes=0)
        except core_store.StorageCorruptError:
            continue
        victim = (cp, light)
        break
    assert victim is not None, "no page found that only the full scan detects"
    cp, light = victim
    assert light.preflight_mode == "light"
    sha = file_sha(cp)
    with pytest.raises(core_store.StorageCorruptError):  # lazy mapping at access time
        light.read_records("s", 0, 10_000)
    assert file_sha(cp) == sha
