"""Application orchestration for bounded final-arbiter reviews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol, cast

from peerhub.application.direct_ask import (
    DirectAskRequest,
    DirectAskResult,
    execute_direct_ask,
)
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.execution import ExecutionCertainty, TransportLimits
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import CommandID, JsonValue
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import RequestState
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.contract import EffectIntent, MutationRequest


ARBITER_BUDGET_TARGET_ID = "arbiter-budget:workspace"
# Deliberate working substitute for legacy's known-broken ``cc.fable``.
_DEFAULT_PROFILE_ID = "cc.deepthink"
_PROMPT_LIMIT = 1200
_VERDICT = re.compile(r"VERDICT:\s*(APPROVE|REJECT)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FinalArbiterPolicy:
    """Workspace policy governing optional final-arbiter invocations."""

    enabled: bool = False
    peer_name: str = "cc"
    profile_id: str = _DEFAULT_PROFILE_ID
    triggers: tuple[str, ...] = ("dissent",)
    max_invocations: int = 5
    window_seconds: int = 18_000

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        _nonempty(self.peer_name, "peer_name")
        _nonempty(self.profile_id, "profile_id")
        if not isinstance(self.triggers, tuple) or any(  # pyright: ignore[reportUnnecessaryIsInstance]
            not isinstance(value, str) or not value.strip()  # pyright: ignore[reportUnnecessaryIsInstance]
            for value in self.triggers
        ):
            raise ValueError("triggers must contain non-empty strings")
        if (
            type(self.max_invocations) is not int
            or self.max_invocations < 1
        ):
            raise ValueError("max_invocations must be a positive integer")
        if type(self.window_seconds) is not int or self.window_seconds < 1:
            raise ValueError("window_seconds must be a positive integer")


def load_final_arbiter_policy(workspace_root: Path) -> FinalArbiterPolicy:
    """Load ``.peerhub/arbiter.json``; absence disables the feature."""

    config_path = workspace_root / ".peerhub" / "arbiter.json"
    if not config_path.exists():
        return FinalArbiterPolicy(enabled=False)
    with config_path.open("r", encoding="utf-8") as stream:
        raw_object: object = json.load(stream)
    if not isinstance(raw_object, Mapping):
        raise ValueError("arbiter.json must contain a JSON object")
    raw = cast(Mapping[object, object], raw_object)
    candidate = raw.get("candidate", {})
    if not isinstance(candidate, Mapping):
        raise ValueError("arbiter.json candidate must be an object")
    candidate_mapping = cast(Mapping[object, object], candidate)
    triggers = raw.get("triggers", ("dissent",))
    if not isinstance(triggers, (list, tuple)):
        raise ValueError("arbiter.json triggers must be an array")
    return FinalArbiterPolicy(
        enabled=_optional_bool(raw, "enabled", False),
        peer_name=_optional_text(candidate_mapping, "peer_name", "cc"),
        profile_id=_optional_text(
            candidate_mapping,
            "profile_id",
            _DEFAULT_PROFILE_ID,
        ),
        triggers=tuple(
            _text_sequence(cast(Sequence[object], triggers), "triggers")
        ),
        max_invocations=_optional_int(raw, "max_invocations", 5),
        window_seconds=_optional_int(raw, "window_seconds", 18_000),
    )


@dataclass(frozen=True, slots=True)
class DissentFinding:
    voter_id: str
    reason: str
    choice: str


def classify_consensus_dissent(
    round_state: Mapping[str, JsonValue],
) -> tuple[DissentFinding, ...]:
    """Return deterministic explicit-disagreement and required-no-vote facts."""

    participants = _mapping_field(round_state, "participants")
    required_raw = participants.get("required")
    if not isinstance(required_raw, (tuple, list)):
        raise InvalidMutationError("participants.required must be an array")
    required = {_nonempty(value, "required participant") for value in required_raw}
    votes = _mapping_field(round_state, "votes")

    findings: list[DissentFinding] = []
    for voter_id in sorted(set(required) | set(votes)):
        vote_raw = votes.get(voter_id)
        if vote_raw is None:
            if voter_id in required:
                findings.append(
                    DissentFinding(voter_id, "no_vote", "no_vote")
                )
            continue
        if not isinstance(vote_raw, Mapping):
            raise InvalidMutationError("consensus vote must be an object")
        choice = vote_raw.get("choice")
        if not isinstance(choice, str) or not choice.strip():
            raise InvalidMutationError("consensus vote choice is invalid")
        if choice.lower() != "agree":
            findings.append(
                DissentFinding(voter_id, "non_agree", choice.lower())
            )
    return tuple(findings)


def build_condensed_arbiter_prompt(
    round_state: Mapping[str, JsonValue],
    dissent: Sequence[DissentFinding],
    *,
    max_characters: int = _PROMPT_LIMIT,
) -> str:
    """Build the deterministic, bounded prompt frozen into a review request."""

    if type(max_characters) is not int or max_characters < 1:
        raise ValueError("max_characters must be a positive integer")
    proposal = _mapping_field(round_state, "proposal")
    participants = _mapping_field(round_state, "participants")
    required_raw = participants.get("required")
    if not isinstance(required_raw, (tuple, list)):
        raise InvalidMutationError("participants.required must be an array")
    required = sorted(
        _nonempty(value, "required participant") for value in required_raw
    )
    question = _nonempty(proposal.get("question"), "proposal.question")
    body = _nonempty(proposal.get("body"), "proposal.body")
    required_summary = _clip(", ".join(required), 220)
    blocker_summary = _clip(
        "\n".join(
            f"- {item.voter_id}: {item.choice} ({item.reason})"
            for item in sorted(dissent, key=lambda value: value.voter_id)
        ),
        320,
    )
    lines = [
        "Return exactly one first line: VERDICT: APPROVE or VERDICT: REJECT.",
        f"Proposal question: {_clip(question, 180)}",
        f"Proposal body: {_clip(body, 320)}",
        f"Required voters (sorted): {required_summary}",
        "Dissent blockers (sorted):",
        blocker_summary,
    ]
    prompt = "\n".join(lines)
    return prompt[:max_characters]


def parse_arbiter_verdict(response_text: str | None) -> str | None:
    """Parse only an exact verdict on the first non-empty response line."""

    if response_text is None:
        return None
    for line in response_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _VERDICT.fullmatch(stripped)
        return match.group(1).upper() if match is not None else None
    return None


class ArbiterBudgetSlotState(StrEnum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ArbiterBudgetReservation:
    reservation_id: str
    review_id: str
    round_id: str
    window_start: int


class ArbiterBudgetExceeded(InvalidMutationError):
    """The anchored policy window has no remaining invocation slots."""

    def __init__(self) -> None:
        super().__init__("budget_exceeded")


class ArbiterBudgetManager:
    """CAS-backed anchored-window reservation and slot-state transitions."""

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        clock: Clock,
        ids: IdSource,
        actor_id: str,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._ids = ids
        self._actor_id = _nonempty(actor_id, "actor_id")

    def reserve(
        self,
        *,
        round_id: str,
        review_id: str,
        policy: FinalArbiterPolicy,
    ) -> ArbiterBudgetReservation:
        reservation_id = self._ids.new_id("arbiter-reservation")
        now = self._clock.now()
        for _ in range(16):
            target = self._broker.get_target(ARBITER_BUDGET_TARGET_ID)
            if target is None:
                expected_revision = 0
                window_start = now
                slots: list[dict[str, JsonValue]] = []
                count = 0
                max_invocations = policy.max_invocations
                window_seconds = policy.window_seconds
            else:
                expected_revision = target.revision
                window_start, count, slots = _budget_values(target.state)
                anchored_window_seconds = _positive_int(
                    target.state.get("window_seconds"),
                    "window_seconds",
                )
                max_invocations = _positive_int(
                    target.state.get("max_invocations"),
                    "max_invocations",
                )
                window_seconds = anchored_window_seconds
                if now >= window_start + anchored_window_seconds:
                    window_start = now
                    slots = []
                    count = 0
                    max_invocations = policy.max_invocations
                    window_seconds = policy.window_seconds
            if count >= max_invocations:
                raise ArbiterBudgetExceeded()

            slots.append(
                {
                    "reservation_id": reservation_id,
                    "round_id": _nonempty(round_id, "round_id"),
                    "review_id": _nonempty(review_id, "review_id"),
                    "state": ArbiterBudgetSlotState.RESERVED.value,
                    "reserved_at": now,
                    "updated_at": now,
                    "command_id": None,
                    "attempt_id": None,
                }
            )
            state = _budget_state(
                window_start=window_start,
                count=count + 1,
                slots=slots,
                max_invocations=max_invocations,
                window_seconds=window_seconds,
                updated_at=now,
            )
            try:
                self._submit(
                    expected_revision=expected_revision,
                    operation="arbiter.budget.reserve",
                    state=state,
                )
                return ArbiterBudgetReservation(
                    reservation_id=reservation_id,
                    review_id=review_id,
                    round_id=round_id,
                    window_start=window_start,
                )
            except StaleRevisionError:
                continue
        raise InvalidMutationError(
            "arbiter budget changed repeatedly during reservation"
        )

    def consume(
        self,
        reservation: ArbiterBudgetReservation,
        *,
        command_id: str | None,
        attempt_id: str | None,
    ) -> bool:
        return self._transition(
            reservation,
            ArbiterBudgetSlotState.CONSUMED,
            command_id=command_id,
            attempt_id=attempt_id,
        )

    def release(self, reservation: ArbiterBudgetReservation) -> bool:
        return self._transition(
            reservation,
            ArbiterBudgetSlotState.RELEASED,
        )

    def mark_unknown(
        self,
        reservation: ArbiterBudgetReservation,
        *,
        command_id: str | None = None,
        attempt_id: str | None = None,
    ) -> bool:
        return self._transition(
            reservation,
            ArbiterBudgetSlotState.UNKNOWN,
            command_id=command_id,
            attempt_id=attempt_id,
        )

    def _transition(
        self,
        reservation: ArbiterBudgetReservation,
        new_state: ArbiterBudgetSlotState,
        *,
        command_id: str | None = None,
        attempt_id: str | None = None,
    ) -> bool:
        now = self._clock.now()
        for _ in range(16):
            target = self._broker.get_target(ARBITER_BUDGET_TARGET_ID)
            if target is None:
                raise RecordNotFoundError(
                    "arbiter-budget",
                    ARBITER_BUDGET_TARGET_ID,
                )
            window_start, count, slots = _budget_values(target.state)
            window_seconds = _positive_int(
                target.state.get("window_seconds"),
                "window_seconds",
            )
            if now >= window_start + window_seconds:
                return False
            selected: dict[str, JsonValue] | None = None
            for slot in slots:
                if slot.get("reservation_id") == reservation.reservation_id:
                    selected = slot
                    break
            if selected is None:
                raise RecordNotFoundError(
                    "arbiter-budget-reservation",
                    reservation.reservation_id,
                )
            current_state = selected.get("state")
            if current_state == new_state.value:
                return True
            if current_state != ArbiterBudgetSlotState.RESERVED.value:
                raise InvalidMutationError(
                    "arbiter budget reservation is no longer RESERVED"
                )
            selected["state"] = new_state.value
            selected["updated_at"] = now
            selected["command_id"] = command_id
            selected["attempt_id"] = attempt_id
            if new_state is ArbiterBudgetSlotState.RELEASED:
                count -= 1
            state = {
                **dict(target.state),
                "count": count,
                "slots": tuple(slots),
                "updated_at": now,
            }
            try:
                self._submit(
                    expected_revision=target.revision,
                    operation=f"arbiter.budget.{new_state.value.lower()}",
                    state=state,
                )
                return True
            except StaleRevisionError:
                continue
        raise InvalidMutationError(
            "arbiter budget changed repeatedly during slot transition"
        )

    def _submit(
        self,
        *,
        expected_revision: int,
        operation: str,
        state: Mapping[str, JsonValue],
    ) -> None:
        request_id = self._ids.new_id("arbiter-budget-request")
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("arbiter-budget-command")
                ),
                correlation_id=self._ids.new_id(
                    "arbiter-budget-correlation"
                ),
                client_id="peerhub.arbiter",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=self._actor_id,
                policy_revision="arbiter-v1",
                target_id=ARBITER_BUDGET_TARGET_ID,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=state,
                effect_intent=EffectIntent(kind="arbiter.noop", payload={}),
            )
        )


class ArbiterExecutor(Protocol):
    def __call__(
        self,
        request: DirectAskRequest,
        *,
        clock: Clock,
        ids: IdSource,
        authenticated_subject: AuthenticatedSubject,
    ) -> DirectAskResult: ...


class ArbiterReviewCoordinator:
    """Reserve, invoke, record, and canonically attach one arbiter review."""

    def __init__(
        self,
        broker: GovernanceBroker,
        consensus: ConsensusService,
        *,
        workspace_root: Path,
        clock: Clock,
        ids: IdSource,
        authenticated_subject: AuthenticatedSubject,
        executor: ArbiterExecutor = execute_direct_ask,
        limits: TransportLimits | None = None,
        configured_policy: FinalArbiterPolicy | None = None,
    ) -> None:
        self._broker = broker
        self._consensus = consensus
        self._workspace_root = workspace_root
        self._clock = clock
        self._ids = ids
        self._subject = authenticated_subject
        self._executor = executor
        self._limits = limits or TransportLimits(
            process_timeout_ms=60_000,
            silence_timeout_ms=60_000,
            max_output_bytes=1_000_000,
        )
        self._configured_policy = configured_policy
        self._budget = ArbiterBudgetManager(
            broker,
            clock=clock,
            ids=ids,
            actor_id=authenticated_subject.principal_id,
        )

    def review(self, round_id: str) -> Mapping[str, JsonValue]:
        """Run a manual review when configured dissent and budget permit it."""

        round_target = self._consensus.get_target(round_id)
        if round_target is None:
            raise RecordNotFoundError("consensus-round", round_id)
        policy = self._configured_policy or load_final_arbiter_policy(
            self._workspace_root
        )
        if not policy.enabled:
            return {"fired": False, "reason": "arbiter_disabled"}
        if round_target.state.get("status") != "resolved":
            raise InvalidMutationError(
                "arbiter review requires a resolved consensus round"
            )
        if "dissent" not in policy.triggers:
            return {"fired": False, "reason": "trigger_not_enabled"}
        dissent = classify_consensus_dissent(round_target.state)
        if not dissent:
            return {"fired": False, "reason": "no_dissent"}

        review_id = self._ids.new_id("arbiter-review")
        try:
            reservation = self._budget.reserve(
                round_id=round_id,
                review_id=review_id,
                policy=policy,
            )
        except ArbiterBudgetExceeded:
            return {"fired": False, "reason": "budget_exceeded"}

        prompt = build_condensed_arbiter_prompt(round_target.state, dissent)
        request_target_id = f"arbiter-review:{round_id}:{review_id}"
        requested_at = self._clock.now()
        try:
            self._create_immutable(
                target_id=request_target_id,
                operation="arbiter.review.request",
                state={
                    "schema": "peerhub.arbiter-review.v1",
                    "kind": "arbiter-review",
                    "scope": round_id,
                    "round_id": round_id,
                    "round_revision": round_target.revision,
                    "review_id": review_id,
                    "trigger": "dissent",
                    "status": "REQUESTED",
                    "requested_at": requested_at,
                    "requested_by": self._subject.principal_id,
                    "candidate": {
                        "peer_name": policy.peer_name,
                        "profile_id": policy.profile_id,
                    },
                    "dissent": tuple(
                        {
                            "voter_id": item.voter_id,
                            "reason": item.reason,
                            "choice": item.choice,
                        }
                        for item in dissent
                    ),
                    "prompt": prompt,
                    "prompt_hash": "sha256:"
                    + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "budget_target_id": ARBITER_BUDGET_TARGET_ID,
                    "budget_reservation_id": reservation.reservation_id,
                },
            )
        except Exception:
            self._budget.release(reservation)
            raise

        ask_request = DirectAskRequest(
            workspace_root=self._workspace_root,
            peer_name=policy.peer_name,
            prompt=prompt,
            required_capability_tier=CapabilityTier.READ_ONLY,
            profile_id=policy.profile_id,
            limits=self._limits,
        )
        try:
            result = self._executor(
                ask_request,
                clock=self._clock,
                ids=self._ids,
                authenticated_subject=self._subject,
            )
        except Exception as error:
            certainty = _exception_certainty(error)
            if certainty is ExecutionCertainty.NOT_STARTED:
                self._budget.release(reservation)
            elif certainty in {
                ExecutionCertainty.STARTED,
                ExecutionCertainty.TERMINAL,
            }:
                self._budget.consume(
                    reservation,
                    command_id=None,
                    attempt_id=None,
                )
            else:
                self._budget.mark_unknown(reservation)
            raise

        if result.request_state is not RequestState.SUCCEEDED_VERIFIED:
            if _result_was_not_started(result):
                self._budget.release(reservation)
                reason = "not_started"
                fired = False
            elif result.execution_certainty in {
                ExecutionCertainty.STARTED,
                ExecutionCertainty.TERMINAL,
            }:
                self._budget.consume(
                    reservation,
                    command_id=result.command_id,
                    attempt_id=result.attempt_id,
                )
                reason = "execution_failed"
                fired = True
            else:
                self._budget.mark_unknown(
                    reservation,
                    command_id=result.command_id,
                    attempt_id=result.attempt_id,
                )
                reason = "execution_uncertain"
                fired = False
            return {
                "fired": fired,
                "reason": reason,
                "review_id": review_id,
                "request_target_id": request_target_id,
            }

        parsed_verdict = parse_arbiter_verdict(result.response_text)
        opinion_target_id = f"arbiter-opinion:{round_id}:{review_id}"
        try:
            self._create_immutable(
                target_id=opinion_target_id,
                operation="arbiter.opinion.record",
                state={
                    "schema": "peerhub.arbiter-opinion.v1",
                    "kind": "arbiter-opinion",
                    "scope": round_id,
                    "round_id": round_id,
                    "review_id": review_id,
                    "request_target_id": request_target_id,
                    "returned_by": {
                        "peer_name": result.peer_kind,
                        "profile_id": result.profile_id,
                    },
                    "dispatch": {
                        "state": result.request_state.value,
                        "command_id": result.command_id,
                        "attempt_id": result.attempt_id,
                        "execution_certainty": (
                            result.execution_certainty.value
                            if result.execution_certainty is not None
                            else None
                        ),
                        "error_code": (
                            result.error_code.value
                            if result.error_code is not None
                            else None
                        ),
                    },
                    "response_text": result.response_text,
                    "parsed_verdict": parsed_verdict,
                    "recorded_at": self._clock.now(),
                },
            )
        except Exception:
            # The provider invocation is already proven complete, so a local
            # opinion-persistence failure must still consume the budget slot.
            self._budget.consume(
                reservation,
                command_id=result.command_id,
                attempt_id=result.attempt_id,
            )
            raise
        self._budget.consume(
            reservation,
            command_id=result.command_id,
            attempt_id=result.attempt_id,
        )

        canonical = False
        if parsed_verdict is not None:
            self._consensus.record_arbiter_opinion(
                round_id,
                request_target_id=request_target_id,
                opinion_target_id=opinion_target_id,
                actor_id=self._subject.principal_id,
            )
            refreshed = self._consensus.get_target(round_id)
            if refreshed is not None:
                reference = refreshed.state.get("arbiter_opinion")
                canonical = (
                    isinstance(reference, Mapping)
                    and reference.get("opinion_target_id")
                    == opinion_target_id
                )

        return {
            "fired": True,
            "reason": "opinion_recorded",
            "review_id": review_id,
            "request_target_id": request_target_id,
            "opinion_target_id": opinion_target_id,
            "parsed_verdict": parsed_verdict,
            "canonical": canonical,
        }

    def _create_immutable(
        self,
        *,
        target_id: str,
        operation: str,
        state: Mapping[str, JsonValue],
    ) -> None:
        request_id = self._ids.new_id("arbiter-request")
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("arbiter-command")),
                correlation_id=self._ids.new_id("arbiter-correlation"),
                client_id="peerhub.arbiter",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=self._subject.principal_id,
                policy_revision="arbiter-v1",
                target_id=target_id,
                expected_revision=0,
                operation=operation,
                desired_state=state,
                effect_intent=EffectIntent(kind="arbiter.noop", payload={}),
            )
        )


def _budget_values(
    state: Mapping[str, JsonValue],
) -> tuple[int, int, list[dict[str, JsonValue]]]:
    if state.get("kind") != "arbiter-budget":
        raise InvalidMutationError("arbiter budget target kind is invalid")
    window_start = _nonnegative_int(state.get("window_start"), "window_start")
    count = _nonnegative_int(state.get("count"), "count")
    slots_raw = state.get("slots")
    if not isinstance(slots_raw, (tuple, list)):
        raise InvalidMutationError("arbiter budget slots must be an array")
    slots: list[dict[str, JsonValue]] = []
    for slot in slots_raw:
        if not isinstance(slot, Mapping):
            raise InvalidMutationError("arbiter budget slot must be an object")
        slots.append(dict(cast(Mapping[str, JsonValue], slot)))
    counted = sum(
        1
        for slot in slots
        if slot.get("state")
        in {
            ArbiterBudgetSlotState.RESERVED.value,
            ArbiterBudgetSlotState.CONSUMED.value,
            ArbiterBudgetSlotState.UNKNOWN.value,
        }
    )
    if counted != count:
        raise InvalidMutationError("arbiter budget count does not match slots")
    return window_start, count, slots


def _budget_state(
    *,
    window_start: int,
    count: int,
    slots: Sequence[Mapping[str, JsonValue]],
    max_invocations: int,
    window_seconds: int,
    updated_at: int,
) -> Mapping[str, JsonValue]:
    return {
        "schema": "peerhub.arbiter-budget.v1",
        "kind": "arbiter-budget",
        "scope": "workspace",
        "window_start": window_start,
        "window_seconds": window_seconds,
        "max_invocations": max_invocations,
        "count": count,
        "slots": tuple(slots),
        "updated_at": updated_at,
    }


def _mapping_field(
    value: Mapping[str, JsonValue],
    field: str,
) -> Mapping[str, JsonValue]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise InvalidMutationError(f"{field} must be an object")
    return cast(Mapping[str, JsonValue], result)


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise InvalidMutationError(f"{field} must be a nonnegative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _nonnegative_int(value, field)
    if result < 1:
        raise InvalidMutationError(f"{field} must be a positive integer")
    return result


def _optional_bool(
    value: Mapping[object, object],
    field: str,
    default: bool,
) -> bool:
    result = value.get(field, default)
    if type(result) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return result


def _optional_text(
    value: Mapping[object, object],
    field: str,
    default: str,
) -> str:
    result = value.get(field, default)
    return _nonempty(result, field)


def _optional_int(
    value: Mapping[object, object],
    field: str,
    default: int,
) -> int:
    result = value.get(field, default)
    if type(result) is not int:
        raise ValueError(f"{field} must be an integer")
    return result


def _text_sequence(value: Sequence[object], field: str) -> tuple[str, ...]:
    return tuple(_nonempty(item, field) for item in value)


def _exception_certainty(error: Exception) -> ExecutionCertainty | None:
    raw = getattr(error, "execution_certainty", None)
    if isinstance(raw, ExecutionCertainty):
        return raw
    if isinstance(raw, str):
        try:
            return ExecutionCertainty(raw)
        except ValueError:
            return None
    return None


def _result_was_not_started(result: DirectAskResult) -> bool:
    if result.execution_certainty is ExecutionCertainty.NOT_STARTED:
        return True
    return result.request_state in {
        RequestState.REJECTED_VALIDATION,
        RequestState.REJECTED_POLICY,
        RequestState.FAILED_PRE_DISPATCH,
    }


__all__ = [
    "ARBITER_BUDGET_TARGET_ID",
    "ArbiterBudgetExceeded",
    "ArbiterBudgetManager",
    "ArbiterBudgetReservation",
    "ArbiterBudgetSlotState",
    "ArbiterReviewCoordinator",
    "DissentFinding",
    "FinalArbiterPolicy",
    "build_condensed_arbiter_prompt",
    "classify_consensus_dissent",
    "load_final_arbiter_policy",
    "parse_arbiter_verdict",
]
