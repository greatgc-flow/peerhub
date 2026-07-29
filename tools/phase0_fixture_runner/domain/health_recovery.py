"""CANDIDATE-tier readiness and measured-failure rules for HR-01..03.

The module operates only on injected readiness evidence and controlled-fake
stage facts. The input field ``stage_status`` represents a stage observation;
the globally outcome-shaped key ``outcome`` is reserved for derived output.
No real process, filesystem, network, provider, or operating-system access
occurs here.
"""

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
    "HR-01",
    "HR-02",
    "HR-03",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "HR-01": (
        "health_recovery.hr01."
        "fresh_readiness_projection"
    ),
    "HR-02": (
        "health_recovery.hr02."
        "expired_revalidation_required"
    ),
    "HR-03": (
        "health_recovery.hr03."
        "measured_failure_matrix"
    ),
}

_CANONICAL_STAGES = (
    "resolve_executable",
    "validate_environment",
    "authenticate",
    "connect_network",
    "call_provider",
    "check_usage_admission",
)
_STAGE_SET = frozenset(_CANONICAL_STAGES)
_STAGE_CLASSIFICATIONS = {
    "resolve_executable": "EXECUTABLE_UNAVAILABLE",
    "validate_environment": "ENVIRONMENT_UNAVAILABLE",
    "authenticate": "AUTH_UNAVAILABLE",
    "connect_network": "NETWORK_UNAVAILABLE",
    "call_provider": "PROVIDER_UNAVAILABLE",
}
_USAGE_FAILURE_REASONS = frozenset(
    {
        "QUOTA_EXHAUSTED",
        "RATE_LIMITED",
    }
)
_CLASSIFICATIONS = frozenset(
    {
        "EXECUTABLE_UNAVAILABLE",
        "ENVIRONMENT_UNAVAILABLE",
        "AUTH_UNAVAILABLE",
        "NETWORK_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
        "QUOTA_EXHAUSTED",
        "RATE_LIMITED",
    }
)
_STAGE_STATUSES = frozenset(
    {
        "OK",
        "FAILED",
    }
)
_NONLEGACY_SCENARIO_IDS = (
    "executable-unavailable",
    "environment-unavailable",
    "auth-unavailable",
    "network-unavailable",
    "provider-unavailable",
    "quota-exhausted",
    "rate-limited",
)
_LEGACY_SCENARIO_ID = (
    "legacy-operational-timeout"
)
_ALL_SCENARIO_IDS = (
    *_NONLEGACY_SCENARIO_IDS,
    _LEGACY_SCENARIO_ID,
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


def _require_literal(
    value: Any,
    expected: str,
    path: str,
) -> str:
    actual = require_string(value, path)
    if actual != expected:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path} expected={expected};"
                f"received={actual}"
            ),
        )
    return actual


def _require_input_enum(
    value: Any,
    allowed: frozenset[str],
    path: str,
) -> str:
    actual = require_string(value, path)
    if actual not in allowed:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} unsupported={actual}",
        )
    return actual


def _require_output_enum(
    value: Any,
    allowed: frozenset[str],
    path: str,
) -> str:
    actual = require_string(value, path)
    if actual not in allowed:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} unsupported={actual}",
        )
    return actual


def _require_list(
    value: Any,
    path: str,
    *,
    output: bool = False,
) -> list[Any]:
    if not isinstance(value, list):
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            f"{path} must be an array",
        )
    return value


def _require_stage_list(
    value: Any,
    path: str,
) -> list[str]:
    raw_values = _require_list(
        value,
        path,
        output=True,
    )
    values = [
        _require_output_enum(
            item,
            _STAGE_SET,
            f"{path}[{index}]",
        )
        for index, item in enumerate(raw_values)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} contains duplicate stages",
        )
    return values


def _validate_readiness_evidence(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evidence = require_mapping(value, path)
    require_exact_fields(
        evidence,
        {
            "receipt_id",
            "runtime_revision",
            "issued_at",
            "observed_at",
            "valid_until",
            "integrity_verified",
        },
        path=path,
    )

    return {
        "receipt_id": require_string(
            evidence["receipt_id"],
            f"{path}.receipt_id",
        ),
        "runtime_revision": require_string(
            evidence["runtime_revision"],
            f"{path}.runtime_revision",
        ),
        "issued_at": require_nonnegative_int(
            evidence["issued_at"],
            f"{path}.issued_at",
        ),
        "observed_at": require_nonnegative_int(
            evidence["observed_at"],
            f"{path}.observed_at",
        ),
        "valid_until": require_nonnegative_int(
            evidence["valid_until"],
            f"{path}.valid_until",
        ),
        "integrity_verified": require_bool(
            evidence["integrity_verified"],
            f"{path}.integrity_verified",
        ),
    }


def _validate_hr01_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "peer_id",
            "sealed_runtime_revision",
            "readiness_evidence",
        },
        path="inputs",
    )

    validated = {
        "peer_id": require_string(
            inputs["peer_id"],
            "inputs.peer_id",
        ),
        "sealed_runtime_revision": require_string(
            inputs["sealed_runtime_revision"],
            "inputs.sealed_runtime_revision",
        ),
        "readiness_evidence": (
            _validate_readiness_evidence(
                inputs["readiness_evidence"],
                "inputs.readiness_evidence",
            )
        ),
    }

    evidence = validated["readiness_evidence"]
    if (
        evidence["runtime_revision"]
        != validated["sealed_runtime_revision"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-01 readiness evidence must match "
                "the sealed runtime revision"
            ),
        )

    if not (
        evidence["issued_at"]
        < evidence["observed_at"]
        < evidence["valid_until"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-01 requires issued_at < observed_at "
                "< valid_until"
            ),
        )

    return validated


def _validate_hr02_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "peer_id",
            "sealed_runtime_revision",
            "readiness_evidence",
            "adapter_declares_probe_safe",
        },
        path="inputs",
    )

    validated = {
        "peer_id": require_string(
            inputs["peer_id"],
            "inputs.peer_id",
        ),
        "sealed_runtime_revision": require_string(
            inputs["sealed_runtime_revision"],
            "inputs.sealed_runtime_revision",
        ),
        "readiness_evidence": (
            _validate_readiness_evidence(
                inputs["readiness_evidence"],
                "inputs.readiness_evidence",
            )
        ),
        "adapter_declares_probe_safe": require_bool(
            inputs["adapter_declares_probe_safe"],
            "inputs.adapter_declares_probe_safe",
        ),
    }

    evidence = validated["readiness_evidence"]
    if (
        evidence["runtime_revision"]
        != validated["sealed_runtime_revision"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-02 readiness evidence must match "
                "the sealed runtime revision"
            ),
        )

    if not (
        evidence["issued_at"]
        < evidence["valid_until"]
        < evidence["observed_at"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-02 requires issued_at < valid_until "
                "< observed_at"
            ),
        )

    if not evidence["integrity_verified"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-02 isolates expiry and requires "
                "integrity-verified evidence"
            ),
        )

    if validated["adapter_declares_probe_safe"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "HR-02 covers only an adapter that does "
                "not declare automatic revalidation safe"
            ),
        )

    return validated


def _validate_attempted_stage(
    value: Any,
    path: str,
) -> dict[str, str]:
    attempted = require_mapping(value, path)
    require_exact_fields(
        attempted,
        {
            "stage",
            "stage_status",
        },
        path=path,
    )

    return {
        "stage": _require_input_enum(
            attempted["stage"],
            _STAGE_SET,
            f"{path}.stage",
        ),
        "stage_status": _require_input_enum(
            attempted["stage_status"],
            _STAGE_STATUSES,
            f"{path}.stage_status",
        ),
    }


def _validate_nonlegacy_scenario(
    value: Any,
    path: str,
) -> dict[str, Any]:
    scenario = require_mapping(value, path)
    scenario_id = require_string(
        scenario.get("scenario_id"),
        f"{path}.scenario_id",
    )

    usage_scenario = scenario_id in {
        "quota-exhausted",
        "rate-limited",
    }
    required_fields = {
        "scenario_id",
        "attempted_stages",
    }
    if usage_scenario:
        required_fields.add(
            "usage_failure_reason"
        )

    require_exact_fields(
        scenario,
        required_fields,
        path=path,
    )

    raw_stages = _require_list(
        scenario["attempted_stages"],
        f"{path}.attempted_stages",
    )
    if not raw_stages:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.attempted_stages "
                "must not be empty"
            ),
        )

    attempted_stages = [
        _validate_attempted_stage(
            stage,
            f"{path}.attempted_stages[{index}]",
        )
        for index, stage in enumerate(raw_stages)
    ]

    stage_names = [
        stage["stage"]
        for stage in attempted_stages
    ]
    expected_prefix = list(
        _CANONICAL_STAGES[
            : len(attempted_stages)
        ]
    )
    if stage_names != expected_prefix:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.attempted_stages must be "
                "a canonical stage-order prefix"
            ),
        )

    if any(
        stage["stage_status"] != "OK"
        for stage in attempted_stages[:-1]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} requires every stage before "
                "the failing stage to be OK"
            ),
        )

    if (
        attempted_stages[-1]["stage_status"]
        != "FAILED"
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} requires the final attempted "
                "stage to be FAILED"
            ),
        )

    failed_stage = attempted_stages[-1]["stage"]
    if usage_scenario:
        if failed_stage != "check_usage_admission":
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{path} usage scenario must fail "
                    "at check_usage_admission"
                ),
            )
        usage_failure_reason = _require_input_enum(
            scenario["usage_failure_reason"],
            _USAGE_FAILURE_REASONS,
            f"{path}.usage_failure_reason",
        )
        expected_reason = (
            "QUOTA_EXHAUSTED"
            if scenario_id == "quota-exhausted"
            else "RATE_LIMITED"
        )
        if usage_failure_reason != expected_reason:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{path}.usage_failure_reason "
                    "does not match scenario_id"
                ),
            )
    else:
        if failed_stage == "check_usage_admission":
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{path} non-usage scenario cannot "
                    "fail at check_usage_admission"
                ),
            )
        usage_failure_reason = None

    expected_failed_stage = {
        "executable-unavailable": (
            "resolve_executable"
        ),
        "environment-unavailable": (
            "validate_environment"
        ),
        "auth-unavailable": "authenticate",
        "network-unavailable": "connect_network",
        "provider-unavailable": "call_provider",
        "quota-exhausted": (
            "check_usage_admission"
        ),
        "rate-limited": (
            "check_usage_admission"
        ),
    }.get(scenario_id)

    if expected_failed_stage is None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.scenario_id unsupported={scenario_id}",
        )

    if failed_stage != expected_failed_stage:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} failed stage does not match "
                "the closed scenario vector"
            ),
        )

    validated: dict[str, Any] = {
        "scenario_id": scenario_id,
        "attempted_stages": attempted_stages,
    }
    if usage_failure_reason is not None:
        validated[
            "usage_failure_reason"
        ] = usage_failure_reason
    return validated


def _validate_legacy_scenario(
    value: Any,
    path: str,
) -> dict[str, Any]:
    scenario = require_mapping(value, path)
    require_exact_fields(
        scenario,
        {
            "scenario_id",
            "legacy_observation",
        },
        path=path,
    )

    scenario_id = require_string(
        scenario["scenario_id"],
        f"{path}.scenario_id",
    )
    if scenario_id != _LEGACY_SCENARIO_ID:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.scenario_id must identify "
                "the legacy timeout row"
            ),
        )

    observation = require_mapping(
        scenario["legacy_observation"],
        f"{path}.legacy_observation",
    )
    require_exact_fields(
        observation,
        {
            "failure_class",
            "health",
            "gate",
            "admission",
        },
        path=f"{path}.legacy_observation",
    )

    validated_observation = {
        "failure_class": require_string(
            observation["failure_class"],
            (
                f"{path}.legacy_observation."
                "failure_class"
            ),
        ),
        "health": require_string(
            observation["health"],
            f"{path}.legacy_observation.health",
        ),
        "gate": require_string(
            observation["gate"],
            f"{path}.legacy_observation.gate",
        ),
        "admission": require_string(
            observation["admission"],
            (
                f"{path}.legacy_observation."
                "admission"
            ),
        ),
    }
    if validated_observation != {
        "failure_class": (
            "operational_error:timeout"
        ),
        "health": "RED",
        "gate": "closed",
        "admission": "rejected",
    }:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.legacy_observation must "
                "preserve the captured timeout fields"
            ),
        )

    return {
        "scenario_id": scenario_id,
        "legacy_observation": (
            validated_observation
        ),
    }


def _validate_hr03_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {"scenarios"},
        path="inputs",
    )

    raw_scenarios = _require_list(
        inputs["scenarios"],
        "inputs.scenarios",
    )
    if len(raw_scenarios) != 8:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.scenarios must contain "
                "exactly eight rows"
            ),
        )

    scenarios: list[dict[str, Any]] = []
    for index, raw_scenario in enumerate(
        raw_scenarios
    ):
        path = f"inputs.scenarios[{index}]"
        scenario_mapping = require_mapping(
            raw_scenario,
            path,
        )
        scenario_id = require_string(
            scenario_mapping.get(
                "scenario_id"
            ),
            f"{path}.scenario_id",
        )

        if scenario_id == _LEGACY_SCENARIO_ID:
            scenarios.append(
                _validate_legacy_scenario(
                    raw_scenario,
                    path,
                )
            )
        else:
            scenarios.append(
                _validate_nonlegacy_scenario(
                    raw_scenario,
                    path,
                )
            )

    scenario_ids = tuple(
        scenario["scenario_id"]
        for scenario in scenarios
    )
    if scenario_ids != _ALL_SCENARIO_IDS:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.scenarios must contain the "
                "closed eight-row matrix in order"
            ),
        )

    return {"scenarios": scenarios}


def validate_health_recovery_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed HR-01/02/03 input vector."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )
    if base_fixture_id == "HR-01":
        return _validate_hr01_inputs(
            raw_inputs
        )
    if base_fixture_id == "HR-02":
        return _validate_hr02_inputs(
            raw_inputs
        )
    return _validate_hr03_inputs(
        raw_inputs
    )


def _validate_admission(
    value: Any,
    path: str,
    *,
    expected_decision: str,
    provider_effect_field: bool,
    reason_field: bool = False,
) -> dict[str, Any]:
    admission = require_mapping(value, path)
    required = {"decision"}
    if provider_effect_field:
        required.add(
            "provider_effect_permitted"
        )
    if reason_field:
        required.add("reason_code")

    require_exact_fields(
        admission,
        required,
        path=path,
    )

    validated: dict[str, Any] = {
        "decision": _require_literal(
            admission["decision"],
            expected_decision,
            f"{path}.decision",
        )
    }
    if provider_effect_field:
        validated[
            "provider_effect_permitted"
        ] = require_bool(
            admission[
                "provider_effect_permitted"
            ],
            (
                f"{path}."
                "provider_effect_permitted"
            ),
        )
    if reason_field:
        validated["reason_code"] = _require_literal(
            admission["reason_code"],
            "READINESS_STALE",
            f"{path}.reason_code",
        )
    return validated


def _validate_hr01_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "readiness_state",
            "gate_state",
            "admission",
        },
        path="output",
    )

    readiness_state = _require_output_enum(
        output["readiness_state"],
        frozenset(
            {
                "READY",
                "PROBE_INCONCLUSIVE",
            }
        ),
        "output.readiness_state",
    )
    if readiness_state == "READY":
        gate_state = _require_literal(
            output["gate_state"],
            "OPEN",
            "output.gate_state",
        )
        admission = _validate_admission(
            output["admission"],
            "output.admission",
            expected_decision="ADMITTED",
            provider_effect_field=True,
        )
    else:
        gate_state = _require_literal(
            output["gate_state"],
            "CLOSED",
            "output.gate_state",
        )
        admission = _validate_admission(
            output["admission"],
            "output.admission",
            expected_decision="REJECTED",
            provider_effect_field=False,
        )

    return {
        "readiness_state": readiness_state,
        "gate_state": gate_state,
        "admission": admission,
    }


def _validate_hr02_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    readiness_state = _require_output_enum(
        output.get("readiness_state"),
        frozenset(
            {
                "READY",
                "READINESS_STALE",
            }
        ),
        "output.readiness_state",
    )

    if readiness_state == "READY":
        require_exact_fields(
            output,
            {
                "readiness_state",
                "gate_state",
                "admission",
            },
            path="output",
        )
        return {
            "readiness_state": readiness_state,
            "gate_state": _require_literal(
                output["gate_state"],
                "OPEN",
                "output.gate_state",
            ),
            "admission": _validate_admission(
                output["admission"],
                "output.admission",
                expected_decision="ADMITTED",
                provider_effect_field=True,
            ),
        }

    require_exact_fields(
        output,
        {
            "readiness_state",
            "gate_state",
            "admission",
            "revalidation_action",
            "zero_dispatch_calls",
        },
        path="output",
    )
    return {
        "readiness_state": readiness_state,
        "gate_state": _require_literal(
            output["gate_state"],
            "CLOSED",
            "output.gate_state",
        ),
        "admission": _validate_admission(
            output["admission"],
            "output.admission",
            expected_decision="REJECTED",
            provider_effect_field=False,
            reason_field=True,
        ),
        "revalidation_action": _require_literal(
            output["revalidation_action"],
            "REVALIDATION_REQUIRED",
            "output.revalidation_action",
        ),
        "zero_dispatch_calls": require_bool(
            output["zero_dispatch_calls"],
            "output.zero_dispatch_calls",
        ),
    }


def _validate_attempted_trace_row(
    value: Any,
    path: str,
) -> dict[str, str]:
    row = require_mapping(value, path)
    require_exact_fields(
        row,
        {
            "stage",
            "outcome",
        },
        path=path,
    )
    return {
        "stage": _require_output_enum(
            row["stage"],
            _STAGE_SET,
            f"{path}.stage",
        ),
        "outcome": _require_output_enum(
            row["outcome"],
            _STAGE_STATUSES,
            f"{path}.outcome",
        ),
    }


def _validate_nonlegacy_result(
    value: Any,
    path: str,
) -> dict[str, Any]:
    result = require_mapping(value, path)
    require_exact_fields(
        result,
        {
            "scenario_id",
            "classification",
            "admission",
            "attempted_trace",
            "forbidden_downstream_stages",
            "forbidden_stages_present",
        },
        path=path,
    )

    raw_trace = _require_list(
        result["attempted_trace"],
        f"{path}.attempted_trace",
        output=True,
    )
    return {
        "scenario_id": require_string(
            result["scenario_id"],
            f"{path}.scenario_id",
        ),
        "classification": _require_output_enum(
            result["classification"],
            _CLASSIFICATIONS,
            f"{path}.classification",
        ),
        "admission": _require_literal(
            result["admission"],
            "REJECTED",
            f"{path}.admission",
        ),
        "attempted_trace": [
            _validate_attempted_trace_row(
                row,
                f"{path}.attempted_trace[{index}]",
            )
            for index, row in enumerate(raw_trace)
        ],
        "forbidden_downstream_stages": (
            _require_stage_list(
                result[
                    "forbidden_downstream_stages"
                ],
                (
                    f"{path}."
                    "forbidden_downstream_stages"
                ),
            )
        ),
        "forbidden_stages_present": (
            _require_stage_list(
                result[
                    "forbidden_stages_present"
                ],
                (
                    f"{path}."
                    "forbidden_stages_present"
                ),
            )
        ),
    }


def _validate_legacy_result(
    value: Any,
    path: str,
) -> dict[str, Any]:
    result = require_mapping(value, path)
    require_exact_fields(
        result,
        {
            "scenario_id",
            "failure_class",
            "health",
            "gate",
            "admission",
        },
        path=path,
    )
    return {
        "scenario_id": _require_literal(
            result["scenario_id"],
            _LEGACY_SCENARIO_ID,
            f"{path}.scenario_id",
        ),
        "failure_class": _require_literal(
            result["failure_class"],
            "operational_error:timeout",
            f"{path}.failure_class",
        ),
        "health": _require_literal(
            result["health"],
            "RED",
            f"{path}.health",
        ),
        "gate": _require_literal(
            result["gate"],
            "closed",
            f"{path}.gate",
        ),
        "admission": _require_literal(
            result["admission"],
            "rejected",
            f"{path}.admission",
        ),
    }


def _validate_hr03_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {"scenario_results"},
        path="output",
    )

    raw_results = _require_list(
        output["scenario_results"],
        "output.scenario_results",
        output=True,
    )
    if len(raw_results) != 8:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.scenario_results must "
                "contain eight rows"
            ),
        )

    validated: list[dict[str, Any]] = []
    for index, raw_result in enumerate(
        raw_results
    ):
        path = (
            f"output.scenario_results[{index}]"
        )
        result_mapping = require_mapping(
            raw_result,
            path,
        )
        scenario_id = require_string(
            result_mapping.get("scenario_id"),
            f"{path}.scenario_id",
        )
        if scenario_id == _LEGACY_SCENARIO_ID:
            validated.append(
                _validate_legacy_result(
                    raw_result,
                    path,
                )
            )
        else:
            validated.append(
                _validate_nonlegacy_result(
                    raw_result,
                    path,
                )
            )

    if tuple(
        row["scenario_id"]
        for row in validated
    ) != _ALL_SCENARIO_IDS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output scenario order does not match "
                "the closed matrix"
            ),
        )

    return {"scenario_results": validated}


def validate_health_recovery_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject HR-01/02/03 output."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )
    if base_fixture_id == "HR-01":
        return _validate_hr01_output(
            raw_output
        )
    if base_fixture_id == "HR-02":
        return _validate_hr02_output(
            raw_output
        )
    return _validate_hr03_output(
        raw_output
    )


def _ready_projection() -> dict[str, Any]:
    return {
        "readiness_state": "READY",
        "gate_state": "OPEN",
        "admission": {
            "decision": "ADMITTED",
            "provider_effect_permitted": True,
        },
    }


def _inconclusive_projection() -> dict[str, Any]:
    return {
        "readiness_state": "PROBE_INCONCLUSIVE",
        "gate_state": "CLOSED",
        "admission": {
            "decision": "REJECTED",
        },
    }


def _stale_projection() -> dict[str, Any]:
    return {
        "readiness_state": "READINESS_STALE",
        "gate_state": "CLOSED",
        "admission": {
            "decision": "REJECTED",
            "reason_code": "READINESS_STALE",
        },
        "revalidation_action": (
            "REVALIDATION_REQUIRED"
        ),
        "zero_dispatch_calls": True,
    }


def _classification_for_scenario(
    scenario: Mapping[str, Any],
) -> str:
    failed_stage = scenario[
        "attempted_stages"
    ][-1]["stage"]
    if failed_stage == "check_usage_admission":
        return scenario[
            "usage_failure_reason"
        ]
    return _STAGE_CLASSIFICATIONS[
        failed_stage
    ]


def _derived_scenario_result(
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        scenario["scenario_id"]
        == _LEGACY_SCENARIO_ID
    ):
        observation = scenario[
            "legacy_observation"
        ]
        return {
            "scenario_id": scenario["scenario_id"],
            "failure_class": observation[
                "failure_class"
            ],
            "health": observation["health"],
            "gate": observation["gate"],
            "admission": observation["admission"],
        }

    attempted_stages = scenario[
        "attempted_stages"
    ]
    failed_stage = attempted_stages[-1]["stage"]
    failed_index = _CANONICAL_STAGES.index(
        failed_stage
    )
    forbidden = list(
        _CANONICAL_STAGES[
            failed_index + 1 :
        ]
    )
    attempted_names = {
        row["stage"]
        for row in attempted_stages
    }

    return {
        "scenario_id": scenario["scenario_id"],
        "classification": (
            _classification_for_scenario(
                scenario
            )
        ),
        "admission": "REJECTED",
        "attempted_trace": [
            {
                "stage": row["stage"],
                "outcome": row["stage_status"],
            }
            for row in attempted_stages
        ],
        "forbidden_downstream_stages": (
            forbidden
        ),
        "forbidden_stages_present": [
            stage
            for stage in forbidden
            if stage in attempted_names
        ],
    }


def _oracle_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "HR-01":
        if raw_inputs["readiness_evidence"][
            "integrity_verified"
        ]:
            return _ready_projection()
        return _inconclusive_projection()

    if base_fixture_id == "HR-02":
        return _stale_projection()

    return {
        "scenario_results": [
            _derived_scenario_result(
                scenario
            )
            for scenario in raw_inputs["scenarios"]
        ]
    }


def _subject_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "HR-01":
        evidence = raw_inputs[
            "readiness_evidence"
        ]
        revision_current = (
            evidence["runtime_revision"]
            == raw_inputs[
                "sealed_runtime_revision"
            ]
        )
        fresh = bool(
            evidence["issued_at"]
            < evidence["observed_at"]
            < evidence["valid_until"]
        )
        if (
            revision_current
            and fresh
            and evidence["integrity_verified"]
        ):
            return _ready_projection()
        return _inconclusive_projection()

    if base_fixture_id == "HR-02":
        evidence = raw_inputs[
            "readiness_evidence"
        ]
        expired = (
            evidence["observed_at"]
            > evidence["valid_until"]
        )
        probe_unsupported = not raw_inputs[
            "adapter_declares_probe_safe"
        ]
        if expired and probe_unsupported:
            return _stale_projection()
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "HR-02 facts do not select the frozen branch",
        )

    scenario_results: list[
        dict[str, Any]
    ] = []
    for scenario in raw_inputs["scenarios"]:
        scenario_results.append(
            _derived_scenario_result(
                scenario
            )
        )
    return {
        "scenario_results": scenario_results
    }


class HealthRecoveryOracle:
    """Pure expected oracle for HR-01 through HR-03."""

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
                    f"oracle_id={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        return _oracle_output(
            self._base,
            raw_inputs,
        )


class HealthRecoverySubjectAdapter:
    """Pure reference adapter over injected health facts."""

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
            f"health_recovery.{label}.reference"
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

        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        return _subject_output(
            self._base,
            raw_inputs,
        )


class FaultInjectedHealthRecoveryAdapter:
    """One isolated health-recovery defect per negative fixture."""

    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        self._fixture_id = (
            f"{base_fixture_id}"
            f"{_NEGATIVE_SUFFIX}"
        )
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            f"health_recovery.{label}."
            "fault_injected"
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
        del context

        if fixture_id != self._fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base in {"HR-01", "HR-02"}:
            return _ready_projection()

        output = _subject_output(
            self._base,
            raw_inputs,
        )
        first_row = output[
            "scenario_results"
        ][0]
        first_row["attempted_trace"].append(
            {
                "stage": "connect_network",
                "outcome": "OK",
            }
        )
        first_row[
            "forbidden_stages_present"
        ] = ["connect_network"]
        return output


def health_recovery_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all HR-01 through HR-03 registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = HealthRecoveryOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=HealthRecoverySubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_health_recovery_inputs
                ),
                output_validator=(
                    validate_health_recovery_output
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
                    FaultInjectedHealthRecoveryAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_health_recovery_inputs
                ),
                output_validator=(
                    validate_health_recovery_output
                ),
            )
        )

    return tuple(registrations)
