"""SOAK-002: many Peers (same adapter kind) and Streams: no vendor-specific Core ceiling. Evidence-only capacity."""
import sqlite3
from contextlib import closing

import pytest

from tests.communication.soak import soak_support as ss

SCALES = [pytest.param("smoke", id="smoke"),
          pytest.param("scaled", id="scaled", marks=[pytest.mark.soak, pytest.mark.slow, pytest.mark.timeout(ss.SCALED_TIMEOUT_S)])]
S = ss.SCALES["smoke"]


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    work = tmp_path_factory.mktemp("soak2")
    return work, ss.run_cardinality(work, "smoke")


@pytest.mark.catalog_id("SOAK-002")
@pytest.mark.parametrize("which", SCALES)
def test_soak_002_cardinality_correct_with_no_hardcoded_ceiling(which, tmp_path, smoke_run):
    scale = "smoke" if which == "smoke" else ss.scaled_name()
    doc = smoke_run[1] if which == "smoke" else ss.run_cardinality(tmp_path / "ws", scale)
    cfg = ss.SCALES[scale]
    c, m = doc["correctness"], doc["measurements"]
    assert c["violations"] == []
    assert (c["peers"], c["streams"], c["peers_with_same_adapter_kind"]) == (cfg["peers"], cfg["streams"], cfg["peers"])
    assert c["offsets_persisted"] > 0 and c["diag_core_sections"] == {"peers": "OK", "streams": "OK"}
    assert c["diag_records_total"] == cfg["streams"] * cfg["stream_records"]
    cc = c["claim_contention"]  # exactly one winner per scope; every loser is an explicit ClaimHeldError, nothing else
    assert cc["violations"] == [] and cc["unexpected_errors"] == []
    assert cc["held_rejections"] == cc["expected_held_rejections"] and cc["scopes"] == cfg["claim_keys"]
    assert m["register_peer"]["count"] == cfg["peers"] and m["create_stream"]["count"] == cfg["streams"]
    assert doc["evidence_only"] is True and doc["capacity_thresholds"] is None
    if which != "smoke":
        ss.write_evidence(doc, "SOAK-002", scale)


@pytest.mark.catalog_id("SOAK-002")
def test_soak_002_peer_count_above_any_small_constant_is_accepted(tmp_path):
    """More Peers of ONE adapter kind than any plausible provider-specific cap (3 vendors, 10, 50) register without rejection."""
    cfg = {**S, "peers": 130, "streams": 4, "members": 5}
    doc = ss.run_cardinality(tmp_path / "ws", "smoke", cfg=cfg)
    assert doc["correctness"]["violations"] == [] and doc["correctness"]["peers_with_same_adapter_kind"] == 130
    with closing(sqlite3.connect(ss.Workspace(tmp_path / "ws").db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM peers WHERE adapter_ref='cc'").fetchone()[0] == 130


@pytest.mark.catalog_id("SOAK-002")
def test_soak_002_oracle_positive_control_and_corruptions(tmp_path, smoke_run):
    src = ss.Workspace(smoke_run[0]).db_path
    P, M, K, R = S["peers"], S["streams"], S["members"], S["stream_records"]
    with closing(sqlite3.connect(src)) as c:
        offsets = {(p, s): pos for p, s, pos in c.execute("SELECT peer_id, stream_id, read_through_position FROM offsets")}
    assert ss.oracle_cardinality(src, P, M, K, R, offsets) == []  # positive control

    def tampered(name, sql):
        dst = tmp_path / f"{name}.db"
        with closing(sqlite3.connect(src)) as a, closing(sqlite3.connect(dst)) as b:
            a.backup(b)
        with closing(sqlite3.connect(dst)) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                c.execute(f'DROP TRIGGER "{t}"')
            c.execute(sql)
            c.commit()
        return dst

    for name, sql, expect in [
        ("lost_peer", "DELETE FROM peers WHERE peer_id='peer-000059'", "peer count"),
        ("lost_record", "DELETE FROM records WHERE stream_id='stream-00003' AND position=2", "record count"),
        ("offset_head", "UPDATE offsets SET read_through_position=99 WHERE rowid=(SELECT MIN(rowid) FROM offsets)", "offset beyond head"),
    ]:
        viol = ss.oracle_cardinality(tampered(name, sql), P, M, K, R, offsets)
        assert any(expect in v for v in viol), (name, viol)
