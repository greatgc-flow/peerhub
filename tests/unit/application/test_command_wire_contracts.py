"""Unit tests verifying wire contracts and parameter encodings for native Command dataclasses.

Preserves wire-contract knowledge originally asserted through the retired LegacyTranslator,
verifying that Command instances encode expected wire methods and parameter dictionaries directly.
"""

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    ClearRoomCommand,
    ConsensusCheckCommand,
    ConsensusProposeCommand,
    ConsensusVoteCommand,
    LeaderClaimCommand,
    LeaderYieldCommand,
    LessonActivateCommand,
    LessonProposeCommand,
    LessonRetireCommand,
    NewTopicCommand,
    TaskCheckpointCommand,
    TaskFailoverCommand,
    TaskStatusCommand,
    TerminalHandoffCommand,
    TerminalHeartbeatCommand,
    ThreadReactCommand,
)


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


# ---------------------------------------------------------------------------
# Consensus Commands
# ---------------------------------------------------------------------------


def test_consensus_commands_wire_contracts() -> None:
    proposal = ConsensusProposeCommand(
        submission=_submission(),
        round_id="r1",
        title="T",
        question="Q",
        body="B",
        proposer_id="cx",
        required_participants=("cx", "ag"),
        eligible_participants=("cx", "ag"),
        risk="normal",
        source_hash="sha256:x",
    )
    assert proposal.method == "consensus.round.propose"
    assert proposal.encode_params()["round_id"] == "r1"

    vote = ConsensusVoteCommand(
        submission=_submission(),
        round_id="r1",
        actor_id="ag",
        choice="agree",
    )
    assert vote.method == "consensus.vote.cast"
    assert vote.encode_params() == {"round_id": "r1", "actor_id": "ag", "choice": "agree"}

    check = ConsensusCheckCommand(
        submission=_submission(),
        round_id="r1",
    )
    assert check.method == "consensus.round.read"
    assert check.encode_params() == {"round_id": "r1"}


# ---------------------------------------------------------------------------
# Duty & Leadership Commands
# ---------------------------------------------------------------------------


def test_duty_commands_wire_contracts() -> None:
    cases = [
        # Leadership is workspace-global: no room/lease/fence params at
        # all, unlike the terminal-duty cases below.
        (
            LeaderClaimCommand(
                submission=_submission(),
                peer_node_id="cx",
                actor_id="cx",
                reason="planning",
                domain="design",
            ),
            "routing.leadership.claim",
        ),
        (
            LeaderYieldCommand(
                submission=_submission(),
                yielding_peer_id="cx",
                actor_id="cx",
                reason="context_exhausted",
            ),
            "routing.leadership.yield",
        ),
        (
            TerminalHandoffCommand(
                submission=_submission(),
                current_lease_id="old",
                room_id="room",
                current_instance_id="i",
                current_profile_id="cx",
                term=1,
                authority_epoch=2,
                new_instance_id="j",
                new_profile_id="ag",
                new_owner_principal_id="p2",
                new_authority_epoch=3,
            ),
            "coordination.terminal.handoff",
        ),
        (
            TerminalHeartbeatCommand(
                submission=_submission(),
                lease_id="lease",
                room_id="room",
                instance_id="i",
                profile_id="cx",
                term=1,
                authority_epoch=2,
            ),
            "coordination.terminal.heartbeat",
        ),
    ]
    for cmd, method in cases:
        assert cmd.method == method
        assert cmd.encode_params()


def test_leadership_commands_workspace_global_params() -> None:
    claim = LeaderClaimCommand(
        submission=_submission(),
        peer_node_id="cx",
        actor_id="cx",
        reason="failover",
        domain="recovery",
    )
    assert claim.encode_params() == {
        "peer_node_id": "cx",
        "actor_id": "cx",
        "reason": "failover",
        "domain": "recovery",
    }

    yielded = LeaderYieldCommand(
        submission=_submission(),
        yielding_peer_id="cc",
        actor_id="cx",
    )
    assert yielded.encode_params() == {
        "yielding_peer_id": "cc",
        "actor_id": "cx",
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Room Commands
# ---------------------------------------------------------------------------


def test_new_topic_wire_contract() -> None:
    cmd = NewTopicCommand(
        submission=_submission(),
        thread_id="thread-1",
        room_id="room-1",
        subject="Topic",
        creator_id="cx",
    )
    assert cmd.method == "coordination.topic.create"
    assert cmd.encode_params() == {
        "thread_id": "thread-1",
        "room_id": "room-1",
        "subject": "Topic",
        "creator_id": "cx",
    }


def test_clear_room_wire_contract() -> None:
    cmd = ClearRoomCommand(
        submission=_submission(),
        old_room_id="old",
        new_room_id="new",
        subject="Fresh",
        actor_id="cx",
    )
    assert cmd.method == "coordination.room.clear"
    assert cmd.encode_params() == {
        "old_room_id": "old",
        "new_room_id": "new",
        "subject": "Fresh",
        "actor_id": "cx",
    }


def test_thread_react_wire_contract() -> None:
    cmd = ThreadReactCommand(
        submission=_submission(),
        message_id="message-1",
        room_id="room-1",
        actor_instance_id="cx-terminal",
        actor_profile_id="cx",
        reaction_type="ACK",
        action="ADD",
    )
    assert cmd.method == "coordination.thread.react"
    assert cmd.encode_params() == {
        "message_id": "message-1",
        "room_id": "room-1",
        "actor_instance_id": "cx-terminal",
        "actor_profile_id": "cx",
        "reaction_type": "ACK",
        "action": "ADD",
    }


# ---------------------------------------------------------------------------
# Task & Lesson Commands
# ---------------------------------------------------------------------------


def test_task_and_lesson_commands_wire_contracts() -> None:
    checkpoint = TaskCheckpointCommand(
        submission=_submission(),
        task_id="t1",
        actor_id="cx",
        checkpoint_id="cp1",
        stage="build",
        request_id="r1",
        attempt_id="a1",
        resume_token_ref="token-1",
        completed_units=("u1",),
        remaining_units=("u2",),
        expected_revision=3,
    )
    assert checkpoint.method == "coordination.task.checkpoint"
    assert checkpoint.encode_params() == {
        "task_id": "t1",
        "actor_id": "cx",
        "checkpoint_id": "cp1",
        "stage": "build",
        "request_id": "r1",
        "attempt_id": "a1",
        "resume_token_ref": "token-1",
        "completed_units": ("u1",),
        "remaining_units": ("u2",),
        "expected_revision": 3,
    }

    status = TaskStatusCommand(
        submission=_submission(),
        task_id="t1",
    )
    assert status.method == "coordination.task.status"
    assert status.encode_params() == {"task_id": "t1"}

    failover = TaskFailoverCommand(
        submission=_submission(),
        task_id="t1",
        to_actor_id="ag",
        reason="unavailable",
        expected_revision=4,
    )
    assert failover.method == "coordination.task.failover"
    assert failover.encode_params() == {
        "task_id": "t1",
        "to_actor_id": "ag",
        "reason": "unavailable",
        "expected_revision": 4,
    }

    propose = LessonProposeCommand(
        submission=_submission(),
        lesson_id="l1",
        title="Title",
        rule="Rule",
        category="cat",
        severity="high",
        proposer_id="cx",
        affected_peers=("ag",),
        scope_kind="room",
        workspace_id="w1",
        sticky=False,
        os=None,
        shell=None,
        task_types=None,
    )
    assert propose.method == "governance.lesson.propose"
    assert propose.encode_params() == {
        "lesson_id": "l1",
        "title": "Title",
        "rule": "Rule",
        "category": "cat",
        "severity": "high",
        "proposer_id": "cx",
        "affected_peers": ("ag",),
        "scope_kind": "room",
        "workspace_id": "w1",
        "sticky": False,
        "os": None,
        "shell": None,
        "task_types": None,
    }

    activate = LessonActivateCommand(
        submission=_submission(),
        lesson_id="l1",
        actor_id="cx",
        expected_revision=2,
    )
    assert activate.method == "governance.lesson.activate"
    assert activate.encode_params() == {
        "lesson_id": "l1",
        "actor_id": "cx",
        "expected_revision": 2,
    }

    retire = LessonRetireCommand(
        submission=_submission(),
        lesson_id="l1",
        actor_id="cx",
        reason="superseded",
        expected_revision=5,
    )
    assert retire.method == "governance.lesson.retire"
    assert retire.encode_params() == {
        "lesson_id": "l1",
        "actor_id": "cx",
        "reason": "superseded",
        "expected_revision": 5,
    }
