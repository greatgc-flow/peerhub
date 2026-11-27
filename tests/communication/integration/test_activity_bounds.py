import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from tests.communication.integration.test_activity_export import deliver, page


def test_large_body_legacy_store_and_unknown_response_metadata(tmp_path, capsys):
    db = tmp_path / "db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="p"))
    store.create_stream(Stream(stream_id="legacy", members=["p"]))
    store.append_record(stream_id="legacy", author_peer_id="p", kind="response", body="한" * 50000,
                        idempotency_key="local", metadata={"delivery_id": "PRIVATE-FAKE"}, created_at="2000-01-01T00:00:00Z")
    result = page(capsys, db, "export", "--max-bytes", "4096")
    assert result["items"][0]["delivery_id"] is None
    assert result["channels"]["delivery_evidence"] == "UNAVAILABLE"
    assert not result["next_cursor"] and result["coverage"]["incremental"] == "NO_LINEAGE"


def test_byte_limited_retry_covers_same_items(tmp_path, capsys):
    db = tmp_path / "db"
    deliver(db)
    whole = page(capsys, db, "export")["items"]
    p = page(capsys, db, "export", "--max-bytes", "1860")
    seen = []
    while True:
        assert len(json.dumps(p, ensure_ascii=True, separators=(",", ":")).encode()) + 2 <= 1860
        seen.extend(p["items"])
        if not p["has_more"]:
            break
        p = page(capsys, db, "export", "--cursor", p["next_cursor"], "--max-bytes", "1860")
    assert seen == whole
    assert page(capsys, db, "export", "--max-bytes", "512", code=2)["error"] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize("token", ["!!", "a" * 70000, "e30="])
def test_invalid_cursor_does_not_return_checkpoint(tmp_path, capsys, token):
    db = tmp_path / "db"
    deliver(db)
    failed = page(capsys, db, "export", "--cursor", token, code=2)
    assert failed["error"] == "INVALID_CURSOR" and "next_cursor" not in failed


@pytest.mark.parametrize("version", [0, 5])
def test_schema_mismatch_is_read_only(tmp_path, capsys, version):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        assert page(capsys, db, "export", code=6)["error"] == "UNSUPPORTED_SCHEMA"
        assert conn.execute("PRAGMA user_version").fetchone() == (version,)


def test_drain_frozen_partition_inventory_before_switching_to_scoped_cursors(tmp_path, capsys):
    db = tmp_path / "db"
    deliver(db)
    first = page(capsys, db, "export", "--limit", "1")
    store = CoreStore(db)
    with closing(sqlite3.connect(db)) as conn:
        member = conn.execute("SELECT peer_id FROM peers LIMIT 1").fetchone()[0]
    for i in range(32):
        store.create_stream(Stream(stream_id=f"x{i}", members=[member]))
    rest = page(capsys, db, "export", "--cursor", first["next_cursor"])
    assert rest["items"] and not rest["has_more"]
    assert page(capsys, db, "export", "--cursor", rest["next_cursor"], code=2)["error"] == "PARTITION_LIMIT"
    assert page(capsys, db, "export", "--stream", "z")["items"]


def test_lineage_marker_cannot_read_linked_files(tmp_path, capsys):
    db = tmp_path / "db"
    deliver(db)
    secret = tmp_path / "private.txt"
    secret.write_text("PRIVATE-LINEAGE")
    marker = tmp_path / "workspace.generation"
    marker.unlink()
    try:
        marker.symlink_to(secret)
    except OSError:
        pytest.skip("symbolic link creation unavailable")
    page(capsys, db, "export", code=5)


def test_malformed_store_stops_without_repair(tmp_path, capsys):
    db = tmp_path / "bad"
    db.write_bytes(b"PRIVATE-NOT-A-DB")
    assert page(capsys, db, "export", code=5)["error"] == "SOURCE_UNAVAILABLE"
    assert db.read_bytes() == b"PRIVATE-NOT-A-DB"


def test_ten_thousand_records_and_evidence_have_bounded_pages_and_release_wal(tmp_path):
    from peerhub.extensions.activity.reader import export_page
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness

    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    with closing(sqlite3.connect(h.db_path)) as writer:
        with writer:
            writer.executemany("INSERT INTO records(record_id,stream_id,position,author_peer_id,kind,idempotency_key,"
                               "payload_digest,created_at,appended_at) VALUES (?,'s',?,'a','message',?,'synthetic',"
                               "'2026-11-28T00:00:00Z','2026-11-28T00:00:00Z')",
                               ((f"r{i}", i, f"k{i}") for i in range(1, 10001)))
            writer.executemany("INSERT INTO bridge_evidence(stream_id,peer_id,kind,detail) VALUES ('s','b','caller',?)",
                               (('{"path":"PRIVATE-SYNTHETIC"}',) for _ in range(10000)))
        events = set()
        count = 0
        token = None
        while True:
            result = export_page(h.db_path, token=token, limit=200)
            assert len(result["items"]) <= 200
            for item in result["items"]:
                count += 1
                events.add(item["source_event_id"])
            token = result["next_cursor"]
            if not result["has_more"]:
                break
        assert count == len(events) == 20000
        # No read transaction survives between polls, even with a consumer retaining its last page.
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
