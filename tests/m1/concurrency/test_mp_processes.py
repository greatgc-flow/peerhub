"""Wave 2: MP-001..004 with REAL OS processes (multiprocessing spawn), process barrier, bounded timeouts."""
import sqlite3

import pytest

from peerhub.m1.models import AppendRequest, compute_record_digest
from tests.m1.harness.mp import BARRIER, OUTQ, run_one, run_procs
from tests.m1.harness.mp_workers import (
    append_worker, cas_worker, claim_worker, same_request_worker, verify_worker,
)
from tests.m1.helpers import append_n, req, seed

pytestmark = [pytest.mark.concurrency, pytest.mark.multiprocess]
NPROC = 4


@pytest.mark.m1_id("MP-001")
def test_mp_001_multiprocess_concurrent_append_unique_positions(harness):
    seed(harness)
    per = 25
    results = run_procs([(append_worker, (str(harness.db_path), BARRIER, w, per, OUTQ)) for w in range(NPROC)])
    ok = [t for r in results for t in r["ok"]]
    errs = [e for r in results for e in r["err"]]
    assert all(e["type"] == "OperationalError" and "locked" in e["msg"] for e in errs), errs  # only explicit busy
    assert len(ok) + len(errs) == NPROC * per and len(ok) > 0
    positions = sorted(p for _, _, p in ok)
    assert len(set(positions)) == len(positions)
    # every process/connection is closed; a fresh verifier process reopens the store
    v = run_one(verify_worker, (str(harness.db_path), "s", OUTQ))
    assert v["integrity"] == "ok"
    assert [r[1] for r in v["records"]] == sorted(r[1] for r in v["records"]) == positions  # strictly ordered, same set
    assert {(r[2], r[0], r[1]) for r in v["records"]} == set(ok)  # same logical records as acked to children
    assert v["digest"] == harness.state_digest()


@pytest.mark.m1_id("MP-002")
def test_mp_002_multiprocess_identical_idempotency_key_one_record(harness):
    seed(harness)
    request = req(body={"x": 1}, key="shared")
    results = run_procs([(same_request_worker, (str(harness.db_path), BARRIER, request, OUTQ)) for _ in range(NPROC)])
    assert all("ok" in r for r in results), results
    assert len({r["ok"] for r in results}) == 1  # all success responses identify the same Record
    (recs,) = [harness.read_records("s", 0, None)]
    assert len(recs) == 1 and (recs[0].record_id, recs[0].position) == results[0]["ok"]
    assert recs[0].payload_digest == compute_record_digest(AppendRequest(**request).model_dump())


@pytest.mark.m1_id("MP-003")
def test_mp_003_multiprocess_offset_cas_single_winner(harness):
    seed(harness)
    append_n(harness, 10)
    off = harness.get_offset("b", "s")
    targets = [2, 4, 6, 8]
    results = run_procs([(cas_worker, (str(harness.db_path), BARRIER, "b", "s", off.revision, p, OUTQ)) for p in targets])
    winners = [r for r in results if "ok" in r]
    losers = [r for r in results if "err" in r]
    assert len(winners) == 1 and len(losers) == NPROC - 1
    assert all(r["err"]["type"] == "CasMismatchError" for r in losers), losers  # stale revision reported
    final = harness.get_offset("b", "s")
    assert (final.read_through_position, final.revision) == winners[0]["ok"] == (winners[0]["new_pos"], off.revision + 1)


@pytest.mark.m1_id("MP-004")
def test_mp_004_multiprocess_claim_race_elects_one_owner(harness):
    seed(harness)
    append_n(harness, 3)
    owners = [f"proc{i}" for i in range(NPROC)]
    results = run_procs([(claim_worker, (str(harness.db_path), str(harness.workspace), BARRIER, o, "a", "s", OUTQ)) for o in owners])
    winners = [r for r in results if "token_generation" in r]
    losers = [r for r in results if "acquire_err" in r]
    assert len(winners) == 1 and len(losers) == NPROC - 1, results
    assert all(r["acquire_err"]["type"] == "ClaimHeldError" for r in losers)
    w = winners[0]
    assert "ack" in w and "append" in w, w  # positive control: the elected owner can ack and append
    for r in losers:  # losers cannot append response / ack
        assert r["ack_err"]["type"] == "StaleClaimError" and r["append_err"]["type"] == "StaleClaimError", r
        assert "ack" not in r and "append" not in r
    with sqlite3.connect(harness.db_path) as c:
        rows = c.execute("SELECT owner_id, generation FROM bridge_claims").fetchall()
        assert rows == [(w["owner"], 1)]
        assert c.execute("SELECT COUNT(*) FROM records WHERE idempotency_key LIKE 'resp-%'").fetchone()[0] == 1
    assert harness.get_offset("a", "s").read_through_position == 1
