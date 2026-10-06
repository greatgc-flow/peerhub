"""M1 public CLI gate item 2/3: `diag quota` (read-only Observation evidence) and an EFFECTIVE `diag health --obs-db`.
Oracles are independent: rows are seeded with raw SQL (not through the Observation write path), expected values are literals."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.cli.app import main
from tests.communication.harness.core import CoreHarness
from tests.communication.harness.observation import ObservationHarness

pytestmark = [pytest.mark.integration, pytest.mark.diag]


def iso(offset_s):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))


def seed(db, pools=(), rows=()):
    """rows: (subject, kind, pool, state, age_seconds, payload, source) -> raw INSERT with captured_at = now - age."""
    with closing(sqlite3.connect(db)) as c:
        for pid, kind in pools:
            c.execute("INSERT INTO resource_pools (resource_pool_id, provider, kind, metadata_json) VALUES (?, 'prov', ?, '{}')", (pid, kind))
        for i, (subj, kind, pool, state, age, payload, source) in enumerate(rows):
            ts = iso(-age)
            c.execute("INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, captured_at, effective_at_us) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (f"o{i}-{subj}-{kind}-{age}", subj, pool, kind, source, state, json.dumps(payload), ts, ts, int((time.time() - age) * 1e6)))
        c.commit()


def run(capsys, db, *args):
    code = main(["--db", str(db), *args])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def fhash(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def ws(tmp_path):
    h = ObservationHarness(tmp_path / "wsA")
    seed(h.db_path, pools=[("P", "QUOTA"), ("Q", "QUOTA"), ("R", "RATE_LIMIT")], rows=[
        ("peer:a", "quota", "P", "MEASURED", 2000, {"remaining_fraction": 0.9}, "src-old"),  # superseded by the newer row below
        ("peer:a", "quota", "P", "MEASURED", 10, {"remaining_fraction": 0.25, "remaining_tokens": 1000, "plan": "pro"}, "src-new"),
        ("peer:b", "rate_limit", None, "MEASURED", 1000, {"requests_per_minute": 60}, "src-rl"),  # older than the 300s TTL -> STALE
        ("peer:c", "quota", "Q", "UNAVAILABLE", 5, {"condition": "probe_failed"}, "src-q"),
        ("peer:a", "reachability", None, "MEASURED", 5, {}, "src-r"),  # not a quota kind
    ])
    return h


def test_quota_json_exact_values_and_states(ws, capsys):
    before = (ws.full_state(), fhash(ws.db_path))
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json")
    rep = json.loads(out)
    assert code == 0 and rep["schema_version"] == "1.0" and rep["status"] == "OK" and rep["overall"] == "OK"
    assert set(rep) == {"schema_version", "status", "source_db", "read_at", "filters", "overall", "pools", "reset_credits", "error"}
    assert [g["resource_pool_ref"] for g in rep["pools"]] == ["P", "Q", "R", None]
    by = {(i["subject_ref"], i["kind"]): i for g in rep["pools"] for i in g["items"]}
    a = by[("peer:a", "quota")]
    assert (a["state"], a["source"], a["resource_pool_ref"]) == ("MEASURED", "src-new", "P")
    assert a["measurements"] == {"remaining_fraction": 0.25, "remaining_tokens": 1000}  # only MEASUREMENT_KEYS ("plan" dropped), latest row only
    assert a["derived"] == {"used_fraction": 0.75} and 9 <= a["age_seconds"] < 60
    b = by[("peer:b", "rate_limit")]
    assert b["state"] == "STALE" and b["measurements"] == {"requests_per_minute": 60} and b["derived"] == {} and b["resource_pool_ref"] is None
    c = by[("peer:c", "quota")]
    assert c["state"] == "UNAVAILABLE" and c["measurements"] == {}
    r = by[(None, "rate_limit")]  # registered pool without any evidence: explicit UNKNOWN, never unlimited
    assert r["state"] == "UNKNOWN" and r["resource_pool_ref"] == "R" and r["age_seconds"] is None and r["measurements"] == {}
    assert ("peer:a", "reachability") not in by
    assert (ws.full_state(), fhash(ws.db_path)) == before  # no write, no refresh


def test_quota_table_and_filters(ws, capsys):
    code, out, _ = run(capsys, ws.db_path, "diag", "quota")
    assert code == 0 and "STALE" in out and "UNKNOWN" in out and "used_fraction=0.75 (derived)" in out and "remaining_tokens=1000" in out
    assert "plan" not in out
    import re
    assert re.search(r"peer:a\s+quota\s+MEASURED\s+age=\d+s\s+src=src-new\s", out) and re.search(r"peer:b\s+rate_limit\s+STALE\s+age=100\ds\s+src=src-rl\s", out)
    assert re.search(r"peer:c\s+quota\s+UNAVAILABLE\s+age=\d+s\s+src=src-q\s+\(no measurement\)", out) and re.search(r"-\s+rate_limit\s+UNKNOWN\s+age=-\s+src=-\s", out)
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json", "--peer", "peer:b")
    rep = json.loads(out)
    assert [(i["subject_ref"], i["kind"]) for g in rep["pools"] for i in g["items"]] == [("peer:b", "rate_limit")]  # exact match, no placeholders
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json", "--pool", "P")
    assert [i["subject_ref"] for g in json.loads(out)["pools"] for i in g["items"]] == ["peer:a"]
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json", "--pool", "R")
    assert [(i["state"], i["resource_pool_ref"]) for g in json.loads(out)["pools"] for i in g["items"]] == [("UNKNOWN", "R")]
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json", "--peer", "peer")  # prefix is not a match
    rep = json.loads(out)
    assert code == 0 and rep["pools"] == [] and rep["overall"] == "UNKNOWN"
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--peer", "peer")
    assert "UNKNOWN" in out and "not 'unlimited'" in out


def test_quota_missing_database_is_unavailable_and_never_created(tmp_path, capsys):
    db = tmp_path / "absent.db"
    code, out, err = run(capsys, db, "diag", "quota", "--json")
    assert code == 5 and json.loads(out)["status"] == "UNAVAILABLE" and not db.exists() and list(tmp_path.iterdir()) == []
    code, out, err = run(capsys, db, "diag", "quota")
    assert code == 5 and "UNAVAILABLE" in err and "UNKNOWN" in out and not db.exists()
    assert "  note: database not found: " + str(db) in out and "DIAG QUOTA UNAVAILABLE: database not found" in err


def test_health_is_read_only_never_creates_or_migrates(tmp_path, capsys):
    db = tmp_path / "absent.db"
    code, out, err = run(capsys, db, "diag", "health", "--stream", "s")
    assert code == 5 and json.loads(out)["status"] == "UNAVAILABLE" and "UNAVAILABLE" in err and not db.exists() and list(tmp_path.iterdir()) == []
    old = tmp_path / "old.db"  # an existing database without any M1 schema: reported, not migrated
    with closing(sqlite3.connect(old)) as c:
        c.execute("CREATE TABLE legacy (x)")
        c.commit()
    before = fhash(old)
    code, out, _ = run(capsys, old, "diag", "health", "--stream", "s")
    assert code == 0 and json.loads(out)["status"] == "ERROR" and fhash(old) == before
    with closing(sqlite3.connect(old)) as c:
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master")] == ["legacy"] and c.execute("PRAGMA user_version").fetchone()[0] == 0
    fut = ObservationHarness(tmp_path / "fut").db_path
    with closing(sqlite3.connect(fut)) as c:
        c.execute("PRAGMA user_version = 999")
        c.commit()
    before = fhash(fut)
    code, out, err = run(capsys, fut, "diag", "health", "--stream", "s")
    assert code == 6 and "SCHEMA VERSION" in err and fhash(fut) == before


def test_quota_db_without_observation_tables_or_evidence(tmp_path, capsys):
    core = CoreHarness(tmp_path / "core")
    with closing(sqlite3.connect(core.db_path)) as c:
        c.execute("DROP TABLE IF EXISTS observations")
        c.commit()
    before = fhash(core.db_path)
    code, out, _ = run(capsys, core.db_path, "diag", "quota", "--json")
    rep = json.loads(out)
    assert code == 5 and rep["status"] == "UNAVAILABLE" and "no such table" in rep["error"] and fhash(core.db_path) == before
    empty = ObservationHarness(tmp_path / "empty")  # tables exist, no evidence: OK but explicitly UNKNOWN
    code, out, _ = run(capsys, empty.db_path, "diag", "quota", "--json")
    rep = json.loads(out)
    assert code == 0 and rep["overall"] == "UNKNOWN" and rep["pools"] == []


def test_quota_storage_fault_and_schema_version_exit_codes(tmp_path, capsys):
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"this is not a sqlite database" * 200)
    code, out, _ = run(capsys, bad, "diag", "quota", "--json")
    assert code == 4 and json.loads(out)["status"] == "FAILED" and bad.read_bytes().startswith(b"this is not")
    h = ObservationHarness(tmp_path / "fut")
    with closing(sqlite3.connect(h.db_path)) as c:
        c.execute("PRAGMA user_version = 999")
        c.commit()
    code, out, _ = run(capsys, h.db_path, "diag", "quota", "--json")
    assert code == 6 and json.loads(out)["status"] == "SCHEMA_VERSION"


def test_quota_obs_db_reads_other_workspace_and_never_creates(ws, tmp_path, capsys):
    other = ObservationHarness(tmp_path / "wsB")
    seed(other.db_path, rows=[("peer:z", "quota", None, "MEASURED", 3, {"remaining": 7}, "src-z")])
    missing = tmp_path / "nope.db"
    code, out, _ = run(capsys, missing, "diag", "quota", "--json", "--obs-db", str(other.db_path))  # --db itself is not needed
    rep = json.loads(out)
    assert code == 0 and rep["source_db"] == str(other.db_path) and not missing.exists()
    assert [(i["subject_ref"], i["measurements"]) for g in rep["pools"] for i in g["items"]] == [("peer:z", {"remaining": 7})]
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json")  # positive control: default reads --db, not the other one
    assert "peer:z" not in out and "peer:a" in out
    gone = tmp_path / "gone.db"
    code, out, _ = run(capsys, ws.db_path, "diag", "quota", "--json", "--obs-db", str(gone))
    assert code == 5 and not gone.exists()


def test_health_obs_db_is_effective(ws, tmp_path, capsys):
    ws.create_peer({"peer_id": "a"})
    ws.create_stream({"stream_id": "s", "members": ["a"]})
    other = ObservationHarness(tmp_path / "wsB")
    seed(other.db_path, rows=[("peer:x", "quota", None, "MEASURED", 3, {"used": 1}, "s1"), ("peer:y", "rate_limit", None, "UNAVAILABLE", 3, {"condition": "down"}, "s2"),
                              ("peer:z", "session", None, "MEASURED", 3, {}, "s3")])
    code, out, _ = run(capsys, ws.db_path, "diag", "health", "--stream", "s")
    own = json.loads(out)
    assert code == 0 and own["status"] == "OK" and own["stream_id"] == "s"
    assert own["observations"] == {"source_db": str(ws.db_path), "status": "OK", "latest_total": 4, "by_state": {"MEASURED": 2, "STALE": 1, "UNAVAILABLE": 1}, "error": None}
    code, out, _ = run(capsys, ws.db_path, "diag", "health", "--stream", "s", "--obs-db", str(other.db_path))
    rep = json.loads(out)
    assert rep["stream_id"] == "s" and rep["members"] == ["a"]  # stream part still from --db
    assert rep["observations"]["source_db"] == str(other.db_path) and rep["observations"]["by_state"] == {"MEASURED": 2, "UNAVAILABLE": 1} and rep["observations"]["latest_total"] == 3
    gone = tmp_path / "gone.db"
    code, out, _ = run(capsys, ws.db_path, "diag", "health", "--stream", "s", "--obs-db", str(gone))
    rep = json.loads(out)
    assert code == 0 and rep["status"] == "OK" and rep["observations"]["status"] == "UNAVAILABLE" and not gone.exists()


def test_observations_summary_honours_read_at(ws):
    from peerhub.extensions.diag_quota import observations_summary

    now = observations_summary(ws.db_path)
    assert now["by_state"] == {"MEASURED": 2, "STALE": 1, "UNAVAILABLE": 1}
    later = observations_summary(ws.db_path, read_at=time.time() + 100000)  # same evidence, read much later: freshness is evaluated at read_at
    assert later["by_state"] == {"STALE": 3, "UNAVAILABLE": 1} and later["latest_total"] == 4
