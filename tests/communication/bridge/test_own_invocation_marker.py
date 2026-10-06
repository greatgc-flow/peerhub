"""D-OWN-A2/A4: separate persisted invocation marker. Certainty never downgrades; an unresolved marker blocks replay and recovery
promotes it to MAY_HAVE_STARTED; a fenced proven pre-spawn failure or a control halt resolves it."""
import multiprocessing as mp
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import IllegalCertaintyTransition, InvocationMarkerError
from peerhub.extensions.bridge_claims import ClaimScopeError, StaleClaimError
from tests.communication.bridge_helpers import bseed, delivery_rows, kinds, more_stream, offset_row, responses, sql
from tests.communication.control_helpers import ctl
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.bridge_workers import crash_cycle_worker, halt_crash_worker

pytestmark = [pytest.mark.integration, pytest.mark.bridge]
MAY, NS = "MAY_HAVE_STARTED", "NOT_STARTED"
OK = [["started", "e1"], ["terminal", {"response": "r"}]]


def _run(target, args, ws):
    p = mp.get_context("spawn").Process(target=target, args=(str(ws),) + args, daemon=True)
    p.start()
    p.join(90)
    alive = p.is_alive()
    if alive:
        p.terminate()
    assert not alive
    return p.exitcode


def markers(h):
    return sql(h, "SELECT delivery_id, invocation_no FROM bridge_invocations ORDER BY delivery_id, invocation_no")


def resolutions(h):
    return sql(h, "SELECT delivery_id, invocation_no, reason FROM bridge_invocation_resolutions ORDER BY delivery_id, invocation_no")


def _marked(h):
    (rec,) = bseed(h)
    tok = h.acquire_claim("b", "s", "A", 30)
    did = h.bridge.begin_attempt(tok, rec)
    h.bridge._mark_invocation(tok, did, external_session_id="x", session_generation=1)
    return rec, tok, did


@pytest.mark.restart
@pytest.mark.catalog_id("BRG-012")
def test_crash_after_marker_before_invoke_is_promoted_never_reinvoked(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _run(crash_cycle_worker, ("b", "s", "bridge.before_runtime_invoke", OK, h.clock.now()), tmp_path / "ws") == 17
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    (d,) = delivery_rows(h2, rec.record_id)
    assert d[7] == NS and len(markers(h2)) == 1 and resolutions(h2) == []  # pending: certainty still NOT_STARTED, marker unresolved
    rt = FakeRuntimeTarget()
    h2.clock.advance(31)
    for _ in range(2):
        res = h2.delivery_cycle("b", "s", rt)
        assert res.status == "blocked_uncertain" and res.certainty == MAY
    assert delivery_rows(h2, rec.record_id)[0][7] == MAY and "invocation_unresolved_recovered" in kinds(h2, d[0])
    assert resolutions(h2) == [(d[0], 1, "promoted_may_have_started")]
    assert rt.calls == [] and responses(h2) == [] and offset_row(h2) == (0, 1)


@pytest.mark.restart
@pytest.mark.catalog_id("BRG-012")
def test_crash_after_invoke_before_evidence_is_promoted_never_reinvoked(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    # positive control: without a crash the same worker completes and nothing is left unresolved
    assert _run(crash_cycle_worker, ("b", "s", "never.fires", OK, h.clock.now()), tmp_path / "ws") == 0
    assert delivery_rows(h, rec.record_id)[0][7] == "TERMINAL"
    h2 = BridgeHarness(tmp_path / "ws2")
    (rec2,) = bseed(h2)
    rt = FakeRuntimeTarget()

    def die():
        raise SystemExit

    rt.script_deliver(("call", die), ("started", "e"))
    with pytest.raises(SystemExit):
        h2.delivery_cycle("b", "s", rt)  # dies inside the invocation, before any start evidence
    assert delivery_rows(h2, rec2.record_id)[0][7] == NS
    h3 = BridgeHarness(tmp_path / "ws2", h2.clock)
    h3.clock.advance(31)
    rt3 = FakeRuntimeTarget()
    assert h3.delivery_cycle("b", "s", rt3).status == "blocked_uncertain" and rt3.calls == []
    assert delivery_rows(h3, rec2.record_id)[0][7] == MAY


@pytest.mark.fault
@pytest.mark.catalog_id("BRG-011")
def test_prespawn_failure_resolves_marker_without_downgrade_and_retry_remarks(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("prespawn_error", "no binary"))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "failed_not_started" and res.certainty == NS
    (d,) = delivery_rows(h, rec.record_id)
    assert resolutions(h) == [(d[0], 1, "prespawn_failure")]
    ev = sql(h, "SELECT kind, certainty FROM bridge_evidence WHERE delivery_id=? ORDER BY seq", (d[0],))
    assert ev == [("attempt_created", NS), ("about_to_invoke", NS), ("prespawn_failure", NS)]  # certainty NEVER left NOT_STARTED
    assert offset_row(h) == (0, 1)
    assert h.delivery_cycle("b", "s", rt).status == "delivered"  # retry: same attempt, a NEW marker
    assert [m[1] for m in markers(h)] == [1, 2] and len(resolutions(h)) == 1 and offset_row(h) == (rec.position, 2)


@pytest.mark.catalog_id("BRG-008")
@pytest.mark.parametrize("kind", ["control.pause", "control.cancel"])
def test_control_halt_after_marker_resolves_without_downgrade(tmp_path, kind):
    fired = []

    def hook(p):
        if p == "bridge.before_runtime_invoke" and not fired:
            fired.append(p)
            ctl(h, kind, "late")

    h = BridgeHarness(tmp_path / "ws", fault_hook=hook)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    res = h.delivery_cycle("b", "s", rt)
    (d,) = delivery_rows(h, rec.record_id)
    assert res.status == ("paused" if kind == "control.pause" else "cancelled") and rt.count("deliver") == 0
    assert resolutions(h) == [(d[0], 1, "control_halt")]
    assert [e[1] for e in sql(h, "SELECT kind, certainty FROM bridge_evidence WHERE delivery_id=? AND kind IN "
                                 "('about_to_invoke','control_halt') ORDER BY seq", (d[0],))] == [NS, NS]
    assert d[7] == NS and h.delivery_cycle("b", "s", rt).status in ("paused", "idle")  # no promotion of a resolved marker
    assert delivery_rows(h, rec.record_id)[0][7] == NS


@pytest.mark.restart
@pytest.mark.catalog_id("BRG-008")
def test_crash_after_resolution_before_ack_does_not_reinvoke(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    assert _run(halt_crash_worker, ("b", "s", "control.cancel", "bridge.after_invocation_resolved", h.clock.now()), tmp_path / "ws") == 17
    h2 = BridgeHarness(tmp_path / "ws", h.clock)
    (d,) = delivery_rows(h2, rec.record_id)
    assert d[7] == NS and d[16] == 0 and resolutions(h2)[0][2] == "control_halt"  # resolved, cancel not yet acked
    h2.clock.advance(31)
    rt = FakeRuntimeTarget()
    assert h2.delivery_cycle("b", "s", rt).status in ("cancelled", "idle") and rt.count("deliver") == 0
    assert delivery_rows(h2, rec.record_id)[0][7] == NS and offset_row(h2)[0] == rec.position
    assert len(resolutions(h2)) == 1  # not promoted: a resolved marker is no uncertainty


@pytest.mark.catalog_id("BRG-011")
def test_stale_and_foreign_callbacks_cannot_resolve(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, tok, did = _marked(h)
    more_stream(h, "s2")
    foreign = h.acquire_claim("b", "s2", "A", 30)
    before = (resolutions(h), kinds(h))
    with pytest.raises(ClaimScopeError):
        h.bridge._resolve_invocation(foreign, did, "prespawn_failure", "x", "prespawn_failure")
    h.clock.advance(31)
    h.acquire_claim("b", "s", "B", 30)  # takeover: token A is now stale
    with pytest.raises(StaleClaimError):
        h.bridge._resolve_invocation(tok, did, "prespawn_failure", "x", "prespawn_failure")
    assert (resolutions(h), kinds(h)) == before and delivery_rows(h, rec.record_id)[0][7] == NS


@pytest.mark.catalog_id("BRG-011")
def test_duplicate_resolution_and_marker_protocol_violations(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, tok, did = _marked(h)
    with pytest.raises(InvocationMarkerError):
        h.bridge._mark_invocation(tok, did)  # unresolved marker exists
    h.bridge._resolve_invocation(tok, did, "prespawn_failure", "x", "prespawn_failure")
    snap = (resolutions(h), kinds(h))
    with pytest.raises(InvocationMarkerError):
        h.bridge._resolve_invocation(tok, did, "prespawn_failure", "x", "prespawn_failure")  # duplicate
    assert (resolutions(h), kinds(h)) == snap
    h.bridge.transition(tok, did, MAY)
    with pytest.raises(InvocationMarkerError):
        h.bridge._mark_invocation(tok, did)  # certainty advanced: no new marker
    with pytest.raises(IllegalCertaintyTransition):
        h.bridge.transition(tok, did, NS)  # no downgrade through the public API either


@pytest.mark.catalog_id("BRG-011")
def test_resolution_refused_once_certainty_advanced(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    rec, tok, did = _marked(h)
    h.bridge.transition(tok, did, MAY)  # unresolved marker + advanced certainty: a resolution would be a hidden downgrade
    snap = (resolutions(h), kinds(h))
    with pytest.raises(InvocationMarkerError):
        h.bridge._resolve_invocation(tok, did, "prespawn_failure", "x", "prespawn_failure")
    assert (resolutions(h), kinds(h)) == snap and delivery_rows(h, rec.record_id)[0][7] == MAY


@pytest.mark.catalog_id("BRG-011")
@pytest.mark.parametrize("stmt", [
    "UPDATE bridge_invocations SET claim_generation=99", "DELETE FROM bridge_invocations",
    "INSERT OR REPLACE INTO bridge_invocations (delivery_id, invocation_no, stream_id, peer_id, claim_generation) VALUES ('{d}',1,'s','b',1)",
    "UPDATE bridge_invocation_resolutions SET reason='x'", "DELETE FROM bridge_invocation_resolutions",
    "INSERT OR REPLACE INTO bridge_invocation_resolutions VALUES ('{d}',1,'x',1)",
    "INSERT INTO bridge_invocation_resolutions VALUES ('nope',1,'x',1)"])
def test_marker_tables_are_append_only(tmp_path, stmt):
    h = BridgeHarness(tmp_path / "ws")
    rec, tok, did = _marked(h)
    h.bridge._resolve_invocation(tok, did, "control_halt", "x", "control_halt")
    snap = (markers(h), resolutions(h))
    with closing(sqlite3.connect(h.db_path)) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("PRAGMA recursive_triggers=ON")
        c.execute(stmt.format(d=did))
        c.commit()
    assert (markers(h), resolutions(h)) == snap


@pytest.mark.migration
@pytest.mark.catalog_id("BRG-012")
def test_marker_schema_upgrades_an_old_store_idempotently(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "e"), ("timeout",))
    assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    h.clock.advance(31)
    with closing(sqlite3.connect(h.db_path)) as c:  # emulate the previous schema: no marker tables
        for t in ("bridge_invocations", "bridge_invocation_resolutions"):
            c.execute(f"DROP TABLE {t}")
        c.commit()
    before = delivery_rows(h, rec.record_id)
    BridgeHarness(tmp_path / "ws", h.clock)  # upgrade on open
    h3 = BridgeHarness(tmp_path / "ws", h.clock)  # idempotent
    names = {r[0] for r in sql(h3, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"bridge_invocations", "bridge_invocation_resolutions"} <= names
    assert delivery_rows(h3, rec.record_id) == before  # old rows untouched, still STARTED/uncertain
    assert h3.delivery_cycle("b", "s", FakeRuntimeTarget()).status == "blocked_uncertain"
