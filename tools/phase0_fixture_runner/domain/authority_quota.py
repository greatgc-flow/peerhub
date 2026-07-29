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
    "AC-09-01",
    "AC-09-02",
    "AC-09-03",
    "AC-09-04",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-09-01": (
        "authority_quota.ac0901.missing_account_evidence"
    ),
    "AC-09-02": (
        "authority_quota.ac0902.stale_account_evidence"
    ),
    "AC-09-03": (
        "authority_quota."
        "ac0903.independent_workspace_evaluation"
    ),
    "AC-09-04": (
        "authority_quota."
        "ac0904.routing_advice_not_authorization"
    ),
}

_EVALUATION_KINDS = frozenset(
    {
        "QUOTA_EVALUATION",
        "DISPATCH_AUTHORIZATION",
    }
)
_EVIDENCE_SOURCES = frozenset(
    {
        "ADAPTER_EXTERNAL",
    }
)
_EVIDENCE_STATES = frozenset(
    {
        "MISSING",
        "STALE",
        "FRESH",
        "ASSUMED",
    }
)
_EVIDENCE_DISPOSITIONS = frozenset(
    {
        "QUOTA_EVIDENCE_UNAVAILABLE",
        "QUOTA_EVIDENCE_STALE",
        "QUOTA_EVIDENCE_FRESH",
        "ASSUMED_QUOTA_AVAILABLE",
    }
)
_DISPOSITIONS = frozenset(
    {
        "QUOTA_EVIDENCE_UNAVAILABLE",
        "QUOTA_EVIDENCE_STALE",
        "WORKSPACES_EVALUATED_INDEPENDENTLY",
        "ROUTING_ADVICE_NOT_AUTHORIZATION",
        "ASSUMED_QUOTA_AVAILABLE",
        "STALE_QUOTA_ACCEPTED",
        "CROSS_WORKSPACE_QUOTA_COORDINATION",
        "ROUTING_ADVICE_AUTHORIZED_DISPATCH",
    }
)
_DISPATCH_AUTHORITY_SOURCES = frozenset(
    {
        "NONE",
        "MANUAL_AUTHORIZATION",
        "ROUTING_RECOMMENDATION",
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


def _require_nullable_string(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def _require_nullable_int(
    value: Any,
    path: str,
) -> int | None:
    if value is None:
        return None
    return require_nonnegative_int(value, path)


def _require_nullable_bool(
    value: Any,
    path: str,
) -> bool | None:
    if value is None:
        return None
    return require_bool(value, path)


def _validate_quota_evidence(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evidence = require_mapping(value, path)
    require_exact_fields(
        evidence,
        {
            "evidence_id",
            "provider_account_id",
            "evidence_source",
            "observed_epoch",
            "available_units",
        },
        path=path,
    )

    return {
        "evidence_id": require_string(
            evidence["evidence_id"],
            f"{path}.evidence_id",
        ),
        "provider_account_id": require_string(
            evidence["provider_account_id"],
            f"{path}.provider_account_id",
        ),
        "evidence_source": _require_enum(
            evidence["evidence_source"],
            _EVIDENCE_SOURCES,
            f"{path}.evidence_source",
        ),
        "observed_epoch": require_nonnegative_int(
            evidence["observed_epoch"],
            f"{path}.observed_epoch",
        ),
        "available_units": require_nonnegative_int(
            evidence["available_units"],
            f"{path}.available_units",
        ),
    }


def _validate_workspace_request(
    value: Any,
    path: str,
) -> dict[str, Any]:
    request = require_mapping(value, path)
    require_exact_fields(
        request,
        {
            "workspace_id",
            "provider_account_id",
            "requested_units",
            "quota_evidence",
        },
        path=path,
    )

    raw_evidence = request["quota_evidence"]
    evidence = (
        None
        if raw_evidence is None
        else _validate_quota_evidence(
            raw_evidence,
            f"{path}.quota_evidence",
        )
    )

    provider_account_id = require_string(
        request["provider_account_id"],
        f"{path}.provider_account_id",
    )
    if (
        evidence is not None
        and evidence["provider_account_id"]
        != provider_account_id
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.quota_evidence.provider_account_id "
                "does not bind to workspace request"
            ),
        )

    return {
        "workspace_id": require_string(
            request["workspace_id"],
            f"{path}.workspace_id",
        ),
        "provider_account_id": provider_account_id,
        "requested_units": require_nonnegative_int(
            request["requested_units"],
            f"{path}.requested_units",
        ),
        "quota_evidence": evidence,
    }


def _validate_routing_recommendation(
    value: Any,
    path: str,
) -> dict[str, Any]:
    recommendation = require_mapping(value, path)
    require_exact_fields(
        recommendation,
        {
            "recommendation_id",
            "provider_id",
            "recommended_workspace_id",
            "advisory_only",
        },
        path=path,
    )

    return {
        "recommendation_id": require_string(
            recommendation["recommendation_id"],
            f"{path}.recommendation_id",
        ),
        "provider_id": require_string(
            recommendation["provider_id"],
            f"{path}.provider_id",
        ),
        "recommended_workspace_id": require_string(
            recommendation["recommended_workspace_id"],
            f"{path}.recommended_workspace_id",
        ),
        "advisory_only": require_bool(
            recommendation["advisory_only"],
            f"{path}.advisory_only",
        ),
    }


def _validate_manual_dispatch_authorization(
    value: Any,
    path: str,
) -> dict[str, Any]:
    authorization = require_mapping(value, path)
    require_exact_fields(
        authorization,
        {
            "authorization_id",
            "workspace_id",
            "provider_id",
            "authorized",
        },
        path=path,
    )

    return {
        "authorization_id": require_string(
            authorization["authorization_id"],
            f"{path}.authorization_id",
        ),
        "workspace_id": require_string(
            authorization["workspace_id"],
            f"{path}.workspace_id",
        ),
        "provider_id": require_string(
            authorization["provider_id"],
            f"{path}.provider_id",
        ),
        "authorized": require_bool(
            authorization["authorized"],
            f"{path}.authorized",
        ),
    }


def _evidence_age(
    facts: Mapping[str, Any],
    request: Mapping[str, Any],
) -> int | None:
    evidence = request["quota_evidence"]
    if evidence is None:
        return None
    return (
        facts["current_evidence_epoch"]
        - evidence["observed_epoch"]
    )


def _evidence_is_fresh(
    facts: Mapping[str, Any],
    request: Mapping[str, Any],
) -> bool:
    age = _evidence_age(facts, request)
    return bool(
        age is not None
        and age
        <= facts["maximum_evidence_age_epochs"]
    )


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)
    requests = facts["workspace_requests"]
    recommendation = facts["routing_recommendation"]
    authorization = facts[
        "manual_dispatch_authorization"
    ]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base_fixture_id}.{detail}",
        )

    if base_fixture_id in {
        "AC-09-01",
        "AC-09-02",
        "AC-09-03",
    }:
        if facts["evaluation_kind"] != "QUOTA_EVALUATION":
            invalid("requires QUOTA_EVALUATION")
        if not requests:
            invalid("quota evaluation requires workspace requests")
        if recommendation is not None or authorization is not None:
            invalid("quota evaluation cannot carry dispatch facts")

    if base_fixture_id == "AC-09-01":
        if len(requests) != 1:
            invalid("requires exactly one workspace request")
        if requests[0]["quota_evidence"] is not None:
            invalid("requires missing provider-account evidence")
        if requests[0]["requested_units"] < 1:
            invalid("requires a nonzero quota request")
        return

    if base_fixture_id == "AC-09-02":
        if len(requests) != 1:
            invalid("requires exactly one workspace request")
        if requests[0]["quota_evidence"] is None:
            invalid("requires present but stale evidence")
        if _evidence_is_fresh(facts, requests[0]):
            invalid("evidence must exceed the freshness boundary")
        if requests[0]["requested_units"] < 1:
            invalid("requires a nonzero quota request")
        return

    if base_fixture_id == "AC-09-03":
        if len(requests) != 2:
            invalid("requires exactly two workspaces")

        provider_accounts = {
            request["provider_account_id"]
            for request in requests
        }
        if len(provider_accounts) != 1:
            invalid(
                "both workspaces must reference one provider account"
            )

        if not all(
            request["quota_evidence"] is not None
            and _evidence_is_fresh(facts, request)
            for request in requests
        ):
            invalid("both workspaces require fresh adapter evidence")

        available_units = {
            request["quota_evidence"]["available_units"]
            for request in requests
        }
        if len(available_units) != 2:
            invalid(
                "workspace evidence must contain different values"
            )

        if not all(
            request["quota_evidence"]["available_units"]
            >= request["requested_units"]
            for request in requests
        ):
            invalid(
                "both independent workspace evaluations "
                "must be sufficient"
            )
        return

    if (
        facts["evaluation_kind"]
        != "DISPATCH_AUTHORIZATION"
    ):
        invalid("requires DISPATCH_AUTHORIZATION")
    if requests:
        invalid("dispatch vector cannot carry quota requests")
    if (
        facts["current_evidence_epoch"] != 0
        or facts["maximum_evidence_age_epochs"] != 0
    ):
        invalid("dispatch vector must not use quota epochs")
    if recommendation is None:
        invalid("requires a routing recommendation")
    if not recommendation["advisory_only"]:
        invalid("routing recommendation must be advisory")
    if authorization is not None:
        invalid("requires absent manual dispatch authorization")


def validate_authority_quota_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed AC-09 quota-authority vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "evaluation_kind",
            "current_evidence_epoch",
            "maximum_evidence_age_epochs",
            "workspace_requests",
            "routing_recommendation",
            "manual_dispatch_authorization",
        },
        path="inputs",
    )

    current_epoch = require_nonnegative_int(
        inputs["current_evidence_epoch"],
        "inputs.current_evidence_epoch",
    )
    maximum_age = require_nonnegative_int(
        inputs["maximum_evidence_age_epochs"],
        "inputs.maximum_evidence_age_epochs",
    )

    raw_requests = inputs["workspace_requests"]
    if not isinstance(raw_requests, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.workspace_requests must be an array",
        )

    requests = [
        _validate_workspace_request(
            request,
            f"inputs.workspace_requests[{index}]",
        )
        for index, request in enumerate(raw_requests)
    ]

    workspace_ids = [
        request["workspace_id"]
        for request in requests
    ]
    if len(workspace_ids) != len(set(workspace_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.workspace_requests contains "
                "duplicate workspace_id values"
            ),
        )

    evidence_ids = [
        request["quota_evidence"]["evidence_id"]
        for request in requests
        if request["quota_evidence"] is not None
    ]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.workspace_requests contains "
                "duplicate quota evidence IDs"
            ),
        )

    for index, request in enumerate(requests):
        evidence = request["quota_evidence"]
        if (
            evidence is not None
            and evidence["observed_epoch"] > current_epoch
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.workspace_requests"
                    f"[{index}].quota_evidence.observed_epoch "
                    "cannot be in the future"
                ),
            )

    raw_recommendation = inputs[
        "routing_recommendation"
    ]
    recommendation = (
        None
        if raw_recommendation is None
        else _validate_routing_recommendation(
            raw_recommendation,
            "inputs.routing_recommendation",
        )
    )

    raw_authorization = inputs[
        "manual_dispatch_authorization"
    ]
    authorization = (
        None
        if raw_authorization is None
        else _validate_manual_dispatch_authorization(
            raw_authorization,
            "inputs.manual_dispatch_authorization",
        )
    )

    normalized = {
        "evaluation_kind": _require_enum(
            inputs["evaluation_kind"],
            _EVALUATION_KINDS,
            "inputs.evaluation_kind",
        ),
        "current_evidence_epoch": current_epoch,
        "maximum_evidence_age_epochs": maximum_age,
        "workspace_requests": requests,
        "routing_recommendation": recommendation,
        "manual_dispatch_authorization": authorization,
    }

    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def _validate_workspace_evaluation(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evaluation = require_mapping(value, path)
    require_exact_fields(
        evaluation,
        {
            "workspace_id",
            "provider_account_id",
            "requested_units",
            "evidence_id",
            "evidence_state",
            "evidence_disposition",
            "evidence_age_epochs",
            "observed_available_units",
            "usable_available_units",
            "quota_sufficient",
            "adapter_evidence_consumed",
            "evidence_authoritative",
        },
        path=path,
    )

    return {
        "workspace_id": require_string(
            evaluation["workspace_id"],
            f"{path}.workspace_id",
        ),
        "provider_account_id": require_string(
            evaluation["provider_account_id"],
            f"{path}.provider_account_id",
        ),
        "requested_units": require_nonnegative_int(
            evaluation["requested_units"],
            f"{path}.requested_units",
        ),
        "evidence_id": _require_nullable_string(
            evaluation["evidence_id"],
            f"{path}.evidence_id",
        ),
        "evidence_state": _require_enum(
            evaluation["evidence_state"],
            _EVIDENCE_STATES,
            f"{path}.evidence_state",
            output=True,
        ),
        "evidence_disposition": _require_enum(
            evaluation["evidence_disposition"],
            _EVIDENCE_DISPOSITIONS,
            f"{path}.evidence_disposition",
            output=True,
        ),
        "evidence_age_epochs": _require_nullable_int(
            evaluation["evidence_age_epochs"],
            f"{path}.evidence_age_epochs",
        ),
        "observed_available_units": _require_nullable_int(
            evaluation["observed_available_units"],
            f"{path}.observed_available_units",
        ),
        "usable_available_units": _require_nullable_int(
            evaluation["usable_available_units"],
            f"{path}.usable_available_units",
        ),
        "quota_sufficient": _require_nullable_bool(
            evaluation["quota_sufficient"],
            f"{path}.quota_sufficient",
        ),
        "adapter_evidence_consumed": require_bool(
            evaluation["adapter_evidence_consumed"],
            f"{path}.adapter_evidence_consumed",
        ),
        "evidence_authoritative": require_bool(
            evaluation["evidence_authoritative"],
            f"{path}.evidence_authoritative",
        ),
    }


def validate_authority_quota_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable AC-09 evidence and authority output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "disposition",
            "workspace_evaluations",
            "provider_quota_evidence_external_only",
            "cross_workspace_coordination_performed",
            "provider_quota_reservation_count",
            "provider_quota_allocation_count",
            "workspace_database_quota_write_count",
            "routing_recommendation_present",
            "routing_recommendation_advisory_only",
            "manual_dispatch_authorization_present",
            "dispatch_authorized",
            "dispatch_authority_source",
        },
        path="output",
    )

    raw_evaluations = output["workspace_evaluations"]
    if not isinstance(raw_evaluations, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.workspace_evaluations must be an array",
        )

    evaluations = [
        _validate_workspace_evaluation(
            evaluation,
            f"output.workspace_evaluations[{index}]",
        )
        for index, evaluation in enumerate(raw_evaluations)
    ]

    workspace_ids = [
        evaluation["workspace_id"]
        for evaluation in evaluations
    ]
    if len(workspace_ids) != len(set(workspace_ids)):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.workspace_evaluations contains "
                "duplicate workspace IDs"
            ),
        )

    return {
        "disposition": _require_enum(
            output["disposition"],
            _DISPOSITIONS,
            "output.disposition",
            output=True,
        ),
        "workspace_evaluations": evaluations,
        "provider_quota_evidence_external_only": require_bool(
            output["provider_quota_evidence_external_only"],
            "output.provider_quota_evidence_external_only",
        ),
        "cross_workspace_coordination_performed": require_bool(
            output[
                "cross_workspace_coordination_performed"
            ],
            (
                "output."
                "cross_workspace_coordination_performed"
            ),
        ),
        "provider_quota_reservation_count": (
            require_nonnegative_int(
                output["provider_quota_reservation_count"],
                "output.provider_quota_reservation_count",
            )
        ),
        "provider_quota_allocation_count": (
            require_nonnegative_int(
                output["provider_quota_allocation_count"],
                "output.provider_quota_allocation_count",
            )
        ),
        "workspace_database_quota_write_count": (
            require_nonnegative_int(
                output["workspace_database_quota_write_count"],
                "output.workspace_database_quota_write_count",
            )
        ),
        "routing_recommendation_present": require_bool(
            output["routing_recommendation_present"],
            "output.routing_recommendation_present",
        ),
        "routing_recommendation_advisory_only": require_bool(
            output["routing_recommendation_advisory_only"],
            "output.routing_recommendation_advisory_only",
        ),
        "manual_dispatch_authorization_present": require_bool(
            output[
                "manual_dispatch_authorization_present"
            ],
            (
                "output."
                "manual_dispatch_authorization_present"
            ),
        ),
        "dispatch_authorized": require_bool(
            output["dispatch_authorized"],
            "output.dispatch_authorized",
        ),
        "dispatch_authority_source": _require_enum(
            output["dispatch_authority_source"],
            _DISPATCH_AUTHORITY_SOURCES,
            "output.dispatch_authority_source",
            output=True,
        ),
    }


def _missing_evaluation(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "workspace_id": request["workspace_id"],
        "provider_account_id": request[
            "provider_account_id"
        ],
        "requested_units": request["requested_units"],
        "evidence_id": None,
        "evidence_state": "MISSING",
        "evidence_disposition": (
            "QUOTA_EVIDENCE_UNAVAILABLE"
        ),
        "evidence_age_epochs": None,
        "observed_available_units": None,
        "usable_available_units": None,
        "quota_sufficient": None,
        "adapter_evidence_consumed": False,
        "evidence_authoritative": False,
    }


def _stale_evaluation(
    facts: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = request["quota_evidence"]
    return {
        "workspace_id": request["workspace_id"],
        "provider_account_id": request[
            "provider_account_id"
        ],
        "requested_units": request["requested_units"],
        "evidence_id": evidence["evidence_id"],
        "evidence_state": "STALE",
        "evidence_disposition": "QUOTA_EVIDENCE_STALE",
        "evidence_age_epochs": _evidence_age(
            facts,
            request,
        ),
        "observed_available_units": evidence[
            "available_units"
        ],
        "usable_available_units": None,
        "quota_sufficient": None,
        "adapter_evidence_consumed": True,
        "evidence_authoritative": False,
    }


def _fresh_evaluation(
    facts: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    usable_available_units: int | None = None,
) -> dict[str, Any]:
    evidence = request["quota_evidence"]
    if usable_available_units is None:
        usable_available_units = evidence[
            "available_units"
        ]

    return {
        "workspace_id": request["workspace_id"],
        "provider_account_id": request[
            "provider_account_id"
        ],
        "requested_units": request["requested_units"],
        "evidence_id": evidence["evidence_id"],
        "evidence_state": "FRESH",
        "evidence_disposition": "QUOTA_EVIDENCE_FRESH",
        "evidence_age_epochs": _evidence_age(
            facts,
            request,
        ),
        "observed_available_units": evidence[
            "available_units"
        ],
        "usable_available_units": usable_available_units,
        "quota_sufficient": (
            usable_available_units
            >= request["requested_units"]
        ),
        "adapter_evidence_consumed": True,
        "evidence_authoritative": True,
    }


def _assumed_missing_evaluation(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    assumed_units = 100
    return {
        "workspace_id": request["workspace_id"],
        "provider_account_id": request[
            "provider_account_id"
        ],
        "requested_units": request["requested_units"],
        "evidence_id": None,
        "evidence_state": "ASSUMED",
        "evidence_disposition": "ASSUMED_QUOTA_AVAILABLE",
        "evidence_age_epochs": None,
        "observed_available_units": None,
        "usable_available_units": assumed_units,
        "quota_sufficient": (
            assumed_units >= request["requested_units"]
        ),
        "adapter_evidence_consumed": False,
        "evidence_authoritative": True,
    }


def _output(
    *,
    disposition: str,
    workspace_evaluations: list[dict[str, Any]],
    provider_quota_evidence_external_only: bool = True,
    cross_workspace_coordination_performed: bool = False,
    provider_quota_reservation_count: int = 0,
    provider_quota_allocation_count: int = 0,
    workspace_database_quota_write_count: int = 0,
    routing_recommendation_present: bool = False,
    routing_recommendation_advisory_only: bool = False,
    manual_dispatch_authorization_present: bool = False,
    dispatch_authorized: bool = False,
    dispatch_authority_source: str = "NONE",
) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "workspace_evaluations": workspace_evaluations,
        "provider_quota_evidence_external_only": (
            provider_quota_evidence_external_only
        ),
        "cross_workspace_coordination_performed": (
            cross_workspace_coordination_performed
        ),
        "provider_quota_reservation_count": (
            provider_quota_reservation_count
        ),
        "provider_quota_allocation_count": (
            provider_quota_allocation_count
        ),
        "workspace_database_quota_write_count": (
            workspace_database_quota_write_count
        ),
        "routing_recommendation_present": (
            routing_recommendation_present
        ),
        "routing_recommendation_advisory_only": (
            routing_recommendation_advisory_only
        ),
        "manual_dispatch_authorization_present": (
            manual_dispatch_authorization_present
        ),
        "dispatch_authorized": dispatch_authorized,
        "dispatch_authority_source": (
            dispatch_authority_source
        ),
    }


def _oracle_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if facts["evaluation_kind"] == "DISPATCH_AUTHORIZATION":
        recommendation = facts["routing_recommendation"]
        authorization = facts[
            "manual_dispatch_authorization"
        ]
        return _output(
            disposition="ROUTING_ADVICE_NOT_AUTHORIZATION",
            workspace_evaluations=[],
            routing_recommendation_present=(
                recommendation is not None
            ),
            routing_recommendation_advisory_only=bool(
                recommendation is not None
                and recommendation["advisory_only"]
            ),
            manual_dispatch_authorization_present=(
                authorization is not None
            ),
            dispatch_authorized=bool(
                authorization is not None
                and authorization["authorized"]
            ),
            dispatch_authority_source=(
                "MANUAL_AUTHORIZATION"
                if (
                    authorization is not None
                    and authorization["authorized"]
                )
                else "NONE"
            ),
        )

    requests = facts["workspace_requests"]
    if len(requests) == 2:
        return _output(
            disposition=(
                "WORKSPACES_EVALUATED_INDEPENDENTLY"
            ),
            workspace_evaluations=[
                _fresh_evaluation(facts, request)
                for request in requests
            ],
        )

    request = requests[0]
    if request["quota_evidence"] is None:
        return _output(
            disposition="QUOTA_EVIDENCE_UNAVAILABLE",
            workspace_evaluations=[
                _missing_evaluation(request)
            ],
        )

    if not _evidence_is_fresh(facts, request):
        return _output(
            disposition="QUOTA_EVIDENCE_STALE",
            workspace_evaluations=[
                _stale_evaluation(facts, request)
            ],
        )

    return _output(
        disposition="WORKSPACES_EVALUATED_INDEPENDENTLY",
        workspace_evaluations=[
            _fresh_evaluation(facts, request)
        ],
    )


def _subject_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    kind = facts["evaluation_kind"]

    if kind == "DISPATCH_AUTHORIZATION":
        recommendation = facts["routing_recommendation"]
        authorization = facts[
            "manual_dispatch_authorization"
        ]
        authorized = bool(
            authorization is not None
            and authorization["authorized"]
        )
        return _output(
            disposition="ROUTING_ADVICE_NOT_AUTHORIZATION",
            workspace_evaluations=[],
            routing_recommendation_present=(
                recommendation is not None
            ),
            routing_recommendation_advisory_only=bool(
                recommendation is not None
                and recommendation["advisory_only"]
            ),
            manual_dispatch_authorization_present=(
                authorization is not None
            ),
            dispatch_authorized=authorized,
            dispatch_authority_source=(
                "MANUAL_AUTHORIZATION"
                if authorized
                else "NONE"
            ),
        )

    evaluations: list[dict[str, Any]] = []
    for request in facts["workspace_requests"]:
        if request["quota_evidence"] is None:
            evaluations.append(
                _missing_evaluation(request)
            )
        elif _evidence_is_fresh(facts, request):
            evaluations.append(
                _fresh_evaluation(facts, request)
            )
        else:
            evaluations.append(
                _stale_evaluation(facts, request)
            )

    disposition = (
        "WORKSPACES_EVALUATED_INDEPENDENTLY"
        if len(evaluations) > 1
        else evaluations[0]["evidence_disposition"]
    )
    return _output(
        disposition=disposition,
        workspace_evaluations=evaluations,
    )


class AuthorityQuotaOracle:
    """Pure AC-09 oracle over injected external evidence."""

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


class AuthorityQuotaSubjectAdapter:
    """Pure reference adapter over injected quota facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_quota.{label}.reference"
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


class FaultInjectedAuthorityQuotaAdapter(
    AuthorityQuotaSubjectAdapter
):
    """One genuine quota-authority defect per negative."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        super().__init__(base_fixture_id)
        faults = {
            "AC-09-01": "assume_missing_quota_available",
            "AC-09-02": "accept_stale_quota_evidence",
            "AC-09-03": "coordinate_shared_account_quota",
            "AC-09-04": "treat_routing_advice_as_authority",
        }
        self._fault = faults[base_fixture_id]
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_quota.{label}.{self._fault}"
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

        if self._base == "AC-09-01":
            request = raw_inputs["workspace_requests"][0]
            return _output(
                disposition="ASSUMED_QUOTA_AVAILABLE",
                workspace_evaluations=[
                    _assumed_missing_evaluation(request)
                ],
                provider_quota_evidence_external_only=False,
            )

        if self._base == "AC-09-02":
            request = raw_inputs["workspace_requests"][0]
            return _output(
                disposition="STALE_QUOTA_ACCEPTED",
                workspace_evaluations=[
                    _fresh_evaluation(
                        raw_inputs,
                        request,
                    )
                ],
            )

        if self._base == "AC-09-03":
            first, second = raw_inputs["workspace_requests"]
            first_evaluation = _fresh_evaluation(
                raw_inputs,
                first,
            )
            shared_remaining = max(
                0,
                first["quota_evidence"]["available_units"]
                - first["requested_units"],
            )
            second_evaluation = _fresh_evaluation(
                raw_inputs,
                second,
                usable_available_units=shared_remaining,
            )
            return _output(
                disposition=(
                    "CROSS_WORKSPACE_QUOTA_COORDINATION"
                ),
                workspace_evaluations=[
                    first_evaluation,
                    second_evaluation,
                ],
                cross_workspace_coordination_performed=True,
                provider_quota_reservation_count=1,
                provider_quota_allocation_count=1,
            )

        recommendation = raw_inputs[
            "routing_recommendation"
        ]
        return _output(
            disposition=(
                "ROUTING_ADVICE_AUTHORIZED_DISPATCH"
            ),
            workspace_evaluations=[],
            routing_recommendation_present=True,
            routing_recommendation_advisory_only=(
                recommendation["advisory_only"]
            ),
            manual_dispatch_authorization_present=False,
            dispatch_authorized=True,
            dispatch_authority_source=(
                "ROUTING_RECOMMENDATION"
            ),
        )


def authority_quota_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all AC-09 quota-authority registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityQuotaOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=AuthorityQuotaSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_authority_quota_inputs
                ),
                output_validator=(
                    validate_authority_quota_output
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
                adapter=FaultInjectedAuthorityQuotaAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_authority_quota_inputs
                ),
                output_validator=(
                    validate_authority_quota_output
                ),
            )
        )

    return tuple(registrations)
