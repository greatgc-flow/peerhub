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
    "DT-01",
    "DT-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "DT-01": "process_lifecycle.dt01.exit_success",
    "DT-06": "process_lifecycle.dt06.exit_failure_with_cleanup_error",
}


def _base_fixture_id(fixture_id: str) -> str:
    for base in _BASE_FIXTURES:
        if fixture_id in {base, f"{base}{_NEGATIVE_SUFFIX}"}:
            return base
    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base = _base_fixture_id(fixture_id)

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base}.{detail}",
        )

    if not facts["spawned"]:
        invalid("requires an observed SPAWNED event")
    if facts["chunk_count"] < 1:
        invalid("requires at least one CHUNK event")

    if base == "DT-01":
        if facts["exit_code"] != 0:
            invalid("requires exit_code 0")
        if facts["cleanup_error_count"] != 0:
            invalid("requires zero cleanup errors")
        return

    if facts["exit_code"] == 0:
        invalid("requires a nonzero exit_code")
    if facts["cleanup_error_count"] < 1:
        invalid("requires at least one cleanup error")


def validate_process_lifecycle_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed spawn/chunk/exit/cleanup-error vector.

    Restricted to the MUST/OBS rule subset agreed in
    DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md: SPAWNED is observed start
    evidence, EXIT(code) sets the terminal result and outcome, and
    CLEANUP_ERROR is attached evidence that never overwrites that result.
    """

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "spawned",
            "chunk_count",
            "exit_code",
            "cleanup_error_count",
        },
        path="inputs",
    )

    normalized = {
        "spawned": require_bool(
            inputs["spawned"],
            "inputs.spawned",
        ),
        "chunk_count": require_nonnegative_int(
            inputs["chunk_count"],
            "inputs.chunk_count",
        ),
        "exit_code": require_nonnegative_int(
            inputs["exit_code"],
            "inputs.exit_code",
        ),
        "cleanup_error_count": require_nonnegative_int(
            inputs["cleanup_error_count"],
            "inputs.cleanup_error_count",
        ),
    }

    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def validate_process_lifecycle_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable spawn/chunk/exit/cleanup-error evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "terminal_classification",
            "effect_certainty",
            "execution_outcome",
            "cleanup_error_count",
        },
        path="output",
    )

    terminal_classification = require_string(
        output["terminal_classification"],
        "output.terminal_classification",
    )
    if terminal_classification != "EXITED":
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.terminal_classification "
                f"unsupported={terminal_classification}"
            ),
        )

    effect_certainty = require_string(
        output["effect_certainty"],
        "output.effect_certainty",
    )
    if effect_certainty not in {"STARTED", "MAY_HAVE_STARTED"}:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.effect_certainty unsupported={effect_certainty}",
        )

    execution_outcome = require_string(
        output["execution_outcome"],
        "output.execution_outcome",
    )
    if execution_outcome not in {"SUCCEEDED", "FAILED"}:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.execution_outcome unsupported={execution_outcome}",
        )

    return {
        "terminal_classification": terminal_classification,
        "effect_certainty": effect_certainty,
        "execution_outcome": execution_outcome,
        "cleanup_error_count": require_nonnegative_int(
            output["cleanup_error_count"],
            "output.cleanup_error_count",
        ),
    }


def _evaluate(facts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "terminal_classification": "EXITED",
        "effect_certainty": "STARTED",
        "execution_outcome": (
            "SUCCEEDED" if facts["exit_code"] == 0 else "FAILED"
        ),
        "cleanup_error_count": facts["cleanup_error_count"],
    }


class ProcessLifecycleOracle:
    """Pure oracle over the agreed MUST/OBS rule subset only."""

    oracle_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self.oracle_id = _ORACLE_IDS[base_fixture_id]
        self.fixture_ids = frozenset(
            {base_fixture_id, f"{base_fixture_id}{_NEGATIVE_SUFFIX}"}
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
        return _evaluate(raw_inputs)


class ProcessLifecycleSubjectAdapter:
    """Reference adapter -- structurally independent re-implementation."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"process_lifecycle.{label}.reference"
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
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        outcome = "FAILED"
        if raw_inputs["exit_code"] == 0:
            outcome = "SUCCEEDED"

        return {
            "terminal_classification": "EXITED",
            "effect_certainty": "STARTED",
            "execution_outcome": outcome,
            "cleanup_error_count": raw_inputs["cleanup_error_count"],
        }


class FaultInjectedProcessLifecycleAdapter:
    """One genuine defect per negative, isolated to a single R2 MUST rule."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        faults = {
            "DT-01": "understate_certainty_despite_exit_evidence",
            "DT-06": "cleanup_error_overwrites_outcome",
        }
        self._fault = faults[base_fixture_id]
        self._fixture_id = f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"process_lifecycle.{label}.{self._fault}"
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
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        if self._base == "DT-01":
            # Wrongly downgrades certainty despite direct SPAWNED+EXIT
            # observation -- violates the OBS-tier rule that a completed
            # EXIT following an observed SPAWNED is strong, not partial,
            # start evidence.
            return {
                "terminal_classification": "EXITED",
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "SUCCEEDED",
                "cleanup_error_count": raw_inputs["cleanup_error_count"],
            }

        # Lets attached cleanup evidence overwrite the primary terminal
        # result's execution_outcome -- directly violates R2 section 4's
        # MUST rule that cleanup evidence is attached to, and never
        # overwrites, the primary terminal result.
        return {
            "terminal_classification": "EXITED",
            "effect_certainty": "STARTED",
            "execution_outcome": "SUCCEEDED",
            "cleanup_error_count": raw_inputs["cleanup_error_count"],
        }


def process_lifecycle_registrations() -> tuple[DomainRegistration, ...]:
    """Return the DT-01/DT-06 registrations (DP-06 remains out of scope)."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = ProcessLifecycleOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=ProcessLifecycleSubjectAdapter(base_fixture_id),
                input_validator=validate_process_lifecycle_inputs,
                output_validator=validate_process_lifecycle_output,
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=FaultInjectedProcessLifecycleAdapter(
                    base_fixture_id
                ),
                input_validator=validate_process_lifecycle_inputs,
                output_validator=validate_process_lifecycle_output,
            )
        )

    return tuple(registrations)
