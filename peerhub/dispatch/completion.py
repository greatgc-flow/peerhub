"""Pure completion assessor for the three-layer outcome model.

``assess_completion`` is a purely functional reducer that composes
``CompletionContract``, ``ExecutionOutcome``, ``ProtocolAssessment``,
and already-verified per-requirement evidence into a single
``CompletionAssessment``.  It never inspects the filesystem or any
mutable state (``PROTOCOL-V1-FREEZE.md`` forbids reopening a path
after verification -- artifact I/O produces verified
identity/digest evidence first, and this pure assessor consumes only
that evidence).

Design ratified in ``SLICE5-KICKOFF-R1.md``'s
"artifacts.py/completion.py contract RATIFIED (2026-08-03, ag+cx
unanimous)" section.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from peerhub.adapters.contract import ProtocolAssessment
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import (
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
)


class RequirementVerdict(str, Enum):
    """Per-requirement evaluation outcome."""

    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class RequirementEvaluation:
    """Evaluation of a single frozen requirement.

    ``requirement_index`` is the 0-based position within
    ``CompletionContract.requirements`` -- the assessor uses it to
    verify complete, non-duplicated coverage.
    """

    requirement_index: int
    verdict: RequirementVerdict
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.requirement_index) is not int
            or self.requirement_index < 0
        ):
            raise ValueError(
                "requirement_index must be a nonnegative integer"
            )
        if not isinstance(self.verdict, RequirementVerdict):
            raise ValueError(
                "verdict must be a RequirementVerdict"
            )


def assess_completion(
    contract: CompletionContract,
    execution: ExecutionOutcome,
    protocol: ProtocolAssessment,
    *,
    requirement_evaluations: tuple[RequirementEvaluation, ...] = (),
) -> CompletionAssessment:
    """Purely functional completion assessment.

    Decision table (ratified):
    - Execution not terminal/clean (timeout/cancelled/nonzero exit/
      protocol failure) -> NOT_APPLICABLE regardless of contract kind.
    - No requirements + clean delivery -> VERIFIED (DELIVERY_ONLY).
    - Any unsatisfied requirement -> INCOMPLETE.
    - Ambiguous/unverifiable requirement -> UNVERIFIED.
    - All requirements satisfied -> VERIFIED.
    - DELIVERY_ONLY + no response -> INCOMPLETE.
    - DELIVERY_ONLY + suspected_truncation -> UNVERIFIED.
    """
    contract_kind = contract.kind

    # --- Execution/protocol gate: NOT_APPLICABLE -----------------------
    if _execution_not_applicable(execution, protocol):
        return CompletionAssessment(
            state=CompletionAssessmentState.NOT_APPLICABLE,
            contract_kind=contract_kind,
        )

    # --- DELIVERY_ONLY contracts (zero requirement) --------------------
    if contract_kind is CompletionContractKind.DELIVERY_ONLY:
        return _assess_delivery_only(protocol, contract_kind)

    # --- Contracts with requirements -----------------------------------
    return _assess_with_requirements(
        contract, protocol, contract_kind, requirement_evaluations
    )


def _execution_not_applicable(
    execution: ExecutionOutcome,
    protocol: ProtocolAssessment,
) -> bool:
    """True when execution/protocol signals preclude any assessment."""
    if execution.timed_out:
        return True
    if execution.cancelled:
        return True
    if not execution.started:
        return True
    if (
        execution.execution_certainty
        is not ExecutionCertainty.TERMINAL
    ):
        return True
    if execution.exit_code is not None and execution.exit_code != 0:
        return True
    if protocol.protocol_failure is not None:
        return True
    return False


def _assess_delivery_only(
    protocol: ProtocolAssessment,
    contract_kind: CompletionContractKind,
) -> CompletionAssessment:
    """Assess a DELIVERY_ONLY contract after execution is clean."""
    if not protocol.response_present:
        return CompletionAssessment(
            state=CompletionAssessmentState.INCOMPLETE,
            contract_kind=contract_kind,
            failed_requirements=("response_present",),
        )
    if protocol.suspected_truncation:
        return CompletionAssessment(
            state=CompletionAssessmentState.UNVERIFIED,
            contract_kind=contract_kind,
        )
    # Zero-requirement contract, clean delivery -> VERIFIED.
    return CompletionAssessment(
        state=CompletionAssessmentState.VERIFIED,
        contract_kind=contract_kind,
    )


def _assess_with_requirements(
    contract: CompletionContract,
    protocol: ProtocolAssessment,
    contract_kind: CompletionContractKind,
    evaluations: tuple[RequirementEvaluation, ...],
) -> CompletionAssessment:
    """Assess a contract that declares at least one requirement."""
    requirement_count = len(contract.requirements)

    # Verify complete, non-duplicated requirement coverage.
    seen_indices: set[int] = set()
    for evaluation in evaluations:
        if evaluation.requirement_index in seen_indices:
            raise ValueError(
                f"duplicate requirement_index: "
                f"{evaluation.requirement_index}"
            )
        if evaluation.requirement_index >= requirement_count:
            raise ValueError(
                f"requirement_index {evaluation.requirement_index} "
                f"out of range (contract has "
                f"{requirement_count} requirements)"
            )
        seen_indices.add(evaluation.requirement_index)

    if len(seen_indices) != requirement_count:
        missing = set(range(requirement_count)) - seen_indices
        raise ValueError(
            f"incomplete requirement coverage: "
            f"missing indices {sorted(missing)}"
        )

    # Aggregate verdicts.
    failed: list[str] = []
    evidence: list[str] = []
    has_unverifiable = False

    for evaluation in evaluations:
        if evaluation.verdict is RequirementVerdict.UNSATISFIED:
            failed.append(str(evaluation.requirement_index))
        elif evaluation.verdict is RequirementVerdict.UNVERIFIABLE:
            has_unverifiable = True
        elif evaluation.verdict is RequirementVerdict.SATISFIED:
            if evaluation.evidence_ref is not None:
                evidence.append(evaluation.evidence_ref)

    if failed:
        return CompletionAssessment(
            state=CompletionAssessmentState.INCOMPLETE,
            contract_kind=contract_kind,
            failed_requirements=tuple(failed),
            evidence_refs=tuple(evidence),
        )

    if has_unverifiable:
        return CompletionAssessment(
            state=CompletionAssessmentState.UNVERIFIED,
            contract_kind=contract_kind,
            evidence_refs=tuple(evidence),
        )

    return CompletionAssessment(
        state=CompletionAssessmentState.VERIFIED,
        contract_kind=contract_kind,
        evidence_refs=tuple(evidence),
    )


# --- Contract-aware predicates (ratified enforcement) ------------------
#
# "No bare state == VERIFIED checks outside the assessment module --
#  provide an explicit, contract-kind-aware predicate that exhaustively
#  matches on contract_kind." (SLICE5-KICKOFF-R1.md)


def is_promotion_eligible(assessment: CompletionAssessment) -> bool:
    """Contract-kind-aware promotion eligibility predicate.

    Exhaustively matches on every ``CompletionContractKind`` member.
    Returns ``True`` only when the assessment state is ``VERIFIED``
    AND the contract kind is one where promotion is meaningful.

    Callers MUST use this predicate (or a similarly exhaustive one)
    instead of bare ``assessment.state == VERIFIED`` checks.
    """
    if assessment.state is not CompletionAssessmentState.VERIFIED:
        return False

    # Exhaustive match on every known contract kind.
    # If a new kind is added to CompletionContractKind without a
    # branch here, the else-raise below will fail loudly.
    kind = assessment.contract_kind
    if kind is CompletionContractKind.DELIVERY_ONLY:
        return True
    if kind is CompletionContractKind.ARTIFACT_REQUIRED:
        return True
    if kind is CompletionContractKind.SCHEMA_VALIDATED:
        return True
    if kind is CompletionContractKind.FIELD_REQUIRED:
        return True
    if kind is CompletionContractKind.CUSTOM_VERIFIER:
        return True
    if kind is CompletionContractKind.VENDOR_RECEIPT:
        return True

    raise ValueError(
        f"unhandled CompletionContractKind in "
        f"is_promotion_eligible: {kind!r}"
    )


def promotion_contract_kind(
    assessment: CompletionAssessment,
) -> CompletionContractKind | None:
    """Return the contract kind if promotion-eligible, else None.

    Provides the segmentation distinction the ratified design requires:
    callers that need to distinguish DELIVERY_ONLY-VERIFIED from
    ARTIFACT_REQUIRED-VERIFIED (e.g. telemetry dashboards that must
    never aggregate verification rates across heterogeneous contract
    kinds) use this instead of a bare boolean.
    """
    if is_promotion_eligible(assessment):
        return assessment.contract_kind
    return None
