import pytest
from pathlib import Path
import sqlite3
import sys
import time

from peerhub.adapters.registry import ResolvedPeerTarget
from peerhub.application.bootstrap import build_direct_ask_admission_config as _build_direct_ask_admission_config

def build_direct_ask_admission_config(*args, **kwargs):
    from dataclasses import replace
    config = _build_direct_ask_admission_config(*args, **kwargs)
    return replace(
        config,
        readiness=replace(
            config.readiness,
            evidence=replace(
                config.readiness.evidence,
                provider_id="controlled-fake"
            )
        )
    )

from peerhub.application.direct_ask import execute_direct_ask, DirectAskRequest, DirectAskResult
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.context import Clock, IdSource, PathLayout
from peerhub.core.execution import TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import RequestState

class DummyClock:
    def now(self) -> int:
        return int(time.time() * 1000)

import uuid

class DummyIds:
    def request_id(self) -> str: return str(uuid.uuid4())
    def process_spawn_id(self) -> str: return str(uuid.uuid4())
    def session_id(self) -> str: return str(uuid.uuid4())
    def attempt_id(self) -> str: return str(uuid.uuid4())
    def route_decision_id(self) -> str: return str(uuid.uuid4())
    def delivery_receipt_id(self) -> str: return str(uuid.uuid4())
    def transition_receipt_id(self) -> str: return str(uuid.uuid4())
    
    def new_id(self, prefix: str) -> str:
        # Some consumers like outbox event strictly require an RFC4122 UUIDv4
        return str(uuid.uuid4())

@pytest.fixture
def clock() -> Clock:
    return DummyClock()

@pytest.fixture
def ids() -> IdSource:
    return DummyIds()


@pytest.mark.slow
def test_execute_direct_ask_real_agy(tmp_path: Path, clock: Clock, ids: IdSource) -> None:
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="ag",
        prompt="say hello in two words",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id="ag.standard",
        limits=TransportLimits(
            process_timeout_ms=60000,
            silence_timeout_ms=60000,
            max_output_bytes=1000000,
        )
    )
    
    result = execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject(
            "local-cli:test-user",
            "test",
        ),
    )
    
    assert result.error_code is None
    # Depending on what the adapter says, it'll likely be SUCCEEDED_VERIFIED
    assert result.request_state == RequestState.SUCCEEDED_VERIFIED
    assert result.response_text is not None
    assert len(result.response_text.strip()) > 0


def test_execute_direct_ask_unknown_peer(tmp_path: Path, clock: Clock, ids: IdSource) -> None:
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="unknown-peer-xyz",
        prompt="say hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=None,
        limits=TransportLimits(
            process_timeout_ms=60000,
            silence_timeout_ms=60000,
            max_output_bytes=1000000,
        )
    )
    
    # Let the exception propagate naturally (resolve_peer_target should raise ValueError)
    with pytest.raises(ValueError, match="unsupported cli_name"):
        execute_direct_ask(
            request,
            clock=clock,
            ids=ids,
            authenticated_subject=AuthenticatedSubject(
                "local-cli:test-user",
                "test",
            ),
        )


def test_direct_ask_binds_machine_subject_to_issued_lease(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakePeerAdapter(stdout="authenticated hello")
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )
    subject = AuthenticatedSubject(
        principal_id=r"local-cli:DOMAIN\alice",
        evidence_source="os-process-owner",
    )

    result = execute_direct_ask(
        DirectAskRequest(
            workspace_root=tmp_path,
            peer_name="fake",
            prompt="say hello",
            required_capability_tier=CapabilityTier.READ_ONLY,
            profile_id=profile.profile_id,
            limits=TransportLimits(
                process_timeout_ms=10_000,
                silence_timeout_ms=10_000,
                max_output_bytes=1_000_000,
            ),
        ),
        clock=clock,
        ids=ids,
        authenticated_subject=subject,
    )

    assert result.response_text == "authenticated hello"
    connection = sqlite3.connect(
        PathLayout.for_workspace(tmp_path).database_path
    )
    try:
        row = connection.execute(
            """
            SELECT
                dispatch_requests.authenticated_principal,
                capability_leases.subject_principal_id
            FROM dispatch_requests
            JOIN capability_leases USING (command_id)
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (subject.principal_id, subject.principal_id)
    assert row != ("cli-user", "cli-user")


class RecordingFakePeerAdapter(FakePeerAdapter):
    def __init__(
        self,
        *args,
        query_first: bool = False,
        supports_session: bool = False,
        max_inline_utf8_bytes: int | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.recorded_requests = []
        self._query_first = query_first
        self._max_inline_utf8_bytes = max_inline_utf8_bytes
        if supports_session:
            from dataclasses import replace
            from peerhub.adapters.contract import Capability
            self.descriptor = replace(
                self.descriptor,
                capabilities=self.descriptor.capabilities | {Capability.SESSION},
            )

    def prompt_policy(self, profile):
        from peerhub.adapters.contract import PromptPolicy
        base = super().prompt_policy(profile)
        return PromptPolicy(
            policy_id=base.policy_id,
            max_inline_utf8_bytes=(
                base.max_inline_utf8_bytes
                if self._max_inline_utf8_bytes is None
                else self._max_inline_utf8_bytes
            ),
            artifact_reference_supported=base.artifact_reference_supported,
            query_first=self._query_first,
        )

    def plan_invocation(self, request, profile, session, limits):
        self.recorded_requests.append(request)
        return super().plan_invocation(request, profile, session, limits)


def test_direct_ask_injects_directives_lessons_and_room_context(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.runtime import create_runtime

    adapter = RecordingFakePeerAdapter(stdout="hello with context")
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    # 1. Pre-seed workspace directives and room into runtime
    user_dir_path = tmp_path / "_sys" / "ai"
    user_dir_path.mkdir(parents=True, exist_ok=True)
    (user_dir_path / "user-directives.md").write_text("User rule: Always verify before mutate.", encoding="utf-8")

    # Initialize store to pre-populate directive, lesson, room
    layout = PathLayout.for_workspace(tmp_path)
    from peerhub.core.context import RuntimeContext
    pre_rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        # Seed runtime directive
        pre_rt.directive_service.migrate(
            directive_id="DIR-TEST-01",
            title="Test Directive",
            rule_markdown="Do not delete production files.",
            digest="sha256:" + "a" * 64,
            consumers=(),
            source_path="test",
        )
        # Seed room
        pre_rt.rooms_service.create_room(
            room_id="room-audit",
            topic_id="topic-1",
            title="Audit Room",
            creator_id="alice",
            participants=("fake", "ag"),
        )
        pre_rt.rooms_service.update_room_summary(
            "room-audit",
            mission="Fix scrubber regression",
            blocked="none",
            phase="active",
            actor_id="alice",
        )
        # Seed lesson and approve it so it becomes ACTIVE
        pre_rt.lesson_service.propose(
            lesson_id="L-TEST-99",
            title="Git Safety",
            rule="Never force push to main",
            category="git",
            severity="critical",
            scope_kind="global",
            affected_peers=("fake",),
            proposer_id="alice",
        )
        pre_rt.lesson_service.approve("L-TEST-99", approved_by_actor_id="alice")
        pre_rt.lesson_service.record_enforcement_result(
            "L-TEST-99", artifact_id="test-artifact", artifact_uri="test://fixture",
            passed=True, actor_id="alice",
        )
        pre_rt.lesson_service.activate("L-TEST-99", actor_id="alice")
    finally:
        pre_rt.close()

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="verify scrubber bug",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        room_id="room-audit",
    )

    result = execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=subject,
    )

    assert result.response_text == "hello with context"
    assert len(adapter.recorded_requests) == 1
    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt is not None

    # Verify context layers are injected
    assert "[USER DIRECTIVES]" in sent_prompt
    assert "User rule: Always verify before mutate." in sent_prompt
    assert "[RUNTIME DIRECTIVES]" in sent_prompt
    assert "DIR-TEST-01" in sent_prompt
    assert "Do not delete production files." in sent_prompt
    assert "[PEER LESSONS]" in sent_prompt
    assert "L-TEST-99" in sent_prompt
    assert "[HUB CONTEXT]" in sent_prompt
    assert "Room ID: room-audit" in sent_prompt
    assert "Fix scrubber regression" in sent_prompt
    assert "[USER QUERY]" in sent_prompt
    assert "verify scrubber bug" in sent_prompt


def test_direct_ask_ag_query_first_ordering(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = RecordingFakePeerAdapter(stdout="ag response", query_first=True)
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    user_dir_path = tmp_path / "_sys" / "ai"
    user_dir_path.mkdir(parents=True, exist_ok=True)
    (user_dir_path / "user-directives.md").write_text("Rule 1.", encoding="utf-8")

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="run prompt for ag",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=subject,
    )

    assert len(adapter.recorded_requests) == 1
    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt is not None
    # query_first: [USER QUERY] leads before [USER DIRECTIVES]
    query_pos = sent_prompt.find("[USER QUERY]")
    directives_pos = sent_prompt.find("[USER DIRECTIVES]")
    assert query_pos != -1 and directives_pos != -1
    assert query_pos < directives_pos


def test_direct_ask_without_context_preserves_raw_prompt(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = RecordingFakePeerAdapter(stdout="clean prompt response")
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="just a simple prompt",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=subject,
    )

    assert len(adapter.recorded_requests) == 1
    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt == "just a simple prompt"


def test_direct_ask_session_resume_compatible(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.adapters.contract import SessionAction

    adapter = RecordingFakePeerAdapter(stdout="session created", supports_session=True)
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    # 1. First ask creates session
    req1 = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="hello session 1",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        session_id="conv-42",
        session_action=SessionAction.CREATE,
    )
    execute_direct_ask(req1, clock=clock, ids=ids, authenticated_subject=subject)

    assert len(adapter.recorded_requests) == 1
    first_req = adapter.recorded_requests[0]
    assert first_req.requested_session_action == SessionAction.CREATE

    # 2. Second ask resumes session
    req2 = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="hello session 2",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        session_id="conv-42",
        resume=True,
    )
    execute_direct_ask(req2, clock=clock, ids=ids, authenticated_subject=subject)

    assert len(adapter.recorded_requests) == 2
    second_req = adapter.recorded_requests[1]
    assert second_req.requested_session_action == SessionAction.RESUME


def test_direct_ask_session_resume_incompatible_model_forces_fresh(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.adapters.contract import SessionAction
    from peerhub.runtime import create_runtime
    from peerhub.dispatch.contract import (
        SessionBindingKey,
        SessionBindingSnapshot,
        SessionBindingState,
    )

    adapter = RecordingFakePeerAdapter(stdout="session response", supports_session=True)
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    # Pre-seed a session binding with an OLD, mismatched fingerprint
    layout = PathLayout.for_workspace(tmp_path)
    from peerhub.core.context import RuntimeContext
    pre_rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        key = SessionBindingKey(
            workspace_scope_id=str(tmp_path),
            instance_id="fake",
            profile_id=profile.profile_id,
            conversation_scope="conv-mismatch",
        )
        with pre_rt.state_store.unit_of_work() as unit:
            unit.add_session_binding(
                SessionBindingSnapshot(
                    key=key,
                    session_id="external-sess-old",
                    current_lease_id=None,
                    adapter_fingerprint="old-stale-fingerprint",
                    readiness_binding="r-1",
                    session_generation=1,
                    revision=1,
                    state=SessionBindingState.ACTIVE,
                    updated_at=clock.now(),
                )
            )
            unit.commit()
    finally:
        pre_rt.close()

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    # Attempt to resume, but fingerprint differs -> must force CREATE
    req = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="hello mismatched session",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        session_id="conv-mismatch",
        resume=True,
    )
    execute_direct_ask(req, clock=clock, ids=ids, authenticated_subject=subject)

    assert len(adapter.recorded_requests) == 1
    sent_req = adapter.recorded_requests[0]
    # Invariant: Incompatible model fingerprint must NOT resume; it must force CREATE!
    assert sent_req.requested_session_action == SessionAction.CREATE


class DirectAskFailThenSucceedAdapter(FakePeerAdapter):
    """Adapter whose first run exits non-zero and second run succeeds."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(stdout="failing\n", exit_code=3)
        self._failing = FakePeerAdapter(stdout="failing\n", exit_code=3)
        self._succeeding = FakePeerAdapter(stdout="recovered\n", exit_code=0)
        self.spawns = 0

    def plan_invocation(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.spawns += 1
        target = self._failing if self.spawns == 1 else self._succeeding
        return target.plan_invocation(*args, **kwargs)  # type: ignore[arg-type]


def test_direct_ask_resilient_dispatch_retries_on_failure(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectAskFailThenSucceedAdapter()
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    req = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="test retry loop",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        max_attempts=3,
    )
    result = execute_direct_ask(req, clock=clock, ids=ids, authenticated_subject=subject)

    assert adapter.spawns == 2
    assert result.response_text is not None and "recovered" in result.response_text
    assert result.request_state == RequestState.SUCCEEDED_VERIFIED


def test_direct_ask_circuit_breaker_opens_on_exhausted_failure(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peerhub.health.contract import PolicyScope, CircuitState

    adapter = FakePeerAdapter(stdout="fatal error\n", exit_code=3)
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )

    subject = AuthenticatedSubject("local-cli:test-user", "test")
    req = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="test circuit breaker",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        max_attempts=2,
    )
    result = execute_direct_ask(req, clock=clock, ids=ids, authenticated_subject=subject)

    assert result.request_state != RequestState.SUCCEEDED_VERIFIED

    layout = PathLayout.for_workspace(tmp_path)
    from peerhub.core.context import RuntimeContext
    from peerhub.runtime import create_runtime
    rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        with rt.state_store.read_unit_of_work() as unit:
            circuit = unit.get_health_circuit(
                PolicyScope.PROFILE,
                profile.profile_id,
            )
            assert circuit is not None
            assert circuit.state == CircuitState.CIRCUIT_OPEN
            assert circuit.receipt is not None
            assert circuit.receipt.incident == f"direct-ask-{result.command_id}"
    finally:
        rt.close()



# ---------------------------------------------------------------------------
# Item G -- cross-dispatch context continuity (ratified backlog,
# docs/reviews/p-drive-mece-migration-audit-2026-09-09.md section 5).
# Durable room/task checkpoints exist but nothing fed them back into a
# SUBSEQUENT dispatch's outgoing prompt.
# ---------------------------------------------------------------------------


def _continuity_target(adapter_stdout: str) -> tuple[RecordingFakePeerAdapter, ResolvedPeerTarget]:
    adapter = RecordingFakePeerAdapter(stdout=adapter_stdout)
    profile = adapter.descriptor.profiles[0]
    return adapter, ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )


def _patch_direct_ask(monkeypatch: pytest.MonkeyPatch, target: ResolvedPeerTarget) -> None:
    monkeypatch.setattr(
        "peerhub.application.direct_ask.resolve_peer_target",
        lambda name, *, profile_id=None: target,
    )
    monkeypatch.setattr(
        "peerhub.application.direct_ask.build_direct_ask_admission_config",
        build_direct_ask_admission_config,
    )


def _seed_checkpointed_task(
    pre_rt,
    *,
    task_id: str,
    coordinator: str,
    room_id: str | None,
    stage: str,
    completed: tuple[str, ...],
    remaining: tuple[str, ...],
) -> None:
    pre_rt.task_service.create(
        task_id=task_id,
        summary=f"summary for {task_id}",
        spec=f"spec for {task_id}",
        creator_id="alice",
        room_id=room_id,
    )
    pre_rt.task_service.claim_start(
        task_id,
        actor_id="alice",
        request_id=f"req-{task_id}",
        coordinator=coordinator,
        attempt_id=f"attempt-{task_id}",
    )
    pre_rt.task_service.checkpoint(
        task_id,
        actor_id="alice",
        checkpoint_id=f"ckpt-{task_id}",
        stage=stage,
        request_id=f"req-{task_id}",
        attempt_id=f"attempt-{task_id}",
        resume_token_ref=None,
        completed_units=completed,
        remaining_units=remaining,
    )


def test_direct_ask_injects_prior_task_checkpoint_for_same_peer(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkpoint left by an EARLIER dispatch reaches the NEXT one.

    No room_id and no task_id are supplied: the continuity resolver must
    discover the workspace's most recent checkpointed task coordinated by
    this same peer on its own (Item G's actual cross-dispatch requirement).
    """
    from peerhub.core.context import RuntimeContext
    from peerhub.runtime import create_runtime

    adapter, target = _continuity_target("continued")
    _patch_direct_ask(monkeypatch, target)

    layout = PathLayout.for_workspace(tmp_path)
    pre_rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        _seed_checkpointed_task(
            pre_rt,
            task_id="task-scrubber",
            coordinator="fake",
            room_id=None,
            stage="phase-2-verification",
            completed=("parse-input", "normalize-rows"),
            remaining=("emit-report",),
        )
    finally:
        pre_rt.close()

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="continue where you left off",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    assert len(adapter.recorded_requests) == 1
    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt is not None
    assert "[CONTINUITY]" in sent_prompt
    assert "task-scrubber" in sent_prompt
    assert "phase-2-verification" in sent_prompt
    assert "emit-report" in sent_prompt
    assert "normalize-rows" in sent_prompt
    assert "continue where you left off" in sent_prompt


def test_direct_ask_injects_latest_room_checkpoint(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded room checkpoint (ctx-save equivalent) is fed back in."""
    from peerhub.core.context import RuntimeContext
    from peerhub.runtime import create_runtime

    adapter, target = _continuity_target("room continued")
    _patch_direct_ask(monkeypatch, target)

    layout = PathLayout.for_workspace(tmp_path)
    pre_rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        pre_rt.rooms_service.create_room(
            room_id="room-continuity",
            topic_id="topic-1",
            title="Continuity Room",
            creator_id="alice",
            participants=("fake",),
        )
        pre_rt.rooms_service.set_room_goal(
            room_id="room-continuity",
            goal="ship the migration audit backlog",
            actor_id="alice",
        )
        pre_rt.rooms_service.append_handoff_note(
            room_id="room-continuity",
            section="KEY_DECISIONS",
            text="ratified execution order B then E then G/H/I",
            actor_id="alice",
        )
        checkpoint = pre_rt.rooms_service.checkpoint(
            "room-continuity",
            actor_id="alice",
            idempotency_key="ckpt-room-continuity-1",
        )
        checkpoint_id = checkpoint["checkpoint_id"]
        assert isinstance(checkpoint_id, str) and checkpoint_id
    finally:
        pre_rt.close()

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="what is the room state",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
        room_id="room-continuity",
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    assert len(adapter.recorded_requests) == 1
    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt is not None
    assert "[CONTINUITY]" in sent_prompt
    assert f"Room checkpoint {checkpoint_id}" in sent_prompt
    assert "room=room-continuity" in sent_prompt
    assert "as_of_event_seq=1" in sent_prompt


def test_direct_ask_continuity_absent_when_no_checkpoints_exist(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No checkpoints anywhere means no continuity block and no raw-prompt drift."""
    adapter, target = _continuity_target("no continuity")
    _patch_direct_ask(monkeypatch, target)

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="plain question",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    assert adapter.recorded_requests[0].prompt_content == "plain question"


def test_direct_ask_continuity_disabled_by_global_config(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The continuity bounds are config, not Python literals.

    Turning continuity off in the global ask config layer must suppress a
    block that the same seeded state produces when it is on.
    """
    from peerhub.core.context import RuntimeContext
    from peerhub.runtime import create_runtime

    config_home = tmp_path / "config-home"
    config_home.mkdir()
    (config_home / "ask.toml").write_text(
        "[continuity]\nenabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    adapter, target = _continuity_target("config-gated")
    _patch_direct_ask(monkeypatch, target)

    layout = PathLayout.for_workspace(workspace)
    pre_rt = create_runtime(RuntimeContext("cli", layout, clock, ids))
    try:
        _seed_checkpointed_task(
            pre_rt,
            task_id="task-gated",
            coordinator="fake",
            room_id=None,
            stage="phase-1",
            completed=("a",),
            remaining=("b",),
        )
    finally:
        pre_rt.close()

    request = DirectAskRequest(
        workspace_root=workspace,
        peer_name="fake",
        prompt="gated question",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    sent_prompt = adapter.recorded_requests[0].prompt_content
    assert sent_prompt == "gated question"


# ---------------------------------------------------------------------------
# Item H -- oversized-prompt staging (ratified backlog section 5).
# direct_ask.py raised ValueError past max_inline_utf8_bytes instead of using
# AdapterRequest's existing prompt_reference field. Parity target:
# hub_peer.py's AgyAdapter.prepare_input, which stages to ai_root/"ipc",
# verifies a digest round-trip, and never truncates.
# ---------------------------------------------------------------------------


def _oversized_prompt(byte_target: int) -> str:
    return "x" * byte_target


def test_direct_ask_stages_oversized_prompt_under_temp_root_by_default(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 3 (dotdir consolidation, ratified 2026-09-09) changed the
    default from workspace-relative staging to the OS temp root, and made
    a successfully-completed dispatch clean up its own staged file --
    content-round-trip and validator-rejection coverage for stage_prompt()
    itself now live in tests/unit/adapters/test_prompt_transport.py."""
    adapter = RecordingFakePeerAdapter(
        stdout="staged ok", max_inline_utf8_bytes=64
    )
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    _patch_direct_ask(monkeypatch, target)

    prompt = _oversized_prompt(4096)
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt=prompt,
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    result = execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    assert result.response_text == "staged ok"
    assert len(adapter.recorded_requests) == 1
    sent = adapter.recorded_requests[0]
    assert sent.prompt_content is None
    assert sent.prompt_reference is not None

    staged_path = Path(sent.prompt_reference)
    # Default location is now "temp": never under the workspace's own
    # durable state directory.
    assert not staged_path.is_relative_to(PathLayout.for_workspace(tmp_path).workspace_home)
    # A successfully-completed dispatch is a definite outcome -- its staged
    # file is cleaned up before execute_direct_ask returns, not left as
    # permanent clutter under either durable dot-directory.
    assert not staged_path.exists()


def test_direct_ask_oversized_prompt_raises_when_staging_disabled(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging is config-gated; turning it off restores the pre-item-H error."""
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    (config_home / "ask.toml").write_text(
        "[prompt_staging]\nenabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    adapter = RecordingFakePeerAdapter(
        stdout="never sent", max_inline_utf8_bytes=64
    )
    profile = adapter.descriptor.profiles[0]
    target = ResolvedPeerTarget(
        cli_name="fake",
        peer_kind="fake",
        adapter=adapter,
        profile=profile,
        executable_path=Path(sys.executable),
    )
    _patch_direct_ask(monkeypatch, target)

    request = DirectAskRequest(
        workspace_root=workspace,
        peer_name="fake",
        prompt=_oversized_prompt(4096),
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    with pytest.raises(ValueError, match="exceeds 64 bytes"):
        execute_direct_ask(
            request,
            clock=clock,
            ids=ids,
            authenticated_subject=AuthenticatedSubject(
                "local-cli:test-user", "test"
            ),
        )


def test_direct_ask_inline_prompt_is_not_staged(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, target = _continuity_target("inline ok")
    _patch_direct_ask(monkeypatch, target)

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="small enough to go inline",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    sent = adapter.recorded_requests[0]
    assert sent.prompt_content == "small enough to go inline"
    assert sent.prompt_reference is None


# --- Item I: durable dispatch transcript storage (ratified backlog section 5) ---

def test_direct_ask_persists_transcript(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, target = _continuity_target("This is the response transcript text")
    _patch_direct_ask(monkeypatch, target)

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    result = execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    assert result.response_text == "This is the response transcript text"
    
    # Verify persistence
    from peerhub.core.context import PathLayout
    db_path = PathLayout.for_workspace(tmp_path).database_path
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM dispatch_transcripts").fetchone()
        
    assert row is not None
    assert row["attempt_id"] == result.attempt_id
    assert row["transcript_text"] == "This is the response transcript text"
    assert row["peer_kind"] == target.peer_kind
    assert row["profile_id"] == target.profile.profile_id
    assert row["created_at"] > 0

def test_direct_ask_transcript_persistence_disabled_by_config(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    (config_home / "ask.toml").write_text(
        "[transcript_storage]\nenabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config_home))

    adapter, target = _continuity_target("This is the response transcript text")
    _patch_direct_ask(monkeypatch, target)

    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="fake",
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        profile_id=target.profile.profile_id,
        limits=TransportLimits(
            process_timeout_ms=10_000,
            silence_timeout_ms=10_000,
            max_output_bytes=1_000_000,
        ),
    )

    execute_direct_ask(
        request,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject("local-cli:test-user", "test"),
    )

    from peerhub.core.context import PathLayout
    db_path = PathLayout.for_workspace(tmp_path).database_path
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        # Table exists but should be empty
        count = conn.execute("SELECT COUNT(*) FROM dispatch_transcripts").fetchone()[0]
    assert count == 0
