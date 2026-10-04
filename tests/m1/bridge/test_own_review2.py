"""cx final review part 2: pre-invoke fence, attempt binding, reconcile addressing, DB-level resolution/certainty guards."""
import itertools
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import ReconcileRejectedError
from tests.m1.bridge_helpers import bseed, delivery_rows, more_stream, offset_row, responses, sql
from tests.m1.fakes import FakeRuntimeTarget
from tests.m1.harness.bridge import BridgeHarness
from tests.m1.helpers import req

pytestmark = [pytest.mark.integration, pytest.mark.bridge]
NS, MAY, ST, TERM = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"


# ---------------------------------------------------------------- 1. claim re-validated right before the runtime invoke
@pytest.mark.m1_id("BRG-012")
def test_takeover_between_marker_and_invoke_never_calls_the_runtime(tmp_path):
    fired = []

    def hook(p):
        if p == "bridge.before_runtime_invoke" and not fired:
            fired.append(p)
            h.clock.advance(31)  # A's lease lapses ...
            h.acquire_claim("b", "s", "B", 30)  # ... and B takes over, no crash

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "fenced" and rt.count("deliver") == 0 and responses(h) == []
    (d,) = delivery_rows(h, rec.record_id)
    assert d[7] == NS and offset_row(h) == (0, 1)
    # B recovers: the unresolved marker is conservatively MAY_HAVE_STARTED (A cannot prove anything once fenced), never re-invoked
    rt2 = FakeRuntimeTarget()
    assert h.new_bridge("B").delivery_cycle("b", "s", rt2).status == "blocked_uncertain" and rt2.count("deliver") == 0
    # positive control: without the takeover the same cycle invokes
    h2 = BridgeHarness(tmp_path / "ws2")
    bseed(h2)
    rt3 = FakeRuntimeTarget()
    assert h2.delivery_cycle("b", "s", rt3).status == "delivered" and rt3.count("deliver") == 1


# ---------------------------------------------------------------- 2. attempt binding authenticates the Record
def test_begin_attempt_rejects_foreign_altered_and_nonexistent_records(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    (other,) = more_stream(h, "s2")
    tok = h.acquire_claim("b", "s", "A", 1000)
    for bad in (other, rec.model_copy(update={"position": 999}), rec.model_copy(update={"record_id": "rec-nope"}),
                rec.model_copy(update={"author_peer_id": "b"})):
        with pytest.raises(ValueError):
            h.bridge.begin_attempt(tok, bad)
    assert delivery_rows(h) == [] and sql(h, "SELECT COUNT(*) FROM bridge_evidence") == [(0,)]
    did = h.bridge.begin_attempt(tok, rec)  # positive control: the authentic Record is bound
    assert delivery_rows(h)[0][0] == did and delivery_rows(h)[0][5] == rec.position


# ---------------------------------------------------------------- 3. reconcile must address the delivery's peer
def _uncertain_delivery(h, rec):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("timeout",))
    assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    return delivery_rows(h, rec.record_id)[0][0]


def test_reconcile_must_target_the_deliverys_peer(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    h.create_peer({"peer_id": "a"})
    h.create_peer({"peer_id": "b"})
    h.create_peer({"peer_id": "c"})
    h.create_stream({"stream_id": "s", "members": ["a", "b", "c"]})
    rec = h.append_record(req(body="m", key="m0", author="a"))
    did = _uncertain_delivery(h, rec)

    def rc(key, targets):
        return h.append_record({**req(body={"decision": "RETRY", "delivery_id": did}, key=key, author="a", kind="control.reconcile"),
                                "targets": targets})
    only_c = rc("c-only", ["c"])
    with pytest.raises(ReconcileRejectedError):
        h.reconcile_uncertain(rec.record_id, only_c)
    assert sql(h, "SELECT COUNT(*) FROM bridge_reconciliations") == [(0,)]
    with closing(sqlite3.connect(h.db_path)) as c, pytest.raises(sqlite3.DatabaseError):  # the DB trigger refuses it too
        c.execute("INSERT INTO bridge_reconciliations (delivery_id, reconcile_record_id, decision) VALUES (?,?,?)", (did, only_c.record_id, "RETRY"))
    assert sql(h, "SELECT COUNT(*) FROM bridge_reconciliations") == [(0,)]
    good = rc("b-target", ["b"])
    assert h.reconcile_uncertain(rec.record_id, good)["reconcile_record_id"] == good.record_id
    assert sql(h, "SELECT COUNT(*) FROM bridge_reconciliations") == [(1,)]


# ---------------------------------------------------------------- 4. resolution rows are verifiable at DB level
def _marked(h):
    (rec,) = bseed(h)
    tok = h.acquire_claim("b", "s", "A", 1000)
    did = h.bridge.begin_attempt(tok, rec)
    h.bridge._mark_invocation(tok, did, external_session_id="x", session_generation=1)
    return rec, tok, did


def _raw(h, stmt, args=()):
    with closing(sqlite3.connect(h.db_path)) as c:  # recursive_triggers OFF (default)
        c.execute(stmt, args)
        c.commit()


def _ev(h, did, kind, no=1, cert=NS):
    _raw(h, "INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) VALUES (?,?,?,?,?,?)",
         (did, "s", "b", kind, cert, json.dumps({"invocation_no": no})))


@pytest.mark.parametrize("case", ["bogus_reason", "no_evidence", "kind_mismatch", "promoted_on_not_started", "missing_marker",
                                  "wrong_invocation_no", "generation_negative"])
def test_resolution_insert_is_constrained_by_the_database(tmp_path, case):
    h = BridgeHarness(tmp_path / "ws")
    rec, tok, did = _marked(h)
    ins = "INSERT INTO bridge_invocation_resolutions (delivery_id, invocation_no, reason, claim_generation) VALUES (?,?,?,?)"
    if case == "bogus_reason":
        _ev(h, did, "prespawn_failure")
        args = (did, 1, "not-a-proven-outcome", 999)
    elif case == "no_evidence":
        args = (did, 1, "prespawn_failure", 1)
    elif case == "kind_mismatch":
        _ev(h, did, "prespawn_failure")
        args = (did, 1, "control_halt", 1)
    elif case == "promoted_on_not_started":
        _ev(h, did, "invocation_unresolved_recovered")
        args = (did, 1, "promoted_may_have_started", 1)
    elif case == "missing_marker":
        _ev(h, did, "prespawn_failure", no=2)
        args = (did, 2, "prespawn_failure", 1)
    elif case == "wrong_invocation_no":
        _ev(h, did, "prespawn_failure", no=7)
        args = (did, 1, "prespawn_failure", 1)
    else:
        _ev(h, did, "prespawn_failure")
        args = (did, 1, "prespawn_failure", -1)
    with pytest.raises(sqlite3.DatabaseError):
        _raw(h, ins, args)
    assert sql(h, "SELECT COUNT(*) FROM bridge_invocation_resolutions") == [(0,)]
    # positive control: the verifiable shape is accepted
    h2 = BridgeHarness(tmp_path / "ws2")
    rec2, tok2, did2 = _marked(h2)
    _ev(h2, did2, "prespawn_failure")
    _raw(h2, ins, (did2, 1, "prespawn_failure", 1))
    assert sql(h2, "SELECT reason FROM bridge_invocation_resolutions") == [("prespawn_failure",)]


# ---------------------------------------------------------------- 5. certainty is forward-only at DB level
LEGAL_RAW = {(NS, MAY), (NS, ST), (MAY, ST), (MAY, TERM), (ST, TERM)}  # independent literal of the permitted changes


def _reach(h, target):
    (rec,) = bseed(h)
    tok = h.acquire_claim("b", "s", "A", 1000)
    did = h.bridge.begin_attempt(tok, rec)
    if target == MAY:
        h.bridge.transition(tok, did, MAY)
    elif target in (ST, TERM):
        h.bridge.transition(tok, did, ST)
        if target == TERM:
            h.bridge.transition(tok, did, TERM)
    return did


@pytest.mark.parametrize("frm,to", list(itertools.product([NS, MAY, ST, TERM], [NS, MAY, ST, TERM, "BOGUS"])))
def test_certainty_update_is_forward_only_in_the_database(tmp_path, frm, to):
    h = BridgeHarness(tmp_path / "ws")
    did = _reach(h, frm)
    if (frm, to) in LEGAL_RAW:
        _raw(h, "UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, did))
        assert delivery_rows(h)[0][7] == to
    elif frm != to:
        with pytest.raises(sqlite3.DatabaseError):
            _raw(h, "UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, did))
        assert delivery_rows(h)[0][7] == frm
    else:  # same-state write is a no-op for non-terminal rows (TERMINAL rows are fully immutable)
        if frm == TERM:
            with pytest.raises(sqlite3.DatabaseError):
                _raw(h, "UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, did))
        else:
            _raw(h, "UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, did))
        assert delivery_rows(h)[0][7] == frm
