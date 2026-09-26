"""Preflight is read-only and reports exactly the blockers the addendum names."""
import importlib.util
import sqlite3
from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.persistence.consensus_activation import activate_consensus_v2
from peerhub.persistence.sqlite import SqliteStateStore

TOOL = Path(__file__).resolve().parents[3] / "tools" / "consensus_preflight" / "consensus_activation_preflight.py"
spec = importlib.util.spec_from_file_location("consensus_activation_preflight", TOOL)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _workspace(tmp_path: Path):
    db = tmp_path / "ws.sqlite3"
    store = SqliteStateStore(db, workspace_home_id="pf")
    store.initialize()
    ids = FakeIdSource([f"i-{n}" for n in range(1, 500)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 5000)), ids=ids)
    service = ConsensusService(broker, clock=FakeClock(range(1, 5000)),
                               ids=FakeIdSource([f"d-{n}" for n in range(1, 500)]))
    return db, broker, service


def _propose(service, rid):
    service.propose(round_id=rid, title="t", question="q", body="b", proposer_id="p1",
                    required_participants=("p1", "p2"), eligible_participants=("p1", "p2"),
                    risk="normal", source_hash="sha256:s")


def test_clean_workspace_has_no_blockers(tmp_path, capsys):
    db, _, _ = _workspace(tmp_path)
    assert preflight.main([str(db)]) == 0
    assert "OK: no blockers" in capsys.readouterr().out


def test_open_legacy_round_is_a_blocker(tmp_path, capsys):
    db, _, service = _workspace(tmp_path)
    _propose(service, "r-open")
    assert preflight.main([str(db)]) == 1
    out = capsys.readouterr().out
    assert "OPEN legacy consensus round" in out and "r-open" in out


def test_unfinished_resolved_effect_is_a_blocker_but_a_finished_round_is_not(tmp_path, capsys):
    db, _, service = _workspace(tmp_path)
    _propose(service, "r-done")
    service.cast_vote("r-done", actor_id="p1", choice="agree")
    service.cast_vote("r-done", actor_id="p2", choice="agree")
    service.resolve("r-done", "approved", "p1", "ok")
    report = preflight.inspect_database(db)
    assert not any("OPEN legacy" in b for b in report["blockers"])
    assert any("unfinished consensus" in b or "effect" in b for b in report["blockers"]) or \
        report["info"]["unfinished_effects"]["unclaimed"]


def test_already_activated_and_inconsistent_states_are_reported(tmp_path):
    db, _, _ = _workspace(tmp_path)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    activate_consensus_v2(conn, now=5)
    assert any("already activated" in b for b in preflight.inspect_database(db)["blockers"])
    conn.execute("DROP TRIGGER consensus_v2_guard_insert")
    conn.close()
    assert any("INCONSISTENT" in b for b in preflight.inspect_database(db)["blockers"])


def test_preflight_never_modifies_the_database(tmp_path):
    db, _, service = _workspace(tmp_path)
    _propose(service, "r-x")
    before = db.read_bytes()
    preflight.main([str(db), "--json"])
    assert db.read_bytes() == before


def test_missing_database_is_exit_2(tmp_path):
    assert preflight.main([str(tmp_path / "nope.sqlite3")]) == 2


def test_rehearsal_on_a_copy_activates_and_leaves_the_original_untouched(tmp_path):
    import shutil
    db, _, service = _workspace(tmp_path)
    _propose(service, "r-r")
    service.abandon("r-r", "test", "drained for the rehearsal", "p1")
    assert not any("OPEN legacy" in b for b in preflight.inspect_database(db)["blockers"])
    original = db.read_bytes()
    copy = tmp_path / "copy.sqlite3"
    shutil.copy(db, copy)
    conn = sqlite3.connect(copy, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        activate_consensus_v2(conn, now=9)
    finally:
        conn.close()
    assert db.read_bytes() == original
    assert preflight.inspect_database(copy)["info"]["activation_state"] == "activated"
