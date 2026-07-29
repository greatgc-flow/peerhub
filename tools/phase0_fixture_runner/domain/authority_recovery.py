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
    f"AC-07-{index:02d}"
    for index in range(1, 7)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-07-01": (
        "authority_recovery.ac0701.pre_marker_failure"
    ),
    "AC-07-02": (
        "authority_recovery."
        "ac0702.post_marker_receipt_recovery"
    ),
    "AC-07-03": (
        "authority_recovery.ac0703.receipt_corruption"
    ),
    "AC-07-04": (
        "authority_recovery."
        "ac0704.backup_digest_mismatch"
    ),
    "AC-07-05": (
        "authority_recovery."
        "ac0705.inverse_fenced_rollback"
    ),
    "AC-07-06": (
        "authority_recovery."
        "ac0706.peerhub_era_writes_refusal"
    ),
}

_RECOVERY_KINDS = frozenset(
    {
        "FORWARD_RECOVERY",
        "ROLLBACK",
    }
)
_FAILURE_POSITIONS = frozenset(
    {
        "NONE",
        "PRE_MARKER",
        "POST_MARKER",
    }
)
_AUTHORITIES = frozenset(
    {
        "ENGRAM",
        "PEERHUB",
    }
)
_ARTIFACT_KINDS = frozenset(
    {
        "BACKUP",
        "STAGING",
    }
)
_DECISIONS = frozenset(
    {
        "STOPPED_SAFE",
        "RECOVERED",
        "REFUSED",
        "ROLLED_BACK",
    }
)
_DISPOSITIONS = frozenset(
    {
        "PRE_MARKER_ATTEMPT_PRESERVED",
        "POST_MARKER_RECEIPT_RESTORE",
        "RECEIPT_INTEGRITY_INVALID",
        "RESTORE_ARTIFACT_DIGEST_MISMATCH",
        "INVERSE_FENCED_ROLLBACK_COMMITTED",
        "PEERHUB_ERA_WRITES_PRESENT",
        "POST_MARKER_BEST_EFFORT_REPLAY",
        "DIRECT_UNFENCED_ROLLBACK",
    }
)
_ERROR_CODES = frozenset(
    {
        "RECEIPT_INTEGRITY_INVALID",
        "RESTORE_ARTIFACT_DIGEST_MISMATCH",
        "PEERHUB_ERA_WRITES_PRESENT",
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


def _validate_transition_receipt(
    value: Any,
    path: str,
) -> dict[str, Any]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        {
            "receipt_id",
            "attempt_id",
            "recorded_receipt_digest",
            "observed_receipt_digest",
            "restore_artifact_id",
            "restore_artifact_digest",
            "peerhub_commit_watermark",
            "forward_epoch",
        },
        path=path,
    )

    return {
        "receipt_id": require_string(
            receipt["receipt_id"],
            f"{path}.receipt_id",
        ),
        "attempt_id": require_string(
            receipt["attempt_id"],
            f"{path}.attempt_id",
        ),
        "recorded_receipt_digest": require_string(
            receipt["recorded_receipt_digest"],
            f"{path}.recorded_receipt_digest",
        ),
        "observed_receipt_digest": require_string(
            receipt["observed_receipt_digest"],
            f"{path}.observed_receipt_digest",
        ),
        "restore_artifact_id": require_string(
            receipt["restore_artifact_id"],
            f"{path}.restore_artifact_id",
        ),
        "restore_artifact_digest": require_string(
            receipt["restore_artifact_digest"],
            f"{path}.restore_artifact_digest",
        ),
        "peerhub_commit_watermark": (
            require_nonnegative_int(
                receipt["peerhub_commit_watermark"],
                f"{path}.peerhub_commit_watermark",
            )
        ),
        "forward_epoch": require_nonnegative_int(
            receipt["forward_epoch"],
            f"{path}.forward_epoch",
        ),
    }


def _validate_restore_artifact(
    value: Any,
    path: str,
) -> dict[str, Any]:
    artifact = require_mapping(value, path)
    require_exact_fields(
        artifact,
        {
            "artifact_id",
            "artifact_kind",
            "observed_digest",
            "custody_verified",
        },
        path=path,
    )

    return {
        "artifact_id": require_string(
            artifact["artifact_id"],
            f"{path}.artifact_id",
        ),
        "artifact_kind": _require_enum(
            artifact["artifact_kind"],
            _ARTIFACT_KINDS,
            f"{path}.artifact_kind",
        ),
        "observed_digest": require_string(
            artifact["observed_digest"],
            f"{path}.observed_digest",
        ),
        "custody_verified": require_bool(
            artifact["custody_verified"],
            f"{path}.custody_verified",
        ),
    }


def _validate_rollback_preconditions(
    value: Any,
    path: str,
) -> dict[str, bool]:
    preconditions = require_mapping(value, path)
    require_exact_fields(
        preconditions,
        {
            "drain_complete",
            "hashes_verified",
            "identity_verified",
            "backup_requirement_satisfied",
            "receipt_requirement_satisfied",
        },
        path=path,
    )

    return {
        "drain_complete": require_bool(
            preconditions["drain_complete"],
            f"{path}.drain_complete",
        ),
        "hashes_verified": require_bool(
            preconditions["hashes_verified"],
            f"{path}.hashes_verified",
        ),
        "identity_verified": require_bool(
            preconditions["identity_verified"],
            f"{path}.identity_verified",
        ),
        "backup_requirement_satisfied": require_bool(
            preconditions["backup_requirement_satisfied"],
            f"{path}.backup_requirement_satisfied",
        ),
        "receipt_requirement_satisfied": require_bool(
            preconditions["receipt_requirement_satisfied"],
            f"{path}.receipt_requirement_satisfied",
        ),
    }


def _validate_rollback_fence(
    value: Any,
    path: str,
) -> dict[str, int]:
    fence = require_mapping(value, path)
    require_exact_fields(
        fence,
        {
            "admission_epoch",
            "committed_epoch",
            "final_epoch",
        },
        path=path,
    )

    return {
        "admission_epoch": require_nonnegative_int(
            fence["admission_epoch"],
            f"{path}.admission_epoch",
        ),
        "committed_epoch": require_nonnegative_int(
            fence["committed_epoch"],
            f"{path}.committed_epoch",
        ),
        "final_epoch": require_nonnegative_int(
            fence["final_epoch"],
            f"{path}.final_epoch",
        ),
    }


def _validate_peerhub_mutation(
    value: Any,
    path: str,
) -> dict[str, Any]:
    mutation = require_mapping(value, path)
    require_exact_fields(
        mutation,
        {
            "mutation_id",
            "fact_id",
            "commit_watermark",
            "in_restore_scope",
        },
        path=path,
    )

    return {
        "mutation_id": require_string(
            mutation["mutation_id"],
            f"{path}.mutation_id",
        ),
        "fact_id": require_string(
            mutation["fact_id"],
            f"{path}.fact_id",
        ),
        "commit_watermark": require_nonnegative_int(
            mutation["commit_watermark"],
            f"{path}.commit_watermark",
        ),
        "in_restore_scope": require_bool(
            mutation["in_restore_scope"],
            f"{path}.in_restore_scope",
        ),
    }


def _receipt_integrity_valid(
    facts: Mapping[str, Any],
) -> bool:
    receipt = facts["transition_receipt"]
    return bool(
        receipt is not None
        and receipt["recorded_receipt_digest"]
        == receipt["observed_receipt_digest"]
    )


def _artifact_digest_valid(
    facts: Mapping[str, Any],
) -> bool:
    receipt = facts["transition_receipt"]
    artifact = facts["restore_artifact"]
    return bool(
        receipt is not None
        and artifact is not None
        and artifact["artifact_id"]
        == receipt["restore_artifact_id"]
        and artifact["observed_digest"]
        == receipt["restore_artifact_digest"]
    )


def _artifact_ready(
    facts: Mapping[str, Any],
) -> bool:
    artifact = facts["restore_artifact"]
    return bool(
        artifact is not None
        and artifact["custody_verified"]
        and _artifact_digest_valid(facts)
    )


def _inverse_fence_valid(
    facts: Mapping[str, Any],
) -> bool:
    fence = facts["rollback_fence"]
    return bool(
        fence is not None
        and fence["admission_epoch"]
        == fence["committed_epoch"]
        and fence["admission_epoch"]
        == fence["final_epoch"]
    )


def _conflicting_mutation_ids(
    facts: Mapping[str, Any],
) -> list[str]:
    receipt = facts["transition_receipt"]
    if receipt is None:
        return []

    watermark = receipt["peerhub_commit_watermark"]
    return [
        mutation["mutation_id"]
        for mutation in facts["peerhub_mutations"]
        if (
            mutation["commit_watermark"] > watermark
            and mutation["in_restore_scope"]
        )
    ]


def _rollback_requirement_checks(
    facts: Mapping[str, Any],
) -> dict[str, bool]:
    if facts["recovery_kind"] != "ROLLBACK":
        return {
            "retired_not_ratified": False,
            "drain_complete": False,
            "hashes_verified": False,
            "identity_verified": False,
            "backup_requirement_satisfied": False,
            "receipt_requirement_satisfied": False,
            "receipt_integrity_verified": False,
            "backup_digest_verified": False,
        }

    preconditions = facts["rollback_preconditions"]
    return {
        "retired_not_ratified": not facts["retired_ratified"],
        "drain_complete": preconditions["drain_complete"],
        "hashes_verified": preconditions["hashes_verified"],
        "identity_verified": preconditions["identity_verified"],
        "backup_requirement_satisfied": preconditions[
            "backup_requirement_satisfied"
        ],
        "receipt_requirement_satisfied": preconditions[
            "receipt_requirement_satisfied"
        ],
        "receipt_integrity_verified": (
            _receipt_integrity_valid(facts)
        ),
        "backup_digest_verified": (
            _artifact_digest_valid(facts)
        ),
    }


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)
    receipt = facts["transition_receipt"]
    artifact = facts["restore_artifact"]
    preconditions = facts["rollback_preconditions"]
    fence = facts["rollback_fence"]
    mutations = facts["peerhub_mutations"]

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base_fixture_id}.{detail}",
        )

    if not facts["attempt_record_present"]:
        invalid("requires a preserved transition-attempt record")

    if base_fixture_id == "AC-07-01":
        if facts["recovery_kind"] != "FORWARD_RECOVERY":
            invalid("requires FORWARD_RECOVERY")
        if facts["failure_position"] != "PRE_MARKER":
            invalid("requires PRE_MARKER failure")
        if facts["marker_committed"]:
            invalid("pre-marker failure cannot have a marker")
        if facts["authority_before_recovery"] != "ENGRAM":
            invalid("Engram must remain authoritative")
        if receipt is not None or artifact is not None:
            invalid("pre-marker failure cannot carry receipt restore data")
        if (
            facts["retired_ratified"]
            or fence is not None
            or mutations
            or any(preconditions.values())
        ):
            invalid("pre-marker vector cannot carry rollback facts")
        return

    if base_fixture_id in {
        "AC-07-02",
        "AC-07-03",
        "AC-07-04",
    }:
        if facts["recovery_kind"] != "FORWARD_RECOVERY":
            invalid("requires FORWARD_RECOVERY")
        if facts["failure_position"] != "POST_MARKER":
            invalid("requires POST_MARKER failure")
        if not facts["marker_committed"]:
            invalid("post-marker failure requires a committed marker")
        if facts["authority_before_recovery"] != "PEERHUB":
            invalid("PeerHub must be authoritative after the marker")
        if receipt is None or artifact is None:
            invalid("post-marker recovery requires receipt restore data")
        if (
            facts["retired_ratified"]
            or fence is not None
            or mutations
            or any(preconditions.values())
        ):
            invalid("forward recovery cannot carry rollback facts")

        receipt_valid = _receipt_integrity_valid(facts)
        artifact_valid = _artifact_digest_valid(facts)
        custody_valid = artifact["custody_verified"]

        if base_fixture_id == "AC-07-02":
            if (
                not receipt_valid
                or not artifact_valid
                or not custody_valid
            ):
                invalid(
                    "requires an intact receipt and verified "
                    "referenced artifact"
                )
            return

        if base_fixture_id == "AC-07-03":
            if receipt_valid:
                invalid("requires a corrupt transition receipt")
            if not artifact_valid or not custody_valid:
                invalid(
                    "receipt-corruption vector must isolate "
                    "the receipt-integrity defect"
                )
            return

        if not receipt_valid:
            invalid("backup mismatch requires an intact receipt")
        if artifact_valid:
            invalid("requires a restore-artifact digest mismatch")
        if not custody_valid:
            invalid(
                "backup mismatch must isolate digest verification"
            )
        return

    if facts["recovery_kind"] != "ROLLBACK":
        invalid("requires ROLLBACK")
    if facts["failure_position"] != "NONE":
        invalid("rollback vector cannot be a recovery failure")
    if not facts["marker_committed"]:
        invalid("rollback requires the committed forward marker")
    if facts["authority_before_recovery"] != "PEERHUB":
        invalid("PeerHub must be authoritative before rollback")
    if receipt is None or artifact is None:
        invalid("rollback requires receipt and backup facts")
    if facts["retired_ratified"]:
        invalid("rollback is prohibited after RETIRED ratification")
    if not all(preconditions.values()):
        invalid("all reused rollback preconditions must be true")
    if not _receipt_integrity_valid(facts):
        invalid("rollback requires an intact transition receipt")
    if not _artifact_ready(facts):
        invalid("rollback requires a verified restore artifact")
    if not _inverse_fence_valid(facts):
        invalid("rollback requires a stable inverse-fence epoch")

    conflicting = _conflicting_mutation_ids(facts)

    if base_fixture_id == "AC-07-05":
        if mutations:
            invalid(
                "successful fenced rollback requires no mutations"
            )
        return

    if len(mutations) != 4:
        invalid(
            "write-refusal vector requires four mutation records"
        )
    if len(conflicting) != 2:
        invalid(
            "write-refusal vector requires two scoped "
            "post-watermark mutations"
        )

    watermark = receipt["peerhub_commit_watermark"]
    if not any(
        mutation["commit_watermark"] <= watermark
        and mutation["in_restore_scope"]
        for mutation in mutations
    ):
        invalid("requires an in-scope pre-watermark control")
    if not any(
        mutation["commit_watermark"] > watermark
        and not mutation["in_restore_scope"]
        for mutation in mutations
    ):
        invalid("requires an out-of-scope post-watermark control")


def validate_authority_recovery_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed AC-07 receipt-driven recovery vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "recovery_kind",
            "failure_position",
            "attempt_id",
            "attempt_record_present",
            "marker_committed",
            "authority_before_recovery",
            "transition_receipt",
            "restore_artifact",
            "retired_ratified",
            "rollback_preconditions",
            "rollback_fence",
            "peerhub_mutations",
        },
        path="inputs",
    )

    raw_receipt = inputs["transition_receipt"]
    receipt = (
        None
        if raw_receipt is None
        else _validate_transition_receipt(
            raw_receipt,
            "inputs.transition_receipt",
        )
    )

    raw_artifact = inputs["restore_artifact"]
    artifact = (
        None
        if raw_artifact is None
        else _validate_restore_artifact(
            raw_artifact,
            "inputs.restore_artifact",
        )
    )

    raw_fence = inputs["rollback_fence"]
    fence = (
        None
        if raw_fence is None
        else _validate_rollback_fence(
            raw_fence,
            "inputs.rollback_fence",
        )
    )

    raw_mutations = inputs["peerhub_mutations"]
    if not isinstance(raw_mutations, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.peerhub_mutations must be an array",
        )

    mutations = [
        _validate_peerhub_mutation(
            mutation,
            f"inputs.peerhub_mutations[{index}]",
        )
        for index, mutation in enumerate(raw_mutations)
    ]
    mutation_ids = [
        mutation["mutation_id"]
        for mutation in mutations
    ]
    if len(mutation_ids) != len(set(mutation_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.peerhub_mutations contains "
                "duplicate mutation_id values"
            ),
        )

    normalized = {
        "recovery_kind": _require_enum(
            inputs["recovery_kind"],
            _RECOVERY_KINDS,
            "inputs.recovery_kind",
        ),
        "failure_position": _require_enum(
            inputs["failure_position"],
            _FAILURE_POSITIONS,
            "inputs.failure_position",
        ),
        "attempt_id": require_string(
            inputs["attempt_id"],
            "inputs.attempt_id",
        ),
        "attempt_record_present": require_bool(
            inputs["attempt_record_present"],
            "inputs.attempt_record_present",
        ),
        "marker_committed": require_bool(
            inputs["marker_committed"],
            "inputs.marker_committed",
        ),
        "authority_before_recovery": _require_enum(
            inputs["authority_before_recovery"],
            _AUTHORITIES,
            "inputs.authority_before_recovery",
        ),
        "transition_receipt": receipt,
        "restore_artifact": artifact,
        "retired_ratified": require_bool(
            inputs["retired_ratified"],
            "inputs.retired_ratified",
        ),
        "rollback_preconditions": (
            _validate_rollback_preconditions(
                inputs["rollback_preconditions"],
                "inputs.rollback_preconditions",
            )
        ),
        "rollback_fence": fence,
        "peerhub_mutations": mutations,
    }

    if normalized["marker_committed"]:
        if receipt is None or artifact is None:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "committed marker requires transition "
                    "receipt and restore artifact"
                ),
            )
    elif receipt is not None or artifact is not None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "uncommitted marker cannot carry committed "
                "receipt restore data"
            ),
        )

    if receipt is not None:
        if receipt["attempt_id"] != normalized["attempt_id"]:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.transition_receipt.attempt_id "
                    "does not bind to inputs.attempt_id"
                ),
            )
        if (
            artifact is not None
            and artifact["artifact_id"]
            != receipt["restore_artifact_id"]
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.restore_artifact.artifact_id "
                    "does not match receipt reference"
                ),
            )

    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def _validate_rollback_requirement_checks(
    value: Any,
    path: str,
) -> dict[str, bool]:
    checks = require_mapping(value, path)
    require_exact_fields(
        checks,
        {
            "retired_not_ratified",
            "drain_complete",
            "hashes_verified",
            "identity_verified",
            "backup_requirement_satisfied",
            "receipt_requirement_satisfied",
            "receipt_integrity_verified",
            "backup_digest_verified",
        },
        path=path,
    )

    return {
        key: require_bool(checks[key], f"{path}.{key}")
        for key in (
            "retired_not_ratified",
            "drain_complete",
            "hashes_verified",
            "identity_verified",
            "backup_requirement_satisfied",
            "receipt_requirement_satisfied",
            "receipt_integrity_verified",
            "backup_digest_verified",
        )
    }


def validate_authority_recovery_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate observable AC-07 recovery and rollback evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "decision",
            "error_code",
            "disposition",
            "authority_after_recovery",
            "attempt_preserved",
            "receipt_integrity_checked",
            "transition_receipt_trusted",
            "backup_digest_checked",
            "restore_artifact_id",
            "restore_performed",
            "fresh_transition_replay_count",
            "inverse_fence_checked",
            "inverse_fence_committed",
            "rollback_epoch",
            "rollback_requirement_checks",
            "enumerated_peerhub_mutation_ids",
        },
        path="output",
    )

    error_code = _require_nullable_string(
        output["error_code"],
        "output.error_code",
    )
    if (
        error_code is not None
        and error_code not in _ERROR_CODES
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.error_code unsupported={error_code}",
        )

    raw_ids = output["enumerated_peerhub_mutation_ids"]
    if not isinstance(raw_ids, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.enumerated_peerhub_mutation_ids "
                "must be an array"
            ),
        )
    mutation_ids = [
        require_string(
            mutation_id,
            (
                "output.enumerated_peerhub_mutation_ids"
                f"[{index}]"
            ),
        )
        for index, mutation_id in enumerate(raw_ids)
    ]
    if len(mutation_ids) != len(set(mutation_ids)):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.enumerated_peerhub_mutation_ids "
                "contains duplicates"
            ),
        )

    return {
        "decision": _require_enum(
            output["decision"],
            _DECISIONS,
            "output.decision",
            output=True,
        ),
        "error_code": error_code,
        "disposition": _require_enum(
            output["disposition"],
            _DISPOSITIONS,
            "output.disposition",
            output=True,
        ),
        "authority_after_recovery": _require_enum(
            output["authority_after_recovery"],
            _AUTHORITIES,
            "output.authority_after_recovery",
            output=True,
        ),
        "attempt_preserved": require_bool(
            output["attempt_preserved"],
            "output.attempt_preserved",
        ),
        "receipt_integrity_checked": require_bool(
            output["receipt_integrity_checked"],
            "output.receipt_integrity_checked",
        ),
        "transition_receipt_trusted": require_bool(
            output["transition_receipt_trusted"],
            "output.transition_receipt_trusted",
        ),
        "backup_digest_checked": require_bool(
            output["backup_digest_checked"],
            "output.backup_digest_checked",
        ),
        "restore_artifact_id": _require_nullable_string(
            output["restore_artifact_id"],
            "output.restore_artifact_id",
        ),
        "restore_performed": require_bool(
            output["restore_performed"],
            "output.restore_performed",
        ),
        "fresh_transition_replay_count": (
            require_nonnegative_int(
                output["fresh_transition_replay_count"],
                "output.fresh_transition_replay_count",
            )
        ),
        "inverse_fence_checked": require_bool(
            output["inverse_fence_checked"],
            "output.inverse_fence_checked",
        ),
        "inverse_fence_committed": require_bool(
            output["inverse_fence_committed"],
            "output.inverse_fence_committed",
        ),
        "rollback_epoch": _require_nullable_int(
            output["rollback_epoch"],
            "output.rollback_epoch",
        ),
        "rollback_requirement_checks": (
            _validate_rollback_requirement_checks(
                output["rollback_requirement_checks"],
                "output.rollback_requirement_checks",
            )
        ),
        "enumerated_peerhub_mutation_ids": mutation_ids,
    }


def _output(
    *,
    decision: str,
    error_code: str | None,
    disposition: str,
    authority_after_recovery: str,
    attempt_preserved: bool = True,
    receipt_integrity_checked: bool = False,
    transition_receipt_trusted: bool = False,
    backup_digest_checked: bool = False,
    restore_artifact_id: str | None = None,
    restore_performed: bool = False,
    fresh_transition_replay_count: int = 0,
    inverse_fence_checked: bool = False,
    inverse_fence_committed: bool = False,
    rollback_epoch: int | None = None,
    rollback_requirement_checks: (
        Mapping[str, bool] | None
    ) = None,
    enumerated_peerhub_mutation_ids: (
        list[str] | None
    ) = None,
) -> dict[str, Any]:
    if rollback_requirement_checks is None:
        rollback_requirement_checks = {
            "retired_not_ratified": False,
            "drain_complete": False,
            "hashes_verified": False,
            "identity_verified": False,
            "backup_requirement_satisfied": False,
            "receipt_requirement_satisfied": False,
            "receipt_integrity_verified": False,
            "backup_digest_verified": False,
        }

    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": disposition,
        "authority_after_recovery": authority_after_recovery,
        "attempt_preserved": attempt_preserved,
        "receipt_integrity_checked": receipt_integrity_checked,
        "transition_receipt_trusted": (
            transition_receipt_trusted
        ),
        "backup_digest_checked": backup_digest_checked,
        "restore_artifact_id": restore_artifact_id,
        "restore_performed": restore_performed,
        "fresh_transition_replay_count": (
            fresh_transition_replay_count
        ),
        "inverse_fence_checked": inverse_fence_checked,
        "inverse_fence_committed": inverse_fence_committed,
        "rollback_epoch": rollback_epoch,
        "rollback_requirement_checks": dict(
            rollback_requirement_checks
        ),
        "enumerated_peerhub_mutation_ids": list(
            enumerated_peerhub_mutation_ids or []
        ),
    }


def _pre_marker_stop() -> dict[str, Any]:
    return _output(
        decision="STOPPED_SAFE",
        error_code=None,
        disposition="PRE_MARKER_ATTEMPT_PRESERVED",
        authority_after_recovery="ENGRAM",
    )


def _post_marker_restore(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = facts["restore_artifact"]
    return _output(
        decision="RECOVERED",
        error_code=None,
        disposition="POST_MARKER_RECEIPT_RESTORE",
        authority_after_recovery="PEERHUB",
        receipt_integrity_checked=True,
        transition_receipt_trusted=True,
        backup_digest_checked=True,
        restore_artifact_id=artifact["artifact_id"],
        restore_performed=True,
    )


def _receipt_integrity_refusal() -> dict[str, Any]:
    return _output(
        decision="REFUSED",
        error_code="RECEIPT_INTEGRITY_INVALID",
        disposition="RECEIPT_INTEGRITY_INVALID",
        authority_after_recovery="PEERHUB",
        receipt_integrity_checked=True,
    )


def _artifact_digest_refusal() -> dict[str, Any]:
    return _output(
        decision="REFUSED",
        error_code="RESTORE_ARTIFACT_DIGEST_MISMATCH",
        disposition="RESTORE_ARTIFACT_DIGEST_MISMATCH",
        authority_after_recovery="PEERHUB",
        receipt_integrity_checked=True,
        transition_receipt_trusted=True,
        backup_digest_checked=True,
    )


def _rollback_success(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    fence = facts["rollback_fence"]
    artifact = facts["restore_artifact"]
    return _output(
        decision="ROLLED_BACK",
        error_code=None,
        disposition="INVERSE_FENCED_ROLLBACK_COMMITTED",
        authority_after_recovery="ENGRAM",
        receipt_integrity_checked=True,
        transition_receipt_trusted=True,
        backup_digest_checked=True,
        restore_artifact_id=artifact["artifact_id"],
        restore_performed=True,
        inverse_fence_checked=True,
        inverse_fence_committed=True,
        rollback_epoch=fence["final_epoch"] + 1,
        rollback_requirement_checks=(
            _rollback_requirement_checks(facts)
        ),
    )


def _peerhub_write_refusal(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    return _output(
        decision="REFUSED",
        error_code="PEERHUB_ERA_WRITES_PRESENT",
        disposition="PEERHUB_ERA_WRITES_PRESENT",
        authority_after_recovery="PEERHUB",
        receipt_integrity_checked=True,
        transition_receipt_trusted=True,
        backup_digest_checked=True,
        inverse_fence_checked=True,
        inverse_fence_committed=False,
        rollback_requirement_checks=(
            _rollback_requirement_checks(facts)
        ),
        enumerated_peerhub_mutation_ids=(
            _conflicting_mutation_ids(facts)
        ),
    )


def _oracle_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if not facts["marker_committed"]:
        return _pre_marker_stop()

    if facts["recovery_kind"] == "FORWARD_RECOVERY":
        if not _receipt_integrity_valid(facts):
            return _receipt_integrity_refusal()

        if not _artifact_ready(facts):
            return _artifact_digest_refusal()

        return _post_marker_restore(facts)

    conflicting = _conflicting_mutation_ids(facts)
    if conflicting:
        return _peerhub_write_refusal(facts)

    return _rollback_success(facts)


def _subject_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    marker_committed = facts["marker_committed"]
    recovery_kind = facts["recovery_kind"]

    if not marker_committed:
        return _pre_marker_stop()

    if recovery_kind == "FORWARD_RECOVERY":
        receipt_ok = _receipt_integrity_valid(facts)
        if not receipt_ok:
            return _receipt_integrity_refusal()

        backup_ok = _artifact_ready(facts)
        if not backup_ok:
            return _artifact_digest_refusal()

        return _post_marker_restore(facts)

    peerhub_writes = _conflicting_mutation_ids(facts)
    if len(peerhub_writes) > 0:
        return _peerhub_write_refusal(facts)

    return _rollback_success(facts)


class AuthorityRecoveryOracle:
    """Pure AC-07 oracle over injected recovery evidence."""

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


class AuthorityRecoverySubjectAdapter:
    """Pure reference adapter over injected recovery facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_recovery.{label}.reference"
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


class FaultInjectedAuthorityRecoveryAdapter(
    AuthorityRecoverySubjectAdapter
):
    """One genuine recovery-authority defect per negative."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        super().__init__(base_fixture_id)
        faults = {
            "AC-07-01": "discard_pre_marker_attempt",
            "AC-07-02": "best_effort_transition_replay",
            "AC-07-03": "trust_corrupt_receipt",
            "AC-07-04": "skip_backup_digest_check",
            "AC-07-05": "direct_unfenced_rollback",
            "AC-07-06": "ignore_peerhub_era_writes",
        }
        self._fault = faults[base_fixture_id]
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_recovery.{label}.{self._fault}"
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

        if self._base == "AC-07-01":
            output = _pre_marker_stop()
            output["attempt_preserved"] = False
            return output

        if self._base == "AC-07-02":
            output = _post_marker_restore(raw_inputs)
            output["disposition"] = (
                "POST_MARKER_BEST_EFFORT_REPLAY"
            )
            output["restore_artifact_id"] = None
            output["restore_performed"] = False
            output["fresh_transition_replay_count"] = 1
            return output

        if self._base == "AC-07-03":
            return _post_marker_restore(raw_inputs)

        if self._base == "AC-07-04":
            output = _post_marker_restore(raw_inputs)
            output["backup_digest_checked"] = False
            return output

        if self._base == "AC-07-05":
            output = _rollback_success(raw_inputs)
            output["disposition"] = (
                "DIRECT_UNFENCED_ROLLBACK"
            )
            output["inverse_fence_checked"] = False
            output["inverse_fence_committed"] = False
            output["rollback_epoch"] = None
            return output

        return _rollback_success(raw_inputs)


def authority_recovery_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all AC-07 recovery-authority registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityRecoveryOracle(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=AuthorityRecoverySubjectAdapter(
                    base_fixture_id
                ),
                input_validator=(
                    validate_authority_recovery_inputs
                ),
                output_validator=(
                    validate_authority_recovery_output
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
                    FaultInjectedAuthorityRecoveryAdapter(
                        base_fixture_id
                    )
                ),
                input_validator=(
                    validate_authority_recovery_inputs
                ),
                output_validator=(
                    validate_authority_recovery_output
                ),
            )
        )

    return tuple(registrations)
