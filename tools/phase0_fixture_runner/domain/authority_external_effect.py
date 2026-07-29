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
    "AC-06-01",
    "AC-06-02",
    "AC-06-03",
    "AC-06-04",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-06-01": (
        "authority_external_effect."
        "ac0601.explicit_terminal_receipt"
    ),
    "AC-06-02": (
        "authority_external_effect."
        "ac0602.absent_terminal_receipt"
    ),
    "AC-06-03": (
        "authority_external_effect."
        "ac0603.ambiguous_provider_observation"
    ),
    "AC-06-04": (
        "authority_external_effect."
        "ac0604.no_blind_replay_incomplete_safe"
    ),
}

_STORED_DISPOSITIONS = frozenset(
    {
        "NONE",
        "COMPLETED",
        "INCOMPLETE_SAFE",
    }
)
_OUTPUT_DISPOSITIONS = frozenset(
    {
        "COMPLETED",
        "INCOMPLETE_SAFE",
    }
)
_EVIDENCE_BASES = frozenset(
    {
        "STORED_TERMINAL_RECEIPT",
        "ABSENT_TERMINAL_RECEIPT",
        "AMBIGUOUS_PROVIDER_OBSERVATION",
        "STORED_INCOMPLETE_SAFE",
        "NO_PROVIDER_EVIDENCE_ASSUMED_ABSENT",
        "AMBIGUOUS_PROVIDER_OBSERVATION_ACCEPTED",
    }
)
_BINDING_KINDS = frozenset(
    {
        "CANDIDATE_MATCH",
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


def _validate_terminal_receipt(
    value: Any,
    path: str,
    *,
    output: bool = False,
) -> dict[str, str]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        {
            "receipt_id",
            "command_id",
            "idempotency_key",
            "provider_effect_id",
            "recorded_disposition",
        },
        path=path,
    )

    return {
        "receipt_id": require_string(
            receipt["receipt_id"],
            f"{path}.receipt_id",
        ),
        "command_id": require_string(
            receipt["command_id"],
            f"{path}.command_id",
        ),
        "idempotency_key": require_string(
            receipt["idempotency_key"],
            f"{path}.idempotency_key",
        ),
        "provider_effect_id": require_string(
            receipt["provider_effect_id"],
            f"{path}.provider_effect_id",
        ),
        "recorded_disposition": _require_enum(
            receipt["recorded_disposition"],
            frozenset({"COMPLETED"}),
            f"{path}.recorded_disposition",
            output=output,
        ),
    }


def _validate_provider_observation(
    value: Any,
    path: str,
) -> dict[str, str]:
    observation = require_mapping(value, path)
    require_exact_fields(
        observation,
        {
            "observation_id",
            "provider_effect_id",
            "binding_kind",
        },
        path=path,
    )

    return {
        "observation_id": require_string(
            observation["observation_id"],
            f"{path}.observation_id",
        ),
        "provider_effect_id": require_string(
            observation["provider_effect_id"],
            f"{path}.provider_effect_id",
        ),
        "binding_kind": _require_enum(
            observation["binding_kind"],
            _BINDING_KINDS,
            f"{path}.binding_kind",
        ),
    }


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)
    receipt = facts["stored_terminal_receipt"]
    observations = facts["provider_observations"]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base_fixture_id}.{detail}",
        )

    if facts["request_kind"] != "IDENTICAL_RETRY":
        invalid("request_kind must be IDENTICAL_RETRY")

    if not facts["external_effect_capable"]:
        invalid("command must be capable of an external effect")

    if not facts["attempt_existed"]:
        invalid("a prior lease or attempt must exist")

    if not facts["effect_may_have_started"]:
        invalid("the prior external effect must possibly have started")

    if base_fixture_id == "AC-06-01":
        if facts["stored_disposition"] != "COMPLETED":
            invalid("requires a stored COMPLETED disposition")
        if receipt is None:
            invalid("requires an explicit durable terminal receipt")
        if observations:
            invalid("stored receipt retry must not require provider evidence")
        return

    if base_fixture_id == "AC-06-02":
        if facts["stored_disposition"] != "NONE":
            invalid("requires no stored disposition")
        if receipt is not None:
            invalid("requires an absent terminal receipt")
        if observations:
            invalid("requires zero provider observations")
        return

    if base_fixture_id == "AC-06-03":
        if facts["stored_disposition"] != "NONE":
            invalid("requires no stored disposition")
        if receipt is not None:
            invalid("requires an absent terminal receipt")
        if len(observations) != 2:
            invalid("requires exactly two provider candidates")
        if not all(
            observation["binding_kind"]
            == "CANDIDATE_MATCH"
            for observation in observations
        ):
            invalid(
                "provider observations must be non-unique candidates"
            )
        return

    if facts["stored_disposition"] != "INCOMPLETE_SAFE":
        invalid("requires a stored INCOMPLETE_SAFE disposition")
    if receipt is not None:
        invalid("stored INCOMPLETE_SAFE cannot carry a terminal receipt")
    if observations:
        invalid(
            "stored INCOMPLETE_SAFE retry must not re-query provider evidence"
        )


def validate_authority_external_effect_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed AC-06 external-effect evidence vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "request_kind",
            "command_id",
            "idempotency_key",
            "external_effect_capable",
            "attempt_existed",
            "effect_may_have_started",
            "stored_disposition",
            "stored_terminal_receipt",
            "provider_observations",
        },
        path="inputs",
    )

    raw_receipt = inputs["stored_terminal_receipt"]
    receipt = (
        None
        if raw_receipt is None
        else _validate_terminal_receipt(
            raw_receipt,
            "inputs.stored_terminal_receipt",
        )
    )

    raw_observations = inputs["provider_observations"]
    if not isinstance(raw_observations, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.provider_observations must be an array",
        )

    observations = [
        _validate_provider_observation(
            observation,
            f"inputs.provider_observations[{index}]",
        )
        for index, observation in enumerate(raw_observations)
    ]

    observation_ids = [
        observation["observation_id"]
        for observation in observations
    ]
    if len(observation_ids) != len(set(observation_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.provider_observations contains "
                "duplicate observation_id values"
            ),
        )

    provider_effect_ids = [
        observation["provider_effect_id"]
        for observation in observations
    ]
    if len(provider_effect_ids) != len(set(provider_effect_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.provider_observations contains "
                "duplicate provider_effect_id values"
            ),
        )

    normalized = {
        "request_kind": require_string(
            inputs["request_kind"],
            "inputs.request_kind",
        ),
        "command_id": require_string(
            inputs["command_id"],
            "inputs.command_id",
        ),
        "idempotency_key": require_string(
            inputs["idempotency_key"],
            "inputs.idempotency_key",
        ),
        "external_effect_capable": require_bool(
            inputs["external_effect_capable"],
            "inputs.external_effect_capable",
        ),
        "attempt_existed": require_bool(
            inputs["attempt_existed"],
            "inputs.attempt_existed",
        ),
        "effect_may_have_started": require_bool(
            inputs["effect_may_have_started"],
            "inputs.effect_may_have_started",
        ),
        "stored_disposition": _require_enum(
            inputs["stored_disposition"],
            _STORED_DISPOSITIONS,
            "inputs.stored_disposition",
        ),
        "stored_terminal_receipt": receipt,
        "provider_observations": observations,
    }

    if receipt is not None:
        if receipt["command_id"] != normalized["command_id"]:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.stored_terminal_receipt.command_id "
                    "does not bind to inputs.command_id"
                ),
            )
        if (
            receipt["idempotency_key"]
            != normalized["idempotency_key"]
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.stored_terminal_receipt.idempotency_key "
                    "does not bind to inputs.idempotency_key"
                ),
            )

    stored_disposition = normalized["stored_disposition"]
    if (
        stored_disposition == "COMPLETED"
        and receipt is None
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.stored_disposition COMPLETED "
                "requires a terminal receipt"
            ),
        )
    if (
        stored_disposition != "COMPLETED"
        and receipt is not None
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.stored_terminal_receipt requires "
                "stored_disposition COMPLETED"
            ),
        )

    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def validate_authority_external_effect_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable AC-06 classification and replay evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "command_id",
            "idempotency_key",
            "disposition",
            "evidence_basis",
            "returned_terminal_receipt",
            "provider_evidence_evaluated",
            "provider_observation_count",
            "reconciliation_required",
            "effect_invocation_count",
            "replay_blocked",
            "stored_record_reused",
        },
        path="output",
    )

    raw_receipt = output["returned_terminal_receipt"]
    receipt = (
        None
        if raw_receipt is None
        else _validate_terminal_receipt(
            raw_receipt,
            "output.returned_terminal_receipt",
            output=True,
        )
    )

    return {
        "command_id": require_string(
            output["command_id"],
            "output.command_id",
        ),
        "idempotency_key": require_string(
            output["idempotency_key"],
            "output.idempotency_key",
        ),
        "disposition": _require_enum(
            output["disposition"],
            _OUTPUT_DISPOSITIONS,
            "output.disposition",
            output=True,
        ),
        "evidence_basis": _require_enum(
            output["evidence_basis"],
            _EVIDENCE_BASES,
            "output.evidence_basis",
            output=True,
        ),
        "returned_terminal_receipt": receipt,
        "provider_evidence_evaluated": require_bool(
            output["provider_evidence_evaluated"],
            "output.provider_evidence_evaluated",
        ),
        "provider_observation_count": require_nonnegative_int(
            output["provider_observation_count"],
            "output.provider_observation_count",
        ),
        "reconciliation_required": require_bool(
            output["reconciliation_required"],
            "output.reconciliation_required",
        ),
        "effect_invocation_count": require_nonnegative_int(
            output["effect_invocation_count"],
            "output.effect_invocation_count",
        ),
        "replay_blocked": require_bool(
            output["replay_blocked"],
            "output.replay_blocked",
        ),
        "stored_record_reused": require_bool(
            output["stored_record_reused"],
            "output.stored_record_reused",
        ),
    }


def _copy_receipt(
    receipt: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if receipt is None:
        return None

    return {
        "receipt_id": receipt["receipt_id"],
        "command_id": receipt["command_id"],
        "idempotency_key": receipt["idempotency_key"],
        "provider_effect_id": receipt["provider_effect_id"],
        "recorded_disposition": receipt[
            "recorded_disposition"
        ],
    }


def _output(
    facts: Mapping[str, Any],
    *,
    disposition: str,
    evidence_basis: str,
    returned_terminal_receipt: Mapping[str, Any] | None,
    provider_evidence_evaluated: bool,
    reconciliation_required: bool,
    effect_invocation_count: int,
    replay_blocked: bool,
    stored_record_reused: bool,
) -> dict[str, Any]:
    return {
        "command_id": facts["command_id"],
        "idempotency_key": facts["idempotency_key"],
        "disposition": disposition,
        "evidence_basis": evidence_basis,
        "returned_terminal_receipt": _copy_receipt(
            returned_terminal_receipt
        ),
        "provider_evidence_evaluated": (
            provider_evidence_evaluated
        ),
        "provider_observation_count": len(
            facts["provider_observations"]
        ),
        "reconciliation_required": reconciliation_required,
        "effect_invocation_count": effect_invocation_count,
        "replay_blocked": replay_blocked,
        "stored_record_reused": stored_record_reused,
    }


def _oracle_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = facts["stored_terminal_receipt"]

    if receipt is not None:
        return _output(
            facts,
            disposition="COMPLETED",
            evidence_basis="STORED_TERMINAL_RECEIPT",
            returned_terminal_receipt=receipt,
            provider_evidence_evaluated=False,
            reconciliation_required=False,
            effect_invocation_count=0,
            replay_blocked=True,
            stored_record_reused=True,
        )

    if facts["stored_disposition"] == "INCOMPLETE_SAFE":
        return _output(
            facts,
            disposition="INCOMPLETE_SAFE",
            evidence_basis="STORED_INCOMPLETE_SAFE",
            returned_terminal_receipt=None,
            provider_evidence_evaluated=False,
            reconciliation_required=True,
            effect_invocation_count=0,
            replay_blocked=True,
            stored_record_reused=True,
        )

    if facts["provider_observations"]:
        return _output(
            facts,
            disposition="INCOMPLETE_SAFE",
            evidence_basis="AMBIGUOUS_PROVIDER_OBSERVATION",
            returned_terminal_receipt=None,
            provider_evidence_evaluated=True,
            reconciliation_required=True,
            effect_invocation_count=0,
            replay_blocked=True,
            stored_record_reused=False,
        )

    return _output(
        facts,
        disposition="INCOMPLETE_SAFE",
        evidence_basis="ABSENT_TERMINAL_RECEIPT",
        returned_terminal_receipt=None,
        provider_evidence_evaluated=True,
        reconciliation_required=True,
        effect_invocation_count=0,
        replay_blocked=True,
        stored_record_reused=False,
    )


def _subject_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    stored_disposition = facts["stored_disposition"]

    if stored_disposition == "COMPLETED":
        return _output(
            facts,
            disposition="COMPLETED",
            evidence_basis="STORED_TERMINAL_RECEIPT",
            returned_terminal_receipt=(
                facts["stored_terminal_receipt"]
            ),
            provider_evidence_evaluated=False,
            reconciliation_required=False,
            effect_invocation_count=0,
            replay_blocked=True,
            stored_record_reused=True,
        )

    if stored_disposition == "INCOMPLETE_SAFE":
        return _output(
            facts,
            disposition="INCOMPLETE_SAFE",
            evidence_basis="STORED_INCOMPLETE_SAFE",
            returned_terminal_receipt=None,
            provider_evidence_evaluated=False,
            reconciliation_required=True,
            effect_invocation_count=0,
            replay_blocked=True,
            stored_record_reused=True,
        )

    observations = facts["provider_observations"]
    if len(observations) == 0:
        evidence_basis = "ABSENT_TERMINAL_RECEIPT"
    else:
        evidence_basis = "AMBIGUOUS_PROVIDER_OBSERVATION"

    return _output(
        facts,
        disposition="INCOMPLETE_SAFE",
        evidence_basis=evidence_basis,
        returned_terminal_receipt=None,
        provider_evidence_evaluated=True,
        reconciliation_required=True,
        effect_invocation_count=0,
        replay_blocked=True,
        stored_record_reused=False,
    )


class AuthorityExternalEffectOracle:
    """Pure AC-06 oracle over injected receipt and provider facts."""

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
        return _oracle_evaluate(raw_inputs)


class AuthorityExternalEffectSubjectAdapter:
    """Pure reference adapter over injected durable evidence facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_external_effect.{label}.reference"
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
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )
        return _subject_evaluate(raw_inputs)


class FaultInjectedAuthorityExternalEffectAdapter(
    AuthorityExternalEffectSubjectAdapter
):
    """One concrete external-effect authority defect per negative."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        super().__init__(base_fixture_id)
        faults = {
            "AC-06-01": "replay_completed_command",
            "AC-06-02": "absence_proves_nonoccurrence",
            "AC-06-03": "accept_ambiguous_completion",
            "AC-06-04": "replay_incomplete_safe_command",
        }
        self._fault = faults[base_fixture_id]
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_external_effect."
            f"{label}.{self._fault}"
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

        if self._base == "AC-06-02":
            return _output(
                raw_inputs,
                disposition="COMPLETED",
                evidence_basis=(
                    "NO_PROVIDER_EVIDENCE_ASSUMED_ABSENT"
                ),
                returned_terminal_receipt=None,
                provider_evidence_evaluated=True,
                reconciliation_required=False,
                effect_invocation_count=1,
                replay_blocked=False,
                stored_record_reused=False,
            )

        if self._base == "AC-06-03":
            return _output(
                raw_inputs,
                disposition="COMPLETED",
                evidence_basis=(
                    "AMBIGUOUS_PROVIDER_OBSERVATION_ACCEPTED"
                ),
                returned_terminal_receipt=None,
                provider_evidence_evaluated=True,
                reconciliation_required=False,
                effect_invocation_count=0,
                replay_blocked=True,
                stored_record_reused=False,
            )

        output = _subject_evaluate(raw_inputs)
        output["effect_invocation_count"] = 1
        output["replay_blocked"] = False
        return output


def authority_external_effect_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all AC-06 external-effect authority registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityExternalEffectOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=AuthorityExternalEffectSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_authority_external_effect_inputs
                ),
                output_validator=(
                    validate_authority_external_effect_output
                ),
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
                adapter=(
                    FaultInjectedAuthorityExternalEffectAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_authority_external_effect_inputs
                ),
                output_validator=(
                    validate_authority_external_effect_output
                ),
            )
        )

    return tuple(registrations)
