"""Pure legacy action translation mapping."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Generic, TypeVar, Any

from peerhub.core.protocol import JsonValue
from peerhub.application.commands import (
    Command,
    SubmissionMetadata,
)

R = TypeVar("R")


def _string_tuple(value: JsonValue | None) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _optional_int(value: JsonValue | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class LegacyActionCall:
    action: str
    arguments: Mapping[str, JsonValue]


@dataclass(frozen=True)
class TranslatedCommand(Generic[R]):
    command: Command[R]


@dataclass(frozen=True)
class KnownLegacyActionNotBacked:
    legacy_action: str
    target_method: str
    ledger_status: str
    reason: str


@dataclass(frozen=True)
class InvalidLegacyArguments:
    action: str
    reason: str


@dataclass(frozen=True)
class UnknownLegacyAction:
    action: str


LegacyTranslationOutcome = (
    TranslatedCommand[object]
    | KnownLegacyActionNotBacked
    | InvalidLegacyArguments
    | UnknownLegacyAction
)


LEGACY_CATALOG = {
    'init-session': 'coordination.session.open',
    'end-session': 'coordination.session.close',
    'send': 'coordination.message.send',
    'broadcast': 'coordination.message.broadcast',
    'mark-read': 'coordination.message.mark_read',
    'append-log': 'governance.audit.append',
    'archive-file': 'governance.artifact.archive',
    'update-status': 'coordination.mission.update',
    'check': 'coordination.message.check',
    'status': 'peerhub.status.read',
    'check-gate': 'health.admission.check',
    'ask': 'dispatch.submit',
    'ask-all': 'dispatch.submit_many',
    'ask-coordinator': 'dispatch.submit_coordinator',
    'consensus-propose': 'consensus.round.propose',
    'consensus-vote': 'consensus.vote.cast',
    'consensus-check': 'consensus.round.read',
    'consensus-sweep': 'consensus.round.sweep',
    'register-node': 'configuration.instance.register',
    'list-nodes': 'configuration.instance.list',
    'health-update': 'health.evidence.record',
    'health-check': 'health.projection.read',
    'peer-status': 'health.instance.status',
    'context-fill': 'coordination.context.fill',
    'checkpoint': 'coordination.checkpoint.create',
    'peer-quarantine': 'health.admission.quarantine',
    'peer-recover': 'health.recovery.authorize_probe',
    'new-topic': 'coordination.topic.create',
    'clear-room': 'coordination.room.clear',
    'preflight': 'peerhub.preflight',
    'context-hash': 'peerhub.context.hash',
    'report-error': 'telemetry.error.record',
    'feedback-add': 'governance.feedback.create',
    'feedback-list': 'governance.feedback.list',
    'feedback-resolve': 'governance.feedback.resolve',
    'artifact-claim': 'governance.artifact.claim',
    'artifact-status': 'governance.artifact.status',
    'artifact-finalize': 'governance.artifact.finalize',
    'leader-yield': 'routing.leadership.yield',
    'leader-claim': 'routing.leadership.claim',
    'elect-leader': 'routing.leadership.elect',
    'discover': 'routing.candidate.discover',
    'assign-role': 'coordination.role.assign',
    'release-role': 'coordination.role.release',
    'role-status': 'coordination.role.status',
    'health-precheck': 'health.admission.precheck',
    'health-sweep': 'health.projection.sweep',
    'freshness-sweep': 'telemetry.freshness.sweep',
    'terminal-handoff': 'coordination.terminal.handoff',
    'terminal-duty-sweep': 'coordination.terminal.duty_sweep',
    'terminal-heartbeat': 'coordination.terminal.heartbeat',
    'terminal-close': 'coordination.terminal.close',
    'append-handoff': 'coordination.handoff.append',
    'task-checkpoint': 'coordination.task.checkpoint',
    'task-status': 'coordination.task.status',
    'task-failover': 'coordination.task.failover',
    'approval-request': 'governance.approval.request',
    'file-lock': 'governance.lock.acquire',
    'file-unlock': 'governance.lock.release',
    'lock-status': 'governance.lock.status',
    'profile-validate': 'configuration.profile.validate',
    'lease-status': 'dispatch.lease.status',
    'lease-sweep': 'dispatch.lease.sweep',
    'model-status': 'configuration.model.status',
    'transient-scan': 'telemetry.transient.scan',
    'directive-add': 'host.directive.add',
    'directive-list': 'host.directive.list',
    'directive-clear': 'host.directive.clear',
    'lessons-list': 'governance.lesson.list',
    'lessons-propose': 'governance.lesson.propose',
    'lessons-activate': 'governance.lesson.activate',
    'lessons-retire': 'governance.lesson.retire',
    'lesson-broadcast': 'coordination.lesson.broadcast',
    'lesson-sweep': 'governance.lesson.sweep',
    'lesson-inject': 'host.lesson.inject',
    'thread-new': 'coordination.thread.create',
    'thread-append': 'coordination.thread.append',
    'thread-react': 'coordination.thread.react',
    'thread-promote': 'coordination.thread.promote',
    'alert-raise': 'coordination.alert.raise',
    'proposal-add': 'governance.proposal.create',
    'proposal-vote': 'governance.proposal.vote',
    'proposal-list': 'governance.proposal.list',
    'broker-submit': 'governance.mutation.submit',
    'broker-drain': 'governance.effect.drain',
    'broker-status': 'governance.mutation.status',
    'update-signatures': 'peerhub.signature.update',
    'arbiter-review': 'consensus.arbiter.review',
    'credit-status': 'host.credit.status',
    'credit-consume': 'host.credit.consume'
}


@dataclass(frozen=True, slots=True)
class SubmitDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitManyDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_many"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitCoordinatorDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_coordinator"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusProposeCommand(Command[Any]):
    """Wire command only; Client sends it to the application boundary."""
    method: ClassVar[str] = "consensus.round.propose"
    submission: SubmissionMetadata
    round_id: str
    title: str
    question: str
    body: str
    proposer_id: str
    required_participants: tuple[str, ...]
    eligible_participants: tuple[str, ...]
    risk: str
    source_hash: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id, "title": self.title, "question": self.question, "body": self.body, "proposer_id": self.proposer_id, "required_participants": self.required_participants, "eligible_participants": self.eligible_participants, "risk": self.risk, "source_hash": self.source_hash}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusVoteCommand(Command[Any]):
    method: ClassVar[str] = "consensus.vote.cast"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    choice: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id, "actor_id": self.actor_id, "choice": self.choice}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusCheckCommand(Command[Any]):
    method: ClassVar[str] = "consensus.round.read"
    submission: SubmissionMetadata
    round_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskCheckpointCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.checkpoint"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    checkpoint_id: str
    stage: str
    request_id: str
    attempt_id: str
    resume_token_ref: str | None
    completed_units: tuple[str, ...]
    remaining_units: tuple[str, ...]
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id, "actor_id": self.actor_id, "checkpoint_id": self.checkpoint_id, "stage": self.stage, "request_id": self.request_id, "attempt_id": self.attempt_id, "resume_token_ref": self.resume_token_ref, "completed_units": self.completed_units, "remaining_units": self.remaining_units, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskStatusCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.status"
    submission: SubmissionMetadata
    task_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskFailoverCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.failover"
    submission: SubmissionMetadata
    task_id: str
    to_actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id, "to_actor_id": self.to_actor_id, "reason": self.reason, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonProposeCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.propose"
    submission: SubmissionMetadata
    lesson_id: str
    title: str
    rule: str
    category: str
    severity: str
    proposer_id: str
    affected_peers: tuple[str, ...]
    scope_kind: str
    workspace_id: str | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "title": self.title, "rule": self.rule, "category": self.category, "severity": self.severity, "proposer_id": self.proposer_id, "affected_peers": self.affected_peers, "scope_kind": self.scope_kind, "workspace_id": self.workspace_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonActivateCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.activate"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonRetireCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.retire"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "reason": self.reason, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


class LegacyTranslator:
    def translate(
        self,
        call: LegacyActionCall,
        submission: SubmissionMetadata,
    ) -> LegacyTranslationOutcome:
        if call.action not in LEGACY_CATALOG:
            return UnknownLegacyAction(action=call.action)
            
        target = LEGACY_CATALOG[call.action]
        
        if call.action == "ask":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitDispatch(submission=submission, prompt=prompt))
        if call.action == "ask-all":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitManyDispatch(submission=submission, prompt=prompt))
        if call.action == "ask-coordinator":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitCoordinatorDispatch(submission=submission, prompt=prompt))
        if call.action == "consensus-propose":
            return TranslatedCommand(command=ConsensusProposeCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
                title=str(call.arguments.get("title", "")),
                question=str(call.arguments.get("question", "")),
                body=str(call.arguments.get("body", "")),
                proposer_id=str(call.arguments.get("proposer_id", "")),
                required_participants=_string_tuple(call.arguments.get("required_participants")),
                eligible_participants=_string_tuple(call.arguments.get("eligible_participants")),
                risk=str(call.arguments.get("risk", "normal")),
                source_hash=str(call.arguments.get("source_hash", "")),
            ))
        if call.action == "consensus-vote":
            return TranslatedCommand(command=ConsensusVoteCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
                actor_id=str(call.arguments.get("actor_id", "")),
                choice=str(call.arguments.get("choice", "")),
            ))
        if call.action == "consensus-check":
            return TranslatedCommand(command=ConsensusCheckCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
            ))
        if call.action == "task-checkpoint":
            return TranslatedCommand(command=TaskCheckpointCommand(
                submission=submission, task_id=str(call.arguments.get("task_id", "")), actor_id=str(call.arguments.get("actor_id", "")), checkpoint_id=str(call.arguments.get("checkpoint_id", "")), stage=str(call.arguments.get("stage", "")), request_id=str(call.arguments.get("request_id", "")), attempt_id=str(call.arguments.get("attempt_id", "")), resume_token_ref=str(call.arguments["resume_token_ref"]) if call.arguments.get("resume_token_ref") is not None else None, completed_units=_string_tuple(call.arguments.get("completed_units")), remaining_units=_string_tuple(call.arguments.get("remaining_units")), expected_revision=_optional_int(call.arguments.get("expected_revision")),
            ))
        if call.action == "task-status":
            return TranslatedCommand(command=TaskStatusCommand(submission=submission, task_id=str(call.arguments.get("task_id", ""))))
        if call.action == "task-failover":
            return TranslatedCommand(command=TaskFailoverCommand(submission=submission, task_id=str(call.arguments.get("task_id", "")), to_actor_id=str(call.arguments.get("to_actor_id", "")), reason=str(call.arguments.get("reason", "")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))
        if call.action == "lessons-propose":
            return TranslatedCommand(command=LessonProposeCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), title=str(call.arguments.get("title", "")), rule=str(call.arguments.get("rule", "")), category=str(call.arguments.get("category", "")), severity=str(call.arguments.get("severity", "")), proposer_id=str(call.arguments.get("proposer_id", "")), affected_peers=_string_tuple(call.arguments.get("affected_peers")), scope_kind=str(call.arguments.get("scope_kind", "global")), workspace_id=str(call.arguments["workspace_id"]) if call.arguments.get("workspace_id") is not None else None))
        if call.action == "lessons-activate":
            return TranslatedCommand(command=LessonActivateCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), actor_id=str(call.arguments.get("actor_id", "")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))
        if call.action == "lessons-retire":
            return TranslatedCommand(command=LessonRetireCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), actor_id=str(call.arguments.get("actor_id", "")), reason=str(call.arguments.get("reason", "MANUAL")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))
            
        return KnownLegacyActionNotBacked(
            legacy_action=call.action,
            target_method=target,
            ledger_status="INVENTORIED",
            reason="no PeerHub handler with semantic backing",
        )
