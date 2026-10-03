"""Wave 2 hardening: SQL-011 independent digest oracle, SQL-006 snapshot UoW, workspace restore crash points (SQL-010)."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge_claims import StaleClaimError
from peerhub.m1.store import CoreStore
from peerhub.m1.workspace import SnapshotInvalidError
from tests.m1.harness.clock import ManualClock
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.sqlite]


def _independent_digest(row: dict) -> str:
    """TD-02 oracle written from the spec text with hashlib/json only (no peerhub code)."""
    proj = {"stream_id": row["stream_id"], "author_peer_id": row["author_peer_id"], "kind": row["kind"],
            "body": json.loads(row["body_json"]), "targets": json.loads(row["targets_json"]), "reply_to": row["reply_to"],
            "refs": json.loads(row["refs_json"]), "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"], "schema_version": "1.0"}
    raw = json.dumps(proj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.m1_id("SQL-011")
def test_sql_011_digest_matches_independent_raw_sql_oracle(harness):
    seed(harness)
    bodies = [{"k": [1, "two", None], "z": {"b": 1, "a": 2}}, "plain", "é中", None, 3.5]
    sent = [req(body=b, key=f"d{i}", metadata={"m": i}, targets=["b"]) for i, b in enumerate(bodies)]
    for r in sent:
        assert not {"record_id", "position", "payload_digest", "appended_at"} & set(r)
    recs = [harness.append_record(r) for r in sent]
    harness.reopen()
    with closing(sqlite3.connect(harness.db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM records ORDER BY position").fetchall()
    assert harness.read_records("s") == recs and len(rows) == 5
    digests = set()
    for row, rec in zip(rows, recs):
        assert row["payload_digest"] == _independent_digest(dict(row)) == rec.payload_digest
        assert rec.record_id and rec.position >= 1 and rec.appended_at and rec.appended_at != sent[0]["created_at"]
        digests.add(row["payload_digest"])
    assert len(digests) == 5  # a constant/degenerate digest cannot satisfy distinct payloads
    tampered = dict(rows[0])
    tampered["kind"] = "other"  # oracle sensitivity control
    assert _independent_digest(tampered) != rows[0]["payload_digest"]


@pytest.mark.m1_id("SQL-006")
def test_sql_006_read_uow_is_one_snapshot_under_concurrent_writer(harness):
    seed(harness)
    append_n(harness, 2)
    with harness.store.read_uow() as ro:
        assert ro.in_transaction  # a real read transaction, not autocommit statements
        assert ro.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
        other = CoreStore(harness.db_path)  # committed by another connection while the UoW is open
        other.append_record(**req(body="late", key="late"))
        assert other.stream_head("s") == 3
        assert ro.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2  # same committed state
        assert ro.execute("SELECT MAX(position) FROM records").fetchone()[0] == 2
    with harness.store.read_uow() as ro2:  # control: a new UoW sees the new commit
        assert ro2.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3


class Crash(RuntimeError):
    pass


def _snapshot(harness, path):
    with closing(sqlite3.connect(harness.db_path)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
    return path


def _setup(harness, tmp_path):
    seed(harness)
    append_n(harness, 2)
    clock = ManualClock()
    tok = harness.claims(clock).acquire("a", "s", "A", lease_sec=1000)
    snap = _snapshot(harness, tmp_path / "snap.db")  # snapshot holds the claim row stamped with generation G1
    append_n(harness, 2, prefix="post")  # live state moves on after the snapshot
    return clock, tok, snap


@pytest.mark.m1_id("SQL-010")
@pytest.mark.parametrize("kind", ["garbage", "future_version", "missing_tables", "corrupt_page"])
def test_sql_010_invalid_snapshot_rejected_before_touching_live_state(harness, tmp_path, kind):
    seed(harness)
    append_n(harness, 2)
    good = _snapshot(harness, tmp_path / "good.db")
    bad = tmp_path / "bad.db"
    if kind == "garbage":
        bad.write_bytes(b"not a database" * 100)
    elif kind == "future_version":
        bad.write_bytes(good.read_bytes())
        with closing(sqlite3.connect(bad)) as c:
            c.execute("PRAGMA user_version = 999")
            c.commit()
    elif kind == "missing_tables":
        with closing(sqlite3.connect(bad)) as c:
            c.execute("CREATE TABLE unrelated (x)")
            c.commit()
    else:
        data = bytearray(good.read_bytes())
        for off in range(4096, len(data), 4096):  # smash every page after the header page
            data[off:off + 64] = b"\xff" * 64
        bad.write_bytes(bytes(data))
    before, gen = harness.db_path.read_bytes(), harness.workspace_generation()
    digest = harness.state_digest()
    with pytest.raises(SnapshotInvalidError):
        harness.ws.restore_snapshot(bad)
    assert harness.db_path.read_bytes() == before and harness.workspace_generation() == gen
    assert harness.state_digest() == digest and not list(harness.workspace.glob("*.restore*"))
    assert harness.ws.restore_snapshot(good) != gen  # positive control: a valid snapshot restores


@pytest.mark.m1_id("SQL-010")
@pytest.mark.parametrize("point", ["restore.after_validate", "restore.after_temp_copy", "restore.after_generation",
                                   "restore.after_replace"])
def test_sql_010_restore_crash_points_never_lose_state_or_revive_old_tokens(harness, tmp_path, point):
    clock, tok, snap = _setup(harness, tmp_path)
    g1, live_digest = harness.workspace_generation(), harness.state_digest()
    fired = []

    def fault(p):
        fired.append(p)
        if p == point:
            raise Crash(p)

    with pytest.raises(Crash):
        harness.ws.restore_snapshot(snap, fault=fault)
    assert fired[-1] == point  # the injection really fired at this crash point
    harness.reopen()
    with closing(sqlite3.connect(harness.db_path)) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    g_now, recs = harness.workspace_generation(), harness.read_records("s")
    if point == "restore.after_replace":  # complete restored state under the new generation
        assert g_now != g1 and len(recs) == 2
    else:  # complete previous authoritative state, never a mix
        assert len(recs) == 4 and harness.state_digest() == live_digest
    if point in ("restore.after_generation", "restore.after_replace"):
        assert g_now != g1
        with pytest.raises(StaleClaimError):  # old/restored claims are not valid under the old generation
            harness.claims(clock).assert_current(tok)
    else:
        assert g_now == g1
        harness.claims(clock).assert_current(tok)  # untouched workspace: token still valid (control)
    g2 = harness.ws.restore_snapshot(snap)  # recovery: retry completes cleanly
    harness.reopen()
    assert g2 == harness.workspace_generation() != g1 and len(harness.read_records("s")) == 2
    assert not list(harness.workspace.glob("*.restore*"))


@pytest.mark.m1_id("SQL-010")
def test_sql_010_guarded_writes_rejected_after_generation_change(harness):
    seed(harness)
    append_n(harness, 2)
    claims = harness.claims(ManualClock())
    tok = claims.acquire("a", "s", "A", lease_sec=1000)
    assert harness.store.append_record(guard=claims.guard(tok), **req(body="ok", key="before")).position == 3  # control
    harness.ws.replace_generation()
    state = harness.state_digest()
    with pytest.raises(StaleClaimError):
        harness.store.append_record(guard=claims.guard(tok), **req(body="x", key="after"))
    with pytest.raises(StaleClaimError):
        harness.store.advance_offset_cas("a", "s", 1, 1, guard=claims.guard(tok))
    with pytest.raises(StaleClaimError):
        claims.finalize_terminal(tok, {"ok": True})
    assert harness.state_digest() == state


@pytest.mark.m1_id("SQL-010")
def test_sql_010_restore_aborts_without_changes_when_live_db_is_busy(harness, tmp_path):
    seed(harness)
    append_n(harness, 2)
    snap = _snapshot(harness, tmp_path / "snap.db")
    append_n(harness, 1, prefix="more")
    gen, digest = harness.workspace_generation(), harness.state_digest()
    reader = sqlite3.connect(harness.db_path, isolation_level=None)  # holds a read mark: TRUNCATE checkpoint cannot finish
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM records").fetchone()
        append_n(harness, 1, prefix="wal")  # WAL now has frames past the reader's mark
        digest = harness.state_digest()
        with pytest.raises(RuntimeError, match="busy"):
            harness.ws.restore_snapshot(snap)
        assert harness.workspace_generation() == gen and harness.state_digest() == digest
        assert not list(harness.workspace.glob("*.restore*"))
    finally:
        reader.close()
    assert harness.ws.restore_snapshot(snap) != gen  # control: succeeds once the reader is gone
