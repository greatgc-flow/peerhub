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
    require_string,
)

_BASE_FIXTURE = "DP-06"
_NEGATIVE_SUFFIX = "-NEG-01"
_NEGATIVE_FIXTURE = f"{_BASE_FIXTURE}{_NEGATIVE_SUFFIX}"
_ORACLE_ID = (
    "dispatch_pipe_recovery.dp06.dispatch_intent_boundary"
)

_TERMINAL_CLASSIFICATIONS = frozenset(
    {
        "START_UNCERTAIN",
        "NOT_STARTED",
    }
)
_EFFECT_CERTAINTIES = frozenset(
    {
        "MAY_HAVE_STARTED",
        "NOT_STARTED",
    }
)
_EXECUTION_OUTCOMES = frozenset(
    {
        "UNKNOWN",
        "NOT_STARTED",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
    if fixture_id in {_BASE_FIXTURE, _NEGATIVE_FIXTURE}:
        return _BASE_FIXTURE
    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


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


def validate_dispatch_pipe_recovery_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the single post-append DP-06 recovery boundary."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "injected_command_id",
            "append_completed",
            "later_terminal_evidence_present",
        },
        path="inputs",
    )

    validated = {
        "injected_command_id": require_string(
            inputs["injected_command_id"],
            "inputs.injected_command_id",
        ),
        "append_completed": require_bool(
            inputs["append_completed"],
            "inputs.append_completed",
        ),
        "later_terminal_evidence_present": require_bool(
            inputs["later_terminal_evidence_present"],
            "inputs.later_terminal_evidence_present",
        ),
    }

    if not validated["append_completed"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "DP-06 covers only interruption after "
                "INTENT_PERSISTED durable append"
            ),
        )

    if validated["later_terminal_evidence_present"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "DP-06 covers only recovery with no later "
                "SPAWNED, EXIT, or terminal evidence"
            ),
        )

    return validated


def validate_dispatch_pipe_recovery_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a DP-06 recovery classification output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "terminal_classification",
            "effect_certainty",
            "execution_outcome",
            "automatically_replayed",
        },
        path="output",
    )

    return {
        "terminal_classification": _require_output_enum(
            output["terminal_classification"],
            _TERMINAL_CLASSIFICATIONS,
            "output.terminal_classification",
        ),
        "effect_certainty": _require_output_enum(
            output["effect_certainty"],
            _EFFECT_CERTAINTIES,
            "output.effect_certainty",
        ),
        "execution_outcome": _require_output_enum(
            output["execution_outcome"],
            _EXECUTION_OUTCOMES,
            "output.execution_outcome",
        ),
        "automatically_replayed": require_bool(
            output["automatically_replayed"],
            "output.automatically_replayed",
        ),
    }


class DispatchPipeRecoveryOracle:
    """Pure R3 dispatch-intent recovery-boundary oracle."""

    oracle_version = 1

    def __init__(self) -> None:
        self.oracle_id = _ORACLE_ID
        self.fixture_ids = frozenset(
            {
                _BASE_FIXTURE,
                _NEGATIVE_FIXTURE,
            }
        )

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _base_fixture_id(fixture_id)
        if (
            raw_inputs["append_completed"]
            and not raw_inputs["later_terminal_evidence_present"]
        ):
            return {
                "terminal_classification": "START_UNCERTAIN",
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "UNKNOWN",
                "automatically_replayed": False,
            }

        raise DomainContractError(
            "DOMAIN_ORACLE_INVALID",
            "unsupported DP-06 recovery facts",
        )


class DispatchPipeRecoverySubjectAdapter:
    """Pure reference adapter for the R3 recovery boundary."""

    adapter_version = 1

    def __init__(self) -> None:
        self.adapter_id = "dispatch_pipe_recovery.dp06.reference"
        self.fixture_ids = frozenset({_BASE_FIXTURE})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != _BASE_FIXTURE:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        append_exists = raw_inputs["append_completed"]
        no_terminal_receipt = not raw_inputs[
            "later_terminal_evidence_present"
        ]
        if append_exists and no_terminal_receipt:
            return {
                "terminal_classification": "START_UNCERTAIN",
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "UNKNOWN",
                "automatically_replayed": False,
            }

        raise DomainContractError(
            "DOMAIN_ADAPTER_INVALID",
            "reference adapter received unsupported DP-06 facts",
        )


class FaultInjectedDispatchPipeRecoveryAdapter:
    """Fault: treats a post-append interruption as definitely not started."""

    adapter_version = 1

    def __init__(self) -> None:
        self.adapter_id = (
            "dispatch_pipe_recovery.dp06.fault_pre_dispatch"
        )
        self.fixture_ids = frozenset({_NEGATIVE_FIXTURE})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != _NEGATIVE_FIXTURE:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        return {
            "terminal_classification": "NOT_STARTED",
            "effect_certainty": "NOT_STARTED",
            "execution_outcome": "NOT_STARTED",
            "automatically_replayed": False,
        }


def dispatch_pipe_recovery_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return the DP-06 recovery-boundary registrations."""

    oracle = DispatchPipeRecoveryOracle()
    return (
        DomainRegistration(
            fixture_id=_BASE_FIXTURE,
            oracle_id=oracle.oracle_id,
            oracle_version=oracle.oracle_version,
            oracle=oracle,
            adapter=DispatchPipeRecoverySubjectAdapter(),
            input_validator=validate_dispatch_pipe_recovery_inputs,
            output_validator=validate_dispatch_pipe_recovery_output,
        ),
        DomainRegistration(
            fixture_id=_NEGATIVE_FIXTURE,
            oracle_id=oracle.oracle_id,
            oracle_version=oracle.oracle_version,
            oracle=oracle,
            adapter=FaultInjectedDispatchPipeRecoveryAdapter(),
            input_validator=validate_dispatch_pipe_recovery_inputs,
            output_validator=validate_dispatch_pipe_recovery_output,
        ),
    )
