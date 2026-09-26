import hashlib
from typing import Optional, Sequence, Dict, Any

from peerhub.governance.broker import GovernanceBroker
from peerhub.core.context import Clock, IdSource
from peerhub.governance.authorization import HealthIdentityPort, CredentialVerifier, AuthorizationGate, AuthorizationError
from peerhub.governance.authority_fence import AuthorityVersionStore, AuthorityFence, StaleAuthorityError
from peerhub.governance.contract import build_mutation_request, EffectIntent
from peerhub.core.errors import StaleRevisionError
from peerhub.core.protocol import canonical_json_bytes
from peerhub.governance.policy_snapshot import freeze_policy_snapshot
from peerhub.governance.provenance import resolve_provenance
from peerhub.governance.candidate import Candidate, AckLedger
from peerhub.governance.consensus import ConsensusStateMachine, EvalContext, AckNackEvent
from peerhub.governance.proposal_policy import build_approval_submission

def candidate_state_hash(state: Dict[str, Any]) -> str:
    """Hash the immutable content being approved (never the mutable ACK ledger/phase)."""
    basis = {
        key: state.get(key)
        for key in ("policy_snapshot", "proposal", "participants", "frozen_authority_set")
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(basis)).hexdigest()


class BrokerAuthorityVersionStore(AuthorityVersionStore):
    def __init__(self, broker: GovernanceBroker) -> None:
        self._broker = broker

    def read_version(self) -> int:
        target = self._broker.get_target("system:authority-version")
        if target is None:
            return 1
        return int(target.state.get("version", target.revision + 1))

    def increment(self) -> int:
        target = self._broker.get_target("system:authority-version")
        if target is None:
            expected_rev = 0
            new_version = 2
        else:
            expected_rev = target.revision
            new_version = int(target.state.get("version", target.revision + 1)) + 1
            
        req = build_mutation_request(
            self._broker.ids,
            id_prefix="auth-ver",
            client_id="authority-store",
            target_id="system:authority-version",
            expected_revision=expected_rev,
            actor_id="system",
            operation="increment",
            desired_state={"version": new_version},
            effect_intent=EffectIntent(kind="consensus.noop", payload={})
        )
        try:
            self._broker.submit(req)
            return new_version
        except StaleRevisionError as e:
            raise StaleAuthorityError("Lost CAS race when incrementing authority version") from e


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
        import dataclasses
        prov_origin, prov_action = resolve_provenance(origin, action)
        policy_snapshot = freeze_policy_snapshot(
            origin=prov_origin,
            action=prov_action,
            config=config,
            risk=risk
        )
        
        expected_authority_version = self._authority_store.read_version()
        
        state = {
            "schema": "peerhub.consensus-round.v2",
            "phase": "voting",
            "policy_snapshot": dataclasses.asdict(policy_snapshot),
            "participants": list(eligible_participants),
            "frozen_authority_set": list(eligible_participants),
            "expected_authority_version": expected_authority_version,
            "ack_ledger": {},
            "proposal": {
                "title": title,
                "question": question,
                "body": body,
                "proposer_id": proposer_id,
                "source_hash": source_hash,
                "required_participants": list(required_participants),
            }
        }
        
        req = build_mutation_request(
            self._ids,
            id_prefix="prop",
            client_id="consensus_shell",
            target_id=round_id,
            expected_revision=0,
            actor_id=proposer_id,
            operation="propose_v2",
            desired_state=state,
            effect_intent=EffectIntent(kind="consensus.noop", payload={})
        )
        return self._broker.submit(req)

    def final_call_ack(
        self,
        round_id: str,
        actor_id: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None
    ) -> Any:
        target = self._broker.get_target(round_id)
        if not target:
            raise ValueError(f"Round not found: {round_id}")
            
        state = dict(target.state)
        phase = state.get("phase", "voting")
        
        # 1. AuthorizationGate
        sm = ConsensusStateMachine(state=phase)
        gate = AuthorizationGate(
            verifier=self._verifier,
            health_port=self._health_port,
            state_machine=sm
        )
        
        frozen_auth_set = frozenset(state.get("frozen_authority_set", []))
        required_participants = frozenset(state.get("participants", []))
        
        # Hydrate AckLedger
        ledger = AckLedger()
        ack_ledger_state = state.get("ack_ledger", {})
        state_hash = candidate_state_hash(state)
        expected_auth_ver = state.get("expected_authority_version", 1)

        c = Candidate(round_id, int(state.get("candidate_revision", 0)), state_hash)
        
        for p in ack_ledger_state.get(state_hash, []):
            ledger.bind_ack(c, p)
            
        ctx = EvalContext(
            current_timestamp=self._clock.now(),
            caller_identity=actor_id,
            frozen_authority_set=frozen_auth_set,
            current_candidate=c,
            bound_ack_participants=ledger.acks_for(c),
            required_participants=required_participants,
        )
        
        event = AckNackEvent(
            candidate_id=state_hash,
            actor=actor_id,
            proof=credential_id or "",
            nack_type=None,
        )
        
        res = gate.authorize_and_evaluate(
            ctx=ctx,
            event=event,
            credential_id=credential_id,
            verified_required=bool(state.get("verified_required", False)),
        )
        
        # 2. AuthorityFence check
        fence = AuthorityFence(self._authority_store)
        fence.check(expected_auth_ver)

        
        if res.ack_recorded:
            ledger.bind_ack(c, actor_id)
            
        state["phase"] = res.new_phase
        state["ack_ledger"] = {state_hash: list(ledger.acks_for(c))}
        
        if res.new_phase == "approved":
            approved_snapshot = {"round_id": round_id, "hash": state_hash}
            effect_intent_dict = {
                "snapshot_hash": state_hash,
                "round_id": round_id,
                "outcome": "approved",
                "resolved_by": actor_id,
                "final_call_complete": True,
            }

            sub = build_approval_submission(approved_snapshot, effect_intent_dict)
            effect = EffectIntent(kind="consensus.resolved", payload=sub.effect_outbox_entry)
            
            req = build_mutation_request(
                self._ids,
                id_prefix="ack",
                client_id="consensus_shell",
                target_id=round_id,
                expected_revision=target.revision if expected_revision is None else expected_revision,
                actor_id=actor_id,
                operation="final_call_ack",
                desired_state=state,
                effect_intent=effect
            )
            return self._broker.submit(req)
        else:
            req = build_mutation_request(
                self._ids,
                id_prefix="ack",
                client_id="consensus_shell",
                target_id=round_id,
                expected_revision=target.revision if expected_revision is None else expected_revision,
                actor_id=actor_id,
                operation="final_call_ack",
                desired_state=state,
                effect_intent=EffectIntent(kind="consensus.noop", payload={})
            )
            return self._broker.submit(req)

    def cast_vote(
        self,
        round_id: str,
        actor_id: str,
        choice: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None
    ) -> Any:
        raise NotImplementedError("RED phase")

    def correction(self, round_id: str, actor_id: str) -> Any:
        raise NotImplementedError("RED phase")

    def retraction(self, round_id: str, actor_id: str) -> Any:
        raise NotImplementedError("RED phase")

    def mark_timeout(self, round_id: str, actor_id: str) -> Any:
        raise NotImplementedError("RED phase")

    def abandon(self, round_id: str, actor_id: str) -> Any:
        raise NotImplementedError("RED phase")
