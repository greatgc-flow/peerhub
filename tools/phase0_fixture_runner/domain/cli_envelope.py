"""CANDIDATE-tier CLI JSONL envelope rules for CJ-01/03/04/06.

The inputs are injected stage facts; this module performs no real byte,
JSON, command, process, or dispatch operations. Framing and JSON parsing
precede version/schema negotiation. CJ-04 therefore represents rejection
after successful framing/JSON parsing but before command parsing or dispatch.
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
    "CJ-01",
    "CJ-03",
    "CJ-04",
    "CJ-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "CJ-01": (
        "cli_envelope.cj01."
        "read_only_typed_result"
    ),
    "CJ-03": (
        "cli_envelope.cj03."
        "malformed_pre_effect_rejection"
    ),
    "CJ-04": (
        "cli_envelope.cj04."
        "unsupported_version_evidence"
    ),
    "CJ-06": (
        "cli_envelope.cj06."
        "redacted_error_mapping"
    ),
}

_MALFORMED_REASONS = frozenset(
    {
        "MISSING_ACTOR_IDENTITY",
        "INVALID_WORKSPACE_SCOPE_TYPE",
    }
)

# Exit code 3 intentionally matches command_authz.py's auth/admission family.
_EXIT_CODE_BY_ERROR_CODE = {
    "ENVELOPE_MALFORMED": 2,
    "PROTOCOL_VERSION_UNSUPPORTED": 2,
    "ACTOR_UNAUTHORIZED": 3,
    "ADMISSION_REVISION_MISMATCH": 3,
}
_ERROR_CODES = frozenset(
    _EXIT_CODE_BY_ERROR_CODE
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


def _require_int_list(
    value: Any,
    path: str,
    *,
    allow_empty: bool,
    output: bool = False,
) -> list[int]:
    raw_values = _require_list(
        value,
        path,
        output=output,
    )
    if not raw_values and not allow_empty:
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            f"{path} must not be empty",
        )

    return [
        require_nonnegative_int(
            item,
            f"{path}[{index}]",
        )
        for index, item in enumerate(raw_values)
    ]


def _require_string_list(
    value: Any,
    path: str,
    *,
    allow_empty: bool,
    output: bool = False,
) -> list[str]:
    raw_values = _require_list(
        value,
        path,
        output=output,
    )
    if not raw_values and not allow_empty:
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            f"{path} must not be empty",
        )

    return [
        require_string(
            item,
            f"{path}[{index}]",
        )
        for index, item in enumerate(raw_values)
    ]


def _opaque_mapping(
    value: Any,
    path: str,
) -> dict[str, Any]:
    return dict(require_mapping(value, path))


def _validate_cj01_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "envelope",
            "injected_result_payload",
        },
        path="inputs",
    )

    envelope = require_mapping(
        inputs["envelope"],
        "inputs.envelope",
    )
    require_exact_fields(
        envelope,
        {
            "actor_identity",
            "workspace_scope",
            "query_selector",
        },
        path="inputs.envelope",
    )

    return {
        "envelope": {
            "actor_identity": require_string(
                envelope["actor_identity"],
                "inputs.envelope.actor_identity",
            ),
            "workspace_scope": require_string(
                envelope["workspace_scope"],
                "inputs.envelope.workspace_scope",
            ),
            "query_selector": require_string(
                envelope["query_selector"],
                "inputs.envelope.query_selector",
            ),
        },
        "injected_result_payload": _opaque_mapping(
            inputs["injected_result_payload"],
            "inputs.injected_result_payload",
        ),
    }


def _validate_cj03_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "malformed_reason",
            "framing_valid",
            "protocol_supported",
        },
        path="inputs",
    )

    validated = {
        "malformed_reason": _require_input_enum(
            inputs["malformed_reason"],
            _MALFORMED_REASONS,
            "inputs.malformed_reason",
        ),
        "framing_valid": require_bool(
            inputs["framing_valid"],
            "inputs.framing_valid",
        ),
        "protocol_supported": require_bool(
            inputs["protocol_supported"],
            "inputs.protocol_supported",
        ),
    }

    if not validated["framing_valid"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "CJ-03 isolates envelope shape failure and "
                "requires valid framing"
            ),
        )

    if not validated["protocol_supported"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "CJ-03 isolates envelope shape failure and "
                "requires a supported protocol"
            ),
        )

    return validated


def _validate_cj04_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "protocol_major",
            "schema_name",
            "supported_protocol_majors",
            "supported_schema_names",
        },
        path="inputs",
    )

    validated = {
        "protocol_major": require_nonnegative_int(
            inputs["protocol_major"],
            "inputs.protocol_major",
        ),
        "schema_name": require_string(
            inputs["schema_name"],
            "inputs.schema_name",
        ),
        "supported_protocol_majors": (
            _require_int_list(
                inputs["supported_protocol_majors"],
                "inputs.supported_protocol_majors",
                allow_empty=False,
            )
        ),
        "supported_schema_names": (
            _require_string_list(
                inputs["supported_schema_names"],
                "inputs.supported_schema_names",
                allow_empty=False,
            )
        ),
    }

    protocol_unsupported = (
        validated["protocol_major"]
        not in validated["supported_protocol_majors"]
    )
    schema_unsupported = (
        validated["schema_name"]
        not in validated["supported_schema_names"]
    )

    if protocol_unsupported == schema_unsupported:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "CJ-04 requires exactly one unsupported "
                "protocol-major or schema dimension"
            ),
        )

    return validated


def _validate_cj06_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "error_code",
            "raw_sensitive_value",
        },
        path="inputs",
    )

    return {
        "error_code": _require_input_enum(
            inputs["error_code"],
            _ERROR_CODES,
            "inputs.error_code",
        ),
        "raw_sensitive_value": require_string(
            inputs["raw_sensitive_value"],
            "inputs.raw_sensitive_value",
        ),
    }


def validate_cli_envelope_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one CLI-envelope fact-injection vector."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )

    if base_fixture_id == "CJ-01":
        return _validate_cj01_inputs(
            raw_inputs
        )
    if base_fixture_id == "CJ-03":
        return _validate_cj03_inputs(
            raw_inputs
        )
    if base_fixture_id == "CJ-04":
        return _validate_cj04_inputs(
            raw_inputs
        )
    return _validate_cj06_inputs(
        raw_inputs
    )


def _validate_cj01_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "status",
            "actor_identity",
            "workspace_scope",
            "result",
            "zero_state_mutations",
            "zero_receipt_writes",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "OK",
            "output.status",
        ),
        "actor_identity": require_string(
            output["actor_identity"],
            "output.actor_identity",
        ),
        "workspace_scope": require_string(
            output["workspace_scope"],
            "output.workspace_scope",
        ),
        "result": _opaque_mapping(
            output["result"],
            "output.result",
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
        "zero_receipt_writes": require_bool(
            output["zero_receipt_writes"],
            "output.zero_receipt_writes",
        ),
    }


def _validate_cj03_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "status",
            "error_code",
            "exit_code",
            "effect_certainty",
            "zero_state_mutations",
            "zero_receipt_writes",
            "zero_outbox_writes",
            "zero_dispatch_calls",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "REJECTED",
            "output.status",
        ),
        "error_code": _require_literal(
            output["error_code"],
            "ENVELOPE_MALFORMED",
            "output.error_code",
        ),
        "exit_code": require_nonnegative_int(
            output["exit_code"],
            "output.exit_code",
        ),
        "effect_certainty": _require_literal(
            output["effect_certainty"],
            "NOT_STARTED",
            "output.effect_certainty",
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
        "zero_receipt_writes": require_bool(
            output["zero_receipt_writes"],
            "output.zero_receipt_writes",
        ),
        "zero_outbox_writes": require_bool(
            output["zero_outbox_writes"],
            "output.zero_outbox_writes",
        ),
        "zero_dispatch_calls": require_bool(
            output["zero_dispatch_calls"],
            "output.zero_dispatch_calls",
        ),
    }


def _validate_cj04_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "status",
            "error_code",
            "exit_code",
            "effect_certainty",
            "supported_protocol_majors",
            "supported_schema_names",
            "zero_state_mutations",
            "zero_dispatch_calls",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "REJECTED",
            "output.status",
        ),
        "error_code": _require_literal(
            output["error_code"],
            "PROTOCOL_VERSION_UNSUPPORTED",
            "output.error_code",
        ),
        "exit_code": require_nonnegative_int(
            output["exit_code"],
            "output.exit_code",
        ),
        "effect_certainty": _require_literal(
            output["effect_certainty"],
            "NOT_STARTED",
            "output.effect_certainty",
        ),
        "supported_protocol_majors": (
            _require_int_list(
                output["supported_protocol_majors"],
                "output.supported_protocol_majors",
                allow_empty=True,
                output=True,
            )
        ),
        "supported_schema_names": (
            _require_string_list(
                output["supported_schema_names"],
                "output.supported_schema_names",
                allow_empty=True,
                output=True,
            )
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
        "zero_dispatch_calls": require_bool(
            output["zero_dispatch_calls"],
            "output.zero_dispatch_calls",
        ),
    }


def _validate_cj06_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "status",
            "error_code",
            "exit_code",
            "message",
            "zero_state_mutations",
            "zero_receipt_writes",
            "zero_outbox_writes",
            "zero_dispatch_calls",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "REJECTED",
            "output.status",
        ),
        "error_code": _require_output_enum(
            output["error_code"],
            _ERROR_CODES,
            "output.error_code",
        ),
        "exit_code": require_nonnegative_int(
            output["exit_code"],
            "output.exit_code",
        ),
        "message": require_string(
            output["message"],
            "output.message",
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
        "zero_receipt_writes": require_bool(
            output["zero_receipt_writes"],
            "output.zero_receipt_writes",
        ),
        "zero_outbox_writes": require_bool(
            output["zero_outbox_writes"],
            "output.zero_outbox_writes",
        ),
        "zero_dispatch_calls": require_bool(
            output["zero_dispatch_calls"],
            "output.zero_dispatch_calls",
        ),
    }


def validate_cli_envelope_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject CLI-envelope output."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )

    if base_fixture_id == "CJ-01":
        return _validate_cj01_output(
            raw_output
        )
    if base_fixture_id == "CJ-03":
        return _validate_cj03_output(
            raw_output
        )
    if base_fixture_id == "CJ-04":
        return _validate_cj04_output(
            raw_output
        )
    return _validate_cj06_output(
        raw_output
    )


def _oracle_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "CJ-01":
        envelope = raw_inputs["envelope"]
        return {
            "status": "OK",
            "actor_identity": envelope[
                "actor_identity"
            ],
            "workspace_scope": envelope[
                "workspace_scope"
            ],
            "result": dict(
                raw_inputs[
                    "injected_result_payload"
                ]
            ),
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
        }

    if base_fixture_id == "CJ-03":
        return {
            "status": "REJECTED",
            "error_code": "ENVELOPE_MALFORMED",
            "exit_code": _EXIT_CODE_BY_ERROR_CODE[
                "ENVELOPE_MALFORMED"
            ],
            "effect_certainty": "NOT_STARTED",
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
            "zero_outbox_writes": True,
            "zero_dispatch_calls": True,
        }

    if base_fixture_id == "CJ-04":
        return {
            "status": "REJECTED",
            "error_code": (
                "PROTOCOL_VERSION_UNSUPPORTED"
            ),
            "exit_code": _EXIT_CODE_BY_ERROR_CODE[
                "PROTOCOL_VERSION_UNSUPPORTED"
            ],
            "effect_certainty": "NOT_STARTED",
            "supported_protocol_majors": list(
                raw_inputs[
                    "supported_protocol_majors"
                ]
            ),
            "supported_schema_names": list(
                raw_inputs[
                    "supported_schema_names"
                ]
            ),
            "zero_state_mutations": True,
            "zero_dispatch_calls": True,
        }

    error_code = raw_inputs["error_code"]
    return {
        "status": "REJECTED",
        "error_code": error_code,
        "exit_code": _EXIT_CODE_BY_ERROR_CODE[
            error_code
        ],
        "message": (
            f"request rejected: {error_code}"
        ),
        "zero_state_mutations": True,
        "zero_receipt_writes": True,
        "zero_outbox_writes": True,
        "zero_dispatch_calls": True,
    }


def _subject_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "CJ-01":
        envelope = raw_inputs["envelope"]
        typed_payload = {
            key: value
            for key, value in raw_inputs[
                "injected_result_payload"
            ].items()
        }
        return {
            "status": "OK",
            "actor_identity": envelope.get(
                "actor_identity"
            ),
            "workspace_scope": envelope.get(
                "workspace_scope"
            ),
            "result": typed_payload,
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
        }

    if base_fixture_id == "CJ-03":
        framing_passed = raw_inputs[
            "framing_valid"
        ]
        protocol_passed = raw_inputs[
            "protocol_supported"
        ]
        malformed = (
            raw_inputs["malformed_reason"]
            in _MALFORMED_REASONS
        )
        if not (
            framing_passed
            and protocol_passed
            and malformed
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "CJ-03 stage facts do not isolate malformed shape",
            )

        error_code = "ENVELOPE_MALFORMED"
        return {
            "status": "REJECTED",
            "error_code": error_code,
            "exit_code": _EXIT_CODE_BY_ERROR_CODE[
                error_code
            ],
            "effect_certainty": "NOT_STARTED",
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
            "zero_outbox_writes": True,
            "zero_dispatch_calls": True,
        }

    if base_fixture_id == "CJ-04":
        protocol_supported = (
            raw_inputs["protocol_major"]
            in raw_inputs[
                "supported_protocol_majors"
            ]
        )
        schema_supported = (
            raw_inputs["schema_name"]
            in raw_inputs[
                "supported_schema_names"
            ]
        )
        if protocol_supported and schema_supported:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "CJ-04 version facts are fully supported",
            )
        if (
            not protocol_supported
            and not schema_supported
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "CJ-04 version facts contain two "
                    "unsupported dimensions"
                ),
            )

        error_code = (
            "PROTOCOL_VERSION_UNSUPPORTED"
        )
        return {
            "status": "REJECTED",
            "error_code": error_code,
            "exit_code": _EXIT_CODE_BY_ERROR_CODE[
                error_code
            ],
            "effect_certainty": "NOT_STARTED",
            "supported_protocol_majors": [
                value
                for value in raw_inputs[
                    "supported_protocol_majors"
                ]
            ],
            "supported_schema_names": [
                value
                for value in raw_inputs[
                    "supported_schema_names"
                ]
            ],
            "zero_state_mutations": True,
            "zero_dispatch_calls": True,
        }

    error_code = raw_inputs["error_code"]
    mapped_exit_code = (
        _EXIT_CODE_BY_ERROR_CODE.get(
            error_code
        )
    )
    if mapped_exit_code is None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"CJ-06 unmapped error_code={error_code}",
        )

    generic_message = "request rejected: " + error_code
    return {
        "status": "REJECTED",
        "error_code": error_code,
        "exit_code": mapped_exit_code,
        "message": generic_message,
        "zero_state_mutations": True,
        "zero_receipt_writes": True,
        "zero_outbox_writes": True,
        "zero_dispatch_calls": True,
    }


class CliEnvelopeOracle:
    """Pure expected oracle for CJ-01/03/04/06."""

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


class CliEnvelopeSubjectAdapter:
    """Pure reference adapter over injected pipeline facts."""

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
            f"cli_envelope.{label}.reference"
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


class FaultInjectedCliEnvelopeAdapter:
    """One isolated CLI-envelope defect per negative fixture."""

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
            f"cli_envelope.{label}."
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

        output = _subject_output(
            self._base,
            raw_inputs,
        )

        if self._base == "CJ-01":
            output["zero_receipt_writes"] = False
        elif self._base == "CJ-03":
            output["zero_state_mutations"] = False
        elif self._base == "CJ-04":
            output[
                "supported_protocol_majors"
            ] = []
            output[
                "supported_schema_names"
            ] = []
        else:
            output["message"] = (
                f"request rejected: "
                f"{raw_inputs['error_code']}: "
                f"{raw_inputs['raw_sensitive_value']}"
            )

        return output


def cli_envelope_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all CJ-01/03/04/06 registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = CliEnvelopeOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=CliEnvelopeSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_cli_envelope_inputs
                ),
                output_validator=(
                    validate_cli_envelope_output
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
                    FaultInjectedCliEnvelopeAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_cli_envelope_inputs
                ),
                output_validator=(
                    validate_cli_envelope_output
                ),
            )
        )

    return tuple(registrations)
