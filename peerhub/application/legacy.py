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
            
        return KnownLegacyActionNotBacked(
            legacy_action=call.action,
            target_method=target,
            ledger_status="INVENTORIED",
            reason="no PeerHub handler with semantic backing",
        )
