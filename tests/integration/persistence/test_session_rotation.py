import pytest
from pathlib import Path
from collections.abc import Iterator
import sqlite3

from peerhub.dispatch.contract import (
    SessionRotationState,
    SessionRotationKey,
    SessionRotationGenerationSnapshot,
)
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.persistence.sqlite_dispatch import SqliteDispatchRepository


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "session-rotation.sqlite3",
        workspace_home_id="workspace-test",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()

@pytest.fixture
def repo(store: SqliteStateStore):
    conn = sqlite3.connect(store.database_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # autocommit to prevent database locked
    try:
        yield SqliteDispatchRepository(lambda: conn)
    finally:
        conn.close()

def test_session_rotation_cas_flow(repo):
    scope = "scope1"
    inst = "inst1"
    prof = "prof1"

    key = SessionRotationKey(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        generation_id=1,
    )

    snapshot = SessionRotationGenerationSnapshot(
        key=key,
        conversation_id="conv_a",
        state=SessionRotationState.ACTIVE,
        claim_token=None,
        claim_expiry=None,
        created_at=100,
        updated_at=100,
    )
    repo.insert_rotation_generation(snapshot)

    # 1. Claim fails if not ACTIVE (wrong generation ID)
    claimed = repo.claim_rotation(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        expected_generation_id=2, # wrong
        claim_token="token_1",
        claim_expiry=200,
        updated_at=110,
    )
    assert not claimed

    # 2. Claim succeeds for generation 1
    claimed = repo.claim_rotation(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        expected_generation_id=1,
        claim_token="token_1",
        claim_expiry=200,
        updated_at=110,
    )
    assert claimed

    # 3. Commit fails on wrong claim token
    committed = repo.commit_rotation(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        expected_generation_id=1,
        claim_token="token_WRONG",
        new_conversation_id="conv_b",
        updated_at=120,
    )
    assert not committed

    # 4. Commit succeeds
    committed = repo.commit_rotation(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        expected_generation_id=1,
        claim_token="token_1",
        new_conversation_id="conv_b",
        updated_at=120,
    )
    assert committed

    # Verify max generation is 2
    max_gen = repo.get_max_rotation_generation(scope, inst, prof, "conv_scope_a")
    assert max_gen.key.generation_id == 2
    assert max_gen.conversation_id == "conv_b"
    assert max_gen.state == SessionRotationState.ACTIVE

    
def test_session_rotation_sweep_expired(repo):
    scope = "scope1"
    inst = "inst1"
    prof = "prof1"

    key = SessionRotationKey(
        workspace_scope_id=scope,
        instance_id=inst,
        profile_id=prof,
        conversation_scope="conv_scope_a",
        generation_id=1,
    )

    snapshot = SessionRotationGenerationSnapshot(
        key=key,
        conversation_id="conv_a",
        state=SessionRotationState.DRAINING,
        claim_token="token_old",
        claim_expiry=150,
        created_at=100,
        updated_at=110,
    )
    repo.insert_rotation_generation(snapshot)
    
    # Doesn't sweep before expiry
    swept = repo.sweep_expired_rotation_claims(current_time=149)
    assert swept == 0
    
    # Sweeps on or after expiry
    swept = repo.sweep_expired_rotation_claims(current_time=150)
    assert swept == 1
    
    max_gen = repo.get_max_rotation_generation(scope, inst, prof, "conv_scope_a")
    assert max_gen.state == SessionRotationState.ACTIVE
    assert max_gen.claim_token is None
    assert max_gen.claim_expiry is None
