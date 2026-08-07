"""Embedded client for PeerHub commands."""

from typing import TypeVar

from peerhub.core.ports import RequestContext
from peerhub.core.protocol import (
    CommandEnvelope,
    CommandOutcome,
    CommandSuccess,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
)
from peerhub.application.api import ApplicationAPI
from peerhub.application.commands import Command

R = TypeVar("R")


class Client:
    def __init__(
        self,
        submitter: ApplicationAPI,
        *,
        caller: RequestContext,
    ) -> None:
        self._submitter = submitter
        self._caller = caller

    def submit(
        self,
        command: Command[R],
        /,
    ) -> CommandOutcome[R]:
        envelope = CommandEnvelope(
            protocol_major=PROTOCOL_MAJOR,
            protocol_minor=PROTOCOL_MINOR,
            schema_version=SCHEMA_VERSION,
            client_request_id=command.submission.client_request_id,
            correlation_id=command.submission.correlation_id,
            client_id=command.submission.client_id,
            actor_id=command.submission.actor_id,
            scope=command.submission.scope,
            method=command.method,
            params=command.encode_params(),
            idempotency_key=command.submission.idempotency_key,
            expected_policy_revision=command.submission.expected_policy_revision,
            expected_configuration_revision=command.submission.expected_configuration_revision,
            client_timestamp=command.submission.client_timestamp,
        )

        outcome = self._submitter.submit(envelope, caller=self._caller)

        if outcome.ok:
            result = command.decode_result(outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            return CommandSuccess(
                ok=True,
                protocol_major=outcome.protocol_major,
                protocol_minor=outcome.protocol_minor,
                schema_version=outcome.schema_version,
                diagnostic_id=outcome.diagnostic_id,
                correlation_id=outcome.correlation_id,  # pyright: ignore[reportArgumentType]
                command_id=outcome.command_id,
                state=outcome.state,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                receipt_ref=outcome.receipt_ref,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                policy_revision=outcome.policy_revision,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                configuration_revision=outcome.configuration_revision,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                idempotency=outcome.idempotency,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                result=result,
            )
        else:
            return outcome  # pyright: ignore[reportReturnType]
