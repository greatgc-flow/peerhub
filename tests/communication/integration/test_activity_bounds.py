import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

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


@pytest.mark.platform_sensitive
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


def test_emitted_cursor_is_always_a_positional_argument_value(tmp_path, capsys, monkeypatch):
    from peerhub.extensions.activity import cursor

    db = tmp_path / "db"
    deliver(db)
    # The checksum of this checkpoint starts with '-' in raw base64url, which argparse treats as an option.
    monkeypatch.setattr(cursor, "identity", lambda _: ("fixture-55", "epoch"))
    first = page(capsys, db, "export", "--stream", "z")
    assert not first["next_cursor"].startswith("-")
    assert not page(capsys, db, "export", "--stream", "z", "--cursor", first["next_cursor"])["items"]


@pytest.mark.parametrize("prefix", ("한" * 84, "\x01" * 253), ids=("unicode", "escaped"))
@pytest.mark.platform_sensitive
def test_wildcard_cursor_fits_portable_process_argv(tmp_path, prefix):
    db = tmp_path / "workspace" / "core.db"
    db.parent.mkdir()
    (db.parent / "workspace.generation").write_text("fixture", encoding="ascii")
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="p"))
    expected = set()
    for index in range(32):
        stream = f"{prefix}{index:03}"
        store.create_stream(Stream(stream_id=stream, members=["p"]))
        record = store.append_record(stream_id=stream, author_peer_id="p", kind="message",
                                     body="fixture", idempotency_key="k",
                                     created_at="2026-11-28T00:00:00Z")
        expected.add(record.record_id)

    def invoke(*extra):
        cmd = [sys.executable, "-m", "peerhub", "--db", str(db), "activity", "export", "--json", *extra]
        # Windows CreateProcess caps argv after flattening, including its terminating NUL.
        assert len(subprocess.list2cmdline(cmd).encode("utf-16-le")) // 2 + 1 <= 32767
        done = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[3], capture_output=True, timeout=45)
        assert done.returncode == 0, done.stdout + done.stderr
        return json.loads(done.stdout)

    first = invoke("--limit", "1")
    assert first["has_more"]
    drain = invoke("--cursor", first["next_cursor"])
    assert not drain["has_more"]
    assert {item["record_id"] for item in first["items"] + drain["items"]} == expected
    assert len(first["items"]) + len(drain["items"]) == len(expected)
    assert invoke("--cursor", drain["next_cursor"])["items"] == []


def test_compact_cursor_handles_max_sequence_numbers():
    from peerhub.extensions.activity import cursor

    prefix = "\x01" * 253
    names = [f"r:{prefix}{i:03}" for i in range(32)] + ["evidence", "outcome"]
    after = dict.fromkeys(names, cursor.MAX_SEQ - 1)
    heads = dict.fromkeys(names, cursor.MAX_SEQ)
    ids = ("a" * 64, "b" * 64, "c" * 64)
    token = cursor.encode(*ids, after, heads)
    assert len(token) <= 24576
    assert cursor.decode(token, *ids) == (after, heads)
    with pytest.raises(cursor.ActivityError, match="CURSOR_SCOPE_MISMATCH"):
        cursor.decode(token, ids[0], ids[1], "another-scope")
    with pytest.raises(cursor.ActivityError, match="RESNAPSHOT_REQUIRED"):
        cursor.decode(token, ids[0], "another-epoch", ids[2])
