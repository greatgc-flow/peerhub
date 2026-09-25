"""Consensus authorization interface definitions (RED)."""

from typing import Protocol, Sequence

from peerhub.core.errors import InvalidMutationError
from peerhub.governance.consensus import (
    ConsensusStateMachine,
    ConsensusEvent,
    EvalContext,
    CredentialVerifier,
    TransitionResult,
)

class AuthorizationError(InvalidMutationError):
    """Raised when an event fails authorization gating."""
    def __init__(self, message: str) -> None:
        super().__init__(message)


class HealthIdentityPort(Protocol):
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool: ...


class AuthorizationGate:
    def __init__(
        self, 
        verifier: CredentialVerifier | None,
        health_port: HealthIdentityPort,
        state_machine: ConsensusStateMachine
    ):
        self.verifier = verifier
        self.health_port = health_port
        self.state_machine = state_machine

    def authorize_and_evaluate(
        self,
        ctx: EvalContext,
        event: ConsensusEvent,
        credential_id: str | None,
        verified_required: bool,
    ) -> TransitionResult:
        raise NotImplementedError("RED phase")
        
    def authorize_final_call_electorate(
        self,
        eligible_participants: Sequence[str],
        evaluated_at: int,
    ) -> bool:
        raise NotImplementedError("RED phase")
