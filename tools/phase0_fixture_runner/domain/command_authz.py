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
    "CJ-02",
    "CJ-05",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "CJ-02": "command_authz.cj02.valid_admission",
    "CJ-05": (
        "command_authz.cj05."
        "authorization_before_effects"
    ),
}

_REJECTION_REASONS = frozenset(
    {
        "ACTOR_UNAUTHORIZED",
        "ADMISSION_REVISION_MISMATCH",
    }
)

_EFFECT_CERTAINTIES = frozenset(
    {
        "NOT_STARTED",
        "MAY_HAVE_STARTED",
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


def _validate_envelope(
    value: Any,
    path: str,
) -> dict[str, Any]:
    envelope = require_mapping(value, path)
    require_exact_fields(
        envelope,
        {
            "actor_identity",
            "client_request_key",
            "expected_configuration_revision",
            "expected_policy_revision",
            "workspace_scope",
        },
        path=path,
    )
    return {
        "actor_identity": require_string(
            envelope["actor_identity"],
            f"{path}.actor_identity",
        ),
        "client_request_key": require_string(
            envelope["client_request_key"],
            f"{path}.client_request_key",
        ),
        "expected_configuration_revision": (
            require_nonnegative_int(
                envelope[
                    "expected_configuration_revision"
                ],
                (
                    f"{path}."
                    "expected_configuration_revision"
                ),
            )
        ),
        "expected_policy_revision": (
            require_nonnegative_int(
                envelope["expected_policy_revision"],
                f"{path}.expected_policy_revision",
            )
        ),
        "workspace_scope": require_string(
            envelope["workspace_scope"],
            f"{path}.workspace_scope",
        ),
    }


def _validate_authorization(
    value: Any,
    path: str,
) -> dict[str, Any]:
    authorization = require_mapping(value, path)
    require_exact_fields(
        authorization,
        {"actor_authorized"},
        path=path,
    )
    return {
        "actor_authorized": require_bool(
            authorization["actor_authorized"],
            f"{path}.actor_authorized",
        )
    }


def _validate_current_revisions(
    value: Any,
    path: str,
) -> dict[str, Any]:
    revisions = require_mapping(value, path)
    require_exact_fields(
        revisions,
        {
            "current_configuration_revision",
            "current_policy_revision",
        },
        path=path,
    )
    return {
        "current_configuration_revision": (
            require_nonnegative_int(
                revisions[
                    "current_configuration_revision"
                ],
                (
                    f"{path}."
                    "current_configuration_revision"
                ),
            )
        ),
        "current_policy_revision": (
            require_nonnegative_int(
                revisions["current_policy_revision"],
                f"{path}.current_policy_revision",
            )
        ),
    }


def _revisions_match(
    inputs: Mapping[str, Any],
) -> bool:
    envelope = inputs["envelope"]
    current = inputs["current_revisions"]
    return bool(
        envelope["expected_configuration_revision"]
        == current["current_configuration_revision"]
        and envelope["expected_policy_revision"]
        == current["current_policy_revision"]
    )


def validate_command_authz_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed command-admission input schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "envelope",
            "authorization",
            "current_revisions",
            "injected_command_id",
        },
        path="inputs",
    )

    validated = {
        "envelope": _validate_envelope(
            inputs["envelope"],
            "inputs.envelope",
        ),
        "authorization": _validate_authorization(
            inputs["authorization"],
            "inputs.authorization",
        ),
        "current_revisions": (
            _validate_current_revisions(
                inputs["current_revisions"],
                "inputs.current_revisions",
            )
        ),
        "injected_command_id": require_string(
            inputs["injected_command_id"],
            "inputs.injected_command_id",
        ),
    }

    authorized = validated["authorization"][
        "actor_authorized"
    ]
    revisions_match = _revisions_match(validated)

    if base == "CJ-02" and not (
        authorized and revisions_match
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "CJ-02 requires an authorized actor and "
                "matching admission revisions"
            ),
        )

    if base == "CJ-05" and (
        authorized and revisions_match
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "CJ-05 requires either missing actor "
                "authority or an admission revision "
                "mismatch"
            ),
        )

    return validated


def _require_literal(
    value: Any,
    expected: str,
    path: str,
) -> str:
    actual = require_string(value, path)
    if actual != expected:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} expected={expected};received={actual}",
        )
    return actual


def _require_one_of(
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


def _require_optional_command_id(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def validate_command_authz_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject command-admission output."""

    base = _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")

    if base == "CJ-02":
        require_exact_fields(
            output,
            {
                "status",
                "command_id",
                "actor_identity",
                "client_request_key",
                "workspace_scope",
                "zero_provider_calls",
                "zero_dispatch_calls",
            },
            path="output",
        )
        return {
            "status": _require_literal(
                output["status"],
                "ADMITTED",
                "output.status",
            ),
            "command_id": require_string(
                output["command_id"],
                "output.command_id",
            ),
            "actor_identity": require_string(
                output["actor_identity"],
                "output.actor_identity",
            ),
            "client_request_key": require_string(
                output["client_request_key"],
                "output.client_request_key",
            ),
            "workspace_scope": require_string(
                output["workspace_scope"],
                "output.workspace_scope",
            ),
            "zero_provider_calls": require_bool(
                output["zero_provider_calls"],
                "output.zero_provider_calls",
            ),
            "zero_dispatch_calls": require_bool(
                output["zero_dispatch_calls"],
                "output.zero_dispatch_calls",
            ),
        }

    require_exact_fields(
        output,
        {
            "status",
            "reason",
            "exit_code",
            "effect_certainty",
            "retryable",
            "command_id",
            "zero_state_mutations",
            "zero_receipt_writes",
            "zero_outbox_writes",
            "zero_provider_calls",
            "zero_dispatch_calls",
        },
        path="output",
    )

    reason = require_string(
        output["reason"],
        "output.reason",
    )
    if reason not in _REJECTION_REASONS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.reason unsupported={reason}",
        )

    return {
        "status": _require_literal(
            output["status"],
            "REJECTED",
            "output.status",
        ),
        "reason": reason,
        "exit_code": require_nonnegative_int(
            output["exit_code"],
            "output.exit_code",
        ),
        "effect_certainty": _require_one_of(
            output["effect_certainty"],
            _EFFECT_CERTAINTIES,
            "output.effect_certainty",
        ),
        "retryable": require_bool(
            output["retryable"],
            "output.retryable",
        ),
        "command_id": _require_optional_command_id(
            output["command_id"],
            "output.command_id",
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
        "zero_provider_calls": require_bool(
            output["zero_provider_calls"],
            "output.zero_provider_calls",
        ),
        "zero_dispatch_calls": require_bool(
            output["zero_dispatch_calls"],
            "output.zero_dispatch_calls",
        ),
    }


class CommandAuthzOracle:
    """Pure admission oracle for CJ-02 and CJ-05."""

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
                    f"oracle_id={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "CJ-02":
            authorized = raw_inputs[
                "authorization"
            ]["actor_authorized"]
            if not (
                authorized
                and _revisions_match(raw_inputs)
            ):
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    "CJ-02 input is not admissible",
                )

            envelope = raw_inputs["envelope"]
            return {
                "status": "ADMITTED",
                "command_id": raw_inputs[
                    "injected_command_id"
                ],
                "actor_identity": envelope[
                    "actor_identity"
                ],
                "client_request_key": envelope[
                    "client_request_key"
                ],
                "workspace_scope": envelope[
                    "workspace_scope"
                ],
                "zero_provider_calls": True,
                "zero_dispatch_calls": True,
            }

        authorized = raw_inputs["authorization"][
            "actor_authorized"
        ]
        reason = (
            "ACTOR_UNAUTHORIZED"
            if not authorized
            else "ADMISSION_REVISION_MISMATCH"
        )
        return {
            "status": "REJECTED",
            "reason": reason,
            "exit_code": 3,
            "effect_certainty": "NOT_STARTED",
            "retryable": False,
            "command_id": None,
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
            "zero_outbox_writes": True,
            "zero_provider_calls": True,
            "zero_dispatch_calls": True,
        }


class CommandAuthzSubjectAdapter:
    """Deterministic reference command-admission adapter."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"command_authz.{label}.reference"
        )
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
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "CJ-02":
            actor_authorized = raw_inputs[
                "authorization"
            ]["actor_authorized"]
            revisions_current = (
                raw_inputs["envelope"][
                    "expected_configuration_revision"
                ]
                == raw_inputs["current_revisions"][
                    "current_configuration_revision"
                ]
                and raw_inputs["envelope"][
                    "expected_policy_revision"
                ]
                == raw_inputs["current_revisions"][
                    "current_policy_revision"
                ]
            )
            if not (
                actor_authorized
                and revisions_current
            ):
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    "CJ-02 input is not admissible",
                )

            envelope = raw_inputs["envelope"]
            allocated_command_id = raw_inputs[
                "injected_command_id"
            ]
            return {
                "status": "ADMITTED",
                "command_id": allocated_command_id,
                "actor_identity": envelope[
                    "actor_identity"
                ],
                "client_request_key": envelope[
                    "client_request_key"
                ],
                "workspace_scope": envelope[
                    "workspace_scope"
                ],
                "zero_provider_calls": True,
                "zero_dispatch_calls": True,
            }

        actor_authorized = raw_inputs[
            "authorization"
        ]["actor_authorized"]
        if not actor_authorized:
            rejection_reason = "ACTOR_UNAUTHORIZED"
        else:
            configuration_matches = (
                raw_inputs["envelope"][
                    "expected_configuration_revision"
                ]
                == raw_inputs["current_revisions"][
                    "current_configuration_revision"
                ]
            )
            policy_matches = (
                raw_inputs["envelope"][
                    "expected_policy_revision"
                ]
                == raw_inputs["current_revisions"][
                    "current_policy_revision"
                ]
            )
            if configuration_matches and policy_matches:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    "CJ-05 input unexpectedly admissible",
                )
            rejection_reason = (
                "ADMISSION_REVISION_MISMATCH"
            )

        return {
            "status": "REJECTED",
            "reason": rejection_reason,
            "exit_code": 3,
            "effect_certainty": "NOT_STARTED",
            "retryable": False,
            "command_id": None,
            "zero_state_mutations": True,
            "zero_receipt_writes": True,
            "zero_outbox_writes": True,
            "zero_provider_calls": True,
            "zero_dispatch_calls": True,
        }


class FaultInjectedCommandAuthzAdapter:
    """Deliberately incorrect CJ adapters for negative fixtures."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"command_authz.{label}.fault_injected"
        )
        self.fixture_ids = frozenset(
            {f"{base_fixture_id}{_NEGATIVE_SUFFIX}"}
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

        if self._base == "CJ-02":
            envelope = raw_inputs["envelope"]
            return {
                "status": "ADMITTED",
                "command_id": raw_inputs[
                    "injected_command_id"
                ],
                "actor_identity": envelope[
                    "actor_identity"
                ],
                "client_request_key": envelope[
                    "client_request_key"
                ],
                "workspace_scope": (
                    f"{envelope['workspace_scope']}"
                    "-mutated"
                ),
                "zero_provider_calls": True,
                "zero_dispatch_calls": True,
            }

        actor_authorized = raw_inputs[
            "authorization"
        ]["actor_authorized"]
        reason = (
            "ACTOR_UNAUTHORIZED"
            if not actor_authorized
            else "ADMISSION_REVISION_MISMATCH"
        )

        # Fault: the adapter consumes and exposes the command ID
        # and performs writes before applying the admission checks.
        leaked_command_id = raw_inputs[
            "injected_command_id"
        ]
        return {
            "status": "REJECTED",
            "reason": reason,
            "exit_code": 3,
            "effect_certainty": "MAY_HAVE_STARTED",
            "retryable": False,
            "command_id": leaked_command_id,
            "zero_state_mutations": False,
            "zero_receipt_writes": False,
            "zero_outbox_writes": True,
            "zero_provider_calls": True,
            "zero_dispatch_calls": True,
        }


def command_authz_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in CJ registry rows."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = CommandAuthzOracle(base_fixture_id)
        positive_adapter = CommandAuthzSubjectAdapter(
            base_fixture_id
        )
        negative_adapter = (
            FaultInjectedCommandAuthzAdapter(
                base_fixture_id
            )
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=(
                    validate_command_authz_inputs
                ),
                output_validator=(
                    validate_command_authz_output
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
                adapter=negative_adapter,
                input_validator=(
                    validate_command_authz_inputs
                ),
                output_validator=(
                    validate_command_authz_output
                ),
            )
        )

    return tuple(registrations)