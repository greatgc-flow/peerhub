import json
import sqlite3
import pytest
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.persistence.sqlite_governance import SqliteGovernanceRepository
from peerhub.governance.contract import TargetState
from peerhub.persistence.consensus_activation import (
    activate_consensus_v2,
    read_activation_state,
    ActivationError,
)

@pytest.fixture
def store(tmp_path):
    store = SqliteStateStore(tmp_path / "workspace.db", workspace_home_id="test")
    store.initialize()
    yield store
    store.close()

@pytest.fixture
def fresh_conn(store):
    conn = sqlite3.connect(store.database_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()

def _target(target_id: str, revision: int, kind: str, scope: str = "default"):
    return TargetState(
        target_id=target_id,
        revision=revision,
        state={"kind": kind, "scope": scope, "data": "val"},
        updated_at=1000,
    )

def test_pre_activation_unchanged(fresh_conn, store):
    """MECE: pre-activation unchanged"""
    assert read_activation_state(fresh_conn) == "pre_activation"
    # Existing behavior test via repo
    uow = store.unit_of_work()
    with uow:
        repo = uow.governance
        repo.compare_and_set_target(None, _target("T1", 1, "consensus-round"))
        uow.commit()
    
    # Assert row is in governed_targets, not consensus_targets
    row = fresh_conn.execute("SELECT * FROM governed_targets WHERE target_id='T1'").fetchone()
    assert row is not None
    v2_row = fresh_conn.execute("SELECT * FROM consensus_targets WHERE target_id='T1'").fetchone()
    assert v2_row is None

def test_activation_success(fresh_conn):
    """MECE: activation success (triggers present, epoch +1, metadata)"""
    epoch_before = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    
    activate_consensus_v2(fresh_conn, now=2000)
    
    assert read_activation_state(fresh_conn) == "activated"
    
    # check epoch
    epoch_after = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    assert epoch_after == epoch_before + 1
    
    # check metadata
    meta = fresh_conn.execute("SELECT * FROM consensus_activation WHERE singleton=1").fetchone()
    assert meta["activated"] == 1
    assert meta["activated_at"] == 2000
    assert meta["activation_epoch"] == epoch_after
    
    # check triggers present
    triggers = fresh_conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    trigger_names = [t["name"] for t in triggers]
    assert "consensus_v2_guard_insert" in trigger_names
    assert "consensus_v2_guard_update" in trigger_names
    assert "consensus_v2_guard_delete" in trigger_names

def test_activation_refused_twice(fresh_conn):
    """MECE: activation refused twice"""
    activate_consensus_v2(fresh_conn, now=2000)
    with pytest.raises(ActivationError):
        activate_consensus_v2(fresh_conn, now=2001)

@pytest.mark.parametrize("step_name", [
    "before_triggers",
    "before_epoch",
    "before_metadata",
    "before_commit",
])
def test_fault_injected_at_every_step_boundary(fresh_conn, step_name):
    """MECE: fault injected at EVERY step boundary rolls back to pre_activation with zero residue"""
    def fault_hook(step: str):
        if step == step_name:
            raise RuntimeError(f"Fault at {step}")
            
    with pytest.raises(RuntimeError, match="Fault at"):
        activate_consensus_v2(fresh_conn, now=2000, fault_hook=fault_hook)
        
    assert read_activation_state(fresh_conn) == "pre_activation"
    # Ensure zero residue (no triggers, epoch unchanged, metadata unchanged)
    epoch = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    assert epoch == 1
    meta = fresh_conn.execute("SELECT * FROM consensus_activation WHERE singleton=1").fetchone()
    assert meta["activated"] == 0
    triggers = fresh_conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'consensus_v2_guard%'").fetchall()
    assert len(triggers) == 0

def test_raw_legacy_style_mutation_fails_after_activation(fresh_conn):
    """MECE: after activation a raw legacy-style INSERT/UPDATE/DELETE of a consensus-kind row fails"""
    activate_consensus_v2(fresh_conn, now=2000)
    
    # raw INSERT fails
    with pytest.raises(sqlite3.IntegrityError, match="ABORT"):
        fresh_conn.execute(
            "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
            "VALUES('T1', 1, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
        )
    
    # raw INSERT fails if column kind is '' but json is consensus-round
    with pytest.raises(sqlite3.IntegrityError, match="ABORT"):
        fresh_conn.execute(
            "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
            "VALUES('T2', 1, '{\"kind\": \"consensus-round\"}', 1000, '', 'scope')"
        )
        
    # Setup a non-consensus row to test UPDATE/DELETE
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T3', 1, '{\"kind\": \"room\"}', 1000, 'room', 'scope')"
    )
    
    # UPDATE that changes kind to consensus-round fails (this tests NEW.target_kind)
    with pytest.raises(sqlite3.IntegrityError, match="ABORT"):
        fresh_conn.execute(
            "UPDATE governed_targets SET target_kind='consensus-round' WHERE target_id='T3'"
        )

    # To test OLD.target_kind, we need an existing consensus-round row.
    # But wait, we can't insert one after activation! We must insert it before activation.
    fresh_conn.execute("UPDATE consensus_activation SET activated=0, activation_epoch=NULL, activated_at=NULL") # force revert for test setup
    fresh_conn.execute("UPDATE workspace_identity SET activation_epoch = activation_epoch - 1")
    fresh_conn.execute("DROP TRIGGER IF EXISTS consensus_v2_guard_insert")
    fresh_conn.execute("DROP TRIGGER IF EXISTS consensus_v2_guard_update")
    fresh_conn.execute("DROP TRIGGER IF EXISTS consensus_v2_guard_delete")
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T4', 1, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
    )
    activate_consensus_v2(fresh_conn, now=2000)
    
    # UPDATE that changes kind AWAY from consensus-round fails (this tests OLD.target_kind)
    with pytest.raises(sqlite3.IntegrityError, match="ABORT"):
        fresh_conn.execute(
            "UPDATE governed_targets SET target_kind='room' WHERE target_id='T4'"
        )
        
    # DELETE of a consensus-round row fails (this tests OLD.target_kind)
    with pytest.raises(sqlite3.IntegrityError, match="ABORT"):
        fresh_conn.execute(
            "DELETE FROM governed_targets WHERE target_id='T4'"
        )

def test_non_consensus_kinds_unaffected(fresh_conn):
    """MECE: non-consensus kinds (room, task) still insert/update/list normally after activation"""
    activate_consensus_v2(fresh_conn, now=2000)
    
    # Insert normal
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T_ROOM', 1, '{\"kind\": \"room\"}', 1000, 'room', 'scope')"
    )
    # Update normal
    fresh_conn.execute(
        "UPDATE governed_targets SET revision=2 WHERE target_id='T_ROOM'"
    )
    # Delete normal
    fresh_conn.execute(
        "DELETE FROM governed_targets WHERE target_id='T_ROOM'"
    )

def test_v2_get_list_cas(fresh_conn, store):
    """MECE: V2 get/list/CAS"""
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    
    uow = store.unit_of_work()
    with uow:
        repo = uow.governance
        # CAS Insert
        inserted = repo.compare_and_set_target(None, _target("T_V2", 1, "consensus-round"))
        assert inserted is True
        
        # Get
        t = repo.get_target("T_V2")
        assert t is not None
        assert t.revision == 1
        
        # CAS Update
        updated = repo.compare_and_set_target(t, _target("T_V2", 2, "consensus-round"))
        assert updated is True
        
        # List
        targets = repo.list_targets("consensus-round")
        assert len(targets) == 1
        assert targets[0].revision == 2
        uow.commit()
        
    # Assert row is in consensus_targets NOT governed_targets
    assert fresh_conn.execute("SELECT * FROM governed_targets WHERE target_id='T_V2'").fetchone() is None
    assert fresh_conn.execute("SELECT * FROM consensus_targets WHERE target_id='T_V2'").fetchone() is not None

def test_legacy_fallback_read(fresh_conn, store):
    """MECE: legacy fallback read"""
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T_LEGACY', 5, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
    )
    
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    
    uow = store.read_unit_of_work()
    with uow:
        t = uow.governance.get_target("T_LEGACY")
        assert t is not None
        assert t.revision == 5

def test_dedup_listing(fresh_conn, store):
    """MECE: dedup listing"""
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T_DUP', 5, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
    )
    
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    
    uow = store.unit_of_work()
    with uow:
        repo = uow.governance
        # V2 row shadows legacy row
        t = repo.get_target("T_DUP")
        repo.compare_and_set_target(t, _target("T_DUP", 6, "consensus-round"))
        
        # list_targets returns deduped
        targets = repo.list_targets("consensus-round")
        assert len(targets) == 1
        assert targets[0].revision == 6
        uow.commit()

def test_promotion(fresh_conn, store):
    """MECE: promotion at original revision+1 and second promoter loses"""
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T_PROMOTE', 5, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
    )
    
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    
    uow1 = store.unit_of_work()
    with uow1:
        repo1 = uow1.governance
        t1 = repo1.get_target("T_PROMOTE")
        assert t1.revision == 5
        
        # Promoter 1 wins
        promoted = repo1.compare_and_set_target(t1, _target("T_PROMOTE", 6, "consensus-round"))
        assert promoted is True
        uow1.commit()

    uow2 = store.unit_of_work()
    with uow2:
        repo2 = uow2.governance
        # Promoter 2 tries to promote from 5 to 6
        # t1 still represents the state at revision 5
        lost = repo2.compare_and_set_target(t1, _target("T_PROMOTE", 6, "consensus-round", scope="diff"))
        assert lost is False
        
    # Verify legacy untouched, V2 inserted
    legacy = fresh_conn.execute("SELECT revision FROM governed_targets WHERE target_id='T_PROMOTE'").fetchone()
    assert legacy["revision"] == 5
    v2 = fresh_conn.execute("SELECT revision FROM consensus_targets WHERE target_id='T_PROMOTE'").fetchone()
    assert v2["revision"] == 6

def test_v2_id_collision_refused(fresh_conn, store):
    """MECE: V2-id collision with legacy id refused"""
    fresh_conn.execute(
        "INSERT INTO governed_targets(target_id, revision, state_json, updated_at, target_kind, target_scope) "
        "VALUES('T_COLLIDE', 5, '{\"kind\": \"consensus-round\"}', 1000, 'consensus-round', 'scope')"
    )
    
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    
    uow = store.unit_of_work()
    with uow:
        repo = uow.governance
        # insert when current is None (target_id must not exist in legacy either -> return False on collision)
        inserted = repo.compare_and_set_target(None, _target("T_COLLIDE", 1, "consensus-round"))
        assert inserted is False
        uow.commit()

def test_workspace_epoch_asserted(fresh_conn, store):
    """MECE: workspace epoch value asserted"""
    epoch_before = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    activate_consensus_v2(fresh_conn, now=2000)
    store._generation = None
    with store.read_unit_of_work():
        pass
    epoch_after = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    
    # Check that repo.compare_and_set_target uses the correct epoch? Or just the epoch is correct?
    assert epoch_after == epoch_before + 1
    # Check property
    assert store._generation[1] == epoch_after


def test_activation_refused_on_inconsistent_partial_state_with_zero_mutation(fresh_conn):
    fresh_conn.execute(
        "CREATE TRIGGER consensus_v2_guard_insert BEFORE INSERT ON governed_targets "
        "BEGIN SELECT RAISE(ABORT, 'ABORT'); END"
    )
    assert read_activation_state(fresh_conn) == "inconsistent"
    epoch_before = fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0]
    with pytest.raises(ActivationError):
        activate_consensus_v2(fresh_conn, now=2000)
    assert fresh_conn.execute("SELECT activation_epoch FROM workspace_identity").fetchone()[0] == epoch_before
    assert fresh_conn.execute("SELECT activated FROM consensus_activation").fetchone()[0] == 0
    assert not fresh_conn.in_transaction
