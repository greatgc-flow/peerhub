"""Claim trigger reconciliation and actual ask-scope isolation (#335, #323).

Providers are NEVER invoked. Timing is recorded only by the separate benchmark;
these gates assert database facts, rollback and synchronized runtime overlap.
"""
from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError
from peerhub.core.store import CoreStore
from peerhub.core.workspace import Workspace
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge_claims import ClaimHeldError, ClaimStore

pytestmark = [pytest.mark.claim, pytest.mark.sqlite, pytest.mark.platform_sensitive]


@pytest.fixture
def seeded(tmp_path):
    ws = Workspace(tmp_path)
    db = tmp_path / "core.db"
    store = CoreStore(db)
    for name in ("p", "user"):
        store.register_peer(Peer(peer_id=name))
    for name in ("a", "b"):
        store.create_stream(Stream(stream_id=name, members=["p", "user"]))
    claims = ClaimStore(db, ws.generation)
    token = claims.acquire("p", "a", "original", 300)
    claims.finalize_terminal(token, {"response": "durable"})
    claims.release(token)
    return db, ws


def _schema(db):
    with closing(sqlite3.connect(db)) as conn:
        return dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))


def _cookie(db):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("PRAGMA schema_version").fetchone()[0]


def test_unchanged_claim_reopen_has_zero_schema_churn(seeded):
    db, ws = seeded
    before, cookie = _schema(db), _cookie(db)
    for _ in range(5):
        ClaimStore(db, ws.generation)
    assert _schema(db) == before
    assert _cookie(db) == cookie


@pytest.mark.parametrize("damage", ["missing", "changed_body", "extra", "quoted_extra"])
def test_reopen_repairs_exact_owned_set_and_preserves_unrelated_triggers(seeded, damage):
    db, ws = seeded
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE unrelated(x)")
        conn.execute("CREATE TRIGGER unrelated_guard BEFORE DELETE ON unrelated BEGIN SELECT 1; END")
        conn.commit()
    expected = _schema(db)
    with closing(sqlite3.connect(db)) as conn:
        if damage in ("missing", "changed_body"):
            conn.execute("DROP TRIGGER bridge_finalizations_no_update")
        if damage == "changed_body":
            conn.execute("CREATE TRIGGER bridge_finalizations_no_update BEFORE UPDATE ON bridge_finalizations BEGIN SELECT 1; END")
        if damage in ("extra", "quoted_extra"):
            name = 'legacy"guard' if damage == "quoted_extra" else "legacy_guard"
            name = '"' + name.replace('"', '""') + '"'
            conn.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON bridge_claims BEGIN SELECT 1; END")
        conn.commit()
    ClaimStore(db, ws.generation)
    assert _schema(db) == expected
    with closing(sqlite3.connect(db)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE bridge_finalizations SET result_json='{}'")


def test_repair_failure_rolls_back_trigger_set_and_cookie(seeded, monkeypatch):
    db, ws = seeded
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP TRIGGER bridge_finalizations_no_update")
        conn.commit()
    damaged, cookie = _schema(db), _cookie(db)
    original_connect = sqlite3.connect

    class FailInstall(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith("CREATE TRIGGER bridge_finalizations_no_delete"):
                raise sqlite3.OperationalError("injected install failure")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        kwargs["factory"] = FailInstall
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        ClaimStore(db, ws.generation)
    assert _schema(db) == damaged
    assert _cookie(db) == cookie
    with closing(original_connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bridge_finalizations").fetchone()[0] == 1


@pytest.mark.parametrize("sql", [
    "UPDATE bridge_finalizations SET result_json='{}'",
    "DELETE FROM bridge_finalizations",
    "INSERT OR REPLACE INTO bridge_finalizations VALUES ('a','p',1,'{}')",
    "INSERT OR REPLACE INTO bridge_finalizations(rowid,stream_id,peer_id,claim_generation,result_json) VALUES (1,'b','p',2,'{}')",
])
def test_immutability_survives_noop_reopen_with_recursive_triggers_off(seeded, sql):
    db, ws = seeded
    ClaimStore(db, ws.generation)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
        assert conn.execute("SELECT result_json FROM bridge_finalizations").fetchone()[0] == '{"response":"durable"}'


def test_concurrent_reopens_keep_current_schema(seeded):
    db, ws = seeded
    before, cookie = _schema(db), _cookie(db)
    barrier = Barrier(4)
    def reopen(_):
        barrier.wait(timeout=30)
        ClaimStore(db, ws.generation)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(reopen, range(4)))
    assert _schema(db) == before
    assert _cookie(db) == cookie


def test_future_schema_refusal_is_still_before_any_repair(seeded):
    db, ws = seeded
    before, cookie = _schema(db), _cookie(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(f"PRAGMA user_version={SUPPORTED_SCHEMA_VERSION+1}")
    with pytest.raises(SchemaVersionError):
        ClaimStore(db, ws.generation)
    assert _schema(db) == before
    assert _cookie(db) == cookie


def test_claims_fence_the_same_scope_but_not_the_entire_workspace(seeded):
    db, ws = seeded
    one, two = ClaimStore(db, ws.generation), ClaimStore(db, ws.generation)
    first = one.acquire("p", "a", "one", 300)
    with pytest.raises(ClaimHeldError):
        two.acquire("p", "a", "two", 300)
    second = two.acquire("p", "b", "two", 300)
    one.release(first)
    two.release(second)


def test_two_actual_asks_overlap_on_distinct_streams_in_one_workspace(seeded):
    db, _ws = seeded
    rendezvous = Barrier(2)

    class Runtime:
        runtime_kind = "offline"
        resumable = True
        def fingerprint(self): return "offline-v1"
        def binding(self): return "offline"
        def create_session(self): return uuid.uuid4().hex
        def resume_session(self, external_session_id, *, vendor_session_id=None): return "ok"
        def deliver(self, external_session_id, record, catch_up):
            yield ("started", uuid.uuid4().hex)
            rendezvous.wait(timeout=30)
            yield ("terminal", {"response": "offline-complete"})

    def run(stream):
        return ask(db, "p", "offline test", stream_id=stream, request_id=f"scope-{stream}",
                   workspace=db.parent, runtime=Runtime())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("a", "b")))
    assert [r["status"] for r in results] == ["delivered", "delivered"]
    assert [r["response"] for r in results] == ["offline-complete", "offline-complete"]
