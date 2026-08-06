import pytest
from peerhub.application.session_saga import (
    SessionRotationSaga,
    RotationDecision,
)
from peerhub.telemetry.contract import SessionContextProjectionSnapshot
from peerhub.dispatch.contract import SessionBindingKey

class FakeClock:
    def now(self) -> int:
        return 1000

class FakeIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-123"

class FakeDispatchRepo:
    def __init__(self, succeed_claim: bool = True):
        self.succeed_claim = succeed_claim
        self.claim_calls = []
        self.commit_calls = []

    def claim_rotation(self, **kwargs) -> bool:
        self.claim_calls.append(kwargs)
        return self.succeed_claim

    def commit_rotation(self, **kwargs) -> bool:
        self.commit_calls.append(kwargs)
        return True

class FakeTelemetryRepo:
    def __init__(self, projection: SessionContextProjectionSnapshot | None = None):
        self.projection = projection
        self.calls = []

    def get_session_context_projection(self, *args, **kwargs) -> SessionContextProjectionSnapshot | None:
        self.calls.append((args, kwargs))
        return self.projection

def make_projection(observed: int, window: int, source: str = "exact_attribution", observed_at: int = 1000) -> SessionContextProjectionSnapshot:
    return SessionContextProjectionSnapshot(
        projection_id="proj-1",
        binding_key=SessionBindingKey("scope", "inst", "prof", "conv"),
        generation_id=1,
        observed_tokens=observed,
        window_tokens=window,
        source=source,
        observed_at=observed_at,
        revision=1,
        updated_at=1000,
    )

def test_reuse_at_hard_limit_does_not_rotate_returns_checkpoint_required():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=make_projection(100, 100))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="reuse",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
    )
    
    assert result.decision == RotationDecision.CHECKPOINT_REQUIRED
    assert len(dispatch.claim_calls) == 0

def test_auto_with_pressure_and_safe_signal_claims_rotation():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=make_projection(150, 100))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
    )
    
    assert result.decision == RotationDecision.ROTATION_CLAIMED
    assert result.claim_token == "claim-123"
    assert len(dispatch.claim_calls) == 1

def test_auto_with_pressure_no_safe_signal_does_not_rotate():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=make_projection(100, 100))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=False,
    )
    
    assert result.decision == RotationDecision.ROTATION_PENDING_PROCEED
    assert len(dispatch.claim_calls) == 0

def test_auto_with_stale_absent_evidence_fails_safe_does_not_rotate():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=None)
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
    )
    
    assert result.decision == RotationDecision.PROCEED_WITH_REUSE
    assert len(dispatch.claim_calls) == 0

def test_estimated_source_evidence_fails_safe_does_not_rotate():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=make_projection(150, 100, source="estimate"))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
    )
    
    assert result.decision == RotationDecision.PROCEED_WITH_REUSE
    assert len(dispatch.claim_calls) == 0

def test_stale_exact_attribution_evidence_fails_safe_does_not_rotate():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=make_projection(150, 100, source="exact_attribution", observed_at=500))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
        max_observation_age_ms=300, # Clock is at 1000, diff is 500 > 300
    )
    
    assert result.decision == RotationDecision.PROCEED_WITH_REUSE
    assert len(dispatch.claim_calls) == 0

def test_concurrent_claim_attempt_fails():
    dispatch = FakeDispatchRepo(succeed_claim=False)
    telemetry = FakeTelemetryRepo(projection=make_projection(100, 100))
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="auto",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=True,
    )
    
    assert result.decision == RotationDecision.ROTATION_IN_PROGRESS_RETRY
    assert result.claim_token is None
    assert len(dispatch.claim_calls) == 1

def test_fresh_always_claims_rotation():
    dispatch = FakeDispatchRepo()
    telemetry = FakeTelemetryRepo(projection=None)
    saga = SessionRotationSaga(dispatch, telemetry, FakeClock(), FakeIdSource())

    result = saga.evaluate_and_claim(
        policy="fresh",
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        current_generation_id=1,
        rotation_safe=False,
    )
    
    assert result.decision == RotationDecision.ROTATION_CLAIMED
    assert result.claim_token == "claim-123"
    assert len(dispatch.claim_calls) == 1
