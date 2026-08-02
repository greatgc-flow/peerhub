from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import (
    DomainContractError,
    DomainRegistration,
    IsolatedDomainContext,
    require_bool,
    require_exact_fields,
    require_mapping,
    require_nonnegative_int,
    require_string,
)

_BASE_FIXTURES = tuple(
    f"AC-08-{index:02d}"
    for index in range(1, 8)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-08-01": (
        "authority_drain.ac0801.normal_completion"
    ),
    "AC-08-02": (
        "authority_drain.ac0802.cooperative_cancellation"
    ),
    "AC-08-03": (
        "authority_drain.ac0803.conjunctive_safe_abort"
    ),
    "AC-08-04": (
        "authority_drain.ac0804.static_classification"
    ),
    "AC-08-05": (
        "authority_drain.ac0805.process_birth_match"
    ),
    "AC-08-06": (
        "authority_drain.ac0806.external_effect_receipt"
    ),
    "AC-08-07": (
        "authority_drain.ac0807.identity_lock_hash_safety"
    ),
}

_LEASE_STATUSES = frozenset(
    {
        "ACTIVE",
        "TERMINAL",
    }
)
_LOOKUP_STATUSES = frozenset(
    {
        "RESOLVED",
        "ABSENT",
        "AMBIGUOUS",
    }
)
_EFFECT_CLASSES = frozenset(
    {
        "CONFIG_ONLY",
        "EXTERNAL_EFFECT",
        "UNKNOWN",
    }
)
_EFFECT_PHASES = frozenset(
    {
        "PRE_EFFECT",
        "MAY_HAVE_STARTED",
        "UNKNOWN",
    }
)
_DECISIONS = frozenset(
    {
        "PROCEED_TO_PRECOMMIT",
        "WAITING_FOR_DRAIN",
        "BLOCKED_INCOMPLETE_SAFE",
    }
)
_DISPOSITIONS = frozenset(
    {
        "DRAIN_COMPLETE",
        "DRAIN_WAITING",
        "COOPERATIVE_CANCELLATION",
        "CUTOFF_SAFE_ABORT",
        "INCOMPLETE_SAFE",
    }
)
_LEASE_DISPOSITIONS = frozenset(
    {
        "COMPLETED",
        "WAITING",
        "CANCEL_REQUESTED",
        "FORCE_ABORTED_SAFE",
        "INCOMPLETE_SAFE",
    }
)


def _base_fixture_id(
    fixture_id: str,
) -> str:
    for base in _BASE_FIXTURES:
        if fixture_id in {
            base,
            f"{base}{_NEGATIVE_SUFFIX}",
        }:
            return base

    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


def _require_enum(
    value: Any,
    allowed: frozenset[str],
    path: str,
    *,
    output: bool = False,
) -> str:
    actual = require_string(value, path)
    if actual not in allowed:
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            f"{path} unsupported={actual}",
        )
    return actual


def _require_nullable_string(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def _validate_classification(
    value: Any,
    path: str,
) -> dict[str, Any]:
    classification = require_mapping(value, path)
    require_exact_fields(
        classification,
        {
            "lookup_status",
            "ratified_table_digest",
            "observed_table_digest",
            "static_effect_class",
            "static_effect_phase",
            "self_declared_effect_class",
            "self_declared_effect_phase",
        },
        path=path,
    )

    lookup_status = _require_enum(
        classification["lookup_status"],
        _LOOKUP_STATUSES,
        f"{path}.lookup_status",
    )
    observed_digest = _require_nullable_string(
        classification["observed_table_digest"],
        f"{path}.observed_table_digest",
    )
    static_class = _require_enum(
        classification["static_effect_class"],
        _EFFECT_CLASSES,
        f"{path}.static_effect_class",
    )
    static_phase = _require_enum(
        classification["static_effect_phase"],
        _EFFECT_PHASES,
        f"{path}.static_effect_phase",
    )

    if lookup_status == "ABSENT":
        if observed_digest is not None:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{path}.observed_table_digest "
                    "must be null when lookup is ABSENT"
                ),
            )
        if (
            static_class != "UNKNOWN"
            or static_phase != "UNKNOWN"
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{path} absent lookup cannot carry "
                    "static classification"
                ),
            )

    if lookup_status == "AMBIGUOUS" and (
        static_class != "UNKNOWN"
        or static_phase != "UNKNOWN"
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} ambiguous lookup cannot carry "
                "a resolved static classification"
            ),
        )

    return {
        "lookup_status": lookup_status,
        "ratified_table_digest": require_string(
            classification["ratified_table_digest"],
            f"{path}.ratified_table_digest",
        ),
        "observed_table_digest": observed_digest,
        "static_effect_class": static_class,
        "static_effect_phase": static_phase,
        "self_declared_effect_class": _require_enum(
            classification["self_declared_effect_class"],
            frozenset(
                {
                    "CONFIG_ONLY",
                    "EXTERNAL_EFFECT",
                }
            ),
            f"{path}.self_declared_effect_class",
        ),
        "self_declared_effect_phase": _require_enum(
            classification["self_declared_effect_phase"],
            frozenset(
                {
                    "PRE_EFFECT",
                    "MAY_HAVE_STARTED",
                }
            ),
            f"{path}.self_declared_effect_phase",
        ),
    }


def _validate_lease(
    value: Any,
    path: str,
) -> dict[str, Any]:
    lease = require_mapping(value, path)
    require_exact_fields(
        lease,
        {
            "lease_id",
            "status",
            "terminal_receipt",
            "operation_may_have_external_effect",
            "provider_effect_visible",
            "classification",
            "process_birth_match",
            "identity_known",
            "lock_owner_known",
            "admission_hashes_unchanged",
        },
        path=path,
    )

    status = _require_enum(
        lease["status"],
        _LEASE_STATUSES,
        f"{path}.status",
    )
    terminal_receipt = require_bool(
        lease["terminal_receipt"],
        f"{path}.terminal_receipt",
    )

    if status == "ACTIVE" and terminal_receipt:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.terminal_receipt cannot be true "
                "for an ACTIVE lease"
            ),
        )

    return {
        "lease_id": require_string(
            lease["lease_id"],
            f"{path}.lease_id",
        ),
        "status": status,
        "terminal_receipt": terminal_receipt,
        "operation_may_have_external_effect": require_bool(
            lease["operation_may_have_external_effect"],
            (
                f"{path}."
                "operation_may_have_external_effect"
            ),
        ),
        "provider_effect_visible": require_bool(
            lease["provider_effect_visible"],
            f"{path}.provider_effect_visible",
        ),
        "classification": _validate_classification(
            lease["classification"],
            f"{path}.classification",
        ),
        "process_birth_match": require_bool(
            lease["process_birth_match"],
            f"{path}.process_birth_match",
        ),
        "identity_known": require_bool(
            lease["identity_known"],
            f"{path}.identity_known",
        ),
        "lock_owner_known": require_bool(
            lease["lock_owner_known"],
            f"{path}.lock_owner_known",
        ),
        "admission_hashes_unchanged": require_bool(
            lease["admission_hashes_unchanged"],
            f"{path}.admission_hashes_unchanged",
        ),
    }


def _classification_accepted(
    lease: Mapping[str, Any],
) -> bool:
    classification = lease["classification"]
    return (
        classification["lookup_status"] == "RESOLVED"
        and classification["observed_table_digest"]
        == classification["ratified_table_digest"]
        and classification["static_effect_class"]
        == "CONFIG_ONLY"
        and classification["static_effect_phase"]
        == "PRE_EFFECT"
    )


def _oracle_safety(
    lease: Mapping[str, Any],
) -> tuple[bool, bool, bool, bool, bool]:
    classification_ok = _classification_accepted(lease)
    process_birth_ok = lease["process_birth_match"]
    admission_hashes_ok = (
        lease["admission_hashes_unchanged"]
    )
    identity_and_lock_ok = (
        lease["identity_known"]
        and lease["lock_owner_known"]
    )
    receipt_rule_ok = (
        not lease["operation_may_have_external_effect"]
        or lease["terminal_receipt"]
    )
    return (
        classification_ok,
        process_birth_ok,
        admission_hashes_ok,
        identity_and_lock_ok,
        receipt_rule_ok,
    )


def _subject_safety(
    lease: Mapping[str, Any],
) -> tuple[bool, bool, bool, bool, bool]:
    static = lease["classification"]
    static_ok = (
        static["lookup_status"] == "RESOLVED"
        and static["ratified_table_digest"]
        == static["observed_table_digest"]
        and static["static_effect_class"] == "CONFIG_ONLY"
        and static["static_effect_phase"] == "PRE_EFFECT"
    )
    birth_ok = bool(lease["process_birth_match"])
    hashes_ok = bool(
        lease["admission_hashes_unchanged"]
    )
    custody_identity_ok = bool(
        lease["identity_known"]
        and lease["lock_owner_known"]
    )
    effect_evidence_ok = bool(
        (
            not lease[
                "operation_may_have_external_effect"
            ]
        )
        or lease["terminal_receipt"]
    )
    return (
        static_ok,
        birth_ok,
        hashes_ok,
        custody_identity_ok,
        effect_evidence_ok,
    )


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base = _base_fixture_id(fixture_id)
    elapsed = facts["elapsed_seconds"]
    leases = facts["leases"]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base}.{detail}",
        )

    if base == "AC-08-01":
        if elapsed >= 90:
            invalid("normal completion must occur before 90 seconds")
        if facts["new_lease_attempts"] < 1:
            invalid("must include a rejected new-lease attempt")
        if not all(
            lease["status"] == "TERMINAL"
            and lease["terminal_receipt"]
            for lease in leases
        ):
            invalid("all leases must be durably terminal")
        return

    if base == "AC-08-02":
        if elapsed != 90:
            invalid("cancellation vector must be at 90 seconds")
        if not any(
            lease["status"] == "ACTIVE"
            for lease in leases
        ):
            invalid("must contain an active lease")
        return

    if elapsed != 120:
        invalid("cutoff vectors must be at 120 seconds")

    if base == "AC-08-03":
        if not all(
            all(_oracle_safety(lease))
            for lease in leases
        ):
            invalid("every lease must meet every safe-abort condition")
        return

    if base == "AC-08-04":
        statuses = {
            lease["classification"]["lookup_status"]
            for lease in leases
        }
        has_digest_mismatch = any(
            lease["classification"]["lookup_status"]
            == "RESOLVED"
            and lease["classification"][
                "observed_table_digest"
            ]
            != lease["classification"][
                "ratified_table_digest"
            ]
            for lease in leases
        )
        if not {
            "ABSENT",
            "AMBIGUOUS",
        }.issubset(statuses):
            invalid("must include absent and ambiguous lookups")
        if not has_digest_mismatch:
            invalid("must include a classification digest mismatch")
        if not all(
            lease["classification"][
                "self_declared_effect_class"
            ]
            == "CONFIG_ONLY"
            and lease["classification"][
                "self_declared_effect_phase"
            ]
            == "PRE_EFFECT"
            for lease in leases
        ):
            invalid("self-declared safe classes are required")
        return

    if base == "AC-08-05":
        if not any(
            not lease["process_birth_match"]
            for lease in leases
        ):
            invalid("must include a process-birth mismatch")
        return

    if base == "AC-08-06":
        if not any(
            lease["operation_may_have_external_effect"]
            and not lease["terminal_receipt"]
            and not lease["provider_effect_visible"]
            for lease in leases
        ):
            invalid(
                "must include unreceipted external-effect capability"
            )
        return

    if base == "AC-08-07":
        if not any(
            not lease["identity_known"]
            for lease in leases
        ):
            invalid("must include unknown identity")
        if not any(
            not lease["lock_owner_known"]
            for lease in leases
        ):
            invalid("must include unknown lock owner")
        if not any(
            not lease["admission_hashes_unchanged"]
            for lease in leases
        ):
            invalid("must include changed admission hashes")


def validate_authority_drain_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize one closed AC-08 input."""

    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "elapsed_seconds",
            "admission_closed",
            "new_lease_attempts",
            "leases",
        },
        path="inputs",
    )

    raw_leases = inputs["leases"]
    if not isinstance(raw_leases, list) or not raw_leases:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.leases must be a non-empty array",
        )

    leases = [
        _validate_lease(
            lease,
            f"inputs.leases[{index}]",
        )
        for index, lease in enumerate(raw_leases)
    ]

    lease_ids = [
        lease["lease_id"]
        for lease in leases
    ]
    if len(set(lease_ids)) != len(lease_ids):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.leases contains duplicate lease_id values",
        )

    normalized = {
        "elapsed_seconds": require_nonnegative_int(
            inputs["elapsed_seconds"],
            "inputs.elapsed_seconds",
        ),
        "admission_closed": require_bool(
            inputs["admission_closed"],
            "inputs.admission_closed",
        ),
        "new_lease_attempts": require_nonnegative_int(
            inputs["new_lease_attempts"],
            "inputs.new_lease_attempts",
        ),
        "leases": leases,
    }

    if not normalized["admission_closed"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.admission_closed must be true during drain",
        )

    _validate_fixture_vector(
        fixture_id,
        normalized,
    )
    return normalized


def _validate_output_lease(
    value: Any,
    path: str,
) -> dict[str, Any]:
    lease = require_mapping(value, path)
    require_exact_fields(
        lease,
        {
            "lease_id",
            "disposition",
            "static_classification_accepted",
            "process_birth_match_accepted",
            "admission_hashes_accepted",
            "identity_and_lock_accepted",
            "terminal_receipt_accepted",
        },
        path=path,
    )
    return {
        "lease_id": require_string(
            lease["lease_id"],
            f"{path}.lease_id",
        ),
        "disposition": _require_enum(
            lease["disposition"],
            _LEASE_DISPOSITIONS,
            f"{path}.disposition",
            output=True,
        ),
        "static_classification_accepted": require_bool(
            lease["static_classification_accepted"],
            f"{path}.static_classification_accepted",
        ),
        "process_birth_match_accepted": require_bool(
            lease["process_birth_match_accepted"],
            f"{path}.process_birth_match_accepted",
        ),
        "admission_hashes_accepted": require_bool(
            lease["admission_hashes_accepted"],
            f"{path}.admission_hashes_accepted",
        ),
        "identity_and_lock_accepted": require_bool(
            lease["identity_and_lock_accepted"],
            f"{path}.identity_and_lock_accepted",
        ),
        "terminal_receipt_accepted": require_bool(
            lease["terminal_receipt_accepted"],
            f"{path}.terminal_receipt_accepted",
        ),
    }


def validate_authority_drain_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one exact AC-08 computed output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "domain_output",
    )
    require_exact_fields(
        output,
        {
            "decision",
            "disposition",
            "elapsed_seconds",
            "admission_closed",
            "new_leases_admitted",
            "cancellation_sent_at_seconds",
            "cutoff_applied",
            "classification_digest_checked",
            "lease_results",
            "marker_eligible",
            "reconciliation_required",
            "zero_provider_calls",
        },
        path="domain_output",
    )

    cancellation = output[
        "cancellation_sent_at_seconds"
    ]
    if cancellation is not None:
        cancellation = require_nonnegative_int(
            cancellation,
            (
                "domain_output."
                "cancellation_sent_at_seconds"
            ),
        )

    raw_results = output["lease_results"]
    if not isinstance(raw_results, list) or not raw_results:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "domain_output.lease_results must be non-empty",
        )

    results = [
        _validate_output_lease(
            result,
            f"domain_output.lease_results[{index}]",
        )
        for index, result in enumerate(raw_results)
    ]
    result_ids = [
        result["lease_id"]
        for result in results
    ]
    if len(set(result_ids)) != len(result_ids):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "domain_output.lease_results contains "
                "duplicate lease IDs"
            ),
        )

    return {
        "decision": _require_enum(
            output["decision"],
            _DECISIONS,
            "domain_output.decision",
            output=True,
        ),
        "disposition": _require_enum(
            output["disposition"],
            _DISPOSITIONS,
            "domain_output.disposition",
            output=True,
        ),
        "elapsed_seconds": require_nonnegative_int(
            output["elapsed_seconds"],
            "domain_output.elapsed_seconds",
        ),
        "admission_closed": require_bool(
            output["admission_closed"],
            "domain_output.admission_closed",
        ),
        "new_leases_admitted": require_nonnegative_int(
            output["new_leases_admitted"],
            "domain_output.new_leases_admitted",
        ),
        "cancellation_sent_at_seconds": cancellation,
        "cutoff_applied": require_bool(
            output["cutoff_applied"],
            "domain_output.cutoff_applied",
        ),
        "classification_digest_checked": require_bool(
            output["classification_digest_checked"],
            (
                "domain_output."
                "classification_digest_checked"
            ),
        ),
        "lease_results": results,
        "marker_eligible": require_bool(
            output["marker_eligible"],
            "domain_output.marker_eligible",
        ),
        "reconciliation_required": require_bool(
            output["reconciliation_required"],
            (
                "domain_output."
                "reconciliation_required"
            ),
        ),
        "zero_provider_calls": require_bool(
            output["zero_provider_calls"],
            "domain_output.zero_provider_calls",
        ),
    }


def _lease_result(
    lease: Mapping[str, Any],
    disposition: str,
    safety: tuple[
        bool,
        bool,
        bool,
        bool,
        bool,
    ] | None = None,
) -> dict[str, Any]:
    if safety is None:
        safety = (
            False,
            lease["process_birth_match"],
            lease["admission_hashes_unchanged"],
            (
                lease["identity_known"]
                and lease["lock_owner_known"]
            ),
            (
                not lease[
                    "operation_may_have_external_effect"
                ]
                or lease["terminal_receipt"]
            ),
        )

    return {
        "lease_id": lease["lease_id"],
        "disposition": disposition,
        "static_classification_accepted": safety[0],
        "process_birth_match_accepted": safety[1],
        "admission_hashes_accepted": safety[2],
        "identity_and_lock_accepted": safety[3],
        "terminal_receipt_accepted": safety[4],
    }


def _output(
    facts: Mapping[str, Any],
    *,
    decision: str,
    disposition: str,
    lease_results: list[dict[str, Any]],
    cancellation_sent_at_seconds: int | None,
    cutoff_applied: bool,
    classification_digest_checked: bool,
    marker_eligible: bool,
    reconciliation_required: bool,
    new_leases_admitted: int = 0,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "disposition": disposition,
        "elapsed_seconds": facts["elapsed_seconds"],
        "admission_closed": facts["admission_closed"],
        "new_leases_admitted": new_leases_admitted,
        "cancellation_sent_at_seconds": (
            cancellation_sent_at_seconds
        ),
        "cutoff_applied": cutoff_applied,
        "classification_digest_checked": (
            classification_digest_checked
        ),
        "lease_results": lease_results,
        "marker_eligible": marker_eligible,
        "reconciliation_required": reconciliation_required,
        "zero_provider_calls": True,
    }


def _oracle_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    leases = facts["leases"]

    if all(
        lease["status"] == "TERMINAL"
        and lease["terminal_receipt"]
        for lease in leases
    ):
        return _output(
            facts,
            decision="PROCEED_TO_PRECOMMIT",
            disposition="DRAIN_COMPLETE",
            lease_results=[
                _lease_result(
                    lease,
                    "COMPLETED",
                )
                for lease in leases
            ],
            cancellation_sent_at_seconds=None,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=True,
            reconciliation_required=False,
        )

    if facts["elapsed_seconds"] < 90:
        return _output(
            facts,
            decision="WAITING_FOR_DRAIN",
            disposition="DRAIN_WAITING",
            lease_results=[
                _lease_result(
                    lease,
                    (
                        "COMPLETED"
                        if (
                            lease["status"] == "TERMINAL"
                            and lease["terminal_receipt"]
                        )
                        else "WAITING"
                    ),
                )
                for lease in leases
            ],
            cancellation_sent_at_seconds=None,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=False,
            reconciliation_required=False,
        )

    if facts["elapsed_seconds"] < 120:
        return _output(
            facts,
            decision="WAITING_FOR_DRAIN",
            disposition="COOPERATIVE_CANCELLATION",
            lease_results=[
                _lease_result(
                    lease,
                    (
                        "COMPLETED"
                        if (
                            lease["status"] == "TERMINAL"
                            and lease["terminal_receipt"]
                        )
                        else "CANCEL_REQUESTED"
                    ),
                )
                for lease in leases
            ],
            cancellation_sent_at_seconds=90,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=False,
            reconciliation_required=False,
        )

    results: list[dict[str, Any]] = []
    incomplete = False

    for lease in leases:
        if (
            lease["status"] == "TERMINAL"
            and lease["terminal_receipt"]
        ):
            results.append(
                _lease_result(
                    lease,
                    "COMPLETED",
                )
            )
            continue

        safety = _oracle_safety(lease)
        if all(safety):
            results.append(
                _lease_result(
                    lease,
                    "FORCE_ABORTED_SAFE",
                    safety,
                )
            )
        else:
            incomplete = True
            results.append(
                _lease_result(
                    lease,
                    "INCOMPLETE_SAFE",
                    safety,
                )
            )

    return _output(
        facts,
        decision=(
            "BLOCKED_INCOMPLETE_SAFE"
            if incomplete
            else "PROCEED_TO_PRECOMMIT"
        ),
        disposition=(
            "INCOMPLETE_SAFE"
            if incomplete
            else "CUTOFF_SAFE_ABORT"
        ),
        lease_results=results,
        cancellation_sent_at_seconds=None,
        cutoff_applied=True,
        classification_digest_checked=True,
        marker_eligible=not incomplete,
        reconciliation_required=incomplete,
    )


def _subject_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    leases = facts["leases"]
    completed = [
        lease
        for lease in leases
        if (
            lease["status"] == "TERMINAL"
            and lease["terminal_receipt"]
        )
    ]

    if len(completed) == len(leases):
        rows = [
            _lease_result(
                lease,
                "COMPLETED",
            )
            for lease in leases
        ]
        return _output(
            facts,
            decision="PROCEED_TO_PRECOMMIT",
            disposition="DRAIN_COMPLETE",
            lease_results=rows,
            cancellation_sent_at_seconds=None,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=True,
            reconciliation_required=False,
        )

    elapsed = facts["elapsed_seconds"]
    if elapsed < 90:
        rows = [
            _lease_result(
                lease,
                (
                    "COMPLETED"
                    if lease in completed
                    else "WAITING"
                ),
            )
            for lease in leases
        ]
        return _output(
            facts,
            decision="WAITING_FOR_DRAIN",
            disposition="DRAIN_WAITING",
            lease_results=rows,
            cancellation_sent_at_seconds=None,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=False,
            reconciliation_required=False,
        )

    if elapsed < 120:
        rows = [
            _lease_result(
                lease,
                (
                    "COMPLETED"
                    if lease in completed
                    else "CANCEL_REQUESTED"
                ),
            )
            for lease in leases
        ]
        return _output(
            facts,
            decision="WAITING_FOR_DRAIN",
            disposition="COOPERATIVE_CANCELLATION",
            lease_results=rows,
            cancellation_sent_at_seconds=90,
            cutoff_applied=False,
            classification_digest_checked=False,
            marker_eligible=False,
            reconciliation_required=False,
        )

    rows: list[dict[str, Any]] = []
    blocked = False

    for lease in leases:
        if lease in completed:
            rows.append(
                _lease_result(
                    lease,
                    "COMPLETED",
                )
            )
            continue

        checks = _subject_safety(lease)
        allowed = (
            checks[0]
            and checks[1]
            and checks[2]
            and checks[3]
            and checks[4]
        )
        if allowed:
            rows.append(
                _lease_result(
                    lease,
                    "FORCE_ABORTED_SAFE",
                    checks,
                )
            )
        else:
            blocked = True
            rows.append(
                _lease_result(
                    lease,
                    "INCOMPLETE_SAFE",
                    checks,
                )
            )

    return _output(
        facts,
        decision=(
            "BLOCKED_INCOMPLETE_SAFE"
            if blocked
            else "PROCEED_TO_PRECOMMIT"
        ),
        disposition=(
            "INCOMPLETE_SAFE"
            if blocked
            else "CUTOFF_SAFE_ABORT"
        ),
        lease_results=rows,
        cancellation_sent_at_seconds=None,
        cutoff_applied=True,
        classification_digest_checked=True,
        marker_eligible=not blocked,
        reconciliation_required=blocked,
    )


class AuthorityDrainOracle:
    """Pure AC-08 oracle over injected drain evidence."""

    oracle_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        self.oracle_id = _ORACLE_IDS[
            base_fixture_id
        ]
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
                (
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                ),
            }
        )

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if (
            _base_fixture_id(fixture_id)
            != self._base
        ):
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"oracle={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )
        return _oracle_evaluate(raw_inputs)


class AuthorityDrainSubjectAdapter:
    """Pure AC-08 reference adapter over injected facts."""

    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            f"authority_drain.{label}.reference"
        )
        self.fixture_ids = frozenset(
            {base_fixture_id}
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id not in self.fixture_ids:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )
        return _subject_evaluate(raw_inputs)


def _unsafe_force_all(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    return _output(
        facts,
        decision="PROCEED_TO_PRECOMMIT",
        disposition="CUTOFF_SAFE_ABORT",
        lease_results=[
            _lease_result(
                lease,
                "FORCE_ABORTED_SAFE",
                _subject_safety(lease),
            )
            for lease in facts["leases"]
        ],
        cancellation_sent_at_seconds=None,
        cutoff_applied=True,
        classification_digest_checked=True,
        marker_eligible=True,
        reconciliation_required=False,
    )


class FaultInjectedAuthorityDrainAdapter(
    AuthorityDrainSubjectAdapter
):
    """One genuine drain-safety defect per negative."""

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        super().__init__(base_fixture_id)
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            f"authority_drain.{label}.fault_injected"
        )
        self.fixture_ids = frozenset(
            {
                (
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                )
            }
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id not in self.fixture_ids:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "AC-08-01":
            return _output(
                raw_inputs,
                decision="WAITING_FOR_DRAIN",
                disposition="DRAIN_WAITING",
                lease_results=[
                    _lease_result(
                        lease,
                        "COMPLETED",
                    )
                    for lease in raw_inputs["leases"]
                ],
                cancellation_sent_at_seconds=None,
                cutoff_applied=False,
                classification_digest_checked=False,
                marker_eligible=False,
                reconciliation_required=False,
                new_leases_admitted=raw_inputs[
                    "new_lease_attempts"
                ],
            )

        if self._base == "AC-08-02":
            return _unsafe_force_all(raw_inputs)

        if self._base == "AC-08-03":
            return _output(
                raw_inputs,
                decision="WAITING_FOR_DRAIN",
                disposition="COOPERATIVE_CANCELLATION",
                lease_results=[
                    _lease_result(
                        lease,
                        "CANCEL_REQUESTED",
                    )
                    for lease in raw_inputs["leases"]
                ],
                cancellation_sent_at_seconds=90,
                cutoff_applied=False,
                classification_digest_checked=False,
                marker_eligible=False,
                reconciliation_required=False,
            )

        if self._base in {
            "AC-08-04",
            "AC-08-05",
            "AC-08-07",
        }:
            return _unsafe_force_all(raw_inputs)

        return _output(
            raw_inputs,
            decision="PROCEED_TO_PRECOMMIT",
            disposition="DRAIN_COMPLETE",
            lease_results=[
                _lease_result(
                    lease,
                    "COMPLETED",
                    _subject_safety(lease),
                )
                for lease in raw_inputs["leases"]
            ],
            cancellation_sent_at_seconds=None,
            cutoff_applied=True,
            classification_digest_checked=True,
            marker_eligible=True,
            reconciliation_required=False,
        )


def authority_drain_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable AC-08 registrations."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityDrainOracle(
            base_fixture_id
        )
        reference = AuthorityDrainSubjectAdapter(
            base_fixture_id
        )
        fault = FaultInjectedAuthorityDrainAdapter(
            base_fixture_id
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=reference,
                input_validator=(
                    validate_authority_drain_inputs
                ),
                output_validator=(
                    validate_authority_drain_output
                ),
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=(
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                ),
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=fault,
                input_validator=(
                    validate_authority_drain_inputs
                ),
                output_validator=(
                    validate_authority_drain_output
                ),
            )
        )

    return tuple(registrations)