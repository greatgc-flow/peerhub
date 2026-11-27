"""Black-box producer checks against a disposable, single-enrollment receiver."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from peerhub.extensions.backup import SecretLeakageDetectedError, create_backup, restore_authoritative
from peerhub.extensions.profile_stats import mark_outcome
from tests.communication.harness.crash_workers import CRASH_EXIT, spawn_exitcode
from tests.communication.integration.test_activity_export import deliver

pytestmark = pytest.mark.platform_sensitive


def export(db, token=None, *, limit=100, code=0):
    args = [sys.executable, "-m", "peerhub", "--db", str(db), "activity", "export", "--stream", "z",
            "--limit", str(limit), "--json"]
    if token is not None:
        args.extend(["--cursor", token])
    result = subprocess.run(args, capture_output=True, timeout=45)
    assert result.returncode == code, result.stderr
    assert b"PRIVATE-" not in result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "receiver.db"
    # Each test has one logical source and one enrolled stream, including across a confirmed restore.
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE event (id TEXT, rev TEXT, digest TEXT NOT NULL, lineage TEXT NOT NULL, PRIMARY KEY(id, rev));
            CREATE TABLE conflict (id TEXT, rev TEXT, digest TEXT, lineage TEXT, PRIMARY KEY(id, rev, digest, lineage));
            CREATE TABLE checkpoint (slot INTEGER PRIMARY KEY CHECK(slot=1), token TEXT NOT NULL);
        """)
    return path


def apply_page(path, page, crash_at=None):
    """Test-only receiver transaction. Only the normal exporter supplies pages."""
    assert (page["schema_version"], page["source"]) == ("1.0", "peerhub")
    with closing(sqlite3.connect(path)) as conn, conn:
        for event in page["items"]:
            assert event["stream_id"] == "z"
            facts = {k: v for k, v in event.items() if k != "source_digest"}
            raw = json.dumps(facts, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            assert digest == event["source_digest"]
            key = (event["source_event_id"], event["source_revision"])
            prior = conn.execute("SELECT digest FROM event WHERE id=? AND rev=?", key).fetchone()
            values = (*key, digest, page["lineage"])
            if prior is None:
                conn.execute("INSERT INTO event VALUES (?, ?, ?, ?)", values)
            elif prior != (digest,):
                conn.execute("INSERT OR IGNORE INTO conflict VALUES (?, ?, ?, ?)", values)
        conn.execute("INSERT OR REPLACE INTO checkpoint VALUES (1, ?)", (page["next_cursor"],))
        if crash_at == "before_commit":
            os._exit(CRASH_EXIT)
    if crash_at == "after_commit":
        os._exit(CRASH_EXIT)


def checkpoint(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT token FROM checkpoint WHERE slot=1").fetchone()[0]


def test_process_death_before_and_after_page_commit(tmp_path, ledger):
    db = tmp_path / "work" / "core.db"
    rid = deliver(db)
    first = export(db)
    apply_page(ledger, first)
    committed = checkpoint(ledger)

    mark_outcome(db, rid, "accepted")
    interrupted = export(db, committed)
    assert [item["outcome"] for item in interrupted["items"]] == ["accepted"]
    assert spawn_exitcode(apply_page, (ledger, interrupted, "before_commit")) == CRASH_EXIT
    assert checkpoint(ledger) == committed
    with closing(sqlite3.connect(ledger)) as conn:
        assert conn.execute("SELECT count(*) FROM event").fetchone() == (len(first["items"]),)

    mark_outcome(db, rid, "retried")
    recovered = export(db, checkpoint(ledger))
    assert recovered["items"][:1] == interrupted["items"]
    assert [item["outcome"] for item in recovered["items"]] == ["accepted", "retried"]
    assert recovered["coverage"]["verification"] == "UNKNOWN"
    assert spawn_exitcode(apply_page, (ledger, recovered, "after_commit")) == CRASH_EXIT
    assert checkpoint(ledger) == recovered["next_cursor"]
    assert export(db, checkpoint(ledger))["items"] == []
    with closing(sqlite3.connect(ledger)) as conn:
        assert conn.execute("SELECT count(*) FROM event").fetchone() == (len(first["items"]) + 2,)
        assert conn.execute("SELECT count(*) FROM conflict").fetchone() == (0,)


def tree_hashes(root):
    return {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_managed_restore_quarantines_reused_key_and_preserves_backup(tmp_path, ledger):
    source = tmp_path / "synthetic" / "core.db"
    rid = deliver(source)
    other = deliver(source, "other", "out-of-scope")
    sealed = tmp_path / "bundle"
    create_backup(source, source.parent / "artifacts", sealed)
    before = tree_hashes(sealed)
    work = tmp_path / "disposable"
    db = work / "core.db"
    generation = restore_authoritative(sealed, work)
    mark_outcome(db, rid, "accepted")

    first = export(db, limit=1)
    assert first["has_more"]
    apply_page(ledger, first)
    drain = export(db, checkpoint(ledger))
    assert not drain["has_more"]
    apply_page(ledger, drain)
    historical = first["items"] + drain["items"]
    assert all(item["record_id"] != other for item in historical)
    token = checkpoint(ledger)

    restore_authoritative(sealed, work, current_generation=generation)
    mark_outcome(db, rid, "rejected")
    failure = export(db, token, code=2)
    assert failure["error"] == "RESNAPSHOT_REQUIRED" and "next_cursor" not in failure
    assert checkpoint(ledger) == token
    restored = export(db)
    assert restored["lineage"] != first["lineage"]
    assert {(e["source_event_id"], e["source_revision"]) for e in restored["items"]} == {
        (e["source_event_id"], e["source_revision"]) for e in historical}
    old, = (item for item in historical if item["kind"] == "peer.outcome")
    new, = (item for item in restored["items"] if item["kind"] == "peer.outcome")
    assert old["source_event_id"] == new["source_event_id"] and old["source_revision"] == new["source_revision"]
    assert (old["outcome"], new["outcome"]) == ("accepted", "rejected")
    assert old["source_digest"] != new["source_digest"]
    apply_page(ledger, restored)
    apply_page(ledger, restored)
    with closing(sqlite3.connect(ledger)) as conn:
        assert conn.execute("SELECT count(*) FROM event").fetchone() == (len(historical),)
        assert conn.execute("SELECT digest, lineage FROM event WHERE id=? AND rev=?",
                            (old["source_event_id"], old["source_revision"])).fetchone() == (
                                old["source_digest"], first["lineage"])
        assert conn.execute("SELECT * FROM conflict").fetchall() == [
            (new["source_event_id"], new["source_revision"], new["source_digest"], restored["lineage"])]
    assert export(db, checkpoint(ledger))["items"] == []
    assert tree_hashes(sealed) == before


@pytest.mark.parametrize(("column", "value"), [
    ("owner_id", "sk-" + "a" * 32),
    ("owner_id", b"ask-aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"),
    ("owner_id", "ask-" + "a" * 32),  # Wrong UUID version and variant.
    ("owner_id", "ask-aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa" + "-extra"),
    ("workspace_generation", "ask-aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"),
])
def test_backup_rejects_token_like_values_outside_generated_owner_shape(tmp_path, column, value):
    db = tmp_path / "work" / "core.db"
    deliver(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(f"UPDATE bridge_claims SET {column}=?", (value,))
    bundle = tmp_path / "bundle"
    with pytest.raises(SecretLeakageDetectedError):
        create_backup(db, db.parent / "artifacts", bundle)
    assert not bundle.exists()
