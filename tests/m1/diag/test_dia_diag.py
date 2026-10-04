"""Wave 5 DIA-001..007, 009, 010: strictly read-only Diag, section isolation, fail-closed reads."""
import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.extensions.diag import ReadonlyDiag
from tests.m1.fakes import FakeRuntimeTarget
from tests.m1.fakes.observation import FakeObservationSource, absent, measured
from tests.m1.harness.observation import T0, ObservationHarness
from tests.m1.helpers import req
from tests.m1.spec import ROOT

pytestmark = [pytest.mark.integration, pytest.mark.diag]
SECTIONS = ["peers", "streams", "resource_pools", "observations", "log"]


def populated(tmp_path, name="ws"):
    h = ObservationHarness(tmp_path / name)
    for p in ("a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["a", "b"]})
    for i in range(3):
        h.append_record(req(body=i, key=f"k{i}"))
    h.cas_offset("b", "s", 1, 1)
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "P", "provider": "openai", "kind": "ACCOUNT"})
    h.capture("peer:b", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})), resource_pool_ref="P")
    h.capture("peer:b", "rate_limit", FakeObservationSource(measured({"requests_per_minute": 60})))
    h.capture("peer:a", "reachability", FakeObservationSource(absent()))
    return h


def file_hash(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Proxy:
    """sqlite connection proxy recording every statement; `ignore_query_only` makes the pragma a no-op (non-read-only handle)."""

    def __init__(self, conn, log, ignore_query_only=False, fail_after=None):
        self.c, self.log, self.ign, self.fail_after = conn, log, ignore_query_only, fail_after

    def execute(self, sql, *a):
        self.log.append(sql.strip())
        if self.fail_after is not None and len(self.log) > self.fail_after:
            raise sqlite3.OperationalError("disk I/O error")
        if self.ign and sql.strip().upper().startswith("PRAGMA QUERY_ONLY"):
            return self.c.execute("SELECT 0") if "=" not in sql else self.c.execute("SELECT 1")
        return self.c.execute(sql, *a)

    def close(self):
        self.c.close()

    def __getattr__(self, n):
        return getattr(self.c, n)


def rw_factory(log, **kw):
    def factory(path):
        conn = sqlite3.connect(path, isolation_level=None)  # deliberately WRITABLE: Diag itself must enforce read-only
        conn.row_factory = sqlite3.Row
        return Proxy(conn, log, **kw)
    return factory


@pytest.mark.m1_id("DIA-001")
def test_dia_001_diag_opens_sqlite_read_only_and_never_begins_a_write(tmp_path, monkeypatch):
    h = populated(tmp_path)
    connects, stmts = [], []
    real = sqlite3.connect

    def spy(*a, **kw):
        connects.append((a, kw))
        c = real(*a, **kw)
        c.set_trace_callback(stmts.append)
        return c

    monkeypatch.setattr(sqlite3, "connect", spy)
    report = ReadonlyDiag(h.db_path).render()
    monkeypatch.undo()
    assert report.status == "OK" and connects
    for a, kw in connects:
        assert kw.get("uri") is True and "mode=ro" in a[0], (a, kw)
    assert any(s.upper().startswith("PRAGMA QUERY_ONLY") for s in stmts)
    for s in stmts:
        head = s.strip().split(None, 1)[0].upper() if s.strip() else ""
        assert head in {"SELECT", "PRAGMA", "BEGIN", "ROLLBACK", "COMMIT", "WITH"}, s
        assert "IMMEDIATE" not in s.upper() and "EXCLUSIVE" not in s.upper(), s
    # control: the instrumentation sees writes (a normal store write begins an IMMEDIATE transaction)
    stmts.clear()
    monkeypatch.setattr(sqlite3, "connect", spy)
    h.append_record(req(body="w", key="kw"))
    monkeypatch.undo()
    assert any("IMMEDIATE" in s.upper() for s in stmts)


@pytest.mark.m1_id("DIA-001")
def test_dia_001_diag_enforces_query_only_itself_and_fails_closed(tmp_path):
    h = populated(tmp_path)
    log = []
    report = ReadonlyDiag(h.db_path, connection_factory=rw_factory(log)).render()
    assert report.status == "OK" and log[0].upper().startswith("PRAGMA QUERY_ONLY")  # first statement on the handle
    log2 = []
    bad = ReadonlyDiag(h.db_path, connection_factory=rw_factory(log2, ignore_query_only=True)).render()
    assert bad.status == "FAILED" and "read-only" in bad.error
    assert not any(s.upper().startswith(("SELECT * FROM", "SELECT COUNT")) for s in log2)  # nothing was read through a writable handle


@pytest.mark.m1_id("DIA-002")
def test_dia_002_diag_cannot_append_record_or_change_any_state(tmp_path):
    h = populated(tmp_path)
    public = {n for n in dir(ReadonlyDiag) if not n.startswith("_")}
    assert public == {"render", "inspect_stream_health"}  # no mutating verb is even reachable
    before = h.full_state()
    for _ in range(2):
        report = h.render_diag()
        assert report.status == "OK" and report.sections["streams"].data["streams"][0]["head_position"] == 3
    assert ReadonlyDiag(h.db_path).inspect_stream_health("s")["offsets"] == {"b": 1}
    assert h.full_state() == before and h.row_counts()["records"] == 3 and len(h.obs_rows()) == 3
    # control: the digest is sensitive, i.e. a real write would have been noticed
    h.append_record(req(body="x", key="kx"))
    assert h.full_state() != before


@pytest.mark.m1_id("DIA-003")
def test_dia_003_diag_never_refreshes_observations_or_calls_a_provider(tmp_path, monkeypatch):
    from peerhub.extensions.observation import ObservationStore

    h = populated(tmp_path)
    src, rt = FakeObservationSource(), FakeRuntimeTarget()  # would raise (empty script) / be recorded if invoked
    for name in ("capture", "persist", "register_resource_pool"):
        monkeypatch.setattr(ObservationStore, name, lambda *a, **k: pytest.fail("Diag called ObservationStore write"))
    before = h.full_state()
    report = h.render_diag(read_at=T0 + 10**6)
    assert report.status == "OK" and src.calls == 0 and rt.calls == []
    assert h.full_state() == before
    item = next(i for i in report.sections["observations"].data["items"] if i["kind"] == "quota")
    assert item["state"] == "STALE" and item["stored_state"] == "MEASURED"  # stale is reported honestly, not refreshed


@pytest.mark.m1_id("DIA-003")
def test_dia_003_diag_module_graph_excludes_store_runtime_and_routing(tmp_path):
    h = populated(tmp_path)
    code = (f"import sys\nfrom peerhub.extensions.diag import ReadonlyDiag\n"
            f"r = ReadonlyDiag(r'{h.db_path}').render()\nassert r.status == 'OK', r\n"
            "bad = [m for m in sys.modules if m.startswith(('peerhub.routing', 'peerhub.dispatch', 'peerhub.adapters', 'peerhub.runtime',"
            " 'peerhub.application', 'peerhub.extensions.bridge', 'peerhub.extensions.session_bridge', 'peerhub.extensions.observation',"
            " 'peerhub.m1.store')) and m != 'peerhub.extensions.observation_model']\n"
            "sys.exit(1 if bad else 0)\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]


def _poison(monkeypatch, calls):
    import subprocess as sp

    from peerhub.extensions.bridge import Bridge
    from peerhub.extensions.bridge_claims import ClaimStore
    from peerhub.extensions.observation import ObservationStore
    from peerhub.m1.store import CoreStore

    def trap(label):
        def f(*a, **k):
            calls.append(label)
            raise AssertionError(f"forbidden port {label}")
        return f

    targets = [(CoreStore, n) for n in ("register_peer", "create_stream", "cas_stream", "append_record", "advance_offset_cas")]
    targets += [(ClaimStore, n) for n in ("acquire", "renew", "heartbeat")]
    targets += [(Bridge, n) for n in ("delivery_cycle", "handle_control", "reconcile_uncertain", "finalize_terminal")]
    targets += [(ObservationStore, n) for n in ("capture", "persist", "register_resource_pool")]
    targets += [(FakeRuntimeTarget, n) for n in ("interrupt", "terminate", "steer", "create_session", "resume_session", "deliver")]
    for cls, n in targets:
        monkeypatch.setattr(cls, n, trap(f"{cls.__name__}.{n}"))
    monkeypatch.setattr(sp, "Popen", trap("subprocess.Popen"))
    monkeypatch.setattr(os, "system", trap("os.system"))
    return targets


@pytest.mark.m1_id("DIA-004")
def test_dia_004_no_mutation_or_runtime_control_port_is_called_by_any_view(tmp_path, monkeypatch):
    h = populated(tmp_path)
    log = tmp_path / "diag.log"
    log.write_text("line1\nline2\n")
    calls = []
    targets = _poison(monkeypatch, calls)
    before = h.full_state()
    for _ in range(2):  # second round after a "restart" of the observer
        d = ReadonlyDiag(h.db_path, log_path=log)
        report = d.render()  # every section
        assert report.status == "OK" and set(report.sections) == set(SECTIONS)
        for s in SECTIONS:
            assert d.render([s]).sections[s].status == "OK"
        assert d.inspect_stream_health("s")["status"] == "OK"
    assert calls == [] and h.full_state() == before
    with pytest.raises(AssertionError):  # control: the poison is live (touching a port is detected)
        h.store.append_record(**req(body="z", key="kz"))
    assert calls == ["CoreStore.append_record"] and len(targets) >= 15


@pytest.mark.m1_id("DIA-005")
def test_dia_005_one_malformed_observation_is_isolated(tmp_path):
    h = populated(tmp_path)
    good = h.render_diag()
    assert good.status == "OK" and good.sections["observations"].errors == ()
    with closing(sqlite3.connect(h.db_path)) as c:
        c.execute("INSERT INTO observations (observation_id, subject_ref, kind, source, state, payload_json, observed_at, captured_at, effective_at_us) "
                  "VALUES ('bad-json','peer:b','quota','s','MEASURED','{not json',  '2026-10-01T00:00:00Z', NULL, 0)")
        c.execute("INSERT INTO observations (observation_id, subject_ref, kind, source, state, payload_json, observed_at, captured_at, effective_at_us) "
                  "VALUES ('bad-state','peer:c','quota','s','BOGUS','{}', '2026-10-01T00:00:00Z', NULL, 0)")
        c.commit()
    before = h.full_state()
    rep = h.render_diag()
    obs = rep.sections["observations"]
    assert rep.status == "PARTIAL" and obs.status == "ERROR"
    assert sorted(e["observation_id"] for e in obs.errors) == ["bad-json", "bad-state"] and all(e["error"] for e in obs.errors)
    assert [i["observation_id"] for i in obs.data["items"]] and {i["observation_id"] for i in obs.data["items"]} == {"obs-1", "obs-2", "obs-3"}
    for name in ("peers", "streams", "resource_pools"):  # unrelated sections remain readable and identical
        assert rep.sections[name].status == "OK" and rep.sections[name].data == good.sections[name].data
    assert h.full_state() == before  # nothing repaired or removed


@pytest.mark.m1_id("DIA-006")
def test_dia_006_store_read_failure_reports_failure_without_repair(tmp_path, monkeypatch):
    h = populated(tmp_path)
    # (1) injected I/O error at open and mid-read: FAILED, only read statements issued
    def boom(path):
        raise sqlite3.OperationalError("disk I/O error")
    rep = ReadonlyDiag(h.db_path, connection_factory=boom).render()
    assert rep.status == "FAILED" and "disk I/O error" in rep.error and rep.sections == {}
    log = []
    rep = ReadonlyDiag(h.db_path, connection_factory=rw_factory(log, fail_after=3)).render()
    assert rep.status == "FAILED" and "disk I/O error" in rep.error
    for s in log:
        assert s.split(None, 1)[0].upper() in {"PRAGMA", "SELECT", "BEGIN", "ROLLBACK", "WITH"}, s
    # (2) missing file: nothing created; (3) garbage file: bytes unchanged; (4) un-migrated empty store: no tables created
    missing = tmp_path / "nowhere" / "core.db"
    assert ReadonlyDiag(missing).render().status == "FAILED" and not missing.parent.exists()
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a sqlite database" * 20)
    gh = file_hash(garbage)
    assert ReadonlyDiag(garbage).render().status == "FAILED" and file_hash(garbage) == gh
    empty = tmp_path / "empty.db"
    with closing(sqlite3.connect(empty)) as c:
        c.execute("PRAGMA user_version = 0")
        c.execute("CREATE TABLE unrelated (x)")
        c.commit()
    eh = file_hash(empty)
    rep = ReadonlyDiag(empty).render()
    assert rep.status in ("PARTIAL", "FAILED") and rep.sections["streams"].status == "ERROR"
    assert file_hash(empty) == eh
    with closing(sqlite3.connect(empty)) as c:
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master")] == ["unrelated"] and c.execute("PRAGMA user_version").fetchone()[0] == 0
    # no migration/repair machinery was imported or run by Diag
    import peerhub.extensions.diag as dm
    assert "run_migrations" not in vars(dm) and "CoreStore" not in vars(dm)
    # control: a healthy store renders OK through the same entry point
    assert ReadonlyDiag(h.db_path).render().status == "OK"


@pytest.mark.m1_id("DIA-007")
def test_dia_007_authoritative_logical_state_and_inputs_unchanged(tmp_path):
    h = populated(tmp_path)
    cfg = tmp_path / "ws" / "config.json"
    cfg.write_text('{"ttl": 300}')
    log = tmp_path / "ws" / "diag.log"
    log.write_text("started\n")
    gen = Path(h.workspace) / "generation"
    inputs = [cfg, log] + ([gen] if gen.exists() else [])
    before = (h.state_digest(), h.row_counts(), h.table_digests(), [file_hash(p) for p in inputs], h.obs_rows(), h.pool_rows())
    rt, src = FakeRuntimeTarget(), FakeObservationSource()
    rep = ReadonlyDiag(h.db_path, log_path=log).render()
    assert rep.status == "OK"
    del rep
    after = (h.state_digest(), h.row_counts(), h.table_digests(), [file_hash(p) for p in inputs], h.obs_rows(), h.pool_rows())
    assert after == before and rt.calls == [] and src.calls == 0
    assert len(inputs) >= 2


@pytest.mark.m1_id("DIA-009")
def test_dia_009_unreadable_log_is_isolated_and_never_repaired(tmp_path, monkeypatch):
    h = populated(tmp_path)
    log = tmp_path / "diag.log"
    log.write_text("one\ntwo\nthree\n")
    ok = ReadonlyDiag(h.db_path, log_path=log).render(read_at=T0)
    assert ok.status == "OK" and ok.sections["log"].data["lines"] == ["one", "two", "three"]
    lh = file_hash(log)

    def denied(path):
        raise PermissionError(13, "Permission denied")

    for name in ("chmod", "remove", "unlink", "rename", "replace", "utime"):
        monkeypatch.setattr(os, name, lambda *a, **k: pytest.fail("Diag attempted a filesystem repair"))
    before = h.full_state()
    rep = ReadonlyDiag(h.db_path, log_path=log, open_log=denied).render(read_at=T0)
    sec = rep.sections["log"]
    assert rep.status == "PARTIAL" and sec.status == "ERROR" and "PermissionError" in sec.errors[0]["error"]
    for name in ("peers", "streams", "resource_pools", "observations"):
        assert rep.sections[name].status == "OK" and rep.sections[name].data == ok.sections[name].data
    # a directory instead of a file, and a missing file, are explicit too (and the missing file is not created)
    d = ReadonlyDiag(h.db_path, log_path=tmp_path).render()
    assert d.sections["log"].status == "ERROR" and d.sections["streams"].status == "OK"
    gone = tmp_path / "missing.log"
    m = ReadonlyDiag(h.db_path, log_path=gone).render()
    assert m.sections["log"].status == "UNAVAILABLE" and not gone.exists()
    n = ReadonlyDiag(h.db_path).render()  # not configured is explicit, not silently empty
    assert n.sections["log"].status == "UNAVAILABLE"
    assert h.full_state() == before and file_hash(log) == lh


@pytest.mark.m1_id("DIA-010")
def test_dia_010_zero_provider_calls_even_when_everything_is_stale(tmp_path, monkeypatch):
    h = populated(tmp_path)
    src, rt = FakeObservationSource(), FakeRuntimeTarget()
    calls = []
    _poison(monkeypatch, calls)
    before = h.full_state()
    rep = h.render_diag(read_at=T0 + 365 * 86400)
    assert rep.status == "OK" and calls == [] and src.calls == 0 and rt.calls == []
    items = rep.sections["observations"].data["items"]
    measured_items = [i for i in items if i["stored_state"] == "MEASURED"]
    assert measured_items and all(i["state"] == "STALE" for i in measured_items)  # stale reported honestly
    assert [i["state"] for i in items if i["stored_state"] == "ABSENT"] == ["ABSENT"]  # non-measured states are not upgraded/rewritten
    assert h.full_state() == before


@pytest.mark.m1_id("DIA-002")
def test_w5_diag_latest_per_subject_kind_matches_store_ordering(tmp_path):
    h = ObservationHarness(tmp_path / "ws")
    mk = lambda f: FakeObservationSource(measured({"remaining_fraction": f}))
    h.clock.set(T0)
    h.capture("peer:x", "quota", mk(0.9))
    h.clock.set(T0 + 10)
    newest = h.capture("peer:x", "quota", mk(0.1))
    h.clock.set(T0 + 5)  # later ordinal but EARLIER local capture time: must not win
    h.capture("peer:x", "quota", mk(0.5))
    h.clock.set(T0 + 10)  # equal capture time to `newest`, later ordinal: wins the tie
    tie = h.capture("peer:x", "quota", mk(0.2))
    items = h.render_diag(read_at=T0 + 11).sections["observations"].data["items"]
    assert [i["observation_id"] for i in items] == [tie.observation_id] != [newest.observation_id]
    assert items[0]["capture_seq"] == 4 and items[0]["payload"] == {"remaining_fraction": 0.2}
    assert h.latest("peer:x", "quota", T0 + 11).observation.observation_id == tie.observation_id
