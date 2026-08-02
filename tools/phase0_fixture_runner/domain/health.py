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
    "HR-04",
    "HR-05",
    "HR-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "HR-04": "health.hr04.authority_clearance",
    "HR-05": "health.hr05.one_probe_grant",
    "HR-06": "health.hr06.cas_probe_transition",
}

_AUTHORITY_CLASSES = frozenset(
    {
        "AUTOMATIC",
        "MANUAL",
        "SECURITY",
        "POLICY",
    }
)
_CIRCUIT_STATES = frozenset(
    {
        "CIRCUIT_OPEN",
        "CIRCUIT_CLOSED",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "incident",
        "gate_generation",
        "timestamp",
        "fingerprint",
    }
)
_PROBE_IDENTITY_FIELDS = frozenset(
    {
        "revision",
        "incident",
        "gate_generation",
        "timestamp",
        "fingerprint",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
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


def _validate_receipt(
    value: Any,
    path: str,
) -> dict[str, Any]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        _RECEIPT_FIELDS,
        path=path,
    )

    return {
        "incident": require_string(
            receipt["incident"],
            f"{path}.incident",
        ),
        "gate_generation": require_nonnegative_int(
            receipt["gate_generation"],
            f"{path}.gate_generation",
        ),
        "timestamp": require_nonnegative_int(
            receipt["timestamp"],
            f"{path}.timestamp",
        ),
        "fingerprint": require_string(
            receipt["fingerprint"],
            f"{path}.fingerprint",
        ),
    }


def _validate_probe_identity(
    value: Any,
    path: str,
) -> dict[str, Any]:
    identity = require_mapping(value, path)
    require_exact_fields(
        identity,
        _PROBE_IDENTITY_FIELDS,
        path=path,
    )

    return {
        "revision": require_nonnegative_int(
            identity["revision"],
            f"{path}.revision",
        ),
        "incident": require_string(
            identity["incident"],
            f"{path}.incident",
        ),
        "gate_generation": require_nonnegative_int(
            identity["gate_generation"],
            f"{path}.gate_generation",
        ),
        "timestamp": require_nonnegative_int(
            identity["timestamp"],
            f"{path}.timestamp",
        ),
        "fingerprint": require_string(
            identity["fingerprint"],
            f"{path}.fingerprint",
        ),
    }


def validate_health_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed health input schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")

    if base == "HR-04":
        require_exact_fields(
            inputs,
            {
                "clearance_receipt",
                "current",
                "quarantine_authority_class",
            },
            path="inputs",
        )

        authority = require_string(
            inputs["quarantine_authority_class"],
            "inputs.quarantine_authority_class",
        )
        if authority not in _AUTHORITY_CLASSES:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.quarantine_authority_class "
                    f"unsupported={authority}"
                ),
            )

        return {
            "clearance_receipt": _validate_receipt(
                inputs["clearance_receipt"],
                "inputs.clearance_receipt",
            ),
            "current": _validate_receipt(
                inputs["current"],
                "inputs.current",
            ),
            "quarantine_authority_class": authority,
        }

    if base == "HR-05":
        require_exact_fields(
            inputs,
            {
                "grant_remaining_probes",
                "probe_attempts",
                "current_health_value",
                "current_gate_state",
                "verified_probe_receipt_applied",
            },
            path="inputs",
        )

        remaining = require_nonnegative_int(
            inputs["grant_remaining_probes"],
            "inputs.grant_remaining_probes",
        )
        if remaining != 1:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.grant_remaining_probes "
                    "must equal 1"
                ),
            )

        attempts = inputs["probe_attempts"]
        if (
            not isinstance(attempts, list)
            or len(attempts) != 2
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.probe_attempts must "
                    "contain exactly two attempts"
                ),
            )

        validated_attempts: list[
            dict[str, str]
        ] = []
        seen: set[str] = set()

        for index, raw_attempt in enumerate(
            attempts
        ):
            path = (
                f"inputs.probe_attempts[{index}]"
            )
            attempt = require_mapping(
                raw_attempt,
                path,
            )
            require_exact_fields(
                attempt,
                {"attempt_id"},
                path=path,
            )

            attempt_id = require_string(
                attempt["attempt_id"],
                f"{path}.attempt_id",
            )
            if attempt_id in seen:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        "duplicate probe "
                        f"attempt_id={attempt_id}"
                    ),
                )

            seen.add(attempt_id)
            validated_attempts.append(
                {
                    "attempt_id": attempt_id,
                }
            )

        receipt_applied = require_bool(
            inputs[
                "verified_probe_receipt_applied"
            ],
            (
                "inputs."
                "verified_probe_receipt_applied"
            ),
        )
        if receipt_applied:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "HR-05 verifies attempts before "
                    "a separately verified probe "
                    "receipt is applied"
                ),
            )

        return {
            "grant_remaining_probes": remaining,
            "probe_attempts": validated_attempts,
            "current_health_value": (
                require_string(
                    inputs["current_health_value"],
                    (
                        "inputs."
                        "current_health_value"
                    ),
                )
            ),
            "current_gate_state": require_string(
                inputs["current_gate_state"],
                "inputs.current_gate_state",
            ),
            "verified_probe_receipt_applied": (
                receipt_applied
            ),
        }

    require_exact_fields(
        inputs,
        {
            "probe_result",
            "reported",
            "current",
        },
        path="inputs",
    )

    probe_result = require_string(
        inputs["probe_result"],
        "inputs.probe_result",
    )
    if probe_result not in {
        "SUCCESS",
        "FAILURE",
    }:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.probe_result "
                f"unsupported={probe_result}"
            ),
        )

    current_raw = require_mapping(
        inputs["current"],
        "inputs.current",
    )
    require_exact_fields(
        current_raw,
        _PROBE_IDENTITY_FIELDS
        | {
            "backoff_count",
            "circuit_state",
        },
        path="inputs.current",
    )

    current_identity = (
        _validate_probe_identity(
            {
                key: current_raw[key]
                for key
                in _PROBE_IDENTITY_FIELDS
            },
            "inputs.current",
        )
    )

    circuit_state = require_string(
        current_raw["circuit_state"],
        "inputs.current.circuit_state",
    )
    if circuit_state not in _CIRCUIT_STATES:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.current.circuit_state "
                f"unsupported={circuit_state}"
            ),
        )

    return {
        "probe_result": probe_result,
        "reported": _validate_probe_identity(
            inputs["reported"],
            "inputs.reported",
        ),
        "current": {
            **current_identity,
            "backoff_count": (
                require_nonnegative_int(
                    current_raw["backoff_count"],
                    (
                        "inputs.current."
                        "backoff_count"
                    ),
                )
            ),
            "circuit_state": circuit_state,
        },
    }


def _validate_optional_reason(
    value: Any,
    allowed: frozenset[str],
    path: str,
) -> str | None:
    if value is None:
        return None

    reason = require_string(value, path)
    if reason not in allowed:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} unsupported={reason}",
        )

    return reason


def validate_health_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject health output."""

    base = _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "output",
    )

    if base == "HR-04":
        require_exact_fields(
            output,
            {
                "circuit_state",
                "clearance_applied",
                "reason",
            },
            path="output",
        )

        state = require_string(
            output["circuit_state"],
            "output.circuit_state",
        )
        if state not in _CIRCUIT_STATES:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.circuit_state "
                    f"unsupported={state}"
                ),
            )

        return {
            "circuit_state": state,
            "clearance_applied": require_bool(
                output["clearance_applied"],
                "output.clearance_applied",
            ),
            "reason": _validate_optional_reason(
                output["reason"],
                frozenset(
                    {
                        (
                            "AUTOMATIC_"
                            "CLEARANCE_APPLIED"
                        ),
                        (
                            "AUTOMATIC_CLEARANCE_"
                            "RECEIPT_MISMATCH"
                        ),
                        (
                            "QUARANTINE_AUTHORITY_"
                            "INSUFFICIENT"
                        ),
                    }
                ),
                "output.reason",
            ),
        }

    if base == "HR-05":
        require_exact_fields(
            output,
            {
                "probe_decisions",
                "remaining_probes",
                "health_value_before",
                "health_value_after",
                "gate_state_before",
                "gate_state_after",
                "state_unchanged",
                (
                    "verified_probe_"
                    "receipt_applied"
                ),
            },
            path="output",
        )

        decisions = output["probe_decisions"]
        if (
            not isinstance(decisions, list)
            or len(decisions) != 2
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.probe_decisions "
                    "must contain exactly two rows"
                ),
            )

        validated_decisions: list[
            dict[str, Any]
        ] = []
        seen: set[str] = set()

        for index, raw_decision in enumerate(
            decisions
        ):
            path = (
                f"output.probe_decisions[{index}]"
            )
            decision = require_mapping(
                raw_decision,
                path,
            )
            require_exact_fields(
                decision,
                {
                    "attempt_id",
                    "disposition",
                    "reason",
                },
                path=path,
            )

            attempt_id = require_string(
                decision["attempt_id"],
                f"{path}.attempt_id",
            )
            if attempt_id in seen:
                raise DomainContractError(
                    "DOMAIN_OUTPUT_INVALID",
                    (
                        "duplicate probe decision "
                        f"attempt_id={attempt_id}"
                    ),
                )

            seen.add(attempt_id)
            disposition = require_string(
                decision["disposition"],
                f"{path}.disposition",
            )
            if disposition not in {
                "EXECUTED",
                "REJECTED",
            }:
                raise DomainContractError(
                    "DOMAIN_OUTPUT_INVALID",
                    (
                        f"{path}.disposition "
                        f"unsupported={disposition}"
                    ),
                )

            validated_decisions.append(
                {
                    "attempt_id": attempt_id,
                    "disposition": disposition,
                    "reason": (
                        _validate_optional_reason(
                            decision["reason"],
                            frozenset(
                                {
                                    (
                                        "PROBE_GRANT_"
                                        "EXHAUSTED"
                                    )
                                }
                            ),
                            f"{path}.reason",
                        )
                    ),
                }
            )

        return {
            "probe_decisions": (
                validated_decisions
            ),
            "remaining_probes": (
                require_nonnegative_int(
                    output["remaining_probes"],
                    "output.remaining_probes",
                )
            ),
            "health_value_before": (
                require_string(
                    output[
                        "health_value_before"
                    ],
                    (
                        "output."
                        "health_value_before"
                    ),
                )
            ),
            "health_value_after": (
                require_string(
                    output["health_value_after"],
                    (
                        "output."
                        "health_value_after"
                    ),
                )
            ),
            "gate_state_before": (
                require_string(
                    output["gate_state_before"],
                    "output.gate_state_before",
                )
            ),
            "gate_state_after": require_string(
                output["gate_state_after"],
                "output.gate_state_after",
            ),
            "state_unchanged": require_bool(
                output["state_unchanged"],
                "output.state_unchanged",
            ),
            (
                "verified_probe_"
                "receipt_applied"
            ): require_bool(
                output[
                    "verified_probe_"
                    "receipt_applied"
                ],
                (
                    "output.verified_probe_"
                    "receipt_applied"
                ),
            ),
        }

    require_exact_fields(
        output,
        {
            "circuit_state_before",
            "circuit_state_after",
            "backoff_count_before",
            "backoff_count_after",
            "reported_matches_current",
            "transition",
        },
        path="output",
    )

    state_before = require_string(
        output["circuit_state_before"],
        "output.circuit_state_before",
    )
    state_after = require_string(
        output["circuit_state_after"],
        "output.circuit_state_after",
    )

    for path, state in (
        (
            "output.circuit_state_before",
            state_before,
        ),
        (
            "output.circuit_state_after",
            state_after,
        ),
    ):
        if state not in _CIRCUIT_STATES:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                f"{path} unsupported={state}",
            )

    transition = require_string(
        output["transition"],
        "output.transition",
    )
    if transition not in {
        "FAILURE_BACKOFF_INCREMENTED",
        "SUCCESS_CIRCUIT_CLOSED",
        "STALE_PROBE_NO_OP",
    }:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.transition "
                f"unsupported={transition}"
            ),
        )

    return {
        "circuit_state_before": state_before,
        "circuit_state_after": state_after,
        "backoff_count_before": (
            require_nonnegative_int(
                output["backoff_count_before"],
                "output.backoff_count_before",
            )
        ),
        "backoff_count_after": (
            require_nonnegative_int(
                output["backoff_count_after"],
                "output.backoff_count_after",
            )
        ),
        "reported_matches_current": (
            require_bool(
                output[
                    "reported_matches_current"
                ],
                (
                    "output."
                    "reported_matches_current"
                ),
            )
        ),
        "transition": transition,
    }


class HealthOracle:
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

        if self._base == "HR-04":
            return self._hr04(raw_inputs)
        if self._base == "HR-05":
            return self._hr05(raw_inputs)

        return self._hr06(raw_inputs)

    def _hr04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = inputs["clearance_receipt"]
        current = inputs["current"]
        matches = all(
            receipt[field] == current[field]
            for field in _RECEIPT_FIELDS
        )

        if (
            inputs[
                "quarantine_authority_class"
            ]
            != "AUTOMATIC"
        ):
            return {
                "circuit_state": (
                    "CIRCUIT_OPEN"
                ),
                "clearance_applied": False,
                "reason": (
                    "QUARANTINE_AUTHORITY_"
                    "INSUFFICIENT"
                ),
            }

        if matches:
            return {
                "circuit_state": (
                    "CIRCUIT_CLOSED"
                ),
                "clearance_applied": True,
                "reason": (
                    "AUTOMATIC_"
                    "CLEARANCE_APPLIED"
                ),
            }

        return {
            "circuit_state": "CIRCUIT_OPEN",
            "clearance_applied": False,
            "reason": (
                "AUTOMATIC_CLEARANCE_"
                "RECEIPT_MISMATCH"
            ),
        }

    def _hr05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        remaining = inputs[
            "grant_remaining_probes"
        ]
        decisions: list[
            dict[str, Any]
        ] = []

        for attempt in inputs["probe_attempts"]:
            if remaining > 0:
                remaining -= 1
                disposition = "EXECUTED"
                reason = None
            else:
                disposition = "REJECTED"
                reason = (
                    "PROBE_GRANT_EXHAUSTED"
                )

            decisions.append(
                {
                    "attempt_id": (
                        attempt["attempt_id"]
                    ),
                    "disposition": disposition,
                    "reason": reason,
                }
            )

        return {
            "probe_decisions": decisions,
            "remaining_probes": remaining,
            "health_value_before": (
                inputs["current_health_value"]
            ),
            "health_value_after": (
                inputs["current_health_value"]
            ),
            "gate_state_before": (
                inputs["current_gate_state"]
            ),
            "gate_state_after": (
                inputs["current_gate_state"]
            ),
            "state_unchanged": True,
            "verified_probe_receipt_applied": (
                False
            ),
        }

    def _hr06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        reported = inputs["reported"]
        current = inputs["current"]
        matches = all(
            reported[field] == current[field]
            for field
            in _PROBE_IDENTITY_FIELDS
        )

        state_before = current[
            "circuit_state"
        ]
        backoff_before = current[
            "backoff_count"
        ]

        if inputs["probe_result"] == "FAILURE":
            state_after = "CIRCUIT_OPEN"
            backoff_after = (
                backoff_before + 1
            )
            transition = (
                "FAILURE_BACKOFF_INCREMENTED"
            )
        elif matches:
            state_after = "CIRCUIT_CLOSED"
            backoff_after = backoff_before
            transition = (
                "SUCCESS_CIRCUIT_CLOSED"
            )
        else:
            state_after = state_before
            backoff_after = backoff_before
            transition = "STALE_PROBE_NO_OP"

        return {
            "circuit_state_before": (
                state_before
            ),
            "circuit_state_after": (
                state_after
            ),
            "backoff_count_before": (
                backoff_before
            ),
            "backoff_count_after": (
                backoff_after
            ),
            "reported_matches_current": (
                matches
            ),
            "transition": transition,
        }


class HealthSubjectAdapter:
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
            f"health.{label}.reference"
        )
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
            }
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

        if self._base == "HR-04":
            return self._hr04(raw_inputs)
        if self._base == "HR-05":
            return self._hr05(raw_inputs)

        return self._hr06(raw_inputs)

    def _hr04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = inputs["clearance_receipt"]
        current = inputs["current"]

        same_incident = (
            receipt["incident"]
            == current["incident"]
        )
        same_generation = (
            receipt["gate_generation"]
            == current["gate_generation"]
        )
        same_timestamp = (
            receipt["timestamp"]
            == current["timestamp"]
        )
        same_fingerprint = (
            receipt["fingerprint"]
            == current["fingerprint"]
        )

        if (
            inputs[
                "quarantine_authority_class"
            ]
            != "AUTOMATIC"
        ):
            state = "CIRCUIT_OPEN"
            applied = False
            reason = (
                "QUARANTINE_AUTHORITY_"
                "INSUFFICIENT"
            )
        elif all(
            (
                same_incident,
                same_generation,
                same_timestamp,
                same_fingerprint,
            )
        ):
            state = "CIRCUIT_CLOSED"
            applied = True
            reason = (
                "AUTOMATIC_"
                "CLEARANCE_APPLIED"
            )
        else:
            state = "CIRCUIT_OPEN"
            applied = False
            reason = (
                "AUTOMATIC_CLEARANCE_"
                "RECEIPT_MISMATCH"
            )

        return {
            "circuit_state": state,
            "clearance_applied": applied,
            "reason": reason,
        }

    def _hr05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        available = inputs[
            "grant_remaining_probes"
        ]
        decisions: list[
            dict[str, Any]
        ] = []
        grant_consumed = False

        for attempt in inputs["probe_attempts"]:
            allowed = (
                available == 1
                and not grant_consumed
            )
            if allowed:
                grant_consumed = True
                available = 0

            decisions.append(
                {
                    "attempt_id": (
                        attempt["attempt_id"]
                    ),
                    "disposition": (
                        "EXECUTED"
                        if allowed
                        else "REJECTED"
                    ),
                    "reason": (
                        None
                        if allowed
                        else (
                            "PROBE_GRANT_"
                            "EXHAUSTED"
                        )
                    ),
                }
            )

        health_before = inputs[
            "current_health_value"
        ]
        gate_before = inputs[
            "current_gate_state"
        ]

        return {
            "probe_decisions": decisions,
            "remaining_probes": available,
            "health_value_before": (
                health_before
            ),
            "health_value_after": (
                health_before
            ),
            "gate_state_before": (
                gate_before
            ),
            "gate_state_after": gate_before,
            "state_unchanged": True,
            "verified_probe_receipt_applied": (
                False
            ),
        }

    def _hr06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        reported = inputs["reported"]
        current = inputs["current"]

        matches = (
            reported["revision"]
            == current["revision"]
            and reported["incident"]
            == current["incident"]
            and reported["gate_generation"]
            == current["gate_generation"]
            and reported["timestamp"]
            == current["timestamp"]
            and reported["fingerprint"]
            == current["fingerprint"]
        )

        before_state = current[
            "circuit_state"
        ]
        before_backoff = current[
            "backoff_count"
        ]

        if inputs["probe_result"] == "FAILURE":
            after_state = "CIRCUIT_OPEN"
            after_backoff = before_backoff + 1
            transition = (
                "FAILURE_BACKOFF_INCREMENTED"
            )
        elif matches:
            after_state = "CIRCUIT_CLOSED"
            after_backoff = before_backoff
            transition = (
                "SUCCESS_CIRCUIT_CLOSED"
            )
        else:
            after_state = before_state
            after_backoff = before_backoff
            transition = "STALE_PROBE_NO_OP"

        return {
            "circuit_state_before": (
                before_state
            ),
            "circuit_state_after": (
                after_state
            ),
            "backoff_count_before": (
                before_backoff
            ),
            "backoff_count_after": (
                after_backoff
            ),
            "reported_matches_current": (
                matches
            ),
            "transition": transition,
        }


class FaultInjectedHealthAdapter:
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
            f"health.{label}.fault_injected"
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

        expected_fixture_id = (
            f"{self._base}{_NEGATIVE_SUFFIX}"
        )
        if fixture_id != expected_fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "HR-04":
            return self._hr04(raw_inputs)
        if self._base == "HR-05":
            return self._hr05(raw_inputs)

        return self._hr06(raw_inputs)

    def _hr04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = inputs["clearance_receipt"]
        current = inputs["current"]

        matches = (
            receipt["incident"]
            == current["incident"]
            and receipt["gate_generation"]
            == current["gate_generation"]
            and receipt["timestamp"]
            == current["timestamp"]
            and receipt["fingerprint"]
            == current["fingerprint"]
        )

        # Fault: receipt equality is incorrectly
        # treated as sufficient authority.
        if matches:
            return {
                "circuit_state": (
                    "CIRCUIT_CLOSED"
                ),
                "clearance_applied": True,
                "reason": (
                    "AUTOMATIC_"
                    "CLEARANCE_APPLIED"
                ),
            }

        return {
            "circuit_state": "CIRCUIT_OPEN",
            "clearance_applied": False,
            "reason": (
                "AUTOMATIC_CLEARANCE_"
                "RECEIPT_MISMATCH"
            ),
        }

    def _hr05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Fault: the grant is checked but never
        # consumed, so both attempts execute.
        decisions = [
            {
                "attempt_id": (
                    attempt["attempt_id"]
                ),
                "disposition": "EXECUTED",
                "reason": None,
            }
            for attempt
            in inputs["probe_attempts"]
        ]

        health = inputs[
            "current_health_value"
        ]
        gate = inputs[
            "current_gate_state"
        ]

        return {
            "probe_decisions": decisions,
            "remaining_probes": 0,
            "health_value_before": health,
            "health_value_after": health,
            "gate_state_before": gate,
            "gate_state_after": gate,
            "state_unchanged": True,
            "verified_probe_receipt_applied": (
                False
            ),
        }

    def _hr06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        reported = inputs["reported"]
        current = inputs["current"]

        matches = all(
            reported[field] == current[field]
            for field
            in _PROBE_IDENTITY_FIELDS
        )
        before_state = current[
            "circuit_state"
        ]
        before_backoff = current[
            "backoff_count"
        ]

        # Fault: a stale successful probe forces
        # the circuit open instead of being an
        # exact no-op.
        if (
            inputs["probe_result"] == "SUCCESS"
            and not matches
        ):
            after_state = "CIRCUIT_OPEN"
            after_backoff = before_backoff
            transition = "STALE_PROBE_NO_OP"
        elif (
            inputs["probe_result"]
            == "FAILURE"
        ):
            after_state = "CIRCUIT_OPEN"
            after_backoff = before_backoff + 1
            transition = (
                "FAILURE_BACKOFF_INCREMENTED"
            )
        else:
            after_state = "CIRCUIT_CLOSED"
            after_backoff = before_backoff
            transition = (
                "SUCCESS_CIRCUIT_CLOSED"
            )

        return {
            "circuit_state_before": (
                before_state
            ),
            "circuit_state_after": (
                after_state
            ),
            "backoff_count_before": (
                before_backoff
            ),
            "backoff_count_after": (
                after_backoff
            ),
            "reported_matches_current": (
                matches
            ),
            "transition": transition,
        }


def health_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in HR registry rows."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = HealthOracle(
            base_fixture_id
        )
        positive_adapter = (
            HealthSubjectAdapter(
                base_fixture_id
            )
        )
        negative_adapter = (
            FaultInjectedHealthAdapter(
                base_fixture_id
            )
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=(
                    validate_health_inputs
                ),
                output_validator=(
                    validate_health_output
                ),
            )
        )

        negative_fixture_id = (
            f"{base_fixture_id}"
            f"{_NEGATIVE_SUFFIX}"
        )
        registrations.append(
            DomainRegistration(
                fixture_id=negative_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=negative_adapter,
                input_validator=(
                    validate_health_inputs
                ),
                output_validator=(
                    validate_health_output
                ),
            )
        )

    return tuple(registrations)