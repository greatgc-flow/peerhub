"""SOAK-001: large Stream keeps ordering/idempotency across restarts. Evidence-only capacity; hard correctness oracles."""
import json
import sqlite3
from contextlib import closing

import pytest

from tests.m1.soak import soak_support as ss

AUTH = [f"author-{i}" for i in range(ss.SCALES["smoke"]["authors"])]
N = ss.SCALES["smoke"]["records"] + 1  # run_large_stream appends one more Record under the Diag snapshot

SCALES = [pytest.param("smoke", id="smoke"),
          pytest.param("scaled", id="scaled", marks=[pytest.mark.soak, pytest.mark.slow, pytest.mark.timeout(ss.SCALED_TIMEOUT_S)])]


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    work = tmp_path_factory.mktemp("soak1")
    return work, ss.run_large_stream(work, "smoke")


def tampered(work, dst, sql: str, params=()):
    """Consistent copy of the smoke DB (backup API) with the immutability triggers dropped, then one raw tamper."""
    src = ss.Workspace(work).db_path
    with closing(sqlite3.connect(src)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)
    with closing(sqlite3.connect(dst)) as c:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.execute(sql, params)
        c.commit()
    return dst


@pytest.mark.m1_id("SOAK-001")
@pytest.mark.parametrize("which", SCALES)
def test_soak_001_large_stream_correct_across_restarts(which, tmp_path, smoke_run):
    scale = "smoke" if which == "smoke" else ss.scaled_name()
    doc = smoke_run[1] if which == "smoke" else ss.run_large_stream(tmp_path / "ws", scale)
    cfg = ss.SCALES[scale]
    c, m = doc["correctness"], doc["measurements"]
    assert c["violations"] == []
    assert c["records_expected"] == cfg["records"] + 1 and c["records_scanned_before_diag_write"] == cfg["records"]
    assert c["retries_same_payload_returned_same_record"] > 0  # mixed retries really happened (live and after restarts)
    assert c["retries_conflicting_payload_rejected"] == cfg["restarts"]
    assert c["offset_beyond_head_rejected"] is True
    d = c["diag"]
    assert d["writer_errors"] == [] and d["core_sections"] == {"peers": "OK", "streams": "OK"} and d["writer_committed"] is True and d["snapshot_is_pre_write"] is True
    assert m["append"]["count"] == cfg["records"] and m["restart_open"]["count"] == cfg["restarts"]
    assert m["sizes_end"]["db_bytes"] > 0
    assert doc["evidence_only"] is True and doc["capacity_thresholds"] is None  # no pass/fail threshold on capacity numbers
    if which != "smoke":
        ss.write_evidence(doc, "SOAK-001", scale)


@pytest.mark.m1_id("SOAK-001")
def test_soak_001_evidence_schema_is_deterministic_and_machine_readable(tmp_path, smoke_run):
    again = ss.run_large_stream(tmp_path / "ws2", "smoke")
    first = smoke_run[1]

    def shape(x):
        return {k: shape(v) for k, v in sorted(x.items())} if isinstance(x, dict) else type(x).__name__

    assert shape(again) == shape(first)
    assert again["correctness"] == first["correctness"]  # same seed -> identical correctness section (timings differ)
    assert first["schema"] == "peerhub.m1.soak.evidence/1" and first["seed"] == ss.SEED
    assert json.loads(json.dumps(first, sort_keys=True)) == first  # lossless JSON round trip


@pytest.mark.m1_id("SOAK-001")
def test_soak_001_oracle_has_a_positive_control_and_kills_each_corruption(tmp_path, smoke_run):
    work = smoke_run[0]
    assert ss.oracle_stream(ss.Workspace(work).db_path, "big", N, AUTH) == []  # positive control: the intact DB is accepted
    mid = N // 2
    dup = ("INSERT INTO records (record_id,stream_id,position,author_peer_id,kind,body_json,targets_json,reply_to,refs_json,"
           "metadata_json,idempotency_key,payload_digest,created_at,appended_at) "
           "SELECT 'dup',stream_id,position+100000,author_peer_id,kind,body_json,targets_json,reply_to,refs_json,"
           "metadata_json,'dupkey',payload_digest,created_at,appended_at FROM records WHERE position=?")
    cases = {
        "skipped": ("DELETE FROM records WHERE stream_id='big' AND position=?", (mid,), "record count"),
        "duplicated": (dup, (mid,), "record count"),
        "nonmonotonic": ("UPDATE records SET position=? WHERE stream_id='big' AND position=?", (N + 50, mid), "strictly increasing"),
        "content": ("UPDATE records SET body_json='{\"n\":-1}' WHERE stream_id='big' AND position=?", (mid,), "chain differs"),
        "offset": ("INSERT OR REPLACE INTO offsets (peer_id,stream_id,read_through_position,revision) VALUES (?, 'big', ?, 9)",
                   (AUTH[0], N + 5), "beyond head"),
    }
    for name, (sql, params, expect) in cases.items():
        db = tampered(work, tmp_path / f"{name}.db", sql, params)
        viol = ss.oracle_stream(db, "big", N, AUTH)
        assert any(expect in v for v in viol), (name, viol)


@pytest.mark.m1_id("SOAK-001")
def test_soak_001_opt_in_reason_and_scale_selection_are_machine_readable(monkeypatch):
    monkeypatch.delenv(ss.OPT_IN_ENV, raising=False)
    assert ss.opt_in_reason() == ("SOAK-OPT-IN[env=PEERHUB_M1_SOAK;required=1;marker=soak]: scaled soak runs are disabled "
                                  "unless PEERHUB_M1_SOAK=1 and -m soak")
    monkeypatch.setenv(ss.OPT_IN_ENV, "1")
    assert ss.opt_in_reason() is None  # positive control
    monkeypatch.setenv(ss.SCALE_ENV, "full")
    assert ss.scaled_name() == "full"
    monkeypatch.setenv(ss.SCALE_ENV, "huge")
    with pytest.raises(ValueError, match="unknown PEERHUB_M1_SOAK_SCALE='huge'"):
        ss.scaled_name()
