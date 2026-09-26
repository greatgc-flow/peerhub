"""Imperative shell of the V2 consensus engine.

Every mutation runs the same pipeline: replay check -> terminal guard ->
AuthorizationGate -> pure ``ConsensusStateMachine.evaluate`` -> authority fence
-> ONE broker transaction (state + receipt + effect [+ authority bump]) whose
precondition re-validates the authority version inside the committing txn.
"""

import dataclasses
import hashlib
from typing import Any, Callable, Dict, Optional, Sequence

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    IdempotencyPayloadMismatchError,
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.protocol import canonical_json_bytes
from peerhub.governance.authority_fence import (
    AuthorityFence,
    AuthorityVersionStore,
    StaleAuthorityError,
)
from peerhub.governance.authorization import (
    AuthorizationError,
    AuthorizationGate,
    CredentialVerifier,
    HealthIdentityPort,
)
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.candidate import AckLedger, Candidate, apply_retraction
from peerhub.governance.consensus import ConsensusStateMachine, EvalContext
from peerhub.governance.contract import (
    EffectIntent,
    EffectOutcome,
    build_mutation_request,
)
from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND
from peerhub.governance.policy_snapshot import (
    ConfigurationError,
    decode_policy_snapshot,
    freeze_policy_snapshot,
)
from peerhub.governance.proposal_policy import build_approval_submission, route_effect_kind
from peerhub.governance.provenance import resolve_provenance

AUTHORITY_TARGET_ID = "system:authority-version"
TERMINAL_PHASES = ("approved", "rejected", "abandoned")
COMMAND_LOG_LIMIT = 200

# Internal callers (proposal recovery) may perform ONLY these operations, and only
# when their principal is on the explicit allowlist passed to the shell. They are
# never electorate members and can never vote, ACK, NACK or resolve.
SYSTEM_OPERATIONS = frozenset({"request_escalation", "reject_on_dissent", "mark_timeout"})


def eligible_of(state: Dict[str, Any]) -> list:
    """Eligible electorate of a round: V1-style participants mapping or a plain list."""
    participants = state.get("participants", [])
    if isinstance(participants, dict) or hasattr(participants, "get"):
        return list(participants.get("eligible", []))
    return list(participants)


def candidate_state_hash(state: Dict[str, Any]) -> str:
    """Hash the immutable content being approved (never the mutable ACK ledger/phase)."""
    basis = {
        key: state.get(key)
        for key in ("policy_snapshot", "proposal", "participants", "frozen_authority_set")
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(basis)).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class _SystemAwareHealth:
    """Allowlisted internal principals have no peer profile to health-check."""

    def __init__(self, inner: Any, system_actor: str) -> None:
        self._inner = inner
        self._system_actor = system_actor

    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        if actor_id == self._system_actor:
            return True
        return self._inner.check_health_gate(actor_id, evaluated_at)


class BrokerAuthorityVersionStore(AuthorityVersionStore):
    def __init__(self, broker: GovernanceBroker) -> None:
        self._broker = broker

    @staticmethod
    def _version_of(target: Any) -> int:
        if target is None:
            return 1
        return int(target.state.get("version", target.revision + 1))

    def read_version(self) -> int:
        return self._version_of(self._broker.get_target(AUTHORITY_TARGET_ID))

    def read_version_in(self, unit: Any) -> int:
        """Read the live version inside the caller's (committing) transaction."""
        return self._version_of(unit.get_target(AUTHORITY_TARGET_ID))

    def build_increment_request(self, unit: Any) -> Any:
        """Mutation that bumps the version, built against the live in-txn revision."""
        target = unit.get_target(AUTHORITY_TARGET_ID)
        return build_mutation_request(
            self._broker.ids,
            id_prefix="auth-ver",
            client_id="authority-store",
            target_id=AUTHORITY_TARGET_ID,
            expected_revision=0 if target is None else target.revision,
            actor_id="system",
            operation="increment",
            desired_state={"version": self._version_of(target) + 1},
            effect_intent=EffectIntent(kind="consensus.noop", payload={}),
        )

    def increment(self) -> int:
        target = self._broker.get_target(AUTHORITY_TARGET_ID)
        new_version = self._version_of(target) + 1
        req = build_mutation_request(
            self._broker.ids,
            id_prefix="auth-ver",
            client_id="authority-store",
            target_id=AUTHORITY_TARGET_ID,
            expected_revision=0 if target is None else target.revision,
            actor_id="system",
            operation="increment",
            desired_state={"version": new_version},
            effect_intent=EffectIntent(kind="consensus.noop", payload={}),
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
        system_principals: frozenset = frozenset(),
    ) -> None:
        # action -> callable(state, round_id, actor_id) -> EffectIntent (approval effect)
        self._effect_factories = dict(effect_factories or {})
        self._broker = broker
        self._clock = clock
        self._ids = ids
        self._verifier = verifier
        self._health_port = health_port
        self._authority_store = authority_store
        self._system_principals = frozenset(system_principals)

    # ------------------------------------------------------------------ helpers
    def _authority_precondition(self, expected_version: int) -> Optional[Callable[[Any], None]]:
        """Authority validation that runs inside the committing broker txn."""
        reader = getattr(self._authority_store, "read_version_in", None)
        if reader is None:
            return None

        def check(unit: Any) -> None:
            live = reader(unit)
            if live != expected_version:
                raise StaleAuthorityError(f"Expected {expected_version}, got {live}")

        return check

    def _approval_effect(self, state: Dict[str, Any], round_id: str, actor_id: str) -> EffectIntent:
        """Approval effect for this round's action (default: consensus.resolved)."""
        action = (state.get("policy_snapshot") or {}).get("action") or state.get("action")
        factory = self._effect_factories.get(action)
        if factory is not None:
            return factory(state, round_id, actor_id)
        candidate = candidate_state_hash(state)
        intent = {
            "snapshot_hash": candidate,
            "round_id": round_id,
            "outcome": "approved",
            "resolved_by": actor_id,
            "final_call_complete": state.get("phase_before_approval") == "final_call",
        }
        sub = build_approval_submission({"round_id": round_id, "hash": candidate}, intent)
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

    def _finalize(self, state: Dict[str, Any], actor_id: str) -> None:
        """Terminal bookkeeping in the V1-readable shape: status + resolution incl. decision hash."""
        state["status"] = "resolved" if state["phase"] in ("approved", "rejected") else "abandoned"
        if state["phase"] in ("approved", "rejected"):
            resolution = dict(state.get("resolution") or {})
            resolution.setdefault("resolved_by", actor_id)
            resolution["outcome"] = state["phase"]
            resolution["decision_hash"] = candidate_state_hash(state)
            resolution.setdefault("resolved_at", self._clock.now())
            state["resolution"] = resolution

    def _revalidate_electorate(self, state: Dict[str, Any]) -> None:
        """Whole-electorate health revalidation before an approval (design 5.2)."""
        gate = AuthorizationGate(
            verifier=self._verifier,
            health_port=self._health_port,
            state_machine=ConsensusStateMachine(state="final_call"),
        )
        if not gate.authorize_final_call_electorate(eligible_of(state), self._clock.now()):
            raise AuthorizationError("Final Call electorate revalidation failed")

    def _authorize_replay(self, state: Dict[str, Any], actor_id: str, credential_id: Optional[str]) -> None:
        """A replay needs the same authentication as the original command."""
        if credential_id is not None:
            if self._verifier is None:
                raise AuthorizationError("Credential presented but no verifier configured.")
            try:
                ok = self._verifier(credential_id=credential_id, claimed_actor_id=actor_id)
            except Exception as e:
                raise AuthorizationError(f"credential verifier error: {e}") from e
            if not ok:
                raise AuthorizationError("Invalid credential.")
        elif state.get("verified_required") and actor_id not in self._system_principals:
            raise AuthorizationError("Credential absent but required.")

    # ---------------------------------------------------------------- creation
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
        action: Optional[str] = None,
        verified_required: bool = False,
    ) -> Any:
        prov_origin, prov_action = resolve_provenance(origin, action)
        policy_snapshot = freeze_policy_snapshot(
            origin=prov_origin, action=prov_action, config=config, risk=risk
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
            # V1-compatible shape so proposal/arbiter code can read V2 rounds unchanged.
            "participants": {
                "required": list(required_participants),
                "eligible": list(eligible_participants),
            },
            "status": "open",
            "verified_required": verified_required,
            "created_at": self._clock.now(),
            "votes": {},
            "dissent_obligations": [],
            "barrier_holders": [],
            "command_log": {},
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
            },
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
            effect_intent=EffectIntent(kind="consensus.noop", payload={}),
        )
        return self._broker.submit(
            req, precondition=self._authority_precondition(expected_authority_version)
        )

    # ---------------------------------------------------------------- pipeline
    def _process_event(
        self,
        round_id: str,
        actor_id: str,
        operation: str,
        event_factory,
        state_updater,
        *,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        command_args: Optional[Dict[str, Any]] = None,
        phase_for_eval: Optional[Callable[[Dict[str, Any]], str]] = None,
    ) -> Any:
        target = self._broker.get_target(round_id)
        if not target:
            raise RecordNotFoundError("consensus-round", round_id)
        state = dict(target.state)

        # Public-command identity: canonical (operation, round, actor, client key).
        scoped_key = None
        args_digest = None
        if idempotency_key is not None:
            scoped_key = _digest([operation, round_id, actor_id, idempotency_key])
            args_digest = _digest(
                {"args": command_args or {}, "expected_revision": expected_revision}
            )
            logged = dict(state.get("command_log") or {})
            if scoped_key in logged:
                if logged[scoped_key] != args_digest:
                    raise IdempotencyPayloadMismatchError(
                        "consensus_shell", operation, idempotency_key
                    )
                self._authorize_replay(state, actor_id, credential_id)
                replay = self._broker.lookup_replay("consensus_shell", operation, scoped_key)
                if replay is not None:
                    return replay

        # Restart consistency (design 4.1): an inconsistent or unversioned frozen
        # snapshot fails closed before anything is evaluated.
        decode_policy_snapshot(dict(state.get("policy_snapshot") or {}))

        phase = state.get("phase", "voting")
        if phase in TERMINAL_PHASES and not (operation == "retraction" and phase == "approved"):
            raise InvalidMutationError("round is terminal")
        eval_phase = phase_for_eval(state) if phase_for_eval is not None else phase

        snapshot = state.get("policy_snapshot") or {}
        final_call_rule = snapshot.get("final_call_rule")
        mandatory_final_call = bool(snapshot.get("mandatory_final_call", False)) or (
            final_call_rule == "always"
        )
        sm = ConsensusStateMachine(
            state=eval_phase,
            final_call_rule=final_call_rule,
            mandatory_floors_hit=mandatory_final_call,
        )

        system_actor = operation in SYSTEM_OPERATIONS and actor_id in self._system_principals
        gate = AuthorizationGate(
            verifier=self._verifier,
            health_port=(
                _SystemAwareHealth(self._health_port, actor_id) if system_actor else self._health_port
            ),
            state_machine=sm,
        )
        frozen_auth_set = frozenset(state.get("frozen_authority_set", []))
        if system_actor:
            frozen_auth_set = frozen_auth_set | {actor_id}

        expected_auth_ver = state.get("expected_authority_version", 1)
        state_hash = candidate_state_hash(state)
        candidate = Candidate(round_id, int(state.get("candidate_revision", 0)), state_hash)
        ledger = AckLedger()
        for participant in (state.get("ack_ledger") or {}).get(state_hash, []):
            ledger.bind_ack(candidate, participant)

        ctx = EvalContext(
            current_timestamp=self._clock.now(),
            caller_identity=actor_id,
            frozen_authority_set=frozen_auth_set,
            current_candidate=candidate,
            bound_ack_participants=ledger.acks_for(candidate),
            required_participants=frozenset(eligible_of(state)),
        )
        event = event_factory(state, state_hash)

        try:
            res = gate.authorize_and_evaluate(
                ctx=ctx,
                event=event,
                credential_id=credential_id,
                verified_required=bool(state.get("verified_required", False)) and not system_actor,
            )
        except AuthorizationError:
            raise
        except InvalidMutationError:
            if operation == "retraction" and phase == "approved":
                from peerhub.governance.consensus import TransitionResult

                res = TransitionResult(new_phase="approved")
            else:
                raise

        fence = AuthorityFence(self._authority_store)
        fence.check(expected_auth_ver)  # fast fail; the committing txn re-validates below

        state["phase"] = res.new_phase
        state["_rev"] = target.revision  # transient: read by effect factories, never persisted
        try:
            effect = state_updater(state, res, ctx, sm, ledger, candidate, fence)
        finally:
            state.pop("_rev", None)
        if state.get("phase") in TERMINAL_PHASES:
            self._finalize(state, actor_id)
        if effect is None:
            effect = EffectIntent(kind="consensus.noop", payload={})

        if scoped_key is not None:
            log = dict(state.get("command_log") or {})
            log[scoped_key] = args_digest
            state["command_log"] = dict(list(log.items())[-COMMAND_LOG_LIMIT:])

        req = build_mutation_request(
            self._ids,
            id_prefix=operation[:3],
            client_id="consensus_shell",
            target_id=round_id,
            expected_revision=target.revision if expected_revision is None else expected_revision,
            actor_id=actor_id,
            operation=operation,
            desired_state=state,
            effect_intent=effect,
        )
        if scoped_key is not None:
            req = dataclasses.replace(req, idempotency_key=scoped_key)
        precondition = self._authority_precondition(expected_auth_ver)

        if operation == "retraction" and state["phase"] == "approved":
            # Post-authorization retraction: the revocation record, the authority
            # increment that fences future work, both receipts and effects commit
            # in ONE transaction (audit finding 4).
            bump = getattr(self._authority_store, "build_increment_request", None)
            if bump is None:
                raise InvalidMutationError(
                    "authority store cannot bump the version atomically with a revocation"
                )
            submissions = self._broker.submit_atomic(
                lambda unit: (req, bump(unit)), precondition=precondition
            )
            return submissions[0]
        return self._broker.submit(req, precondition=precondition)

    # --------------------------------------------------------------- operations
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
                candidate_id=state_hash, actor=actor_id, proof=credential_id or "", nack_type=None
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.ack_recorded:
                ledger.bind_ack(c, actor_id)
            state["ack_ledger"] = {c.target_state_hash: sorted(ledger.acks_for(c))}
            # The ACKer's own concerns (earlier block / dissent) are resolved by their ACK.
            holders = sorted(set(state.get("barrier_holders") or []) - {actor_id})
            dissent = sorted(set(state.get("dissent_obligations") or []) - {actor_id})
            state["barrier_holders"], state["dissent_obligations"] = holders, dissent
            if res.new_phase == "approved":
                if holders or dissent:
                    state["phase"] = "final_call"  # barrier / unresolved dissent still hold
                    return None
                if not ledger.is_complete(c, eligible_of(state)):
                    raise InvalidMutationError("Final Call ACKs are not complete for the current candidate")
                self._revalidate_electorate(state)
                state["phase_before_approval"] = "final_call"
                return self._approval_effect(state, round_id, actor_id)
            return None

        return self._process_event(
            round_id, actor_id, "final_call_ack", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={},
        )

    def final_call_nack(
        self,
        round_id: str,
        actor_id: str,
        nack_type: str = "block",
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> Any:
        """NACK during Final Call: block (holds the barrier), cosmetic (logged) or terminal_rejection."""
        from peerhub.governance.consensus import AckNackEvent

        def event_factory(state, state_hash):
            return AckNackEvent(
                candidate_id=state_hash, actor=actor_id, proof=credential_id or "", nack_type=nack_type
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.barrier_held:
                state["barrier_holders"] = sorted(set(state.get("barrier_holders") or []) | {actor_id})
                state["barrier_held"] = True
            if res.cosmetic_logged:
                state["cosmetic_nacks"] = list(state.get("cosmetic_nacks", [])) + [actor_id]
            if res.terminal_rejection:
                state["resolution"] = {
                    "outcome": "rejected", "basis": "terminal_rejection", "resolved_by": actor_id
                }
                return self._resolution_effect(state, round_id, actor_id, "rejected", "terminal_rejection")
            return None

        return self._process_event(
            round_id, actor_id, "final_call_nack", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={"nack_type": nack_type},
        )

    def cast_vote(
        self,
        round_id: str,
        actor_id: str,
        choice: str,
        expected_revision: Optional[int] = None,
        credential_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Any:
        from peerhub.governance.consensus import QuorumMetEvent, ResolutionEvent, VoteEvent

        if choice not in ("agree", "disagree", "abstain", "need_more_info", "block"):
            raise InvalidMutationError(
                "choice must be agree, disagree, abstain, need_more_info or block"
            )
        if reason is not None and not isinstance(reason, str):
            raise InvalidMutationError("reason must be a string or null")

        def event_factory(state, state_hash):
            return VoteEvent(actor=actor_id, choice=choice, credential=credential_id)

        def updater(state, res, ctx, sm, ledger, c, fence):
            votes = dict(state.get("votes", {}))
            vote_record = {
                "choice": choice, "actor_id": actor_id,
                "cast_at": self._clock.now(), "reason": reason,
            }
            dissent = set(state.get("dissent_obligations") or []) - {actor_id}
            if res.dissent_obligation_added:
                vote_record["dissent_obligation"] = True
                dissent.add(actor_id)
            state["dissent_obligations"] = sorted(dissent)
            votes[actor_id] = vote_record
            state["votes"] = votes

            required_votes = (state.get("policy_snapshot") or {}).get("required_votes")
            if type(required_votes) is not int or required_votes < 1:
                raise ConfigurationError("policy snapshot has no valid required_votes")

            agreements = sum(1 for v in votes.values() if v.get("choice") == "agree")
            decisive = sum(1 for v in votes.values() if v.get("choice") in ("agree", "disagree"))
            state["quorum"] = {
                "reached": agreements >= required_votes,
                "counted_votes": agreements,
                "recorded_votes": len(votes),
                "decisive_votes": decisive,
                "required_votes": required_votes,
            }
            if agreements >= required_votes:
                # Unresolved dissent forces the mandatory Final Call floor (design 4.2).
                quorum_sm = sm
                if dissent and not sm.mandatory_floors_hit:
                    quorum_sm = ConsensusStateMachine(
                        state=sm.state, final_call_rule=sm.final_call_rule, mandatory_floors_hit=True
                    )
                q_res = quorum_sm.evaluate(ctx, QuorumMetEvent(agreement_count=agreements))
                state["phase"] = q_res.new_phase
                if q_res.new_phase == "quorum_reached":
                    resolver = ConsensusStateMachine(state="quorum_reached")
                    r_res = resolver.evaluate(ctx, ResolutionEvent(outcome="approved"))
                    state["phase"] = r_res.new_phase
                    if r_res.new_phase == "approved":
                        state["phase_before_approval"] = "quorum_reached"
                        return self._approval_effect(state, round_id, actor_id)
            return None

        return self._process_event(
            round_id, actor_id, "cast_vote", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={"choice": choice, "reason": reason},
        )

    def correction(
        self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None,
        credential_id: Optional[str] = None, expected_revision: Optional[int] = None,
    ) -> Any:
        from peerhub.governance.consensus import CorrectionEvent

        def event_factory(state, state_hash):
            return CorrectionEvent(actor=actor_id)

        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.acks_dropped:
                ledger.invalidate("correction/new dissent")
                state["ack_ledger"] = {}
            if res.fresh_votes_required:
                # A correction invalidates the candidate: every vote and concern is void.
                state["votes"] = {}
                state["dissent_obligations"] = []
                state["barrier_holders"] = []
                state.pop("barrier_held", None)
                required = (state.get("policy_snapshot") or {}).get("required_votes")
                state["quorum"] = {
                    "reached": False, "counted_votes": 0, "recorded_votes": 0,
                    "decisive_votes": 0, "required_votes": required,
                }
                state["candidate_revision"] = int(state.get("candidate_revision", 0)) + 1
            return None

        return self._process_event(
            round_id, actor_id, "correction", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={},
        )

    def retraction(
        self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None,
        credential_id: Optional[str] = None, expected_revision: Optional[int] = None,
    ) -> Any:
        from peerhub.governance.consensus import RetractionEvent

        def event_factory(state, state_hash):
            return RetractionEvent(candidate_id=state_hash, actor=actor_id, proof="")

        def updater(state, res, ctx, sm, ledger, c, fence):
            if state["phase"] == "approved":
                state["revocations"] = list(state.get("revocations", [])) + ["RevocationRecorded"]
            elif res.acks_dropped:
                apply_retraction(ledger, c, authorized=False)  # drops every bound ACK
                state["ack_ledger"] = {}
            return None

        return self._process_event(
            round_id, actor_id, "retraction", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={},
        )

    def mark_timeout(
        self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None,
        credential_id: Optional[str] = None, expected_revision: Optional[int] = None,
    ) -> Any:
        from peerhub.governance.consensus import TimeoutEvent

        def event_factory(state, state_hash):
            deadlines = (state.get("policy_snapshot") or {}).get("deadlines") or {}
            window = deadlines.get(state.get("phase", "voting"), deadlines.get("voting"))
            deadline = None
            if window is not None:
                deadline = int(state.get("created_at", 0)) + int(window)
                if self._clock.now() < deadline:
                    raise InvalidMutationError("round deadline has not been reached")
            return TimeoutEvent(
                requester=actor_id, deadline=float(deadline if deadline is not None else self._clock.now())
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            if res.evidence_recorded:
                state["timeout_evidence"] = {
                    "actor_id": actor_id,
                    "recorded_at": self._clock.now(),
                    "phase_at_timeout": state.get("phase"),
                }
            return None

        return self._process_event(
            round_id, actor_id, "mark_timeout", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={},
        )

    def abandon(
        self, round_id: str, actor_id: str, idempotency_key: Optional[str] = None,
        credential_id: Optional[str] = None, expected_revision: Optional[int] = None,
    ) -> Any:
        from peerhub.governance.consensus import AbandonEvent

        def event_factory(state, state_hash):
            return AbandonEvent(reason="abandoned", requesting_actor=actor_id)

        return self._process_event(
            round_id, actor_id, "abandon", event_factory, lambda *a: None,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={},
        )

    def reject_on_dissent(
        self, round_id: str, actor_id: str, basis: str, idempotency_key: Optional[str] = None,
        credential_id: Optional[str] = None, expected_revision: Optional[int] = None,
    ) -> Any:
        """Reject an open round after verifying a stored eligible dissent (design 6.1 rule 5)."""
        from peerhub.governance.consensus import ResolutionEvent

        def event_factory(state, state_hash):
            eligible = set(eligible_of(state))
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
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={"basis": basis},
        )

    def request_escalation(
        self, round_id: str, reason: str, requester_id: str, tier: int, required_authority: str,
        idempotency_key: Optional[str] = None, credential_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> Any:
        from peerhub.governance.consensus import EscalationEvent

        def replacement_deadline(state) -> int:
            deadlines = (state.get("policy_snapshot") or {}).get("deadlines") or {}
            return self._clock.now() + int(deadlines.get("escalation", 1800))

        def event_factory(state, state_hash):
            return EscalationEvent(
                source_phases=[state.get("phase", "voting")],
                replacement_deadline=float(replacement_deadline(state)),
            )

        def updater(state, res, ctx, sm, ledger, c, fence):
            state["escalation"] = {
                "reason": reason,
                "requested_by": requester_id,
                "tier": tier,
                "required_authority": required_authority,
                "requested_at": self._clock.now(),
                "replacement_deadline": replacement_deadline(state),
            }
            return None

        return self._process_event(
            round_id, requester_id, "request_escalation", event_factory, updater,
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key,
            command_args={"reason": reason, "tier": tier, "required_authority": required_authority},
        )

    def resolve(
        self, round_id: str, outcome: str, resolved_by: str, basis: str,
        idempotency_key: Optional[str] = None, credential_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> Any:
        """Resolve an escalated (or quorum_reached) round to approved/rejected.

        Requires a regular authorized electorate member (never a system principal).
        """
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
            expected_revision=expected_revision, credential_id=credential_id,
            idempotency_key=idempotency_key, command_args={"outcome": outcome, "basis": basis},
            phase_for_eval=lambda st: "escalated" if st.get("escalation") else st.get("phase", "voting"),
        )

    # ------------------------------------------------------------------ effects
    def _all_unfinished_effects(self) -> list:
        """Every unfinished effect (pages through the broker's bounded discovery)."""
        limit = 100
        while True:
            found = self._broker.recover_pending_effects(limit=limit)
            if len(found) < limit or limit >= 1_000_000:
                return list(found)
            limit *= 4

    def process_consensus_effects(self, round_id: str, *, owner_id: Optional[str] = None) -> tuple:
        """Claim and receipt this round's pending consensus effects as the V2 worker.

        Only the ``consensus-v2:`` owner prefix passes the activation fence. The
        ratified-invariant effect is left for its exclusive materializer; an
        unknown effect kind holds (EffectRoutingError) before anything is claimed.
        An effect this owner already claimed (crash after claim) is resumed under
        its recorded attempt instead of failing; no-op effects are completed so
        they never hide later work.
        """
        owner = owner_id or f"consensus-v2:{round_id}"
        if not owner.startswith("consensus-v2:"):
            raise ValueError("V2 consensus worker owner must start with 'consensus-v2:'")
        matching = []
        for pending in self._all_unfinished_effects():
            payload = pending.event.payload
            effect_payload = payload.get("effect_payload")
            if not (
                payload.get("target_id") == round_id
                or (isinstance(effect_payload, dict) and effect_payload.get("round_id") == round_id)
            ):
                continue
            kind = payload.get("effect_kind")
            if kind == RATIFIED_INVARIANT_EFFECT_KIND:
                continue
            if kind != "consensus.noop":
                route_effect_kind(kind, "generic")  # raises EffectRoutingError (hold) if unknown
            claimed_by = pending.event.claimed_by
            if claimed_by is not None and claimed_by != owner:
                continue  # owned by another worker
            matching.append(pending)
        receipts = []
        for pending in matching:
            event = pending.event
            attempt_id = (
                event.claim_attempt_id
                if event.claimed_by == owner and event.claim_attempt_id
                else self._ids.new_id("effect-attempt")
            )
            claimed = self._broker.claim_effect(event.event_id, owner_id=owner, attempt_id=attempt_id)
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
