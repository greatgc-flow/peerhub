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
    "AC-03-01",
    "AC-03-02",
    "AC-03-03",
    "AC-03-04",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-03-01": (
        "authority_shadow.ac0301.same_revision_equivalence"
    ),
    "AC-03-02": (
        "authority_shadow.ac0302.same_revision_value_drift"
    ),
    "AC-03-03": (
        "authority_shadow.ac0303.revision_change_reset"
    ),
    "AC-03-04": (
        "authority_shadow.ac0304.no_effect_observation"
    ),
}

_DISPOSITIONS = frozenset(
    {
        "SAME_REVISION_EQUIVALENT",
        "SAME_REVISION_VALUE_DRIFT",
        "REVISION_CHANGED_RESET",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
    for base in _BASE_FIXTURES:
        if fixture_id in {base, f"{base}{_NEGATIVE_SUFFIX}"}:
            return base
    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    return value


def _validate_comparison(
    value: Any,
    path: str,
) -> dict[str, str]:
    comparison = require_mapping(value, path)
    require_exact_fields(
        comparison,
        {
            "comparison_id",
            "source_revision",
            "configuration_revision",
            "candidate_value",
            "legacy_value",
        },
        path=path,
    )
    return {
        "comparison_id": require_string(
            comparison["comparison_id"],
            f"{path}.comparison_id",
        ),
        "source_revision": require_string(
            comparison["source_revision"],
            f"{path}.source_revision",
        ),
        "configuration_revision": require_string(
            comparison["configuration_revision"],
            f"{path}.configuration_revision",
        ),
        "candidate_value": require_string(
            comparison["candidate_value"],
            f"{path}.candidate_value",
        ),
        "legacy_value": require_string(
            comparison["legacy_value"],
            f"{path}.legacy_value",
        ),
    }


def _validate_fixture_vector(
    fixture_id: str,
    inputs: Mapping[str, Any],
) -> None:
    base = _base_fixture_id(fixture_id)
    baseline = (
        inputs["baseline_source_revision"],
        inputs["baseline_configuration_revision"],
    )
    comparisons = inputs["comparisons"]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base}.{detail}",
        )

    same_as_baseline = [
        (
            comparison["source_revision"],
            comparison["configuration_revision"],
        )
        == baseline
        for comparison in comparisons
    ]
    equivalent = [
        comparison["candidate_value"]
        == comparison["legacy_value"]
        for comparison in comparisons
    ]

    if base == "AC-03-01":
        if (
            inputs["initial_consecutive_equivalence_streak"] < 1
            or len(comparisons) != 2
            or not all(same_as_baseline)
            or not all(equivalent)
        ):
            invalid(
                "requires two equal same-revision comparisons "
                "after a nonzero streak"
            )
        return

    if base == "AC-03-02":
        if (
            inputs["initial_consecutive_equivalence_streak"] < 1
            or len(comparisons) != 1
            or not same_as_baseline[0]
            or equivalent[0]
        ):
            invalid(
                "requires one mismatched comparison under "
                "the unchanged baseline revision"
            )
        return

    if base == "AC-03-03":
        if (
            inputs["initial_consecutive_equivalence_streak"] < 1
            or len(comparisons) != 1
            or same_as_baseline[0]
            or not equivalent[0]
        ):
            invalid(
                "requires one equal comparison whose source or "
                "configuration revision changed"
            )
        return

    if (
        inputs["initial_consecutive_equivalence_streak"] != 0
        or len(comparisons) != 2
        or not all(same_as_baseline)
        or equivalent != [True, False]
    ):
        invalid(
            "requires equal then mismatched same-revision "
            "comparisons from a zero streak"
        )


def validate_authority_shadow_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed SHADOW_VALIDATE comparison vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "initial_consecutive_equivalence_streak",
            "baseline_source_revision",
            "baseline_configuration_revision",
            "comparisons",
        },
        path="inputs",
    )

    raw_comparisons = _require_list(
        inputs["comparisons"],
        "inputs.comparisons",
    )
    if not raw_comparisons:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.comparisons must be non-empty",
        )

    comparisons = [
        _validate_comparison(
            comparison,
            f"inputs.comparisons[{index}]",
        )
        for index, comparison in enumerate(raw_comparisons)
    ]
    comparison_ids = [
        comparison["comparison_id"]
        for comparison in comparisons
    ]
    if len(comparison_ids) != len(set(comparison_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.comparisons contains duplicate comparison_id values",
        )

    normalized = {
        "initial_consecutive_equivalence_streak": (
            require_nonnegative_int(
                inputs["initial_consecutive_equivalence_streak"],
                "inputs.initial_consecutive_equivalence_streak",
            )
        ),
        "baseline_source_revision": require_string(
            inputs["baseline_source_revision"],
            "inputs.baseline_source_revision",
        ),
        "baseline_configuration_revision": require_string(
            inputs["baseline_configuration_revision"],
            "inputs.baseline_configuration_revision",
        ),
        "comparisons": comparisons,
    }
    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def _validate_output_row(
    value: Any,
    path: str,
) -> dict[str, Any]:
    row = require_mapping(value, path)
    require_exact_fields(
        row,
        {
            "comparison_id",
            "source_revision",
            "configuration_revision",
            "values_equivalent",
            "revision_changed",
            "disposition",
            "consecutive_equivalence_streak",
        },
        path=path,
    )
    disposition = require_string(
        row["disposition"],
        f"{path}.disposition",
    )
    if disposition not in _DISPOSITIONS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path}.disposition unsupported={disposition}",
        )

    return {
        "comparison_id": require_string(
            row["comparison_id"],
            f"{path}.comparison_id",
        ),
        "source_revision": require_string(
            row["source_revision"],
            f"{path}.source_revision",
        ),
        "configuration_revision": require_string(
            row["configuration_revision"],
            f"{path}.configuration_revision",
        ),
        "values_equivalent": require_bool(
            row["values_equivalent"],
            f"{path}.values_equivalent",
        ),
        "revision_changed": require_bool(
            row["revision_changed"],
            f"{path}.revision_changed",
        ),
        "disposition": disposition,
        "consecutive_equivalence_streak": require_nonnegative_int(
            row["consecutive_equivalence_streak"],
            f"{path}.consecutive_equivalence_streak",
        ),
    }


def validate_authority_shadow_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable SHADOW_VALIDATE output evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "phase",
            "live_writer",
            "comparison_rows",
            "final_consecutive_equivalence_streak",
            "legacy_writes",
            "peerhub_operational_state_mutations",
            "provider_calls",
        },
        path="output",
    )

    phase = require_string(output["phase"], "output.phase")
    if phase != "SHADOW_VALIDATE":
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.phase unsupported={phase}",
        )

    live_writer = require_string(
        output["live_writer"],
        "output.live_writer",
    )
    if live_writer != "ENGRAM":
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.live_writer unsupported={live_writer}",
        )

    raw_rows = _require_list(
        output["comparison_rows"],
        "output.comparison_rows",
    )
    if not raw_rows:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.comparison_rows must be non-empty",
        )

    rows = [
        _validate_output_row(row, f"output.comparison_rows[{index}]")
        for index, row in enumerate(raw_rows)
    ]
    row_ids = [row["comparison_id"] for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.comparison_rows contains duplicate comparison_id values",
        )

    return {
        "phase": phase,
        "live_writer": live_writer,
        "comparison_rows": rows,
        "final_consecutive_equivalence_streak": (
            require_nonnegative_int(
                output["final_consecutive_equivalence_streak"],
                "output.final_consecutive_equivalence_streak",
            )
        ),
        "legacy_writes": require_nonnegative_int(
            output["legacy_writes"],
            "output.legacy_writes",
        ),
        "peerhub_operational_state_mutations": (
            require_nonnegative_int(
                output["peerhub_operational_state_mutations"],
                "output.peerhub_operational_state_mutations",
            )
        ),
        "provider_calls": require_nonnegative_int(
            output["provider_calls"],
            "output.provider_calls",
        ),
    }


class _ShadowComparisonEngine:
    """Pure comparison engine over injected snapshot facts only."""

    def _run(
        self,
        raw_inputs: Mapping[str, Any],
        *,
        fault: str | None = None,
    ) -> dict[str, Any]:
        active_revision = (
            raw_inputs["baseline_source_revision"],
            raw_inputs["baseline_configuration_revision"],
        )
        streak = raw_inputs["initial_consecutive_equivalence_streak"]
        rows: list[dict[str, Any]] = []

        for comparison in raw_inputs["comparisons"]:
            revision = (
                comparison["source_revision"],
                comparison["configuration_revision"],
            )
            revision_changed = revision != active_revision
            values_equivalent = (
                comparison["candidate_value"]
                == comparison["legacy_value"]
            )

            if fault == "restart_same_revision_streak":
                if values_equivalent and not revision_changed:
                    streak = 1
                    disposition = "SAME_REVISION_EQUIVALENT"
                elif revision_changed:
                    streak = 1 if values_equivalent else 0
                    disposition = "REVISION_CHANGED_RESET"
                else:
                    streak = 0
                    disposition = "SAME_REVISION_VALUE_DRIFT"
            elif fault == "accept_same_revision_value_drift":
                if not values_equivalent and not revision_changed:
                    streak += 1
                    disposition = "SAME_REVISION_EQUIVALENT"
                elif revision_changed:
                    streak = 1 if values_equivalent else 0
                    disposition = "REVISION_CHANGED_RESET"
                elif values_equivalent:
                    streak += 1
                    disposition = "SAME_REVISION_EQUIVALENT"
                else:
                    streak = 0
                    disposition = "SAME_REVISION_VALUE_DRIFT"
            elif fault == "pool_revision_change_streak":
                if revision_changed and values_equivalent:
                    revision_changed = False
                    streak += 1
                    disposition = "SAME_REVISION_EQUIVALENT"
                elif revision_changed:
                    streak = 0
                    disposition = "REVISION_CHANGED_RESET"
                elif values_equivalent:
                    streak += 1
                    disposition = "SAME_REVISION_EQUIVALENT"
                else:
                    streak = 0
                    disposition = "SAME_REVISION_VALUE_DRIFT"
            elif revision_changed:
                streak = 1 if values_equivalent else 0
                disposition = "REVISION_CHANGED_RESET"
            elif values_equivalent:
                streak += 1
                disposition = "SAME_REVISION_EQUIVALENT"
            else:
                streak = 0
                disposition = "SAME_REVISION_VALUE_DRIFT"

            rows.append(
                {
                    "comparison_id": comparison["comparison_id"],
                    "source_revision": comparison["source_revision"],
                    "configuration_revision": (
                        comparison["configuration_revision"]
                    ),
                    "values_equivalent": values_equivalent,
                    "revision_changed": revision_changed,
                    "disposition": disposition,
                    "consecutive_equivalence_streak": streak,
                }
            )
            active_revision = revision

        return {
            "phase": "SHADOW_VALIDATE",
            "live_writer": "ENGRAM",
            "comparison_rows": rows,
            "final_consecutive_equivalence_streak": streak,
            "legacy_writes": 0,
            "peerhub_operational_state_mutations": (
                1 if fault == "mutate_peerhub_operational_state" else 0
            ),
            "provider_calls": 0,
        }


class AuthorityShadowOracle(_ShadowComparisonEngine):
    """Pure shadow-validation oracle."""

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
        return self._run(raw_inputs)


class AuthorityShadowSubjectAdapter(_ShadowComparisonEngine):
    """Reference adapter over injected read-only comparison facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"authority_shadow.{label}.reference"
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
        return self._run(raw_inputs)


class FaultInjectedAuthorityShadowAdapter(_ShadowComparisonEngine):
    """One concrete shadow-authority defect for one negative fixture."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str, fault: str) -> None:
        self._base = base_fixture_id
        self._fixture_id = f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        self._fault = fault
        self.adapter_id = f"authority_shadow.{base_fixture_id.lower().replace('-', '')}.{fault}"
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
        return self._run(raw_inputs, fault=self._fault)


def authority_shadow_registrations() -> tuple[DomainRegistration, ...]:
    """Return all AC-03 shadow-validation fixture registrations."""

    registrations: list[DomainRegistration] = []
    faults = {
        "AC-03-01": "restart_same_revision_streak",
        "AC-03-02": "accept_same_revision_value_drift",
        "AC-03-03": "pool_revision_change_streak",
        "AC-03-04": "mutate_peerhub_operational_state",
    }

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityShadowOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=AuthorityShadowSubjectAdapter(base_fixture_id),
                input_validator=validate_authority_shadow_inputs,
                output_validator=validate_authority_shadow_output,
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=FaultInjectedAuthorityShadowAdapter(
                    base_fixture_id,
                    faults[base_fixture_id],
                ),
                input_validator=validate_authority_shadow_inputs,
                output_validator=validate_authority_shadow_output,
            )
        )

    return tuple(registrations)
