"""Stateful tests for sqlite_dispatch CAS primitives."""
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, Bundle, rule, invariant, run_state_machine_as_test
from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict

from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.dispatch.contract import (
    SessionRotationState, 
    SessionRotationKey, 
    SessionRotationGenerationSnapshot
)

@dataclass(frozen=True)
class MockKey:
    workspace: str
    instance: str
    profile: str
    conversation_scope: str

@dataclass
class ShadowState:
    generation: int
    state: SessionRotationState
    claim_token: str | None
    claim_expiry: int | None

class Clock:
    def __init__(self):
        self.time = 0

class CASStateMachine(RuleBasedStateMachine):
    def __init__(self, store: SqliteStateStore, clock: Clock):
        super().__init__()
        self.store = store
        self.clock = clock
        self.shadows: dict[MockKey, ShadowState] = {}
        
    keys = Bundle("keys")
    
    @rule(
        target=keys,
        workspace=st.sampled_from(["ws-1"]),
        instance=st.sampled_from(["inst-1"]),
        profile=st.sampled_from(["prof-1"]),
        conversation_scope=st.sampled_from(["conv-A", "conv-B"])
    )
    def create_key(self, workspace, instance, profile, conversation_scope):
        key = MockKey(workspace, instance, profile, conversation_scope)
        if key not in self.shadows:
            self.shadows[key] = ShadowState(
                generation=1,
                state=SessionRotationState.ACTIVE,
                claim_token=None,
                claim_expiry=None,
            )
            db_key = SessionRotationKey(
                workspace_scope_id=workspace,
                instance_id=instance,
                profile_id=profile,
                conversation_scope=conversation_scope,
                generation_id=1,
            )
            with self.store.unit_of_work() as uow:
                try:
                    uow.dispatch.insert_rotation_generation(
                        SessionRotationGenerationSnapshot(
                            key=db_key,
                            conversation_id=conversation_scope,
                            state=SessionRotationState.ACTIVE,
                            claim_token=None,
                            claim_expiry=None,
                            created_at=0,
                            updated_at=0,
                        )
                    )
                except Exception:
                    pass
                uow.commit()
        return key

    @rule(time_advance=st.integers(min_value=1, max_value=1000))
    def advance_time(self, time_advance):
        self.clock.time += time_advance
        
    @rule(key=keys, token=st.uuids().map(str), expiry_offset=st.integers(min_value=1, max_value=1000))
    def claim(self, key: MockKey, token: str, expiry_offset: int):
        expiry = self.clock.time + expiry_offset
        shadow = self.shadows[key]
        
        with self.store.unit_of_work() as uow:
            success = uow.dispatch.claim_rotation(
                workspace_scope_id=key.workspace,
                instance_id=key.instance,
                profile_id=key.profile,
                conversation_scope=key.conversation_scope,
                expected_generation_id=shadow.generation,
                claim_token=token,
                claim_expiry=expiry,
                updated_at=self.clock.time,
            )
            uow.commit()
            
        if shadow.state == SessionRotationState.ACTIVE:
            assert success is True, f"Expected claim to succeed for ACTIVE generation {shadow.generation} of {key}"
            shadow.state = SessionRotationState.DRAINING
            shadow.claim_token = token
            shadow.claim_expiry = expiry
        else:
            assert success is False, "Expected claim to fail for non-ACTIVE generation"

    @rule(key=keys, token_offset=st.integers(min_value=0, max_value=1))
    def commit_claim(self, key: MockKey, token_offset: int):
        shadow = self.shadows[key]
        token = shadow.claim_token if token_offset == 0 and shadow.claim_token else "wrong-token"
        
        with self.store.unit_of_work() as uow:
            success = uow.dispatch.commit_rotation(
                workspace_scope_id=key.workspace,
                instance_id=key.instance,
                profile_id=key.profile,
                conversation_scope=key.conversation_scope,
                expected_generation_id=shadow.generation,
                claim_token=token,
                new_conversation_id=f"{key.conversation_scope}-gen{shadow.generation+1}",
                updated_at=self.clock.time,
            )
            uow.commit()
            
        if shadow.state == SessionRotationState.DRAINING and shadow.claim_token == token:
            assert success is True, "Expected commit to succeed for valid DRAINING claim"
            shadow.state = SessionRotationState.ACTIVE
            shadow.generation += 1
            shadow.claim_token = None
            shadow.claim_expiry = None
        else:
            assert success is False, "Expected commit to fail for invalid claim or state"

    @rule()
    def sweep(self):
        with self.store.unit_of_work() as uow:
            swept = uow.dispatch.sweep_expired_rotation_claims(current_time=self.clock.time)
            uow.commit()
            
        expected_sweep_count = 0
        for shadow in self.shadows.values():
            if shadow.state == SessionRotationState.DRAINING and shadow.claim_expiry is not None and shadow.claim_expiry <= self.clock.time:
                expected_sweep_count += 1
                shadow.state = SessionRotationState.ACTIVE
                shadow.claim_token = None
                shadow.claim_expiry = None
                
        assert swept == expected_sweep_count, f"Expected {expected_sweep_count} swept, got {swept}"

    @rule(key=keys)
    def stale_claim_attempt(self, key: MockKey):
        shadow = self.shadows[key]
        if shadow.generation > 1:
            stale_gen = shadow.generation - 1
            with self.store.unit_of_work() as uow:
                success = uow.dispatch.claim_rotation(
                    workspace_scope_id=key.workspace,
                    instance_id=key.instance,
                    profile_id=key.profile,
                    conversation_scope=key.conversation_scope,
                    expected_generation_id=stale_gen,
                    claim_token="stale",
                    claim_expiry=self.clock.time + 100,
                    updated_at=self.clock.time,
                )
                uow.commit()
            assert success is False, "Stale claim must fail"

    @invariant()
    def check_max_generation(self):
        for key, shadow in self.shadows.items():
            with self.store.unit_of_work() as uow:
                max_gen = uow.dispatch.get_max_rotation_generation(
                    key.workspace,
                    key.instance,
                    key.profile,
                    key.conversation_scope,
                )
            assert max_gen is not None
            assert max_gen.key.generation_id == shadow.generation, f"Generation mismatch for {key}"
            assert max_gen.state == shadow.state, f"State mismatch for {key}"
            if shadow.state == SessionRotationState.DRAINING:
                assert max_gen.claim_token == shadow.claim_token
                assert max_gen.claim_expiry == shadow.claim_expiry

def test_cas_state_machine():
    class Machine(CASStateMachine):
        def __init__(self):
            import tempfile
            self.temp_dir = tempfile.TemporaryDirectory()
            self.store_path = Path(self.temp_dir.name) / "test.db"
            store = SqliteStateStore(self.store_path, workspace_home_id="ws")
            store.initialize()
            super().__init__(store, Clock())
            
        def teardown(self):
            self.store.close()
            self.temp_dir.cleanup()
            
    run_state_machine_as_test(Machine)
