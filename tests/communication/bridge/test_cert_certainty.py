"""Wave 3 CERT-001..003: execution certainty monotonicity and reconcile.retry (TD-11, TD-26, STM-030..038)."""
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import IllegalCertaintyTransition, ReconcileRejectedError
from tests.communication.bridge_helpers import bseed, delivery_rows, evidence, kinds, offset_row, records, responses, sql
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

NS, MAY, ST, TERM = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"
STATES = (NS, MAY, ST, TERM)
# Independent oracle (STATE_MACHINE_COVERAGE.json STM-030..038 + TD-11 "never downgrades"): the ONLY legal edges.
# D-W3-3: forward edges on late evidence (MAY->STARTED/TERMINAL) are legal; downgrades and TERMINAL overwrites are not.
LEGAL = {(NS, NS), (NS, MAY), (NS, ST), (ST, ST), (ST, TERM), (MAY, MAY), (MAY, ST), (MAY, TERM), (TERM, TERM)}
# D-OWN-A2/A4: independent literal of the FORBIDDEN edges (no downgrade anywhere, NOT_STARTED -> TERMINAL forbidden)
FORBIDDEN = {(NS, TERM), (MAY, NS), (ST, NS), (ST, MAY), (TERM, NS), (TERM, MAY), (TERM, ST)}
assert not LEGAL & FORBIDDEN and len(LEGAL) + len(FORBIDDEN) == 16


def _reconcile(h, did, decision="RETRY", kind="control.reconcile", key="rc1", stream="s"):
    return h.append_record(req(body={"decision": decision, "delivery_id": did}, key=key, author="a", stream=stream, kind=kind))


def _uncertain(h, script=(("timeout",),)):
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(*script)
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "uncertain"
    return rec, rt, res.delivery_id


def _reach(h, target):
    """Drive one fresh delivery into `target` through the public Bridge API only."""
    (rec,) = bseed(h)
    tok = h.acquire_claim("b", "s", "A", 1000)
    did = h.bridge.begin_attempt(tok, rec)
    if target == MAY:
        h.bridge.transition(tok, did, MAY)
    elif target in (ST, TERM):
        h.bridge.transition(tok, did, ST)
        if target == TERM:
            h.bridge.transition(tok, did, TERM)
    return tok, did


@pytest.mark.unit
@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-001")
@pytest.mark.parametrize("frm", STATES)
@pytest.mark.parametrize("to", STATES)
def test_cert_001_full_certainty_transition_matrix(tmp_path, frm, to):
    h = BridgeHarness(tmp_path / "ws")
    tok, did = _reach(h, frm)
    assert delivery_rows(h)[0][7] == frm  # precondition reached through the API
    before_rows, before_ev = delivery_rows(h), evidence(h)
    if (frm, to) in LEGAL:
        h.bridge.transition(tok, did, to)
        assert delivery_rows(h)[0][7] == to and len(evidence(h)) == len(before_ev) + 1
    else:
        with pytest.raises(IllegalCertaintyTransition):
            h.bridge.transition(tok, did, to)
        assert delivery_rows(h) == before_rows and evidence(h) == before_ev  # forbidden: nothing written


@pytest.mark.unit
@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-001")
def test_cert_001_may_have_started_not_cleared_by_time_or_restart(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, rt, did = _uncertain(h, [("timeout",)])
    snap = delivery_rows(h, rec.record_id)
    assert snap[0][7] == MAY
    h.clock.advance(10 ** 7)  # far beyond any lease/TTL
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # restart
    rt2 = FakeRuntimeTarget()
    for _ in range(3):
        res = h2.delivery_cycle("b", "s", rt2)
        assert res.status == "blocked_uncertain" and res.certainty == MAY
    assert rt2.calls == [] and delivery_rows(h2, rec.record_id) == snap and offset_row(h2) == (0, 1) and responses(h2) == []
    # unrelated control records / unrelated reconcile do not clear it
    h2.append_record(req(body="chatter", key="c1", author="a"))
    assert h2.delivery_cycle("b", "s", rt2).status == "blocked_uncertain" and rt2.calls == []
    # positive control: a NOT_STARTED failure DOES retry after time passes (uncertainty, not failure, is what blocks)
    h3 = BridgeHarness(tmp_path / "ws3")
    bseed(h3)
    rt3 = FakeRuntimeTarget()
    rt3.script_deliver(("prespawn_error", "x"))
    assert h3.delivery_cycle("b", "s", rt3).status == "failed_not_started"
    h3.clock.advance(10 ** 7)
    assert BridgeHarness(tmp_path / "ws3", h3.clock).delivery_cycle("b", "s", rt3).status == "delivered"


@pytest.mark.unit
@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-001")
def test_cert_001_ledger_is_append_only_and_terminal_immutable_at_the_database(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    h.delivery_cycle("b", "s", FakeRuntimeTarget())  # TERMINAL
    with closing(sqlite3.connect(h.db_path)) as c:
        before = c.execute("SELECT * FROM bridge_deliveries").fetchall(), c.execute("SELECT * FROM bridge_evidence").fetchall()
        for stmt in ("UPDATE bridge_deliveries SET certainty='STARTED'", "UPDATE bridge_deliveries SET result_json='{}'",
                     "UPDATE bridge_evidence SET kind='x'", "DELETE FROM bridge_evidence", "DELETE FROM bridge_deliveries"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(stmt)
        assert (c.execute("SELECT * FROM bridge_deliveries").fetchall(), c.execute("SELECT * FROM bridge_evidence").fetchall()) == before
        c.execute("UPDATE bridge_deliveries SET acked=1")  # positive control: non-truth bookkeeping column remains writable
        c.commit()


@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-002")
def test_cert_002_reconcile_retry_authorizes_new_linked_attempt_old_evidence_immutable(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, rt, d1 = _uncertain(h, [("timeout",)])
    old_row, old_events = delivery_rows(h, rec.record_id)[0], evidence(h, d1)
    # before any reconcile the retry stays blocked (control)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.count("deliver") == 1
    rr = _reconcile(h, d1)
    auth = h.reconcile_uncertain(rec.record_id, rr)
    assert auth["delivery_id"] == d1 and auth["reconcile_record_id"] == rr.record_id and auth["consumed_attempt"] is None
    assert delivery_rows(h, rec.record_id) == [old_row]  # registering authorization does not touch D
    res = h.delivery_cycle("b", "s", rt)  # default script: started -> terminal
    assert res.status == "delivered"
    rows = delivery_rows(h, rec.record_id)
    assert len(rows) == 2 and rows[0] == old_row  # D byte-identical, certainty still MAY_HAVE_STARTED
    new = rows[1]
    assert new[0] != d1 and new[6] == 2 and new[7] == TERM and new[17] == rr.record_id  # distinct id, attempt 2, audit link
    assert evidence(h, d1)[:len(old_events)] == old_events  # old evidence is an unchanged prefix (append-only)
    assert [e for e in evidence(h, d1) if e[1] == "reconcile_registered"] and rt.count("deliver") == 2
    assert sql(h, "SELECT consumed_attempt FROM bridge_reconciliations WHERE delivery_id=?", (d1,)) == [(2,)]
    assert len(responses(h)) == 1 and offset_row(h) == (rec.position, 2)


@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-002")
def test_cert_002_reconcile_rejections_leave_state_unchanged_with_positive_control(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, rt, d1 = _uncertain(h, [("timeout",)])
    state = sql(h, "SELECT * FROM bridge_reconciliations"), delivery_rows(h), evidence(h)
    for decision in ("ACCEPT", "ABANDON", "retry", None):  # only RETRY is defined in M1 (TD-26 Deferred)
        rr = _reconcile(h, d1, decision=decision, key=f"k-{decision}")
        with pytest.raises(ReconcileRejectedError):
            h.reconcile_uncertain(rec.record_id, rr)
    wrong_kind = _reconcile(h, d1, kind="message", key="wk")
    with pytest.raises(ReconcileRejectedError):
        h.reconcile_uncertain(rec.record_id, wrong_kind)
    bogus = _reconcile(h, "s:b:rec-nope:1", key="bg")
    with pytest.raises(ReconcileRejectedError):
        h.reconcile_uncertain(rec.record_id, bogus)
    other = h.append_record(req(body="o", key="oth", author="a"))
    with pytest.raises(ReconcileRejectedError):
        h.reconcile_uncertain(other.record_id, _reconcile(h, d1, key="mismatch"))  # record/delivery mismatch
    assert (sql(h, "SELECT * FROM bridge_reconciliations"), delivery_rows(h), evidence(h)) == state
    # a TERMINAL delivery cannot be reconciled either
    h2 = BridgeHarness(tmp_path / "ws2")
    (r2,) = bseed(h2)
    res = h2.delivery_cycle("b", "s", FakeRuntimeTarget())
    with pytest.raises(ReconcileRejectedError):
        h2.reconcile_uncertain(r2.record_id, _reconcile(h2, res.delivery_id))
    # positive control: the same call shape with a valid RETRY on the uncertain delivery is accepted
    assert h.reconcile_uncertain(rec.record_id, _reconcile(h, d1, key="good"))["decision"] == "RETRY"


@pytest.mark.restart
@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-003")
def test_cert_003_reconcile_retry_survives_restart_and_never_double_authorizes(tmp_path):
    crash = CrashInjector("bridge.after_reconcile_commit")
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    rec, rt, d1 = _uncertain(h, [("timeout",)])
    old_row = delivery_rows(h, rec.record_id)[0]
    rr = _reconcile(h, d1)
    with pytest.raises(CrashInjected):
        h.reconcile_uncertain(rec.record_id, rr)  # dies right after the authorization commit
    assert sql(h, "SELECT delivery_id, reconcile_record_id, consumed_attempt FROM bridge_reconciliations") == [(d1, rr.record_id, None)]
    assert len(delivery_rows(h, rec.record_id)) == 1  # no attempt started yet
    h2 = BridgeHarness(tmp_path / "ws", h.clock)  # restart
    rt2 = FakeRuntimeTarget()
    rt2.script_deliver(("started", "e2"), ("terminal", {"response": "again"}))
    r1 = h2.delivery_cycle("b", "s", rt2)
    r2 = h2.delivery_cycle("b", "s", rt2)  # second recovery cycle
    assert r1.status == "delivered" and r2.status == "idle"
    rows = delivery_rows(h2, rec.record_id)
    assert len(rows) == 2 and rows[0] == old_row and rows[1][6] == 2  # exactly one new attempt, old evidence unchanged
    assert rt2.count("deliver") == 1 and len(responses(h2)) == 1
    assert sql(h2, "SELECT consumed_attempt FROM bridge_reconciliations") == [(2,)]
    # duplicate consumption: same reconcile again, and a second distinct reconcile for the same delivery, authorize nothing new
    h2.reconcile_uncertain(rec.record_id, rr)
    again = h2.reconcile_uncertain(rec.record_id, _reconcile(h2, d1, key="rc2"))
    assert again["reconcile_record_id"] == rr.record_id and again["consumed_attempt"] == 2
    h3 = BridgeHarness(tmp_path / "ws", h.clock)
    rt3 = FakeRuntimeTarget()
    assert h3.delivery_cycle("b", "s", rt3).status == "idle" and rt3.calls == []
    assert len(delivery_rows(h3, rec.record_id)) == 2 and len(responses(h3)) == 1


@pytest.mark.certainty
@pytest.mark.catalog_id("CERT-003")
def test_cert_003_consumed_authorization_cannot_authorize_a_further_attempt(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, rt, d1 = _uncertain(h, [("timeout",)])
    rr = _reconcile(h, d1)
    h.reconcile_uncertain(rec.record_id, rr)
    rt.script_deliver(("timeout",))  # the authorized second attempt is uncertain again
    assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    rows = delivery_rows(h, rec.record_id)
    assert len(rows) == 2 and rows[1][7] == MAY
    before = rows, sql(h, "SELECT * FROM bridge_reconciliations")
    # replaying the OLD reconcile Record (or a new one aimed at D1) must not authorize attempt 3
    h.reconcile_uncertain(rec.record_id, rr)
    h.reconcile_uncertain(rec.record_id, _reconcile(h, d1, key="rc-again"))
    for _ in range(2):
        assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain"
    assert rt.count("deliver") == 2 and (delivery_rows(h, rec.record_id), sql(h, "SELECT * FROM bridge_reconciliations")) == before
    # positive control: an explicit reconcile of the NEW delivery does authorize exactly one more attempt
    d2 = rows[1][0]
    h.reconcile_uncertain(rec.record_id, _reconcile(h, d2, key="rc-d2"))
    assert h.delivery_cycle("b", "s", rt).status == "delivered" and len(delivery_rows(h, rec.record_id)) == 3
