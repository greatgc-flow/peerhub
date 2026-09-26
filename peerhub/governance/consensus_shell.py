import dataclasses
import hashlib
from typing import Optional, Sequence, Dict, Any

from peerhub.governance.broker import GovernanceBroker
from peerhub.core.context import Clock, IdSource
from peerhub.governance.authorization import HealthIdentityPort, CredentialVerifier, AuthorizationGate, AuthorizationError
from peerhub.governance.authority_fence import AuthorityVersionStore, AuthorityFence, StaleAuthorityError
from peerhub.governance.contract import build_mutation_request, EffectIntent
from peerhub.core.errors import StaleRevisionError, InvalidMutationError
from peerhub.core.protocol import canonical_json_bytes
from peerhub.governance.policy_snapshot import ConfigurationError, freeze_policy_snapshot
from peerhub.governance.provenance import resolve_provenance
from peerhub.governance.candidate import Candidate, AckLedger
from peerhub.governance.consensus import ConsensusStateMachine, EvalContext, AckNackEvent
from peerhub.governance.proposal_policy import build_approval_submission, route_effect_kind
from peerhub.governance.contract import EffectOutcome
from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND

def candidate_state_hash(state: Dict[str, Any]) -> str:
    """Hash the immutable content being approved (never the mutable ACK ledger/phase)."""
    basis = {
        key: state.get(key)
        for key in ("policy_snapshot", "proposal", "participants", "frozen_authority_set")
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(basis)).hexdigest()


AUTHORITY_TARGET_ID = "system:authority-version"


class BrokerAuthorityVersionStore(AuthorityVersionStore):
    def __init__(self, broker: GovernanceBroker) -> None:
        self._broker = broker

    def read_version(self) -> int:
        target = self._broker.get_target("system:authority-version")
        if target is None:
            return 1
        return int(target.state.get("version", target.revision + 1))

    def read_version_in(self, unit: Any) -> int:
        """Read the live version inside the caller's (committing) transaction."""
        target = unit.get_target(AUTHORITY_TARGET_ID)
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
        authority_store: AuthorityVersionStore,
        effect_factories: Optional[Dict[str, Any]] = None,
    ) -> None:
        # action -> callable(state, round_id, actor_id) -> EffectIntent (approval effect)
        self._effect_factories = dict(effect_factories or {})
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
            "kind": "consensus-round",
            "round_id": round_id,
            "origin": prov_origin,
            "action": prov_action,
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

    def _process_event(
        self,
        round_id: str,
        actor_id: str,
        operation: str,
        event_factory,
        state_updater,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        phase_for_eval: Optional[Any] = None,
    ) -> Any:
        # A public command is identified by (operation, round, actor, client key):
        # a durable replay returns the stored receipt without re-evaluating.
        scoped_key = None
        if idempotency_key is not None:
            scoped_key = f"{round_id}:{actor_id}:{idempotency_key}"
            replay = self._broker.lookup_replay("consensus_shell", operation, scoped_key)
            if replay is not None:
                return replay

        target = self._broker.get_target(round_id)
        if not target:
            raise ValueError(f"Round not found: {round_id}")
            
        state = dict(target.state)
        phase = state.get("phase", "voting")
        if phase_for_eval is not None:
            phase = phase_for_eval(state)
        
        snapshot = state.get("policy_snapshot") or {}
        final_call_rule = snapshot.get("final_call_rule")
        mandatory_final_call = bool(snapshot.get("mandatory_final_call", False))
        if final_call_rule == "always":
            mandatory_final_call = True
            
        sm = ConsensusStateMachine(state=phase, final_call_rule=final_call_rule, mandatory_floors_hit=mandatory_final_call)
        gate = AuthorizationGate(
            verifier=self._verifier,
            health_port=self._health_port,
            state_machine=sm
        )
        
        frozen_auth_set = frozenset(state.get("frozen_authority_set", []))
        required_participants = frozenset(state.get("participants", []))
        
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
        
        event = event_factory(state, state_hash)
        
        try:
            res = gate.authorize_and_evaluate(
                ctx=ctx,
                event=event,
                credential_id=credential_id,
                verified_required=bool(state.get("verified_required", False)),
            )
        except AuthorizationError:
            raise
        except InvalidMutationError as e:
            if operation == "retraction" and phase == "approved":
                from peerhub.governance.consensus import TransitionResult
                res = TransitionResult(new_phase="approved")
            else:
                raise
        
        fence = AuthorityFence(self._authority_store)
        fence.check(expected_auth_ver)  # fast fail; the committing txn re-validates below

        state["phase"] = res.new_phase
        
        effect = state_updater(state, res, ctx, sm, ledger, c, fence)
        if effect is None:
            effect = EffectIntent(kind="consensus.noop", payload={})
            
        req = build_mutation_request(
            self._ids,
            id_prefix=operation[:3],
            client_id="consensus_shell",
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=actor_id,
            operation=operation,
            desired_state=state,
            effect_intent=effect
        )
        if scoped_key is not None:
            req = dataclasses.replace(req, idempotency_key=scoped_key)
        submission = self._broker.submit(
            req, precondition=self._authority_precondition(expected_auth_ver)
        )
        if operation == "retraction" and state["phase"] == "approved":
            # Post-authorization retraction: recorded, and future work is fenced.
            fence.bump_for("revocation")
        return submission

    def _approval_effect(self, state: Dict[str, Any], round_id: str, actor_id: str) -> EffectIntent:
        """Approval effect for this round's action (default: consensus.resolved)."""
        action = (state.get("policy_snapshot") or {}).get("action") or state.get("action")
        factory = self._effect_factories.get(action)
        if factory is not None:
            return factory(state, round_id, actor_id)
        candidate = candidate_state_hash(state)
        approved_snapshot = {"round_id": round_id, "hash": candidate}
        intent = {
            "snapshot_hash": candidate,
            "round_id": round_id,
            "outcome": "approved",
            "resolved_by": actor_id,
            "final_call_complete": state.get("phase_before_approval") == "final_call",
        }
        sub = build_approval_submission(approved_snapshot, intent)
        return EffectIntent(kind="consensus.resolved", payload=sub.effect_outbox_entry)

    def _resolution_effect(
        self, state: Dict[str, Any], round_id: str, actor_id: str, outcome: str, basis: str
    ) -> EffectIntent:
        if outcome == "approved":
            return self._approval_effect(state, round_id, actor_id)
        return EffectIntent(
            kind="consensus.resolved",
            payload={
                "round_id": round_id,
                "outcome": outcome,
                "basis": basis,
                "resolved_by": actor_id,
                "snapshot_hash": candidate_state_hash(state),
            },
        )

    def reject_on_dissent(
        self, round_id: str, actor_id: str, basis: str, idempotency_key: Optional[str] = None
    ) -> Any:
        """Reject an open round after verifying a stored eligible dissent (design 6.1 rule 5)."""
        from peerhub.governance.consensus import ResolutionEvent

        def event_factory(state, state_hash):
            eligible = set(state.get("participants", []))
            votes = state.get("votes", {}) or {}
            if not any(
                voter in eligible and (vote or {}).get("choice") in ("disagree", "block")
                for voter, vote in votes.items()
            ):
                raise InvalidMutationError("no stored eligible dissent to reject on")
            return ResolutionEvent(outcome="rejected", basis="eligible_dissent")

        def updater(state, res, ctx, sm, ledger, c, fence):
            state["resolution"] = {"outcome": "rejected", "basis": basis, "resolved_by": actor_id}
            return self._resolution_effect(state, round_id, actor_id, "rejected", basis)

        return self._process_event(
            round_id, actor_id, "reject_on_dissent", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def request_escalation(
        self, round_id: str, reason: str, requester_id: str, tier: int, required_authority: str,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        from peerhub.governance.consensus import EscalationEvent

        def event_factory(state, state_hash):
            return EscalationEvent(
                source_phases=[state.get("phase", "voting")],
                replacement_deadline=self._clock.now() + 1800,
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            state["escalation"] = {
                "reason": reason,
                "requested_by": requester_id,
                "tier": tier,
                "required_authority": required_authority,
                "requested_at": self._clock.now(),
            }

        return self._process_event(
            round_id, requester_id, "request_escalation", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def resolve(
        self, round_id: str, outcome: str, resolved_by: str, basis: str,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """Resolve an escalated (or quorum_reached) round to approved/rejected."""
        from peerhub.governance.consensus import ResolutionEvent

        def event_factory(state, state_hash):
            return ResolutionEvent(outcome=outcome)

        def updater(state, res, ctx, sm, ledger, c, fence):
            state["resolution"] = {"outcome": outcome, "basis": basis, "resolved_by": resolved_by}
            if outcome == "approved":
                state["phase_before_approval"] = "escalated"
            return self._resolution_effect(state, round_id, resolved_by, outcome, basis)

        return self._process_event(
            round_id, resolved_by, "resolve", event_factory, updater,
            idempotency_key=idempotency_key,
            phase_for_eval=lambda st: "escalated" if st.get("escalation") else st.get("phase", "voting"),
        )

    def final_call_nack(
        self, round_id: str, actor_id: str, nack_type: str = "block",
        credential_id: Optional[str] = None, idempotency_key: Optional[str] = None,
    ) -> Any:
        """NACK during Final Call: block (holds the barrier), cosmetic (logged) or terminal_rejection."""
        from peerhub.governance.consensus import AckNackEvent

        def event_factory(state, state_hash):
            return AckNackEvent(
                candidate_id=state_hash, actor=actor_id, proof=credential_id or "", nack_type=nack_type
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.barrier_held:
                state["barrier_held"] = True
            if res.cosmetic_logged:
                state["cosmetic_nacks"] = list(state.get("cosmetic_nacks", [])) + [actor_id]
            if res.terminal_rejection:
                state["resolution"] = {"outcome": "rejected", "basis": "terminal_rejection", "resolved_by": actor_id}
                return self._resolution_effect(state, round_id, actor_id, "rejected", "terminal_rejection")

        return self._process_event(
            round_id, actor_id, "final_call_nack", event_factory, updater,
            credential_id=credential_id, idempotency_key=idempotency_key,
        )

    def _authority_precondition(self, expected_version: int):
        """Authority validation that runs inside the committing broker txn."""
        reader = getattr(self._authority_store, "read_version_in", None)
        if reader is None:
            return None

        def check(unit: Any) -> None:
            live = reader(unit)
            if live != expected_version:
                raise StaleAuthorityError(f"Expected {expected_version}, got {live}")

        return check

    def final_call_ack(
        self,
        round_id: str,
        actor_id: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        from peerhub.governance.consensus import AckNackEvent
        
        def event_factory(state, state_hash):
            return AckNackEvent(
                candidate_id=state_hash,
                actor=actor_id,
                proof=credential_id or "",
                nack_type=None,
            )
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.ack_recorded:
                ledger.bind_ack(c, actor_id)
                
            state["ack_ledger"] = {c.target_state_hash: list(ledger.acks_for(c))}
            
            if res.new_phase == "approved":
                state["phase_before_approval"] = "final_call"
                return self._approval_effect(state, round_id, actor_id)

        return self._process_event(
            round_id, actor_id, "final_call_ack", event_factory, updater, expected_revision, credential_id,
            idempotency_key,
        )

    def cast_vote(
        self,
        round_id: str,
        actor_id: str,
        choice: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        from peerhub.governance.consensus import VoteEvent, QuorumMetEvent, ResolutionEvent
        
        def event_factory(state, state_hash):
            return VoteEvent(actor=actor_id, choice=choice, credential=credential_id)
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            votes = dict(state.get("votes", {}))
            vote_record = {"choice": choice}
            if res.dissent_obligation_added:
                vote_record["dissent_obligation"] = True
            votes[actor_id] = vote_record
            state["votes"] = votes
            
            required_votes = (state.get("policy_snapshot") or {}).get("required_votes")
            if type(required_votes) is not int or required_votes < 1:
                raise ConfigurationError("policy snapshot has no valid required_votes")

            agreements = sum(1 for v in votes.values() if v.get("choice") == "agree")
            if agreements >= required_votes:
                q_res = sm.evaluate(ctx, QuorumMetEvent(agreement_count=agreements))
                state["phase"] = q_res.new_phase
                if q_res.new_phase == "quorum_reached":
                    # No mandatory Final Call: resolve immediately through the core so
                    # the approval and its effect commit in this one submission.
                    resolver = ConsensusStateMachine(state="quorum_reached")
                    r_res = resolver.evaluate(ctx, ResolutionEvent(outcome="approved"))
                    state["phase"] = r_res.new_phase
                    if r_res.new_phase == "approved":
                        state["phase_before_approval"] = "quorum_reached"
                        return self._approval_effect(state, round_id, actor_id)

        return self._process_event(
            round_id, actor_id, "cast_vote", event_factory, updater, expected_revision, credential_id,
            idempotency_key,
        )

    def correction(self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None) -> Any:
        from peerhub.governance.consensus import CorrectionEvent
        
        def event_factory(state, state_hash):
            return CorrectionEvent(actor=actor_id)
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.acks_dropped:
                state["ack_ledger"] = {}

        return self._process_event(
            round_id, actor_id, "correction", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def retraction(self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None) -> Any:
        from peerhub.governance.consensus import RetractionEvent
        
        def event_factory(state, state_hash):
            return RetractionEvent(candidate_id=state_hash, actor=actor_id, proof="")
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            if state["phase"] == "approved":
                # The fence bump happens right after the commit (see _process_event)
                # so this command's own version check is not invalidated by it.
                state["revocations"] = list(state.get("revocations", [])) + ["RevocationRecorded"]
            elif res.acks_dropped:
                state["ack_ledger"] = {}

        return self._process_event(
            round_id, actor_id, "retraction", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def mark_timeout(self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None) -> Any:
        from peerhub.governance.consensus import TimeoutEvent
        
        def event_factory(state, state_hash):
            return TimeoutEvent(requester=actor_id, deadline=self._clock.now() + 1800)
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.evidence_recorded:
                state["timeout_evidence"] = {"actor_id": actor_id, "recorded_at": self._clock.now()}

        return self._process_event(
            round_id, actor_id, "mark_timeout", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def abandon(self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None) -> Any:
        from peerhub.governance.consensus import AbandonEvent
        
        def event_factory(state, state_hash):
            return AbandonEvent(reason="abandoned", requesting_actor=actor_id)
            
        def updater(state, res, ctx, sm, ledger, c, fence):
            pass

        return self._process_event(
            round_id, actor_id, "abandon", event_factory, updater,
            idempotency_key=idempotency_key,
        )

    def process_consensus_effects(self, round_id: str, *, owner_id: Optional[str] = None) -> tuple:
        """Claim and receipt this round's pending consensus effects as the V2 worker.

        Only the ``consensus-v2:`` owner prefix passes the activation fence.
        The ratified-invariant effect is left for its exclusive materializer;
        an unknown effect kind holds (EffectRoutingError) before anything is
        claimed.
        """
        owner = owner_id or f"consensus-v2:{round_id}"
        if not owner.startswith("consensus-v2:"):
            raise ValueError("V2 consensus worker owner must start with 'consensus-v2:'")
        matching = []
        for pending in self._broker.recover_pending_effects():
            payload = pending.event.payload
            effect_payload = payload.get("effect_payload")
            if not (
                payload.get("target_id") == round_id
                or (isinstance(effect_payload, dict) and effect_payload.get("round_id") == round_id)
            ):
                continue
            kind = payload.get("effect_kind")
            if kind == "consensus.noop" or kind == RATIFIED_INVARIANT_EFFECT_KIND:
                continue
            route_effect_kind(kind, "generic")  # raises EffectRoutingError (hold) if unknown
            matching.append(pending)
        receipts = []
        for pending in matching:
            attempt_id = self._ids.new_id("effect-attempt")
            claimed = self._broker.claim_effect(
                pending.event.event_id, owner_id=owner, attempt_id=attempt_id
            )
            receipts.append(
                self._broker.record_effect_result(
                    claimed.event_id,
                    owner_id=owner,
                    attempt_id=attempt_id,
                    outcome=EffectOutcome.EFFECT_SUCCEEDED,
                    evidence_refs=(f"consensus-round:{round_id}",),
                )
            )
        return tuple(receipts)
