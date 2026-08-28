"""Tests for Stage 2 command boundary."""

import pytest
from pathlib import Path
from typing import Any

from peerhub.application.api import ApplicationAPI, AdmissionInputsProvider, AdmissionInputs, AdmitDispatchPayload
from peerhub.application.commands import AdmitDispatch, GetDispatchRequest, GetDispatchLease, SubmissionMetadata
from peerhub.application.legacy import LegacyTranslator, LegacyActionCall, InvalidLegacyArguments, KnownLegacyActionNotBacked, TranslatedCommand, LEGACY_CATALOG, AppendHandoffCommand, ConsensusProposeCommand, ContextFillCommand, ContinuityCheckpointCommand, SessionOpenCommand, SessionCloseCommand, SessionHeartbeatCommand, ThreadReactCommand, MessageSendCommand, MessageCheckCommand, MessageMarkReadCommand, ThreadPromoteCommand, LessonBroadcastCommand, ProposalListCommand, ArbiterReviewCommand, RegisterNodeCommand, ListNodesCommand
from peerhub.application.direct_ask import DirectAskRequest, DirectAskResult
from peerhub.client import Client
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import RequestState
from peerhub.core.protocol import CommandEnvelope, CommandSuccess, CommandFailure, ErrorCode, PROTOCOL_MAJOR, PROTOCOL_MINOR, SCHEMA_VERSION, IdempotencyDisposition
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.ports import RequestContext
from peerhub.runtime import create_runtime, RuntimeContext
from peerhub.core.context import PathLayout
from peerhub.dispatch.duty_lease import (
    DutyLeaseCreateRequest,
    DutyOwnerIdentity,
)
from peerhub.dispatch.room_session import (
    RoomSessionOpenRequest,
    RoomSessionState,
)
from tests.integration.conftest import FakeClock, FakeIdSource


def test_admit_dispatch_payload_requires_capability_tier() -> None:
    with pytest.raises(ValueError):
        AdmitDispatchPayload.model_validate({})


def test_admit_dispatch_payload_rejects_unknown_capability_tier() -> None:
    with pytest.raises(ValueError):
        AdmitDispatchPayload.model_validate(
            {"required_capability_tier": "SUPERUSER"}
        )


@pytest.mark.parametrize(
    "supplied_tier",
    [
        None,
        0,
        1,
        True,
        1.0,
        "read_only",
        "READONLY",
        "",
        ["READ_ONLY"],
        {"name": "READ_ONLY"},
    ],
)
def test_admit_dispatch_payload_rejects_non_enum_capability_tiers(
    supplied_tier: object,
) -> None:
    """Only an exact ``CapabilityTier`` name (or member) is accepted.

    The API boundary must not coerce an ordinal, a truthy value, or a
    case-variant name into a tier: ``CapabilityTier`` is an ``IntEnum``, so
    an unguarded validator would happily read ``1`` as ``WORKTREE_WRITE``
    and silently widen the grant a caller asked for.
    """

    with pytest.raises(ValueError):
        AdmitDispatchPayload.model_validate(
            {"required_capability_tier": supplied_tier}
        )


def test_admit_dispatch_payload_accepts_every_declared_capability_tier() -> None:
    """Fail-closed must not mean fail-always: each real name still parses."""

    for tier in CapabilityTier:
        payload = AdmitDispatchPayload.model_validate(
            {"required_capability_tier": tier.name}
        )
        assert payload.required_capability_tier is tier


def test_legacy_consensus_propose_translates_and_executes(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
            caller = RequestContext(principal="user-1", client_id="client-1")
            client = Client(runtime.application_api, caller=caller)
            submission = SubmissionMetadata(
                client_request_id="req-1", correlation_id="corr-1", client_id="client-1",
                actor_id="peer-1", scope={}, idempotency_key="idem-1",
                expected_policy_revision=None, expected_configuration_revision=None,
                client_timestamp=1000,
            )
            translated = LegacyTranslator().translate(
            LegacyActionCall("consensus-propose", {
                "round_id": "round-1", "title": "Title", "question": "Question",
                "body": "Body", "proposer_id": "peer-1",
                "required_participants": ["peer-1", "peer-2"],
                "eligible_participants": ["peer-1", "peer-2"],
                "risk": "normal", "source_hash": "hash",
            }),
            submission,
        )
            assert isinstance(translated, TranslatedCommand)
            assert isinstance(translated.command, ConsensusProposeCommand)
            outcome = client.submit(translated.command)
            assert isinstance(outcome, CommandSuccess)
            target = runtime.governance_broker.get_target("round-1")
            assert target is not None
            assert target.state["proposal"]["title"] == "Title"


def _legacy_submission() -> SubmissionMetadata:
    return SubmissionMetadata("req-extra", "corr-extra", "client-1", "peer-1", {}, "idem-extra", None, None, 1000)


def test_legacy_task_checkpoint_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.task_service.create(task_id="task-1", summary="s", spec="x", creator_id="peer-1")
    runtime.task_service.claim_start("task-1", actor_id="peer-1", request_id="r", coordinator="c", attempt_id="a")
    translated = LegacyTranslator().translate(LegacyActionCall("task-checkpoint", {"task_id":"task-1","actor_id":"peer-1","checkpoint_id":"cp","stage":"one","request_id":"r","attempt_id":"a","resume_token_ref":None,"completed_units":[],"remaining_units":[]}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.task_service.get_target("task-1").state["state"] == "CHECKPOINTED"


def test_legacy_lesson_propose_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    translated = LegacyTranslator().translate(LegacyActionCall("lessons-propose", {"lesson_id":"lesson-1","title":"T","rule":"R","category":"c","severity":"low","proposer_id":"peer-1","affected_peers":[]}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.lesson_service.get_target("lesson-1").state["lifecycle"] == "PROPOSED"


def test_legacy_room_topic_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(room_id="room-1", topic_id="t", title="Room", creator_id="peer-1", participants=())
    translated = LegacyTranslator().translate(LegacyActionCall("new-topic", {"thread_id":"thread-1","room_id":"room-1","subject":"Topic","creator_id":"peer-1"}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.rooms_service.get_target("thread-1").state["subject"] == "Topic"


def test_legacy_leader_claim_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    translated = LegacyTranslator().translate(LegacyActionCall("leader-claim", {"room_id":"room-1","instance_id":"inst-1","profile_id":"peer","owner_principal_id":"peer-1","authority_epoch":1}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    lease_id = outcome.result["lease_id"]
    assert runtime.duty_lease_coordinator.get_lease(lease_id).state.value == "ACTIVE"


def test_legacy_terminal_close_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    lease = runtime.terminal_duty_service.claim_terminal_duty(
        "room-close", DutyOwnerIdentity("instance-1", "profile-1"), "peer-1", 1
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall("terminal-close", {"lease_id": lease.lease_id, "room_id": "room-close", "instance_id": "instance-1", "profile_id": "profile-1", "term": lease.term, "authority_epoch": lease.authority_epoch}),
        _legacy_submission(),
    )
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.duty_lease_coordinator.get_lease(lease.lease_id).state.value == "RELEASED"


def test_terminal_close_can_end_duty_and_room_session(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    owner = DutyOwnerIdentity("instance-close", "profile-close")
    lease = runtime.terminal_duty_service.claim_terminal_duty(
        "room-close-both", owner, "peer-close", 1
    )
    session = runtime.room_participation_coordinator.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-close",
            room_id="room-close-both",
            actor_principal_id="peer-close",
            owner=owner,
            session_fingerprint="fingerprint-close-both",
            heartbeat_timeout_ms=5_000,
        )
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "terminal-close",
            {
                "lease_id": lease.lease_id,
                "room_id": lease.room_id,
                "instance_id": owner.instance_id,
                "profile_id": owner.profile_id,
                "term": lease.term,
                "authority_epoch": lease.authority_epoch,
                "close_session": True,
                "session_id": session.session_id,
                "session_generation": session.session_generation,
                "workspace_scope_id": session.workspace_scope_id,
                "actor_principal_id": session.actor_principal_id,
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["duty_close"]["status"] == "ok"
    assert outcome.result["session_close"]["status"] == "ok"
    assert (
        runtime.duty_lease_coordinator.get_lease(lease.lease_id).state.value
        == "RELEASED"
    )
    persisted = runtime.room_participation_coordinator.get_session(
        session.session_id
    )
    assert persisted is not None
    assert persisted.state is RoomSessionState.ENDED


def test_terminal_close_reports_session_failure_after_duty_close_and_retries(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    owner = DutyOwnerIdentity("instance-partial", "profile-partial")
    lease = runtime.terminal_duty_service.claim_terminal_duty(
        "room-close-partial", owner, "peer-partial", 1
    )
    session = runtime.room_participation_coordinator.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-partial",
            room_id="room-close-partial",
            actor_principal_id="peer-partial",
            owner=owner,
            session_fingerprint="fingerprint-partial",
            heartbeat_timeout_ms=5_000,
        )
    )

    def translated_close(generation: int):
        translated = LegacyTranslator().translate(
            LegacyActionCall(
                "terminal-close",
                {
                    "lease_id": lease.lease_id,
                    "room_id": lease.room_id,
                    "instance_id": owner.instance_id,
                    "profile_id": owner.profile_id,
                    "term": lease.term,
                    "authority_epoch": lease.authority_epoch,
                    "close_session": True,
                    "session_id": session.session_id,
                    "session_generation": generation,
                    "workspace_scope_id": session.workspace_scope_id,
                    "actor_principal_id": session.actor_principal_id,
                },
            ),
            _legacy_submission(),
        )
        assert isinstance(translated, TranslatedCommand)
        return translated

    failed = client.submit(
        translated_close(session.session_generation + 1).command
    )
    assert isinstance(failed, CommandSuccess)
    assert failed.result["duty_close"]["status"] == "ok"
    assert failed.result["session_close"]["status"] == "failed"
    assert (
        runtime.duty_lease_coordinator.get_lease(lease.lease_id).state.value
        == "RELEASED"
    )
    still_active = runtime.room_participation_coordinator.get_session(
        session.session_id
    )
    assert still_active is not None
    assert still_active.state is RoomSessionState.ACTIVE

    retried = client.submit(
        translated_close(session.session_generation).command
    )
    assert isinstance(retried, CommandSuccess)
    assert retried.result["duty_close"]["status"] == "ok"
    assert retried.result["session_close"]["status"] == "ok"
    ended = runtime.room_participation_coordinator.get_session(
        session.session_id
    )
    assert ended is not None
    assert ended.state is RoomSessionState.ENDED


def test_legacy_terminal_duty_sweep_expires_only_timed_out_lease(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    expired = runtime.duty_lease_coordinator.create_lease(
        DutyLeaseCreateRequest(
            room_id="room-sweep-expired",
            role="terminal-duty",
            owner=DutyOwnerIdentity("instance-expired", "profile-expired"),
            owner_principal_id="peer-expired",
            heartbeat_timeout_ms=1,
            authority_epoch=1,
        )
    )
    active = runtime.duty_lease_coordinator.create_lease(
        DutyLeaseCreateRequest(
            room_id="room-sweep-active",
            role="terminal-duty",
            owner=DutyOwnerIdentity("instance-active", "profile-active"),
            owner_principal_id="peer-active",
            heartbeat_timeout_ms=5_000,
            authority_epoch=1,
        )
    )
    runtime.duty_lease_coordinator._clock = type(
        "SweepClock", (), {"now": lambda self: 1_002}
    )()
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "terminal-duty-sweep",
            {
                "role": "terminal-duty",
                "recovery_actor_principal_id": "system:sweep",
                "trigger": "HEARTBEAT_TIMEOUT",
                "evidence_digest": "sha256:sweep-evidence",
                "policy_id": "terminal-duty-recovery",
                "policy_revision": "1",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["expired_count"] == 1
    assert outcome.result["leases"][0]["lease_id"] == expired.lease_id
    assert (
        runtime.duty_lease_coordinator.get_lease(expired.lease_id).state.value
        == "EXPIRED"
    )
    assert (
        runtime.duty_lease_coordinator.get_lease(active.lease_id).state.value
        == "ACTIVE"
    )


def test_legacy_thread_append_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-append",
        topic_id="topic-append",
        title="Append Room",
        creator_id="peer-author",
        participants=(),
    )
    runtime.rooms_service.create_thread(
        thread_id="thread-append",
        room_id="room-append",
        subject="Append Topic",
        creator_id="peer-author",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-append",
            {
                "message_id": "message-append-1",
                "room_id": "room-append",
                "thread_id": "thread-append",
                "author_id": "peer-author",
                "body": "A durable appended message",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    message = runtime.rooms_service.get_target("message:message-append-1")
    assert message is not None
    assert message.state["thread_id"] == "thread-append"
    assert message.state["body"] == "A durable appended message"


def test_legacy_send_translates_and_persists_mailbox_delivery(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-mail-send",
        topic_id="topic-mail-send",
        title="Mailbox Send",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "send",
            {
                "room_id": "room-mail-send",
                "sender_instance_id": "peer-a-terminal",
                "sender_profile_id": "peer-a",
                "recipient_instance_id": "peer-b-terminal",
                "recipient_profile_id": "peer-b",
                "body": "A private delivery",
                "message_type": "MSG",
                "correlation_id": "mail-correlation-1",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, MessageSendCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    target_id = str(outcome.result["target_id"])
    message = runtime.governance_broker.get_target(target_id)
    assert message is not None
    assert message.state["kind"] == "inbox-message"
    assert message.state["body"] == "A private delivery"
    assert message.state["recipient"] == {
        "instance_id": "peer-b-terminal",
        "profile_id": "peer-b",
    }
    assert message.state["correlation_id"] == "mail-correlation-1"


def test_legacy_check_returns_only_the_callers_private_messages(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-mail-check",
        topic_id="topic-mail-check",
        title="Mailbox Check",
        creator_id="peer-a",
        participants=("peer-a", "peer-b", "peer-c"),
    )
    runtime.rooms_service.send_message(
        room_id="room-mail-check",
        sender_instance_id="peer-a-terminal",
        sender_profile_id="peer-a",
        recipient_instance_id="peer-b-terminal",
        recipient_profile_id="peer-b",
        body="Only peer-b sees this",
    )
    runtime.rooms_service.send_message(
        room_id="room-mail-check",
        sender_instance_id="peer-a-terminal",
        sender_profile_id="peer-a",
        recipient_instance_id="peer-c-terminal",
        recipient_profile_id="peer-c",
        body="Only peer-c sees this",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "check",
            {
                "room_id": "room-mail-check",
                "caller_instance_id": "peer-b-terminal",
                "caller_profile_id": "peer-b",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, MessageCheckCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    messages = outcome.result["messages"]
    assert len(messages) == 1
    assert messages[0]["state"]["body"] == "Only peer-b sees this"
    assert messages[0]["state"]["recipient"] == {
        "instance_id": "peer-b-terminal",
        "profile_id": "peer-b",
    }


def test_legacy_mark_read_translates_and_advances_cursor(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-mail-read",
        topic_id="topic-mail-read",
        title="Mailbox Read",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    runtime.rooms_service.send_message(
        room_id="room-mail-read",
        sender_instance_id="peer-a-terminal",
        sender_profile_id="peer-a",
        recipient_instance_id="peer-b-terminal",
        recipient_profile_id="peer-b",
        body="Mark this read",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "mark-read",
            {
                "room_id": "room-mail-read",
                "recipient_instance_id": "peer-b-terminal",
                "recipient_profile_id": "peer-b",
                "up_through_sequence": 1,
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, MessageMarkReadCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["target_id"] == (
        "inbox-cursor:room-mail-read:peer-b-terminal:peer-b"
    )
    assert runtime.rooms_service.check_inbox(
        room_id="room-mail-read",
        caller_instance_id="peer-b-terminal",
        caller_profile_id="peer-b",
    ) == ()


def test_legacy_thread_promote_translates_and_marks_mailbox_source(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-mail-promote",
        topic_id="topic-mail-promote",
        title="Mailbox Promotion",
        creator_id="peer-a",
        participants=("peer-a", "peer-b"),
    )
    runtime.rooms_service.create_thread(
        thread_id="thread-mail-promote",
        room_id="room-mail-promote",
        subject="Decisions",
        creator_id="peer-a",
    )
    delivery = runtime.rooms_service.send_message(
        room_id="room-mail-promote",
        sender_instance_id="peer-a-terminal",
        sender_profile_id="peer-a",
        recipient_instance_id="peer-b-terminal",
        recipient_profile_id="peer-b",
        body="Promote this delivery",
    )
    message_id = delivery.receipt.target_id.removeprefix("inbox-message:")
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-promote",
            {
                "message_id": message_id,
                "room_id": "room-mail-promote",
                "thread_id": "thread-mail-promote",
                "actor_id": "peer-b",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ThreadPromoteCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    source = runtime.governance_broker.get_target(delivery.receipt.target_id)
    promoted = runtime.governance_broker.list_targets(
        "message", "room-mail-promote"
    )
    assert source is not None
    assert source.state["promoted_to"] == "thread-mail-promote"
    assert len(promoted) == 1
    assert promoted[0].state["metadata"] == {
        "promoted_from_inbox_message_id": message_id,
    }


def test_legacy_append_handoff_and_checkpoint_execute_end_to_end(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-handoff",
        topic_id="topic-handoff",
        title="Handoff Room",
        creator_id="peer-1",
        participants=(),
    )
    runtime.rooms_service.set_room_goal(
        room_id="room-handoff",
        goal="Preserve room continuity",
        actor_id="peer-1",
    )
    append_translation = LegacyTranslator().translate(
        LegacyActionCall(
            "append-handoff",
            {
                "room_id": "room-handoff",
                "section": "KEY_DECISIONS",
                "text": "Use append-only continuity notes",
                "actor_id": "peer-1",
            },
        ),
        _legacy_submission(),
    )
    assert isinstance(append_translation, TranslatedCommand)
    assert isinstance(append_translation.command, AppendHandoffCommand)
    assert isinstance(client.submit(append_translation.command), CommandSuccess)

    checkpoint_translation = LegacyTranslator().translate(
        LegacyActionCall(
            "checkpoint",
            {"room_id": "room-handoff", "actor_id": "peer-1"},
        ),
        _legacy_submission(),
    )
    assert isinstance(checkpoint_translation, TranslatedCommand)
    assert isinstance(
        checkpoint_translation.command, ContinuityCheckpointCommand
    )
    outcome = client.submit(checkpoint_translation.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["sections"]["GOAL"]["value"] == (
        "Preserve room continuity"
    )
    assert outcome.result["sections"]["KEY_DECISIONS"]["items"] == (
        "Use append-only continuity notes",
    )
    assert "## KEY_DECISIONS" in outcome.result["markdown"]
    replay = client.submit(checkpoint_translation.command)
    assert isinstance(replay, CommandSuccess)
    assert replay.result == outcome.result
    notes = runtime.governance_broker.list_targets(
        "continuity-note", "room-handoff"
    )
    checkpoints = runtime.governance_broker.list_targets(
        "checkpoint-created", "room-handoff"
    )
    assert len(notes) == 1
    assert len(checkpoints) == 1


def test_legacy_context_fill_translates_and_executes_read_only(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-context-fill",
        topic_id="topic-context-fill",
        title="Context Fill Room",
        creator_id="peer-1",
        participants=(),
    )
    runtime.rooms_service.set_room_goal(
        room_id="room-context-fill",
        goal="Fill the startup context",
        actor_id="peer-1",
    )
    runtime.rooms_service.append_handoff_note(
        room_id="room-context-fill",
        section="PENDING_ISSUES",
        text="Run the terminal suite",
        actor_id="peer-1",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "context-fill",
            {
                "room_id": "room-context-fill",
                "session_id": "metadata-only-session",
                "sections": ("GOAL", "PENDING_ISSUES"),
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ContextFillCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.state == "COMPLETED"
    assert outcome.result["session_id"] == "metadata-only-session"
    assert tuple(outcome.result["sections"]) == (
        "GOAL",
        "PENDING_ISSUES",
    )
    assert outcome.result["sections"]["GOAL"]["value"] == (
        "Fill the startup context"
    )
    assert outcome.result["sections"]["PENDING_ISSUES"]["items"] == (
        "Run the terminal suite",
    )
    assert runtime.governance_broker.list_targets(
        "checkpoint-created", "room-context-fill"
    ) == ()


def test_legacy_thread_react_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-react",
        topic_id="topic-react",
        title="React Room",
        creator_id="peer-author",
        participants=(),
    )
    runtime.rooms_service.create_thread(
        thread_id="thread-react",
        room_id="room-react",
        subject="React Topic",
        creator_id="peer-author",
    )
    runtime.rooms_service.append_message(
        message_id="message-react-1",
        room_id="room-react",
        thread_id="thread-react",
        author_id="peer-author",
        body="A message that can be acknowledged",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-react",
            {
                "message_id": "message-react-1",
                "room_id": "room-react",
                "actor_instance_id": "peer-reader-terminal",
                "actor_profile_id": "peer-reader",
                "reaction_type": "ACK",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    state = runtime.rooms_service.get_reaction_state(
        "message-react-1",
        "peer-reader-terminal",
        "peer-reader",
        "ACK",
    )
    events = runtime.governance_broker.list_targets("reaction-event", "room-react")
    assert state is not None
    assert state.state["status"] == "ACTIVE"
    assert len(events) == 1
    assert events[0].state["action"] == "ADD"


def test_legacy_thread_react_remove_dispatches_to_unreact(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-unreact-legacy",
        topic_id="topic-unreact-legacy",
        title="Legacy Unreact Room",
        creator_id="peer-author",
        participants=(),
    )
    runtime.rooms_service.create_thread(
        thread_id="thread-unreact-legacy",
        room_id="room-unreact-legacy",
        subject="Legacy Unreact Topic",
        creator_id="peer-author",
    )
    runtime.rooms_service.append_message(
        message_id="message-unreact-legacy",
        room_id="room-unreact-legacy",
        thread_id="thread-unreact-legacy",
        author_id="peer-author",
        body="Remove a reaction from this message",
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-react",
            {
                "message_id": "message-unreact-legacy",
                "room_id": "room-unreact-legacy",
                "actor_instance_id": "peer-reader-terminal",
                "actor_profile_id": "peer-reader",
                "reaction_type": "ACK",
                "action": "REMOVE",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ThreadReactCommand)
    assert translated.command.action == "REMOVE"
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    state = runtime.rooms_service.get_reaction_state(
        "message-unreact-legacy",
        "peer-reader-terminal",
        "peer-reader",
        "ACK",
    )
    events = runtime.governance_broker.list_targets(
        "reaction-event", "room-unreact-legacy"
    )
    assert state is not None
    assert state.state["status"] == "REMOVED"
    assert len(events) == 1
    assert events[0].state["action"] == "REMOVE"


def test_native_thread_react_remove_executes_through_client(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-unreact-native",
        topic_id="topic-unreact-native",
        title="Native Unreact Room",
        creator_id="peer-author",
        participants=(),
    )
    runtime.rooms_service.create_thread(
        thread_id="thread-unreact-native",
        room_id="room-unreact-native",
        subject="Native Unreact Topic",
        creator_id="peer-author",
    )
    runtime.rooms_service.append_message(
        message_id="message-unreact-native",
        room_id="room-unreact-native",
        thread_id="thread-unreact-native",
        author_id="peer-author",
        body="Native REMOVE reaches the service",
    )
    runtime.rooms_service.react(
        message_id="message-unreact-native",
        room_id="room-unreact-native",
        actor_instance_id="peer-reader-terminal",
        actor_profile_id="peer-reader",
        reaction_type="ACK",
    )
    command = ThreadReactCommand(
        submission=_legacy_submission(),
        message_id="message-unreact-native",
        room_id="room-unreact-native",
        actor_instance_id="peer-reader-terminal",
        actor_profile_id="peer-reader",
        reaction_type="ACK",
        action="REMOVE",
    )

    outcome = client.submit(command)

    assert isinstance(outcome, CommandSuccess)
    state = runtime.rooms_service.get_reaction_state(
        "message-unreact-native",
        "peer-reader-terminal",
        "peer-reader",
        "ACK",
    )
    assert state is not None
    assert state.state["status"] == "REMOVED"
    assert state.state["latest_action"] == "REMOVE"


def test_thread_react_rejects_unknown_action(runtime_setup) -> None:
    _, client, _ = runtime_setup
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-react",
            {
                "message_id": "message-invalid-action",
                "room_id": "room-invalid-action",
                "actor_instance_id": "peer-reader-terminal",
                "actor_profile_id": "peer-reader",
                "reaction_type": "ACK",
                "action": "TOGGLE",
            },
        ),
        _legacy_submission(),
    )
    assert isinstance(translated, InvalidLegacyArguments)
    assert translated.reason == "action must be ADD or REMOVE"

    native = ThreadReactCommand(
        submission=_legacy_submission(),
        message_id="message-invalid-action",
        room_id="room-invalid-action",
        actor_instance_id="peer-reader-terminal",
        actor_profile_id="peer-reader",
        reaction_type="ACK",
        action="TOGGLE",
    )
    outcome = client.submit(native)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code is ErrorCode.INVALID_PARAMS


def test_legacy_lesson_broadcast_translates_and_delivers_to_room_members(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.lesson_service.propose(
        lesson_id="broadcast-lesson",
        title="Broadcast title",
        rule="Broadcast rule",
        category="verification",
        severity="LOW",
        proposer_id="sender",
        affected_peers=(),
    )
    runtime.lesson_service.approve(
        "broadcast-lesson",
        approved_by_actor_id="human:reviewer",
    )
    runtime.lesson_service.activate("broadcast-lesson", actor_id="sender")
    runtime.rooms_service.create_room(
        room_id="broadcast-room",
        topic_id="broadcast-topic",
        title="Broadcast Room",
        creator_id="sender",
        participants=("sender", "peer-b", "peer-c"),
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "lesson-broadcast",
            {
                "lesson_id": "broadcast-lesson",
                "room_id": "broadcast-room",
                "sender_instance_id": "sender",
                "sender_profile_id": "sender",
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, LessonBroadcastCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["recipient_profile_ids"] == ("peer-b", "peer-c")

    for peer_id in ("peer-b", "peer-c"):
        inbox = runtime.rooms_service.check_inbox(
            room_id="broadcast-room",
            caller_instance_id=peer_id,
            caller_profile_id=peer_id,
        )
        assert len(inbox) == 1
        assert inbox[0].state["message_type"] == "LESSON"
        delivery = runtime.governance_broker.get_target(
            f"lesson-delivery:broadcast-lesson:{peer_id}"
        )
        assert delivery is not None
        assert delivery.state["status"] == "PENDING"


def test_legacy_init_session_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "init-session",
            {
                "workspace_scope_id": "workspace-1",
                "room_id": "room-session-1",
                "actor_principal_id": "peer-1",
                "instance_id": "instance-1",
                "profile_id": "cx.standard",
                "session_fingerprint": "fingerprint-1",
                "heartbeat_timeout_ms": 5_000,
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, SessionOpenCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["state"] == "ACTIVE"
    assert outcome.result["owner"] == {
        "instance_id": "instance-1",
        "profile_id": "cx.standard",
    }
    session = runtime.room_participation_coordinator.get_session(
        str(outcome.result["session_id"])
    )
    assert session is not None
    assert session.state is RoomSessionState.ACTIVE


def test_legacy_end_session_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    owner = DutyOwnerIdentity("instance-1", "cx.standard")
    session = runtime.room_participation_coordinator.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-1",
            room_id="room-session-close",
            actor_principal_id="peer-1",
            owner=owner,
            session_fingerprint="fingerprint-close",
            heartbeat_timeout_ms=5_000,
        )
    )
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "end-session",
            {
                "session_id": session.session_id,
                "session_generation": session.session_generation,
                "workspace_scope_id": session.workspace_scope_id,
                "room_id": session.room_id,
                "actor_principal_id": session.actor_principal_id,
                "instance_id": owner.instance_id,
                "profile_id": owner.profile_id,
            },
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, SessionCloseCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["state"] == "ENDED"
    persisted = runtime.room_participation_coordinator.get_session(
        session.session_id
    )
    assert persisted is not None
    assert persisted.state is RoomSessionState.ENDED


def test_native_session_heartbeat_executes_through_client(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    owner = DutyOwnerIdentity("instance-1", "cx.standard")
    session = runtime.room_participation_coordinator.open_session(
        RoomSessionOpenRequest(
            workspace_scope_id="workspace-1",
            room_id="room-session-heartbeat",
            actor_principal_id="peer-1",
            owner=owner,
            session_fingerprint="fingerprint-heartbeat",
            heartbeat_timeout_ms=5_000,
        )
    )
    command = SessionHeartbeatCommand(
        submission=_legacy_submission(),
        session_id=session.session_id,
        session_generation=session.session_generation,
        workspace_scope_id=session.workspace_scope_id,
        room_id=session.room_id,
        actor_principal_id=session.actor_principal_id,
        instance_id=owner.instance_id,
        profile_id=owner.profile_id,
        heartbeat_timeout_ms=10_000,
    )

    outcome = client.submit(command)

    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["state"] == "ACTIVE"
    assert outcome.result["heartbeat_expires_at"] == 11_000
    persisted = runtime.room_participation_coordinator.get_session(
        session.session_id
    )
    assert persisted is not None
    assert persisted.heartbeat_expires_at == 11_000


def test_legacy_approval_request_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.task_service.create(task_id="approval-task", summary="s", spec="x", creator_id="peer-1")
    runtime.task_service.claim_start("approval-task", actor_id="peer-1", request_id="r", coordinator="c", attempt_id="a")
    translated = LegacyTranslator().translate(LegacyActionCall("approval-request", {"task_id":"approval-task","requester_id":"peer-1","approval_id":"approval-1","approver_id":"peer-2"}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.governance_broker.get_target("approval:approval-1").state["status"] == "PENDING"


def test_legacy_consensus_sweep_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.consensus_service.propose(round_id="sweep-round", title="t", question="q", body="b", proposer_id="peer-1", required_participants=("peer-1", "peer-2"), eligible_participants=("peer-1", "peer-2"), risk="normal", source_hash="h")
    translated = LegacyTranslator().translate(LegacyActionCall("consensus-sweep", {"round_id":"sweep-round","reason":"stalled"}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(client.submit(translated.command), CommandSuccess)
    assert runtime.consensus_service.get_target("sweep-round").state["timeout_evidence"]["reason"] == "stalled"


def test_legacy_lessons_list_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.lesson_service.propose(lesson_id="listed-lesson", title="T", rule="R", category="c", severity="low", proposer_id="peer-1", affected_peers=())
    runtime.lesson_service.approve("listed-lesson", approved_by_actor_id="peer-1")
    runtime.lesson_service.activate("listed-lesson", actor_id="peer-1")
    translated = LegacyTranslator().translate(LegacyActionCall("lessons-list", {}), _legacy_submission())
    assert isinstance(translated, TranslatedCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert any(item["target_id"] == "lesson:listed-lesson" for item in outcome.result["lessons"])


def test_native_proposal_list_includes_open_and_resolved_rounds(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    service = runtime.consensus_service
    participants = ("peer-1", "peer-2")
    for round_id in ("proposal-open", "proposal-resolved"):
        service.propose(
            round_id=round_id,
            title=round_id,
            question="Ready?",
            body="Body",
            proposer_id="peer-1",
            required_participants=participants,
            eligible_participants=participants,
            risk="normal",
            source_hash=f"sha256:{round_id}",
        )
    service.cast_vote("proposal-resolved", actor_id="peer-1", choice="agree")
    service.cast_vote("proposal-resolved", actor_id="peer-2", choice="agree")
    service.final_call_ack("proposal-resolved", actor_id="peer-1", ack=True)
    service.final_call_ack("proposal-resolved", actor_id="peer-2", ack=True)

    outcome = client.submit(ProposalListCommand(_legacy_submission()))

    assert isinstance(outcome, CommandSuccess)
    proposals = outcome.result["proposals"]
    assert {item["target_id"] for item in proposals} == {
        "proposal-open",
        "proposal-resolved",
    }
    resolved = next(
        item for item in proposals if item["target_id"] == "proposal-resolved"
    )
    assert resolved["state"]["status"] == "resolved"


def test_legacy_proposal_list_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.consensus_service.propose(
        round_id="legacy-proposal-list",
        title="List me",
        question="Visible?",
        body="Body",
        proposer_id="peer-1",
        required_participants=("peer-1", "peer-2"),
        eligible_participants=("peer-1", "peer-2"),
        risk="normal",
        source_hash="sha256:legacy-proposal-list",
    )

    translated = LegacyTranslator().translate(
        LegacyActionCall("proposal-list", {}),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ProposalListCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert any(
        item["target_id"] == "legacy-proposal-list"
        for item in outcome.result["proposals"]
    )


class _FakeArbiterExecutor:
    """Stands in for execute_direct_ask so this test never spawns a real peer process."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.requests: list[DirectAskRequest] = []

    def __call__(
        self,
        request: DirectAskRequest,
        *,
        clock: Any,
        ids: Any,
        authenticated_subject: AuthenticatedSubject,
    ) -> DirectAskResult:
        del clock, ids, authenticated_subject
        self.requests.append(request)
        return DirectAskResult(
            command_id="ask-command-1",
            attempt_id="ask-attempt-1",
            peer_kind="cc",
            profile_id=request.profile_id,
            response_text=self.response_text,
            request_state=RequestState.SUCCEEDED_VERIFIED,
            error_code=None,
            execution_certainty=None,
        )


def test_legacy_arbiter_review_translates_and_executes(tmp_path: Path) -> None:
    config_dir = tmp_path / ".peerhub"
    config_dir.mkdir()
    (config_dir / "arbiter.json").write_text(
        '{"enabled": true, "triggers": ["dissent"]}',
        encoding="utf-8",
    )

    fake_executor = _FakeArbiterExecutor("VERDICT: APPROVE")
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context, arbiter_executor=fake_executor) as runtime:
        caller = RequestContext(principal="user-1", client_id="client-1")
        client = Client(runtime.application_api, caller=caller)

        runtime.consensus_service.propose(
            round_id="legacy-arbiter-review",
            title="Deploy?",
            question="Should we deploy?",
            body="Review the rollout evidence and decide.",
            proposer_id="peer-1",
            required_participants=("peer-1", "peer-2"),
            eligible_participants=("peer-1", "peer-2"),
            risk="normal",
            source_hash="sha256:legacy-arbiter-review",
        )
        runtime.consensus_service.cast_vote(
            "legacy-arbiter-review", actor_id="peer-1", choice="agree"
        )
        runtime.consensus_service.cast_vote(
            "legacy-arbiter-review", actor_id="peer-2", choice="disagree"
        )
        target = runtime.consensus_service.get_target("legacy-arbiter-review")
        assert target is not None
        if target.state["phase"] != "quorum_reached":
            runtime.consensus_service.request_escalation(
                "legacy-arbiter-review", "dissenting vote", "peer-1", 0, "human-tier-0",
            )
        runtime.consensus_service.resolve(
            "legacy-arbiter-review", "approved", "human:reviewer", "manual resolution",
        )

        translated = LegacyTranslator().translate(
            LegacyActionCall("arbiter-review", {"round_id": "legacy-arbiter-review"}),
            _legacy_submission(),
        )

        assert isinstance(translated, TranslatedCommand)
        assert isinstance(translated.command, ArbiterReviewCommand)
        outcome = client.submit(translated.command)
        assert isinstance(outcome, CommandSuccess)
        assert outcome.result["fired"] is True
        assert outcome.result["parsed_verdict"] == "APPROVE"
        assert fake_executor.requests, (
            "arbiter review must dispatch through the injected fake executor, "
            "never spawn a real peer process during a test"
        )
        round_after = runtime.consensus_service.get_target("legacy-arbiter-review")
        assert round_after is not None
        assert round_after.state["arbiter_opinion"]["verdict"] == "APPROVE"


def test_legacy_list_nodes_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup

    translated = LegacyTranslator().translate(
        LegacyActionCall("list-nodes", {}), _legacy_submission()
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ListNodesCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert {item["state"]["node_id"] for item in outcome.result["nodes"]} == {
        "ag",
        "cc",
        "cx",
    }


def test_legacy_register_node_translates_and_executes(runtime_setup) -> None:
    runtime, client, _ = runtime_setup

    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "register-node",
            {"node_id": "legacy-worker-1", "peer_kind": "cc", "actor_id": "peer-1"},
        ),
        _legacy_submission(),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, RegisterNodeCommand)
    outcome = client.submit(translated.command)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["target_id"] == "peer-node:legacy-worker-1"

    translated_list = LegacyTranslator().translate(
        LegacyActionCall("list-nodes", {}), _legacy_submission()
    )
    assert isinstance(translated_list, TranslatedCommand)
    listed = client.submit(translated_list.command)
    assert isinstance(listed, CommandSuccess)
    assert any(
        item["state"]["node_id"] == "legacy-worker-1"
        for item in listed.result["nodes"]
    )


def test_admit_success(runtime_setup, monkeypatch):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    from peerhub.dispatch.contract import RequestSnapshot, AdmissionReceipt, RequestState, LeaseState
    from peerhub.core.protocol import CommandID
    
    req = MagicMock()
    req.command_id = "cmd-123"
    req.state = RequestState.ADMITTED
    req.revision = 1
    req.lease_id = "lease-1"
    req.selected_peer_instance_id = "inst-1"
    req.selected_profile_id = "prof-1"
    req.route_decision_digest = "digest"
    receipt = MagicMock()
    receipt.admission_receipt_id = "rec-123"
    
    lease = MagicMock()
    lease.lease_id = "lease-1"
    lease.state = LeaseState.RESERVED

    capability_lease = MagicMock()
    capability_lease.capability_lease_id = "cap-lease-1"

    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=None,
        dispatch_admission=(req, receipt, lease, capability_lease)
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={
            "kind": "DELIVERY_ONLY",
            "replay_safe": False,
        },
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandSuccess)
    frozen_contract = mock_workflows.admit_request.call_args.kwargs[
        "completion_contract"
    ]
    assert frozen_contract.replay_safe is False


def test_missing_idempotency_key(runtime_setup):
    rt, client, caller = runtime_setup
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key=None, # Missing idempotency key
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.MISSING_IDEMPOTENCY_KEY


def test_admit_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.admit",
        params={"prompt": "hello", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message


@pytest.mark.parametrize(
    "completion_contract",
    (
        {"replay_safe": "false"},
        {"kind": "NOT_A_COMPLETION_KIND"},
        {"kind": "ARTIFACT_REQUIRED", "requirements": []},
        {"requirements": {"field": "status"}},
        {"requirements": ["not-an-object"]},
        {"unexpected_extra": True},
    ),
)
def test_admit_rejects_malformed_completion_contract_at_decode(
    runtime_setup,
    completion_contract: dict[str, Any],
):
    rt, _, caller = runtime_setup
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-malformed-contract",
        correlation_id="corr-malformed-contract",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.admit",
        params={
            "prompt": "hello",
            "completion_contract": completion_contract,
        },
        idempotency_key="idem-malformed-contract",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=caller)

    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code is ErrorCode.INVALID_PARAMS
    assert (
        outcome.error.execution_certainty
        is ExecutionCertainty.NOT_STARTED
    )

def test_unauthorized_client(runtime_setup):
    rt, client, _ = runtime_setup
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-WRONG",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.CLIENT_UNKNOWN


def test_unbacked_command(tmp_path: Path):
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id="home-1",
        paths=layout,
        clock=FakeClock(),
        ids=FakeIdSource(),
    )
    # We create the runtime WITHOUT a provider to simulate NOT_BACKED
    rt = create_runtime(context, admission_provider=None)
    caller = RequestContext(principal="user-1", client_id="client-1")
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={},
        session_policy={},
    )
    
    # The registration checks availability, but since we modify it after init we must re-register
    # In ApplicationAPI the availability is set during init. If we want it NOT_BACKED we can test with a raw envelope.
    
    env = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="r", correlation_id="c", client_id="client-1", actor_id=None, scope={},
        method="dispatch.admit", params={}, idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    # Registration availability should already be NOT_BACKED
    
    outcome = rt.application_api.submit(env, caller=caller)
    assert not outcome.ok
    assert outcome.error.code == ErrorCode.COMMAND_NOT_BACKED


def test_legacy_translation_ask():
    translator = LegacyTranslator()
    sub = SubmissionMetadata(
        client_request_id="r", correlation_id="c", client_id="c1", actor_id=None, scope={},
        idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    out = translator.translate(LegacyActionCall(action="ask", arguments={"prompt": "test"}), sub)
    assert isinstance(out, TranslatedCommand)
    assert out.command.method == "dispatch.submit"


def test_legacy_translation_unbacked():
    translator = LegacyTranslator()
    sub = SubmissionMetadata(
        client_request_id="r", correlation_id="c", client_id="c1", actor_id=None, scope={},
        idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    out = translator.translate(LegacyActionCall(action="status", arguments={}), sub)
    assert isinstance(out, KnownLegacyActionNotBacked)
    assert out.legacy_action == "status"
    assert out.target_method == LEGACY_CATALOG["status"]


def test_admit_rejected_internal_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    
    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=MagicMock(error_code="exhausted"),
        dispatch_admission=None
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INTERNAL_ERROR
    assert outcome.error.details.get("exception") == "RuntimeError"


def test_req_get_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message

    # Test missing required field
    envelope2 = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={},  # missing target_command_id
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome2 = rt.application_api.submit(envelope2, caller=caller)
    assert isinstance(outcome2, CommandFailure)
    assert outcome2.error.code == ErrorCode.INVALID_PARAMS
    assert "target_command_id" in outcome2.error.message


def test_request_get_enforces_resource_ownership(runtime_setup):
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from peerhub.dispatch.contract import RequestState

    rt, _, caller = runtime_setup
    dispatch = MagicMock()
    dispatch.get_request.return_value = SimpleNamespace(
        command_id="cmd-123",
        client_id="client-1",
        client_request_id="original-request",
        correlation_id="original-correlation",
        authenticated_principal="user-1",
        command_type="dispatch.admit",
        idempotency_key="original-idempotency",
        payload_digest="0" * 64,
        scope={},
        expected_policy_revision=None,
        expected_configuration_revision=None,
        policy_revision=1,
        configuration_revision=1,
        selected_peer_instance_id="instance-1",
        selected_profile_id="profile-1",
        route_decision_digest="1" * 64,
        lease_id="lease-123",
        state=RequestState.ADMITTED,
        revision=1,
        created_at=1000,
        updated_at=1000,
        terminal_error_code=None,
    )
    rt.application_api._dispatch = dispatch

    own_envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="lookup-own",
        correlation_id="corr-own",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123"},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    own = rt.application_api.submit(own_envelope, caller=caller)
    assert isinstance(own, CommandSuccess)
    assert own.result["command_id"] == "cmd-123"

    other_caller = RequestContext(principal="user-2", client_id="client-2")
    other_envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="lookup-other",
        correlation_id="corr-other",
        client_id="client-2",
        actor_id="user-2",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123"},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    other = rt.application_api.submit(other_envelope, caller=other_caller)
    assert isinstance(other, CommandFailure)
    assert other.error.code is ErrorCode.CLIENT_UNKNOWN
    assert other.error.execution_certainty is ExecutionCertainty.NOT_STARTED


def test_lease_get_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message

    # Test missing required field
    envelope2 = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={},  # missing lease_id
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome2 = rt.application_api.submit(envelope2, caller=caller)
    assert isinstance(outcome2, CommandFailure)
    assert outcome2.error.code == ErrorCode.INVALID_PARAMS
    assert "lease_id" in outcome2.error.message


def test_lease_get_success(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.dispatch.contract import LeaseSnapshot, LeaseState, LeaseFenceTuple, ProcessBirthIdentity
    from peerhub.core.protocol import CommandID

    mock_dispatch = MagicMock()
    fence = LeaseFenceTuple(
        session_id="sess-1",
        lease_id="lease-123",
        fencing_token=1,
        revision=42,
        owner_principal_id="principal-1",
        owner_instance_id="instance-1",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=9999,
            process_creation_time=1000,
        ),
        command_id=CommandID("cmd-123"),
        authority_epoch=1,
        attempt_id="att-1",
        owner_peer_id="peer-1",
    )
    lease = LeaseSnapshot(
        lease_id="lease-123",
        session_id="sess-1",
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=1000,
        created_at=1000,
        updated_at=1000
    )
    mock_dispatch.get_lease.return_value = lease
    request = MagicMock()
    request.client_id = "client-1"
    mock_dispatch.get_request.return_value = request
    rt.application_api._dispatch = mock_dispatch

    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123"},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["revision"] == 42
    assert outcome.result["fence_revision"] == 42
    assert outcome.result["lease_id"] == "lease-123"


def test_lease_get_enforces_resource_ownership(runtime_setup):
    rt, _, caller = runtime_setup

    from unittest.mock import MagicMock
    from peerhub.dispatch.contract import LeaseSnapshot, LeaseState, LeaseFenceTuple, ProcessBirthIdentity
    from peerhub.core.protocol import CommandID

    mock_dispatch = MagicMock()
    fence = LeaseFenceTuple(
        session_id="sess-1",
        lease_id="lease-123",
        fencing_token=1,
        revision=42,
        owner_principal_id="principal-1",
        owner_instance_id="instance-1",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=9999,
            process_creation_time=1000,
        ),
        command_id=CommandID("cmd-123"),
        authority_epoch=1,
        attempt_id="att-1",
        owner_peer_id="peer-1",
    )
    lease = LeaseSnapshot(
        lease_id="lease-123",
        session_id="sess-1",
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=1000,
        created_at=1000,
        updated_at=1000,
    )
    mock_dispatch.get_lease.return_value = lease
    request = MagicMock()
    request.client_id = "client-1"
    mock_dispatch.get_request.return_value = request
    rt.application_api._dispatch = mock_dispatch

    other_caller = RequestContext(principal="user-2", client_id="client-2")
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-other",
        correlation_id="corr-other",
        client_id="client-2",
        actor_id="user-2",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123"},
        idempotency_key="idem-other",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=other_caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code is ErrorCode.CLIENT_UNKNOWN
    assert outcome.error.execution_certainty is ExecutionCertainty.NOT_STARTED


def test_admit_route_exhausted(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    
    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=MagicMock(error_code="exhausted"),
        dispatch_admission=None
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-exhausted",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        required_capability_tier=CapabilityTier.READ_ONLY,
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INTERNAL_ERROR
