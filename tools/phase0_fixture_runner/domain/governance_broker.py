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

_BASE_FIXTURES = (
    "GB-02",
    "GB-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "GB-02": (
        "governance_broker.gb02."
        "isolated_cas_staleness"
    ),
    "GB-06": (
        "governance_broker.gb06."
        "lock_contention_release"
    ),
}

_CASE_KINDS = frozenset(
    {
        "ISOLATED_CAS_CONTENTION",
        "LOCK_CONTENTION_RELEASE",
    }
)
_RULE_TIERS = frozenset(
    {
        "OBS",
        "CANDIDATE",
    }
)
_CAS_VERDICTS = frozenset(
    {
        "MATCH",
        "STALE",
    }
)
_CAS_DISPOSITIONS = frozenset(
    {
        "COMMITTED",
        "REJECTED_STALE_CAS",
    }
)
_CAS_DECISIONS = frozenset(
    {
        "CAS_STALE_REJECTED",
        "STALE_CAS_ACCEPTED",
    }
)
_LOCK_OPERATIONS = frozenset(
    {
        "LOCK",
        "UNLOCK",
    }
)
_LOCK_DISPOSITIONS = frozenset(
    {
        "ACQUIRED",
        "REJECTED_LOCK_HELD",
        "RELEASED",
        "TRANSFERRED",
        "REJECTED_NOT_OWNER",
    }
)
_LOCK_DECISIONS = frozenset(
    {
        "LOCK_CONTENTION_REJECTED_AND_RELEASED",
        "LOCK_SILENTLY_TRANSFERRED",
    }
)

_GB02_SCOPE_BOUNDARY = (
    "INJECTED_CAS_STALENESS_ONLY__"
    "NORMALIZE_INSIDE_DRAIN_ORDERING_UNPROVEN"
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


def _require_list(
    value: Any,
    path: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    return value


def _require_string_list(
    value: Any,
    path: str,
) -> list[str]:
    raw_values = _require_list(value, path)
    values = [
        require_string(item, f"{path}[{index}]")
        for index, item in enumerate(raw_values)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate values",
        )
    return values


def _validate_cas_request(
    value: Any,
    path: str,
) -> dict[str, Any]:
    request = require_mapping(value, path)
    require_exact_fields(
        request,
        {
            "request_id",
            "payload_value",
            "expected_revision",
            "target_revision",
            "cas_verdict",
        },
        path=path,
    )

    expected_revision = require_nonnegative_int(
        request["expected_revision"],
        f"{path}.expected_revision",
    )
    target_revision = require_nonnegative_int(
        request["target_revision"],
        f"{path}.target_revision",
    )
    if target_revision <= expected_revision:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.target_revision must exceed "
                "expected_revision"
            ),
        )

    return {
        "request_id": require_string(
            request["request_id"],
            f"{path}.request_id",
        ),
        "payload_value": require_string(
            request["payload_value"],
            f"{path}.payload_value",
        ),
        "expected_revision": expected_revision,
        "target_revision": target_revision,
        "cas_verdict": _require_enum(
            request["cas_verdict"],
            _CAS_VERDICTS,
            f"{path}.cas_verdict",
        ),
    }


def _validate_lock_action(
    value: Any,
    path: str,
) -> dict[str, Any]:
    action = require_mapping(value, path)
    require_exact_fields(
        action,
        {
            "action_id",
            "operation",
            "peer_id",
        },
        path=path,
    )
    return {
        "action_id": require_string(
            action["action_id"],
            f"{path}.action_id",
        ),
        "operation": _require_enum(
            action["operation"],
            _LOCK_OPERATIONS,
            f"{path}.operation",
        ),
        "peer_id": require_string(
            action["peer_id"],
            f"{path}.peer_id",
        ),
    }


def _validate_fixture_vector(
    fixture_id: str,
    case_kind: str,
    facts: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)

    if base_fixture_id == "GB-02":
        expected_facts = {
            "initial_revision": 17,
            "initial_value": "normalized-base",
            "requests": [
                {
                    "request_id": "gb02-commit",
                    "payload_value": "gb02-commit",
                    "expected_revision": 17,
                    "target_revision": 18,
                    "cas_verdict": "MATCH",
                },
                {
                    "request_id": "gb02-stale",
                    "payload_value": "gb02-stale",
                    "expected_revision": 17,
                    "target_revision": 18,
                    "cas_verdict": "STALE",
                },
            ],
        }
        if (
            case_kind != "ISOLATED_CAS_CONTENTION"
            or facts != expected_facts
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "GB-02 requires one matching request followed "
                    "by one injected stale same-revision request"
                ),
            )
        return

    expected_facts = {
        "resource_id": "governance-state",
        "actions": [
            {
                "action_id": "lock-ag",
                "operation": "LOCK",
                "peer_id": "ag",
            },
            {
                "action_id": "lock-cx",
                "operation": "LOCK",
                "peer_id": "cx",
            },
            {
                "action_id": "unlock-ag",
                "operation": "UNLOCK",
                "peer_id": "ag",
            },
        ],
    }
    if (
        case_kind != "LOCK_CONTENTION_RELEASE"
        or facts != expected_facts
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "GB-06 requires ag lock, cx contention, then "
                "ag unlock"
            ),
        )


def validate_governance_broker_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed GB-02/GB-06 evidence vector."""

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
    raw_facts = require_mapping(
        inputs["facts"],
        "inputs.facts",
    )

    if case_kind == "ISOLATED_CAS_CONTENTION":
        require_exact_fields(
            raw_facts,
            {
                "initial_revision",
                "initial_value",
                "requests",
            },
            path="inputs.facts",
        )

        raw_requests = _require_list(
            raw_facts["requests"],
            "inputs.facts.requests",
        )
        if len(raw_requests) != 2:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "inputs.facts.requests must contain two requests",
            )

        requests = [
            _validate_cas_request(
                request,
                f"inputs.facts.requests[{index}]",
            )
            for index, request in enumerate(raw_requests)
        ]
        request_ids = [
            request["request_id"]
            for request in requests
        ]
        if len(request_ids) != len(set(request_ids)):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "inputs.facts.requests contains duplicate request IDs",
            )

        facts: dict[str, Any] = {
            "initial_revision": require_nonnegative_int(
                raw_facts["initial_revision"],
                "inputs.facts.initial_revision",
            ),
            "initial_value": require_string(
                raw_facts["initial_value"],
                "inputs.facts.initial_value",
            ),
            "requests": requests,
        }
    else:
        require_exact_fields(
            raw_facts,
            {
                "resource_id",
                "actions",
            },
            path="inputs.facts",
        )

        raw_actions = _require_list(
            raw_facts["actions"],
            "inputs.facts.actions",
        )
        if len(raw_actions) != 3:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "inputs.facts.actions must contain three actions",
            )

        actions = [
            _validate_lock_action(
                action,
                f"inputs.facts.actions[{index}]",
            )
            for index, action in enumerate(raw_actions)
        ]
        action_ids = [
            action["action_id"]
            for action in actions
        ]
        if len(action_ids) != len(set(action_ids)):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "inputs.facts.actions contains duplicate action IDs",
            )

        facts = {
            "resource_id": require_string(
                raw_facts["resource_id"],
                "inputs.facts.resource_id",
            ),
            "actions": actions,
        }

    _validate_fixture_vector(
        fixture_id,
        case_kind,
        facts,
    )
    return {
        "case_kind": case_kind,
        "facts": facts,
    }


def _validate_cas_record(
    value: Any,
    path: str,
) -> dict[str, Any]:
    record = require_mapping(value, path)
    require_exact_fields(
        record,
        {
            "request_id",
            "payload_value",
            "expected_revision",
            "target_revision",
            "cas_verdict",
            "disposition",
            "mutation_applied",
            "error_type",
            "archive_location",
        },
        path=path,
    )
    return {
        "request_id": require_string(
            record["request_id"],
            f"{path}.request_id",
        ),
        "payload_value": require_string(
            record["payload_value"],
            f"{path}.payload_value",
        ),
        "expected_revision": require_nonnegative_int(
            record["expected_revision"],
            f"{path}.expected_revision",
        ),
        "target_revision": require_nonnegative_int(
            record["target_revision"],
            f"{path}.target_revision",
        ),
        "cas_verdict": _require_enum(
            record["cas_verdict"],
            _CAS_VERDICTS,
            f"{path}.cas_verdict",
            output=True,
        ),
        "disposition": _require_enum(
            record["disposition"],
            _CAS_DISPOSITIONS,
            f"{path}.disposition",
            output=True,
        ),
        "mutation_applied": require_bool(
            record["mutation_applied"],
            f"{path}.mutation_applied",
        ),
        "error_type": _require_nullable_string(
            record["error_type"],
            f"{path}.error_type",
        ),
        "archive_location": _require_nullable_string(
            record["archive_location"],
            f"{path}.archive_location",
        ),
    }


def _validate_drain_summary(
    value: Any,
    path: str,
) -> dict[str, int]:
    summary = require_mapping(value, path)
    require_exact_fields(
        summary,
        {
            "processed",
            "committed",
            "failed",
        },
        path=path,
    )
    processed = require_nonnegative_int(
        summary["processed"],
        f"{path}.processed",
    )
    committed = require_nonnegative_int(
        summary["committed"],
        f"{path}.committed",
    )
    failed = require_nonnegative_int(
        summary["failed"],
        f"{path}.failed",
    )
    if committed + failed != processed:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.committed plus failed must equal "
                "processed"
            ),
        )
    return {
        "processed": processed,
        "committed": committed,
        "failed": failed,
    }


def _validate_gb02_details(
    value: Any,
) -> dict[str, Any]:
    details = require_mapping(
        value,
        "output.details",
    )
    require_exact_fields(
        details,
        {
            "scope_boundary",
            "requests",
            "drain_summary",
            "final_revision",
            "final_value",
            "stale_request_mutation_applied",
        },
        path="output.details",
    )

    scope_boundary = require_string(
        details["scope_boundary"],
        "output.details.scope_boundary",
    )
    if scope_boundary != _GB02_SCOPE_BOUNDARY:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.details.scope_boundary must preserve "
                "the GB-02 proof boundary"
            ),
        )

    raw_requests = _require_list(
        details["requests"],
        "output.details.requests",
    )
    if len(raw_requests) != 2:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.details.requests must contain two records",
        )

    return {
        "scope_boundary": scope_boundary,
        "requests": [
            _validate_cas_record(
                record,
                f"output.details.requests[{index}]",
            )
            for index, record in enumerate(raw_requests)
        ],
        "drain_summary": _validate_drain_summary(
            details["drain_summary"],
            "output.details.drain_summary",
        ),
        "final_revision": require_nonnegative_int(
            details["final_revision"],
            "output.details.final_revision",
        ),
        "final_value": require_string(
            details["final_value"],
            "output.details.final_value",
        ),
        "stale_request_mutation_applied": require_bool(
            details["stale_request_mutation_applied"],
            (
                "output.details."
                "stale_request_mutation_applied"
            ),
        ),
    }


def _validate_lock_record(
    value: Any,
    path: str,
) -> dict[str, Any]:
    record = require_mapping(value, path)
    require_exact_fields(
        record,
        {
            "action_id",
            "operation",
            "peer_id",
            "exit_code",
            "disposition",
            "mutation_applied",
            "authoritative_owner_after",
        },
        path=path,
    )
    return {
        "action_id": require_string(
            record["action_id"],
            f"{path}.action_id",
        ),
        "operation": _require_enum(
            record["operation"],
            _LOCK_OPERATIONS,
            f"{path}.operation",
            output=True,
        ),
        "peer_id": require_string(
            record["peer_id"],
            f"{path}.peer_id",
        ),
        "exit_code": require_nonnegative_int(
            record["exit_code"],
            f"{path}.exit_code",
        ),
        "disposition": _require_enum(
            record["disposition"],
            _LOCK_DISPOSITIONS,
            f"{path}.disposition",
            output=True,
        ),
        "mutation_applied": require_bool(
            record["mutation_applied"],
            f"{path}.mutation_applied",
        ),
        "authoritative_owner_after": (
            _require_nullable_string(
                record["authoritative_owner_after"],
                f"{path}.authoritative_owner_after",
            )
        ),
    }


def _validate_gb06_details(
    value: Any,
) -> dict[str, Any]:
    details = require_mapping(
        value,
        "output.details",
    )
    require_exact_fields(
        details,
        {
            "resource_id",
            "action_records",
            "contending_owner",
            "rejected_because",
            "ownership_sequence",
            "terminal_lock_present",
            "terminal_owner",
        },
        path="output.details",
    )

    raw_records = _require_list(
        details["action_records"],
        "output.details.action_records",
    )
    if len(raw_records) != 3:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.details.action_records must contain "
                "three records"
            ),
        )

    terminal_lock_present = require_bool(
        details["terminal_lock_present"],
        "output.details.terminal_lock_present",
    )
    terminal_owner = _require_nullable_string(
        details["terminal_owner"],
        "output.details.terminal_owner",
    )
    if terminal_lock_present != (terminal_owner is not None):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.details terminal lock presence and "
                "owner disagree"
            ),
        )

    return {
        "resource_id": require_string(
            details["resource_id"],
            "output.details.resource_id",
        ),
        "action_records": [
            _validate_lock_record(
                record,
                f"output.details.action_records[{index}]",
            )
            for index, record in enumerate(raw_records)
        ],
        "contending_owner": require_string(
            details["contending_owner"],
            "output.details.contending_owner",
        ),
        "rejected_because": require_string(
            details["rejected_because"],
            "output.details.rejected_because",
        ),
        "ownership_sequence": _require_string_list(
            details["ownership_sequence"],
            "output.details.ownership_sequence",
        ),
        "terminal_lock_present": terminal_lock_present,
        "terminal_owner": terminal_owner,
    }


def validate_governance_broker_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one governance-broker output."""

    base_fixture_id = _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "rule_tier",
            "decision",
            "details",
        },
        path="output",
    )

    if base_fixture_id == "GB-02":
        expected_tier = "CANDIDATE"
        decisions = _CAS_DECISIONS
    else:
        expected_tier = "OBS"
        decisions = _LOCK_DECISIONS

    rule_tier = _require_enum(
        output["rule_tier"],
        _RULE_TIERS,
        "output.rule_tier",
        output=True,
    )
    if rule_tier != expected_tier:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"fixture_id={fixture_id};"
                f"rule_tier={rule_tier}"
            ),
        )

    decision = _require_enum(
        output["decision"],
        decisions,
        "output.decision",
        output=True,
    )

    return {
        "rule_tier": rule_tier,
        "decision": decision,
        "details": (
            _validate_gb02_details(output["details"])
            if base_fixture_id == "GB-02"
            else _validate_gb06_details(output["details"])
        ),
    }


def _oracle_gb02(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    first_request = facts["requests"][0]
    second_request = facts["requests"][1]

    return {
        "rule_tier": "CANDIDATE",
        "decision": "CAS_STALE_REJECTED",
        "details": {
            "scope_boundary": _GB02_SCOPE_BOUNDARY,
            "requests": [
                {
                    "request_id": first_request["request_id"],
                    "payload_value": first_request[
                        "payload_value"
                    ],
                    "expected_revision": first_request[
                        "expected_revision"
                    ],
                    "target_revision": first_request[
                        "target_revision"
                    ],
                    "cas_verdict": first_request[
                        "cas_verdict"
                    ],
                    "disposition": "COMMITTED",
                    "mutation_applied": True,
                    "error_type": None,
                    "archive_location": None,
                },
                {
                    "request_id": second_request["request_id"],
                    "payload_value": second_request[
                        "payload_value"
                    ],
                    "expected_revision": second_request[
                        "expected_revision"
                    ],
                    "target_revision": second_request[
                        "target_revision"
                    ],
                    "cas_verdict": second_request[
                        "cas_verdict"
                    ],
                    "disposition": "REJECTED_STALE_CAS",
                    "mutation_applied": False,
                    "error_type": "RuntimeError",
                    "archive_location": "broker/error",
                },
            ],
            "drain_summary": {
                "processed": 2,
                "committed": 1,
                "failed": 1,
            },
            "final_revision": first_request[
                "target_revision"
            ],
            "final_value": first_request["payload_value"],
            "stale_request_mutation_applied": False,
        },
    }


def _reference_gb02(
    facts: Mapping[str, Any],
    *,
    accept_stale: bool,
) -> dict[str, Any]:
    current_revision = facts["initial_revision"]
    current_value = facts["initial_value"]
    processed = 0
    committed = 0
    failed = 0
    request_records: list[dict[str, Any]] = []

    for request in facts["requests"]:
        processed += 1
        stale = request["cas_verdict"] == "STALE"
        mutation_applied = not stale or accept_stale

        if mutation_applied:
            current_revision = request["target_revision"]
            current_value = request["payload_value"]
            committed += 1
            disposition = "COMMITTED"
            error_type = None
            archive_location = None
        else:
            failed += 1
            disposition = "REJECTED_STALE_CAS"
            error_type = "RuntimeError"
            archive_location = "broker/error"

        request_records.append(
            {
                "request_id": request["request_id"],
                "payload_value": request["payload_value"],
                "expected_revision": request[
                    "expected_revision"
                ],
                "target_revision": request[
                    "target_revision"
                ],
                "cas_verdict": request["cas_verdict"],
                "disposition": disposition,
                "mutation_applied": mutation_applied,
                "error_type": error_type,
                "archive_location": archive_location,
            }
        )

    stale_request_mutation_applied = any(
        record["cas_verdict"] == "STALE"
        and record["mutation_applied"]
        for record in request_records
    )

    return {
        "rule_tier": "CANDIDATE",
        "decision": (
            "STALE_CAS_ACCEPTED"
            if stale_request_mutation_applied
            else "CAS_STALE_REJECTED"
        ),
        "details": {
            "scope_boundary": _GB02_SCOPE_BOUNDARY,
            "requests": request_records,
            "drain_summary": {
                "processed": processed,
                "committed": committed,
                "failed": failed,
            },
            "final_revision": current_revision,
            "final_value": current_value,
            "stale_request_mutation_applied": (
                stale_request_mutation_applied
            ),
        },
    }


def _oracle_gb06(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    acquire = facts["actions"][0]
    contend = facts["actions"][1]
    release = facts["actions"][2]

    return {
        "rule_tier": "OBS",
        "decision": (
            "LOCK_CONTENTION_REJECTED_AND_RELEASED"
        ),
        "details": {
            "resource_id": facts["resource_id"],
            "action_records": [
                {
                    "action_id": acquire["action_id"],
                    "operation": acquire["operation"],
                    "peer_id": acquire["peer_id"],
                    "exit_code": 0,
                    "disposition": "ACQUIRED",
                    "mutation_applied": True,
                    "authoritative_owner_after": "ag",
                },
                {
                    "action_id": contend["action_id"],
                    "operation": contend["operation"],
                    "peer_id": contend["peer_id"],
                    "exit_code": 1,
                    "disposition": "REJECTED_LOCK_HELD",
                    "mutation_applied": False,
                    "authoritative_owner_after": "ag",
                },
                {
                    "action_id": release["action_id"],
                    "operation": release["operation"],
                    "peer_id": release["peer_id"],
                    "exit_code": 0,
                    "disposition": "RELEASED",
                    "mutation_applied": True,
                    "authoritative_owner_after": None,
                },
            ],
            "contending_owner": "cx",
            "rejected_because": "locked by ag",
            "ownership_sequence": ["ag"],
            "terminal_lock_present": False,
            "terminal_owner": None,
        },
    }


def _reference_gb06(
    facts: Mapping[str, Any],
    *,
    transfer_on_contention: bool,
) -> dict[str, Any]:
    owner: str | None = None
    ownership_sequence: list[str] = []
    action_records: list[dict[str, Any]] = []
    contending_owner: str | None = None
    rejected_because: str | None = None
    transferred = False

    for action in facts["actions"]:
        operation = action["operation"]
        peer_id = action["peer_id"]

        if operation == "LOCK" and owner is None:
            owner = peer_id
            ownership_sequence.append(peer_id)
            exit_code = 0
            disposition = "ACQUIRED"
            mutation_applied = True
        elif operation == "LOCK":
            contending_owner = peer_id
            rejected_because = f"locked by {owner}"

            if transfer_on_contention:
                owner = peer_id
                ownership_sequence.append(peer_id)
                exit_code = 0
                disposition = "TRANSFERRED"
                mutation_applied = True
                transferred = True
            else:
                exit_code = 1
                disposition = "REJECTED_LOCK_HELD"
                mutation_applied = False
        elif owner == peer_id:
            owner = None
            exit_code = 0
            disposition = "RELEASED"
            mutation_applied = True
        else:
            exit_code = 1
            disposition = "REJECTED_NOT_OWNER"
            mutation_applied = False

        action_records.append(
            {
                "action_id": action["action_id"],
                "operation": operation,
                "peer_id": peer_id,
                "exit_code": exit_code,
                "disposition": disposition,
                "mutation_applied": mutation_applied,
                "authoritative_owner_after": owner,
            }
        )

    if contending_owner is None or rejected_because is None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "GB-06 vector did not contain lock contention",
        )

    return {
        "rule_tier": "OBS",
        "decision": (
            "LOCK_SILENTLY_TRANSFERRED"
            if transferred
            else "LOCK_CONTENTION_REJECTED_AND_RELEASED"
        ),
        "details": {
            "resource_id": facts["resource_id"],
            "action_records": action_records,
            "contending_owner": contending_owner,
            "rejected_because": rejected_because,
            "ownership_sequence": ownership_sequence,
            "terminal_lock_present": owner is not None,
            "terminal_owner": owner,
        },
    }


class GovernanceBrokerOracle:
    """Pure expected oracle for GB-02 and GB-06."""

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

        facts = raw_inputs["facts"]
        if self._base == "GB-02":
            return _oracle_gb02(facts)
        return _oracle_gb06(facts)


class GovernanceBrokerSubjectAdapter:
    """Pure reference adapter over injected broker facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"governance_broker.{label}.reference"
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
        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        facts = raw_inputs["facts"]
        if self._base == "GB-02":
            return _reference_gb02(
                facts,
                accept_stale=False,
            )
        return _reference_gb06(
            facts,
            transfer_on_contention=False,
        )


class FaultInjectedGovernanceBrokerAdapter:
    """One genuine fixture-specific governance-broker defect."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"governance_broker.{label}.fault"
        )
        self.fixture_ids = frozenset(
            {self._fixture_id}
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != self._fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        facts = raw_inputs["facts"]
        if self._base == "GB-02":
            return _reference_gb02(
                facts,
                accept_stale=True,
            )
        return _reference_gb06(
            facts,
            transfer_on_contention=True,
        )


def governance_broker_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all GB-02/GB-06 governance-broker registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = GovernanceBrokerOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=GovernanceBrokerSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_governance_broker_inputs
                ),
                output_validator=(
                    validate_governance_broker_output
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
                adapter=(
                    FaultInjectedGovernanceBrokerAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_governance_broker_inputs
                ),
                output_validator=(
                    validate_governance_broker_output
                ),
            )
        )

    return tuple(registrations)
