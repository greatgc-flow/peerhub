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

_BASE_FIXTURES = tuple(
    f"AC-02-{index:02d}"
    for index in range(1, 6)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-02-01": (
        "authority_identity.ac0201.matching_identity"
    ),
    "AC-02-02": (
        "authority_identity.ac0202.resolved_identity_mismatch"
    ),
    "AC-02-03": (
        "authority_identity.ac0203.copied_home_rejection"
    ),
    "AC-02-04": (
        "authority_identity.ac0204.audited_relocation_import"
    ),
    "AC-02-05": (
        "authority_identity.ac0205.home_id_collision"
    ),
}

_OPERATIONS = frozenset(
    {
        "OPEN",
        "RELOCATION_IMPORT",
    }
)
_OPEN_CONTEXTS = frozenset(
    {
        "NORMAL",
        "COPIED_DIRECTORY",
    }
)
_DECISIONS = frozenset(
    {
        "ACCEPTED",
        "REJECTED",
    }
)
_DISPOSITIONS = frozenset(
    {
        "IDENTITY_CONFIRMED",
        "IDENTITY_MISMATCH",
        "EXPLICIT_RELOCATION_REQUIRED",
        "RELOCATION_IMPORTED",
        "RELOCATION_RECEIPT_INVALID",
        "HOME_ID_COLLISION",
    }
)


def _base_fixture_id(
    fixture_id: str,
) -> str:
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


def _require_optional_string(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None

    return require_string(value, path)


def _validate_identity(
    value: Any,
    path: str,
) -> dict[str, str]:
    identity = require_mapping(value, path)
    require_exact_fields(
        identity,
        {
            "volume_guid",
            "file_id",
        },
        path=path,
    )

    return {
        "volume_guid": require_string(
            identity["volume_guid"],
            f"{path}.volume_guid",
        ),
        "file_id": require_string(
            identity["file_id"],
            f"{path}.file_id",
        ),
    }


def _validate_binding(
    value: Any,
    path: str,
) -> dict[str, Any]:
    binding = require_mapping(value, path)
    require_exact_fields(
        binding,
        {
            "workspace_home_id",
            "database_identity",
            "resolved_identity",
            "home_content_digest",
            "recorded_presented_path",
        },
        path=path,
    )

    return {
        "workspace_home_id": require_string(
            binding["workspace_home_id"],
            f"{path}.workspace_home_id",
        ),
        "database_identity": require_string(
            binding["database_identity"],
            f"{path}.database_identity",
        ),
        "resolved_identity": _validate_identity(
            binding["resolved_identity"],
            f"{path}.resolved_identity",
        ),
        "home_content_digest": require_string(
            binding["home_content_digest"],
            f"{path}.home_content_digest",
        ),
        "recorded_presented_path": require_string(
            binding["recorded_presented_path"],
            f"{path}.recorded_presented_path",
        ),
    }


def _validate_observed_home(
    value: Any,
    path: str,
) -> dict[str, Any]:
    observed = require_mapping(value, path)
    require_exact_fields(
        observed,
        {
            "workspace_home_id",
            "database_identity",
            "resolved_identity",
            "home_content_digest",
            "presented_path",
        },
        path=path,
    )

    return {
        "workspace_home_id": require_string(
            observed["workspace_home_id"],
            f"{path}.workspace_home_id",
        ),
        "database_identity": require_string(
            observed["database_identity"],
            f"{path}.database_identity",
        ),
        "resolved_identity": _validate_identity(
            observed["resolved_identity"],
            f"{path}.resolved_identity",
        ),
        "home_content_digest": require_string(
            observed["home_content_digest"],
            f"{path}.home_content_digest",
        ),
        "presented_path": require_string(
            observed["presented_path"],
            f"{path}.presented_path",
        ),
    }


def _validate_receipt(
    value: Any,
    path: str,
) -> dict[str, Any]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        {
            "receipt_id",
            "source_workspace_home_id",
            "source_database_identity",
            "source_identity",
            "target_identity",
            "export_digest",
            "import_digest",
            "audited_export",
        },
        path=path,
    )

    return {
        "receipt_id": require_string(
            receipt["receipt_id"],
            f"{path}.receipt_id",
        ),
        "source_workspace_home_id": require_string(
            receipt["source_workspace_home_id"],
            f"{path}.source_workspace_home_id",
        ),
        "source_database_identity": require_string(
            receipt["source_database_identity"],
            f"{path}.source_database_identity",
        ),
        "source_identity": _validate_identity(
            receipt["source_identity"],
            f"{path}.source_identity",
        ),
        "target_identity": _validate_identity(
            receipt["target_identity"],
            f"{path}.target_identity",
        ),
        "export_digest": require_string(
            receipt["export_digest"],
            f"{path}.export_digest",
        ),
        "import_digest": require_string(
            receipt["import_digest"],
            f"{path}.import_digest",
        ),
        "audited_export": require_bool(
            receipt["audited_export"],
            f"{path}.audited_export",
        ),
    }


def validate_authority_identity_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed, injected AC-02 schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "operation",
            "open_context",
            "known_bindings",
            "observed_home",
            "relocation_receipt",
        },
        path="inputs",
    )

    operation = _require_enum(
        inputs["operation"],
        _OPERATIONS,
        "inputs.operation",
    )
    open_context = _require_enum(
        inputs["open_context"],
        _OPEN_CONTEXTS,
        "inputs.open_context",
    )

    raw_bindings = _require_list(
        inputs["known_bindings"],
        "inputs.known_bindings",
    )
    if not raw_bindings:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.known_bindings "
                "must be non-empty"
            ),
        )

    bindings = [
        _validate_binding(
            value,
            f"inputs.known_bindings[{index}]",
        )
        for index, value in enumerate(
            raw_bindings
        )
    ]
    observed = _validate_observed_home(
        inputs["observed_home"],
        "inputs.observed_home",
    )

    raw_receipt = inputs[
        "relocation_receipt"
    ]
    receipt = (
        None
        if raw_receipt is None
        else _validate_receipt(
            raw_receipt,
            "inputs.relocation_receipt",
        )
    )

    if base == "AC-02-04":
        if (
            operation != "RELOCATION_IMPORT"
            or open_context != "NORMAL"
            or receipt is None
            or len(bindings) != 1
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-02-04 requires one binding "
                    "and an explicit relocation "
                    "import receipt"
                ),
            )
    else:
        expected_context = (
            "COPIED_DIRECTORY"
            if base == "AC-02-03"
            else "NORMAL"
        )
        expected_count = (
            2
            if base == "AC-02-05"
            else 1
        )

        if (
            operation != "OPEN"
            or open_context != expected_context
            or receipt is not None
            or len(bindings) != expected_count
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{base} requires "
                    f"OPEN/{expected_context}, "
                    f"{expected_count} binding(s), "
                    "and no relocation receipt"
                ),
            )

    return {
        "operation": operation,
        "open_context": open_context,
        "known_bindings": bindings,
        "observed_home": observed,
        "relocation_receipt": receipt,
    }


def _validate_audit_record(
    value: Any,
    path: str,
) -> dict[str, Any]:
    record = require_mapping(value, path)
    require_exact_fields(
        record,
        {
            "action",
            "receipt_id",
            "workspace_home_id",
            "database_identity",
            "prior_identity",
            "new_identity",
            "export_digest",
            "import_digest",
        },
        path=path,
    )

    action = require_string(
        record["action"],
        f"{path}.action",
    )
    if action != "RELOCATION_IMPORT":
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.action "
                f"unsupported={action}"
            ),
        )

    return {
        "action": action,
        "receipt_id": require_string(
            record["receipt_id"],
            f"{path}.receipt_id",
        ),
        "workspace_home_id": require_string(
            record["workspace_home_id"],
            f"{path}.workspace_home_id",
        ),
        "database_identity": require_string(
            record["database_identity"],
            f"{path}.database_identity",
        ),
        "prior_identity": _validate_identity(
            record["prior_identity"],
            f"{path}.prior_identity",
        ),
        "new_identity": _validate_identity(
            record["new_identity"],
            f"{path}.new_identity",
        ),
        "export_digest": require_string(
            record["export_digest"],
            f"{path}.export_digest",
        ),
        "import_digest": require_string(
            record["import_digest"],
            f"{path}.import_digest",
        ),
    }


def validate_authority_identity_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and adapter AC-02 output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "decision",
            "error_code",
            "disposition",
            "active_binding",
            "audit_records",
            "zero_operational_state_opens",
            "zero_binding_mutations",
            "zero_audit_writes",
            "zero_legacy_mutations",
            "zero_provider_calls",
        },
        path="output",
    )

    decision = _require_enum(
        output["decision"],
        _DECISIONS,
        "output.decision",
        output=True,
    )
    error_code = _require_optional_string(
        output["error_code"],
        "output.error_code",
    )
    if error_code not in {
        None,
        "WORKSPACE_IDENTITY_MISMATCH",
    }:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.error_code "
                f"unsupported={error_code}"
            ),
        )

    if (
        (
            decision == "ACCEPTED"
            and error_code is not None
        )
        or (
            decision == "REJECTED"
            and error_code
            != "WORKSPACE_IDENTITY_MISMATCH"
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output decision/error_code "
                "combination is invalid"
            ),
        )

    raw_active = output["active_binding"]
    active = (
        None
        if raw_active is None
        else _validate_binding(
            raw_active,
            "output.active_binding",
        )
    )

    raw_audits = _require_list(
        output["audit_records"],
        "output.audit_records",
        output=True,
    )
    audits = [
        _validate_audit_record(
            value,
            f"output.audit_records[{index}]",
        )
        for index, value in enumerate(
            raw_audits
        )
    ]

    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": _require_enum(
            output["disposition"],
            _DISPOSITIONS,
            "output.disposition",
            output=True,
        ),
        "active_binding": active,
        "audit_records": audits,
        "zero_operational_state_opens": (
            require_bool(
                output[
                    "zero_operational_state_opens"
                ],
                (
                    "output."
                    "zero_operational_state_opens"
                ),
            )
        ),
        "zero_binding_mutations": require_bool(
            output["zero_binding_mutations"],
            "output.zero_binding_mutations",
        ),
        "zero_audit_writes": require_bool(
            output["zero_audit_writes"],
            "output.zero_audit_writes",
        ),
        "zero_legacy_mutations": require_bool(
            output["zero_legacy_mutations"],
            "output.zero_legacy_mutations",
        ),
        "zero_provider_calls": require_bool(
            output["zero_provider_calls"],
            "output.zero_provider_calls",
        ),
    }


def _identity_key(
    identity: Mapping[str, Any],
) -> tuple[str, str]:
    return (
        str(identity["volume_guid"]),
        str(identity["file_id"]),
    )


def _binding_from_observed(
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "workspace_home_id": (
            observed["workspace_home_id"]
        ),
        "database_identity": (
            observed["database_identity"]
        ),
        "resolved_identity": dict(
            observed["resolved_identity"]
        ),
        "home_content_digest": (
            observed["home_content_digest"]
        ),
        "recorded_presented_path": (
            observed["presented_path"]
        ),
    }


class AuthorityIdentityOracle:
    """Pure AC-02 oracle over injected observations."""

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

    def _has_collision(
        self,
        bindings: list[
            Mapping[str, Any]
        ],
    ) -> bool:
        claims: dict[
            str,
            set[tuple[str, str, str]],
        ] = {}

        for binding in bindings:
            identity = _identity_key(
                binding["resolved_identity"]
            )
            claims.setdefault(
                binding["workspace_home_id"],
                set(),
            ).add(
                (
                    binding["database_identity"],
                    identity[0],
                    identity[1],
                )
            )

        return any(
            len(signatures) > 1
            for signatures in claims.values()
        )

    def _reject(
        self,
        disposition: str,
    ) -> dict[str, Any]:
        return {
            "decision": "REJECTED",
            "error_code": (
                "WORKSPACE_IDENTITY_MISMATCH"
            ),
            "disposition": disposition,
            "active_binding": None,
            "audit_records": [],
            "zero_operational_state_opens": True,
            "zero_binding_mutations": True,
            "zero_audit_writes": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _open(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        bindings = inputs["known_bindings"]
        observed = inputs["observed_home"]

        same_home = [
            binding
            for binding in bindings
            if binding["workspace_home_id"]
            == observed["workspace_home_id"]
        ]
        identity = _identity_key(
            observed["resolved_identity"]
        )

        identity_owned_elsewhere = any(
            (
                _identity_key(
                    binding["resolved_identity"]
                )
                == identity
                and (
                    binding["workspace_home_id"]
                    != observed["workspace_home_id"]
                    or binding["database_identity"]
                    != observed["database_identity"]
                )
            )
            for binding in bindings
        )

        matched = (
            len(same_home) == 1
            and same_home[0][
                "database_identity"
            ]
            == observed["database_identity"]
            and _identity_key(
                same_home[0]["resolved_identity"]
            )
            == identity
            and not identity_owned_elsewhere
        )

        if not matched:
            disposition = (
                "EXPLICIT_RELOCATION_REQUIRED"
                if inputs["open_context"]
                == "COPIED_DIRECTORY"
                else "IDENTITY_MISMATCH"
            )
            return self._reject(disposition)

        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "disposition": (
                "IDENTITY_CONFIRMED"
            ),
            "active_binding": dict(
                same_home[0]
            ),
            "audit_records": [],
            "zero_operational_state_opens": False,
            "zero_binding_mutations": True,
            "zero_audit_writes": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _relocate(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        bindings = inputs["known_bindings"]
        observed = inputs["observed_home"]
        receipt = inputs["relocation_receipt"]
        source = bindings[0]

        target_owned_elsewhere = any(
            (
                _identity_key(
                    binding["resolved_identity"]
                )
                == _identity_key(
                    observed["resolved_identity"]
                )
                and binding["workspace_home_id"]
                != observed["workspace_home_id"]
            )
            for binding in bindings
        )

        receipt_valid = (
            receipt is not None
            and receipt["audited_export"]
            and receipt[
                "source_workspace_home_id"
            ]
            == source["workspace_home_id"]
            and receipt[
                "source_database_identity"
            ]
            == source["database_identity"]
            and _identity_key(
                receipt["source_identity"]
            )
            == _identity_key(
                source["resolved_identity"]
            )
            and receipt["target_identity"]
            == observed["resolved_identity"]
            and observed["workspace_home_id"]
            == source["workspace_home_id"]
            and observed["database_identity"]
            == source["database_identity"]
            and receipt["export_digest"]
            == source["home_content_digest"]
            and receipt["import_digest"]
            == observed["home_content_digest"]
            and not target_owned_elsewhere
        )

        if not receipt_valid:
            return self._reject(
                "RELOCATION_RECEIPT_INVALID"
            )

        active = _binding_from_observed(
            observed
        )
        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "disposition": (
                "RELOCATION_IMPORTED"
            ),
            "active_binding": active,
            "audit_records": [
                {
                    "action": (
                        "RELOCATION_IMPORT"
                    ),
                    "receipt_id": (
                        receipt["receipt_id"]
                    ),
                    "workspace_home_id": (
                        observed[
                            "workspace_home_id"
                        ]
                    ),
                    "database_identity": (
                        observed[
                            "database_identity"
                        ]
                    ),
                    "prior_identity": dict(
                        source[
                            "resolved_identity"
                        ]
                    ),
                    "new_identity": dict(
                        observed[
                            "resolved_identity"
                        ]
                    ),
                    "export_digest": (
                        receipt["export_digest"]
                    ),
                    "import_digest": (
                        receipt["import_digest"]
                    ),
                }
            ],
            "zero_operational_state_opens": True,
            "zero_binding_mutations": False,
            "zero_audit_writes": False,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

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

        if self._has_collision(
            raw_inputs["known_bindings"]
        ):
            return self._reject(
                "HOME_ID_COLLISION"
            )

        if raw_inputs["operation"] == "OPEN":
            return self._open(raw_inputs)

        return self._relocate(raw_inputs)


class AuthorityIdentitySubjectAdapter:
    """Pure reference AC-02 adapter with no I/O."""

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
            "authority_identity."
            f"{label}.reference"
        )
        self.fixture_ids = frozenset(
            {base_fixture_id}
        )

    def _detect_duplicate_home_claims(
        self,
        records: list[
            Mapping[str, Any]
        ],
    ) -> bool:
        first_claim: dict[
            str,
            tuple[
                str,
                tuple[str, str],
            ],
        ] = {}

        for record in records:
            signature = (
                record["database_identity"],
                _identity_key(
                    record["resolved_identity"]
                ),
            )
            prior = first_claim.get(
                record["workspace_home_id"]
            )

            if (
                prior is not None
                and prior != signature
            ):
                return True

            first_claim[
                record["workspace_home_id"]
            ] = signature

        return False

    def _deny(
        self,
        disposition: str,
    ) -> dict[str, Any]:
        return {
            "decision": "REJECTED",
            "error_code": (
                "WORKSPACE_IDENTITY_MISMATCH"
            ),
            "disposition": disposition,
            "active_binding": None,
            "audit_records": [],
            "zero_operational_state_opens": True,
            "zero_binding_mutations": True,
            "zero_audit_writes": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _admit_open(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        observation = inputs["observed_home"]
        candidate: Mapping[
            str,
            Any,
        ] | None = None

        for stored in inputs["known_bindings"]:
            if (
                stored["workspace_home_id"]
                == observation[
                    "workspace_home_id"
                ]
            ):
                candidate = stored
                break

        bound_identity = (
            candidate is not None
            and candidate["database_identity"]
            == observation[
                "database_identity"
            ]
            and candidate["resolved_identity"]
            == observation[
                "resolved_identity"
            ]
        )

        conflicting_owner = False
        for stored in inputs["known_bindings"]:
            if (
                stored["resolved_identity"]
                == observation[
                    "resolved_identity"
                ]
                and (
                    stored["workspace_home_id"]
                    != observation[
                        "workspace_home_id"
                    ]
                    or stored[
                        "database_identity"
                    ]
                    != observation[
                        "database_identity"
                    ]
                )
            ):
                conflicting_owner = True

        if (
            not bound_identity
            or conflicting_owner
        ):
            disposition = (
                "EXPLICIT_RELOCATION_REQUIRED"
                if inputs["open_context"]
                == "COPIED_DIRECTORY"
                else "IDENTITY_MISMATCH"
            )
            return self._deny(disposition)

        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "disposition": (
                "IDENTITY_CONFIRMED"
            ),
            "active_binding": dict(candidate),
            "audit_records": [],
            "zero_operational_state_opens": False,
            "zero_binding_mutations": True,
            "zero_audit_writes": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _apply_import(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        old = inputs["known_bindings"][0]
        new = inputs["observed_home"]
        proof = inputs["relocation_receipt"]

        acceptable = (
            proof is not None
            and all(
                (
                    proof["audited_export"]
                    is True,
                    proof[
                        "source_workspace_home_id"
                    ]
                    == old["workspace_home_id"],
                    proof[
                        "source_database_identity"
                    ]
                    == old["database_identity"],
                    proof["source_identity"]
                    == old["resolved_identity"],
                    proof["target_identity"]
                    == new["resolved_identity"],
                    new["workspace_home_id"]
                    == old["workspace_home_id"],
                    new["database_identity"]
                    == old["database_identity"],
                    proof["export_digest"]
                    == old[
                        "home_content_digest"
                    ],
                    proof["import_digest"]
                    == new[
                        "home_content_digest"
                    ],
                )
            )
        )

        for existing in inputs[
            "known_bindings"
        ]:
            if (
                existing["resolved_identity"]
                == new["resolved_identity"]
                and existing[
                    "workspace_home_id"
                ]
                != new["workspace_home_id"]
            ):
                acceptable = False

        if not acceptable:
            return self._deny(
                "RELOCATION_RECEIPT_INVALID"
            )

        rebound = _binding_from_observed(new)
        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "disposition": (
                "RELOCATION_IMPORTED"
            ),
            "active_binding": rebound,
            "audit_records": [
                {
                    "action": (
                        "RELOCATION_IMPORT"
                    ),
                    "receipt_id": (
                        proof["receipt_id"]
                    ),
                    "workspace_home_id": (
                        new["workspace_home_id"]
                    ),
                    "database_identity": (
                        new["database_identity"]
                    ),
                    "prior_identity": dict(
                        old["resolved_identity"]
                    ),
                    "new_identity": dict(
                        new["resolved_identity"]
                    ),
                    "export_digest": (
                        proof["export_digest"]
                    ),
                    "import_digest": (
                        proof["import_digest"]
                    ),
                }
            ],
            "zero_operational_state_opens": True,
            "zero_binding_mutations": False,
            "zero_audit_writes": False,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _evaluate(
        self,
        raw_inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._detect_duplicate_home_claims(
            raw_inputs["known_bindings"]
        ):
            return self._deny(
                "HOME_ID_COLLISION"
            )

        if raw_inputs["operation"] == "OPEN":
            return self._admit_open(raw_inputs)

        return self._apply_import(raw_inputs)

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id not in self.fixture_ids:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        return self._evaluate(raw_inputs)


class FaultInjectedAuthorityIdentityAdapter(
    AuthorityIdentitySubjectAdapter
):
    """A specific AC-02 defect for each negative vector."""

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        super().__init__(base_fixture_id)
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            "authority_identity."
            f"{label}.fault_injected"
        )
        self.fixture_ids = frozenset(
            {
                (
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                )
            }
        )

    def _unsafe_open(
        self,
        observed: Mapping[str, Any],
        *,
        binding_mutated: bool,
    ) -> dict[str, Any]:
        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "disposition": (
                "IDENTITY_CONFIRMED"
            ),
            "active_binding": (
                _binding_from_observed(
                    observed
                )
            ),
            "audit_records": [],
            "zero_operational_state_opens": False,
            "zero_binding_mutations": (
                not binding_mutated
            ),
            "zero_audit_writes": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id not in self.fixture_ids:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        observed = raw_inputs["observed_home"]
        bindings = raw_inputs[
            "known_bindings"
        ]

        if self._base == "AC-02-01":
            stored = bindings[0]
            if (
                stored[
                    "recorded_presented_path"
                ]
                != observed["presented_path"]
            ):
                return self._deny(
                    "IDENTITY_MISMATCH"
                )

            return self._evaluate(raw_inputs)

        if self._base == "AC-02-02":
            stored = bindings[0]
            if (
                stored[
                    "recorded_presented_path"
                ]
                == observed["presented_path"]
            ):
                return self._unsafe_open(
                    observed,
                    binding_mutated=False,
                )

            return self._evaluate(raw_inputs)

        if self._base == "AC-02-03":
            stored = bindings[0]
            if (
                stored["home_content_digest"]
                == observed[
                    "home_content_digest"
                ]
            ):
                return self._unsafe_open(
                    observed,
                    binding_mutated=False,
                )

            return self._evaluate(raw_inputs)

        if self._base == "AC-02-04":
            actual = dict(
                self._evaluate(raw_inputs)
            )
            actual["audit_records"] = []
            actual["zero_audit_writes"] = True
            return actual

        return self._unsafe_open(
            observed,
            binding_mutated=True,
        )


def authority_identity_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in AC-02 rows."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityIdentityOracle(
            base_fixture_id
        )
        positive_adapter = (
            AuthorityIdentitySubjectAdapter(
                base_fixture_id
            )
        )
        negative_adapter = (
            FaultInjectedAuthorityIdentityAdapter(
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
                    validate_authority_identity_inputs
                ),
                output_validator=(
                    validate_authority_identity_output
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
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=negative_adapter,
                input_validator=(
                    validate_authority_identity_inputs
                ),
                output_validator=(
                    validate_authority_identity_output
                ),
            )
        )

    return tuple(registrations)