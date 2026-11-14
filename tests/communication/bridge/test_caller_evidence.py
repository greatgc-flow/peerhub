"""#374: one `caller` evidence row per delivery and invoking process, written with the invocation marker, before the provider starts."""
import json
import os
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import _digest  # pyright: ignore[reportPrivateUsage]
from peerhub.extensions.bridge_claims import StaleClaimError
from tests.communication.bridge_helpers import bseed, delivery_rows, kinds, sql
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness

pytestmark = [pytest.mark.integration, pytest.mark.bridge]

A = {"v": 1, "tool": "cc", "label": "lead-a", "session": "s-a", "host": "box|win32", "ns": "-", "ask_pid": 11,
     "ask_created": 1700000000.25, "cwd": "D:\\a", "batch": None, "task": None}
B = {**A, "label": "lead-b", "session": "s-b", "ask_pid": 22, "ask_created": 1700000001.5, "cwd": "D:\\b",
     "batch": "nightly", "task": "t1"}


def caller_rows(h, did=None):
    q = "SELECT delivery_id, certainty, detail FROM bridge_evidence WHERE kind='caller'" + (" AND delivery_id=?" if did else "")
    return sql(h, q + " ORDER BY seq", (did,) if did else ())


def test_caller_row_exists_before_the_provider_starts(tmp_path):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    (rec,) = bseed(h)
    seen = []
    rt = FakeRuntimeTarget()
    rt.script_deliver(("call", lambda: seen.append(kinds(h))), ("started", "e1"), ("terminal", {"response": "r"}))
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert seen and seen[0][-2:] == ["caller", "about_to_invoke"]
    (d,) = delivery_rows(h, rec.record_id)
    ((did, certainty, detail),) = caller_rows(h)
    assert (did, certainty) == (d[0], "NOT_STARTED") and json.loads(detail) == A
    assert kinds(h, d[0])[:3] == ["attempt_created", "caller", "about_to_invoke"]


def test_one_row_over_two_invocations_of_one_delivery(tmp_path):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "no binary"))
    assert h.delivery_cycle("b", "s", rt).status == "failed_not_started"
    assert h.delivery_cycle("b", "s", rt).status == "delivered"  # same delivery, invocation 2
    (d,) = delivery_rows(h, rec.record_id)
    assert sql(h, "SELECT invocation_no FROM bridge_invocations WHERE delivery_id=?", (d[0],)) == [(1,), (2,)]
    assert len(caller_rows(h, d[0])) == 1


def _fail_first_invocation(h):
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "no binary"))
    assert h.delivery_cycle("b", "s", rt).status == "failed_not_started"  # invocation 1 resolved; delivery stays NOT_STARTED
    h.clock.advance(31)  # the first process's lease ends
    return rec


def test_another_process_running_a_later_invocation_adds_its_own_row(tmp_path):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    rec = _fail_first_invocation(h)
    other = h.new_bridge("second-ask", caller=B)
    assert other.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    (d,) = delivery_rows(h, rec.record_id)
    assert sql(h, "SELECT invocation_no FROM bridge_invocations WHERE delivery_id=?", (d[0],)) == [(1,), (2,)]
    assert [json.loads(r[2])["label"] for r in caller_rows(h, d[0])] == ["lead-a", "lead-b"]  # newest names the live ask
    order = kinds(h, d[0])
    assert order.index("caller", order.index("prespawn_failure")) < order.index("about_to_invoke", order.index("prespawn_failure"))


@pytest.mark.parametrize("field", ["host", "ns", "ask_pid", "ask_created"])
def test_any_identity_field_that_differs_is_another_process(tmp_path, field):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    _fail_first_invocation(h)
    changed = {**A, field: "other" if isinstance(A[field], str) else A[field] + 1}
    assert h.new_bridge("second-ask", caller=changed).delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    assert len(caller_rows(h)) == 2


def test_the_same_process_in_a_new_bridge_adds_no_row(tmp_path):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    _fail_first_invocation(h)
    same_process = {**A, "label": "renamed", "cwd": "D:\\elsewhere", "batch": "b", "task": "t"}  # identity unchanged
    assert h.new_bridge("again", caller=same_process).delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    assert [json.loads(r[2])["label"] for r in caller_rows(h)] == ["lead-a"]


def test_a_later_invocation_adds_the_first_row_when_none_exists(tmp_path):
    h = BridgeHarness(tmp_path / "ws")  # an ask without caller evidence ran invocation 1
    rec = _fail_first_invocation(h)
    assert h.new_bridge("second-ask", caller=B).delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    (d,) = delivery_rows(h, rec.record_id)
    assert [json.loads(r[2])["label"] for r in caller_rows(h, d[0])] == ["lead-b"]


@pytest.mark.parametrize("detail", ["[1]", '{"v": 1}', '"text"'])
def test_a_newest_row_without_identity_counts_as_another_process(tmp_path, detail):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    _fail_first_invocation(h)
    with closing(sqlite3.connect(h.db_path)) as conn, conn:
        did = conn.execute("SELECT delivery_id FROM bridge_deliveries").fetchone()[0]
        conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) "
                     "VALUES (?, 's', 'b', 'caller', 'NOT_STARTED', ?)", (did, detail))
    assert h.new_bridge("again", caller=A).delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    assert len(caller_rows(h)) == 3


def test_no_row_on_replay(tmp_path):
    h = BridgeHarness(tmp_path / "ws", caller=A)
    bseed(h)
    rt = FakeRuntimeTarget()
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    replay = h.new_bridge("other-owner", caller=B)
    h.clock.advance(31)  # the first owner's lease ends; the replaying owner takes the claim
    for _ in range(2):
        replay.delivery_cycle("b", "s", rt)
    assert [json.loads(r[2])["label"] for r in caller_rows(h)] == ["lead-a"]


def test_no_row_without_caller(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    assert caller_rows(h) == []


@pytest.mark.parametrize("bad", ["not a dict", ["v", 1], 0])
def test_no_row_for_a_caller_that_is_not_a_dict(tmp_path, bad):
    h = BridgeHarness(tmp_path / "ws", caller=bad)
    bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    assert caller_rows(h) == []


def test_no_row_from_a_second_process_that_loses_the_claim(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    loser = h.new_bridge("loser", caller=B)
    stale = h.acquire_claim("b", "s", "loser", 30)
    did = loser.begin_attempt(stale, rec)
    h.clock.advance(31)
    h.acquire_claim("b", "s", "winner", 30)  # takeover: the loser's token is fenced
    with pytest.raises(StaleClaimError):
        loser._mark_invocation(stale, did, external_session_id="x", session_generation=1)
    assert caller_rows(h) == [] and sql(h, "SELECT COUNT(*) FROM bridge_invocations") == [(0,)]


def test_ask_writes_the_detected_caller_once_and_replay_adds_none(tmp_path, monkeypatch):
    from peerhub.extensions.ask import ask
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_LEAD", "lead-x")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-x")
    monkeypatch.setenv("PEERHUB_BATCH_ID", "nightly")
    monkeypatch.setenv("PEERHUB_BATCH_TASK", "t1")
    db = tmp_path / "ask.db"
    first = ask(db, "cx", "question", request_id="r1", runtime=FakeRuntimeTarget())
    assert first["status"] == "delivered"
    monkeypatch.setenv("PEERHUB_LEAD", "lead-y")
    replay = ask(db, "cx", "question", request_id="r1", runtime=FakeRuntimeTarget())
    assert replay["status"] == "recovered_terminal" and replay["delivery_id"] == first["delivery_id"]
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT delivery_id, detail FROM bridge_evidence WHERE kind='caller'").fetchall()
    ((did, detail),) = rows
    got = json.loads(detail)
    assert did == first["delivery_id"]
    assert (got["v"], got["tool"], got["label"], got["session"], got["batch"], got["task"]) == (1, "cc", "lead-x", "sess-x", "nightly", "t1")
    assert got["ask_pid"] == os.getpid() and got["cwd"] == str(tmp_path)
    assert not any(k in first for k in ("caller", "ask_pid", "ask_created"))


def _run(tmp_path, name, who):
    h = BridgeHarness(tmp_path / name, caller=who)
    (rec,) = bseed(h)
    assert h.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "delivered"
    (d,) = delivery_rows(h, rec.record_id)
    did = d[0]
    digest, result = sql(h, "SELECT result_digest, result_json FROM bridge_deliveries WHERE delivery_id=?", (did,))[0]
    terminal = json.loads(sql(h, "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'", (did,))[0][0])
    body = sql(h, "SELECT body_json, metadata_json FROM records WHERE kind='response'")
    return h, digest, json.loads(result), terminal, body


def test_digests_and_retry_do_not_depend_on_the_caller(tmp_path):
    ha, digest_a, result_a, terminal_a, body_a = _run(tmp_path, "wa", A)
    hb, digest_b, result_b, terminal_b, body_b = _run(tmp_path, "wb", B)
    _, digest_n, result_n, _, body_n = _run(tmp_path, "wn", None)
    for text in (json.dumps(result_a), json.dumps(terminal_a), repr(body_a)):
        assert "lead-a" not in text and "ask_pid" not in text
    assert terminal_a["digest"] == digest_a == _digest(result_a) and terminal_b["digest"] == digest_b == _digest(result_b)
    # the digested result has the same shape and reply whatever the caller (record ids differ per workspace)
    assert [b[0] for b in body_a] == [b[0] for b in body_b] == [b[0] for b in body_n]
    assert sorted(result_a) == sorted(result_b) == sorted(result_n)
    assert result_a["response"] == result_b["response"] == result_n["response"]
    # idempotent retry by another caller: nothing re-runs and no second caller row appears
    again = ha.new_bridge("retry", caller=B)
    ha.clock.advance(31)
    rt = FakeRuntimeTarget()
    again.delivery_cycle("b", "s", rt)
    assert rt.calls == [] or all(c[0] != "deliver" for c in rt.calls)
    assert len(caller_rows(ha)) == 1
    assert sql(ha, "SELECT result_digest FROM bridge_deliveries") == [(digest_a,)]
