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
        # Check credential
        if credential_id is not None:
            if self.verifier is None:
                raise AuthorizationError("Credential presented but no verifier configured.")
            try:
                is_valid = self.verifier(credential_id=credential_id, claimed_actor_id=ctx.caller_identity)
            except Exception as e:
                raise AuthorizationError(f"Verifier failure: {e}") from e
            if not is_valid:
                raise AuthorizationError("Invalid credential.")
        elif verified_required:
            raise AuthorizationError("Credential absent but required.")

        # Check electorate gate
        if ctx.caller_identity not in ctx.frozen_authority_set:
            raise AuthorizationError("Actor not in frozen authority set.")

        # Check health gate
        try:
            is_healthy = self.health_port.check_health_gate(ctx.caller_identity, int(ctx.current_timestamp))
        except Exception as e:
            raise AuthorizationError(f"health service error: {e}") from e
            
        if not is_healthy:
            raise AuthorizationError("Health check failed.")

        return self.state_machine.evaluate(ctx, event)
        
    def authorize_final_call_electorate(
        self,
        eligible_participants: Sequence[str],
        evaluated_at: int,
    ) -> bool:
        for participant in eligible_participants:
            try:
                is_healthy = self.health_port.check_health_gate(participant, evaluated_at)
            except Exception as e:
                raise AuthorizationError(f"health service error: {e}") from e
            if not is_healthy:
                return False
        return True
