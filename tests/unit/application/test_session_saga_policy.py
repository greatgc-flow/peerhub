"""TDD cases for the new policy-driven Session Rotation Saga (SS-* cases)."""
import pytest
from peerhub.dispatch.policy import SessionPolicy
from peerhub.application.session_saga import SessionRotationSaga, RotationDecision

# Using fake implementations mirroring the existing test file style
class FakeDispatchRepo:
    def __init__(self, succeed_claim: bool = True):
        self.succeed_claim = succeed_claim
        self.claim_calls = []

    def claim_rotation(self, **kwargs) -> bool:
        self.claim_calls.append(kwargs)
        return self.succeed_claim

class FakeTelemetryRepo:
    def __init__(self, projection=None):
        self.projection = projection
        self.calls = []

    def get_session_context_projection(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.projection

class FakeClock:
    def now(self):
        return 1000

class FakeIdSource:
    def new_id(self, prefix: str):
        return f"{prefix}-123"

class FakeProjection:
    def __init__(self, observed, window, source="exact_attribution", observed_at=1000):
        self.observed_tokens = observed
        self.window_tokens = window
        self.source = source
        self.observed_at = observed_at

def test_ss_01_first_ever_dispatch():
    """SS-01: First-ever dispatch -> fresh creates one; auto/reuse create one implicitly."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="fresh", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    # The actual implementation of evaluate_and_claim should be updated to accept `session_policy`
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope=None, # no session exists
        current_generation_id=0,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.ROTATION_CLAIMED

def test_ss_02_auto_between_soft_and_hard():
    """SS-02: auto + exact-attribution + soft <= p < hard -> CHECKPOINT_PENDING."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(80, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.CHECKPOINT_PENDING

def test_ss_03_auto_above_hard_and_safe():
    """SS-03: auto + exact-attribution + p >= hard + safe=true -> ROTATION_CLAIMED."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(95, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.ROTATION_CLAIMED

def test_ss_04_auto_above_hard_but_unsafe():
    """SS-04: auto + exact-attribution + p >= hard + safe=false -> ROTATION_PENDING_PROCEED."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(95, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=False
    )
    assert result.decision == RotationDecision.ROTATION_PENDING_PROCEED

def test_ss_05_auto_estimate_only():
    """SS-05: auto + estimate-only evidence -> PROCEED_WITH_REUSE."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(95, 100, source="estimate")), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.PROCEED_WITH_REUSE

def test_ss_06_auto_stale_evidence():
    """SS-06: auto + stale evidence -> PROCEED_WITH_REUSE."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(95, 100, observed_at=0)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=1)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.PROCEED_WITH_REUSE

def test_ss_07_fresh_regardless_of_evidence():
    """SS-07: fresh regardless of any evidence -> ROTATION_CLAIMED."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(10, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="fresh", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.ROTATION_CLAIMED

def test_ss_08_reuse_pressure_above_hard():
    """SS-08: reuse + pressure above hard -> CHECKPOINT_REQUIRED."""
    saga = SessionRotationSaga(FakeDispatchRepo(), FakeTelemetryRepo(FakeProjection(95, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="reuse", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.CHECKPOINT_REQUIRED

def test_ss_09_concurrent_rotation_claimed():
    """SS-09: Concurrent ROTATION_CLAIMED from two dispatches -> Second gets ROTATION_IN_PROGRESS_RETRY."""
    saga = SessionRotationSaga(FakeDispatchRepo(succeed_claim=False), FakeTelemetryRepo(FakeProjection(95, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    result = saga.evaluate_and_claim(
        session_policy=policy,
        workspace_scope_id="scope",
        instance_id="inst",
        profile_id="prof",
        conversation_scope="conv-1",
        current_generation_id=1,
        rotation_safe=True
    )
    assert result.decision == RotationDecision.ROTATION_IN_PROGRESS_RETRY

def test_ss_10_rotation_in_progress_retry_exhausted():
    """SS-10: ROTATION_IN_PROGRESS_RETRY + max retries exhausted -> Dispatch fails with clear error."""
    saga = SessionRotationSaga(FakeDispatchRepo(succeed_claim=False), FakeTelemetryRepo(FakeProjection(95, 100)), FakeClock(), FakeIdSource())
    policy = SessionPolicy(default_mode="auto", soft_pressure_threshold=75, hard_pressure_threshold=90, max_observation_age_ms=30000)
    
    with pytest.raises(Exception, match="max retries exhausted"):
        saga.evaluate_and_claim(
            session_policy=policy,
            workspace_scope_id="scope",
            instance_id="inst",
            profile_id="prof",
            conversation_scope="conv-1",
            current_generation_id=1,
            rotation_safe=True,
            retry_count=3,
            max_retries=3
        )
