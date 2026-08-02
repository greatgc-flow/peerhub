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

_BASE_FIXTURES = tuple(
    f"AC-05-{index:02d}"
    for index in range(1, 9)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-05-01": (
        "authority_json_custody.ac0501."
        "stable_declared_scope"
    ),
    "AC-05-02": (
        "authority_json_custody.ac0502."
        "changed_declared_file"
    ),
    "AC-05-03": (
        "authority_json_custody.ac0503."
        "write_before_receipt_crash"
    ),
    "AC-05-04": (
        "authority_json_custody.ac0504."
        "omitted_write_scope_entry"
    ),
    "AC-05-05": (
        "authority_json_custody.ac0505."
        "post_abort_write_fenced"
    ),
    "AC-05-06": (
        "authority_json_custody.ac0506."
        "file_share_delete_custody_failure"
    ),
    "AC-05-07": (
        "authority_json_custody.ac0507."
        "absent_path_namespace_failure"
    ),
    "AC-05-08": (
        "authority_json_custody.ac0508."
        "custody_unobtainable"
    ),
}

_DECISIONS = frozenset(
    {
        "PROCEED",
        "ABORT",
        "HOLD_FOR_RECONCILIATION",
    }
)
_DISPOSITIONS = frozenset(
    {
        "INPUTS_STABLE",
        "INPUT_DRIFT",
        "INCOMPLETE_SAFE",
        "WRITE_SCOPE_OMISSION",
        "POST_ABORT_WRITE_FENCED",
        "CUSTODY_UNPROVABLE",
    }
)
_ERROR_CODES = frozenset(
    {
        "CUTOVER_INPUT_DRIFT",
        "WRITE_SCOPE_NOT_QUIESCED",
    }
)
_CUSTODY_FAILURE_FACTS = frozenset(
    {
        "ALREADY_OPEN_FILE_SHARE_DELETE",
        "ABSENT_PATH_NAMESPACE_UNFENCED",
        "EXCLUSIVE_CUSTODY_UNOBTAINABLE",
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


def _require_optional_digest(
    value: Any,
    exists: bool,
    path: str,
) -> str | None:
    if exists:
        return require_string(value, path)

    if value is not None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} must be null "
                "when exists=false"
            ),
        )

    return None


def _validate_snapshot_entry(
    value: Any,
    path: str,
) -> dict[str, Any]:
    entry = require_mapping(value, path)
    require_exact_fields(
        entry,
        {
            "path",
            "exists",
            "digest",
        },
        path=path,
    )

    exists = require_bool(
        entry["exists"],
        f"{path}.exists",
    )

    return {
        "path": require_string(
            entry["path"],
            f"{path}.path",
        ),
        "exists": exists,
        "digest": _require_optional_digest(
            entry["digest"],
            exists,
            f"{path}.digest",
        ),
    }


def _validate_observed_write(
    value: Any,
    path: str,
) -> dict[str, Any]:
    write = require_mapping(value, path)
    require_exact_fields(
        write,
        {
            "path",
            "durable_terminal_receipt",
            "crashed_before_receipt",
        },
        path=path,
    )

    durable = require_bool(
        write["durable_terminal_receipt"],
        f"{path}.durable_terminal_receipt",
    )
    crashed = require_bool(
        write["crashed_before_receipt"],
        f"{path}.crashed_before_receipt",
    )

    if durable and crashed:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} cannot both have a "
                "durable receipt and crash "
                "before it"
            ),
        )

    return {
        "path": require_string(
            write["path"],
            f"{path}.path",
        ),
        "durable_terminal_receipt": durable,
        "crashed_before_receipt": crashed,
    }


def _validate_post_abort_attempt(
    value: Any,
    path: str,
) -> dict[str, bool]:
    attempt = require_mapping(value, path)
    require_exact_fields(
        attempt,
        {
            "lease_safely_aborted",
            "attempted",
            "effect_observed",
        },
        path=path,
    )

    aborted = require_bool(
        attempt["lease_safely_aborted"],
        f"{path}.lease_safely_aborted",
    )
    attempted = require_bool(
        attempt["attempted"],
        f"{path}.attempted",
    )
    effect = require_bool(
        attempt["effect_observed"],
        f"{path}.effect_observed",
    )

    if effect and not attempted:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.effect_observed "
                "requires attempted=true"
            ),
        )

    return {
        "lease_safely_aborted": aborted,
        "attempted": attempted,
        "effect_observed": effect,
    }


def _validate_custody_observation(
    value: Any,
    path: str,
) -> dict[str, Any]:
    custody = require_mapping(value, path)
    require_exact_fields(
        custody,
        {
            "object_exclusivity_provable",
            "namespace_custody_provable",
            "failure_facts",
        },
        path=path,
    )

    object_ok = require_bool(
        custody[
            "object_exclusivity_provable"
        ],
        (
            f"{path}."
            "object_exclusivity_provable"
        ),
    )
    namespace_ok = require_bool(
        custody[
            "namespace_custody_provable"
        ],
        (
            f"{path}."
            "namespace_custody_provable"
        ),
    )

    raw_facts = _require_list(
        custody["failure_facts"],
        f"{path}.failure_facts",
    )
    facts = [
        _require_enum(
            item,
            _CUSTODY_FAILURE_FACTS,
            f"{path}.failure_facts[{index}]",
        )
        for index, item in enumerate(
            raw_facts
        )
    ]

    if len(facts) != len(set(facts)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.failure_facts "
                "contains duplicates"
            ),
        )

    if object_ok and namespace_ok and facts:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.failure_facts must "
                "be empty when custody is "
                "provable"
            ),
        )

    if (
        not (object_ok and namespace_ok)
        and not facts
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path}.failure_facts is "
                "required when custody is "
                "unprovable"
            ),
        )

    return {
        "object_exclusivity_provable": (
            object_ok
        ),
        "namespace_custody_provable": (
            namespace_ok
        ),
        "failure_facts": facts,
    }


def _unique_paths(
    rows: list[Mapping[str, Any]],
    path: str,
) -> None:
    values = [
        str(row["path"])
        for row in rows
    ]

    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate paths",
        )


def _snapshot_map(
    rows: list[Mapping[str, Any]],
) -> dict[str, tuple[bool, str | None]]:
    return {
        str(row["path"]): (
            bool(row["exists"]),
            (
                None
                if row["digest"] is None
                else str(row["digest"])
            ),
        )
        for row in rows
    }


def _snapshots_equal(
    inputs: Mapping[str, Any],
) -> bool:
    return _snapshot_map(
        inputs["admission_snapshot"]
    ) == _snapshot_map(
        inputs["precommit_snapshot"]
    )


def validate_authority_json_custody_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed injected AC-05 schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )
    require_exact_fields(
        inputs,
        {
            "declared_write_scope",
            "admission_snapshot",
            "precommit_snapshot",
            "observed_legacy_writes",
            "post_abort_attempt",
            "custody_observation",
        },
        path="inputs",
    )

    raw_scope = _require_list(
        inputs["declared_write_scope"],
        "inputs.declared_write_scope",
    )
    scope = [
        require_string(
            value,
            (
                "inputs.declared_write_scope"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(
            raw_scope
        )
    ]

    if (
        not scope
        or len(scope) != len(set(scope))
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.declared_write_scope "
                "must be non-empty and unique"
            ),
        )

    admission = [
        _validate_snapshot_entry(
            value,
            (
                "inputs.admission_snapshot"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(
            _require_list(
                inputs["admission_snapshot"],
                "inputs.admission_snapshot",
            )
        )
    ]
    precommit = [
        _validate_snapshot_entry(
            value,
            (
                "inputs.precommit_snapshot"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(
            _require_list(
                inputs["precommit_snapshot"],
                "inputs.precommit_snapshot",
            )
        )
    ]
    writes = [
        _validate_observed_write(
            value,
            (
                "inputs.observed_legacy_writes"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(
            _require_list(
                inputs[
                    "observed_legacy_writes"
                ],
                (
                    "inputs."
                    "observed_legacy_writes"
                ),
            )
        )
    ]

    _unique_paths(
        admission,
        "inputs.admission_snapshot",
    )
    _unique_paths(
        precommit,
        "inputs.precommit_snapshot",
    )
    _unique_paths(
        writes,
        "inputs.observed_legacy_writes",
    )

    if set(scope) != {
        row["path"]
        for row in admission
    }:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.admission_snapshot "
                "must cover the declared scope "
                "exactly"
            ),
        )

    if set(scope) != {
        row["path"]
        for row in precommit
    }:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.precommit_snapshot "
                "must cover the declared scope "
                "exactly"
            ),
        )

    attempt = _validate_post_abort_attempt(
        inputs["post_abort_attempt"],
        "inputs.post_abort_attempt",
    )
    custody = _validate_custody_observation(
        inputs["custody_observation"],
        "inputs.custody_observation",
    )

    normalized = {
        "declared_write_scope": scope,
        "admission_snapshot": admission,
        "precommit_snapshot": precommit,
        "observed_legacy_writes": writes,
        "post_abort_attempt": attempt,
        "custody_observation": custody,
    }

    stable = _snapshots_equal(normalized)
    outside_scope = any(
        row["path"] not in set(scope)
        for row in writes
    )
    unreceipted_crash = any(
        (
            row["crashed_before_receipt"]
            and not row[
                "durable_terminal_receipt"
            ]
        )
        for row in writes
    )
    custody_ok = (
        custody[
            "object_exclusivity_provable"
        ]
        and custody[
            "namespace_custody_provable"
        ]
    )

    if base == "AC-05-01":
        valid = (
            stable
            and not writes
            and not attempt["attempted"]
            and custody_ok
            and len(scope) == 2
            and [
                row["path"]
                for row in admission
            ]
            != [
                row["path"]
                for row in precommit
            ]
        )
    elif base == "AC-05-02":
        valid = (
            not stable
            and not writes
            and not attempt["attempted"]
            and custody_ok
        )
    elif base == "AC-05-03":
        valid = (
            stable
            and unreceipted_crash
            and not outside_scope
            and not attempt["attempted"]
            and custody_ok
        )
    elif base == "AC-05-04":
        valid = (
            stable
            and outside_scope
            and not attempt["attempted"]
            and custody_ok
        )
    elif base == "AC-05-05":
        valid = (
            stable
            and not writes
            and attempt
            == {
                "lease_safely_aborted": True,
                "attempted": True,
                "effect_observed": False,
            }
            and custody_ok
        )
    else:
        required_fact = {
            "AC-05-06": (
                "ALREADY_OPEN_FILE_SHARE_DELETE"
            ),
            "AC-05-07": (
                "ABSENT_PATH_NAMESPACE_UNFENCED"
            ),
            "AC-05-08": (
                "EXCLUSIVE_CUSTODY_UNOBTAINABLE"
            ),
        }[base]

        valid = (
            stable
            and not writes
            and not attempt["attempted"]
            and not custody_ok
            and custody["failure_facts"]
            == [required_fact]
        )

        if base == "AC-05-07":
            valid = (
                valid
                and any(
                    not row["exists"]
                    for row in admission
                )
            )

    if not valid:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{base} does not encode "
                "its frozen proof vector"
            ),
        )

    return normalized


def _require_optional_error(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None

    return _require_enum(
        value,
        _ERROR_CODES,
        path,
        output=True,
    )


def validate_authority_json_custody_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and adapter AC-05 output."""

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
            "marker_eligible",
            "marker_writes",
            "legacy_write_mutations",
            "uncertain_effects",
            "reconciliation_required",
            "custody_verdict_consumed",
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
    error_code = _require_optional_error(
        output["error_code"],
        "output.error_code",
    )
    marker_eligible = require_bool(
        output["marker_eligible"],
        "output.marker_eligible",
    )

    if (
        decision == "PROCEED"
        and (
            error_code is not None
            or not marker_eligible
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "PROCEED requires "
                "marker_eligible=true "
                "and no error"
            ),
        )

    if (
        decision != "PROCEED"
        and marker_eligible
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "non-PROCEED output cannot "
                "be marker eligible"
            ),
        )

    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": _require_enum(
            output["disposition"],
            _DISPOSITIONS,
            "output.disposition",
            output=True,
        ),
        "marker_eligible": marker_eligible,
        "marker_writes": (
            require_nonnegative_int(
                output["marker_writes"],
                "output.marker_writes",
            )
        ),
        "legacy_write_mutations": (
            require_nonnegative_int(
                output[
                    "legacy_write_mutations"
                ],
                (
                    "output."
                    "legacy_write_mutations"
                ),
            )
        ),
        "uncertain_effects": require_bool(
            output["uncertain_effects"],
            "output.uncertain_effects",
        ),
        "reconciliation_required": (
            require_bool(
                output[
                    "reconciliation_required"
                ],
                (
                    "output."
                    "reconciliation_required"
                ),
            )
        ),
        "custody_verdict_consumed": (
            require_bool(
                output[
                    "custody_verdict_consumed"
                ],
                (
                    "output."
                    "custody_verdict_consumed"
                ),
            )
        ),
        "zero_provider_calls": require_bool(
            output["zero_provider_calls"],
            "output.zero_provider_calls",
        ),
    }


def _output(
    *,
    decision: str,
    error_code: str | None,
    disposition: str,
    marker_eligible: bool,
    legacy_write_mutations: int = 0,
    uncertain_effects: bool = False,
    reconciliation_required: bool = False,
    custody_verdict_consumed: bool = False,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": disposition,
        "marker_eligible": marker_eligible,
        "marker_writes": 0,
        "legacy_write_mutations": (
            legacy_write_mutations
        ),
        "uncertain_effects": uncertain_effects,
        "reconciliation_required": (
            reconciliation_required
        ),
        "custody_verdict_consumed": (
            custody_verdict_consumed
        ),
        "zero_provider_calls": True,
    }


class AuthorityJsonCustodyOracle:
    """Pure AC-05 oracle over injected evidence."""

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
                    f"oracle={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        scope = set(
            raw_inputs[
                "declared_write_scope"
            ]
        )

        if any(
            write["path"] not in scope
            for write in raw_inputs[
                "observed_legacy_writes"
            ]
        ):
            return _output(
                decision="ABORT",
                error_code=None,
                disposition=(
                    "WRITE_SCOPE_OMISSION"
                ),
                marker_eligible=False,
            )

        admission = _snapshot_map(
            raw_inputs["admission_snapshot"]
        )
        precommit = _snapshot_map(
            raw_inputs["precommit_snapshot"]
        )

        if admission != precommit:
            return _output(
                decision="ABORT",
                error_code=(
                    "CUTOVER_INPUT_DRIFT"
                ),
                disposition="INPUT_DRIFT",
                marker_eligible=False,
            )

        if any(
            not write[
                "durable_terminal_receipt"
            ]
            for write in raw_inputs[
                "observed_legacy_writes"
            ]
        ):
            return _output(
                decision=(
                    "HOLD_FOR_RECONCILIATION"
                ),
                error_code=None,
                disposition="INCOMPLETE_SAFE",
                marker_eligible=False,
                uncertain_effects=True,
                reconciliation_required=True,
            )

        custody = raw_inputs[
            "custody_observation"
        ]
        if not (
            custody[
                "object_exclusivity_provable"
            ]
            and custody[
                "namespace_custody_provable"
            ]
        ):
            return _output(
                decision="ABORT",
                error_code=(
                    "WRITE_SCOPE_NOT_QUIESCED"
                ),
                disposition=(
                    "CUSTODY_UNPROVABLE"
                ),
                marker_eligible=False,
                custody_verdict_consumed=True,
            )

        attempt = raw_inputs[
            "post_abort_attempt"
        ]
        if (
            attempt["lease_safely_aborted"]
            and attempt["attempted"]
        ):
            if attempt["effect_observed"]:
                return _output(
                    decision="ABORT",
                    error_code=(
                        "WRITE_SCOPE_NOT_QUIESCED"
                    ),
                    disposition=(
                        "CUSTODY_UNPROVABLE"
                    ),
                    marker_eligible=False,
                    legacy_write_mutations=1,
                    custody_verdict_consumed=True,
                )

            return _output(
                decision="PROCEED",
                error_code=None,
                disposition=(
                    "POST_ABORT_WRITE_FENCED"
                ),
                marker_eligible=True,
                custody_verdict_consumed=True,
            )

        return _output(
            decision="PROCEED",
            error_code=None,
            disposition="INPUTS_STABLE",
            marker_eligible=True,
            custody_verdict_consumed=True,
        )


class AuthorityJsonCustodySubjectAdapter:
    """Pure AC-05 adapter with no filesystem I/O."""

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
            "authority_json_custody."
            f"{label}.reference"
        )
        self.fixture_ids = frozenset(
            {base_fixture_id}
        )

    def _evaluate(
        self,
        facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        declared = set(
            facts["declared_write_scope"]
        )
        observed_paths = {
            write["path"]
            for write in facts[
                "observed_legacy_writes"
            ]
        }

        if not observed_paths.issubset(
            declared
        ):
            return _output(
                decision="ABORT",
                error_code=None,
                disposition=(
                    "WRITE_SCOPE_OMISSION"
                ),
                marker_eligible=False,
            )

        at_admission = {
            row["path"]: (
                row["exists"],
                row["digest"],
            )
            for row in facts[
                "admission_snapshot"
            ]
        }
        before_marker = {
            row["path"]: (
                row["exists"],
                row["digest"],
            )
            for row in facts[
                "precommit_snapshot"
            ]
        }

        if at_admission != before_marker:
            return _output(
                decision="ABORT",
                error_code=(
                    "CUTOVER_INPUT_DRIFT"
                ),
                disposition="INPUT_DRIFT",
                marker_eligible=False,
            )

        unresolved_write = False
        for write in facts[
            "observed_legacy_writes"
        ]:
            if (
                write[
                    "durable_terminal_receipt"
                ]
                is False
            ):
                unresolved_write = True

        if unresolved_write:
            return _output(
                decision=(
                    "HOLD_FOR_RECONCILIATION"
                ),
                error_code=None,
                disposition="INCOMPLETE_SAFE",
                marker_eligible=False,
                uncertain_effects=True,
                reconciliation_required=True,
            )

        custody = facts[
            "custody_observation"
        ]
        custody_confirmed = all(
            (
                custody[
                    "object_exclusivity_provable"
                ],
                custody[
                    "namespace_custody_provable"
                ],
            )
        )

        if not custody_confirmed:
            return _output(
                decision="ABORT",
                error_code=(
                    "WRITE_SCOPE_NOT_QUIESCED"
                ),
                disposition=(
                    "CUSTODY_UNPROVABLE"
                ),
                marker_eligible=False,
                custody_verdict_consumed=True,
            )

        attempt = facts[
            "post_abort_attempt"
        ]
        if (
            attempt["lease_safely_aborted"]
            and attempt["attempted"]
        ):
            if attempt["effect_observed"]:
                return _output(
                    decision="ABORT",
                    error_code=(
                        "WRITE_SCOPE_NOT_QUIESCED"
                    ),
                    disposition=(
                        "CUSTODY_UNPROVABLE"
                    ),
                    marker_eligible=False,
                    legacy_write_mutations=1,
                    custody_verdict_consumed=True,
                )

            return _output(
                decision="PROCEED",
                error_code=None,
                disposition=(
                    "POST_ABORT_WRITE_FENCED"
                ),
                marker_eligible=True,
                custody_verdict_consumed=True,
            )

        return _output(
            decision="PROCEED",
            error_code=None,
            disposition="INPUTS_STABLE",
            marker_eligible=True,
            custody_verdict_consumed=True,
        )

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
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        return self._evaluate(raw_inputs)


class FaultInjectedAuthorityJsonCustodyAdapter(
    AuthorityJsonCustodySubjectAdapter
):
    """One evidence-handling defect per negative."""

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
            "authority_json_custody."
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
                    f"adapter={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "AC-05-01":
            ordered_equal = all(
                (
                    left["path"]
                    == right["path"]
                    and left["exists"]
                    == right["exists"]
                    and left["digest"]
                    == right["digest"]
                )
                for left, right in zip(
                    raw_inputs[
                        "admission_snapshot"
                    ],
                    raw_inputs[
                        "precommit_snapshot"
                    ],
                    strict=True,
                )
            )

            if not ordered_equal:
                return _output(
                    decision="ABORT",
                    error_code=(
                        "CUTOVER_INPUT_DRIFT"
                    ),
                    disposition=(
                        "INPUT_DRIFT"
                    ),
                    marker_eligible=False,
                )

            return self._evaluate(raw_inputs)

        if self._base in {
            "AC-05-02",
            "AC-05-03",
            "AC-05-04",
        }:
            return _output(
                decision="PROCEED",
                error_code=None,
                disposition="INPUTS_STABLE",
                marker_eligible=True,
                custody_verdict_consumed=True,
            )

        if self._base == "AC-05-05":
            return _output(
                decision="PROCEED",
                error_code=None,
                disposition=(
                    "POST_ABORT_WRITE_FENCED"
                ),
                marker_eligible=True,
                legacy_write_mutations=1,
                custody_verdict_consumed=True,
            )

        return _output(
            decision="PROCEED",
            error_code=None,
            disposition="INPUTS_STABLE",
            marker_eligible=True,
            custody_verdict_consumed=False,
        )


def authority_json_custody_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable AC-05 registrations."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityJsonCustodyOracle(
            base_fixture_id
        )
        reference = (
            AuthorityJsonCustodySubjectAdapter(
                base_fixture_id
            )
        )
        fault = (
            FaultInjectedAuthorityJsonCustodyAdapter(
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
                adapter=reference,
                input_validator=(
                    validate_authority_json_custody_inputs
                ),
                output_validator=(
                    validate_authority_json_custody_output
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
                adapter=fault,
                input_validator=(
                    validate_authority_json_custody_inputs
                ),
                output_validator=(
                    validate_authority_json_custody_output
                ),
            )
        )

    return tuple(registrations)