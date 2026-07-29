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
    "RT-01",
    "RT-02",
    "RT-03",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "RT-01": "routing_discovery.rt01.structured_audit",
    "RT-02": "routing_discovery.rt02.reasoned_exclusion",
    "RT-03": "routing_discovery.rt03.absent_usage_evidence",
}

_CASE_KINDS = frozenset(
    {
        "ELIGIBLE_SELECTION",
        "CAPABILITY_MISMATCH",
        "USAGE_UNDECLARED",
    }
)
_RULE_TIERS = frozenset({"OBS", "CANDIDATE"})
_DECISIONS = frozenset(
    {
        "CANDIDATE_SELECTED",
        "CANDIDATE_EXCLUDED",
        "USAGE_ABSENT",
    }
)
_STATUSES = frozenset({"GREEN"})
_USAGE_DISPOSITIONS = frozenset({"ABSENT", "DECLARED"})


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


def _require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    values = [
        require_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate values",
        )
    return values


def _validate_candidate(
    value: Any,
    path: str,
) -> dict[str, Any]:
    candidate = require_mapping(value, path)
    require_exact_fields(
        candidate,
        {
            "candidate_id",
            "score",
            "status",
            "tier",
            "capability_match",
        },
        path=path,
    )
    status = _require_enum(
        candidate["status"],
        _STATUSES,
        f"{path}.status",
    )
    return {
        "candidate_id": require_string(
            candidate["candidate_id"],
            f"{path}.candidate_id",
        ),
        "score": require_nonnegative_int(
            candidate["score"],
            f"{path}.score",
        ),
        "status": status,
        "tier": require_string(
            candidate["tier"],
            f"{path}.tier",
        ),
        "capability_match": require_bool(
            candidate["capability_match"],
            f"{path}.capability_match",
        ),
    }


def _validate_fixture_vector(
    fixture_id: str,
    case_kind: str,
    facts: Mapping[str, Any],
) -> None:
    base = _base_fixture_id(fixture_id)

    if base == "RT-01":
        if (
            case_kind != "ELIGIBLE_SELECTION"
            or facts["candidate"]["candidate_id"] != "cx"
            or facts["candidate"]["score"] != 12
            or facts["candidate"]["status"] != "GREEN"
            or facts["candidate"]["tier"] != "mid"
            or not facts["candidate"]["capability_match"]
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "RT-01 requires the observed eligible cx "
                    "candidate facts"
                ),
            )
        return

    if base == "RT-02":
        if (
            case_kind != "CAPABILITY_MISMATCH"
            or facts["candidate"]["capability_match"]
            or facts["required_capability"]
            in facts["peer_capabilities"]
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "RT-02 requires a candidate missing the "
                    "required capability"
                ),
            )
        return

    if (
        case_kind != "USAGE_UNDECLARED"
        or facts["usage_evidence_state"] != "UNDECLARED"
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "RT-03 requires explicitly undeclared usage evidence",
        )


def validate_routing_discovery_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed RT-01..03 routing-discovery vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {"case_kind", "facts"},
        path="inputs",
    )
    case_kind = _require_enum(
        inputs["case_kind"],
        _CASE_KINDS,
        "inputs.case_kind",
    )
    raw_facts = require_mapping(inputs["facts"], "inputs.facts")

    if case_kind == "ELIGIBLE_SELECTION":
        require_exact_fields(
            raw_facts,
            {"candidate"},
            path="inputs.facts",
        )
        facts = {
            "candidate": _validate_candidate(
                raw_facts["candidate"],
                "inputs.facts.candidate",
            ),
        }
    elif case_kind == "CAPABILITY_MISMATCH":
        require_exact_fields(
            raw_facts,
            {
                "candidate",
                "required_capability",
                "peer_capabilities",
            },
            path="inputs.facts",
        )
        facts = {
            "candidate": _validate_candidate(
                raw_facts["candidate"],
                "inputs.facts.candidate",
            ),
            "required_capability": require_string(
                raw_facts["required_capability"],
                "inputs.facts.required_capability",
            ),
            "peer_capabilities": _require_string_list(
                raw_facts["peer_capabilities"],
                "inputs.facts.peer_capabilities",
            ),
        }
    else:
        require_exact_fields(
            raw_facts,
            {"peer_id", "usage_evidence_state"},
            path="inputs.facts",
        )
        facts = {
            "peer_id": require_string(
                raw_facts["peer_id"],
                "inputs.facts.peer_id",
            ),
            "usage_evidence_state": _require_enum(
                raw_facts["usage_evidence_state"],
                frozenset({"UNDECLARED"}),
                "inputs.facts.usage_evidence_state",
            ),
        }

    _validate_fixture_vector(fixture_id, case_kind, facts)
    return {"case_kind": case_kind, "facts": facts}


def _validate_audit_record(
    value: Any,
    path: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    record = require_mapping(value, path)
    require_exact_fields(
        record,
        {
            "candidate_id",
            "score",
            "status",
            "tier",
            "capability_match",
        },
        path=path,
    )
    return {
        "candidate_id": require_string(
            record["candidate_id"],
            f"{path}.candidate_id",
        ),
        "score": require_nonnegative_int(
            record["score"],
            f"{path}.score",
        ),
        "status": _require_enum(
            record["status"],
            _STATUSES,
            f"{path}.status",
            output=True,
        ),
        "tier": require_string(
            record["tier"],
            f"{path}.tier",
        ),
        "capability_match": require_bool(
            record["capability_match"],
            f"{path}.capability_match",
        ),
    }


def _validate_details(
    decision: str,
    value: Any,
) -> dict[str, Any]:
    details = require_mapping(value, "output.details")

    if decision == "CANDIDATE_SELECTED":
        require_exact_fields(
            details,
            {"candidate_id", "audit_record"},
            path="output.details",
        )
        return {
            "candidate_id": require_string(
                details["candidate_id"],
                "output.details.candidate_id",
            ),
            "audit_record": _validate_audit_record(
                details["audit_record"],
                "output.details.audit_record",
            ),
        }

    if decision == "CANDIDATE_EXCLUDED":
        require_exact_fields(
            details,
            {
                "candidate_id",
                "capability_match",
                "exclusion_reason",
            },
            path="output.details",
        )
        reason = details["exclusion_reason"]
        if reason is not None:
            reason = require_string(
                reason,
                "output.details.exclusion_reason",
            )
        return {
            "candidate_id": require_string(
                details["candidate_id"],
                "output.details.candidate_id",
            ),
            "capability_match": require_bool(
                details["capability_match"],
                "output.details.capability_match",
            ),
            "exclusion_reason": reason,
        }

    require_exact_fields(
        details,
        {"peer_id", "usage_disposition", "usage_value"},
        path="output.details",
    )
    usage_value = details["usage_value"]
    if usage_value is not None:
        usage_value = require_nonnegative_int(
            usage_value,
            "output.details.usage_value",
        )
    return {
        "peer_id": require_string(
            details["peer_id"],
            "output.details.peer_id",
        ),
        "usage_disposition": _require_enum(
            details["usage_disposition"],
            _USAGE_DISPOSITIONS,
            "output.details.usage_disposition",
            output=True,
        ),
        "usage_value": usage_value,
    }


def validate_routing_discovery_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one routing-discovery output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {"rule_tier", "decision", "details"},
        path="output",
    )
    decision = _require_enum(
        output["decision"],
        _DECISIONS,
        "output.decision",
        output=True,
    )
    return {
        "rule_tier": _require_enum(
            output["rule_tier"],
            _RULE_TIERS,
            "output.rule_tier",
            output=True,
        ),
        "decision": decision,
        "details": _validate_details(decision, output["details"]),
    }


def _oracle_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "ELIGIBLE_SELECTION":
        candidate = facts["candidate"]
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CANDIDATE_SELECTED",
            "details": {
                "candidate_id": candidate["candidate_id"],
                "audit_record": {
                    "candidate_id": candidate["candidate_id"],
                    "score": candidate["score"],
                    "status": candidate["status"],
                    "tier": candidate["tier"],
                    "capability_match": candidate["capability_match"],
                },
            },
        }

    if case_kind == "CAPABILITY_MISMATCH":
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CANDIDATE_EXCLUDED",
            "details": {
                "candidate_id": facts["candidate"]["candidate_id"],
                "capability_match": False,
                "exclusion_reason": "CAPABILITY_UNSUPPORTED",
            },
        }

    return {
        "rule_tier": "OBS",
        "decision": "USAGE_ABSENT",
        "details": {
            "peer_id": facts["peer_id"],
            "usage_disposition": "ABSENT",
            "usage_value": None,
        },
    }


def _reference_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "ELIGIBLE_SELECTION":
        candidate = facts["candidate"]
        audit = {
            field: candidate[field]
            for field in (
                "candidate_id",
                "score",
                "status",
                "tier",
                "capability_match",
            )
        }
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CANDIDATE_SELECTED",
            "details": {
                "candidate_id": audit["candidate_id"],
                "audit_record": audit,
            },
        }

    if case_kind == "CAPABILITY_MISMATCH":
        supported = (
            facts["required_capability"]
            in facts["peer_capabilities"]
        )
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CANDIDATE_EXCLUDED",
            "details": {
                "candidate_id": facts["candidate"]["candidate_id"],
                "capability_match": supported,
                "exclusion_reason": (
                    None
                    if supported
                    else "CAPABILITY_UNSUPPORTED"
                ),
            },
        }

    return {
        "rule_tier": "OBS",
        "decision": "USAGE_ABSENT",
        "details": {
            "peer_id": facts["peer_id"],
            "usage_disposition": "ABSENT",
            "usage_value": None,
        },
    }


class RoutingDiscoveryOracle:
    """Pure expected routing-discovery oracle."""

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
                f"oracle={self.oracle_id};fixture_id={fixture_id}",
            )
        return _oracle_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class RoutingDiscoverySubjectAdapter:
    """Pure reference adapter over injected routing facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"routing_discovery.{label}.reference"
        )
        self.fixture_ids = frozenset({base_fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )
        return _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class FaultInjectedRoutingDiscoveryAdapter:
    """One fixture-specific routing-discovery defect."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self._fixture_id = f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"routing_discovery.{label}.fault"
        self.fixture_ids = frozenset({self._fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != self._fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        output = _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )
        details = output["details"]

        if self._base == "RT-01":
            details["audit_record"] = None
        elif self._base == "RT-02":
            details["exclusion_reason"] = None
        else:
            details["usage_disposition"] = "DECLARED"
            details["usage_value"] = 0

        return output


def routing_discovery_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all RT-01..03 routing-discovery registrations."""

    registrations: list[DomainRegistration] = []
    for base_fixture_id in _BASE_FIXTURES:
        oracle = RoutingDiscoveryOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=RoutingDiscoverySubjectAdapter(
                    base_fixture_id
                ),
                input_validator=validate_routing_discovery_inputs,
                output_validator=validate_routing_discovery_output,
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=FaultInjectedRoutingDiscoveryAdapter(
                    base_fixture_id
                ),
                input_validator=validate_routing_discovery_inputs,
                output_validator=validate_routing_discovery_output,
            )
        )
    return tuple(registrations)
