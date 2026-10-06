"""Wave 3 BRG-002/003/004/005/010/016/017/018: session mapping and generations against FakeRuntimeTarget (no real CLI)."""
import pytest

from peerhub.extensions.bridge import SessionError
from tests.communication.bridge_helpers import bseed, delivery_rows, more_stream, offset_row, records, responses, sql
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

pytestmark = [pytest.mark.integration, pytest.mark.bridge]


def _add(h, key, stream="s", author="a"):
    return h.append_record(req(body=key, key=key, author=author, stream=stream))


def _session_rows(h):
    return sql(h, "SELECT peer_id, stream_id, runtime_kind, external_session_id, session_generation, adapter_fingerprint, binding, "
                  "resumable, state, last_seen FROM bridge_sessions ORDER BY 1,2")


def _events(h, peer="b", stream="s"):
    return sql(h, "SELECT event, from_state, to_state, generation FROM bridge_session_events WHERE peer_id=? AND stream_id=? ORDER BY seq",
               (peer, stream))


@pytest.mark.catalog_id("BRG-002")
def test_brg_002_no_mapping_creates_fresh_generation_1(bridge_h):
    (rec,) = bseed(bridge_h)
    rt = FakeRuntimeTarget()
    assert bridge_h.current_mapping("b", "s") is None and _session_rows(bridge_h) == []  # control: nothing before delivery
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 1
    assert [c[0] for c in rt.calls] == ["create", "deliver"]  # created, never resumed
    assert rt.calls[1][1:3] == ("ext-1", rec.record_id)
    assert _session_rows(bridge_h) == [("b", "s", "fake", "ext-1", 1, "F1", "model-a/profile-1", 1, "ACTIVE", bridge_h.clock.now())]
    assert _events(bridge_h) == [("created", "NONE", "ACTIVE", 1)]  # STM-040 NONE -> ACTIVE


@pytest.mark.catalog_id("BRG-003")
def test_brg_003_resumable_matching_fingerprint_uses_existing_session(bridge_h):
    bseed(bridge_h, n=1)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    r2 = _add(bridge_h, "second")
    bridge_h.clock.advance(5)
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 1
    assert rt.count("create") == 1 and ("resume", "ext-1") in rt.calls  # resume path, same external id
    assert [c for c in rt.calls if c[0] == "deliver"][1][1:3] == ("ext-1", r2.record_id)
    assert _session_rows(bridge_h)[0][3:5] == ("ext-1", 1) and _session_rows(bridge_h)[0][9] == bridge_h.clock.now()  # last_seen updated
    assert _events(bridge_h)[-1] == ("resume_ok", "ACTIVE", "ACTIVE", 1)  # STM-041
    # negative control: a NON-resumable mapping is never resumed (fresh generation instead)
    h2 = BridgeHarness(bridge_h.workspace.parent / "ws2")
    bseed(h2)
    rt2 = FakeRuntimeTarget(resumable=False)
    h2.delivery_cycle("b", "s", rt2)
    _add(h2, "second")
    h2.delivery_cycle("b", "s", rt2)
    assert rt2.count("resume") == 0 and rt2.count("create") == 2 and _session_rows(h2)[0][4] == 2


@pytest.mark.catalog_id("BRG-004")
def test_brg_004_lost_session_triggers_fresh_generation_with_catch_up(bridge_h):
    first = bseed(bridge_h, n=1)
    rt = FakeRuntimeTarget()
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"
    r2, r3 = _add(bridge_h, "u2"), _add(bridge_h, "u3")
    rt.sessions.clear()  # provider lost the session
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 2
    assert ("resume", "ext-1") in rt.calls and rt.count("create") == 2
    ids_in_order = [r[0] for r in records(bridge_h)]  # independent oracle: durable Stream order
    # catch-up = every durable Record before the one being delivered (the response to m0 is among them)
    pos_r2 = [r[1] for r in records(bridge_h) if r[0] == r2.record_id][0]
    # D-W4-8b: a fresh generation is bootstrapped from the Stream history (Offset governs delivery only)
    expected_catch_up = [r[0] for r in records(bridge_h) if r[1] < pos_r2]
    assert rt.catch_ups[-1] == expected_catch_up and first[0].record_id in expected_catch_up
    assert rt.catch_ups[0] == []  # first delivery had no history
    assert _session_rows(bridge_h)[0][3:5] == ("ext-2", 2) and _session_rows(bridge_h)[0][8] == "FRESH"
    assert _events(bridge_h) == [("created", "NONE", "ACTIVE", 1), ("session_lost", "ACTIVE", "LOST", 1),
                                 ("fresh_generation", "LOST", "FRESH", 2)]  # STM-042, STM-043
    assert bridge_h.delivery_cycle("b", "s", rt).status == "delivered"  # next record via the new generation
    delivered = [c[2] for c in rt.calls if c[0] == "deliver"]
    inputs = [r[0] for r in records(bridge_h) if r[3] == "message"]  # Wave 4: the bridge also writes context.boundary Records (not inputs)
    assert delivered == [first[0].record_id, r2.record_id, r3.record_id] and delivered == inputs  # no lost Record, no duplicates
    assert offset_row(bridge_h)[0] == max(r[1] for r in records(bridge_h) if r[0] == r3.record_id)
    assert [c for c in rt.calls if c[0] == "deliver"][2][1] == "ext-2"


@pytest.mark.catalog_id("BRG-005")
def test_brg_005_fingerprint_change_forces_fresh_generation(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget(fingerprint="F1")
    bridge_h.delivery_cycle("b", "s", rt)
    _add(bridge_h, "next")
    rt.set_fingerprint("F2")
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 2
    assert ("resume", "ext-1") not in rt.calls  # the old external session is NOT blindly resumed
    assert [c for c in rt.calls if c[0] == "deliver"][1][1] == "ext-2"
    row = _session_rows(bridge_h)[0]
    assert row[3:7] == ("ext-2", 2, "F2", "model-a/profile-1") and row[8] == "FRESH"
    assert _events(bridge_h)[-2:] == [("fingerprint_change", "ACTIVE", "FRESH", 1), ("fresh_generation", "ACTIVE", "FRESH", 2)]
    # positive control: same fingerprint afterwards resumes the new generation
    _add(bridge_h, "third")
    bridge_h.delivery_cycle("b", "s", rt)
    assert ("resume", "ext-2") in rt.calls and rt.count("create") == 2


@pytest.mark.catalog_id("BRG-010")
def test_brg_010_two_peers_same_adapter_keep_independent_sessions(bridge_h):
    for p in ("a", "cx-01", "cx-02"):
        bridge_h.create_peer({"peer_id": p})
    bridge_h.create_stream({"stream_id": "s", "members": ["a", "cx-01", "cx-02"]})
    r1 = _add(bridge_h, "q1")
    rt = FakeRuntimeTarget()  # ONE adapter instance shared by both peers
    assert bridge_h.delivery_cycle("cx-01", "s", rt).status == "delivered"
    assert offset_row(bridge_h, "cx-02")[0] == 0 and bridge_h.current_mapping("cx-02", "s") is None  # no bleed yet
    assert bridge_h.delivery_cycle("cx-02", "s", rt).status == "delivered"
    m1, m2 = bridge_h.current_mapping("cx-01", "s"), bridge_h.current_mapping("cx-02", "s")
    assert (m1["external_session_id"], m2["external_session_id"]) == ("ext-1", "ext-2")
    assert {r[0] for r in sql(bridge_h, "SELECT peer_id FROM bridge_claims")} == {"cx-01", "cx-02"}
    assert {r[2] for r in responses(bridge_h)} == {"cx-01", "cx-02"}
    # second round: each peer resumes ITS OWN session; offsets stay peer-scoped
    r2 = _add(bridge_h, "q2")
    rt.calls.clear()
    bridge_h.delivery_cycle("cx-01", "s", rt)
    assert rt.calls[0] == ("resume", "ext-1") and rt.calls[1][1] == "ext-1"
    assert bridge_h.current_mapping("cx-02", "s")["external_session_id"] == "ext-2"
    off1, off2 = offset_row(bridge_h, "cx-01"), offset_row(bridge_h, "cx-02")
    assert off1[0] > off2[0] and r1 and r2  # cx-02 has not consumed q2 yet
    bridge_h.delivery_cycle("cx-02", "s", rt)
    assert rt.calls[2] == ("resume", "ext-2") and rt.calls[3][1] == "ext-2"


@pytest.mark.catalog_id("BRG-016")
@pytest.mark.parametrize("outcome", ["rejected", "unsupported"])
def test_brg_016_resume_failure_falls_back_once_to_fresh_generation(bridge_h, outcome):
    bseed(bridge_h)
    rt = FakeRuntimeTarget()
    bridge_h.delivery_cycle("b", "s", rt)
    _add(bridge_h, "next")
    rt.script_resume(outcome)  # provider rejects the stored session id
    res = bridge_h.delivery_cycle("b", "s", rt)
    assert res.status == "delivered" and res.session_generation == 2
    assert rt.count("resume") == 1 and rt.count("create") == 2  # exactly ONE fresh generation, no resume loop
    assert _events(bridge_h)[1:] == [("resume_rejected", "ACTIVE", "FRESH", 1), ("fresh_generation", "ACTIVE", "FRESH", 2)]  # STM-045
    assert _session_rows(bridge_h)[0][3:5] == ("ext-2", 2)
    assert len(rt.catch_ups[-1]) >= 1 and rt.catch_up_meta[-1]["mode"] == "fresh_generation"  # catch-up delivered with the fresh generation
    assert len(responses(bridge_h)) == 2  # one response per input, no duplicates
    # positive control: when resume succeeds nothing fresh is created
    _add(bridge_h, "third")
    bridge_h.delivery_cycle("b", "s", rt)
    assert rt.count("create") == 2 and _session_rows(bridge_h)[0][4] == 2


@pytest.mark.fault
@pytest.mark.catalog_id("BRG-017")
def test_brg_017_repeated_session_failures_terminate_boundedly(tmp_path):
    h = BridgeHarness(tmp_path / "ws", max_session_attempts=3)
    (rec,) = bseed(h)
    rt = FakeRuntimeTarget()
    rt.script_create(*[SessionError("provider down")] * 50)
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "session_unavailable" and res.certainty == "NOT_STARTED" and res.detail == {"attempts": 3}
    assert rt.count("create") == 3 and rt.count("deliver") == 0  # exactly the configured bound, nothing delivered
    assert len(records(h)) == 1 and offset_row(h) == (0, 1)  # no duplicate/new Records, Offset unchanged
    (d,) = delivery_rows(h, rec.record_id)
    assert d[7] == "NOT_STARTED"  # certainty column
    fails = [e for e in _events(h) if e[0] == "create_failed"]
    assert len(fails) == 3
    # second cycle is again bounded (3 more attempts, not an unbounded loop) and still creates no duplicate rows
    h.delivery_cycle("b", "s", rt)
    assert rt.count("create") == 6 and len(delivery_rows(h, rec.record_id)) == 1
    # positive control: success on the last allowed attempt delivers
    h2 = BridgeHarness(tmp_path / "ws2", max_session_attempts=3)
    bseed(h2)
    rt2 = FakeRuntimeTarget()
    rt2.script_create(SessionError("x"), SessionError("y"), "ok")
    assert h2.delivery_cycle("b", "s", rt2).status == "delivered" and rt2.count("create") == 3
    # bound of 1 means exactly one attempt
    h3 = BridgeHarness(tmp_path / "ws3", max_session_attempts=1)
    bseed(h3)
    rt3 = FakeRuntimeTarget()
    rt3.script_create(*[SessionError("z")] * 5)
    assert h3.delivery_cycle("b", "s", rt3).status == "session_unavailable" and rt3.count("create") == 1


@pytest.mark.fault
@pytest.mark.catalog_id("BRG-017")
def test_brg_017_resume_and_create_all_fail_after_existing_mapping(tmp_path):
    h = BridgeHarness(tmp_path / "ws", max_session_attempts=2)
    bseed(h)
    rt = FakeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    _add(h, "next")
    before_resp = len(responses(h))
    rt.script_resume("rejected")
    rt.script_create(SessionError("a"), SessionError("b"))
    res = h.delivery_cycle("b", "s", rt)
    assert res.status == "session_unavailable" and rt.count("resume") == 1 and rt.count("create") == 1 + 2
    unread = [r for r in records(h) if r[3] == 'message'][-1]
    assert len(responses(h)) == before_resp and offset_row(h)[0] < unread[1]  # own response skipped, unread Record NOT consumed
    assert h.current_mapping("b", "s")["session_generation"] == 1  # old generation preserved, not overwritten by a failed one


@pytest.mark.restart
@pytest.mark.catalog_id("BRG-018")
def test_brg_018_session_mapping_survives_reopen_exactly(bridge_h):
    bseed(bridge_h)
    rt = FakeRuntimeTarget(fingerprint="F1")
    bridge_h.delivery_cycle("b", "s", rt)
    _add(bridge_h, "n2")
    rt.set_fingerprint("F2")
    bridge_h.clock.advance(7)
    bridge_h.delivery_cycle("b", "s", rt)  # mapping is now generation 2 (G != 1 so a recreate would be visible)
    expected = [("b", "s", "fake", "ext-2", 2, "F2", "model-a/profile-1", 1, "FRESH", bridge_h.clock.now())]  # independent oracle
    assert _session_rows(bridge_h) == expected
    before = bridge_h.current_mapping("b", "s")
    reopened = BridgeHarness(bridge_h.workspace, bridge_h.clock)  # fresh store/claim/bridge instances on the same files
    assert reopened.current_mapping("b", "s") == before
    assert _session_rows(reopened) == expected
    _add(reopened, "n3")
    reopened.clock.advance(1)
    res = reopened.delivery_cycle("b", "s", rt)  # same provider (it survives a bridge restart); compatible fingerprint
    assert res.status == "delivered" and res.session_generation == 2
    assert rt.count("create") == 2 and ("resume", "ext-2") in rt.calls  # resumed G=2, did not recreate generation 1
    assert reopened.current_mapping("b", "s")["external_session_id"] == "ext-2"
    # control: an incompatible fingerprint after reopen would have created generation 3
    _add(reopened, "n4")
    rt.set_fingerprint("F3")
    assert reopened.delivery_cycle("b", "s", rt).session_generation == 3
