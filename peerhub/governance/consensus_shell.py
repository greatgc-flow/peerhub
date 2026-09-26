from typing import Optional, Sequence, Dict, Any

from peerhub.governance.broker import GovernanceBroker
from peerhub.core.context import Clock, IdSource
from peerhub.governance.authorization import HealthIdentityPort, CredentialVerifier
from peerhub.governance.authority_fence import AuthorityVersionStore


class BrokerAuthorityVersionStore(AuthorityVersionStore):
    def __init__(self, broker: GovernanceBroker) -> None:
        self._broker = broker

    def read_version(self) -> int:
        raise NotImplementedError("RED phase")

    def increment(self) -> int:
        raise NotImplementedError("RED phase")


class ConsensusShell:
    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        clock: Clock,
        ids: IdSource,
        verifier: Optional[CredentialVerifier],
        health_port: HealthIdentityPort,
        authority_store: AuthorityVersionStore
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids
        self._verifier = verifier
        self._health_port = health_port
        self._authority_store = authority_store

    def propose_v2(
        self,
        round_id: str,
        title: str,
        question: str,
        body: str,
        proposer_id: str,
        required_participants: Sequence[str],
        eligible_participants: Sequence[str],
        risk: str,
        source_hash: str,
        config: Dict[str, Any],
        origin: Optional[str] = None,
        action: Optional[str] = None
    ) -> Any:
        raise NotImplementedError("RED phase")

    def final_call_ack(
        self,
        round_id: str,
        actor_id: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None
    ) -> Any:
        raise NotImplementedError("RED phase")
