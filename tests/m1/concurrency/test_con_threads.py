"""Wave 2: CON-001..005, 009..011 with REAL threads and independent SQLite connections (one CoreStore per writer)."""
import sqlite3
import threading
from collections import Counter

import pytest

from peerhub.m1.store import CasMismatchError, CoreStore, IdempotencyConflictError, OffsetBeyondHeadError
from tests.m1.harness.mp import run_threads
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.concurrency, pytest.mark.sqlite]
BT = 15  # bounded barrier/join timeout (s)


def _positions(db_path, stream="s"):
    with sqlite3.connect(db_path) as c:
        return [r[0] for r in c.execute("SELECT position FROM records WHERE stream_id=? ORDER BY position", (stream,))]


def _integrity(db_path):
    with sqlite3.connect(db_path) as c:
        return c.execute("PRAGMA integrity_check").fetchone()[0]


def _writers(harness, n_writers, per_writer, *, stream="s", author="a", tag="w"):
    barrier = threading.Barrier(n_writers, timeout=BT)

    def make(w):
        store = CoreStore(harness.db_path)  # independent store/connections per writer

        def run():
            barrier.wait()
            out = []
            for i in range(per_writer):
                try:
                    out.append(store.append_record(**req(body=[w, i], key=f"{tag}{w}-{i}", stream=stream, author=author)))
                except sqlite3.OperationalError as e:  # only an explicit bounded busy failure is tolerated
                    assert "locked" in str(e) or "busy" in str(e), e
                    out.append(e)
            return out
        return run

    return run_threads([make(w) for w in range(n_writers)], timeout=BT * 2)


@pytest.mark.m1_id("CON-001")
def test_con_001_concurrent_append_unique_ordered_positions(harness):
    seed(harness)
    results = _writers(harness, 2, 50)
    recs = [r for res in results for r in res]
    assert len(recs) == 100 and all(not isinstance(r, Exception) for r in recs), [r for r in recs if isinstance(r, Exception)][:3]
    read = harness.read_records("s", 0, None)
    assert len(read) == 100
    pos = [r.position for r in read]
    assert pos == sorted(pos) and len(set(pos)) == 100
    assert {r.record_id for r in read} == {r.record_id for r in recs}
    assert {r.idempotency_key for r in read} == {f"w{w}-{i}" for w in range(2) for i in range(50)}
    # per-writer program order is preserved inside the global order
    for w in range(2):
        mine = [r.position for r in read if r.idempotency_key.startswith(f"w{w}-")]
        assert mine == sorted(mine)


@pytest.mark.m1_id("CON-002")
def test_con_002_high_contention_append_is_corruption_free(harness):
    seed(harness)
    results = _writers(harness, 8, 50)
    flat = [r for res in results for r in res]
    assert len(flat) == 400  # one response per attempted append, none lost
    ok = [r for r in flat if not isinstance(r, Exception)]
    failed = [r for r in flat if isinstance(r, Exception)]  # only explicit busy errors passed the writer filter
    assert len(ok) + len(failed) == 400
    ok_keys = [r.idempotency_key for r in ok]
    assert len(set(ok_keys)) == len(ok_keys) and len(set(r.record_id for r in ok)) == len(ok)  # no duplicate success
    with sqlite3.connect(harness.db_path) as c:
        rows = c.execute("SELECT idempotency_key, record_id, position FROM records").fetchall()
    assert len(rows) == len(ok)  # durable rows == explicit successes (nothing extra, nothing missing)
    assert sorted(rows) == sorted((r.idempotency_key, r.record_id, r.position) for r in ok)
    assert len({p for _, _, p in rows}) == len(rows)
    assert _integrity(harness.db_path) == "ok"
    fk = sqlite3.connect(harness.db_path)
    try:
        assert fk.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fk.close()


@pytest.mark.m1_id("CON-003")
def test_con_003_concurrent_identical_retries_create_one_record(harness):
    seed(harness)
    for rnd in range(25):  # many real races, each with a fresh key
        barrier = threading.Barrier(2, timeout=BT)
        stores = [CoreStore(harness.db_path) for _ in range(2)]

        def make(st):
            def run():
                barrier.wait()
                return st.append_record(**req(body="same", key=f"id-{rnd}"))
            return run

        res = run_threads([make(s) for s in stores])
        assert all(not isinstance(r, Exception) for r in res), res
        assert res[0].record_id == res[1].record_id and res[0].position == res[1].position
        with sqlite3.connect(harness.db_path) as c:
            assert c.execute("SELECT COUNT(*) FROM records WHERE idempotency_key=?", (f"id-{rnd}",)).fetchone()[0] == 1
    assert _positions(harness.db_path) == sorted(set(_positions(harness.db_path))) and len(_positions(harness.db_path)) == 25


@pytest.mark.m1_id("CON-004")
def test_con_004_same_key_different_payload_one_winner_one_conflict(harness):
    seed(harness)
    winners = Counter()
    for rnd in range(25):
        barrier = threading.Barrier(2, timeout=BT)
        stores = [CoreStore(harness.db_path) for _ in range(2)]

        def make(st, body):
            def run():
                barrier.wait()
                return st.append_record(**req(body=body, key=f"K-{rnd}"))
            return run

        res = run_threads([make(stores[0], "A"), make(stores[1], "B")])
        ok = [r for r in res if not isinstance(r, Exception)]
        bad = [r for r in res if isinstance(r, Exception)]
        assert len(ok) == 1 and len(bad) == 1 and isinstance(bad[0], IdempotencyConflictError), res
        with sqlite3.connect(harness.db_path) as c:
            rows = c.execute("SELECT record_id, body_json FROM records WHERE idempotency_key=?", (f"K-{rnd}",)).fetchall()
        assert rows == [(ok[0].record_id, f'"{ok[0].body}"')]
        winners[ok[0].body] += 1
    assert sum(winners.values()) == 25  # both orders are allowed; exactly-one is the oracle


@pytest.mark.m1_id("CON-005")
def test_con_005_offset_cas_race_exactly_one_winner(harness):
    seed(harness)
    append_n(harness, 10)
    for rnd in range(15):
        peer = "b"
        harness.create_peer({"peer_id": f"p{rnd}"})
        harness.cas_stream("s", harness.get_stream("s").revision, {"members": ["a", "b", f"p{rnd}"]})
        peer = f"p{rnd}"
        rev = harness.get_offset(peer, "s").revision
        barrier = threading.Barrier(2, timeout=BT)
        stores = [CoreStore(harness.db_path) for _ in range(2)]

        def make(st, pos):
            def run():
                barrier.wait()
                return st.advance_offset_cas(peer, "s", pos, rev)
            return run

        res = run_threads([make(stores[0], 4), make(stores[1], 7)])
        ok = [r for r in res if not isinstance(r, Exception)]
        lost = [r for r in res if isinstance(r, Exception)]
        assert len(ok) == 1 and len(lost) == 1 and isinstance(lost[0], CasMismatchError), res
        final = harness.get_offset(peer, "s")
        assert final == ok[0] and final.revision == rev + 1 and final.read_through_position in (4, 7)


@pytest.mark.m1_id("CON-009")
def test_con_009_different_streams_progress_independently(harness):
    seed(harness, stream="s1")
    harness.create_stream({"stream_id": "s2", "members": ["a", "b"]})
    barrier = threading.Barrier(4, timeout=BT)

    def make(stream, w):
        store = CoreStore(harness.db_path)

        def run():
            barrier.wait()
            return [store.append_record(**req(body=i, key=f"{stream}-{w}-{i}", stream=stream)) for i in range(40)]
        return run

    res = run_threads([make("s1", 0), make("s1", 1), make("s2", 0), make("s2", 1)], timeout=BT * 2)
    assert all(not isinstance(r, Exception) for r in res), res
    for stream in ("s1", "s2"):
        pos = [r.position for r in harness.read_records(stream, 0, None)]
        committed = _positions(harness.db_path, stream)  # actual committed positions; gaps are allowed (TD-01)
        assert pos == committed and len(pos) == 80 and len(set(pos)) == 80 and pos == sorted(pos)
        assert all(r.stream_id == stream for r in harness.read_records(stream, 0, None))
    p1, p2 = _positions(harness.db_path, "s1"), _positions(harness.db_path, "s2")
    assert set(p1) & set(p2)  # independent per-stream sequences overlap in value; a shared counter could never repeat


def _race_offset_vs_append(harness, order):
    """Deterministic orderings via real hooks. Offset at H=3, writer appends position 4, advancer CAS to 4."""
    seed(harness)
    append_n(harness, 3)
    off = harness.cas_offset("b", "s", 1, 3)
    writer, advancer = CoreStore(harness.db_path), CoreStore(harness.db_path)
    gate_in, gate_release = threading.Event(), threading.Event()

    def hold(point_name):
        def hook(point):
            if point == point_name:
                gate_in.set()
                assert gate_release.wait(BT)
        return hook

    out = {}
    entered = threading.Event()  # set by the SECOND thread's store when it enters its competing operation

    def signal(point_name):
        def hook(point):
            if point == point_name:
                entered.set()
        return hook

    if order == "advancer-first":  # advancer holds the write lock inside its head check; writer must queue behind it
        advancer.fault_hook = hold("offset.before_head_check")
        writer.fault_hook = signal("append.begin")
        first = threading.Thread(target=lambda: out.update(adv=_try(lambda: advancer.advance_offset_cas("b", "s", 4, off.revision))), daemon=True)
        second = threading.Thread(target=lambda: out.update(app=_try(lambda: writer.append_record(**req(body="new", key="h4")))), daemon=True)
    else:  # writer-first: writer commits H+1 only after the advancer is already waiting for the lock
        writer.fault_hook = hold("append.before_commit")
        advancer.fault_hook = signal("offset.begin")
        first = threading.Thread(target=lambda: out.update(app=_try(lambda: writer.append_record(**req(body="new", key="h4")))), daemon=True)
        second = threading.Thread(target=lambda: out.update(adv=_try(lambda: advancer.advance_offset_cas("b", "s", 4, off.revision))), daemon=True)
    first.start()
    assert gate_in.wait(BT)
    second.start()
    assert entered.wait(BT), "second thread never entered its competing operation"  # handshake before releasing
    gate_release.set()
    first.join(BT), second.join(BT)
    assert not first.is_alive() and not second.is_alive()
    return off, out


def _try(fn):
    try:
        return fn()
    except BaseException as e:
        return e


@pytest.mark.m1_id("CON-010")
def test_con_010_offset_head_check_and_append_race_never_skips_unseen_record(harness):
    off, out = _race_offset_vs_append(harness, "advancer-first")
    # serialization order: CAS (head still 3) -> append. The CAS to 4 must be rejected explicitly, nothing moved.
    assert isinstance(out["adv"], OffsetBeyondHeadError) and not isinstance(out["app"], Exception)
    assert harness.get_offset("b", "s") == off and harness.get_stream("s") is not None
    retry = harness.cas_offset("b", "s", off.revision, 4)  # stale attempt retried after commit: accepted, head is 4
    assert retry.read_through_position == 4 and retry.read_through_position <= harness.store.stream_head("s")

    from tests.m1.harness.core import CoreHarness
    h2 = CoreHarness(harness.workspace.parent / "ws2")
    off2, out2 = _race_offset_vs_append(h2, "writer-first")
    # serialization order: append -> CAS. Accepted offset equals the committed head visible in the same order.
    assert not isinstance(out2["app"], Exception) and not isinstance(out2["adv"], Exception), out2
    assert out2["adv"].read_through_position == h2.store.stream_head("s") == 4

    # free-running real race (many rounds): invariant holds under any interleaving
    h3 = CoreHarness(harness.workspace.parent / "ws3")
    seed(h3)
    head, rev = 0, 1
    for rnd in range(20):
        barrier = threading.Barrier(2, timeout=BT)
        w, a = CoreStore(h3.db_path), CoreStore(h3.db_path)
        target = head + 1

        def do_append():
            barrier.wait()
            return w.append_record(**req(body=rnd, key=f"r{rnd}"))

        def do_cas():
            barrier.wait()
            return a.advance_offset_cas("b", "s", target, rev)

        r_app, r_cas = run_threads([do_append, do_cas])
        assert not isinstance(r_app, Exception)
        committed_head = h3.store.stream_head("s")
        if isinstance(r_cas, Exception):
            assert isinstance(r_cas, OffsetBeyondHeadError), r_cas  # explicit; never skips or silently clamps
            r_cas = h3.cas_offset("b", "s", rev, target)  # retry after commit must succeed
        assert r_cas.read_through_position == target <= committed_head
        head, rev = committed_head, r_cas.revision


@pytest.mark.m1_id("CON-011")
def test_con_011_append_during_paged_catch_up_no_dup_no_skip(harness):
    seed(harness)
    initial = append_n(harness, 10)
    reader = CoreStore(harness.db_path)
    page1 = reader.read_records("s", 0, 4)
    assert [r.position for r in page1] == [1, 2, 3, 4]
    writer_done = threading.Event()

    def writer():
        w = CoreStore(harness.db_path)
        for i in range(6):
            w.append_record(**req(body=f"late{i}", key=f"late{i}"))
        writer_done.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert writer_done.wait(BT)  # barrier: new Records are committed before the next page is read
    t.join(BT)
    seen = list(page1)
    cursor = seen[-1].position
    while True:
        page = reader.read_records("s", cursor, 4)
        if not page:
            break
        seen.extend(page)
        cursor = page[-1].position
    pos = [r.position for r in seen]
    committed = _positions(harness.db_path)
    assert pos == committed == sorted(set(pos))  # no repeat, no skip, ordered
    assert [r.record_id for r in seen[:10]] == [r.record_id for r in initial]
    assert [r.idempotency_key for r in seen[10:]] == [f"late{i}" for i in range(6)]  # late commits appear after their position only
    assert reader.read_records("s", cursor, 4) == []  # positive control: terminated at head
