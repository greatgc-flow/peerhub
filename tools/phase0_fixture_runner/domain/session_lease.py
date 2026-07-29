"""CANDIDATE-tier session-lease rules for SL-01 through SL-06.

The module operates only on injected session, binding, lease, identity,
and recovery facts. It performs no real process, filesystem, network, or
operating-system operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import (
    DomainContractError,
    DomainRegistration,
    IsolatedDomainContext,
    canonical_json_bytes,
    require_bool,
    require_exact_fields,
    require_mapping,
    require_nonnegative_int,
    require_string,
    sha256_bytes,
)

_BASE_FIXTURES = (
    "SL-01",
    "SL-02",
    "SL-03",
    "SL-04",
    "SL-05",
    "SL-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "SL-01": "session_lease.sl01.fresh_create",
    "SL-02": "session_lease.sl02.compatible_resume",
    "SL-03": "session_lease.sl03.mismatch_rejection",
    "SL-04": "session_lease.sl04.concurrent_leases",
    "SL-05": "session_lease.sl05.owner_fence",
    "SL-06": "session_lease.sl06.recovery_receipt",
}

_BINDING_FIELDS = frozenset(
    {
        "workspace_scope_id",
        "room_id",
        "room_configuration_revision",
        "peer_id",
        "protocol_major",
        "resume_contract_revision",
        "profile_id",
        "profile_revision",
        "effective_tier",
        "binding_schema_version",
    }
)

_RECOVERY_TRIGGERS = frozenset(
    {
        "STALE_REVISION",
        "STALE_FENCING_TOKEN",
        "OWNER_PRINCIPAL_MISMATCH",
        "OWNER_INSTANCE_MISMATCH",
        "PROCESS_BIRTH_MISMATCH",
        "INVALID_EXPIRY",
        "EXPIRED_WITH_UNVERIFIED_OWNER",
    }
)
_RECOVERY_DECISIONS = frozenset(
    {
        "QUARANTINE",
        "FENCE_AND_EXPIRE",
        "FENCE_AND_CLOSE",
        "FENCE_AND_REASSIGN",
        "RECONCILIATION_REQUIRED",
    }
)
_LEASE_AUTHORITY_CERTAINTIES = frozenset(
    {
        "CONFIRMED_CURRENT",
        "PRIOR_HOLDER_UNVERIFIED",
        "FENCED_FOR_FUTURE_WRITES",
    }
)
_SL05_STATUSES = frozenset(
    {
        "REJECTED",
        "ACCEPTED",
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


def fingerprint(binding: Mapping[str, Any]) -> str:
    """Return the canonical session-binding fingerprint."""

    return (
        "sha256:"
        + sha256_bytes(
            canonical_json_bytes(binding)
        )
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


def _require_string_list(
    value: Any,
    path: str,
) -> list[str]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} must be an array",
        )

    values = [
        require_string(
            item,
            f"{path}[{index}]",
        )
        for index, item in enumerate(value)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} contains duplicate values",
        )
    return values


def _validate_binding(
    value: Any,
    path: str,
) -> dict[str, Any]:
    binding = require_mapping(value, path)
    require_exact_fields(
        binding,
        _BINDING_FIELDS,
        path=path,
    )

    return {
        "workspace_scope_id": require_string(
            binding["workspace_scope_id"],
            f"{path}.workspace_scope_id",
        ),
        "room_id": require_string(
            binding["room_id"],
            f"{path}.room_id",
        ),
        "room_configuration_revision": (
            require_nonnegative_int(
                binding[
                    "room_configuration_revision"
                ],
                (
                    f"{path}."
                    "room_configuration_revision"
                ),
            )
        ),
        "peer_id": require_string(
            binding["peer_id"],
            f"{path}.peer_id",
        ),
        "protocol_major": require_nonnegative_int(
            binding["protocol_major"],
            f"{path}.protocol_major",
        ),
        "resume_contract_revision": (
            require_nonnegative_int(
                binding[
                    "resume_contract_revision"
                ],
                (
                    f"{path}."
                    "resume_contract_revision"
                ),
            )
        ),
        "profile_id": require_string(
            binding["profile_id"],
            f"{path}.profile_id",
        ),
        "profile_revision": require_nonnegative_int(
            binding["profile_revision"],
            f"{path}.profile_revision",
        ),
        "effective_tier": require_string(
            binding["effective_tier"],
            f"{path}.effective_tier",
        ),
        "binding_schema_version": (
            require_nonnegative_int(
                binding[
                    "binding_schema_version"
                ],
                (
                    f"{path}."
                    "binding_schema_version"
                ),
            )
        ),
    }


def _validate_lease_seed(
    value: Any,
    path: str,
) -> dict[str, Any]:
    lease = require_mapping(value, path)
    require_exact_fields(
        lease,
        {
            "lease_id",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
            "fencing_token",
            "revision",
        },
        path=path,
    )

    return {
        "lease_id": require_string(
            lease["lease_id"],
            f"{path}.lease_id",
        ),
        "owner_principal_id": require_string(
            lease["owner_principal_id"],
            f"{path}.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            lease["owner_instance_id"],
            f"{path}.owner_instance_id",
        ),
        "owner_process_birth_identity": require_string(
            lease[
                "owner_process_birth_identity"
            ],
            (
                f"{path}."
                "owner_process_birth_identity"
            ),
        ),
        "fencing_token": require_nonnegative_int(
            lease["fencing_token"],
            f"{path}.fencing_token",
        ),
        "revision": require_nonnegative_int(
            lease["revision"],
            f"{path}.revision",
        ),
    }


def _validate_sl01_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "binding",
            "session_id",
            "lease_id",
            "lease_kind",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
        },
        path="inputs",
    )

    validated = {
        "binding": _validate_binding(
            inputs["binding"],
            "inputs.binding",
        ),
        "session_id": require_string(
            inputs["session_id"],
            "inputs.session_id",
        ),
        "lease_id": require_string(
            inputs["lease_id"],
            "inputs.lease_id",
        ),
        "lease_kind": require_string(
            inputs["lease_kind"],
            "inputs.lease_kind",
        ),
        "owner_principal_id": require_string(
            inputs["owner_principal_id"],
            "inputs.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            inputs["owner_instance_id"],
            "inputs.owner_instance_id",
        ),
        "owner_process_birth_identity": require_string(
            inputs[
                "owner_process_birth_identity"
            ],
            (
                "inputs."
                "owner_process_birth_identity"
            ),
        ),
    }

    if (
        validated["session_id"]
        == validated["lease_id"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-01 requires independent session_id "
                "and lease_id values"
            ),
        )

    return validated


def _validate_sl02_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "stored_binding",
            "resolved_binding",
            "stored_session_id",
            "new_lease_id",
            "lease_kind",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
        },
        path="inputs",
    )

    validated = {
        "stored_binding": _validate_binding(
            inputs["stored_binding"],
            "inputs.stored_binding",
        ),
        "resolved_binding": _validate_binding(
            inputs["resolved_binding"],
            "inputs.resolved_binding",
        ),
        "stored_session_id": require_string(
            inputs["stored_session_id"],
            "inputs.stored_session_id",
        ),
        "new_lease_id": require_string(
            inputs["new_lease_id"],
            "inputs.new_lease_id",
        ),
        "lease_kind": require_string(
            inputs["lease_kind"],
            "inputs.lease_kind",
        ),
        "owner_principal_id": require_string(
            inputs["owner_principal_id"],
            "inputs.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            inputs["owner_instance_id"],
            "inputs.owner_instance_id",
        ),
        "owner_process_birth_identity": require_string(
            inputs[
                "owner_process_birth_identity"
            ],
            (
                "inputs."
                "owner_process_birth_identity"
            ),
        ),
    }

    if (
        validated["resolved_binding"]
        != validated["stored_binding"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-02 requires exact stored/resolved "
                "binding equality"
            ),
        )

    return validated


def _validate_sl03_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "stored_binding",
            "resolved_binding",
            "stored_session_id",
        },
        path="inputs",
    )

    validated = {
        "stored_binding": _validate_binding(
            inputs["stored_binding"],
            "inputs.stored_binding",
        ),
        "resolved_binding": _validate_binding(
            inputs["resolved_binding"],
            "inputs.resolved_binding",
        ),
        "stored_session_id": require_string(
            inputs["stored_session_id"],
            "inputs.stored_session_id",
        ),
    }

    if (
        validated["resolved_binding"]
        == validated["stored_binding"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-03 requires a real binding "
                "mismatch"
            ),
        )

    return validated


def _validate_sl04_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "session_id",
            "owner_peer_id",
            "lease_a",
            "lease_b",
            "renew_target_lease_id",
        },
        path="inputs",
    )

    validated = {
        "session_id": require_string(
            inputs["session_id"],
            "inputs.session_id",
        ),
        "owner_peer_id": require_string(
            inputs["owner_peer_id"],
            "inputs.owner_peer_id",
        ),
        "lease_a": _validate_lease_seed(
            inputs["lease_a"],
            "inputs.lease_a",
        ),
        "lease_b": _validate_lease_seed(
            inputs["lease_b"],
            "inputs.lease_b",
        ),
        "renew_target_lease_id": require_string(
            inputs["renew_target_lease_id"],
            "inputs.renew_target_lease_id",
        ),
    }

    if (
        validated["lease_a"]["lease_id"]
        == validated["lease_b"]["lease_id"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "SL-04 requires distinct lease IDs",
        )

    if (
        validated["renew_target_lease_id"]
        != validated["lease_a"]["lease_id"]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-04 requires lease_a as the "
                "renewal target"
            ),
        )

    return validated


def _validate_sl05_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "session_id",
            "lease_id",
            "persisted",
            "requester",
        },
        path="inputs",
    )

    persisted = require_mapping(
        inputs["persisted"],
        "inputs.persisted",
    )
    require_exact_fields(
        persisted,
        {
            "owner_peer_id",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
            "fencing_token",
            "revision",
        },
        path="inputs.persisted",
    )

    requester = require_mapping(
        inputs["requester"],
        "inputs.requester",
    )
    require_exact_fields(
        requester,
        {
            "asserted_peer_id",
            "authenticated_principal_id",
            "instance_id",
            "process_birth_identity",
        },
        path="inputs.requester",
    )

    validated = {
        "session_id": require_string(
            inputs["session_id"],
            "inputs.session_id",
        ),
        "lease_id": require_string(
            inputs["lease_id"],
            "inputs.lease_id",
        ),
        "persisted": {
            "owner_peer_id": require_string(
                persisted["owner_peer_id"],
                (
                    "inputs.persisted."
                    "owner_peer_id"
                ),
            ),
            "owner_principal_id": require_string(
                persisted["owner_principal_id"],
                (
                    "inputs.persisted."
                    "owner_principal_id"
                ),
            ),
            "owner_instance_id": require_string(
                persisted["owner_instance_id"],
                (
                    "inputs.persisted."
                    "owner_instance_id"
                ),
            ),
            "owner_process_birth_identity": (
                require_string(
                    persisted[
                        "owner_process_birth_identity"
                    ],
                    (
                        "inputs.persisted."
                        "owner_process_birth_identity"
                    ),
                )
            ),
            "fencing_token": require_nonnegative_int(
                persisted["fencing_token"],
                (
                    "inputs.persisted."
                    "fencing_token"
                ),
            ),
            "revision": require_nonnegative_int(
                persisted["revision"],
                "inputs.persisted.revision",
            ),
        },
        "requester": {
            "asserted_peer_id": require_string(
                requester["asserted_peer_id"],
                (
                    "inputs.requester."
                    "asserted_peer_id"
                ),
            ),
            "authenticated_principal_id": (
                require_string(
                    requester[
                        "authenticated_principal_id"
                    ],
                    (
                        "inputs.requester."
                        "authenticated_principal_id"
                    ),
                )
            ),
            "instance_id": require_string(
                requester["instance_id"],
                "inputs.requester.instance_id",
            ),
            "process_birth_identity": (
                require_string(
                    requester[
                        "process_birth_identity"
                    ],
                    (
                        "inputs.requester."
                        "process_birth_identity"
                    ),
                )
            ),
        },
    }

    if (
        validated["requester"][
            "asserted_peer_id"
        ]
        != validated["persisted"][
            "owner_peer_id"
        ]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-05 requires a matching "
                "self-asserted peer ID"
            ),
        )

    if (
        validated["requester"][
            "authenticated_principal_id"
        ]
        == validated["persisted"][
            "owner_principal_id"
        ]
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-05 requires an authenticated "
                "principal mismatch"
            ),
        )

    return validated


def _validate_sl06_inputs(
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "session_id",
            "lease_id",
            "recovery_actor_principal_id",
            "trigger",
            "policy_id",
            "decision",
            "certainty_before_policy",
            "certainty_after_policy",
            "pre_lifecycle_state",
            "pre_revision",
            "pre_fencing_token",
            "post_lifecycle_state",
            "post_revision",
            "post_fencing_token",
        },
        path="inputs",
    )

    validated = {
        "session_id": require_string(
            inputs["session_id"],
            "inputs.session_id",
        ),
        "lease_id": require_string(
            inputs["lease_id"],
            "inputs.lease_id",
        ),
        "recovery_actor_principal_id": (
            require_string(
                inputs[
                    "recovery_actor_principal_id"
                ],
                (
                    "inputs."
                    "recovery_actor_principal_id"
                ),
            )
        ),
        "trigger": _require_input_enum(
            inputs["trigger"],
            _RECOVERY_TRIGGERS,
            "inputs.trigger",
        ),
        "policy_id": require_string(
            inputs["policy_id"],
            "inputs.policy_id",
        ),
        "decision": _require_input_enum(
            inputs["decision"],
            _RECOVERY_DECISIONS,
            "inputs.decision",
        ),
        "certainty_before_policy": (
            _require_input_enum(
                inputs[
                    "certainty_before_policy"
                ],
                _LEASE_AUTHORITY_CERTAINTIES,
                "inputs.certainty_before_policy",
            )
        ),
        "certainty_after_policy": (
            _require_input_enum(
                inputs[
                    "certainty_after_policy"
                ],
                _LEASE_AUTHORITY_CERTAINTIES,
                "inputs.certainty_after_policy",
            )
        ),
        "pre_lifecycle_state": require_string(
            inputs["pre_lifecycle_state"],
            "inputs.pre_lifecycle_state",
        ),
        "pre_revision": require_nonnegative_int(
            inputs["pre_revision"],
            "inputs.pre_revision",
        ),
        "pre_fencing_token": (
            require_nonnegative_int(
                inputs["pre_fencing_token"],
                "inputs.pre_fencing_token",
            )
        ),
        "post_lifecycle_state": require_string(
            inputs["post_lifecycle_state"],
            "inputs.post_lifecycle_state",
        ),
        "post_revision": require_nonnegative_int(
            inputs["post_revision"],
            "inputs.post_revision",
        ),
        "post_fencing_token": (
            require_nonnegative_int(
                inputs["post_fencing_token"],
                "inputs.post_fencing_token",
            )
        ),
    }

    if (
        validated["post_revision"]
        != validated["pre_revision"] + 1
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-06 post_revision must advance "
                "exactly once"
            ),
        )

    if (
        validated["post_fencing_token"]
        != validated["pre_fencing_token"] + 1
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "SL-06 post_fencing_token must "
                "advance exactly once"
            ),
        )

    return validated


def validate_session_lease_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed SL-01 through SL-06 vector."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )

    if base_fixture_id == "SL-01":
        return _validate_sl01_inputs(
            raw_inputs
        )
    if base_fixture_id == "SL-02":
        return _validate_sl02_inputs(
            raw_inputs
        )
    if base_fixture_id == "SL-03":
        return _validate_sl03_inputs(
            raw_inputs
        )
    if base_fixture_id == "SL-04":
        return _validate_sl04_inputs(
            raw_inputs
        )
    if base_fixture_id == "SL-05":
        return _validate_sl05_inputs(
            raw_inputs
        )
    return _validate_sl06_inputs(
        raw_inputs
    )


def _validate_session_record(
    value: Any,
    path: str,
) -> dict[str, Any]:
    session = require_mapping(value, path)
    require_exact_fields(
        session,
        {
            "session_id",
            "binding",
            "binding_fingerprint",
            "lifecycle_state",
            "revision",
        },
        path=path,
    )

    return {
        "session_id": require_string(
            session["session_id"],
            f"{path}.session_id",
        ),
        "binding": _validate_binding(
            session["binding"],
            f"{path}.binding",
        ),
        "binding_fingerprint": require_string(
            session["binding_fingerprint"],
            f"{path}.binding_fingerprint",
        ),
        "lifecycle_state": _require_literal(
            session["lifecycle_state"],
            "ACTIVE",
            f"{path}.lifecycle_state",
        ),
        "revision": require_nonnegative_int(
            session["revision"],
            f"{path}.revision",
        ),
    }


def _validate_created_lease(
    value: Any,
    path: str,
) -> dict[str, Any]:
    lease = require_mapping(value, path)
    require_exact_fields(
        lease,
        {
            "lease_id",
            "session_id",
            "lease_kind",
            "owner_peer_id",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
            "fencing_token",
            "revision",
            "lifecycle_state",
        },
        path=path,
    )

    return {
        "lease_id": require_string(
            lease["lease_id"],
            f"{path}.lease_id",
        ),
        "session_id": require_string(
            lease["session_id"],
            f"{path}.session_id",
        ),
        "lease_kind": require_string(
            lease["lease_kind"],
            f"{path}.lease_kind",
        ),
        "owner_peer_id": require_string(
            lease["owner_peer_id"],
            f"{path}.owner_peer_id",
        ),
        "owner_principal_id": require_string(
            lease["owner_principal_id"],
            f"{path}.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            lease["owner_instance_id"],
            f"{path}.owner_instance_id",
        ),
        "owner_process_birth_identity": (
            require_string(
                lease[
                    "owner_process_birth_identity"
                ],
                (
                    f"{path}."
                    "owner_process_birth_identity"
                ),
            )
        ),
        "fencing_token": require_nonnegative_int(
            lease["fencing_token"],
            f"{path}.fencing_token",
        ),
        "revision": require_nonnegative_int(
            lease["revision"],
            f"{path}.revision",
        ),
        "lifecycle_state": _require_literal(
            lease["lifecycle_state"],
            "ACTIVE",
            f"{path}.lifecycle_state",
        ),
    }


def _validate_sl01_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "session",
            "lease",
        },
        path="output",
    )
    return {
        "session": _validate_session_record(
            output["session"],
            "output.session",
        ),
        "lease": _validate_created_lease(
            output["lease"],
            "output.lease",
        ),
    }


def _validate_sl02_output(
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
            "session_id",
            "lease_id",
            "owner_peer_id",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
            "fencing_token",
            "lifecycle_state",
            "stored_fingerprint",
            "resolved_fingerprint",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "COMPATIBLE_RESUME",
            "output.status",
        ),
        "session_id": require_string(
            output["session_id"],
            "output.session_id",
        ),
        "lease_id": require_string(
            output["lease_id"],
            "output.lease_id",
        ),
        "owner_peer_id": require_string(
            output["owner_peer_id"],
            "output.owner_peer_id",
        ),
        "owner_principal_id": require_string(
            output["owner_principal_id"],
            "output.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            output["owner_instance_id"],
            "output.owner_instance_id",
        ),
        "owner_process_birth_identity": require_string(
            output[
                "owner_process_birth_identity"
            ],
            (
                "output."
                "owner_process_birth_identity"
            ),
        ),
        "fencing_token": require_nonnegative_int(
            output["fencing_token"],
            "output.fencing_token",
        ),
        "lifecycle_state": _require_literal(
            output["lifecycle_state"],
            "ACTIVE",
            "output.lifecycle_state",
        ),
        "stored_fingerprint": require_string(
            output["stored_fingerprint"],
            "output.stored_fingerprint",
        ),
        "resolved_fingerprint": require_string(
            output["resolved_fingerprint"],
            "output.resolved_fingerprint",
        ),
    }


def _validate_sl03_output(
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
            "code",
            "prior_session_id",
            "mismatched_fields",
            "stored_fingerprint",
            "resolved_fingerprint",
            "disposition",
            "zero_state_mutations",
        },
        path="output",
    )

    return {
        "status": _require_literal(
            output["status"],
            "REJECTED",
            "output.status",
        ),
        "code": _require_literal(
            output["code"],
            "SESSION_BINDING_MISMATCH",
            "output.code",
        ),
        "prior_session_id": require_string(
            output["prior_session_id"],
            "output.prior_session_id",
        ),
        "mismatched_fields": _require_string_list(
            output["mismatched_fields"],
            "output.mismatched_fields",
        ),
        "stored_fingerprint": require_string(
            output["stored_fingerprint"],
            "output.stored_fingerprint",
        ),
        "resolved_fingerprint": require_string(
            output["resolved_fingerprint"],
            "output.resolved_fingerprint",
        ),
        "disposition": _require_literal(
            output["disposition"],
            "REJECTED",
            "output.disposition",
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
    }


def _validate_lease_result(
    value: Any,
    path: str,
) -> dict[str, Any]:
    lease = require_mapping(value, path)
    require_exact_fields(
        lease,
        {
            "lease_id",
            "owner_peer_id",
            "owner_principal_id",
            "owner_instance_id",
            "owner_process_birth_identity",
            "fencing_token",
            "revision",
            "lifecycle_state",
        },
        path=path,
    )

    return {
        "lease_id": require_string(
            lease["lease_id"],
            f"{path}.lease_id",
        ),
        "owner_peer_id": require_string(
            lease["owner_peer_id"],
            f"{path}.owner_peer_id",
        ),
        "owner_principal_id": require_string(
            lease["owner_principal_id"],
            f"{path}.owner_principal_id",
        ),
        "owner_instance_id": require_string(
            lease["owner_instance_id"],
            f"{path}.owner_instance_id",
        ),
        "owner_process_birth_identity": (
            require_string(
                lease[
                    "owner_process_birth_identity"
                ],
                (
                    f"{path}."
                    "owner_process_birth_identity"
                ),
            )
        ),
        "fencing_token": require_nonnegative_int(
            lease["fencing_token"],
            f"{path}.fencing_token",
        ),
        "revision": require_nonnegative_int(
            lease["revision"],
            f"{path}.revision",
        ),
        "lifecycle_state": _require_literal(
            lease["lifecycle_state"],
            "ACTIVE",
            f"{path}.lifecycle_state",
        ),
    }


def _validate_sl04_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "renewed_lease",
            "unchanged_lease",
        },
        path="output",
    )

    return {
        "renewed_lease": _validate_lease_result(
            output["renewed_lease"],
            "output.renewed_lease",
        ),
        "unchanged_lease": _validate_lease_result(
            output["unchanged_lease"],
            "output.unchanged_lease",
        ),
    }


def _validate_sl05_output(
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
            "code",
            "lease_id",
            "fencing_token",
            "revision",
            "zero_state_mutations",
        },
        path="output",
    )

    return {
        "status": _require_output_enum(
            output["status"],
            _SL05_STATUSES,
            "output.status",
        ),
        "code": _require_literal(
            output["code"],
            "LEASE_FENCE_VIOLATION",
            "output.code",
        ),
        "lease_id": require_string(
            output["lease_id"],
            "output.lease_id",
        ),
        "fencing_token": require_nonnegative_int(
            output["fencing_token"],
            "output.fencing_token",
        ),
        "revision": require_nonnegative_int(
            output["revision"],
            "output.revision",
        ),
        "zero_state_mutations": require_bool(
            output["zero_state_mutations"],
            "output.zero_state_mutations",
        ),
    }


def _validate_recovery_receipt(
    value: Any,
    path: str,
) -> dict[str, Any]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        {
            "session_id",
            "lease_id",
            "recovery_actor_principal_id",
            "trigger",
            "policy_id",
            "decision",
            "certainty_before_policy",
            "certainty_after_policy",
            "pre_lifecycle_state",
            "pre_revision",
            "pre_fencing_token",
            "post_lifecycle_state",
            "post_revision",
            "post_fencing_token",
        },
        path=path,
    )

    return {
        "session_id": require_string(
            receipt["session_id"],
            f"{path}.session_id",
        ),
        "lease_id": require_string(
            receipt["lease_id"],
            f"{path}.lease_id",
        ),
        "recovery_actor_principal_id": require_string(
            receipt[
                "recovery_actor_principal_id"
            ],
            (
                f"{path}."
                "recovery_actor_principal_id"
            ),
        ),
        "trigger": _require_output_enum(
            receipt["trigger"],
            _RECOVERY_TRIGGERS,
            f"{path}.trigger",
        ),
        "policy_id": require_string(
            receipt["policy_id"],
            f"{path}.policy_id",
        ),
        "decision": _require_output_enum(
            receipt["decision"],
            _RECOVERY_DECISIONS,
            f"{path}.decision",
        ),
        "certainty_before_policy": (
            _require_output_enum(
                receipt[
                    "certainty_before_policy"
                ],
                _LEASE_AUTHORITY_CERTAINTIES,
                f"{path}.certainty_before_policy",
            )
        ),
        "certainty_after_policy": (
            _require_output_enum(
                receipt[
                    "certainty_after_policy"
                ],
                _LEASE_AUTHORITY_CERTAINTIES,
                f"{path}.certainty_after_policy",
            )
        ),
        "pre_lifecycle_state": require_string(
            receipt["pre_lifecycle_state"],
            f"{path}.pre_lifecycle_state",
        ),
        "pre_revision": require_nonnegative_int(
            receipt["pre_revision"],
            f"{path}.pre_revision",
        ),
        "pre_fencing_token": require_nonnegative_int(
            receipt["pre_fencing_token"],
            f"{path}.pre_fencing_token",
        ),
        "post_lifecycle_state": require_string(
            receipt["post_lifecycle_state"],
            f"{path}.post_lifecycle_state",
        ),
        "post_revision": require_nonnegative_int(
            receipt["post_revision"],
            f"{path}.post_revision",
        ),
        "post_fencing_token": require_nonnegative_int(
            receipt["post_fencing_token"],
            f"{path}.post_fencing_token",
        ),
    }


def _validate_sl06_output(
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {"receipt"},
        path="output",
    )
    return {
        "receipt": _validate_recovery_receipt(
            output["receipt"],
            "output.receipt",
        )
    }


def validate_session_lease_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject session-lease output."""

    base_fixture_id = _base_fixture_id(
        fixture_id
    )

    if base_fixture_id == "SL-01":
        return _validate_sl01_output(
            raw_output
        )
    if base_fixture_id == "SL-02":
        return _validate_sl02_output(
            raw_output
        )
    if base_fixture_id == "SL-03":
        return _validate_sl03_output(
            raw_output
        )
    if base_fixture_id == "SL-04":
        return _validate_sl04_output(
            raw_output
        )
    if base_fixture_id == "SL-05":
        return _validate_sl05_output(
            raw_output
        )
    return _validate_sl06_output(
        raw_output
    )


def _mismatched_fields(
    stored_binding: Mapping[str, Any],
    resolved_binding: Mapping[str, Any],
) -> list[str]:
    return sorted(
        key
        for key in _BINDING_FIELDS
        if (
            stored_binding[key]
            != resolved_binding[key]
        )
    )


def _lease_result(
    lease: Mapping[str, Any],
    owner_peer_id: str,
    *,
    fencing_token: int,
    revision: int,
) -> dict[str, Any]:
    return {
        "lease_id": lease["lease_id"],
        "owner_peer_id": owner_peer_id,
        "owner_principal_id": lease[
            "owner_principal_id"
        ],
        "owner_instance_id": lease[
            "owner_instance_id"
        ],
        "owner_process_birth_identity": lease[
            "owner_process_birth_identity"
        ],
        "fencing_token": fencing_token,
        "revision": revision,
        "lifecycle_state": "ACTIVE",
    }


def _oracle_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "SL-01":
        binding = raw_inputs["binding"]
        return {
            "session": {
                "session_id": raw_inputs[
                    "session_id"
                ],
                "binding": dict(binding),
                "binding_fingerprint": fingerprint(
                    binding
                ),
                "lifecycle_state": "ACTIVE",
                "revision": 1,
            },
            "lease": {
                "lease_id": raw_inputs["lease_id"],
                "session_id": raw_inputs[
                    "session_id"
                ],
                "lease_kind": raw_inputs[
                    "lease_kind"
                ],
                "owner_peer_id": binding[
                    "peer_id"
                ],
                "owner_principal_id": raw_inputs[
                    "owner_principal_id"
                ],
                "owner_instance_id": raw_inputs[
                    "owner_instance_id"
                ],
                "owner_process_birth_identity": (
                    raw_inputs[
                        "owner_process_birth_identity"
                    ]
                ),
                "fencing_token": 1,
                "revision": 1,
                "lifecycle_state": "ACTIVE",
            },
        }

    if base_fixture_id == "SL-02":
        stored_binding = raw_inputs[
            "stored_binding"
        ]
        resolved_binding = raw_inputs[
            "resolved_binding"
        ]
        return {
            "status": "COMPATIBLE_RESUME",
            "session_id": raw_inputs[
                "stored_session_id"
            ],
            "lease_id": raw_inputs[
                "new_lease_id"
            ],
            "owner_peer_id": resolved_binding[
                "peer_id"
            ],
            "owner_principal_id": raw_inputs[
                "owner_principal_id"
            ],
            "owner_instance_id": raw_inputs[
                "owner_instance_id"
            ],
            "owner_process_birth_identity": (
                raw_inputs[
                    "owner_process_birth_identity"
                ]
            ),
            "fencing_token": 1,
            "lifecycle_state": "ACTIVE",
            "stored_fingerprint": fingerprint(
                stored_binding
            ),
            "resolved_fingerprint": fingerprint(
                resolved_binding
            ),
        }

    if base_fixture_id == "SL-03":
        stored_binding = raw_inputs[
            "stored_binding"
        ]
        resolved_binding = raw_inputs[
            "resolved_binding"
        ]
        return {
            "status": "REJECTED",
            "code": "SESSION_BINDING_MISMATCH",
            "prior_session_id": raw_inputs[
                "stored_session_id"
            ],
            "mismatched_fields": _mismatched_fields(
                stored_binding,
                resolved_binding,
            ),
            "stored_fingerprint": fingerprint(
                stored_binding
            ),
            "resolved_fingerprint": fingerprint(
                resolved_binding
            ),
            "disposition": "REJECTED",
            "zero_state_mutations": True,
        }

    if base_fixture_id == "SL-04":
        lease_a = raw_inputs["lease_a"]
        lease_b = raw_inputs["lease_b"]
        owner_peer_id = raw_inputs[
            "owner_peer_id"
        ]
        return {
            "renewed_lease": _lease_result(
                lease_a,
                owner_peer_id,
                fencing_token=(
                    lease_a["fencing_token"] + 1
                ),
                revision=lease_a["revision"] + 1,
            ),
            "unchanged_lease": _lease_result(
                lease_b,
                owner_peer_id,
                fencing_token=lease_b[
                    "fencing_token"
                ],
                revision=lease_b["revision"],
            ),
        }

    if base_fixture_id == "SL-05":
        persisted = raw_inputs["persisted"]
        return {
            "status": "REJECTED",
            "code": "LEASE_FENCE_VIOLATION",
            "lease_id": raw_inputs["lease_id"],
            "fencing_token": persisted[
                "fencing_token"
            ],
            "revision": persisted["revision"],
            "zero_state_mutations": True,
        }

    return {
        "receipt": {
            key: raw_inputs[key]
            for key in (
                "session_id",
                "lease_id",
                "recovery_actor_principal_id",
                "trigger",
                "policy_id",
                "decision",
                "certainty_before_policy",
                "certainty_after_policy",
                "pre_lifecycle_state",
                "pre_revision",
                "pre_fencing_token",
                "post_lifecycle_state",
                "post_revision",
                "post_fencing_token",
            )
        }
    }


def _subject_output(
    base_fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if base_fixture_id == "SL-01":
        binding = raw_inputs["binding"]
        session_id = raw_inputs["session_id"]
        lease_id = raw_inputs["lease_id"]
        session = {
            "session_id": session_id,
            "binding": {
                key: binding[key]
                for key in _BINDING_FIELDS
            },
            "binding_fingerprint": fingerprint(
                binding
            ),
            "lifecycle_state": "ACTIVE",
            "revision": 1,
        }
        lease = {
            "lease_id": lease_id,
            "session_id": session_id,
            "lease_kind": raw_inputs[
                "lease_kind"
            ],
            "owner_peer_id": binding["peer_id"],
            "owner_principal_id": raw_inputs[
                "owner_principal_id"
            ],
            "owner_instance_id": raw_inputs[
                "owner_instance_id"
            ],
            "owner_process_birth_identity": (
                raw_inputs[
                    "owner_process_birth_identity"
                ]
            ),
            "fencing_token": 1,
            "revision": 1,
            "lifecycle_state": "ACTIVE",
        }
        return {
            "session": session,
            "lease": lease,
        }

    if base_fixture_id == "SL-02":
        stored = raw_inputs["stored_binding"]
        resolved = raw_inputs["resolved_binding"]
        stored_digest = fingerprint(stored)
        resolved_digest = fingerprint(resolved)
        if (
            stored != resolved
            or stored_digest != resolved_digest
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "SL-02 bindings are incompatible",
            )

        return {
            "status": "COMPATIBLE_RESUME",
            "session_id": raw_inputs[
                "stored_session_id"
            ],
            "lease_id": raw_inputs[
                "new_lease_id"
            ],
            "owner_peer_id": resolved["peer_id"],
            "owner_principal_id": raw_inputs[
                "owner_principal_id"
            ],
            "owner_instance_id": raw_inputs[
                "owner_instance_id"
            ],
            "owner_process_birth_identity": (
                raw_inputs[
                    "owner_process_birth_identity"
                ]
            ),
            "fencing_token": 1,
            "lifecycle_state": "ACTIVE",
            "stored_fingerprint": stored_digest,
            "resolved_fingerprint": resolved_digest,
        }

    if base_fixture_id == "SL-03":
        stored = raw_inputs["stored_binding"]
        resolved = raw_inputs["resolved_binding"]
        differences = sorted(
            key
            for key in stored
            if stored[key] != resolved[key]
        )
        if not differences:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "SL-03 bindings unexpectedly match",
            )

        return {
            "status": "REJECTED",
            "code": "SESSION_BINDING_MISMATCH",
            "prior_session_id": raw_inputs[
                "stored_session_id"
            ],
            "mismatched_fields": differences,
            "stored_fingerprint": fingerprint(
                stored
            ),
            "resolved_fingerprint": fingerprint(
                resolved
            ),
            "disposition": "REJECTED",
            "zero_state_mutations": True,
        }

    if base_fixture_id == "SL-04":
        target_id = raw_inputs[
            "renew_target_lease_id"
        ]
        results: dict[str, dict[str, Any]] = {}

        for lease_name in ("lease_a", "lease_b"):
            lease = raw_inputs[lease_name]
            is_target = (
                lease["lease_id"] == target_id
            )
            results[lease_name] = _lease_result(
                lease,
                raw_inputs["owner_peer_id"],
                fencing_token=(
                    lease["fencing_token"]
                    + (1 if is_target else 0)
                ),
                revision=(
                    lease["revision"]
                    + (1 if is_target else 0)
                ),
            )

        return {
            "renewed_lease": results["lease_a"],
            "unchanged_lease": results["lease_b"],
        }

    if base_fixture_id == "SL-05":
        persisted = raw_inputs["persisted"]
        requester = raw_inputs["requester"]
        full_owner_match = bool(
            requester["asserted_peer_id"]
            == persisted["owner_peer_id"]
            and requester[
                "authenticated_principal_id"
            ]
            == persisted["owner_principal_id"]
            and requester["instance_id"]
            == persisted["owner_instance_id"]
            and requester[
                "process_birth_identity"
            ]
            == persisted[
                "owner_process_birth_identity"
            ]
        )
        if full_owner_match:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "SL-05 requester unexpectedly owns lease",
            )

        return {
            "status": "REJECTED",
            "code": "LEASE_FENCE_VIOLATION",
            "lease_id": raw_inputs["lease_id"],
            "fencing_token": persisted[
                "fencing_token"
            ],
            "revision": persisted["revision"],
            "zero_state_mutations": True,
        }

    receipt: dict[str, Any] = {}
    for field in (
        "session_id",
        "lease_id",
        "recovery_actor_principal_id",
        "trigger",
        "policy_id",
        "decision",
        "certainty_before_policy",
        "certainty_after_policy",
        "pre_lifecycle_state",
        "pre_revision",
        "pre_fencing_token",
        "post_lifecycle_state",
        "post_revision",
        "post_fencing_token",
    ):
        receipt[field] = raw_inputs[field]

    return {"receipt": receipt}


class SessionLeaseOracle:
    """Pure expected oracle for SL-01 through SL-06."""

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


class SessionLeaseSubjectAdapter:
    """Pure reference adapter over injected lease facts."""

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
            f"session_lease.{label}.reference"
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


class FaultInjectedSessionLeaseAdapter:
    """One isolated session-lease defect per negative fixture."""

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
            f"session_lease.{label}."
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

        if self._base == "SL-05":
            persisted = raw_inputs["persisted"]
            requester = raw_inputs["requester"]
            asserted_peer_matches = (
                requester["asserted_peer_id"]
                == persisted["owner_peer_id"]
            )
            if not asserted_peer_matches:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        "SL-05 fault requires a matching "
                        "asserted peer"
                    ),
                )
            return {
                "status": "ACCEPTED",
                "code": "LEASE_FENCE_VIOLATION",
                "lease_id": raw_inputs["lease_id"],
                "fencing_token": (
                    persisted["fencing_token"] + 1
                ),
                "revision": (
                    persisted["revision"] + 1
                ),
                "zero_state_mutations": False,
            }

        output = _subject_output(
            self._base,
            raw_inputs,
        )

        if self._base == "SL-01":
            output["lease"]["lease_id"] = (
                output["session"]["session_id"]
            )
        elif self._base == "SL-02":
            output["session_id"] = (
                "legacy-replacement-session-SL-02"
            )
        elif self._base == "SL-03":
            output["zero_state_mutations"] = False
        elif self._base == "SL-04":
            output["unchanged_lease"][
                "fencing_token"
            ] += 1
        else:
            output["receipt"][
                "post_fencing_token"
            ] = output["receipt"][
                "pre_fencing_token"
            ]

        return output


def session_lease_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all SL-01 through SL-06 registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = SessionLeaseOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=SessionLeaseSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_session_lease_inputs
                ),
                output_validator=(
                    validate_session_lease_output
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
                    FaultInjectedSessionLeaseAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_session_lease_inputs
                ),
                output_validator=(
                    validate_session_lease_output
                ),
            )
        )

    return tuple(registrations)
