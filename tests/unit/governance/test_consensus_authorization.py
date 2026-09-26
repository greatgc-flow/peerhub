"""TDD RED-state spec for consensus authorization (B2)."""

import pytest

from peerhub.governance.authorization import (
    AuthorizationGate,
    AuthorizationError,
)
from peerhub.governance.consensus import (
    ConsensusStateMachine,
    EvalContext,
    VoteEvent,
    TransitionResult,
    ConsensusEvent,
)


class FakeVerifier:
    def __init__(self, valid_pairs: set[tuple[str, str]]):
        self.valid_pairs = valid_pairs
        
    def __call__(self, *, credential_id: str, claimed_actor_id: str) -> bool:
        return (credential_id, claimed_actor_id) in self.valid_pairs

class FakeHealthPort:
    def __init__(self, healthy_actors: set[str], error_actors: set[str] | None = None):
        self.healthy_actors = healthy_actors
        self.error_actors = error_actors or set()
        
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        if actor_id in self.error_actors:
            raise RuntimeError("health service error")
        return actor_id in self.healthy_actors

class SpyStateMachine:
    def __init__(self):
        self.real_sm = ConsensusStateMachine(state="voting")
        self.calls = []
        
    def evaluate(self, ctx: EvalContext, event: ConsensusEvent) -> TransitionResult:
        self.calls.append((ctx, event))
        return self.real_sm.evaluate(ctx, event)


def make_ctx(actor_id: str, eligible: set[str], timestamp: int = 100) -> EvalContext:
    return EvalContext(
        current_timestamp=float(timestamp),
        caller_identity=actor_id,
        frozen_authority_set=frozenset(eligible)
    )

# --- Credential Matrix Tests ---

def test_credential_absent_optional():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    gate.authorize_and_evaluate(ctx, event, None, False)
    assert len(spy.calls) == 1

def test_credential_absent_required():
    spy = SpyStateMachine()
    gate = AuthorizationGate(FakeVerifier(set()), FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, None, True)
    assert len(spy.calls) == 0

def test_credential_absent_required_no_verifier():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, None, True)
    assert len(spy.calls) == 0

def test_credential_present_optional_valid():
    spy = SpyStateMachine()
    gate = AuthorizationGate(FakeVerifier({("cred1", "actor1")}), FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    gate.authorize_and_evaluate(ctx, event, "cred1", False)
    assert len(spy.calls) == 1

def test_credential_present_optional_invalid():
    spy = SpyStateMachine()
    gate = AuthorizationGate(FakeVerifier(set()), FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, "bad_cred", False)
    assert len(spy.calls) == 0

def test_credential_present_optional_no_verifier():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, "cred1", False)
    assert len(spy.calls) == 0

def test_credential_present_required_valid():
    spy = SpyStateMachine()
    gate = AuthorizationGate(FakeVerifier({("cred1", "actor1")}), FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    gate.authorize_and_evaluate(ctx, event, "cred1", True)
    assert len(spy.calls) == 1

def test_credential_present_required_invalid():
    spy = SpyStateMachine()
    gate = AuthorizationGate(FakeVerifier(set()), FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, "bad_cred", True)
    assert len(spy.calls) == 0

def test_credential_present_required_no_verifier():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, "cred1", True)
    assert len(spy.calls) == 0

# --- Eligibility Tests ---

def test_eligibility_not_in_frozen_set():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor2"}), spy)
    ctx = make_ctx("actor2", {"actor1", "actor3"})
    event = VoteEvent(actor="actor2", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, None, False)
    assert len(spy.calls) == 0

def test_eligibility_in_frozen_set():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor2"}), spy)
    ctx = make_ctx("actor2", {"actor1", "actor2"})
    event = VoteEvent(actor="actor2", choice="agree")
    gate.authorize_and_evaluate(ctx, event, None, False)
    assert len(spy.calls) == 1

# --- Health Gate Tests ---

def test_health_missing_or_unavailable():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort(set()), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError):
        gate.authorize_and_evaluate(ctx, event, None, False)
    assert len(spy.calls) == 0

def test_health_service_error_fails_closed():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}, error_actors={"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    with pytest.raises(AuthorizationError, match="health service error"):
        gate.authorize_and_evaluate(ctx, event, None, False)
    assert len(spy.calls) == 0

def test_final_call_revalidation_all_healthy():
    gate = AuthorizationGate(None, FakeHealthPort({"actor1", "actor2"}), SpyStateMachine())
    assert gate.authorize_final_call_electorate(["actor1", "actor2"], 100) is True

def test_final_call_revalidation_one_unhealthy():
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), SpyStateMachine())
    assert gate.authorize_final_call_electorate(["actor1", "actor2"], 100) is False

def test_final_call_revalidation_service_error_fails_closed():
    gate = AuthorizationGate(None, FakeHealthPort({"actor1", "actor2"}, error_actors={"actor2"}), SpyStateMachine())
    with pytest.raises(AuthorizationError, match="health service error"):
        gate.authorize_final_call_electorate(["actor1", "actor2"], 100)
        
def test_returns_state_machine_result():
    spy = SpyStateMachine()
    gate = AuthorizationGate(None, FakeHealthPort({"actor1"}), spy)
    ctx = make_ctx("actor1", {"actor1"})
    event = VoteEvent(actor="actor1", choice="agree")
    result = gate.authorize_and_evaluate(ctx, event, None, False)
    assert isinstance(result, TransitionResult)
    assert result.new_phase == "voting"
