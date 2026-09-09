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
    def __init__(self, *args, query_first: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_requests = []
        self._query_first = query_first

    def prompt_policy(self, profile):
        from peerhub.adapters.contract import PromptPolicy
        base = super().prompt_policy(profile)
        return PromptPolicy(
            policy_id=base.policy_id,
            max_inline_utf8_bytes=base.max_inline_utf8_bytes,
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
