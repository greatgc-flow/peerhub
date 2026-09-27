"""Consultation rounds for ask/broadcast at REVIEW / QUORUM / UNANIMOUS depth (R4 2.1, 8.1, 8.2).

Design (peer-consulted 2026-09-27; cx.astra + ag.deepthink, adopted where they agree):
* electorate = the configured proposal voters (``load_proposal_voters``), health-gated with the same
  rule as proposals (``PeerHealthGatePort``), logical peers de-duplicated;
* REVIEW: quorum 1, reviewer = first eligible voter that is not the proposer (CD-R-04 self-review is
  an ``InvalidMutationError``); no eligible reviewer -> immediate escalation, dispatch held (CD-R-05);
* QUORUM / UNANIMOUS: binding round through the V2 consensus engine (min two, frozen policy);
* the round stores the query DIGEST only; the exact text is staged as an EPHEMERAL scratch file whose
  path is in the round body and is removed when the consultation ends (R4 2.5);
* the initiating process prints the round id, then polls durable state every 250 ms until the frozen
  ``consensus.timeout_seconds`` deadline; peers cast evidence with
  ``peerhub consensus vote|ack --round-id R``;
* outcomes: approved -> dispatch; dissent/BLOCK -> held; timeout -> REVIEW proceeds (non-blocking,
  CD-R-03) while QUORUM/UNANIMOUS escalate and hold. Requires the consensus V2 engine (activated
  workspace); otherwise it fails closed.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from peerhub.adapters.prompt_transport import remove_staged_prompt, stage_prompt
from peerhub.application.consensus_facade import TIMEOUT_SWEEP_PRINCIPAL
from peerhub.application.health_gate_port import PeerHealthGatePort
from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.policy import ConsultationDepth

CONSULTATION_ACTION = "dispatch.consultation"
CONSULTATION_SYSTEM_PRINCIPAL = "system:consultation-gate"
POLL_INTERVAL_SECONDS = 0.25
STAGING_DIR = "prompt-staging"


class ConsultationError(ValueError):
    """The consultation cannot run or was held (CLI exit 2)."""


@dataclass(frozen=True)
class ConsultationOutcome:
    proceed: bool
    round_id: str | None
    reason: str


def eligible_electorate(
    voters: Sequence[str], gate: Any, now: int, *, proposer: str
) -> tuple[str, ...]:
    """Configured voters, de-duplicated, that pass the health gate (proposer excluded for REVIEW)."""

    seen: list[str] = []
    for voter in voters:
        if voter not in seen:
            seen.append(voter)
    return tuple(v for v in seen if gate.check_health_gate(v, now) and v != proposer)


def select_reviewer(
    electorate: Sequence[str], proposer: str, *, requested: str | None = None
) -> str:
    """CD-R-04 / CD-R-05 for REVIEW.

    An explicitly requested reviewer equal to the proposer is a self-review
    (``InvalidMutationError``); otherwise the first eligible non-proposer is chosen; none left is an
    explicit blocker (escalation, dispatch held).
    """

    if requested is not None:
        if requested == proposer:
            raise InvalidMutationError("self-review is prohibited")
        if requested not in electorate:
            raise ConsultationError(f"requested reviewer {requested!r} is not eligible")
        return requested
    for voter in electorate:
        if voter != proposer:
            return voter
    raise ConsultationError("no eligible reviewer: escalation required, dispatch held")


def run_consultation(
    *,
    depth: ConsultationDepth,
    action: str,
    prompt: str,
    workspace_root: Path,
    runtime: Any,
    proposer: str,
    voters: Sequence[str],
    timeout_seconds: int,
    write: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    policy_provider: Any,
    staging_root: Path | None = None,
) -> ConsultationOutcome:
    """Create the consultation round, wait for its decision, return whether dispatch may proceed."""

    facade = runtime.consensus_facade
    if not facade.is_v2():
        raise ConsultationError(
            "consultation rounds need the consensus V2 engine (activate it first); "
            "refusing to bypass a configured obligation"
        )
    clock_now = int(runtime.context.clock.now())
    gate = PeerHealthGatePort(runtime.peer_registry_service, runtime.health_service)

    if depth is ConsultationDepth.REVIEW:
        electorate_all = eligible_electorate(voters, gate, clock_now, proposer="")
        reviewer = select_reviewer(electorate_all, proposer)
        electorate: tuple[str, ...] = (reviewer,)
        required_votes = 1
    else:
        electorate = eligible_electorate(voters, gate, clock_now, proposer=proposer)
        if len(electorate) < 2:
            raise ConsultationError(
                f"{depth.value} consultation needs at least two eligible voters "
                f"({len(electorate)} available): escalation required, dispatch held"
            )
        required_votes = len(electorate) if depth is ConsultationDepth.UNANIMOUS else 0

    digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    round_id = f"consultation-{runtime.context.ids.new_id('round')}"
    staged = stage_prompt(
        prompt,
        root=staging_root or workspace_root,
        relative_dir=STAGING_DIR,
        request_id=round_id,
    )
    try:
        config = policy_provider.round_config(
            CONSULTATION_ACTION, "normal", len(electorate), len(electorate)
        )
        if required_votes:
            config = {**config, "required_votes": required_votes, "formula": depth.value}
        facade.propose_v2(
            round_id,
            f"Consultation for {action}",
            f"Approve dispatching this {action}?",
            (
                f"depth={depth.value}\nquery digest: {digest}\n"
                f"ephemeral query copy (removed when this consultation ends): {staged.path}"
            ),
            proposer,
            list(electorate),
            list(electorate),
            "normal",
            digest,
            config,
            origin="consultation",
            action=CONSULTATION_ACTION,
        )
        write(
            f"consultation {depth.value} round {round_id}: waiting for {', '.join(electorate)} "
            f"(peerhub consensus vote --round-id {round_id} --actor <peer> --choice agree)"
        )
        return _wait(
            facade, round_id, depth, timeout_seconds, write, sleep, now
        )
    finally:
        remove_staged_prompt(str(staged.path))


def _wait(
    facade: Any,
    round_id: str,
    depth: ConsultationDepth,
    timeout_seconds: int,
    write: Callable[[str], None],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> ConsultationOutcome:
    deadline = now() + timeout_seconds
    while True:
        target = facade.get_target(round_id)
        state = dict(target.state) if target is not None else {}
        phase = state.get("phase")
        if phase == "approved":
            return ConsultationOutcome(True, round_id, f"consultation approved ({depth.value})")
        if phase in ("rejected", "abandoned"):
            return ConsultationOutcome(False, round_id, f"consultation {phase}: dispatch held")
        votes = dict(state.get("votes") or {})
        if any(dict(v).get("choice") in ("disagree", "block") for v in votes.values()):
            facade.reject_on_dissent(
                round_id, rejected_by=CONSULTATION_SYSTEM_PRINCIPAL,
                basis="consultation dissent",
            )
            return ConsultationOutcome(False, round_id, "consultation dissent: dispatch held")
        remaining = deadline - now()
        if remaining <= 0:
            if depth is ConsultationDepth.REVIEW:
                facade.mark_timeout(round_id, "consultation review timeout")
                return ConsultationOutcome(
                    True, round_id, "REVIEW timed out: non-blocking, dispatch proceeds"
                )
            facade.request_escalation(
                round_id, "consultation_timeout", CONSULTATION_SYSTEM_PRINCIPAL, 0, "human-tier-0"
            )
            return ConsultationOutcome(
                False, round_id, f"{depth.value} consultation timed out: escalated, dispatch held"
            )
        sleep(min(POLL_INTERVAL_SECONDS, remaining))


__all__ = [
    "CONSULTATION_ACTION",
    "CONSULTATION_SYSTEM_PRINCIPAL",
    "TIMEOUT_SWEEP_PRINCIPAL",
    "ConsultationError",
    "ConsultationOutcome",
    "eligible_electorate",
    "run_consultation",
    "select_reviewer",
]
