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
    f"CS-{index:02d}"
    for index in range(1, 7)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "CS-01": "consensus.cs01.round_contract_freeze",
    "CS-02": "consensus.cs02.idempotent_repeat_vote",
    "CS-03": "consensus.cs03.conflicting_vote_rejected",
    "CS-04": "consensus.cs04.timeout_escalation",
    "CS-05": "consensus.cs05.single_unanimous_decision",
    "CS-06": "consensus.cs06.arbiter_separated_derivation",
}

_CASE_KINDS = frozenset(
    {
        "PROPOSAL_FREEZE",
        "IDENTICAL_REPEAT_VOTE",
        "CONFLICTING_REPEAT_VOTE",
        "TIMEOUT_SWEEP",
        "UNANIMOUS_FINALIZATION",
        "ARBITER_DERIVATION",
    }
)
_RULE_TIERS = frozenset(
    {
        "OBS",
        "CANDIDATE",
    }
)
_DECISIONS = frozenset(
    {
        "ROUND_CONTRACT_FROZEN",
        "VOTE_IDEMPOTENT_NOOP",
        "VOTE_REJECTED",
        "ROUND_ESCALATED",
        "ROUND_FINALIZED",
        "OUTCOMES_DERIVED",
    }
)
_VOTE_VALUES = frozenset(
    {
        "AGREE",
        "DISAGREE",
    }
)
_ARBITER_VALUES = frozenset(
    {
        "APPROVE",
        "REJECT",
    }
)
_EFFECTIVE_OUTCOMES = frozenset(
    {
        "APPROVED",
        "REJECTED",
    }
)
_DERIVATION_BASES = frozenset(
    {
        "VOTE_NO_DISSENT",
        "DISSENT_NO_ARBITER",
        "ARBITER_OPINION",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
    for base_fixture_id in _BASE_FIXTURES:
        if fixture_id in {
            base_fixture_id,
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
        }:
            return base_fixture_id

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


def _require_string_list(
    value: Any,
    path: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    if not value and not allow_empty:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be non-empty",
        )

    values = [
        require_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicates",
        )
    return values


def _validate_vote(
    value: Any,
    path: str,
) -> dict[str, str]:
    vote = require_mapping(value, path)
    require_exact_fields(
        vote,
        {
            "voter_id",
            "vote_value",
            "reason",
        },
        path=path,
    )

    return {
        "voter_id": require_string(
            vote["voter_id"],
            f"{path}.voter_id",
        ),
        "vote_value": _require_enum(
            vote["vote_value"],
            _VOTE_VALUES,
            f"{path}.vote_value",
        ),
        "reason": require_string(
            vote["reason"],
            f"{path}.reason",
        ),
    }


def _validate_vote_list(
    value: Any,
    path: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-empty array",
        )

    votes = [
        _validate_vote(
            vote,
            f"{path}[{index}]",
        )
        for index, vote in enumerate(value)
    ]
    voter_ids = [
        vote["voter_id"]
        for vote in votes
    ]
    if len(voter_ids) != len(set(voter_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate voters",
        )
    return votes


def _validate_exclusion(
    value: Any,
    path: str,
) -> dict[str, str]:
    exclusion = require_mapping(value, path)
    require_exact_fields(
        exclusion,
        {
            "voter_id",
            "reason",
        },
        path=path,
    )

    return {
        "voter_id": require_string(
            exclusion["voter_id"],
            f"{path}.voter_id",
        ),
        "reason": require_string(
            exclusion["reason"],
            f"{path}.reason",
        ),
    }


def _validate_exclusions(
    value: Any,
    path: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-empty array",
        )

    exclusions = [
        _validate_exclusion(
            exclusion,
            f"{path}[{index}]",
        )
        for index, exclusion in enumerate(value)
    ]
    voter_ids = [
        exclusion["voter_id"]
        for exclusion in exclusions
    ]
    if len(voter_ids) != len(set(voter_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate voters",
        )
    return exclusions


def _validate_arbiter_opinion(
    value: Any,
    path: str,
) -> dict[str, str]:
    opinion = require_mapping(value, path)
    require_exact_fields(
        opinion,
        {
            "opinion_id",
            "opinion_value",
        },
        path=path,
    )

    return {
        "opinion_id": require_string(
            opinion["opinion_id"],
            f"{path}.opinion_id",
        ),
        "opinion_value": _require_enum(
            opinion["opinion_value"],
            _ARBITER_VALUES,
            f"{path}.opinion_value",
        ),
    }


def _validate_derivation_case(
    value: Any,
    path: str,
) -> dict[str, Any]:
    case = require_mapping(value, path)
    require_exact_fields(
        case,
        {
            "case_id",
            "votes",
            "arbiter_opinion",
        },
        path=path,
    )

    raw_opinion = case["arbiter_opinion"]
    opinion = (
        None
        if raw_opinion is None
        else _validate_arbiter_opinion(
            raw_opinion,
            f"{path}.arbiter_opinion",
        )
    )

    return {
        "case_id": require_string(
            case["case_id"],
            f"{path}.case_id",
        ),
        "votes": _validate_vote_list(
            case["votes"],
            f"{path}.votes",
        ),
        "arbiter_opinion": opinion,
    }


def _validate_proposal_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "round_id",
            "policy_revision",
            "decision_rule",
            "voters",
            "exclusions",
        },
        path=path,
    )

    voters = _require_string_list(
        facts["voters"],
        f"{path}.voters",
    )
    exclusions = _validate_exclusions(
        facts["exclusions"],
        f"{path}.exclusions",
    )

    if any(
        exclusion["voter_id"] not in voters
        for exclusion in exclusions
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.exclusions references unknown voter",
        )

    return {
        "round_id": require_string(
            facts["round_id"],
            f"{path}.round_id",
        ),
        "policy_revision": require_string(
            facts["policy_revision"],
            f"{path}.policy_revision",
        ),
        "decision_rule": require_string(
            facts["decision_rule"],
            f"{path}.decision_rule",
        ),
        "voters": voters,
        "exclusions": exclusions,
    }


def _validate_repeat_vote_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "round_id",
            "existing_vote",
            "attempted_vote",
        },
        path=path,
    )

    return {
        "round_id": require_string(
            facts["round_id"],
            f"{path}.round_id",
        ),
        "existing_vote": _validate_vote(
            facts["existing_vote"],
            f"{path}.existing_vote",
        ),
        "attempted_vote": _validate_vote(
            facts["attempted_vote"],
            f"{path}.attempted_vote",
        ),
    }


def _validate_timeout_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "round_id",
            "electorate",
            "recorded_votes",
            "sweep_timeout_seconds",
        },
        path=path,
    )

    electorate = _require_string_list(
        facts["electorate"],
        f"{path}.electorate",
    )
    votes = _validate_vote_list(
        facts["recorded_votes"],
        f"{path}.recorded_votes",
    )
    if any(
        vote["voter_id"] not in electorate
        for vote in votes
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.recorded_votes contains non-electorate voter",
        )

    return {
        "round_id": require_string(
            facts["round_id"],
            f"{path}.round_id",
        ),
        "electorate": electorate,
        "recorded_votes": votes,
        "sweep_timeout_seconds": require_nonnegative_int(
            facts["sweep_timeout_seconds"],
            f"{path}.sweep_timeout_seconds",
        ),
    }


def _validate_finalization_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "round_id",
            "required_voters",
            "recorded_votes",
        },
        path=path,
    )

    required_voters = _require_string_list(
        facts["required_voters"],
        f"{path}.required_voters",
    )
    votes = _validate_vote_list(
        facts["recorded_votes"],
        f"{path}.recorded_votes",
    )

    return {
        "round_id": require_string(
            facts["round_id"],
            f"{path}.round_id",
        ),
        "required_voters": required_voters,
        "recorded_votes": votes,
    }


def _validate_arbiter_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "round_id",
            "derivation_cases",
        },
        path=path,
    )

    raw_cases = facts["derivation_cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.derivation_cases must be non-empty",
        )

    cases = [
        _validate_derivation_case(
            case,
            f"{path}.derivation_cases[{index}]",
        )
        for index, case in enumerate(raw_cases)
    ]
    case_ids = [
        case["case_id"]
        for case in cases
    ]
    if len(case_ids) != len(set(case_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.derivation_cases contains duplicate IDs",
        )

    return {
        "round_id": require_string(
            facts["round_id"],
            f"{path}.round_id",
        ),
        "derivation_cases": cases,
    }


def _dissent_voters(
    votes: list[Mapping[str, Any]],
) -> list[str]:
    return [
        vote["voter_id"]
        for vote in votes
        if vote["vote_value"] == "DISAGREE"
    ]


def _validate_fixture_vector(
    fixture_id: str,
    inputs: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)
    facts = inputs["facts"]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base_fixture_id}.{detail}",
        )

    expected_case_kinds = {
        "CS-01": "PROPOSAL_FREEZE",
        "CS-02": "IDENTICAL_REPEAT_VOTE",
        "CS-03": "CONFLICTING_REPEAT_VOTE",
        "CS-04": "TIMEOUT_SWEEP",
        "CS-05": "UNANIMOUS_FINALIZATION",
        "CS-06": "ARBITER_DERIVATION",
    }
    if (
        inputs["case_kind"]
        != expected_case_kinds[base_fixture_id]
    ):
        invalid(
            "case_kind does not match fixture"
        )

    if base_fixture_id == "CS-01":
        if facts["decision_rule"] != "UNANIMOUS":
            invalid("requires UNANIMOUS decision rule")
        if facts["voters"] != ["cc", "ag", "cx"]:
            invalid("requires observed voter order cc,ag,cx")
        if facts["exclusions"] != [
            {
                "voter_id": "cx",
                "reason": "health_red",
            }
        ]:
            invalid("requires cx excluded for health_red")
        return

    if base_fixture_id == "CS-02":
        existing = facts["existing_vote"]
        attempted = facts["attempted_vote"]
        if existing != attempted:
            invalid("requires an identical repeated vote")
        if (
            existing["voter_id"] != "ag"
            or existing["vote_value"] != "AGREE"
            or existing["reason"] != "phase0-idempotent"
        ):
            invalid("requires observed ag agree vote")
        return

    if base_fixture_id == "CS-03":
        existing = facts["existing_vote"]
        attempted = facts["attempted_vote"]
        if (
            existing["voter_id"]
            != attempted["voter_id"]
        ):
            invalid("conflicting votes must share a voter")
        if (
            existing["vote_value"]
            == attempted["vote_value"]
        ):
            invalid("requires differing vote values")
        if (
            existing["voter_id"] != "ag"
            or existing["vote_value"] != "AGREE"
            or attempted["vote_value"] != "DISAGREE"
        ):
            invalid("requires observed ag agree then disagree")
        return

    if base_fixture_id == "CS-04":
        if facts["sweep_timeout_seconds"] != 0:
            invalid("requires timeout zero sweep")
        if facts["electorate"] != ["ag", "cc", "cx"]:
            invalid("requires observed electorate ag,cc,cx")
        if facts["recorded_votes"] != [
            {
                "voter_id": "ag",
                "vote_value": "AGREE",
                "reason": "retained-before-timeout",
            }
        ]:
            invalid("requires retained ag agree vote")
        return

    if base_fixture_id == "CS-05":
        if facts["required_voters"] != ["cc", "ag"]:
            invalid("requires observed required voters cc,ag")
        vote_by_voter = {
            vote["voter_id"]: vote["vote_value"]
            for vote in facts["recorded_votes"]
        }
        if set(vote_by_voter) != {"cc", "ag"}:
            invalid("requires one vote from every required voter")
        if set(vote_by_voter.values()) != {"AGREE"}:
            invalid("requires unanimous agree votes")
        return

    cases = facts["derivation_cases"]
    if [
        case["case_id"]
        for case in cases
    ] != [
        "no-dissent",
        "dissent-no-opinion",
        "dissent-with-opinion",
    ]:
        invalid("requires all three candidate derivation branches")

    first, second, third = cases
    if (
        _dissent_voters(first["votes"])
        or first["arbiter_opinion"] is not None
    ):
        invalid("first branch requires no dissent or opinion")
    if (
        not _dissent_voters(second["votes"])
        or second["arbiter_opinion"] is not None
    ):
        invalid("second branch requires dissent without opinion")
    if (
        not _dissent_voters(third["votes"])
        or third["arbiter_opinion"] is None
        or third["arbiter_opinion"][
            "opinion_value"
        ] != "APPROVE"
    ):
        invalid(
            "third branch requires dissent and APPROVE opinion"
        )


def validate_consensus_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed CS-01..06 consensus vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "case_kind",
            "facts",
        },
        path="inputs",
    )

    case_kind = _require_enum(
        inputs["case_kind"],
        _CASE_KINDS,
        "inputs.case_kind",
    )

    if case_kind == "PROPOSAL_FREEZE":
        facts = _validate_proposal_facts(
            inputs["facts"],
            "inputs.facts",
        )
    elif case_kind in {
        "IDENTICAL_REPEAT_VOTE",
        "CONFLICTING_REPEAT_VOTE",
    }:
        facts = _validate_repeat_vote_facts(
            inputs["facts"],
            "inputs.facts",
        )
    elif case_kind == "TIMEOUT_SWEEP":
        facts = _validate_timeout_facts(
            inputs["facts"],
            "inputs.facts",
        )
    elif case_kind == "UNANIMOUS_FINALIZATION":
        facts = _validate_finalization_facts(
            inputs["facts"],
            "inputs.facts",
        )
    else:
        facts = _validate_arbiter_facts(
            inputs["facts"],
            "inputs.facts",
        )

    normalized = {
        "case_kind": case_kind,
        "facts": facts,
    }
    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def _copy_vote(
    vote: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "voter_id": vote["voter_id"],
        "vote_value": vote["vote_value"],
        "reason": vote["reason"],
    }


def _validate_output_vote_list(
    value: Any,
    path: str,
    *,
    allow_empty: bool = False,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} must be an array",
        )
    if not value and not allow_empty:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} must be non-empty",
        )
    return [
        _validate_vote(
            vote,
            f"{path}[{index}]",
        )
        for index, vote in enumerate(value)
    ]


def _validate_derivation_row(
    value: Any,
    path: str,
) -> dict[str, Any]:
    row = require_mapping(value, path)
    require_exact_fields(
        row,
        {
            "case_id",
            "dissent_present",
            "dissent_voters",
            "vote_value",
            "arbiter_opinion_recorded",
            "arbiter_opinion_value",
            "effective_outcome",
            "derivation_basis",
        },
        path=path,
    )

    opinion_value = _require_nullable_string(
        row["arbiter_opinion_value"],
        f"{path}.arbiter_opinion_value",
    )
    if (
        opinion_value is not None
        and opinion_value not in _ARBITER_VALUES
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.arbiter_opinion_value "
                f"unsupported={opinion_value}"
            ),
        )

    return {
        "case_id": require_string(
            row["case_id"],
            f"{path}.case_id",
        ),
        "dissent_present": require_bool(
            row["dissent_present"],
            f"{path}.dissent_present",
        ),
        "dissent_voters": _require_string_list(
            row["dissent_voters"],
            f"{path}.dissent_voters",
            allow_empty=True,
        ),
        "vote_value": _require_enum(
            row["vote_value"],
            _ARBITER_VALUES,
            f"{path}.vote_value",
            output=True,
        ),
        "arbiter_opinion_recorded": require_bool(
            row["arbiter_opinion_recorded"],
            f"{path}.arbiter_opinion_recorded",
        ),
        "arbiter_opinion_value": opinion_value,
        "effective_outcome": _require_enum(
            row["effective_outcome"],
            _EFFECTIVE_OUTCOMES,
            f"{path}.effective_outcome",
            output=True,
        ),
        "derivation_basis": _require_enum(
            row["derivation_basis"],
            _DERIVATION_BASES,
            f"{path}.derivation_basis",
            output=True,
        ),
    }


def _validate_output_details(
    fixture_id: str,
    value: Any,
) -> dict[str, Any]:
    base_fixture_id = _base_fixture_id(fixture_id)
    details = require_mapping(value, "output.details")

    if base_fixture_id == "CS-01":
        require_exact_fields(
            details,
            {
                "round_id",
                "round_contract_frozen",
                "policy_revision",
                "decision_rule",
                "voters",
                "required_voters",
                "excluded_voters",
            },
            path="output.details",
        )
        raw_exclusions = details["excluded_voters"]
        if not isinstance(raw_exclusions, list):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                "output.details.excluded_voters must be an array",
            )
        exclusions = [
            _validate_exclusion(
                exclusion,
                (
                    "output.details.excluded_voters"
                    f"[{index}]"
                ),
            )
            for index, exclusion in enumerate(raw_exclusions)
        ]
        return {
            "round_id": require_string(
                details["round_id"],
                "output.details.round_id",
            ),
            "round_contract_frozen": require_bool(
                details["round_contract_frozen"],
                "output.details.round_contract_frozen",
            ),
            "policy_revision": require_string(
                details["policy_revision"],
                "output.details.policy_revision",
            ),
            "decision_rule": require_string(
                details["decision_rule"],
                "output.details.decision_rule",
            ),
            "voters": _require_string_list(
                details["voters"],
                "output.details.voters",
            ),
            "required_voters": _require_string_list(
                details["required_voters"],
                "output.details.required_voters",
            ),
            "excluded_voters": exclusions,
        }

    if base_fixture_id in {"CS-02", "CS-03"}:
        require_exact_fields(
            details,
            {
                "round_id",
                "recorded_votes",
                "vote_record_count",
                "repeat_noop",
                "attempted_vote_applied",
                "error_code",
                "original_vote_immutable",
            },
            path="output.details",
        )
        error_code = _require_nullable_string(
            details["error_code"],
            "output.details.error_code",
        )
        if (
            error_code is not None
            and error_code != "VOTE_ALREADY_CAST"
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.details.error_code "
                    f"unsupported={error_code}"
                ),
            )
        return {
            "round_id": require_string(
                details["round_id"],
                "output.details.round_id",
            ),
            "recorded_votes": _validate_output_vote_list(
                details["recorded_votes"],
                "output.details.recorded_votes",
            ),
            "vote_record_count": require_nonnegative_int(
                details["vote_record_count"],
                "output.details.vote_record_count",
            ),
            "repeat_noop": require_bool(
                details["repeat_noop"],
                "output.details.repeat_noop",
            ),
            "attempted_vote_applied": require_bool(
                details["attempted_vote_applied"],
                "output.details.attempted_vote_applied",
            ),
            "error_code": error_code,
            "original_vote_immutable": require_bool(
                details["original_vote_immutable"],
                "output.details.original_vote_immutable",
            ),
        }

    if base_fixture_id == "CS-04":
        require_exact_fields(
            details,
            {
                "round_id",
                "status",
                "effective_outcome",
                "retained_votes",
                "missing_electorate",
                "decision_event_count",
            },
            path="output.details",
        )
        status = require_string(
            details["status"],
            "output.details.status",
        )
        if status not in {"ESCALATED", "FINALIZED"}:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                f"output.details.status unsupported={status}",
            )
        effective_outcome = require_string(
            details["effective_outcome"],
            "output.details.effective_outcome",
        )
        if effective_outcome not in {
            "TIMEOUT_UNRESOLVED",
            "TIMEOUT_FINALIZED",
        }:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.details.effective_outcome "
                    f"unsupported={effective_outcome}"
                ),
            )
        return {
            "round_id": require_string(
                details["round_id"],
                "output.details.round_id",
            ),
            "status": status,
            "effective_outcome": effective_outcome,
            "retained_votes": _validate_output_vote_list(
                details["retained_votes"],
                "output.details.retained_votes",
            ),
            "missing_electorate": _require_string_list(
                details["missing_electorate"],
                "output.details.missing_electorate",
            ),
            "decision_event_count": require_nonnegative_int(
                details["decision_event_count"],
                "output.details.decision_event_count",
            ),
        }

    if base_fixture_id == "CS-05":
        require_exact_fields(
            details,
            {
                "round_id",
                "status",
                "effective_outcome",
                "required_voters",
                "recorded_votes",
                "decision_event_count",
            },
            path="output.details",
        )
        return {
            "round_id": require_string(
                details["round_id"],
                "output.details.round_id",
            ),
            "status": require_string(
                details["status"],
                "output.details.status",
            ),
            "effective_outcome": require_string(
                details["effective_outcome"],
                "output.details.effective_outcome",
            ),
            "required_voters": _require_string_list(
                details["required_voters"],
                "output.details.required_voters",
            ),
            "recorded_votes": _validate_output_vote_list(
                details["recorded_votes"],
                "output.details.recorded_votes",
            ),
            "decision_event_count": require_nonnegative_int(
                details["decision_event_count"],
                "output.details.decision_event_count",
            ),
        }

    require_exact_fields(
        details,
        {
            "round_id",
            "derivation_formula",
            "derivation_rows",
        },
        path="output.details",
    )
    raw_rows = details["derivation_rows"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.details.derivation_rows must be non-empty",
        )
    rows = [
        _validate_derivation_row(
            row,
            f"output.details.derivation_rows[{index}]",
        )
        for index, row in enumerate(raw_rows)
    ]
    return {
        "round_id": require_string(
            details["round_id"],
            "output.details.round_id",
        ),
        "derivation_formula": require_string(
            details["derivation_formula"],
            "output.details.derivation_formula",
        ),
        "derivation_rows": rows,
    }


def validate_consensus_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable consensus decision evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "rule_tier",
            "decision",
            "details",
        },
        path="output",
    )

    return {
        "rule_tier": _require_enum(
            output["rule_tier"],
            _RULE_TIERS,
            "output.rule_tier",
            output=True,
        ),
        "decision": _require_enum(
            output["decision"],
            _DECISIONS,
            "output.decision",
            output=True,
        ),
        "details": _validate_output_details(
            fixture_id,
            output["details"],
        ),
    }


def _oracle_derivation_rows(
    cases: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for case in cases:
        dissent_voters = _dissent_voters(case["votes"])
        dissent_present = bool(dissent_voters)
        opinion = case["arbiter_opinion"]
        vote_value = (
            "REJECT"
            if dissent_present
            else "APPROVE"
        )

        if not dissent_present:
            effective_outcome = vote_value + "D"
            basis = "VOTE_NO_DISSENT"
        elif opinion is None:
            effective_outcome = "REJECTED"
            basis = "DISSENT_NO_ARBITER"
        else:
            effective_outcome = (
                "APPROVED"
                if opinion["opinion_value"] == "APPROVE"
                else "REJECTED"
            )
            basis = "ARBITER_OPINION"

        rows.append(
            {
                "case_id": case["case_id"],
                "dissent_present": dissent_present,
                "dissent_voters": dissent_voters,
                "vote_value": vote_value,
                "arbiter_opinion_recorded": (
                    opinion is not None
                ),
                "arbiter_opinion_value": (
                    None
                    if opinion is None
                    else opinion["opinion_value"]
                ),
                "effective_outcome": effective_outcome,
                "derivation_basis": basis,
            }
        )

    return rows


def _reference_derivation_rows(
    cases: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for case in cases:
        dissenters = [
            vote["voter_id"]
            for vote in case["votes"]
            if vote["vote_value"] == "DISAGREE"
        ]
        has_dissent = len(dissenters) > 0
        opinion = case["arbiter_opinion"]
        vote_value = "REJECT" if has_dissent else "APPROVE"

        if has_dissent and opinion is not None:
            effective = (
                "APPROVED"
                if opinion["opinion_value"] == "APPROVE"
                else "REJECTED"
            )
            basis = "ARBITER_OPINION"
        elif has_dissent:
            effective = "REJECTED"
            basis = "DISSENT_NO_ARBITER"
        else:
            effective = "APPROVED"
            basis = "VOTE_NO_DISSENT"

        rows.append(
            {
                "case_id": case["case_id"],
                "dissent_present": has_dissent,
                "dissent_voters": dissenters,
                "vote_value": vote_value,
                "arbiter_opinion_recorded": (
                    opinion is not None
                ),
                "arbiter_opinion_value": (
                    opinion["opinion_value"]
                    if opinion is not None
                    else None
                ),
                "effective_outcome": effective,
                "derivation_basis": basis,
            }
        )

    return rows


def _oracle_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "PROPOSAL_FREEZE":
        excluded = {
            exclusion["voter_id"]
            for exclusion in facts["exclusions"]
        }
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_CONTRACT_FROZEN",
            "details": {
                "round_id": facts["round_id"],
                "round_contract_frozen": True,
                "policy_revision": facts["policy_revision"],
                "decision_rule": facts["decision_rule"],
                "voters": list(facts["voters"]),
                "required_voters": [
                    voter
                    for voter in facts["voters"]
                    if voter not in excluded
                ],
                "excluded_voters": [
                    dict(exclusion)
                    for exclusion in facts["exclusions"]
                ],
            },
        }

    if case_kind == "IDENTICAL_REPEAT_VOTE":
        return {
            "rule_tier": "OBS",
            "decision": "VOTE_IDEMPOTENT_NOOP",
            "details": {
                "round_id": facts["round_id"],
                "recorded_votes": [
                    _copy_vote(facts["existing_vote"])
                ],
                "vote_record_count": 1,
                "repeat_noop": True,
                "attempted_vote_applied": False,
                "error_code": None,
                "original_vote_immutable": True,
            },
        }

    if case_kind == "CONFLICTING_REPEAT_VOTE":
        return {
            "rule_tier": "OBS",
            "decision": "VOTE_REJECTED",
            "details": {
                "round_id": facts["round_id"],
                "recorded_votes": [
                    _copy_vote(facts["existing_vote"])
                ],
                "vote_record_count": 1,
                "repeat_noop": False,
                "attempted_vote_applied": False,
                "error_code": "VOTE_ALREADY_CAST",
                "original_vote_immutable": True,
            },
        }

    if case_kind == "TIMEOUT_SWEEP":
        recorded_voters = {
            vote["voter_id"]
            for vote in facts["recorded_votes"]
        }
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_ESCALATED",
            "details": {
                "round_id": facts["round_id"],
                "status": "ESCALATED",
                "effective_outcome": "TIMEOUT_UNRESOLVED",
                "retained_votes": [
                    _copy_vote(vote)
                    for vote in facts["recorded_votes"]
                ],
                "missing_electorate": [
                    voter
                    for voter in facts["electorate"]
                    if voter not in recorded_voters
                ],
                "decision_event_count": 0,
            },
        }

    if case_kind == "UNANIMOUS_FINALIZATION":
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_FINALIZED",
            "details": {
                "round_id": facts["round_id"],
                "status": "FINALIZED",
                "effective_outcome": "UNANIMOUS",
                "required_voters": list(
                    facts["required_voters"]
                ),
                "recorded_votes": [
                    _copy_vote(vote)
                    for vote in facts["recorded_votes"]
                ],
                "decision_event_count": 1,
            },
        }

    return {
        "rule_tier": "CANDIDATE",
        "decision": "OUTCOMES_DERIVED",
        "details": {
            "round_id": facts["round_id"],
            "derivation_formula": (
                "NO_DISSENT=>VOTE;"
                "DISSENT_WITHOUT_ARBITER=>REJECTED;"
                "DISSENT_WITH_ARBITER=>ARBITER_OPINION"
            ),
            "derivation_rows": _oracle_derivation_rows(
                facts["derivation_cases"]
            ),
        },
    }


def _reference_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "PROPOSAL_FREEZE":
        excluded_ids = [
            item["voter_id"]
            for item in facts["exclusions"]
        ]
        required = [
            voter
            for voter in facts["voters"]
            if voter not in excluded_ids
        ]
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_CONTRACT_FROZEN",
            "details": {
                "round_id": facts["round_id"],
                "round_contract_frozen": True,
                "policy_revision": facts["policy_revision"],
                "decision_rule": facts["decision_rule"],
                "voters": list(facts["voters"]),
                "required_voters": required,
                "excluded_voters": [
                    {
                        "voter_id": item["voter_id"],
                        "reason": item["reason"],
                    }
                    for item in facts["exclusions"]
                ],
            },
        }

    if case_kind == "IDENTICAL_REPEAT_VOTE":
        existing = facts["existing_vote"]
        return {
            "rule_tier": "OBS",
            "decision": "VOTE_IDEMPOTENT_NOOP",
            "details": {
                "round_id": facts["round_id"],
                "recorded_votes": [_copy_vote(existing)],
                "vote_record_count": 1,
                "repeat_noop": True,
                "attempted_vote_applied": False,
                "error_code": None,
                "original_vote_immutable": True,
            },
        }

    if case_kind == "CONFLICTING_REPEAT_VOTE":
        original = facts["existing_vote"]
        return {
            "rule_tier": "OBS",
            "decision": "VOTE_REJECTED",
            "details": {
                "round_id": facts["round_id"],
                "recorded_votes": [_copy_vote(original)],
                "vote_record_count": 1,
                "repeat_noop": False,
                "attempted_vote_applied": False,
                "error_code": "VOTE_ALREADY_CAST",
                "original_vote_immutable": True,
            },
        }

    if case_kind == "TIMEOUT_SWEEP":
        retained = [
            _copy_vote(vote)
            for vote in facts["recorded_votes"]
        ]
        voted = [
            vote["voter_id"]
            for vote in retained
        ]
        missing = [
            voter
            for voter in facts["electorate"]
            if voter not in voted
        ]
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_ESCALATED",
            "details": {
                "round_id": facts["round_id"],
                "status": "ESCALATED",
                "effective_outcome": "TIMEOUT_UNRESOLVED",
                "retained_votes": retained,
                "missing_electorate": missing,
                "decision_event_count": 0,
            },
        }

    if case_kind == "UNANIMOUS_FINALIZATION":
        votes = [
            _copy_vote(vote)
            for vote in facts["recorded_votes"]
        ]
        return {
            "rule_tier": "OBS",
            "decision": "ROUND_FINALIZED",
            "details": {
                "round_id": facts["round_id"],
                "status": "FINALIZED",
                "effective_outcome": "UNANIMOUS",
                "required_voters": list(
                    facts["required_voters"]
                ),
                "recorded_votes": votes,
                "decision_event_count": 1,
            },
        }

    return {
        "rule_tier": "CANDIDATE",
        "decision": "OUTCOMES_DERIVED",
        "details": {
            "round_id": facts["round_id"],
            "derivation_formula": (
                "NO_DISSENT=>VOTE;"
                "DISSENT_WITHOUT_ARBITER=>REJECTED;"
                "DISSENT_WITH_ARBITER=>ARBITER_OPINION"
            ),
            "derivation_rows": _reference_derivation_rows(
                facts["derivation_cases"]
            ),
        },
    }


class ConsensusOracle:
    """Pure consensus oracle over injected round facts."""

    oracle_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self.oracle_id = _ORACLE_IDS[base_fixture_id]
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
                f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
            }
        )

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if _base_fixture_id(fixture_id) != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"oracle={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )
        return _oracle_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class ConsensusSubjectAdapter:
    """Pure structurally independent consensus adapter."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"consensus.{label}.reference"
        self.fixture_ids = frozenset({base_fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )
        return _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class FaultInjectedConsensusAdapter:
    """One genuine consensus defect per negative."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        faults = {
            "CS-01": "retain_excluded_required_voter",
            "CS-02": "double_record_identical_vote",
            "CS-03": "apply_conflicting_vote",
            "CS-04": "silently_finalize_timeout",
            "CS-05": "duplicate_decision_event",
            "CS-06": "ignore_arbiter_override",
        }
        self._fault = faults[base_fixture_id]
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"consensus.{label}.{self._fault}"
        )
        self.fixture_ids = frozenset({self._fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id != self._fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        output = _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )
        details = output["details"]

        if self._base == "CS-01":
            details["required_voters"] = list(
                raw_inputs["facts"]["voters"]
            )
            return output

        if self._base == "CS-02":
            details["recorded_votes"].append(
                _copy_vote(
                    raw_inputs["facts"]["attempted_vote"]
                )
            )
            details["vote_record_count"] = 2
            details["repeat_noop"] = False
            return output

        if self._base == "CS-03":
            details["recorded_votes"] = [
                _copy_vote(
                    raw_inputs["facts"]["attempted_vote"]
                )
            ]
            details["attempted_vote_applied"] = True
            details["error_code"] = None
            details["original_vote_immutable"] = False
            return output

        if self._base == "CS-04":
            output["decision"] = "ROUND_FINALIZED"
            details["status"] = "FINALIZED"
            details["effective_outcome"] = "TIMEOUT_FINALIZED"
            details["decision_event_count"] = 1
            return output

        if self._base == "CS-05":
            details["decision_event_count"] = 2
            return output

        rows = details["derivation_rows"]
        for row in rows:
            if row["case_id"] == "dissent-with-opinion":
                row["effective_outcome"] = "REJECTED"
        return output


def consensus_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all CS-01..06 consensus registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = ConsensusOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=ConsensusSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=validate_consensus_inputs,
                output_validator=validate_consensus_output,
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=(
                    f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
                ),
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=FaultInjectedConsensusAdapter(
                    base_fixture_id
                ),
                input_validator=validate_consensus_inputs,
                output_validator=validate_consensus_output,
            )
        )

    return tuple(registrations)
